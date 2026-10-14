#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
在微信 4.0 的所有缓存文件里按关键字搜索（自动尝试 gzip/br/zlib 解压）。
用途：找到 PC 微信请求公众号历史列表后落盘的响应体。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_wx_cache import decompress_all, read_shared  # noqa: E402
from wechat_paths import find_web_root  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python hunt_cache.py <关键字1> [关键字2] ...")
        return
    keywords = [k.encode() for k in sys.argv[1:]]
    root = os.environ.get("WX_CACHE_ROOT") or find_web_root()
    if not root:
        print("[!] 没找到微信缓存目录，可用环境变量 WX_CACHE_ROOT 显式指定")
        return
    print(f"关键字: {keywords}  根目录: {root}\n")
    total = 0
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
            if raw is None:
                continue
            blobs = [("raw", raw)] + decompress_all(raw)
            if len(raw) > 1024:
                blobs += decompress_all(raw[256:]) + decompress_all(raw[512:])
            for name, blob in blobs:
                for kw in keywords:
                    i = blob.find(kw)
                    if i >= 0:
                        total += 1
                        print(f"=== {fp}")
                        print(f"    解压={name}  关键字={kw.decode()}  @ {i}  "
                              f"命中次数={blob.count(kw)}")
                        print(f"    上下文: {blob[max(0,i-160):i+420]!r}\n")
                        break
    print(f"共 {total} 处命中。")


if __name__ == "__main__":
    main()
