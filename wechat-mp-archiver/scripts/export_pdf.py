#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把采集到的公众号文章逐篇导出为「图文自包含」的 PDF。

思路：
  1. 从 SQLite 里取出每篇的正文 HTML（content_html）
  2. 把正文里的图片（mmbiz.qpic.cn，带防盗链）下载到本地
  3. 用一套适合中文长文阅读的模板重新排版成本地 HTML
  4. 用 Playwright 驱动本机 Chrome 打印为 PDF（图片已内嵌，不依赖网络）

产出：
  output/pdf/<序号>_<日期>_<标题>.pdf     ← 最终交付，一篇一个文件
  output/_build/<序号>/index.html         ← 中间产物（可删）
"""

import os
import re
import sys
import time
import json
import sqlite3
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

OUT = ""
DB = ""
BUILD = ""
PDFDIR = ""

ACCOUNT = ""  # 由 main() 按参数赋值，显示在页眉
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REFERER = "https://mp.weixin.qq.com/"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

PAGE_CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #fff; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
               "Segoe UI", "Helvetica Neue", sans-serif;
  font-size: 15.5px;
  line-height: 1.95;
  color: #1f1f1f;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
article { max-width: 720px; margin: 0 auto; }

header { margin: 0 0 26px; padding-bottom: 15px; border-bottom: 2px solid #222; }
h1 {
  font-size: 25px; line-height: 1.45; font-weight: 700;
  margin: 0 0 13px; letter-spacing: .3px; color: #111;
}
.meta { font-size: 12.5px; color: #8c8c8c; letter-spacing: .6px; }
.meta .acct { color: #b3271e; font-weight: 600; }
.meta .dot { margin: 0 8px; color: #ccc; }

.content { font-size: 15.5px; word-wrap: break-word; overflow-wrap: break-word; }
.content p { margin: 0 0 15px; }
.content section, .content div { margin: 0; }
.content span { font-size: inherit; }
.content h1, .content h2, .content h3 {
  font-size: 19px; font-weight: 700; margin: 28px 0 13px; line-height: 1.5;
}
.content img {
  display: block; max-width: 100%; height: auto;
  margin: 20px auto; border-radius: 3px;
}
.content strong, .content b { font-weight: 700; }
.content a { color: #576b95; text-decoration: none; word-break: break-all; }
.content blockquote {
  margin: 16px 0; padding: 10px 16px; border-left: 3px solid #ddd;
  background: #fafafa; color: #555;
}
.content table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px; }
.content td, .content th { border: 1px solid #e0e0e0; padding: 7px 10px; }

footer {
  margin-top: 38px; padding-top: 13px; border-top: 1px solid #e8e8e8;
  font-size: 10.5px; color: #a8a8a8; letter-spacing: .3px;
  display: flex; justify-content: space-between; align-items: flex-start;
}
footer .src {
  max-width: 56%; text-align: right; word-break: break-all; line-height: 1.5;
}
"""

FOOTER_TMPL = (
    '<div style="width:100%;font-size:9px;color:#b0b0b0;text-align:center;'
    'font-family:sans-serif;padding:0 4mm;">'
    '<span class="pageNumber"></span> / <span class="totalPages"></span></div>'
)


def log(msg):
    print(msg, flush=True)


def safe_filename(s: str, limit: int = 60) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")
    return (s[:limit] or "无标题")


def session_factory() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": REFERER})
    return s


def fetch_image(sess: requests.Session, url: str, dest_dir: str, idx: int):
    """下载单张图片，返回本地文件名；失败返回 None。"""
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200 or len(r.content) < 100:
            return None
        ct = (r.headers.get("Content-Type") or "").lower()
        ext = None
        for k, v in (("png", "png"), ("gif", "gif"), ("webp", "webp"),
                     ("jpeg", "jpg"), ("jpg", "jpg")):
            if k in ct:
                ext = v
                break
        if ext is None:
            m = re.search(r"wx_fmt=(\w+)", url)
            ext = {"jpeg": "jpg", "jpg": "jpg", "png": "png",
                   "gif": "gif", "webp": "webp"}.get(
                       (m.group(1).lower() if m else "jpg"), "jpg")
        fn = f"img_{idx:03d}.{ext}"
        with open(os.path.join(dest_dir, fn), "wb") as f:
            f.write(r.content)
        return fn
    except Exception:
        return None


def build_one_article(art, sess, index: int):
    """生成单篇的本地 HTML，返回 (html_path, 图片统计)。"""
    sn = art["sn"]
    title = art["title"] or "无标题"
    html_body = art["content_html"] or ""
    folder = os.path.join(BUILD, f"{index:03d}")
    os.makedirs(folder, exist_ok=True)

    # ---- 1. 抽取并下载图片 ----
    img_tags = re.findall(r"<img[^>]*>", html_body)
    ok_img = fail_img = 0
    for i, tag in enumerate(img_tags):
        m = re.search(r'data-src="([^"]+)"', tag) or re.search(r'src="([^"]+)"', tag)
        if not m:
            html_body = html_body.replace(tag, "", 1)
            continue
        url = m.group(1).replace("&amp;", "&")
        fn = fetch_image(sess, url, folder, i)
        if fn:
            ok_img += 1
            html_body = html_body.replace(tag, f'<img src="{fn}" alt="">', 1)
        else:
            fail_img += 1
            html_body = html_body.replace(tag, "", 1)

    # ---- 2. 补全外链文本、清理空段 ----
    html_body = re.sub(r"<p>\s*</p>", "", html_body)
    html_body = re.sub(r"(<br\s*/?>\s*){3,}", "<br/><br/>", html_body)

    date = (art["publish_at"] or "")[:10]
    author = (art["author"] or "").strip()
    # 作者名与公众号名相同就不重复显示
    author_html = ""
    if author and author != ACCOUNT:
        author_html = f'<span class="dot">·</span>{author}'

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<article>
  <header>
    <h1>{title}</h1>
    <div class="meta">
      <span class="acct">{ACCOUNT}</span>
      <span class="dot">·</span>{date}{author_html}
    </div>
  </header>
  <div class="content">
{html_body}
  </div>
  <footer>
    <span>{ACCOUNT} · 全 {art['publish_at'][:16] if art['publish_at'] else ''}</span>
    <span class="src">{art['url']}</span>
  </footer>
</article>
</body>
</html>"""

    html_path = os.path.join(folder, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)
    return html_path, ok_img, fail_img


def pick_browser():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def render_pdfs(jobs):
    """jobs: list of (html_path, pdf_path, title)"""
    from playwright.sync_api import sync_playwright

    exe = pick_browser()
    log(f"[i] 使用渲染引擎：{exe}")

    done = 0
    failed = []
    with sync_playwright() as p:
        kwargs = {"headless": True}
        if exe:
            kwargs["executable_path"] = exe
        browser = p.chromium.launch(**kwargs)
        ctx = browser.new_context(viewport={"width": 900, "height": 1200},
                                  device_scale_factor=2)
        page = ctx.new_page()
        page.set_default_timeout(60000)

        for html_path, pdf_path, title in jobs:
            try:
                page.goto("file:///" + html_path.replace("\\", "/"),
                          wait_until="load")
                # 等所有图片真正解码完成
                page.evaluate("""() => Promise.all(
                    Array.from(document.images).map(img =>
                        img.complete ? null : new Promise(r => {
                            img.onload = r; img.onerror = r;
                        }))
                )""")
                page.emulate_media(media="print")
                page.pdf(path=pdf_path, format="A4", print_background=True,
                         display_header_footer=True,
                         header_template="<div></div>",
                         footer_template=FOOTER_TMPL,
                         margin={"top": "16mm", "bottom": "18mm",
                                 "left": "15mm", "right": "15mm"})
                done += 1
                size = os.path.getsize(pdf_path)
                log(f"  [{done:>2}/{len(jobs)}] {size/1024:>7.0f} KB  {title[:34]}")
            except Exception:
                failed.append((title, traceback.format_exc(limit=2)))
                log(f"  [!!] 失败：{title}")

        browser.close()
    return done, failed


def main():
    import argparse

    global OUT, DB, BUILD, PDFDIR, ACCOUNT

    ap = argparse.ArgumentParser(
        description="把采集到的公众号文章逐篇导出为图文自包含的 PDF")
    ap.add_argument("-d", "--db", default="output/weixin_articles.db",
                    help="SQLite 库路径")
    ap.add_argument("-o", "--outdir", default="output", help="输出根目录")
    ap.add_argument("-a", "--account", default="", help="公众号名，显示在 PDF 页眉")
    ap.add_argument("-n", "--limit", type=int, default=0, help="只处理前 N 篇（调试用）")
    args = ap.parse_args()

    OUT = os.path.abspath(args.outdir)
    DB = os.path.abspath(args.db)
    BUILD = os.path.join(OUT, "_build")
    PDFDIR = os.path.join(OUT, "pdf")
    ACCOUNT = args.account or "公众号"

    only = args.limit or None

    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(PDFDIR, exist_ok=True)

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = [r for r in db.execute("SELECT * FROM articles")]
    rows = [r for r in rows if (r["content_html"] or "").strip()]
    rows.sort(key=lambda r: r["publish_ts"] or 0)
    if only:
        rows = rows[:only]

    log(f"[i] 待转换 {len(rows)} 篇（共 {db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]} 条记录）")

    sess = session_factory()

    # ---- 阶段一：串行生成 HTML（内部图片并发下载）----
    log("[1/2] 生成排版后的本地 HTML ...")
    t0 = time.time()
    jobs = []
    total_img = total_fail = 0
    used = set()
    for i, art in enumerate(rows, 1):
        idx = i
        html_path, ok_img, fail_img = build_one_article(art, sess, idx)
        total_img += ok_img
        total_fail += fail_img

        date = (art["publish_at"] or "")[:10]
        base = f"{idx:03d}_{date}_{safe_filename(art['title'])}"
        name, n = base, 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        jobs.append((html_path, os.path.join(PDFDIR, name + ".pdf"), art["title"] or ""))

        if i % 15 == 0 or i == len(rows):
            log(f"      {i}/{len(rows)}  用时 {time.time()-t0:.0f}s")

    log(f"[i] 图片：成功 {total_img} 张，失败 {total_fail} 张")

    # ---- 阶段二：渲染 PDF ----
    log("[2/2] 渲染 PDF ...")
    t1 = time.time()
    done, failed = render_pdfs(jobs)
    log(f"[✓] 完成 {done} 篇，用时 {time.time()-t1:.0f}s")
    if failed:
        log(f"[!] {len(failed)} 篇失败：")
        for t, e in failed:
            log(f"    - {t}: {e.splitlines()[-1] if e else ''}")

    # 汇总
    import glob
    pdfs = glob.glob(os.path.join(PDFDIR, "*.pdf"))
    size = sum(os.path.getsize(f) for f in pdfs)
    log(f"\n[✓] {PDFDIR}")
    log(f"    共 {len(pdfs)} 个 PDF，合计 {size/1024/1024:.1f} MB")

    with open(os.path.join(OUT, "pdf_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"count": len(pdfs), "images_ok": total_img,
                   "images_failed": total_fail,
                   "files": sorted(os.path.basename(x) for x in pdfs)},
                  f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
