#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从微信内置浏览器缓存中提取所有 mp.weixin.qq.com 的请求 URL，
重点是带凭证的 profile_ext / getmsg 请求。

这些 URL 里含 __biz / uin / key / pass_ticket / appmsg_token，
是「抓包党」通常要靠 F12 手动复制才能拿到的东西 —— 只要用过 PC 微信
打开过目标号的历史消息页，它们就已经躺在磁盘上了。

用法：
    python creds_from_cache.py                # 自动发现微信缓存目录
    python creds_from_cache.py <缓存根目录>    # 指定目录
    python creds_from_cache.py --plain        # 只输出 URL，便于管道处理
"""
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_wx_cache import read_shared  # noqa: E402
from wechat_paths import find_web_root  # noqa: E402

URL_RE = re.compile(rb"https://mp\.weixin\.qq\.com/[A-Za-z0-9_\-/\.\?=&%:+;,@!~\*'\(\)]{20,3000}")
KEYWORD = re.compile(rb"(profile_ext|action=getmsg|appmsg_extract)")


def collect(root: str) -> list[tuple[str, str, float]]:
    found: dict[str, tuple[str, float]] = {}
    for dirpath, _dirs, files in os.walk(root):
        if "cache" not in dirpath.lower():
            continue
        for fn in files:
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) < 300:
                    continue
            except OSError:
                continue
            raw = read_shared(fp)
            if raw is None or b"mp.weixin.qq.com" not in raw:
                continue
            for m in URL_RE.finditer(raw):
                if not KEYWORD.search(m.group(0)):
                    continue
                u = m.group(0).decode("utf-8", "replace")
                if u not in found:
                    try:
                        mt = os.path.getmtime(fp)
                    except OSError:
                        mt = 0
                    found[u] = (fp, mt)
    return sorted(((u, fp, mt) for u, (fp, mt) in found.items()),
                  key=lambda kv: kv[2], reverse=True)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    plain = "--plain" in sys.argv

    root = args[0] if args else os.environ.get("WX_CACHE_ROOT") or find_web_root()
    if not root or not os.path.isdir(root):
        print("[!] 没找到微信浏览器目录。请确认已用 PC 微信打开过目标公众号页面，"
              "或显式传入缓存根目录。")
        raise SystemExit(1)

    items = collect(root)
    if not items:
        print("没找到 profile_ext / getmsg 请求。\n"
              "  → 请在 PC 微信里打开目标公众号主页并滚动一下，然后重试。")
        return

    if plain:
        for u, _fp, _mt in items:
            print(u)
        return

    print(f"共找到 {len(items)} 条相关请求（按缓存文件时间倒序）：\n")
    for i, (u, fp, mt) in enumerate(items, 1):
        t = datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")
        print(f"--- [{i}] 文件时间 {t}")
        print(f"    {u}\n")


if __name__ == "__main__":
    main()
