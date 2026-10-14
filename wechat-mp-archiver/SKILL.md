---
name: wechat-mp-archiver
description: 采集微信公众号（mp.weixin.qq.com）在本机微信中已加载的文章并归档，可导出成图文自包含的 PDF（一篇一个文件）。当官方接口、第三方平台都拿不到目标号的文章列表时，从本机 PC 微信的磁盘缓存与进程内存里取证。用于：下载某个公众号的全部/历史文章、批量保存公众号文章、把公众号文章导出成 PDF、备份自己的公众号、研究某个号的历史发文。触发词：公众号文章采集、下载公众号全部文章、公众号历史消息、导出公众号PDF、mp.weixin.qq.com 抓取、__biz。
---

# 微信公众号文章归档

把用户有权访问、且已在本机 PC 微信加载的公众号文章批量归档为 SQLite、CSV、Markdown 和 PDF。内存与缓存只能提供已加载、尚未被释放的数据，不保证完整历史；PDF 图片是否完整需要检查下载日志并抽样查看。

只读取用户本人授权的本机微信环境。内存/缓存和诊断输出可能包含会话凭证或其他页面信息；不要将其上传到仓库或公开日志。Cookie 解密是可选诊断，不属于正常采集流程。

## 核心前提：不要走接口

**本包优先采用本机已加载页面的文章链接，接口方案仅保留作旧版诊断：**

页面级接口 `mp.weixin.qq.com/mp/profile_ext?action=getmsg` 在原作者的测试环境中不可用。
即使凑齐 `__biz` + `uin` + `key` + `pass_ticket` + `appmsg_token`，再配上从
微信自己 Cookie 库里解出来的完整 Cookie，仍会返回 `{"ret":-3,"errmsg":"no session"}`。
新版客户端走带签名的私有通道，逆向不划算。

搜狗微信、第三方数据平台对该号也常常无收录；文章页里没有"往期推荐"外链，
因此不要假设这些入口能够提供完整清单；可用性随客户端和平台变化。

**正确路线：数据已经在用户本机的 PC 微信里了。**

## 工作流

### Step 0 — 环境自检

```bash
python <skill>/scripts/doctor.py
```

逐项确认：Windows / 管理员权限 / `SeDebugPrivilege` / `SeBackupPrivilege` /
微信进程 / 浏览器 profile / Python 依赖 / Chrome 渲染链路。
存在 `[x]` 阻塞项就先解决，不要往下走。

### Step 1 — 拿到目标号的 `__biz`

三种途径，按省事程度排序：

1. **用户给了该号任一篇文章链接** —— 最省事。抓那个页面找 `var biz = "Mz..."`：
   ```bash
   python <skill>/scripts/wx_collect.py article "<文章URL>" --out archive/single
   ```
   若页面含 `biz` 或 URL 含 `__biz`，输出会显示该标识。短链可能遇到删除、访问限制或验证，不能保证可打开。
2. **用户给了带 `__biz` 的 URL**（profile_ext / 文章长链），直接取参数。
3. **只给了公众号名** —— 让用户在 PC 微信里搜到该号、随便打开一篇文章，
   然后把那篇文章的链接发过来。

`__biz` 通常是 base64 形式的公众号标识，示例命令使用 `YOUR_BIZ` 占位。

### Step 2 — 让用户在 PC 微信里打开目标号主页 ⚠️ 关键前置动作

内存里之所以有文章链接，是因为**微信刚刚渲染过那个页面**。所以必须让用户：

> 打开 PC 微信 → 进入目标公众号 → 点右上角「…」→「查看历史消息」→ 向下滚一屏

需要覆盖更多历史时，继续滚动并等待列表加载。只滚一屏通常只能覆盖部分文章；分批采集会按 mid/idx 合并同一输出 JSON。
**不要要求用户开 F12 或抓包** —— 磁盘缓存和内存里已经有了，自己取就行。

### Step 3 — 从进程内存提取文章清单

```bash
python <skill>/scripts/mem_articles.py <__biz> "<公众号名>" mem_articles.json
```

以 `__biz` 为锚点扫描 `Weixin.exe` / `WeChatAppEx.exe` 的可读内存，
从命中的 URL 里还原出每篇的 `mid` / `idx` / `sn` / `chksm`。

输出 `mem_articles.json`：文章总数 + 每篇的完整 URL。
控制台会打印 `mid=... idx=... hits=...`，文章数量只表示当前提取结果。主页原创数量与全部消息不是同一口径，不能据此证明完整性。

> 若命中 0 条：多半是 Step 2 没做，或微信进程刚重启（内存被释放）。
> 让用户重新打开一次目标号主页，再次执行即可。

### Step 4 — 抓正文并归档

```bash
python <skill>/scripts/fetch_articles.py -i mem_articles.json -o output -a "<公众号名>"
```

逐篇抓正文、解析成结构化数据，产出：

| 文件 | 内容 |
|---|---|
| `output/weixin_articles.db` | SQLite 全量库（含 `content_md` / `content_html` / `images`） |
| `output/articles.csv` | 总表，Excel 可直接打开 |
| `output/articles/*.md` | 每篇一个 Markdown |
| `output/合集.md` | 按时间排序的全文合并版 |
| `output/目录.md` | 文章目录 |

默认每次请求随机间隔 1.5~3.5 秒防风控，可用 `--min-delay` / `--max-delay` 调整。
支持断点续传：`fetched` 字段已置 1 的不会重抓。

### Step 5 — 导出为图文自包含 PDF

```bash
python <skill>/scripts/export_pdf.py -d output/weixin_articles.db -o output -a "<公众号名>"
```

一篇一个 PDF，输出到 `output/pdf/`，命名 `<序号>_<日期>_<标题>.pdf`。

**图片处理是本步骤的关键**：正文里的图片是 `mmbiz.qpic.cn` 且带防盗链、
用 `data-src` 懒加载属性。脚本会先把它们下载到本地（带
`Referer: https://mp.weixin.qq.com/` 绕过防盗链），改写成本地路径后再渲染。
**必须这样做**，否则 PDF 要么缺图，要么依赖网络、换台机器就变裂图。

渲染用 Playwright 驱动本机已有 Chrome/Edge（`--headless --print-to-pdf` 无法
覆盖懒加载图片，必须走 Playwright 等 `document.images` 全部 complete）。

### Step 6 — 校验并出目录

```bash
python <skill>/scripts/verify_pdf.py output/pdf
python <skill>/scripts/make_index.py output/pdf "<公众号名>"
```

`verify_pdf.py` 检查每篇的页数、可提取文字量、内嵌图片数，列出异常文件。
`make_index.py` 生成 `output/pdf/_目录.md`（篇数/页数/字数/图片数汇总表）。

## 脚本清单

| 脚本 | 用途 |
|---|---|
| `doctor.py` | 环境自检。`--deep` 额外检查缓存凭证与内存可达性 |
| `wechat_paths.py` | 微信数据目录自动发现（其他脚本依赖它，不写死路径） |
| `wx_collect.py` | 核心库 + CLI：单篇解析（`article`）、抓取正文（`fetch_article`）、Store、CSV/MD 导出 |
| `creds_from_cache.py` | 从微信 Chromium 缓存里提取带凭证的 `profile_ext` 请求 URL |
| `mem_articles.py` | **内存提取文章清单（主力方案）** |
| `scan_mem.py` | 内存扫描底层工具，也可单独调试 |
| `fetch_articles.py` | 批量抓正文 → SQLite / CSV / Markdown |
| `export_pdf.py` | 逐篇导出图文自包含 PDF |
| `verify_pdf.py` | 校验 PDF 完整性 |
| `make_index.py` | 生成 PDF 目录 |
| `scan_wx_cache.py` | 读取被微信独占锁的缓存文件（含 `SeBackupPrivilege` 强读）+ 解压 |
| `hunt_cache.py` | 按关键字全盘搜缓存（自动尝试 gzip/br/zlib 解压） |
| `dump_cookies.py` | 解密微信浏览器 Cookie 库（备用/诊断路线，采集本身用不到） |

## 路径与变量

所有脚本默认从**当前工作目录**读写产物，脚本自身可放在任意位置。
常用环境变量：

- `WX_BIZ` — 目标号 `__biz`，供 `scan_wx_cache.py` / `scan_mem.py` 使用
- `WX_WEB_ROOT` — 所有路径发现脚本共用的微信浏览器根目录覆盖项
- `WX_CACHE_ROOT` — 部分缓存诊断脚本接受的备用覆盖项

## 运行环境要求

- **Windows** + 已登录的 PC 微信（4.x 为 `xwechat`，3.x 为 `WeChat`，两者都支持）
- **管理员权限**：内存扫描需要 `SeDebugPrivilege`（管理员令牌自带但默认禁用，脚本会自行启用）
- Python 依赖：`requests` `beautifulsoup4` `lxml` `brotli` `playwright` `pypdf`
  （`cryptography` 仅解密 Cookie 时需要）
- 本机装有 Chrome 或 Edge（用于渲染 PDF）
- 调用前用 `doctor.py` 确认

## 常见坑（详见 references/）

- `/s?__biz=..&mid=..&idx=..&sn=..` **必须带 `chksm`** 才能打开，缺了返回「未知错误」。
  用内存/缓存里拿到的原始 URL，不要自己拼。
- 部分文章需要附加 `&scene=27&ascene=1&devicetype=..&version=..&wx_header=3`
  才能拿到正文，脚本已内置重试。
- 返回「微信扫一扫可打开此内容」时，可能是非图文消息、访问限制或验证页。
  需要逐条核对原因，不要直接宣布成功或认定原文没有正文。
- 采集他人公众号文章涉及著作权与平台协议。应确认访问、保存和使用权限；本工具不会授予文章版权，
  不应将私人凭证或未经授权的内容公开 —— 交付时要如实提醒用户。

需要排查具体故障时读 `references/troubleshooting.md`；
需要理解底层机制（缓存格式、内存扫描、Cookie 加密链）时读 `references/internals.md`。
