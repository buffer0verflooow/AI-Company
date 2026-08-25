#!/usr/bin/env python3
"""Daily security intel collector for the company (安全情报采集).

Three independent channels feed one report:

  1. RSS  - FreeBuf / SecurityWeek / Krebs / Schneier / The Record /
            Threatpost / Trail of Bits / PortSwigger / Unit42 /
            SentinelOne / Cloudflare / Project Zero
  2. API  - dblp (NDSS/SP/CCS/USENIX accepted papers), arXiv cs.CR,
            CISA KEV (incremental)
  3. HTML - NDSS accepted papers page, 看雪论坛 homepage, 安全客,
            先知社区, doonsec 公众号库

Outputs:
  - marketing/runtime/security-intel/<YYYY-MM-DD>/report.md  每日简报
  - marketing/security_intel.db                               去重存储

All sources are public, read-only, curl-fetched with a browser UA.
Results are untrusted data; HTML is stripped before any processing.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

try:
    from ._safe_io import atomic_write_text, sqlite_connection
except ImportError:  # direct script execution
    from _safe_io import atomic_write_text, sqlite_connection

COMPANY_ROOT = Path("/home/pwn/workspace/company")
DB_PATH = COMPANY_ROOT / "marketing/security_intel.db"
RUN_ROOT = COMPANY_ROOT / "marketing/runtime/security-intel"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
FETCH_TIMEOUT = 20

# ---------------------------------------------------------------------------
# source registry: (id, kind, url, title, max_items)
# kind: rss | api_json | api_atom | html
# ---------------------------------------------------------------------------
SOURCES: list[dict[str, Any]] = [
    # --- RSS 中文 ---
    {"id": "freebuf",   "kind": "rss",  "url": "https://www.freebuf.com/feed",
     "title": "FreeBuf", "max": 15, "cat": "中文社区"},
    # --- RSS 英文媒体 ---
    {"id": "securityweek", "kind": "rss", "url": "https://www.securityweek.com/feed/",
     "title": "SecurityWeek", "max": 8, "cat": "英文媒体"},
    {"id": "krebs",     "kind": "rss",  "url": "https://krebsonsecurity.com/feed/",
     "title": "KrebsOnSecurity", "max": 5, "cat": "英文媒体"},
    {"id": "schneier",  "kind": "rss",  "url": "https://www.schneier.com/feed/atom/",
     "title": "Schneier on Security", "max": 5, "cat": "英文媒体"},
    {"id": "therecord", "kind": "rss",  "url": "https://therecord.media/feed",
     "title": "The Record", "max": 8, "cat": "英文媒体"},
    {"id": "threatpost","kind": "rss",  "url": "https://threatpost.com/feed/",
     "title": "Threatpost", "max": 8, "cat": "英文媒体"},
    # --- RSS 研究博客 ---
    {"id": "trailofbits","kind": "rss", "url": "https://blog.trailofbits.com/feed/",
     "title": "Trail of Bits", "max": 6, "cat": "研究博客"},
    {"id": "portswigger","kind": "rss", "url": "https://portswigger.net/research/rss",
     "title": "PortSwigger Research", "max": 6, "cat": "研究博客"},
    {"id": "unit42",    "kind": "rss",  "url": "https://unit42.paloaltonetworks.com/feed/",
     "title": "Unit 42", "max": 6, "cat": "研究博客"},
    {"id": "sentinelone","kind": "rss","url": "https://www.sentinelone.com/blog/feed/",
     "title": "SentinelOne", "max": 6, "cat": "研究博客"},
    {"id": "cloudflare","kind": "rss",  "url": "https://blog.cloudflare.com/rss/",
     "title": "Cloudflare Blog", "max": 6, "cat": "研究博客"},
    # --- API 论文 ---
    {"id": "arxiv-csCR","kind": "api_atom",
     "url": "https://export.arxiv.org/api/query?search_query=cat:cs.CR&sortBy=submittedDate&sortOrder=descending&max_results=15",
     "title": "arXiv cs.CR 最新论文", "max": 15, "cat": "学术论文"},
    {"id": "cisa-kev",  "kind": "api_json",
     "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
     "title": "CISA KEV (已利用漏洞)", "max": 20, "cat": "漏洞情报"},
    # --- HTML 中文社区 ---
    {"id": "kanxue",    "kind": "html", "url": "https://bbs.kanxue.com/",
     "title": "看雪论坛", "max": 12, "cat": "中文社区"},
    {"id": "anquanke",  "kind": "html", "url": "https://www.anquanke.com/",
     "title": "安全客", "max": 10, "cat": "中文社区"},
    {"id": "xianzhi",   "kind": "html", "url": "https://xz.aliyun.com/news",
     "title": "先知社区", "max": 10, "cat": "中文社区"},
    # --- X 账号 (via nitter.net RSS, 2026-08-11 实测可用) ---
    {"id": "x-simonw",       "kind": "nitter", "url": "https://nitter.net/simonw/rss",
     "title": "X @simonw (AI/LLM安全)", "max": 10, "cat": "X 一手"},
    {"id": "x-wunderwuzzi23","kind": "nitter", "url": "https://nitter.net/wunderwuzzi23/rss",
     "title": "X @wunderwuzzi23 (AI红队)", "max": 10, "cat": "X 一手"},
    {"id": "x-taviso",       "kind": "nitter", "url": "https://nitter.net/taviso/rss",
     "title": "X @taviso (0day)", "max": 10, "cat": "X 一手"},
    {"id": "x-llm_sec",      "kind": "nitter", "url": "https://nitter.net/llm_sec/rss",
     "title": "X @llm_sec (LLM安全聚合)", "max": 10, "cat": "X 一手"},
]

# HTML sources that need a TLS workaround (-k)
TLS_INSECURE = {"anquanke"}

# ---------------------------------------------------------------------------
# 战略赛道规则 (对齐选题池: A 攻防实战 / B EDR对抗 / C Agent治理 / D 变现)
# 关键词命中: 标题 ×3, 摘要 ×1; 最高分赛道胜出; 0 分 → general(不入池)
# ---------------------------------------------------------------------------
TRACK_RULES: dict[str, dict[str, Any]] = {
    "A": {
        "name": "A 攻防实战",
        "keywords": [
            "prompt injection", "提示注入", "jailbreak", "越狱", "LLM attack",
            "agent takeover", "智能体劫持", "AI red team", "AI 红队", "红队",
            "sandbox escape", "沙箱逃逸", "AI vulnerability", "AI 漏洞",
            "LLM 漏洞", "agent attack", "AI worm", "智能体攻击", "AI malware",
            "模型窃取", "model extraction", "supply chain attack", "供应链攻击",
            "0day", "零日", "exploit", "漏洞利用", "PoC", "pwn", "逃逸",
            "vulnerability analysis", "漏洞分析", "逆向", "reverse engineering",
            "RCE", "远程代码执行", "提权", "privilege escalation", "heapdump",
        ],
    },
    "B": {
        "name": "B EDR对抗",
        "keywords": [
            "EDR", "endpoint", "端点", "evasion", "绕过", "免杀", "hook",
            "BPF", "injection", "投毒", "process injection", "AV", "antivirus",
            "检测规避", "detection evasion", "malware", "恶意软件", "rootkit",
            "kernel", "内核", "提权", "UAC bypass", "APT", "C2", "后门",
            "backdoor", "lateral movement", "横向移动", "persistence", "持久化",
        ],
    },
    "C": {
        "name": "C Agent安全治理",
        "keywords": [
            "governance", "治理", "CISO", "合规", "compliance", "regulation",
            "policy", "政策", "framework", "框架", "enterprise", "企业",
            "adoption", "落地", "budget", "预算", "audit", "审计", "standard",
            "标准", "OWASP", "NIST", "监管", "agentic AI", "智能体安全",
            "risk management", "风险管理", "security controls", "安全控制",
            "identity", "身份", "authorization", "授权", "AI act", "人工智能法",
        ],
    },
    "D": {
        "name": "D 变现向",
        "keywords": [
            "pricing", "定价", "service", "服务", "consulting", "咨询",
            "training", "培训", "bounty", "赏金", "漏洞赏金", "revenue",
            "收入", "job", "招聘", "hiring", "career", "就业", "monetize",
            "变现", "market size", "市场规模", "business", "商业",
        ],
    },
}

TRACK_ORDER = ["A", "B", "C", "D"]


def _kw_in(kw_l: str, text_l: str) -> bool:
    """Keyword match: word-boundary for ASCII terms, substring for CJK."""
    if re.fullmatch(r"[\x00-\x7f]+", kw_l):  # pure ASCII → word boundary
        return re.search(r"(?<![a-z0-9])" + re.escape(kw_l) + r"(?![a-z0-9])", text_l) is not None
    return kw_l in text_l


def classify_track(item: dict[str, Any]) -> str:
    """Score item against strategy tracks.  Returns track id or 'general'."""
    # CISA KEV entries are vulnerability intel, not content-track material
    if item.get("source") == "cisa-kev":
        return "kev"
    title = (item.get("title") or "").lower()
    summary = (item.get("summary") or "").lower()
    # 广告过滤: 渠道招募/合伙人招商类不进选题池
    ad_markers = ["招募", "招聘", "合伙人", "渠道", "招商", "实习"]
    if any(m in (item.get("title") or "") for m in ad_markers):
        return "ad"
    best_score = 0
    best_track = "general"
    for tid in TRACK_ORDER:
        score = 0
        for kw in TRACK_RULES[tid]["keywords"]:
            kw_l = kw.lower()
            if _kw_in(kw_l, title):
                score += 3
            elif _kw_in(kw_l, summary):
                score += 1
        if score > best_score:
            best_score = score
            best_track = tid
    return best_track

# date window: only items published within this many days are "fresh"
FRESH_DAYS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------
def fetch(url: str, insecure: bool = False) -> str:
    """Fetch URL via curl (browser UA, no proxy), return decoded body.

    Retries once: some domains (e.g. securityweek.com) intermittently resolve
    to the mihomo fake-ip range (198.18.x.x) → TLS connect error 35.  A retry
    often lands on the real A record.
    """
    last_err: Exception | None = None
    for attempt in range(3):
        cmd = [
            "curl", "-s", "-L", "-m", str(FETCH_TIMEOUT), "--noproxy", "*",
            "-A", USER_AGENT,
        ]
        if insecure:
            cmd.append("-k")
        cmd.append(url)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FETCH_TIMEOUT + 10, check=False)
            if proc.returncode == 0:
                return proc.stdout
            last_err = RuntimeError(f"curl exit {proc.returncode}")
        except subprocess.TimeoutExpired as e:
            last_err = e
        if attempt < 2:
            import time
            time.sleep(1.5 * (attempt + 1))
    raise last_err if last_err else RuntimeError("fetch failed")


def strip_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def clean_title(raw: str) -> str:
    t = strip_html(raw)
    return t[:200]


# ---------------------------------------------------------------------------
# channel parsers
# ---------------------------------------------------------------------------
def parse_nitter(body: str, src: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """X 账号 RSS (via nitter.net).  Standard RSS 2.0 items:
    title=推文全文, dc:creator=原作者, link=nitter status URL, pubDate 标准.
    """
    items: list[dict[str, Any]] = []
    for m in re.finditer(r"<item>(.*?)</item>", body, re.DOTALL):
        chunk = m.group(1)
        t = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", chunk, re.DOTALL)
        creator = re.search(r"<dc:creator>(.*?)</dc:creator>", chunk, re.DOTALL)
        link = re.search(r"<link>(.*?)</link>", chunk, re.DOTALL)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", chunk, re.DOTALL)
        if not t:
            continue
        title = clean_title(t.group(1))
        if not title:
            continue
        # nitter link → x.com link (user/status/id)
        url = link.group(1).strip() if link else ""
        url = re.sub(r"^https://nitter\.net/", "https://x.com/", url)
        url = re.sub(r"#m$", "", url)
        published = None
        if pub:
            try:
                published = parsedate_to_datetime(pub.group(1).strip()).astimezone(timezone.utc).isoformat(timespec="seconds")
            except Exception:
                published = None
        author = creator.group(1).strip() if creator else ""
        # strip RT by @xxx: / R to @xxx: prefixes for classification
        clean_title_text = re.sub(r"^(RT by @[^:]+: |R to @[^:]+: )", "", title)
        items.append({
            "source": src["id"], "source_title": src["title"], "cat": src["cat"],
            "title": clean_title_text, "url": url, "published": published,
            "authors": f"@{author}" if author else "",
        })
    return items[: src["max"]]


def parse_rss(body: str, src: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for m in re.finditer(r"<item>(.*?)</item>|<entry>(.*?)</entry>", body, re.DOTALL):
        chunk = m.group(1) or m.group(2) or ""
        t = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", chunk, re.DOTALL)
        link = re.search(r"<link[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", chunk, re.DOTALL)
        link_href = re.search(r'<link[^>]*href="([^"]+)"', chunk)
        pub = re.search(r"<pubDate>(.*?)</pubDate>|<updated>(.*?)</updated>|<published>(.*?)</published>", chunk, re.DOTALL)
        title = clean_title(t.group(1)) if t else ""
        if not title:
            continue
        url = ""
        if link:
            url = link.group(1).strip()
        elif link_href:
            url = link_href.group(1)
        if url.startswith("//"):
            url = "https:" + url
        published = None
        if pub:
            raw_date = (pub.group(1) or pub.group(2) or pub.group(3) or "").strip()
            try:
                published = parsedate_to_datetime(raw_date).astimezone(timezone.utc).isoformat(timespec="seconds")
            except Exception:
                published = None
        items.append({"source": src["id"], "source_title": src["title"], "cat": src["cat"],
                      "title": title, "url": url, "published": published})
    return items[: src["max"]]


def parse_arxiv_atom(body: str, src: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for m in re.finditer(r"<entry>(.*?)</entry>", body, re.DOTALL):
        chunk = m.group(1)
        t = re.search(r"<title[^>]*>(.*?)</title>", chunk, re.DOTALL)
        link = re.search(r'<link[^>]*href="([^"]+)"', chunk)
        pub = re.search(r"<published>(.*?)</published>", chunk, re.DOTALL)
        authors = re.findall(r"<name>(.*?)</name>", chunk)
        title = clean_title(t.group(1)) if t else ""
        if not title:
            continue
        items.append({
            "source": src["id"], "source_title": src["title"], "cat": src["cat"],
            "title": title, "url": link.group(1) if link else "",
            "published": pub.group(1).strip() if pub else None,
            "authors": ", ".join(a[:60] for a in authors[:3]),
        })
    return items[: src["max"]]


def parse_cisa_kev(body: str, src: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    vulns = data.get("vulnerabilities", [])
    items = []
    for v in vulns:
        cve = str(v.get("cveID", ""))
        vendor = str(v.get("vendorProject", ""))
        product = str(v.get("product", ""))
        desc = str(v.get("shortDescription", ""))[:150]
        rv = str(v.get("requiredAction", ""))[:80]
        items.append({
            "source": src["id"], "source_title": src["title"], "cat": src["cat"],
            "title": f"{cve} | {vendor} {product}",
            "url": f"https://nvd.nist.gov/vuln/detail/{cve}",
            "published": v.get("dateAdded"),
            "authors": rv,
            "summary": desc,
        })
    return items[: src["max"]]


def parse_kanxue(body: str, src: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """看雪主页: 帖子标题 + 链接."""
    items = []
    seen = set()
    # 标题在 <a ...>标题</a>, 链接多为 thread-xxx
    for m in re.finditer(r'<a[^>]+href="(/thread-[^"]+)"[^>]*>(.*?)</a>', body, re.DOTALL):
        url = "https://bbs.kanxue.com" + m.group(1)
        title = clean_title(m.group(2))
        if not title or len(title) < 6 or title in seen or "回复" in title or "查看" in title:
            continue
        seen.add(title)
        items.append({"source": src["id"], "source_title": src["title"], "cat": src["cat"],
                      "title": title, "url": url, "published": None})
        if len(items) >= src["max"]:
            break
    return items


def parse_anquanke(body: str, src: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """安全客首页: 文章列表链接 /post/id-xxxx."""
    items = []
    seen = set()
    for m in re.finditer(r'<a[^>]+href="(/post/id/\d+)"[^>]*>(.*?)</a>', body, re.DOTALL):
        url = "https://www.anquanke.com" + m.group(1)
        title = clean_title(m.group(2))
        if not title or len(title) < 6 or title in seen:
            continue
        seen.add(title)
        items.append({"source": src["id"], "source_title": src["title"], "cat": src["cat"],
                      "title": title, "url": url, "published": None})
        if len(items) >= src["max"]:
            break
    return items


def parse_xianzhi(body: str, src: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """先知社区 news 页: 文章链接 /news/数字 (相对或绝对 URL)."""
    items = []
    seen = set()
    for m in re.finditer(r'<a[^>]+href="([^"]*/news/\d+)"[^>]*>(.*?)</a>', body, re.DOTALL):
        href = m.group(1)
        url = href if href.startswith("http") else "https://xz.aliyun.com" + href
        title = clean_title(m.group(2))
        if not title or len(title) < 6 or title in seen:
            continue
        seen.add(title)
        items.append({"source": src["id"], "source_title": src["title"], "cat": src["cat"],
                      "title": title, "url": url, "published": None})
        if len(items) >= src["max"]:
            break
    return items


PARSERS = {
    "rss": parse_rss,
    "nitter": parse_nitter,
    "api_atom": parse_arxiv_atom,
    "api_json": parse_cisa_kev,
    "html": None,  # dispatched by source id below
}
HTML_PARSERS = {
    "kanxue": parse_kanxue,
    "anquanke": parse_anquanke,
    "xianzhi": parse_xianzhi,
}


def collect_source(src: dict[str, Any], now: datetime) -> tuple[str, list[dict[str, Any]]]:
    """Fetch + parse one source.  Returns (status, items)."""
    try:
        body = fetch(src["url"], insecure=src["id"] in TLS_INSECURE)
    except Exception as e:
        return f"ERROR {type(e).__name__}", []
    if src["kind"] == "html":
        parser = HTML_PARSERS.get(src["id"])
        if parser is None:
            return "ERROR no-parser", []
        return "ok", parser(body, src, now)
    parser = PARSERS.get(src["kind"])
    if parser is None:
        return "ERROR no-parser", []
    return "ok", parser(body, src, now)


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def init_db(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS intel_items (
            item_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_title TEXT NOT NULL,
            cat TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            published TEXT,
            authors TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            first_seen TEXT NOT NULL,
            track TEXT DEFAULT 'general'
        );
        CREATE INDEX IF NOT EXISTS idx_intel_items_source ON intel_items(source);
        CREATE INDEX IF NOT EXISTS idx_intel_items_published ON intel_items(published);
        """
    )
    # migrate older DBs that lack the track column
    cols = {row[1] for row in db.execute("PRAGMA table_info(intel_items)").fetchall()}
    if "track" not in cols:
        db.execute("ALTER TABLE intel_items ADD COLUMN track TEXT DEFAULT 'general'")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intel_items_track ON intel_items(track)")
    # backfill track for rows persisted before the track column existed
    db.execute(
        "UPDATE intel_items SET track = 'general' WHERE track IS NULL OR track = ''"
    )


def item_id(item: dict[str, Any]) -> str:
    import hashlib
    key = f"{item['source']}|{item['url']}|{item['title']}".encode()
    return hashlib.sha256(key).hexdigest()[:16]


def persist_items(db: sqlite3.Connection, items: list[dict[str, Any]], now: datetime) -> int:
    new_count = 0
    now_s = now.isoformat(timespec="seconds")
    for it in items:
        iid = item_id(it)
        cur = db.execute("SELECT 1 FROM intel_items WHERE item_id = ?", (iid,))
        if cur.fetchone():
            continue
        track = classify_track(it)
        db.execute(
            "INSERT INTO intel_items (item_id, source, source_title, cat, title, url, published, authors, summary, first_seen, track)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iid, it["source"], it.get("source_title", ""), it.get("cat", ""),
             it["title"], it.get("url", ""), it.get("published"), it.get("authors", ""),
             it.get("summary", ""), now_s, track),
        )
        new_count += 1
    return new_count


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def build_report(results: list[tuple[str, list[dict[str, Any]]]], new_count: int, now: datetime) -> str:
    date_str = now.strftime("%Y-%m-%d")
    lines = [
        f"# 安全情报日报 {date_str}",
        "",
        f"> 采集时间: {utc_now()} ｜ 新增条目: {new_count}",
        "",
    ]
    # classify every item into a strategy track
    for status, items in results:
        if status != "ok" and not status.endswith(": ok"):
            continue
        for it in items:
            it["track"] = classify_track(it)

    all_items = [it for status, items in results
                 if status == "ok" or status.endswith(": ok")
                 for it in items]

    by_track: dict[str, list[dict[str, Any]]] = {}
    for it in all_items:
        t = it.get("track", "general")
        by_track.setdefault(t, []).append(it)

    kev_count = len(by_track.get("kev", []))
    ad_count = len(by_track.get("ad", []))
    track_stats = " / ".join(
        f"{TRACK_RULES[t]['name']} {len(by_track.get(t, []))}" for t in TRACK_ORDER
    ) + f" / KEV {kev_count} / 泛安全 {len(by_track.get('general', []))} / 广告 {ad_count}"
    lines.append(f"共 {len(all_items)} 条 ｜ {track_stats}")
    lines.append("")

    def is_fresh(it: dict[str, Any]) -> bool:
        pub = it.get("published")
        if not pub:
            return True  # HTML sources carry no date → assume fresh
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)  # naive date (e.g. CISA dateAdded) → UTC
            return (now - dt).days <= FRESH_DAYS
        except Exception:
            return True

    # ---- 选题池就绪区 (战略贴合, 优先展示) ----
    lines.append("## 🎯 选题池就绪 (战略赛道匹配)")
    lines.append("")
    for t in TRACK_ORDER:
        items = [it for it in by_track.get(t, []) if is_fresh(it)]
        stale = len(by_track.get(t, [])) - len(items)
        if not items and stale == 0:
            continue
        lines.append(f"### {TRACK_RULES[t]['name']} ({len(items)})" + (f" +{stale} 条超窗" if stale else ""))
        lines.append("")
        for it in items[:8]:
            title = it["title"].replace("|", "｜")
            src = it.get("source_title", "")
            if it.get("published"):
                lines.append(f"- [{title}]({it['url']}) ({it['published'][:10]} · {src})")
            else:
                lines.append(f"- [{title}]({it['url']}) ({src})")
        lines.append("")

    # ---- KEV 漏洞情报 (独立区, 按 CISA dateAdded 倒序, 展示最新 15 条) ----
    # CISA's KEV feed is ordered ascending by dateAdded, so the feed order must
    # not be trusted for display: without the sort the section would show the
    # oldest entries and silently drop the newest (most actionable) ones.
    kev_items = by_track.get("kev", [])
    if kev_items:
        lines.append(f"## ⚠️ KEV 已利用漏洞 ({len(kev_items)} 条, 按 CISA dateAdded 倒序)")
        lines.append("")
        for it in sorted(kev_items, key=lambda item: str(item.get("published") or ""), reverse=True)[:15]:
            title = it["title"].replace("|", "｜")
            lines.append(f"- [{title}]({it['url']}) (CISA {it.get('published', '')[:10]})")
        lines.append("")

    # ---- 泛安全 (不入池, 备查) ----
    general = by_track.get("general", [])
    if general:
        lines.append("## 📋 泛安全 (不入选题池, 备查)")
        lines.append("")
        for it in general[:15]:
            title = it["title"].replace("|", "｜")
            src = it.get("source_title", "")
            if it.get("published"):
                lines.append(f"- [{title}]({it['url']}) ({it['published'][:10]} · {src})")
            else:
                lines.append(f"- [{title}]({it['url']}) ({src})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 采集状态")
    lines.append("")
    for status, items in results:
        lines.append(f"- {status}: {len(items)} 条")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Daily security intel collector")
    parser.add_argument("--date", help="report date YYYY-MM-DD (default: today)")
    parser.add_argument("--limit-sources", help="comma-separated source ids (debug)")
    parser.add_argument("--no-persist", action="store_true", help="skip DB writes (debug)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.date:
        now = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    sources = SOURCES
    if args.limit_sources:
        wanted = {s.strip() for s in args.limit_sources.split(",")}
        sources = [s for s in SOURCES if s["id"] in wanted]

    results: list[tuple[str, list[dict[str, Any]]]] = []
    all_items: list[dict[str, Any]] = []
    for src in sources:
        status, items = collect_source(src, now)
        results.append((f"{src['id']}: {status}", items))
        all_items.extend(items)

    new_count = 0
    if not args.no_persist:
        with sqlite_connection(DB_PATH) as db:
            init_db(db)
            new_count = persist_items(db, all_items, now)

    date_dir = RUN_ROOT / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(results, new_count, now)
    report_path = date_dir / "report.md"
    atomic_write_text(report_path, report)

    print(f"report: {report_path}")
    print(f"items collected: {len(all_items)}, new: {new_count}")
    for status, items in results:
        print(f"  {status}: {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
