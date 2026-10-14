#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
环境自检：判断当前机器能否跑通「本机微信取证 → 抓正文 → 导出 PDF」全流程。

用法：
    python doctor.py           # 基础检查（秒级）
    python doctor.py --deep    # 额外扫描缓存与微信进程内存（较慢）
"""
import ctypes
import importlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATUS = []


def hr(t: str) -> None:
    print(f"\n{'=' * 60}\n{t}\n{'=' * 60}")


def ok(m: str) -> None:
    STATUS.append(("OK", m))
    print(f"  [v] {m}")


def warn(m: str) -> None:
    STATUS.append(("WARN", m))
    print(f"  [!] {m}")


def bad(m: str) -> None:
    STATUS.append(("FAIL", m))
    print(f"  [x] {m}")


CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def check_platform() -> None:
    hr("1. 运行环境")
    print(f"  Python {sys.version.split()[0]}  |  {sys.platform}")
    if sys.platform != "win32":
        bad("本流程依赖 Windows（PC 微信 + Win32 内存/文件 API）")
    else:
        ok("Windows 平台")


def check_privilege() -> None:
    hr("2. 权限（内存扫描 / 读被锁文件需要）")
    try:
        admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        admin = False
    if admin:
        ok("当前进程是管理员")
    else:
        warn("当前不是管理员 —— 内存扫描与 SeBackupPrivilege 都会失败，"
             "请以管理员身份重启客户端")

    try:
        # 中文系统下 whoami 输出是 GBK，必须按 mbcs 解码，否则 UnicodeDecodeError
        out = subprocess.run(["whoami", "/priv"], capture_output=True,
                             encoding="mbcs", errors="replace",
                             timeout=15).stdout or ""
        for name, note in (("SeDebugPrivilege", "扫描微信进程内存"),
                           ("SeBackupPrivilege", "读被独占锁的 Cookie/缓存文件")):
            if name in out:
                ok(f"令牌含 {name}（{note}）")
            else:
                bad(f"令牌不含 {name}，无法{note}")
    except Exception as e:
        warn(f"无法查询令牌特权：{e}")


def check_wechat() -> dict:
    hr("3. 微信本地环境")
    info = {}
    try:
        from wechat_paths import (find_doc_root, find_web_root,
                                  list_cache_dirs, list_cookie_dbs,
                                  list_profiles)
    except Exception as e:
        bad(f"导入 wechat_paths 失败：{e}")
        return info

    web = find_web_root()
    if web:
        ok(f"内置浏览器目录：{web}")
        info["web_root"] = web
    else:
        bad("没找到微信内置浏览器目录 —— 请确认装了 PC 微信且启动过一次")
        return info

    profs = list_profiles(web)
    if profs:
        ok(f"浏览器 profile {len(profs)} 个")
        for p in profs:
            n_cache = len([d for d in list_cache_dirs(web) if p in d])
            has_ck = any(p in c for c in list_cookie_dbs(web))
            print(f"        {os.path.basename(p):54s} cache={n_cache} cookies={'Y' if has_ck else '-'}")
    else:
        warn("profiles 目录为空 —— 微信里还没打开过任何网页")

    doc = find_doc_root()
    if doc:
        ok(f"微信文件目录：{doc}")
    else:
        warn("没找到 xwechat_files / WeChat Files 目录")

    try:
        from scan_mem import target_pids
        pids = target_pids()
        if pids:
            ok(f"微信进程：{', '.join(f'{n}({p})' for p, n in pids)}")
        else:
            warn("没有检测到运行中的微信进程 —— 请先启动 PC 微信，"
                 "并在里面打开目标公众号主页")
    except Exception as e:
        warn(f"枚举微信进程失败：{e}")
    return info


def check_deps() -> None:
    hr("4. Python 依赖")
    mods = [
        ("requests", "抓取正文", True),
        ("bs4", "解析 HTML", True),
        ("lxml", "HTML 解析后端", True),
        ("brotli", "解压 Chromium 缓存", True),
        ("cryptography", "解密 Cookie（可选路线）", False),
        ("playwright", "渲染 PDF", True),
        ("pypdf", "校验/生成目录", True),
    ]
    for name, why, required in mods:
        try:
            importlib.import_module(name)
            ok(f"{name:14s} — {why}")
        except ImportError:
            if required:
                bad(f"{name:14s} 缺失（{why}）→ pip install {name}")
            else:
                warn(f"{name:14s} 缺失（{why}，非必需）")


def check_browser() -> None:
    hr("5. PDF 渲染引擎")
    exe = next((p for p in CHROME_CANDIDATES if os.path.exists(p)), None)
    if exe:
        ok(f"找到浏览器：{exe}")
    else:
        bad("没找到 Chrome / Edge —— 导出 PDF 需要其中之一")
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, executable_path=exe)
            pg = b.new_page()
            pg.set_content("<h1>pdf ok</h1>")
            pdf = pg.pdf()
            b.close()
        ok(f"Playwright 渲染链路可用（生成 {len(pdf)} 字节测试 PDF）")
    except Exception as e:
        bad(f"Playwright 启动失败：{type(e).__name__}: {str(e)[:160]}")


def check_deep() -> None:
    hr("6. 深度检查（缓存凭证 / 内存文章链接）")
    try:
        from creds_from_cache import collect
        from wechat_paths import find_web_root
        root = find_web_root()
        print("  扫描微信缓存中的 profile_ext 请求 ...")
        items = collect(root) if root else []
        if items:
            import datetime
            t = datetime.datetime.fromtimestamp(os.path.getmtime(items[0][1]))
            ok(f"缓存里发现 {len(items)} 条带凭证的请求（最新缓存文件时间 "
               f"{t:%Y-%m-%d %H:%M}）")
            print("        请求凭证已隐藏；不要公开缓存或诊断文件")
        else:
            warn("缓存里没有 profile_ext 凭证 —— 请在 PC 微信里打开目标公众号主页"
                 "并滚动一屏，然后重跑")
    except Exception as e:
        warn(f"深度检查失败：{e}")


def main() -> None:
    deep = "--deep" in sys.argv
    print("微信文章归档 · 环境自检")
    check_platform()
    check_privilege()
    check_wechat()
    check_deps()
    check_browser()
    if deep:
        check_deep()

    hr("结论")
    n_bad = sum(1 for s, _ in STATUS if s == "FAIL")
    n_warn = sum(1 for s, _ in STATUS if s == "WARN")
    if n_bad:
        print(f"  {n_bad} 项阻塞、{n_warn} 项警告。先解决 [x] 标记的问题。")
    elif n_warn:
        print(f"  无阻塞项，{n_warn} 项警告。通常可以继续，注意上面提示。")
    else:
        print("  全部通过，可以直接开始采集。")
    print("\n  下一步：微信里打开目标公众号主页 → 执行 mem_articles.py 取文章清单")
    raise SystemExit(1 if n_bad else 0)


if __name__ == "__main__":
    main()
