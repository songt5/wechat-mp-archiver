#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微信公众号正文解析、SQLite 存储与导出工具。

推荐流程：mem_articles.py 提取已加载链接，fetch_articles.py 批量归档。
单篇或本地 HTML：python wx_collect.py article <URL或HTML路径> --out output
probe / collect 是保留的旧版接口诊断命令，不保证在当前微信可用。
仅处理用户有权访问与保存的内容，诊断凭证与输出不应公开。
"""

from __future__ import annotations

import argparse
import csv
import html as html_mod
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

CST = timezone(timedelta(hours=8))

UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.40(0x18002832) NetType/WIFI Language/zh_CN"
)
UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

JUNK_LINES = re.compile(
    r"^(继续滑动看下一个|轻触阅读原文|向上滑动看下一个|预览时标签不可点|"
    r"微信扫一扫|知道了|长按识别二维码|取消|允许|分析|$$|阅读原文)$"
)


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------
def make_session(cookie: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA_IPHONE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://mp.weixin.qq.com/",
        }
    )
    if cookie:
        s.headers["Cookie"] = cookie
    return s


def sleep_random(lo: float, hi: float) -> None:
    t = random.uniform(lo, hi)
    time.sleep(t)


def ts_to_str(ts) -> str:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d %H:%M:%S")


def ts_to_date(ts) -> str:
    s = ts_to_str(ts)
    return s[:10] if s else "0000-00-00"


def safe_filename(name: str, limit: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
    return (name[:limit] or "untitled").strip()


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------
# 凭证解析
# --------------------------------------------------------------------------
class Credential:
    def __init__(self, biz="", uin="", key="", pass_ticket="", scene="124",
                 appmsg_token="", cookie="", account=""):
        self.biz = biz
        self.uin = uin
        self.key = key
        self.pass_ticket = pass_ticket
        self.scene = scene or "124"
        self.appmsg_token = appmsg_token
        self.cookie = cookie
        self.account = account

    @property
    def ok(self) -> bool:
        if not self.biz:
            return False
        return bool(self.key and self.uin) or bool(self.pass_ticket)

    def missing(self) -> list[str]:
        miss = []
        if not self.biz:
            miss.append("__biz")
        if not (self.key and self.uin) and not self.pass_ticket:
            miss.append("uin+key 或 pass_ticket（至少要有一组）")
        if not self.appmsg_token:
            miss.append("appmsg_token（可自动补，非致命）")
        return miss

    def summary(self) -> str:
        def mask(v: str) -> str:
            if not v:
                return "-"
            return v[:6] + "..." + v[-4:] if len(v) > 14 else v[:6] + "..."
        return (
            f"__biz={self.biz}\n"
            f"uin={self.uin}\n"
            f"key={mask(self.key)}\n"
            f"pass_ticket={mask(self.pass_ticket)}\n"
            f"appmsg_token={mask(self.appmsg_token)}\n"
            f"scene={self.scene}"
        )


def credential_from_url(url: str) -> Credential:
    q = parse_qs(urlparse(url).query)
    # 有些抓包 URL 把参数写在 fragment 里
    frag = urlparse(url).fragment
    if frag and "key=" in frag:
        q.update(parse_qs(frag))
    # appmsg_token 有时被拼在 query 之后，形如 ...&appmsg_token=xxx&x5=0
    if "appmsg_token" not in q:
        m = re.search(r"appmsg_token=([^&\s]+)", url)
        if m:
            q["appmsg_token"] = [m.group(1)]

    def g(*names):
        for n in names:
            if q.get(n):
                return q[n][0]
        return ""

    return Credential(
        biz=g("__biz", "biz"),
        uin=g("uin"),
        key=g("key"),
        pass_ticket=g("pass_ticket"),
        scene=g("scene") or "124",
        appmsg_token=g("appmsg_token"),
    )


def build_home_url(c: Credential) -> str:
    return (
        "https://mp.weixin.qq.com/mp/profile_ext?action=home"
        f"&__biz={c.biz}&scene={c.scene or '124'}&devicetype=iOS16.6"
        "&version=18002832&lang=zh_CN&nettype=WIFI&a8scene=3"
        f"&pass_ticket={c.pass_ticket}&wx_header=3"
    )


def enrich_from_home(session: requests.Session, c: Credential,
                     home_url: str | None = None) -> bool:
    """
    历史消息首页的 HTML 里通常内联了 appmsg_token / key / uin。
    当抓到的链接缺这些参数时，试着自动补齐，能省一轮返工。
    """
    if c.appmsg_token and (c.key or not c.pass_ticket):
        return False
    url = home_url or build_home_url(c)
    try:
        r = session.get(url, timeout=30, headers={"User-Agent": UA_IPHONE})
    except requests.RequestException:
        return False
    h = r.text
    if "请在微信客户端打开" in h or len(h) < 500:
        log("[i] 首页 HTML 拿不到（提示「请在微信客户端打开链接」），"
            "无法自动补参数，继续用抓包参数试。")
        return False
    changed = False
    for name, attr in (("appmsg_token", "appmsg_token"), ("key", "key"), ("uin", "uin")):
        if getattr(c, attr):
            continue
        v = js_var(h, name)
        if v:
            setattr(c, attr, v)
            changed = True
            log(f"[i] 已从首页 HTML 自动补齐 {name}")
    return changed



# --------------------------------------------------------------------------
# 第一步：翻页拿历史文章列表
# --------------------------------------------------------------------------
def build_getmsg_url(c: Credential, offset: int, count: int = 10) -> str:
    # 注意：pass_ticket / uin 里含 + / = 等字符，必须逐值 URL 编码，
    # 否则 + 会被服务端当成空格，key 校验直接失败。
    def q(v) -> str:
        return quote(str(v), safe="")

    parts = [
        "action=getmsg",
        "__biz=" + quote(str(c.biz), safe="="),
        "f=json",
        f"offset={offset}",
        f"count={count}",
        "is_ok=1",
        "scene=" + q(c.scene or "124"),
        "uin=" + q(c.uin),
        "key=" + q(c.key),
        "pass_ticket=" + q(c.pass_ticket),
        "wxtoken=",
        "appmsg_token=" + q(c.appmsg_token),
        "x5=0",
    ]
    return "https://mp.weixin.qq.com/mp/profile_ext?" + "&".join(parts)


def normalize_article_url(url: str) -> str:
    url = url.replace("&amp;", "&").strip()
    url = re.sub(r"#rd$", "", url)
    url = url.replace("http://mp.weixin.qq.com", "https://mp.weixin.qq.com")
    return url


def extract_sn(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    sn = (q.get("sn") or [""])[0]
    if sn:
        return sn
    m = re.search(r"/s/([A-Za-z0-9_\-]+)", url)
    return m.group(1) if m else url


def fetch_msg_page(session: requests.Session, c: Credential, offset: int) -> dict:
    url = build_getmsg_url(c, offset)
    r = session.get(url, timeout=30)
    txt = r.text.strip()
    if not txt.startswith("{"):
        raise RuntimeError(
            f"接口返回的不是 JSON（可能触发了验证页或链接已失效），长度={len(txt)}"
        )
    data = r.json()
    ret = data.get("ret", data.get("base_resp", {}).get("ret"))
    if ret not in (0, None):
        raise SessionExpired(
            f"接口返回 ret={ret} errmsg={data.get('errmsg') or data.get('base_resp', {}).get('errmsg')}"
        )
    return data


class SessionExpired(RuntimeError):
    pass


def parse_msg_page(data: dict, account: str) -> tuple[list[dict], int, bool]:
    """返回 (文章条目列表, next_offset, can_continue)"""
    items: list[dict] = []
    raw = data.get("general_msg_list", "")
    if raw:
        try:
            gl = json.loads(raw)
        except json.JSONDecodeError:
            gl = {}
        for it in gl.get("list", []):
            comm = it.get("comm_msg_info", {}) or {}
            app = it.get("app_msg_ext_info")
            if app:
                items.append(_pack(app, comm, account))
                for sub in app.get("multi_app_msg_item_list") or []:
                    items.append(_pack(sub, comm, account))
    next_offset = int(data.get("next_offset") or 0)
    can_continue = str(data.get("can_msg_continue", "0")) == "1"
    return items, next_offset, can_continue


def _pack(app: dict, comm: dict, account: str) -> dict:
    url = normalize_article_url(app.get("content_url", ""))
    return {
        "sn": extract_sn(url) if url else "",
        "title": html_mod.unescape(app.get("title", "") or ""),
        "digest": html_mod.unescape(app.get("digest", "") or ""),
        "author": app.get("author", "") or "",
        "url": url,
        "cover": app.get("cover", "") or "",
        "account": account,
        "publish_ts": int(comm.get("datetime") or 0),
        "comm_id": str(comm.get("id") or ""),
        "item_idx": int(app.get("itemidx") or 1),
        "content_type": int(comm.get("type") or 0),
    }


# --------------------------------------------------------------------------
# 第二步：抓正文
# --------------------------------------------------------------------------
def html_to_markdown(node) -> str:
    out: list[str] = []
    for el in node.children:
        if isinstance(el, str):
            t = el.replace("\u00a0", " ")
            if t.strip():
                out.append(t)
            continue
        name = (el.name or "").lower()
        if name in ("script", "style", "svg", "iframe", "mp-common-profile",
                    "mpcpc", "mp-common-mpaudio", "mpvoice"):
            continue
        if name == "img":
            src = el.get("data-src") or el.get("src") or ""
            if src:
                out.append(f"\n\n![]({src})\n\n")
        elif name == "br":
            out.append("\n")
        elif name == "a":
            txt = el.get_text(" ", strip=True)
            href = el.get("href", "")
            if href and txt:
                out.append(f"[{txt}]({href})")
            elif txt:
                out.append(txt)
        elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lvl = int(name[1])
            txt = el.get_text(" ", strip=True)
            if txt:
                out.append("\n\n" + "#" * lvl + " " + txt + "\n\n")
        elif name == "blockquote":
            inner = html_to_markdown(el).strip()
            if inner:
                out.append("\n\n" + "\n".join("> " + ln for ln in inner.splitlines()) + "\n\n")
        elif name == "li":
            txt = el.get_text(" ", strip=True)
            if txt:
                out.append("\n- " + txt)
        else:
            out.append(html_to_markdown(el))
    return "".join(out)


def clean_markdown(md: str) -> str:
    md = md.replace("\u200b", "").replace("\ufeff", "")
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    lines = []
    for ln in md.splitlines():
        s = ln.strip()
        if JUNK_LINES.match(s):
            continue
        lines.append(ln.rstrip())
    md = "\n".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def unescape_js(s: str) -> str:
    """只还原 JS 字符串字面量里常见的转义，避免破坏 UTF-8 中文。"""
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "u" and i + 5 < len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'",
                       "/": "/", "\\": "\\", "b": "\b", "f": "\f"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def js_var(html_text: str, name: str) -> str:
    """提取页面里的 JS 变量，单双引号都支持。"""
    m = re.search(
        r"var\s+%s\s*=\s*(['\"])((?:\\.|(?!\1).)*)\1" % re.escape(name), html_text
    )
    return unescape_js(m.group(2)) if m else ""


def parse_article_html(html_text: str, url: str = "") -> dict:
    soup = BeautifulSoup(html_text, "lxml")

    def var(name: str) -> str:
        return js_var(html_text, name)

    title = var("msg_title")
    if not title:
        el = soup.select_one("#activity-name")
        title = el.get_text(strip=True) if el else ""

    account = var("nickname") or var("nick_name")
    if not account:
        el = soup.select_one("#js_name")
        account = el.get_text(strip=True) if el else ""

    author = var("author")
    if not author:
        el = soup.select_one("#js_author_name")
        author = el.get_text(strip=True) if el else ""

    publish_ts = var("ct") or var("publish_time")

    content_node = soup.select_one("#js_content")
    if content_node is None:
        body = soup.body.get_text(" ", strip=True) if soup.body else ""
        return {
            "ok": False,
            "reason": "页面里没有 js_content（可能被删除、需要验证，或不是图文消息）",
            "title": title, "account": account, "author": author,
            "publish_ts": int(publish_ts) if publish_ts.isdigit() else 0,
            "markdown": "", "html": "", "url": url, "raw_len": len(html_text),
            "hint": body[:200],
        }

    for tag in content_node.select("[style*='display:none'], [style*='display: none']"):
        tag.decompose()

    content_html = "".join(str(c) for c in content_node.children)
    md = clean_markdown(html_to_markdown(content_node))

    imgs = [
        (img.get("data-src") or img.get("src") or "")
        for img in content_node.find_all("img")
    ]
    imgs = [i for i in imgs if i]

    return {
        "ok": True,
        "title": title,
        "account": account,
        "author": author,
        "publish_ts": int(publish_ts) if publish_ts.isdigit() else 0,
        "markdown": md,
        "html": content_html,
        "images": imgs,
        "url": url,
        "raw_len": len(html_text),
    }


def fetch_article(session: requests.Session, url: str, retries: int = 3) -> dict:
    last = ""
    for i in range(retries):
        try:
            r = session.get(url, timeout=30, headers={"User-Agent": UA_IPHONE})
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
            else:
                res = parse_article_html(r.text, url)
                if res.get("ok"):
                    return res
                last = res.get("reason", "")
                if "环境异常" in r.text or "验证" in (res.get("title") or ""):
                    last = "疑似触发风控验证页"
        except requests.RequestException as e:
            last = f"网络错误 {e}"
        time.sleep(2 + i * 2)
    return {"ok": False, "reason": last, "url": url, "title": ""}


# --------------------------------------------------------------------------
# 存储
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    sn          TEXT PRIMARY KEY,
    title       TEXT,
    digest      TEXT,
    author      TEXT,
    account     TEXT,
    publish_ts  INTEGER,
    publish_at  TEXT,
    url         TEXT,
    cover       TEXT,
    content_md  TEXT,
    content_html TEXT,
    images      TEXT,
    fetched     INTEGER DEFAULT 0,
    fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pub ON articles(publish_ts DESC);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def upsert_list_item(self, it: dict) -> bool:
        if not it.get("sn"):
            return False
        cur = self.db.execute("SELECT sn FROM articles WHERE sn=?", (it["sn"],))
        if cur.fetchone():
            return False
        self.db.execute(
            """INSERT OR IGNORE INTO articles
               (sn,title,digest,author,account,publish_ts,publish_at,url,cover,fetched)
               VALUES (?,?,?,?,?,?,?,?,?,0)""",
            (it["sn"], it["title"], it["digest"], it["author"], it["account"],
             it["publish_ts"], ts_to_str(it["publish_ts"]), it["url"], it["cover"]),
        )
        self.db.commit()
        return True

    def pending(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM articles WHERE fetched=0 AND url<>'' ORDER BY publish_ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return list(self.db.execute(sql))

    def save_content(self, sn: str, res: dict) -> None:
        self.db.execute(
            """UPDATE articles SET title=COALESCE(NULLIF(?,''),title),
               account=COALESCE(NULLIF(?,''),account),
               author=COALESCE(NULLIF(?,''),author),
               publish_ts=CASE WHEN ?>0 THEN ? ELSE publish_ts END,
               publish_at=CASE WHEN ?>0 THEN ? ELSE publish_at END,
               content_md=?, content_html=?, images=?, fetched=1, fetched_at=?
               WHERE sn=?""",
            (res.get("title", ""), res.get("account", ""), res.get("author", ""),
             res.get("publish_ts", 0), res.get("publish_ts", 0),
             res.get("publish_ts", 0), ts_to_str(res.get("publish_ts", 0)),
             res.get("markdown", ""), res.get("html", ""),
             json.dumps(res.get("images", []), ensure_ascii=False),
             datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"), sn),
        )
        self.db.commit()

    def mark_failed(self, sn: str) -> None:
        self.db.execute("UPDATE articles SET fetched=-1 WHERE sn=?", (sn,))
        self.db.commit()

    def all_rows(self):
        return list(self.db.execute("SELECT * FROM articles ORDER BY publish_ts DESC"))

    def count(self, fetched=None) -> int:
        if fetched is None:
            return self.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        return self.db.execute(
            "SELECT COUNT(*) FROM articles WHERE fetched=?", (fetched,)
        ).fetchone()[0]

    def set_meta(self, k: str, v) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
        self.db.commit()

    def get_meta(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default


def export_csv(store: Store, path: str) -> None:
    rows = store.all_rows()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["发布时间", "标题", "作者", "公众号", "摘要", "链接",
                    "正文字数", "图片数", "抓取状态"])
        for r in rows:
            body = r["content_md"] or ""
            imgs = json.loads(r["images"] or "[]") if r["images"] else []
            status = {1: "已抓正文", 0: "仅列表", -1: "抓取失败"}.get(r["fetched"], "?")
            w.writerow([r["publish_at"], r["title"], r["author"], r["account"],
                        r["digest"], r["url"], len(body), len(imgs), status])


def export_markdown(store: Store, outdir: str) -> int:
    os.makedirs(outdir, exist_ok=True)
    n = 0
    for r in store.all_rows():
        if not r["content_md"]:
            continue
        fn = f"{ts_to_date(r['publish_ts'])}_{safe_filename(r['title'])}.md"
        path = os.path.join(outdir, fn)
        if os.path.exists(path):
            stem, ext = os.path.splitext(fn)
            path = os.path.join(outdir, f"{stem}_{r['sn'][:6]}{ext}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {r['title']}\n\n")
            f.write(f"- 公众号：{r['account'] or ''}\n")
            f.write(f"- 作者：{r['author'] or ''}\n")
            f.write(f"- 发布时间：{r['publish_at'] or ''}\n")
            f.write(f"- 原文：{r['url']}\n\n---\n\n")
            f.write(r["content_md"])
            f.write("\n")
        n += 1
    return n


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_article(args) -> int:
    """解析单篇文章（不需要凭证）"""
    target = args.target
    if os.path.isfile(target):
        html_text = open(target, encoding="utf-8", errors="ignore").read()
        url = ""
    else:
        session = make_session(args.cookie)
        r = session.get(target, timeout=30)
        html_text, url = r.text, target

    res = parse_article_html(html_text, url)
    if not res.get("ok"):
        log(f"[x] 解析失败：{res.get('reason')}")
        if res.get("hint"):
            log(f"    页面开头：{res['hint'][:120]}")
        return 1

    outdir = args.out
    os.makedirs(outdir, exist_ok=True)
    pub = ts_to_str(res["publish_ts"])
    fn = f"{ts_to_date(res['publish_ts'])}_{safe_filename(res['title'])}.md"
    path = os.path.join(outdir, fn)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {res['title']}\n\n")
        f.write(f"- 公众号：{res['account']}\n")
        f.write(f"- 作者：{res['author']}\n")
        f.write(f"- 发布时间：{pub}\n")
        if url:
            f.write(f"- 原文：{url}\n")
        f.write(f"\n---\n\n{res['markdown']}\n")

    biz = js_var(html_text, "biz") or (parse_qs(urlparse(url).query).get("__biz") or [""])[0]
    if biz:
        log(f"    __biz：{biz}")
    else:
        log("    未识别到 __biz，请使用带 __biz 的文章长链")
    log(f"[✓] 标题：{res['title']}")
    log(f"    公众号：{res['account']}    作者：{res['author']}")
    log(f"    发布时间：{pub}")
    log(f"    正文 {len(res['markdown'])} 字，图片 {len(res['images'])} 张")
    log(f"    已保存：{path}")
    return 0


def cmd_probe(args) -> int:
    """体检：判断手上的凭证能不能用，缺什么一目了然。"""
    c = credential_from_url(args.cred or "")
    for k in ("biz", "uin", "key", "pass_ticket", "appmsg_token"):
        v = getattr(args, k, None)
        if v:
            setattr(c, k, v)
    if args.cookie:
        c.cookie = args.cookie
    if args.account:
        c.account = args.account

    log("[i] 解析到的参数：\n    " + c.summary().replace("\n", "\n    "))
    miss = c.missing()
    if miss:
        log("[!] 还缺：" + "、".join(miss))
    if not c.biz:
        log("[x] 连 __biz 都没有 —— 请确认你复制的是 profile_ext 那条请求的完整 URL。")
        return 1

    session = make_session(c.cookie)
    log("[i] 尝试自动补齐缺失参数 ...")
    enrich_from_home(session, c)

    log("[i] 试着拉第 1 页 ...")
    try:
        data = fetch_msg_page(session, c, 0)
    except SessionExpired as e:
        log(f"[x] 不可用：{e}")
        log("    → key / pass_ticket 通常只有几十分钟有效期，请重新抓一条新的 URL。")
        log("    → 若你发的是「复制链接」那种不带 key 的地址，需要改用 F12/抓包拿到完整请求 URL。")
        return 2
    except Exception as e:
        log(f"[x] 请求失败：{e}")
        return 3

    items, next_offset, can = parse_msg_page(data, c.account)
    log(f"[✓] 凭证可用！本页返回 {len(items)} 条，next_offset={next_offset}，"
        f"还能继续翻={can}")
    for it in items[:5]:
        log(f"    · {ts_to_str(it['publish_ts'])}  {it['title'][:38]}")
    if len(items) > 5:
        log(f"    · ... 等共 {len(items)} 条")
    log("")
    log(f"    直接跑全量：python wx_collect.py collect --cred \"<同一条URL>\" --full"
        + (f" --cookie \"{c.cookie}\"" if c.cookie else ""))
    return 0


def cmd_collect(args) -> int:
    c = credential_from_url(args.cred)
    if args.biz:
        c.biz = args.biz
    if args.uin:
        c.uin = args.uin
    if args.key:
        c.key = args.key
    if args.pass_ticket:
        c.pass_ticket = args.pass_ticket
    if args.cookie:
        c.cookie = args.cookie
    if args.account:
        c.account = args.account

    if not c.ok:
        log("[x] 凭证不完整。--cred 需要是完整的 profile_ext 请求 URL，"
            "且至少要有 __biz，并带 uin+key 或 pass_ticket。")
        log("    解析结果：\n" + c.summary())
        return 1

    log("[i] 凭证解析成功：\n    " + c.summary().replace("\n", "\n    "))
    session = make_session(c.cookie)
    enrich_from_home(session, c)
    store = Store(args.db)

    # ---------- 阶段 1：翻页 ----------
    if not args.no_list:
        offset = int(store.get_meta("next_offset", 0) or 0) if args.resume else 0
        if args.resume and offset:
            log(f"[i] 断点续传，从 offset={offset} 继续")
        page = 0
        total_new = 0
        while True:
            page += 1
            try:
                data = fetch_msg_page(session, c, offset)
            except SessionExpired as e:
                log(f"[!] 凭证已失效：{e}")
                log("    → 请重新抓一条新的 profile_ext URL，再用 --resume 续跑。")
                store.set_meta("next_offset", offset)
                return 2
            except Exception as e:
                log(f"[!] 翻页出错（offset={offset}）：{e}")
                store.set_meta("next_offset", offset)
                return 3

            items, next_offset, can_continue = parse_msg_page(data, args.account or "")
            new = sum(1 for it in items if store.upsert_list_item(it))
            total_new += new
            log(f"[{page:>3}页] offset={offset:<6} 本页 {len(items):>2} 条，"
                f"新增 {new:>2} 条，累计库内 {store.count()} 条")

            if args.max_pages and page >= args.max_pages:
                store.set_meta("next_offset", next_offset)
                log(f"[i] 达到 --max-pages={args.max_pages}，停在这里，"
                    f"next_offset={next_offset}")
                break
            if not can_continue or next_offset <= offset:
                log("[i] 已经翻到底，没有更多历史文章了。")
                store.set_meta("next_offset", 0)
                break
            offset = next_offset
            store.set_meta("next_offset", offset)
            sleep_random(args.delay_min, args.delay_max)

    log(f"[i] 列表阶段完成，库内共 {store.count()} 条")
    if not args.full:
        export_csv(store, os.path.join(args.out, "articles.csv"))
        log(f"[✓] 已导出列表：{os.path.join(args.out, 'articles.csv')}")
        log("    （加 --full 才会继续抓正文）")
        return 0

    # ---------- 阶段 2：抓正文 ----------
    pend = store.pending(args.limit)
    log(f"[i] 待抓正文 {len(pend)} 篇")
    ok = fail = 0
    for i, row in enumerate(pend, 1):
        res = fetch_article(session, row["url"])
        if res.get("ok"):
            store.save_content(row["sn"], res)
            ok += 1
            log(f"  [{i}/{len(pend)}] ✓ {res['title'][:34]}  ({len(res['markdown'])} 字)")
        else:
            store.mark_failed(row["sn"])
            fail += 1
            log(f"  [{i}/{len(pend)}] ✗ {row['title'][:34]}  → {res.get('reason')}")
        if i < len(pend):
            sleep_random(args.delay_min, args.delay_max)

    os.makedirs(args.out, exist_ok=True)
    n_md = export_markdown(store, os.path.join(args.out, "articles"))
    csv_path = os.path.join(args.out, "articles.csv")
    export_csv(store, csv_path)
    log("")
    log(f"[✓] 完成：正文成功 {ok} 篇，失败 {fail} 篇")
    log(f"    SQLite：{args.db}")
    log(f"    CSV   ：{csv_path}")
    log(f"    Markdown：{os.path.join(args.out, 'articles')} （{n_md} 个文件）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="微信公众号历史文章采集器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python wx_collect.py article https://mp.weixin.qq.com/s/xxxx\n"
               "  python wx_collect.py collect --cred \"<profile_ext 完整URL>\" --max-pages 2\n"
               "  python wx_collect.py collect --cred \"<profile_ext 完整URL>\" --full\n",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("article", help="解析单篇文章（无需凭证）")
    pa.add_argument("target", help="文章 URL 或本地 HTML 文件")
    pa.add_argument("--out", default="output", help="输出目录，默认 output")
    pa.add_argument("--cookie", default=None, help="可选，mp.weixin.qq.com 的 Cookie")
    pa.set_defaults(func=cmd_article)

    pp = sub.add_parser("probe", help="体检：判断凭证能不能用、缺什么")
    pp.add_argument("--cred", default=None, help="profile_ext 请求 URL（或仅带 __biz 的地址）")
    pp.add_argument("--cookie", default=None)
    pp.add_argument("--biz", default=None)
    pp.add_argument("--uin", default=None)
    pp.add_argument("--key", default=None, dest="key")
    pp.add_argument("--pass-ticket", default=None, dest="pass_ticket")
    pp.add_argument("--appmsg-token", default=None, dest="appmsg_token")
    pp.add_argument("--account", default=None)
    pp.set_defaults(func=cmd_probe)

    pc = sub.add_parser("collect", help="翻页采集历史文章，可选抓正文")
    pc.add_argument("--cred", required=True,
                    help="抓包得到的 profile_ext 完整请求 URL")
    pc.add_argument("--cookie", default=None, help="可选 Cookie 头")
    pc.add_argument("--biz", default=None)
    pc.add_argument("--uin", default=None)
    pc.add_argument("--key", default=None, dest="key")
    pc.add_argument("--pass-ticket", default=None, dest="pass_ticket")
    pc.add_argument("--account", default=None, help="公众号名称，写入结果便于识别")
    pc.add_argument("--out", default="output", help="输出目录，默认 output")
    pc.add_argument("--db", default=None, help="SQLite 路径，默认 <out>/weixin_articles.db")
    pc.add_argument("--full", action="store_true", help="列表翻完后继续抓正文")
    pc.add_argument("--limit", type=int, default=None, help="最多抓多少篇正文")
    pc.add_argument("--max-pages", type=int, default=None, help="最多翻多少页，用于试跑")
    pc.add_argument("--no-list", action="store_true", help="跳过翻页，只抓已有列表的正文")
    pc.add_argument("--resume", action="store_true", help="从上次的 next_offset 续跑")
    pc.add_argument("--delay-min", type=float, default=3.0)
    pc.add_argument("--delay-max", type=float, default=8.0)
    pc.set_defaults(func=cmd_collect)

    args = p.parse_args()
    if args.cmd == "collect":
        os.makedirs(args.out, exist_ok=True)
        if not args.db:
            args.db = os.path.join(args.out, "weixin_articles.db")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
