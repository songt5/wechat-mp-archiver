#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微信（Windows）本地数据目录的自动发现。

不同微信版本/安装位置下，数据目录不一样，各脚本统一从这里取路径，
避免把某台机器的绝对路径写死。

微信 4.x (xwechat)：
    %APPDATA%\\Tencent\\xwechat\\radium\\web            内置浏览器(Chromium)根目录
        ├── Local State                                含 DPAPI 加密的 AES 主密钥
        └── profiles\\<profile>\\
                Cache\\Cache_Data\\                     页面缓存（Simple Cache 格式）
                Network\\Cookies                       Cookie 库（SQLite，被独占锁）

微信 3.x (WeChat，老版本)：
    %APPDATA%\\Tencent\\WeChat\\radium\\web            结构类似

微信文件目录（聊天记录/接收的文件）：
    %USERPROFILE%\\Documents\\xwechat_files\\<账号>\\
    %USERPROFILE%\\Documents\\WeChat Files\\<账号>\\
"""
import os

PROCESS_HINTS = ("Weixin", "WeChat", "wechat", "WeChatAppEx", "wechatweb")


def roaming() -> str:
    return os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming")


def localappdata() -> str:
    return os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")


def userprofile() -> str:
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def find_web_root(verbose: bool = False) -> str | None:
    """定位微信内置浏览器（xweb / Chromium）根目录。"""
    cands = [
        os.environ.get("WX_WEB_ROOT"),
        os.path.join(roaming(), "Tencent", "xwechat", "radium", "web"),
        os.path.join(roaming(), "Tencent", "WeChat", "radium", "web"),
        os.path.join(localappdata(), "Tencent", "xwechat", "radium", "web"),
    ]
    for c in cands:
        if c and os.path.isdir(c):
            if verbose:
                print(f"[i] 微信浏览器根目录: {c}")
            return c
    return None


def list_profiles(web_root: str | None = None) -> list[str]:
    """列出所有浏览器 profile 目录，例如 webview_xxx / multitab_xxx / web_shell。"""
    wr = web_root or find_web_root()
    if not wr:
        return []
    pdir = os.path.join(wr, "profiles")
    if not os.path.isdir(pdir):
        return []
    out = []
    for d in sorted(os.listdir(pdir)):
        fp = os.path.join(pdir, d)
        if os.path.isdir(fp):
            out.append(fp)
    return out


def list_cache_dirs(web_root: str | None = None) -> list[str]:
    """列出所有 Chromium Cache 目录。"""
    out = []
    for prof in list_profiles(web_root):
        for name in ("Cache", "Code Cache", "CacheStorage"):
            c = os.path.join(prof, name)
            if os.path.isdir(c):
                out.append(c)
    return out


def list_cookie_dbs(web_root: str | None = None) -> list[str]:
    """列出所有 `Network\\Cookies` SQLite 库路径。"""
    out = []
    for prof in list_profiles(web_root):
        c = os.path.join(prof, "Network", "Cookies")
        if os.path.isfile(c):
            out.append(c)
    return out


def find_doc_root() -> str | None:
    """定位微信文件（聊天记录）根目录。"""
    for d in (os.path.join(userprofile(), "Documents", "xwechat_files"),
              os.path.join(userprofile(), "Documents", "WeChat Files")):
        if os.path.isdir(d):
            return d
    return None


def wechat_process_names() -> tuple[str, ...]:
    return PROCESS_HINTS


if __name__ == "__main__":
    wr = find_web_root(verbose=True)
    if not wr:
        print("[!] 没找到微信浏览器目录，请确认已安装并至少启动过一次 PC 微信")
        raise SystemExit(1)
    profs = list_profiles(wr)
    print(f"[i] profile 数量: {len(profs)}")
    for p in profs:
        caches = [d for d in list_cache_dirs() if p in d]
        cookies = [d for d in list_cookie_dbs() if p in d]
        print(f"    {os.path.basename(p):52s} cache={len(caches)} cookies={len(cookies)}")
    doc = find_doc_root()
    print(f"[i] 微信文件目录: {doc or '(未找到)'}")
