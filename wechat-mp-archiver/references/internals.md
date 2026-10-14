# 底层机制

理解这些才能在方案失效时自己找到新路子。以下记录来自原测试环境，不代表所有微信版本均适用；本发布检查没有复测真实微信采集。

## 1. 微信 4.0 的本地结构

微信 4.x（进程名 `Weixin.exe`）内置了一个真正的 Chromium（代号 xweb / radium），
用来渲染公众号、网页、小程序。数据布局：

```
%APPDATA%\Tencent\xwechat\radium\web\
├── Local State                                  # 含 os_crypt.encrypted_key（DPAPI 保护的 AES 主密钥）
└── profiles\
    ├── multitab\<hash>\                         # 多标签页渲染进程用的 profile
    │   ├── Cache\Cache_Data\data_0..data_3      # HTTP 缓存（Chromium Simple Cache）
    │   └── Network\Cookies                      # Cookie 库（SQLite）
    ├── webview\<hash>\                          # 内嵌网页
    └── web_shell\                               # 主壳

%USERPROFILE%\Documents\xwechat_files\<账号>\    # 聊天记录、收到的文件
```

老版本微信 3.x 路径是 `Tencent\WeChat\radium\web`，结构一致。
`wechat_paths.py` 会两个都找。

关键点：**公众号页面被渲染过，其请求 URL（含凭证）就会留在 Cache 里**。
这是整个方案不依赖抓包的基础。

## 2. 读被独占锁住的文件

微信对 `Cache_Data\*` 和 `Network\Cookies` 持独占锁，直接用 `open()` 会
`PermissionError`，`robocopy /B` 也会失败（错误 32）。

三个层次的应对，按成功率递增：

1. `CreateFileW(path, GENERIC_READ, FILE_SHARE_READ|WRITE|DELETE, ...)`
   —— 显式申请共享读。缓存文件用这招基本就够。
2. 启用 `SeBackupPrivilege` 再打开，并加 `FILE_FLAG_BACKUP_SEMANTICS` (0x02000000)
   —— 需要管理员权限。对大部分文件有效。
3. **VSS 卷影副本** —— 对 Cookie 库这类彻底锁死的文件，这是唯一可行的办法：

```powershell
$res = (Get-WmiObject -List Win32_ShadowCopy).Create("C:\", "ClientAccessible")
$sc  = Get-WmiObject Win32_ShadowCopy | Where-Object { $_.ID -eq $res.ShadowID }
Copy-Item -LiteralPath "$($sc.DeviceObject)\Users\...\Network\Cookies" -Destination <dst> -Force
$sc.Delete()      # 用完立刻删掉，是本会话创建的，不影响系统还原点
```

`scan_wx_cache.py::read_shared()` 已实现第 1、2 层。

## 3. Chromium 缓存格式

`Cache_Data` 下是 Simple Cache：文件头 + 元数据 + 响应体，响应体可能是
**gzip / brotli / zlib / raw-deflate** 压缩的。所以扫描时要逐个试：

```python
cands = [raw] + decompress(raw) + decompress(raw[256:]) + decompress(raw[512:])
```

打分规则：统计 `appmsg_token` `general_msg_list` `content_url` `__biz` 等
关键字的出现次数，取最高分的那个解压结果。这招能可靠地把压缩响应还原出来。

> 注意：**列表数据（`general_msg_list`）通常不会落盘**，只有页面请求 URL 会。
> 所以缓存能提供"凭证"，但提供不了"文章清单"——清单要去内存拿。

## 4. 进程内存扫描（拿文章清单的主力方案）

PC 微信渲染公众号主页时，DOM 里的文章链接就在渲染进程内存里，形如：

```
https://mp.weixin.qq.com/s?__biz=Mz...==&mid=2247483659&idx=1&sn=88cf...&chksm=f1b6...
```

扫描方法（`scan_mem.py`）：

1. `EnablePrivilege("SeDebugPrivilege")`
2. 枚举进程快照，挑出名字含 `Weixin` / `WeChat` / `WeChatAppEx` 的进程
3. `OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)`
4. `VirtualQueryEx` 遍历内存区，筛 `MEM_COMMIT` + 可读保护位 +
   排除 `PAGE_GUARD` + 单区 `<= 64MB`（避免超长耗时）
5. `ReadProcessMemory` 整块读出，`data.find(needle)` 定位锚点
6. 以锚点为中心取前后上下文（`i-60 : i+400`），再正则抽 URL

以 `__biz` 为锚点比搜 `mp.weixin.qq.com` 精准得多，且能自动过滤出目标号。

限制：单进程最多读 ~2.5GB 内存；命中数超 400 提前收敛。
文章数很多的号建议分几次跑（微信会分批加载列表）。

## 5. Cookie 加密链（备用路线，采集本身不需要）

Chromium on Windows 的标准方案，微信用的是同一套：

1. `Local State` 里 `os_crypt.encrypted_key` = `base64(DPAPI(aes_key))`，
   **去掉开头 5 字节 `DPAPI` 标记**后 `CryptUnprotectData` 得到 AES-256 密钥
2. `Cookies` 是 SQLite，`cookies.encrypted_value` 前 3 字节是 `v10`，
   随后 12 字节 nonce + 密文 + 16 字节 GCM tag
3. `AESGCM(key).decrypt(blob[3:15], blob[15:], None)` 解密

实测注意：微信这批 Cookie 解出来后，**明文前面还有一段固定长度的头部**
（各 Cookie 共享同一前缀），需要按公共前缀剥掉才是真正的值，否则拼出来的
Cookie 头可能带有额外前缀。当前 `dump_cookies.py` 没有可靠实现该前缀剥离，诊断结果不能保证可直接使用。

如前所述，即使这样拼出完整 Cookie，`getmsg` 接口依然返回 `no session`。

## 6. 微信文章页结构

- 正文容器：`#js_content`
- 标题：`#activity-name`
- 元信息（作者/时间）：`#meta_content`、`var ct = "时间戳"`、`var nickname`
- 图片：`<img data-src="https://mmbiz.qpic.cn/...">`，**用 `data-src` 而非 `src`**
  （懒加载），所以无论是解析还是转 PDF，都要主动把 `data-src` 当图片地址用
- 图片防盗链：`mmbiz.qpic.cn` 校验 `Referer`，请求时必须带
  `Referer: https://mp.weixin.qq.com/`，否则返回 403 或占位图

`chksm` 参数是文章 URL 的完整性校验位。实测：`/s?__biz=..&mid=..&idx=..&sn=..`
不带 `chksm` 会返回「未知错误」；带上就能打开。所以**不要自己拼 URL**，
一律用内存/缓存里原样拿到的。

## 7. 为什么不用第三方方案

- **搜狗微信**：小号常无收录（实测目标号返回"暂无相关官方认证订阅号"）
- **新榜 / 清博 / 西瓜数据**：付费、有延迟、小号收录不全，且服务稳定性参差
- **wechat2rss 一类**：对历史回溯支持有限
- **浏览器自动化翻页**：能行，但几十篇还行，上千篇不经济，且要处理登录态
