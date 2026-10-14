#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
为导出的 PDF 生成一份 `_目录.md`（篇数、时间范围、每篇页数/图片数/字数）。

用法：
    python make_index.py                      # 默认读 output/pdf
    python make_index.py <pdf 目录> <公众号名>
"""
import datetime
import glob
import os
import re
import sys

from pypdf import PdfReader

PDFDIR = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "output/pdf")
ACCOUNT = sys.argv[2] if len(sys.argv) > 2 else "公众号"


def count_images(page):
    try:
        res = page.get("/Resources")
        if res is None:
            return 0
        xo = res.get("/XObject")
        if xo is None:
            return 0
        xo = xo.get_object()
        n, seen = 0, set()
        for k in xo:
            try:
                o = xo[k].get_object()
            except Exception:
                continue
            if o.get("/Subtype") == "/Image":
                r = getattr(xo[k], "idnum", k)
                if r not in seen:
                    seen.add(r)
                    n += 1
        return n
    except Exception:
        return 0


def main() -> None:
    files = sorted(glob.glob(os.path.join(PDFDIR, "*.pdf")))
    if not files:
        print(f"[!] {PDFDIR} 下没有 PDF")
        return

    recs = []
    for fp in files:
        r = PdfReader(fp)
        pages = len(r.pages)
        imgs = sum(count_images(p) for p in r.pages)
        txt = "".join((p.extract_text() or "") for p in r.pages)
        chars = len(re.sub(r"\s", "", txt))
        name = os.path.basename(fp)
        m = re.match(r"(\d+)_(\d{4}-\d{2}-\d{2})_(.*)\.pdf$", name)
        if m:
            recs.append((m.group(1), m.group(2), m.group(3), pages, imgs, chars))
        else:
            recs.append(("-", "-", name[:-4], pages, imgs, chars))

    tot_p = sum(x[3] for x in recs)
    tot_i = sum(x[4] for x in recs)
    tot_c = sum(x[5] for x in recs)

    L = [f"# {ACCOUNT} · 文章 PDF 合集\n"]
    L.append(f"- 文章数量：**{len(recs)} 篇**（一篇一个 PDF）")
    L.append(f"- 时间范围：{recs[0][1]} ~ {recs[-1][1]}")
    L.append(f"- 总页数：{tot_p} 页　总字数：{tot_c:,} 字　内嵌图片：{tot_i} 张")
    L.append(f"- 导出时间：{datetime.datetime.now():%Y-%m-%d %H:%M}")
    L.append("\n所有 PDF 均为**图文自包含**：图片已嵌入文件内部，"
             "离线、换电脑、转发都不会丢图。\n")
    L.append("| # | 发布日期 | 标题 | 页数 | 图片 | 字数 |")
    L.append("|---|---|---|---|---|---|")
    for idx, date, title, pages, imgs, chars in recs:
        L.append(f"| {idx} | {date} | {title.replace('|', chr(92) + '|')} "
                 f"| {pages} | {imgs} | {chars:,} |")

    out = os.path.join(PDFDIR, "_目录.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[✓] {out}")
    print(f"    {len(recs)} 篇 / {tot_p} 页 / {tot_c:,} 字 / {tot_i} 张图")


if __name__ == "__main__":
    main()
