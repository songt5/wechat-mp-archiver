# 微信公众号文章归档 · WeChat MP Archiver

**让值得保存的文章，成为可以检索、整理和离线阅读的资料库。**

WeChat MP Archiver 是一个面向 Windows PC 微信的文章归档 Skill：从已加载的公众号历史列表中提取文章链接，批量保存正文，再导出 SQLite、CSV、Markdown 和逐篇 PDF。适合整理自己的公众号、沉淀获授权的研究资料，或建立个人阅读档案。

| 想做什么 | 可以得到什么 |
|---|---|
| 留存文章，方便离线阅读 | 逐篇 PDF，下载成功的图片嵌入文件 |
| 建立可继续整理的知识库 | 逐篇 Markdown、合集和文章目录 |
| 查看、筛选和分析文章 | CSV 表格与 SQLite 数据库 |
| 交给 AI 助手执行归档流程 | 完整 Skill 指令、脚本和排错资料 |

**免费开源 · MIT 许可证 · 支持修改与再分发**。代码许可不包含文章和图片的内容授权。

英文简介：A Windows-first AI skill for archiving loaded WeChat public-account articles into Markdown, PDF, CSV, and SQLite.

本仓库同时包含 AI 助手的 Skill 指令和可单独运行的 Python 脚本。**不保证获取全部历史文章**：微信页面采用分页或懒加载，提取范围取决于用户加载过的内容、客户端版本和内存状态。

## 功能

- 从本机微信进程内存提取目标公众号已加载的文章链接。
- 按 `mid/idx` 合并同一公众号的多次清单，减少重复条目。
- 抓取正文并保存为 SQLite、CSV、逐篇 Markdown、合集和目录。
- 下载正文图片，用 Playwright 渲染逐篇 PDF。
- 检查 PDF 页数、可提取文字和直接嵌入的图片对象。

图片下载失败时 PDF 可能缺图，统计检查也不能证明原文图片齐全，需要对照日志并抽样查看。

## 环境要求

- Windows；内存扫描必须使用 **64 位 Python 3.10 或更高版本**。
- 已安装、已登录的 PC 微信。本包包含微信 3.x/4.x 常见路径发现逻辑，实际兼容性需在使用者的客户端验证。
- 内存扫描需要管理员权限和可用的 `SeDebugPrivilege`。
- 导出 PDF 需要 Chrome、Edge，或 Playwright 安装的 Chromium。
- 抓取正文和下载图片需要网络。

## 安装依赖

在仓库根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

没有 Chrome/Edge，或浏览器安装在脚本无法发现的位置时，可以安装 Chromium：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

Cookie 解密属于可选诊断，正常采集不需要。确实需要时再安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
```

依赖没有锁定具体版本；本次发布检查只覆盖检查报告列出的项目，不能视为所有依赖组合均通过。

## 作为 Skill 使用

将完整的 `wechat-mp-archiver` 文件夹放入你的 AI 助手支持的技能目录，保留 `SKILL.md`、`scripts` 和 `references` 的相对位置。不要只复制 `SKILL.md`。

向助手提供公众号的一篇文章链接和目标，例如：

> 使用 wechat-mp-archiver，把我已在本机微信中加载的这个公众号文章归档为 Markdown 和 PDF。先检查环境，并说明文章覆盖范围。

不同助手的 Skill 安装目录与发现方式可能不同，请按所用工具的规则安装；本包的 Python 依赖仍需安装到实际运行脚本的解释器中。

## 手动运行

以下命令均在仓库根目录执行。内存采集阶段请使用管理员 PowerShell；后续正文抓取和 PDF 导出通常无需管理员权限。输出放在单独的 `archive` 文件夹，避免与源码混在一起。

### 1. 环境检查

```powershell
.\.venv\Scripts\python.exe .\wechat-mp-archiver\scripts\doctor.py
```

先处理 `[x]` 项；`[!]` 项也需要根据当前流程判断。自检没有阻塞项，不代表已验证文章覆盖完整。

### 2. 确认目标公众号并加载历史列表

准备目标公众号的一篇文章链接，在 PC 微信里打开目标公众号的历史消息列表，继续滚动并等待所需时间范围加载。不要在采集前重启微信。

解析单篇文章以查看 `biz` 字段（请替换示例地址）：

```powershell
.\.venv\Scripts\python.exe .\wechat-mp-archiver\scripts\wx_collect.py article "https://mp.weixin.qq.com/s/文章短链" --out .\archive\single
```

`__biz` 是公众号标识，不是文章标题。短链、长链、验证页和已删除文章可能有不同表现，不能保证任意链接都可直接访问。

### 3. 提取已加载链接

将 `YOUR_BIZ` 替换为实际 `__biz`，将示例公众号替换为目标名称：

```powershell
New-Item -ItemType Directory -Force .\archive | Out-Null
.\.venv\Scripts\python.exe .\wechat-mp-archiver\scripts\mem_articles.py "YOUR_BIZ" "示例公众号" .\archive\mem_articles.json
```

继续滚动加载更多文章后，可以重复同一命令，结果会与同一文件内已有清单合并。不同公众号请使用不同清单和输出目录。内存释放、扫描大小和命中数量限制都可能造成遗漏。

### 4. 抓正文并归档

```powershell
.\.venv\Scripts\python.exe .\wechat-mp-archiver\scripts\fetch_articles.py -i .\archive\mem_articles.json -o .\archive\output -a "示例公众号"
```

已成功抓取的文章不会重复请求。当前实现将失败项记为 `fetched=-1`，重跑不会自动重试这些项，需要单独排查。被限流时暂停或增大间隔，例如加入 `--min-delay 4 --max-delay 9`。

### 5. 导出和检查 PDF

```powershell
.\.venv\Scripts\python.exe .\wechat-mp-archiver\scripts\export_pdf.py -d .\archive\output\weixin_articles.db -o .\archive\output -a "示例公众号"
.\.venv\Scripts\python.exe .\wechat-mp-archiver\scripts\verify_pdf.py .\archive\output\pdf
.\.venv\Scripts\python.exe .\wechat-mp-archiver\scripts\make_index.py .\archive\output\pdf "示例公众号"
```

试导出少量文章时，可以为 `export_pdf.py` 加入 `-n 3`。图片下载失败会记录在日志和 `pdf_manifest.json`；不要把“PDF 有文字”当作“图片全部完整”。

## 输出文件

```text
archive/
├── mem_articles.json
└── output/
    ├── weixin_articles.db
    ├── articles.csv
    ├── articles/*.md
    ├── 合集.md
    ├── 目录.md
    ├── pdf/*.pdf
    ├── pdf/_目录.md
    ├── pdf_manifest.json
    └── _build/              # 本地 HTML 和下载图片等中间文件
```

## 隐私与使用边界

内存、缓存和诊断输出可能含有文章链接、页面标题、Cookie 或会话凭证。只在使用者本人授权的本机微信环境运行。正常流程不需要解密 Cookie，也不会自动上传诊断信息到 GitHub。

`doctor.py --deep` 会读取缓存；`creds_from_cache.py`、`dump_cookies.py` 等诊断工具可能直接输出敏感值。不要把输出、截图、缓存、数据库、文章合集或凭证提交到公开仓库。提交前检查文件和 Git 暂存区；`.gitignore` 不能替代检查。

源码的 MIT 许可证不授予微信公众号文章或图片的使用权。请按你实际拥有的授权保存和使用内容。

## 已知限制与排错

- 只提取已加载且仍在可读内存中的链接，不保证全量历史。
- 客户端升级、页面验证、删除文章和访问限制可能导致失败。
- 旧版 `probe/collect` 接口命令保留作诊断，不作为推荐主流程。
- Cookie 诊断工具未保证适配所有微信加密格式。
- PDF 图像统计只做基础检查，未递归统计所有 Form XObject，也未与原文逐图比对。
- 本次发布前没有执行真实微信内存/缓存扫描或联网批量采集。

详见 [排错手册](wechat-mp-archiver/references/troubleshooting.md) 和 [底层机制](wechat-mp-archiver/references/internals.md)。

## 仓库结构

```text
README.md
LICENSE
.gitignore
requirements.txt
requirements-optional.txt
wechat-mp-archiver/
├── SKILL.md
├── scripts/
└── references/
```

欢迎提交 Issue 或 Pull Request。报告问题时提供 Windows、Python、微信版本、脱敏后的错误和复现步骤，不要提交 Cookie、登录凭证或原始内存/缓存。

## 许可证

[MIT](LICENSE)。Copyright (c) 2026 wechat-mp-archiver contributors.
