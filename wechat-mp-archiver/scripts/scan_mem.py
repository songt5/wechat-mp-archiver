#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
在微信相关进程的内存里搜索目标公众号的文章链接。

背景：PC 微信 4.0 用内置 Chromium(xweb) 渲染公众号主页，页面 DOM 里带着
每篇文章的 mp.weixin.qq.com/s?__biz=...&mid=...&idx=...&sn=... 链接。
磁盘缓存里没落明文，但内存里一定有。

需要 SeDebugPrivilege（管理员令牌自带，默认禁用，这里手动启用）。
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_wx_cache import enable_privilege  # noqa: E402

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}  # R, RW, WRITECOPY, XR, XRW, XWC


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("__alignment1", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__alignment2", wt.DWORD),
    ]


def target_pids() -> list[tuple[int, str]]:
    """列出微信相关进程。"""
    out = []
    k32 = ctypes.windll.kernel32
    # 用 tasklist 更省事，但这里直接遍历进程快照
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    k32.Process32First.restype = wt.BOOL
    k32.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    k32.Process32Next.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.CloseHandle.restype = wt.BOOL
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.c_void_p(-1).value:
        raise OSError('CreateToolhelp32Snapshot failed')
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    if k32.Process32First(snap, ctypes.byref(pe)):
        while True:
            name = pe.szExeFile.decode("mbcs", "ignore")
            if any(s in name for s in ("Weixin", "WeChat", "wechat")):
                out.append((int(pe.th32ProcessID), name))
            if not k32.Process32Next(snap, ctypes.byref(pe)):
                break
    k32.CloseHandle(snap)
    return out


def scan_pid(pid: int, needle: bytes, max_mb: int = 1500) -> list[bytes]:
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.OpenProcess.restype = wt.HANDLE
    k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION64), ctypes.c_size_t]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.ReadProcessMemory.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.CloseHandle.restype = wt.BOOL
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return []
    found = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION64()
    total = 0
    limit = max_mb * 1024 * 1024
    try:
        while addr < 0x7FFFFFFFFFFF and total < limit:
            if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi),
                                      ctypes.sizeof(mbi)):
                break
            base = mbi.BaseAddress
            size = mbi.RegionSize
            if (mbi.State == MEM_COMMIT and mbi.RegionSize > 0
                    and not (mbi.Protect & PAGE_GUARD)
                    and (mbi.Protect & 0xFF) in READABLE
                    and size <= 0x4000000):
                buf = ctypes.create_string_buffer(size)
                read = ctypes.c_size_t(0)
                if k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, size,
                                         ctypes.byref(read)):
                    data = buf.raw[:read.value]
                    total += len(data)
                    start = 0
                    while True:
                        i = data.find(needle, start)
                        if i < 0:
                            break
                        found.append(data[max(0, i - 60):i + 400])
                        start = i + 1
                        if len(found) > 400:
                            return found
            addr = base + size
    finally:
        k32.CloseHandle(h)
    return found


def main() -> None:
    needle = sys.argv[1].encode() if len(sys.argv) > 1 else b"mp.weixin.qq.com/s?"
    _biz = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("WX_BIZ", "")
    biz = _biz.encode()
    print("启用 SeDebugPrivilege:", enable_privilege("SeDebugPrivilege"))
    pids = target_pids()
    print(f"微信相关进程: {pids}\n")
    urls = OrderedDict()
    for pid, name in pids:
        try:
            hits = scan_pid(pid, needle)
        except Exception as e:
            print(f"  PID {pid} {name}: 扫描失败 {e}")
            continue
        print(f"  PID {pid} {name}: 命中 {len(hits)} 处")
        for h in hits:
            for m in re.finditer(rb"https?://mp\.weixin\.qq\.com/s\?[A-Za-z0-9_\-\.\?=&%:]{20,900}", h):
                u = m.group(0).decode("utf-8", "ignore")
                urls.setdefault(u, 0)
                urls[u] += 1
    print(f"\n提取到 {len(urls)} 条不同的文章链接：")
    for i, (u, n) in enumerate(list(urls.items())[:60], 1):
        mark = "  <<< 目标号" if biz.decode() in u else ""
        print(f"{i:>3}. [{n}次] {u[:150]}{mark}")


if __name__ == "__main__":
    main()
