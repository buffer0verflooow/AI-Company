#!/usr/bin/env python3
"""AgentKey market radar probe — post-process step after the main radar run.

Reads the same market_radar_config.json, dispatches parallel agentkey queries
via hermes chat subprocess, and writes results into market_signals.db so the
existing pulse-building pipeline sees them as an additional data source.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._safe_io import atomic_write_text, read_text_limited, scrub_environment
except ImportError:  # direct script execution
    from _safe_io import atomic_write_text, read_text_limited, scrub_environment

COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_CONFIG = COMPANY_ROOT / "automation/market_radar_config.json"
DEFAULT_DB = COMPANY_ROOT / "marketing/market_signals.db"
WORKSPACE = Path("/home/pwn/workspace")
HERMES = os.environ.get("HERMES_EXECUTABLE", "hermes")
MAX_WORKERS = 4          # parallel agentkey queries per run
WORKER_TIMEOUT = 120     # seconds per agentkey query run
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int_config(config: dict[str, Any], key: str, default: int) -> int:
    """Coerce a config value to int, falling back on malformed values.

    The probe reads the market-radar config; a single non-numeric value must
    not crash the whole probe cycle.
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


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(read_text_limited(path, max_bytes=5 * 1024 * 1024))
    if not isinstance(value, dict):
        raise TypeError("agentkey radar config must be an object")
    return value


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("""
        CREATE TABLE IF NOT EXISTS agentkey_probe_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'running',
            theme_count INTEGER NOT NULL DEFAULT 0,
            signal_count INTEGER NOT NULL DEFAULT 0,
            error TEXT DEFAULT '',
            started_at TEXT NOT NULL,
            completed_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


def _sanitize_text(value: str, limit: int = 2400) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _canonical_url(value: str) -> str:
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
    except (ValueError, AttributeError):
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
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    clean_qs = []
    tracking = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in tracking:
            continue
        clean_qs.append((key, val))
    return urlunsplit((parsed.scheme.lower(), host,
                       parsed.path or "/", urlencode(clean_qs), ""))


def _publish_date(snippet: str) -> str:
    m = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", snippet)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    return ""


def _topic_searches(topics: dict) -> list[dict]:
    """Build agentkey search queries for each unique market-radar theme."""
    searches = []
    for theme_key, theme in topics.items():
        for kw in theme.get("keywords_en", []) + theme.get("keywords_zh", []):
            searches.append({
                "theme": theme_key,
                "theme_title": theme.get("title", theme_key),
                "product_line": theme.get("product_line", "company"),
                "query": kw,
            })
    return searches


def _agentkey_prompt(theme_title: str, query: str, output_path: str) -> str:
    query_json = json.dumps(query, ensure_ascii=False)
    return f"""[AGENTKEY_RADAR_PROBE]
你是公司市场雷达的 AgentKey 探测 Worker。使用 agentkey skill（自动加载）进行 web 搜索。

任务主题：{json.dumps(theme_title, ensure_ascii=False)}
搜索查询：{json.dumps(query, ensure_ascii=False)}

执行步骤：
1. 用 execute_tool(name="agentkey_search", params={{"query": {query_json}, "num": 5}}) 搜索
2. 从结果中提取每条的有效 URL、标题和摘要
3. 将结果以 JSON 格式写入 {json.dumps(output_path, ensure_ascii=False)}，格式：
   [{{"title": "...", "url": "...", "snippet": "...", "source_domain": "..."}}, ...]
4. 不要修改输出文件外的任何内容，不要执行任何外部操作。
"""


def _run_one_query(theme_title: str, query: str, output_dir: Path) -> list[dict]:
    """Spawn a hermes chat subprocess that loads agentkey skill and runs a search."""
    output_path = output_dir / f"ak-{uuid.uuid4().hex[:12]}.json"
    prompt = _agentkey_prompt(theme_title, query, str(output_path))
    env, _dropped = scrub_environment()
    env["COMPANY_ROUTER_BYPASS"] = "1"
    env["HERMES_SESSION_SOURCE"] = "tool"
    env["HERMES_WRITE_SAFE_ROOT"] = str(output_dir.resolve())
    env["TERMINAL_CWD"] = str(output_dir.resolve())
    try:
        try:
            proc = subprocess.run(
                [HERMES, "chat", "-q", prompt, "-Q", "--source", "tool",
                 "--skills", "agentkey", "--max-turns", "8"],
                cwd=str(WORKSPACE), env=env,
                capture_output=True, text=True, timeout=WORKER_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [{"error": f"agentkey worker timed out ({WORKER_TIMEOUT}s)"}]
        except OSError as exc:
            return [{"error": f"agentkey worker failed to start: {exc}"}]

        if proc.returncode != 0:
            return [{"error": f"hermes exit {proc.returncode}: {proc.stderr[:500]}"}]

        # Wait for output file with backoff
        for _ in range(10):
            try:
                ready = output_path.is_file() and output_path.stat().st_size > 10
            except OSError:
                ready = False
            if ready:
                break
            time.sleep(1)
        else:
            return [{"error": "agentkey worker produced no output file"}]

        try:
            results = json.loads(read_text_limited(output_path, max_bytes=MAX_OUTPUT_BYTES))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
            return [{"error": f"invalid JSON from agentkey worker: {exc}"}]
        if isinstance(results, dict):
            results = [results]
        if not isinstance(results, list):
            return [{"error": "agentkey worker output must be an array or object"}]
        valid = [item for item in results if isinstance(item, dict)]
        if len(valid) != len(results):
            valid.append({"error": "agentkey worker output contained non-object items"})
        return valid
    finally:
        with suppress(OSError):
            output_path.unlink(missing_ok=True)


def _parse_agentkey_result(raw: dict, theme: str, theme_title: str,
                            product_line: str, query: str) -> dict | None:
    """Convert an agentkey result item to the market-signal record format."""
    if "error" in raw:
        return None
    url = str(raw.get("url") or "")
    title = str(raw.get("title") or "")
    snippet = str(raw.get("snippet") or raw.get("content") or "")
    if not url or not title:
        return None
    canon = _canonical_url(url)
    if not canon:
        return None
    from urllib.parse import urlsplit
    domain = urlsplit(canon).hostname or ""
    return {
        "canonical_url": canon,
        "source_domain": domain,
        "theme": theme,
        "theme_title": theme_title,
        "product_line": product_line,
        "channel": "agentkey",
        "query": query,
        "title": _sanitize_text(title, 400),
        "url": url,
        "snippet": _sanitize_text(snippet),
        "published_at": _publish_date(snippet),
    }


def _signal_id(theme: str, url: str) -> str:
    return "MKT-SIG-" + hashlib.sha256(f"{theme}|{url}".encode()).hexdigest()[:16]


def persist_results(db: sqlite3.Connection, run_id: str,
                    results: list[dict], themes: dict) -> int:
    """Write agentkey probe signals into market_signals.db."""
    saved = 0

    for item in results:
        sig_id = _signal_id(item["theme"], item["canonical_url"])

        scores = _score_signal(item, themes.get(item["theme"], {}))
        evidence = json.dumps({
            "source": "agentkey",
            "captured_by_run": run_id,
            "query": item.get("query", ""),
        }, ensure_ascii=False)

        now = utc_now()
        try:
            cursor = db.execute(
                """INSERT OR IGNORE INTO market_signals
                   (signal_id,canonical_url,theme,theme_title,product_line,query_id,query_text,
                    channel,title,url,source_domain,snippet,published_at,first_seen_at,last_seen_at,
                    occurrences,relevance_score,commercial_score,freshness_score,source_score,
                    total_score,eligible_for_pulse,content_risk,latest_run_id,
                    evidence_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,
                    ?,?,?,?,?,1,'clean',?,?,?,?)""",
                (
                    sig_id, item["canonical_url"], item["theme"], item["theme_title"],
                    item.get("product_line", "company"),
                    f"agentkey-{item['theme'][:32]}", item.get("query", ""),
                    "agentkey", item["title"], item["url"], item["source_domain"],
                    item["snippet"], item.get("published_at", ""),
                    now, now,
                    scores["relevance"], scores["commercial"], scores["freshness"],
                    scores["source"], scores["total"],
                    run_id, evidence, now, now,
                ),
            )
            if cursor.rowcount > 0:
                saved += 1
        except sqlite3.IntegrityError:
            continue
    return saved


def _score_signal(item: dict, theme: dict) -> dict:
    """Score an agentkey signal using the same criteria as market_radar.py."""
    haystack = f"{item['title']} {item['snippet']}".lower()
    strategic = (theme.get("keywords_en", []) + theme.get("keywords_zh", [])
                 + ["AI", "agent", "security", "enterprise", "智能体", "安全"])
    commercial = ["budget", "spending", "procurement", "buy", "adoption",
                  "deployment", "customer", "pricing", "paid", "hiring",
                  "预算", "采购", "付费", "客户", "部署", "采用", "招聘"]
    relevance = min(35.0, 8.0 + sum(1 for kw in strategic if kw.lower() in haystack) * 4.5)
    commercial_score = min(25.0, sum(1 for kw in commercial if kw.lower() in haystack) * 4.0)
    freshness = 8.0
    pub = item.get("published_at", "")
    if pub:
        try:
            parsed = datetime.fromisoformat(str(pub))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
            freshness = 20.0 if age <= 7 else 15.0 if age <= 30 else 10.0 if age <= 180 else 4.0
        except (TypeError, ValueError):
            pass
    source_score = 10.0  # agentkey web search — generic baseline
    total = max(0.0, min(100.0, relevance + commercial_score + freshness + source_score))
    return {
        "relevance": round(relevance, 2),
        "commercial": round(commercial_score, 2),
        "freshness": round(freshness, 2),
        "source": round(source_score, 2),
        "total": round(total, 2),
    }


def extract_themes(config: dict) -> dict:
    """Extract unique themes from the market radar config queries."""
    topics: dict = {}
    for q in config.get("queries", []):
        if not isinstance(q, dict):
            raise TypeError("each agentkey radar query must be an object")
        theme = str(q.get("theme") or "").strip()
        if not theme or not q.get("enabled", True):
            continue
        if theme not in topics:
            topics[theme] = {
                "title": q.get("theme_title", theme),
                "product_line": q.get("product_line", "company"),
                "keywords_en": [],
                "keywords_zh": [],
            }
        query_text = str(q.get("query") or "").strip()
        if not query_text or len(query_text) > 400 or "\n" in query_text:
            raise ValueError(f"invalid agentkey radar query for theme {theme!r}")
        # Classify keyword by language
        if re.search(r"[\u4e00-\u9fff]", query_text):
            topics[theme]["keywords_zh"].append(query_text)
        else:
            topics[theme]["keywords_en"].append(query_text)
    return topics


def run_probe(config: dict) -> dict:
    """Run the full agentkey radar probe cycle."""
    themes = extract_themes(config)
    if not themes:
        return {"status": "no_themes", "signals": 0}

    run_id = f"AK-RUN-{uuid.uuid4().hex[:12]}"
    run_root = Path(config.get("run_root", str(COMPANY_ROOT / "marketing" / "runtime" / "market-radar")))
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(config.get("market_db", str(DEFAULT_DB)))
    started = utc_now()

    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO agentkey_probe_runs
               (run_id,status,theme_count,started_at,created_at,updated_at)
               VALUES (?,'running',?,?,?,?)""",
            (run_id, len(themes), started, started, started),
        )
        db.commit()
    finally:
        db.close()

    searches = _topic_searches(themes)
    max_searches = min(50, max(1, _int_config(config, "agentkey_max_queries", _int_config(config, "max_queries_per_cycle", 10))))
    searches = searches[:max_searches]
    all_results: list[dict] = []
    errors: list[str] = []
    report_path = run_dir / "agentkey-probe-report.md"
    try:
        workers = max(1, min(len(searches), max(1, _int_config(config, "agentkey_max_workers", MAX_WORKERS))))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agentkey-radar") as pool:
            futures = {
                pool.submit(_run_one_query, search["theme_title"], search["query"], run_dir): (index, search)
                for index, search in enumerate(searches, 1)
            }
            for future in as_completed(futures):
                index, search = futures[future]
                print(f"[{index}/{len(searches)}] query={search['query'][:60]}...", flush=True)
                try:
                    items = future.result()
                except Exception as exc:
                    items = [{"error": f"agentkey query failed: {exc}"}]
                parsed = 0
                for item_raw in items:
                    record = _parse_agentkey_result(
                        item_raw, search["theme"], search["theme_title"],
                        search["product_line"], search["query"],
                    )
                    if record:
                        all_results.append(record)
                        parsed += 1
                    elif "error" in item_raw:
                        errors.append(str(item_raw["error"]))
                print(f"  → {parsed} signals", flush=True)

        db = connect(db_path)
        try:
            saved = persist_results(db, run_id, all_results, themes)
            db.commit()
        finally:
            db.close()

        lines = [
            "# AgentKey 雷达探测报告", "", f"> Run: `{run_id}`",
            f"> 生成时间: {utc_now()}", f"> 主题数: {len(themes)}",
            f"> 查询数: {len(searches)}", f"> 新信号: {saved}",
            f"> 错误: {len(errors)}" if errors else "", "", "## 主题",
        ]
        for value in themes.values():
            lines.append(
                f"- **{value['title']}** "
                f"({len(value['keywords_en'])} en + {len(value['keywords_zh'])} zh)"
            )
        lines.append("")
        if errors:
            lines.extend(["## 错误", ""])
            lines.extend(f"- {_sanitize_text(error, 200)}" for error in errors[:10])
            lines.append("")
        atomic_write_text(report_path, "\n".join(lines) + "\n")

        completed = utc_now()
        db = connect(db_path)
        try:
            db.execute(
                """UPDATE agentkey_probe_runs
                   SET status='completed',signal_count=?,completed_at=?,updated_at=?
                   WHERE run_id=?""",
                (saved, completed, completed, run_id),
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        completed = utc_now()
        try:
            db = connect(db_path)
            try:
                db.execute(
                    """UPDATE agentkey_probe_runs
                       SET status='failed',error=?,completed_at=?,updated_at=? WHERE run_id=?""",
                    (str(exc)[:2000], completed, completed, run_id),
                )
                db.commit()
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            pass
        return {
            "run_id": run_id, "status": "failed", "error": str(exc),
            "themes": len(themes), "searches": len(searches), "signals": 0,
        }

    return {
        "run_id": run_id,
        "status": "completed",
        "themes": len(themes),
        "searches": len(searches),
        "signals": saved,
        "errors": len(errors),
        "report_path": str(report_path),
    }


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    config = load_config(config_path)
    result = run_probe(config)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
