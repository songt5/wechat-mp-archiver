#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把从内存提取到的文章链接，全量抓取正文并归档。

产出：
  output/weixin_articles.db    SQLite 全量库
  output/articles.csv          总表（Excel 可直接打开）
  output/articles/*.md         每篇一个 Markdown
  output/合集.md               按时间排序的全部文章合并版
  output/目录.md               文章目录
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wx_collect import (Store, export_csv, export_markdown, fetch_article,  # noqa: E402
                        log, make_session, sleep_random, ts_to_date, ts_to_str)

DEFAULT_INPUT = "mem_articles.json"
DEFAULT_OUTDIR = "output"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="抓取公众号文章正文并归档成 SQLite / CSV / Markdown")
    ap.add_argument("-i", "--input", default=DEFAULT_INPUT,
                    help="文章链接清单 JSON（mem_articles.py 的产物）")
    ap.add_argument("-o", "--outdir", default=DEFAULT_OUTDIR, help="输出目录")
    ap.add_argument("-a", "--account", default="", help="公众号名，覆盖清单里的值")
    ap.add_argument("--min-delay", type=float, default=1.5, help="请求最小间隔（秒）")
    ap.add_argument("--max-delay", type=float, default=3.5, help="请求最大间隔（秒）")
    args = ap.parse_args()

    OUT = os.path.abspath(args.outdir)
    DB = os.path.join(OUT, "weixin_articles.db")
    meta = json.load(open(os.path.abspath(args.input), encoding="utf-8"))
    account = args.account or meta.get("account") or "公众号"
    arts = sorted(meta["articles"], key=lambda a: (len(a["mid"]), a["mid"], a["idx"]))
    os.makedirs(OUT, exist_ok=True)

    store = Store(DB)
    added = 0
    for a in arts:
        it = {"sn": a["sn"], "title": "", "digest": "", "author": "",
              "url": a["url"], "cover": "", "account": account,
              "publish_ts": 0, "comm_id": a["mid"], "item_idx": a["idx"],
              "content_type": 0}
        if store.upsert_list_item(it):
            added += 1
    log(f"[i] 链接入库：新增 {added} 条，库内共 {store.count()} 条")

    session = make_session()
    pend = store.pending()
    log(f"[i] 待抓正文 {len(pend)} 篇，预计 {len(pend) * 2.5 / 60:.0f} 分钟\n")

    ok = fail = 0
    t0 = time.time()
    for i, row in enumerate(pend, 1):
        res = fetch_article(session, row["url"])
        if res.get("ok"):
            store.save_content(row["sn"], res)
            ok += 1
            log(f"  [{i:>2}/{len(pend)}] OK  {res['title'][:30]:<32} {len(res['markdown']):>6} 字")
        else:
            store.mark_failed(row["sn"])
            fail += 1
            log(f"  [{i:>2}/{len(pend)}] XX  {row['sn'][:10]}  {res.get('reason')}")
        if i < len(pend):
            sleep_random(args.min_delay, args.max_delay)

    n_md = export_markdown(store, os.path.join(OUT, "articles"))
    export_csv(store, os.path.join(OUT, "articles.csv"))

    rows = [r for r in store.all_rows() if r["content_md"]]
    rows.sort(key=lambda r: r["publish_ts"])
    with open(os.path.join(OUT, "合集.md"), "w", encoding="utf-8") as f:
        f.write(f"# {account} · 全部文章\n\n")
        f.write(f"共 {len(rows)} 篇，导出时间 "
                f"{time.strftime('%Y-%m-%d %H:%M')}\n\n---\n\n")
        for r in rows:
            f.write(f"## {r['title']}\n\n")
            f.write(f"> {r['publish_at']}　|　{r['url']}\n\n")
            f.write(r["content_md"] + "\n\n---\n\n")

    with open(os.path.join(OUT, "目录.md"), "w", encoding="utf-8") as f:
        f.write(f"# {account} 文章目录（{len(rows)} 篇）\n\n")
        f.write("| # | 发布时间 | 标题 | 字数 |\n|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            f.write(f"| {i} | {r['publish_at'][:10]} | {r['title']} | "
                    f"{len(r['content_md'])} |\n")

    log(f"\n[✓] 全部完成，用时 {time.time() - t0:.0f} 秒")
    log(f"    成功 {ok} 篇 / 失败 {fail} 篇")
    log(f"    SQLite   : {DB}")
    log(f"    CSV      : {os.path.join(OUT, 'articles.csv')}")
    log(f"    Markdown : {os.path.join(OUT, 'articles')}（{n_md} 个文件）")
    log(f"    合并版   : {os.path.join(OUT, '合集.md')}")
    log(f"    目录     : {os.path.join(OUT, '目录.md')}")


if __name__ == "__main__":
    main()
