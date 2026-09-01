#!/usr/bin/env python3
"""Evidence-first external market radar backed by the AnySearch API.

Only public, static queries from the checked-in configuration are sent to the
provider.  Results are treated as untrusted data, normalized into a dedicated
ledger, deduplicated by canonical URL, and promoted to a market pulse only when
multiple independent source domains support the same configured theme.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

try:
    from ._safe_io import atomic_write_text, read_text_limited
except ImportError:  # direct script execution
    from _safe_io import atomic_write_text, read_text_limited


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_CONFIG = COMPANY_ROOT / "automation/market_radar_config.json"
DEFAULT_DB = COMPANY_ROOT / "marketing/market_signals.db"
DEFAULT_RUN_ROOT = COMPANY_ROOT / "marketing/runtime/market-radar"
ANYSEARCH_ENDPOINT = "https://api.anysearch.com/mcp"
ANYSEARCH_CLIENT = "company-market-radar/1.0"
UPSTREAM_COMMIT = "b1a1bae6b257f1326d2e6ed51f64b36be75065e7"
UPSTREAM_ZIP_SHA256 = "920eddb2e25f5c144c2d920a12ffdcbecdf979d38d7cb76629d91805ea78907f"

Fetcher = Callable[[str, dict[str, Any], dict[str, Any]], str]

PROMPT_INJECTION_PATTERNS = (
    "ignore previous instructions", "ignore all previous", "system prompt",
    "developer message", "reveal your prompt", "忽略之前", "忽略以上",
    "系统提示词", "执行以下命令", "调用工具", "读取密钥",
)
FORBIDDEN_SEARCH_TYPES = {
    "PeopleSearch", "EmailLookup", "x_people", "reddit_people",
    "linkedin_people",
}
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int_config(config: dict[str, Any], key: str, default: int) -> int:
    """Coerce a config value to int, falling back on malformed values.

    The radar config is hand-edited JSON; a single non-numeric value must
    not crash the whole radar cycle or the pulse builder.
    """
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default


def _float_config(config: dict[str, Any], key: str, default: float) -> float:
    """Coerce a config value to float, falling back on malformed values."""
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) else default


def _safe_query_int(value: Any, default: int) -> int:
    """Coerce a per-query numeric setting to int, falling back on malformed
    values.  Query entries live in the hand-edited radar config; a single
    non-numeric max_results must not fail the whole radar cycle.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    payload = json.loads(read_text_limited(path, max_bytes=5 * 1024 * 1024))
    if not isinstance(payload, dict):
        raise TypeError("market radar config must be an object")
    endpoint = str(payload.get("endpoint") or ANYSEARCH_ENDPOINT)
    if endpoint != ANYSEARCH_ENDPOINT:
        raise ValueError(f"market radar endpoint must remain pinned to {ANYSEARCH_ENDPOINT}")
    validate_queries(payload.get("queries") or [])
    return payload


def validate_queries(queries: Iterable[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for item in queries:
        if not isinstance(item, dict):
            raise TypeError("each market query must be an object")
        query_id = str(item.get("id") or "").strip()
        query = str(item.get("query") or "").strip()
        theme = str(item.get("theme") or "").strip()
        if not query_id or query_id in seen:
            raise ValueError(f"market query id is missing or duplicated: {query_id!r}")
        seen.add(query_id)
        if not query or len(query) > 400 or "\n" in query:
            raise ValueError(f"invalid public market query: {query_id}")
        if not theme:
            raise ValueError(f"market query theme missing: {query_id}")
        params = item.get("sub_domain_params") or {}
        if not isinstance(params, dict):
            raise TypeError(f"sub_domain_params must be an object: {query_id}")
        search_type = str(params.get("type") or "")
        if search_type in FORBIDDEN_SEARCH_TYPES:
            raise ValueError(f"privacy-sensitive search type is forbidden: {search_type}")
        lowered = query.lower()
        if any(marker in lowered for marker in ("password=", "token=", "api_key", "/home/pwn/", "@example.com")):
            raise ValueError(f"query may contain sensitive or local data: {query_id}")


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.executescript(
            """
        CREATE TABLE IF NOT EXISTS market_radar_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'running',
            query_count INTEGER NOT NULL DEFAULT 0,
            result_count INTEGER NOT NULL DEFAULT 0,
            signal_count INTEGER NOT NULL DEFAULT 0,
            pulse_count INTEGER NOT NULL DEFAULT 0,
            raw_path TEXT DEFAULT '',
            report_path TEXT DEFAULT '',
            error TEXT DEFAULT '',
            started_at TEXT NOT NULL,
            completed_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS market_signals (
            signal_id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL,
            theme TEXT NOT NULL,
            theme_title TEXT NOT NULL,
            product_line TEXT NOT NULL DEFAULT 'company',
            query_id TEXT NOT NULL,
            query_text TEXT NOT NULL,
            channel TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            source_domain TEXT NOT NULL,
            snippet TEXT NOT NULL,
            published_at TEXT DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1,
            relevance_score REAL NOT NULL DEFAULT 0,
            commercial_score REAL NOT NULL DEFAULT 0,
            freshness_score REAL NOT NULL DEFAULT 0,
            source_score REAL NOT NULL DEFAULT 0,
            total_score REAL NOT NULL DEFAULT 0,
            eligible_for_pulse INTEGER NOT NULL DEFAULT 1,
            content_risk TEXT NOT NULL DEFAULT 'clean',
            latest_run_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(theme,canonical_url)
        );
        CREATE INDEX IF NOT EXISTS idx_market_signals_theme
        ON market_signals(theme,total_score DESC,last_seen_at DESC);

        CREATE TABLE IF NOT EXISTS market_pulses (
            pulse_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES market_radar_runs(run_id),
            theme TEXT NOT NULL,
            theme_title TEXT NOT NULL,
            product_line TEXT NOT NULL DEFAULT 'company',
            summary TEXT NOT NULL,
            signal_ids_json TEXT NOT NULL DEFAULT '[]',
            source_domains_json TEXT NOT NULL DEFAULT '[]',
            source_urls_json TEXT NOT NULL DEFAULT '[]',
            independent_sources INTEGER NOT NULL DEFAULT 0,
            signal_count INTEGER NOT NULL DEFAULT 0,
            average_score REAL NOT NULL DEFAULT 0,
            max_score REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            evidence_path TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id,theme)
        );
        CREATE INDEX IF NOT EXISTS idx_market_pulses_queue
        ON market_pulses(status,score DESC,created_at);
            """
        )
        signal_columns = {row[1] for row in db.execute("PRAGMA table_info(market_signals)")}
        if "eligible_for_pulse" not in signal_columns:
            db.execute("ALTER TABLE market_signals ADD COLUMN eligible_for_pulse INTEGER NOT NULL DEFAULT 1")
        db.commit()
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def anysearch_call(tool_name: str, arguments: dict[str, Any], config: dict[str, Any]) -> str:
    """Call the pinned AnySearch JSON-RPC endpoint without auto-registration."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Anysearch-Client": ANYSEARCH_CLIENT}
    api_key = str(os.environ.get("ANYSEARCH_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(ANYSEARCH_ENDPOINT, data=payload, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect())
    timeout = min(120, max(1, _int_config(config, "timeout_seconds", 30)))
    max_bytes = min(10 * 1024 * 1024, max(4096, _int_config(config, "max_response_bytes", 2_000_000)))
    # The radar is a daily cron whose single upstream dependency is this pinned
    # endpoint; a transient network blip or a 5xx/429 gateway response must not
    # fail the whole run.  Retry bounded times with small backoff, mirroring the
    # retry discipline already used by ``security_intel.fetch``.
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with opener.open(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
            break
        except urllib.error.HTTPError as exc:
            try:
                detail = str(exc.reason or exc)
            finally:
                exc.close()
            error = RuntimeError(f"AnySearch HTTP error {exc.code}: {detail}")
            transient = exc.code in {429, 500, 502, 503, 504}
            if not transient or attempt >= 2:
                raise error from exc
            last_error = error
        except (urllib.error.URLError, TimeoutError) as exc:
            error = RuntimeError(f"AnySearch request failed: {exc}")
            if attempt >= 2:
                raise error from exc
            last_error = error
        time.sleep(1 + attempt)
    else:
        # Only reachable when every attempt failed; keep the last error visible.
        raise last_error if last_error is not None else RuntimeError("AnySearch request failed")
    if len(body) > max_bytes:
        raise RuntimeError(f"AnySearch response exceeds {max_bytes} bytes")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"AnySearch returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TypeError("AnySearch response root must be an object")
    if parsed.get("error"):
        message = parsed["error"].get("message") if isinstance(parsed["error"], dict) else parsed["error"]
        raise RuntimeError(f"AnySearch API error: {message}")
    result = parsed.get("result") or {}
    if not isinstance(result, dict):
        raise TypeError("AnySearch result must be an object")
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "")
    raise RuntimeError("AnySearch response did not contain text content")


def _sanitize_text(value: str, limit: int = 2400) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _content_risk(*values: str) -> str:
    lowered = " ".join(values).lower()
    return "prompt_injection" if any(pattern in lowered for pattern in PROMPT_INJECTION_PATTERNS) else "clean"


def canonical_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        hostname = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not hostname or "@" in parsed.netloc:
        return ""
    hostname = hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    query = []
    for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in TRACKING_QUERY_KEYS:
            continue
        query.append((key, val))
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path, urllib.parse.urlencode(query), ""))


def _published_at(text: str) -> str:
    match = re.search(r"Posted:\s*([^|\n]+)", text, re.IGNORECASE)
    if match:
        try:
            return parsedate_to_datetime(match.group(1).strip()).astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            pass
    match = re.search(r"\b(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    return ""


def parse_batch_markdown(text: str, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse AnySearch's Markdown batch output into bounded untrusted records."""
    lines = text.splitlines()
    current_query = 0
    current: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []

    def finish() -> None:
        nonlocal current
        if not current:
            return
        url = canonical_url(str(current.get("url") or ""))
        if url:
            current["canonical_url"] = url
            current["source_domain"] = urllib.parse.urlsplit(url).hostname or ""
            current["snippet"] = _sanitize_text(" ".join(current.pop("snippet_lines", [])))
            current["title"] = _sanitize_text(str(current.get("title") or ""), 400)
            current["published_at"] = _published_at(current["snippet"])
            current["content_risk"] = _content_risk(current["title"], current["snippet"])
            records.append(current)
        current = None

    for raw in lines:
        query_match = re.match(r"^## Query\s+(\d+):", raw)
        if query_match:
            finish()
            current_query = int(query_match.group(1)) - 1
            continue
        result_match = re.match(r"^###\s+\d+\.\s+(.+)$", raw)
        if result_match:
            finish()
            if 0 <= current_query < len(queries):
                query = queries[current_query]
                current = {
                    "query_id": query["id"], "query_text": query["query"],
                    "theme": query["theme"], "theme_title": query["theme_title"],
                    "product_line": query.get("product_line", "company"),
                    "channel": query.get("channel", "web"),
                    "title": result_match.group(1), "url": "", "snippet_lines": [],
                }
            continue
        if not current:
            continue
        url_match = re.match(r"^- \*\*URL\*\*:\s*(\S+)", raw)
        if url_match:
            current["url"] = url_match.group(1)
        elif raw.startswith("- "):
            current["snippet_lines"].append(raw[2:])
        elif raw and not raw.startswith("##") and not raw.startswith("---"):
            current["snippet_lines"].append(raw)
    finish()
    return records


def _contains_any(text: str, terms: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if str(term).lower() in lowered)


def score_signal(record: dict[str, Any], query: dict[str, Any], config: dict[str, Any], *, now: datetime | None = None) -> dict[str, float]:
    current = now or datetime.now(timezone.utc)
    haystack = f"{record['title']} {record['snippet']}"
    strategic = list(config.get("strategic_keywords") or []) + list(query.get("keywords") or [])
    commercial = list(config.get("commercial_keywords") or [])
    relevance = min(35.0, 8.0 + _contains_any(haystack, strategic) * 4.5)
    commercial_score = min(25.0, _contains_any(haystack, commercial) * 4.0)
    published = record.get("published_at")
    freshness = 8.0
    if published:
        try:
            parsed = datetime.fromisoformat(str(published))
            if parsed.tzinfo is None:
                # Normalize naive timestamps to UTC before comparing against the
                # aware ``current`` clock; a mixed comparison would raise
                # TypeError and abort the whole radar run on one bad record.
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (current - parsed).total_seconds() / 86400)
            freshness = 20.0 if age_days <= 7 else 15.0 if age_days <= 30 else 10.0 if age_days <= 180 else 4.0
        except (TypeError, ValueError):
            pass
    channel = str(record.get("channel") or "web")
    source_score = {"research": 18.0, "web": 14.0, "jobs": 16.0, "social": 8.0}.get(channel, 10.0)
    risk_penalty = 30.0 if record.get("content_risk") != "clean" else 0.0
    total = max(0.0, min(100.0, relevance + commercial_score + freshness + source_score - risk_penalty))
    return {
        "relevance": round(relevance, 2), "commercial": round(commercial_score, 2),
        "freshness": round(freshness, 2), "source": round(source_score, 2),
        "total": round(total, 2),
    }


def signal_eligible(record: dict[str, Any], query: dict[str, Any], scores: dict[str, float], config: dict[str, Any]) -> bool:
    if record.get("content_risk") != "clean":
        return False
    if scores["total"] < _float_config(config, "minimum_signal_score", 42):
        return False
    text = f"{record['title']} {record['snippet']}".lower()
    required_any = [str(term).lower() for term in query.get("required_any") or []]
    if required_any and not any(term in text for term in required_any):
        return False
    for group in query.get("required_all_groups") or []:
        terms = [str(term).lower() for term in group]
        if terms and not any(term in text for term in terms):
            return False
    return True


def _signal_id(theme: str, url: str) -> str:
    return "MKT-SIG-" + hashlib.sha256(f"{theme}|{url}".encode()).hexdigest()[:16]


def persist_signals(
    db: sqlite3.Connection,
    run_id: str,
    records: list[dict[str, Any]],
    queries_by_id: dict[str, dict[str, Any]],
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    timestamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    saved: list[dict[str, Any]] = []
    for record in records:
        query = queries_by_id[record["query_id"]]
        scores = score_signal(record, query, config, now=now)
        eligible = signal_eligible(record, query, scores, config)
        signal_id = _signal_id(record["theme"], record["canonical_url"])
        evidence = {
            "upstream": "anysearch", "upstream_commit": UPSTREAM_COMMIT,
            "query_id": record["query_id"], "captured_by_run": run_id,
        }
        db.execute(
            """INSERT INTO market_signals
               (signal_id,canonical_url,theme,theme_title,product_line,query_id,query_text,
                channel,title,url,source_domain,snippet,published_at,first_seen_at,last_seen_at,
                occurrences,relevance_score,commercial_score,freshness_score,source_score,
                total_score,eligible_for_pulse,content_risk,latest_run_id,evidence_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(theme,canonical_url) DO UPDATE SET
                 query_id=excluded.query_id,query_text=excluded.query_text,channel=excluded.channel,
                 title=excluded.title,url=excluded.url,source_domain=excluded.source_domain,
                 snippet=excluded.snippet,published_at=excluded.published_at,
                 last_seen_at=excluded.last_seen_at,occurrences=market_signals.occurrences+1,
                 relevance_score=excluded.relevance_score,commercial_score=excluded.commercial_score,
                 freshness_score=excluded.freshness_score,source_score=excluded.source_score,
                 total_score=excluded.total_score,eligible_for_pulse=excluded.eligible_for_pulse,
                 content_risk=excluded.content_risk,
                 latest_run_id=excluded.latest_run_id,evidence_json=excluded.evidence_json,
                 updated_at=excluded.updated_at""",
            (
                signal_id, record["canonical_url"], record["theme"], record["theme_title"],
                record.get("product_line", "company"), record["query_id"], record["query_text"],
                record["channel"], record["title"], record["url"], record["source_domain"],
                record["snippet"], record.get("published_at", ""), timestamp, timestamp,
                scores["relevance"], scores["commercial"], scores["freshness"], scores["source"],
                scores["total"], int(eligible), record["content_risk"], run_id, json.dumps(evidence, ensure_ascii=False),
                timestamp, timestamp,
            ),
        )
        saved.append({**record, **scores, "eligible_for_pulse": eligible, "signal_id": signal_id})
    return saved


def build_pulses(
    db: sqlite3.Connection,
    run_id: str,
    signals: list[dict[str, Any]],
    evidence_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        if signal.get("eligible_for_pulse"):
            grouped[signal["theme"]].append(signal)
    minimum_sources = max(2, _int_config(config, "minimum_independent_sources", 2))
    minimum_average = _float_config(config, "minimum_average_signal_score", 45)
    now = utc_now()
    pulses: list[dict[str, Any]] = []
    for theme, items in grouped.items():
        domains = sorted({item["source_domain"] for item in items})
        average = sum(float(item["total"]) for item in items) / len(items)
        maximum = max(float(item["total"]) for item in items)
        qualifies = len(domains) >= minimum_sources and average >= minimum_average
        channel_count = len({item["channel"] for item in items})
        confidence = min(
            0.90,
            0.10 + min(0.30, len(domains) * 0.08) + min(0.30, average / 200) + min(0.15, channel_count * 0.05),
        )
        score = min(95.0, average + min(20.0, max(0, len(domains) - 1) * 8.0))
        top = sorted(items, key=lambda item: item["total"], reverse=True)[:4]
        title = str(top[0]["theme_title"])
        summary = "；".join(f"{item['title']}（{item['source_domain']}）" for item in top)
        pulse_id = "MKT-PULSE-" + hashlib.sha256(f"{run_id}|{theme}".encode()).hexdigest()[:16]
        status = "new" if qualifies else "insufficient_evidence"
        pulse = {
            "pulse_id": pulse_id, "run_id": run_id, "theme": theme, "theme_title": title,
            "product_line": top[0].get("product_line", "company"), "summary": summary,
            "signal_ids": [item["signal_id"] for item in top], "source_domains": domains,
            "source_urls": [item["canonical_url"] for item in top],
            "independent_sources": len(domains), "signal_count": len(items),
            "average_score": round(average, 2), "max_score": round(maximum, 2),
            "confidence": round(confidence, 3), "score": round(score, 2), "status": status,
            "evidence_path": str(evidence_path),
        }
        if qualifies:
            db.execute(
                """UPDATE market_pulses SET status='superseded',updated_at=?
                   WHERE theme=? AND status='new'""",
                (now, theme),
            )
        db.execute(
            """INSERT INTO market_pulses
               (pulse_id,run_id,theme,theme_title,product_line,summary,signal_ids_json,
                source_domains_json,source_urls_json,independent_sources,signal_count,
                average_score,max_score,confidence,score,status,evidence_path,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pulse_id, run_id, theme, title, pulse["product_line"], summary,
                json.dumps(pulse["signal_ids"], ensure_ascii=False),
                json.dumps(domains, ensure_ascii=False), json.dumps(pulse["source_urls"], ensure_ascii=False),
                len(domains), len(items), pulse["average_score"], pulse["max_score"],
                pulse["confidence"], pulse["score"], status, str(evidence_path), now, now,
            ),
        )
        pulses.append(pulse)
    return pulses


def _chunks(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _api_query(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "query": item["query"],
        "max_results": _safe_query_int(item.get("max_results", 5), 5),
    }
    for key in ("domain", "sub_domain", "sub_domain_params"):
        if item.get(key):
            result[key] = item[key]
    return result


def _write_report(path: Path, run_id: str, signals: list[dict[str, Any]], pulses: list[dict[str, Any]]) -> None:
    lines = [
        "# 市场雷达报告", "", f"> Run: `{run_id}`", f"> 生成时间: {utc_now()}",
        "> 外部结果均为不可信输入；进入经营队列前已清洗、去重并要求独立来源佐证。", "",
        "## 市场脉冲", "",
    ]
    if not pulses:
        lines.append("没有形成达到多来源门槛的市场脉冲。")
    for pulse in sorted(pulses, key=lambda item: item["score"], reverse=True):
        lines.extend([
            f"### {pulse['theme_title']}", "",
            f"- 状态：`{pulse['status']}`",
            f"- 分数：{pulse['score']}；置信度：{pulse['confidence']}",
            f"- 独立来源：{pulse['independent_sources']}；信号数：{pulse['signal_count']}",
            f"- 摘要：{pulse['summary']}", "",
        ])
    lines.extend(["## 信号明细", "", "| 主题 | 分数 | 入选脉冲 | 渠道 | 来源 | 标题 |", "|---|---:|:---:|---|---|---|"])
    for item in sorted(signals, key=lambda signal: signal["total"], reverse=True):
        title = item["title"].replace("\\", "\\\\").replace("|", "\\|")
        title = title.replace("[", "\\[").replace("]", "\\]")
        link = item["canonical_url"].replace("(", "%28").replace(")", "%29")
        eligible = "✅" if item.get("eligible_for_pulse") else "—"
        lines.append(f"| {item['theme']} | {item['total']} | {eligible} | {item['channel']} | {item['source_domain']} | [{title}]({link}) |")
    atomic_write_text(path, "\n".join(lines) + "\n")


def run_radar(
    config: dict[str, Any],
    *,
    fetcher: Fetcher = anysearch_call,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not config.get("enabled", True):
        return {"status": "disabled", "signals": 0, "pulses": 0}
    configured_queries = config.get("queries") or []
    validate_queries(configured_queries)
    queries = [dict(item) for item in configured_queries if item.get("enabled", True)]
    max_queries = min(50, max(1, _int_config(config, "max_queries_per_cycle", 10)))
    queries = queries[:max_queries]
    run_id = f"MKT-RUN-{uuid.uuid4().hex[:12]}"
    run_root = Path(config.get("run_root") or DEFAULT_RUN_ROOT)
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(config.get("market_db") or DEFAULT_DB)
    started = utc_now()
    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO market_radar_runs
               (run_id,status,query_count,started_at,created_at,updated_at)
               VALUES (?,'running',?,?,?,?)""",
            (run_id, len(queries), started, started, started),
        )
        db.commit()
    finally:
        db.close()
    try:
        atomic_write_text(run_dir / "request.json", json.dumps({
            "run_id": run_id, "queries": queries, "endpoint": ANYSEARCH_ENDPOINT,
            "upstream_commit": UPSTREAM_COMMIT, "upstream_zip_sha256": UPSTREAM_ZIP_SHA256,
            "api_key_mode": "environment" if os.environ.get("ANYSEARCH_API_KEY") else "anonymous",
        }, ensure_ascii=False, indent=2))
    except OSError as exc:
        # The run row is already recorded as 'running'; an I/O failure writing
        # the request manifest must mark the run failed instead of leaving the
        # row stuck at 'running' and aborting the whole radar cron.
        completed = utc_now()
        db = connect(db_path)
        try:
            db.execute(
                """UPDATE market_radar_runs SET status='failed',error=?,completed_at=?,updated_at=?
                   WHERE run_id=?""",
                (str(exc)[:2000], completed, completed, run_id),
            )
            db.commit()
        finally:
            db.close()
        return {"run_id": run_id, "status": "failed", "error": str(exc), "signals": 0, "pulses": 0}

    all_records: list[dict[str, Any]] = []
    raw_sections: list[str] = []
    error = ""
    try:
        batch_size = min(5, max(1, _int_config(config, "batch_size", 5)))
        for batch_no, batch in enumerate(_chunks(queries, batch_size), 1):
            response = fetcher("batch_search", {"queries": [_api_query(item) for item in batch]}, config)
            raw_sections.append(f"<!-- batch {batch_no} -->\n{response}")
            all_records.extend(parse_batch_markdown(response, batch))
    except Exception as exc:  # noqa: BLE001 -- one failed batch must not abort the radar run
        error = str(exc)

    raw_path = run_dir / "raw-response.md"
    try:
        atomic_write_text(raw_path, "\n\n".join(raw_sections))
    except OSError as exc:
        error = error or f"failed to write raw response: {exc}"
    if error:
        completed = utc_now()
        db = connect(db_path)
        try:
            db.execute(
                """UPDATE market_radar_runs SET status='failed',raw_path=?,error=?,completed_at=?,updated_at=?
                   WHERE run_id=?""",
                (str(raw_path), error, completed, completed, run_id),
            )
            db.commit()
        finally:
            db.close()
        return {"run_id": run_id, "status": "failed", "error": error, "signals": 0, "pulses": 0}

    queries_by_id = {item["id"]: item for item in queries}
    try:
        db = connect(db_path)
        try:
            signals = persist_signals(db, run_id, all_records, queries_by_id, config, now=now)
            pulses = build_pulses(db, run_id, signals, raw_path, config)
            report_path = run_dir / "market-radar-report.md"
            _write_report(report_path, run_id, signals, pulses)
            atomic_write_text(run_dir / "market-pulses.json", json.dumps(pulses, ensure_ascii=False, indent=2))
            completed = utc_now()
            db.execute(
                """UPDATE market_radar_runs SET status='completed',result_count=?,signal_count=?,pulse_count=?,
                   raw_path=?,report_path=?,error='',completed_at=?,updated_at=? WHERE run_id=?""",
                (len(all_records), len(signals), len(pulses), str(raw_path), str(report_path), completed, completed, run_id),
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 -- persist failure status even if the happy path raised
        completed = utc_now()
        try:
            db = connect(db_path)
            try:
                db.execute(
                    """UPDATE market_radar_runs SET status='failed',raw_path=?,error=?,completed_at=?,updated_at=?
                       WHERE run_id=?""",
                    (str(raw_path), str(exc)[:2000], completed, completed, run_id),
                )
                db.commit()
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            pass
        return {"run_id": run_id, "status": "failed", "error": str(exc), "signals": 0, "pulses": 0}
    qualified = [pulse for pulse in pulses if pulse["status"] == "new"]
    return {
        "run_id": run_id, "status": "completed", "results": len(all_records),
        "signals": len(signals), "pulses": len(pulses), "qualified_pulses": len(qualified),
        "report_path": str(run_dir / "market-radar-report.md"),
    }


def pulse_snapshot(db_path: Path = DEFAULT_DB, status: str = "new") -> list[dict[str, Any]]:
    db = connect(db_path)
    try:
        sql = "SELECT * FROM market_pulses"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY score DESC,created_at DESC"
        return [dict(row) for row in db.execute(sql, params)]
    finally:
        db.close()


def mark_pulse(db_path: Path, pulse_id: str, status: str) -> None:
    allowed = {"new", "evaluated", "needs_approval", "dismissed"}
    if status not in allowed:
        raise ValueError(f"unsupported market pulse status: {status}")
    db = connect(db_path)
    try:
        changed = db.execute(
            "UPDATE market_pulses SET status=?,updated_at=? WHERE pulse_id=?",
            (status, utc_now(), pulse_id),
        )
        if changed.rowcount != 1:
            raise ValueError(f"unknown market pulse: {pulse_id}")
        db.commit()
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and score external market signals")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--pulses", action="store_true")
    parser.add_argument("--status", default="new")
    args = parser.parse_args()
    config = load_config(Path(args.config))
    db_path = Path(config.get("market_db") or DEFAULT_DB)
    if args.pulses:
        print(json.dumps(pulse_snapshot(db_path, args.status), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(run_radar(config), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
