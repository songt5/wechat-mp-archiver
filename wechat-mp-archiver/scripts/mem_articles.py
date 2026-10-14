#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从微信进程内存里提取目标公众号当前已加载的文章链接。

做法：以 __biz 为锚点搜索内存，取每个命中点周围大段上下文，
既能拿到文章 URL，也能顺带捞到页面 JSON 里的标题/时间。
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import re
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_wx_cache import enable_privilege
from urllib.parse import parse_qs, urlparse
from scan_mem import scan_pid, target_pids  # noqa: E402

if len(sys.argv) < 2:
    print(__doc__)
    print("用法: python mem_articles.py <__biz> [公众号名] [输出 json]")
    print("示例: python mem_articles.py YOUR_BIZ 示例公众号 mem_articles.json")
    raise SystemExit(1)

BIZ = sys.argv[1]
ACCOUNT = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = sys.argv[3] if len(sys.argv) > 3 else "mem_articles.json"

URL_RE = re.compile(
    rb"https?://mp\.weixin\.qq\.com/s\?[A-Za-z0-9_\-\.\?=&%:]{20,1200}")
TITLE_RE = re.compile(rb'"title"\s*:\s*"((?:[^"\\]|\\.){1,200})"')
DATE_RE = re.compile(rb'"(?:create_time|send_time|date_time|publish_time|update_time)"\s*:\s*"?(\d{9,11})"?')

contexts = []
if ctypes.sizeof(ctypes.c_void_p) != 8:
    raise SystemExit('请使用 64 位 Python')
if not enable_privilege('SeDebugPrivilege'):
    raise SystemExit('无法启用 SeDebugPrivilege，请以管理员身份运行')
print("已启用 SeDebugPrivilege")
pids = target_pids()
print(f"扫描 {len(pids)} 个微信进程，锚点 {BIZ}\n")
for pid, name in pids:
    try:
        hits = scan_pid(pid, BIZ.encode(), max_mb=2500)
    except Exception as e:
        print(f"  {pid} {name}: 失败 {e}")
        continue
    if hits:
        print(f"  {pid} {name}: {len(hits)} 处")
    contexts.extend(hits)

print(f"\n共收集上下文 {len(contexts)} 段")

arts = OrderedDict()
if os.path.isfile(OUT):
    with open(OUT, encoding='utf-8') as f:
        previous = json.load(f)
    if previous.get('biz') != BIZ:
        raise SystemExit('输出清单属于其他公众号，请使用不同输出文件')
    for a in previous.get('articles', []):
        arts[(str(a['mid']), str(a.get('idx', 1)))] = a
titles = []
for ctx in contexts:
    for m in URL_RE.finditer(ctx):
        u = m.group(0).decode("utf-8", "ignore")
        q = {k: v[0] for k, v in parse_qs(urlparse(u.replace('&amp;', '&')).query).items()}
        if q.get('__biz') != BIZ:
            continue
        mid, idx, sn, chksm = (q.get("mid", ""), q.get("idx", "1"),
                               q.get("sn", ""), q.get("chksm", ""))
        if not (mid and sn) or not idx.isdigit():
            continue
        key = (mid, idx)
        if key not in arts:
            arts[key] = {"mid": mid, "idx": int(idx or 1), "sn": sn,
                         "chksm": chksm,
                         "url": u.replace('&amp;', '&'),
                         "hits": 0}
        if chksm and not arts[key].get('chksm'):
            arts[key]['chksm'] = chksm
            arts[key]['url'] = u.replace('&amp;', '&')
        arts[key]["hits"] = arts[key].get('hits', 0) + 1
    for m in TITLE_RE.finditer(ctx):
        t = m.group(1).decode("utf-8", "ignore")
        if t and t not in titles:
            titles.append(t)

items = sorted(arts.values(), key=lambda a: (len(a["mid"]), a["mid"], a["idx"]))
print(f"去重后得到 {len(items)} 篇不同文章\n")
for a in items:
    print(f"  mid={a['mid']:>12s} idx={a['idx']} hits={a['hits']:>3} {a['url'][:78]}")

json.dump({"account": ACCOUNT, "biz": BIZ, "count": len(items),
           "articles": items, "titles_seen": titles},
          open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n已保存 -> {OUT}（另记录了 {len(titles)} 个疑似标题）")
if titles:
    print("疑似标题示例:", titles[:15])
