#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从微信 4.0 内置 Chromium 的 Cookies 库里解出 mp.weixin.qq.com 的 Cookie。

原理（Chromium on Windows 标准做法）：
  1. <profile>/../Local State 里 os_crypt.encrypted_key = base64(DPAPI(密钥))
     去掉开头的 "DPAPI" 5 字节后用 CryptUnprotectData 解出 AES-256 key
  2. Cookies 是 SQLite，cookies 表里 encrypted_value 前 3 字节是 "v10"，
     之后 12 字节 nonce + 密文 + 16 字节 GCM tag
  3. AES-256-GCM 解密即可拿到明文 Cookie

微信进程会独占锁住这些文件，所以统一用共享读方式取字节。
"""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import sqlite3
import sys
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from scan_wx_cache import read_shared  # noqa: E402
from wechat_paths import find_web_root, list_cookie_dbs  # noqa: E402

WEB_ROOT = find_web_root() or ""


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(data: bytes) -> bytes | None:
    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.c_char_p(data),
                                               ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        return None
    buf = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return buf


def get_master_key() -> bytes | None:
    if not WEB_ROOT:
        print("[!] 没找到微信浏览器目录，无法定位 Local State")
        return None
    p = os.path.join(WEB_ROOT, "Local State")
    raw = read_shared(p)
    if raw is None:
        return None
    j = json.loads(raw.decode("utf-8", "ignore"))
    enc = j.get("os_crypt", {}).get("encrypted_key")
    if not enc:
        print("[!] Local State 里没有 os_crypt.encrypted_key")
        return None
    blob = base64.b64decode(enc)
    print(f"[i] encrypted_key 前缀 = {blob[:5]!r}")
    if blob[:5] != b"DPAPI":
        print("[!] 不是 DPAPI 保护（可能是新的 App-Bound 加密），无法直接解")
        return None
    return dpapi_unprotect(blob[5:])


def decrypt_value(blob: bytes, key: bytes) -> str | None:
    if not blob:
        return ""
    if blob[:3] in (b"v10", b"v11"):
        try:
            return AESGCM(key).decrypt(blob[3:15], blob[15:], None).decode("utf-8", "ignore")
        except Exception:
            return None
    try:
        out = dpapi_unprotect(blob)
        return out.decode("utf-8", "ignore") if out else None
    except Exception:
        return None


def load_cookies(cookies_path: str, key: bytes | None, host_filter=("weixin",)):
    raw = read_shared(cookies_path)
    if raw is None:
        print(f"[!] 读不到 {cookies_path}")
        return []
    tmp = os.path.join(tempfile.gettempdir(), "_wx_cookies_copy.db")
    open(tmp, "wb").write(raw)
    con = sqlite3.connect(tmp)
    rows = con.execute(
        "SELECT host_key, name, encrypted_value, value, path, is_secure, "
        "expires_utc FROM cookies").fetchall()
    out = []
    for host, name, enc, val, path, sec, exp in rows:
        if host_filter and not any(f in host for f in host_filter):
            continue
        if enc:
            dec = decrypt_value(enc, key) if key else None
        else:
            dec = val
        out.append((host, name, dec, path, sec, exp))
    con.close()
    return out


def main() -> None:
    print("=== 1. 取主密钥 ===")
    key = get_master_key()
    print("[i] 主密钥:", "OK" if key else "失败")

    targets = []
    # 1) 优先用卷影副本导出的本地拷贝（微信对原始库持独占锁，直读多半失败）
    copy_dir = os.path.join(HERE, "_cookiecopy")
    if os.path.isdir(copy_dir):
        targets += [os.path.join(copy_dir, f) for f in sorted(os.listdir(copy_dir))
                    if os.path.isfile(os.path.join(copy_dir, f))]
    # 2) 再尝试直读原始库（需管理员权限 + SeBackupPrivilege）
    targets += list_cookie_dbs(WEB_ROOT or None)
    for cp in targets:
        if not os.path.exists(cp):
            continue
        import datetime
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(cp)).strftime("%m-%d %H:%M")
        print(f"\n=== 2. {os.path.basename(os.path.dirname(os.path.dirname(cp)))}  "
              f"(更新于 {mt}) ===")
        cs = load_cookies(cp, key)
        if not cs:
            print("    （无 weixin 相关 Cookie）")
            continue
        for host, name, val, path, sec, exp in cs:
            show = val if val is None else (val[:70] + "..." if len(val) > 70 else val)
            print(f"    {host:28s} {name:22s} = {show}")
        # 顺手输出可直接给 requests 用的 Cookie 头
        pairs = [f"{n}={v}" for h, n, v, *_ in cs if v]
        if pairs:
            print("\n    Cookie 头：")
            print("    " + "; ".join(pairs)[:4000])


if __name__ == "__main__":
    main()
