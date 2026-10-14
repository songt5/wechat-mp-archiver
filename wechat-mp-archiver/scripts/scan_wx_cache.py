#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从微信 4.0 内置 Chromium 缓存里挖 profile_ext 页面 / getmsg 响应。
微信 PC 版 4.0 用 xweb(Chromium) 渲染公众号页面，请求过的页面会落在
radium/web/profiles/<profile>/Cache/Cache_Data/ 里。这些文件是 Chromium
Simple Cache 格式，正文可能是 gzip/brotli/zlib 压缩的。
本脚本扫描缓存目录，尝试各种解压方式，把命中关键字的内容 dump 出来。
"""
import ctypes
import ctypes.wintypes
import gzip
import os
import re
import sys
import zlib

try:
    import brotli
except ImportError:
    brotli = None

KEYS = [b"profile_ext", b"general_msg_list", b"appmsg_token", b"content_url"]
SCORE_KEYS = [b"appmsg_token", b"getmsg", b"general_msg_list", b"nickname",
              b"js_profile", b"msgList", b"content_url", b"__biz"]

# 可选：指定目标公众号的 __biz（base64），只关注它的缓存条目
_biz = os.environ.get("WX_BIZ", "").encode()
if _biz:
    KEYS.insert(0, _biz)

GENERIC_READ = 0x80000000
FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.wintypes.DWORD),
                ("HighPart", ctypes.wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", ctypes.wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", ctypes.wintypes.DWORD),
                ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


def enable_privilege(name: str) -> bool:
    """启用当前令牌里的某项特权（如 SeBackupPrivilege）。"""
    advapi = ctypes.windll.advapi32
    k32 = ctypes.windll.kernel32
    wt = ctypes.wintypes
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.CloseHandle.restype = wt.BOOL
    advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi.OpenProcessToken.restype = wt.BOOL
    advapi.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(_LUID)]
    advapi.LookupPrivilegeValueW.restype = wt.BOOL
    advapi.AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.POINTER(_TOKEN_PRIVILEGES), wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    advapi.AdjustTokenPrivileges.restype = wt.BOOL
    handle = wt.HANDLE()
    if not advapi.OpenProcessToken(k32.GetCurrentProcess(),
                                   TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                   ctypes.byref(handle)):
        return False
    luid = _LUID()
    if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
        k32.CloseHandle(handle)
        return False
    tp = _TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Privileges[0].Luid = luid
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
    k32.SetLastError(0)
    try:
        adjusted = advapi.AdjustTokenPrivileges(handle, False, ctypes.byref(tp), 0, None, None)
        return bool(adjusted) and k32.GetLastError() == 0
    finally:
        k32.CloseHandle(handle)


def read_shared(path: str) -> bytes | None:
    """
    读取被微信进程独占的文件。
      1) 普通共享读（缓存文件用这个就够）
      2) 失败则启用 SeBackupPrivilege + FILE_FLAG_BACKUP_SEMANTICS 强读
         （Cookie 库这类被独占锁住的文件走这条，需要管理员权限）
    """
    try:
        k32 = ctypes.windll.kernel32
        wt = ctypes.wintypes
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
        k32.CreateFileW.restype = wt.HANDLE
        k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        k32.ReadFile.restype = wt.BOOL
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.CloseHandle.restype = wt.BOOL
        size = os.path.getsize(path)
        for flags, priv in ((0, False), (FILE_FLAG_BACKUP_SEMANTICS, True)):
            if priv and not enable_privilege("SeBackupPrivilege"):
                continue
            h = k32.CreateFileW(ctypes.c_wchar_p(path), GENERIC_READ,
                                FILE_SHARE_ALL, None, OPEN_EXISTING, flags, None)
            if h == INVALID_HANDLE:
                continue
            try:
                buf = ctypes.create_string_buffer(size)
                read = ctypes.c_ulong(0)
                if not k32.ReadFile(h, buf, ctypes.c_ulong(size),
                                    ctypes.byref(read), None):
                    continue
                return buf.raw[:read.value]
            finally:
                k32.CloseHandle(h)
        return None
    except Exception:
        try:
            return open(path, "rb").read()
        except OSError:
            return None


def decompress_all(b: bytes):
    out = []
    if brotli:
        try:
            out.append(("br", brotli.decompress(b)))
        except Exception:
            pass
    try:
        out.append(("gzip", gzip.decompress(b)))
    except Exception:
        pass
    for name, wbits in (("zlib", 15), ("raw", -15)):
        try:
            out.append((name, zlib.decompress(b, wbits)))
        except Exception:
            pass
    try:
        d = zlib.decompressobj(15)
        r = d.decompress(b)
        if len(r) > 200:
            out.append(("zlib_partial", r))
    except Exception:
        pass
    return out


def scan(path: str) -> None:
    hits = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                if os.path.getsize(fp) < 200:
                    continue
            except OSError:
                continue
            raw = read_shared(fp)
            if raw is None:
                continue
            if not any(k in raw for k in KEYS):
                continue
            hits += 1
            cands = [("raw", raw)] + decompress_all(raw) + decompress_all(raw[256:]) + decompress_all(raw[512:])
            best = (0, "", b"")
            for name, blob in cands:
                s = sum(blob.count(k) for k in SCORE_KEYS)
                if s > best[0]:
                    best = (s, name, blob)
            print(f"\n=== {fp}  ({len(raw)} B)  命中，最佳解压={best[1]} 得分={best[0]}")
            blob = best[2]
            for k in KEYS:
                i = blob.find(k)
                print(f"    {k.decode():22s} @ {i}")
            for kw in (b"appmsg_token", b"content_url", b"general_msg_list", b"pass_ticket"):
                i = blob.find(kw)
                if i >= 0:
                    print(f"    [{kw.decode()}] {blob[max(0,i-80):i+200]!r}")
            # 把解压后的内容存下来，便于后续深挖
            outp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_cache_dump_%d.bin" % hits)
            open(outp, "wb").write(blob)
            print(f"    已 dump -> {outp}")
    if hits == 0:
        print("没有命中任何缓存条目。")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        from wechat_paths import find_web_root
        root = find_web_root()
        if not root:
            print("[!] 没找到微信浏览器目录。可显式传入路径："
                  "python scan_wx_cache.py <cache 目录>")
            raise SystemExit(1)
        targets = [root]
    for t in targets:
        print(f"扫描：{t}")
        scan(t)
