import glob
import json
import os
import re
import sqlite3
import sys

from pypdf import PdfReader

PDFDIR = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "output/pdf")

def count_images(page):
    try:
        res = page.get("/Resources")
        if res is None: return 0
        xo = res.get("/XObject")
        if xo is None: return 0
        xo = xo.get_object()
        n = 0
        seen = set()
        for k in xo:
            try:
                o = xo[k].get_object()
            except Exception:
                continue
            if o.get("/Subtype") == "/Image":
                ref = getattr(xo[k], "idnum", k)
                if ref not in seen:
                    seen.add(ref); n += 1
        return n
    except Exception:
        return 0

files = sorted(glob.glob(os.path.join(PDFDIR, "*.pdf")))
print(f"共 {len(files)} 个 PDF\n")
if not files:
    print('[!] 没有 PDF 可检查，请先导出或检查目录路径')
    raise SystemExit(1)

bad, total_pages, total_imgs, total_chars = [], 0, 0, 0
rows = []
for fp in files:
    try:
        r = PdfReader(fp)
        pages = len(r.pages)
        imgs = sum(count_images(p) for p in r.pages)
        txt = "".join((p.extract_text() or "") for p in r.pages)
        chars = len(re.sub(r"\s", "", txt))
        total_pages += pages; total_imgs += imgs; total_chars += chars
        rows.append((os.path.basename(fp), pages, imgs, chars))
        if pages == 0 or chars < 50:
            bad.append((os.path.basename(fp), pages, chars))
    except Exception as e:
        bad.append((os.path.basename(fp), -1, str(e)[:60]))

print(f"总页数   : {total_pages}")
print(f"总文字量 : {total_chars:,} 字")
print(f"内嵌图片 : {total_imgs} 张")
print(f"平均每篇 : {total_pages/len(files):.1f} 页\n")

print("=== 含图最多的 6 篇 ===")
for n, pg, im, ch in sorted(rows, key=lambda x: -x[2])[:6]:
    print(f"  {im:>3} 张 / {pg:>2} 页  {n[:52]}")

print("\n=== 页数最多的 3 篇 ===")
for n, pg, im, ch in sorted(rows, key=lambda x: -x[1])[:3]:
    print(f"  {pg:>2} 页 / {im:>2} 图  {n[:52]}")

if bad:
    print(f"\n[!] 异常 {len(bad)} 个:")
    for b in bad: print("   ", b)
else:
    print("\n[✓] 基础检查通过：页数正常、文字可提取；图片完整性仍需对照下载日志并抽样查看")

raise SystemExit(1 if bad else 0)
