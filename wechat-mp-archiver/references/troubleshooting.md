# 排错手册

按"症状 → 原因 → 处理"组织。先跑 `python scripts/doctor.py`。只有在用户授权诊断缓存时才运行 `--deep`；其输出可能包含会话凭证，不应公开。

## 采集阶段

### 内存扫描命中 0 条文章

最可能的原因：**用户没在微信里打开过目标号主页**，或打开后微信进程重启过。

处理：
1. 确认 `__biz` 没写错（base64，注意结尾的 `==`）
2. 让用户重新操作一次：PC 微信 → 目标公众号 → 右上角「…」→「查看历史消息」→ 向下滚一屏
3. 立刻重跑 `mem_articles.py`
4. 仍为 0 → 确认微信进程在跑（`doctor.py` 会列出来），并确认是管理员身份运行

### 提取到的文章数比公众号显示的少

正常现象。微信列表是分页/懒加载的，内存里只有用户滚动过的那部分。
让用户**多往下滚几屏**（滚动触发加载），再重跑即可。
已提取的条目会去重合并。

### 出现 `ret=-3 errmsg=no session`

这是接口路线的失败信息，说明在尝试 `profile_ext?action=getmsg`。
**不要继续在这条路上花时间** —— 该接口在原测试环境不可用，不保证其他版本的行为。改走内存扫描。

### 权限相关报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `OpenProcess` 失败 / 空结果 | 非管理员，或 `SeDebugPrivilege` 未启用 | 以管理员身份重启客户端；脚本会尝试自行启用该特权 |
| `PermissionError` 读缓存 | 文件被微信独占锁 | `read_shared()` 会自动降级；仍失败则用 VSS 卷影副本 |
| robocopy 错误 32 | 同上，robocopy 没有共享读语义 | 改用卷影副本（见 `internals.md` 第 2 节） |

## 抓正文阶段

### 某几篇抓不到正文

先区分两种情况：

1. **页面返回「微信扫一扫可打开此内容，使用完整服务」** —— 这是贴图 / 纯图片消息，
   **当前响应没有可解析的文字正文，也可能是访问限制或页面验证**，不是抓取失败。删掉或忽略即可。
2. **返回「未知错误」/「参数错误」** —— URL 缺 `chksm`，或需要附加参数。
   脚本已内置 `&scene=27&ascene=1&devicetype=..&version=..&wx_header=3` 重试。

### 被限流 / 请求频繁

加大间隔：`--min-delay 4 --max-delay 9`。必要时分几次跑，
`fetched=1` 的不会重抓（断点续传）。

## PDF 阶段

### 图片全丢 / 显示裂图

1. 检查下载图片时是否带了 `Referer: https://mp.weixin.qq.com/`
   （`export_pdf.py` 已内置，若改动过务必保留）
2. 确认缓存/内存提取的正文 HTML 里图片是 `data-src` 而不是 `src`
3. 看 `export_pdf.py` 的日志末尾"图片：成功 N 张，失败 M 张"，
   `失败 > 0` 说明网络或防盗链有问题

### PDF 里图片是空白的（有位置没内容）

懒加载图片没等加载完就打印了。必须用 Playwright 渲染并在
`page.pdf()` 之前执行等待所有 `document.images` 的 `complete`：

```python
page.evaluate("""() => Promise.all(
    Array.from(document.images).map(img =>
        img.complete ? null : new Promise(r => { img.onload = r; img.onerror = r; }))
)""")
```

用命令行 `chrome --headless --print-to-pdf` 做不到这件事，不要图省事。

### 找不到浏览器

`export_pdf.py` 按顺序找 Chrome / Edge 的常见安装路径。
都没有时用 `pip install playwright && playwright install chromium` 装一个自带的。

### PDF 页数异常 / 文字提取不出来

跑 `verify_pdf.py <目录>`，它会逐篇检查页数、可提取字数、内嵌图片数，
直接列出异常文件。文字提取不出来一般意味着渲染成了图片（字体问题），
换一个带中文字体的渲染环境重跑。

### 中文变成方块

渲染环境缺中文字体。`export_pdf.py` 的模板里字体栈是
`Microsoft YaHei / PingFang SC / Hiragino Sans GB / Segoe UI`，
在没装这些字体的机器上要补一个，或改用系统已有的中文字体。

## 交付前的检查清单

- [ ] 是否覆盖预期时间范围；原创数与消息总数口径不同，不能证明完整历史
- [ ] `verify_pdf.py` 输出无异常项
- [ ] PDF 数量 = 有正文的文章数（不等于提取到的链接总数，差的是非图文消息）
- [ ] 打开一两篇含图最多的 PDF，目视确认图片正常显示
- [ ] 向用户说明：抓取失败项需要逐条区分非图文消息、访问限制和解析失败
- [ ] 提醒用户采集他人公众号文章的著作权与平台协议风险
