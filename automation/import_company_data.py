#!/usr/bin/env python3
"""Import article performance exports and observed model prices into company stores.

The importer is deliberately evidence-first: every imported row points to the
original export (or a copied company evidence file), and reruns upsert rather
than creating duplicate measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from ._safe_io import atomic_write_text
except ImportError:  # direct ``python automation/import_company_data.py`` invocation
    from _safe_io import atomic_write_text


ROOT = Path("/home/pwn/workspace/company")
ARTICLE_DB = ROOT / "marketing/article_performance.db"
FINANCE_DB = ROOT / "finance/finance_ledger.db"
ARTICLE_EVIDENCE = ROOT / "marketing/evidence/article-stats-2026-07-15/数据统计.zip"
ZENMUX_EVIDENCE = ROOT / "finance/sources/zenmux-models-2026-07-15.json"
OHMYGPT_EVIDENCE = ROOT / "finance/sources/ohmygpt-models-2026-07-15.html"
MAX_ARCHIVE_MEMBERS = 200
MAX_ARCHIVE_FILE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def article_db() -> sqlite3.Connection:
    ARTICLE_DB.parent.mkdir(parents=True, exist_ok=True)
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(ARTICLE_DB)
        db.row_factory = sqlite3.Row
        db.executescript(
            """
        CREATE TABLE IF NOT EXISTS article_metrics (
            article_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '微信公众号',
            published_at TEXT,
            measured_at TEXT NOT NULL,
            reads INTEGER,
            avg_dwell_seconds REAL,
            completion_rate REAL,
            listen_count INTEGER,
            new_followers INTEGER,
            shares INTEGER,
            wow_count INTEGER,
            likes INTEGER,
            favorites INTEGER,
            reward_points REAL,
            comments INTEGER,
            delivered INTEGER,
            message_reads INTEGER,
            first_shares INTEGER,
            total_shares INTEGER,
            share_generated_reads INTEGER,
            trend_json TEXT NOT NULL DEFAULT '[]',
            demographics_json TEXT NOT NULL DEFAULT '{}',
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            UNIQUE(title, measured_at, source_path)
        );
        CREATE INDEX IF NOT EXISTS idx_article_metrics_title ON article_metrics(title);
        CREATE TABLE IF NOT EXISTS article_source_metrics (
            source_metric_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            published_at TEXT,
            channel TEXT NOT NULL,
            reads INTEGER NOT NULL DEFAULT 0,
            read_share REAL,
            measured_at TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            UNIQUE(title, published_at, channel, measured_at, source_path)
        );
        CREATE INDEX IF NOT EXISTS idx_article_source_title ON article_source_metrics(title);
            """
        )
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


def finance_db() -> sqlite3.Connection:
    FINANCE_DB.parent.mkdir(parents=True, exist_ok=True)
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(FINANCE_DB)
        db.row_factory = sqlite3.Row
        db.execute(
            """
        CREATE TABLE IF NOT EXISTS model_prices (
            price_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            model_slug TEXT NOT NULL,
            endpoint TEXT,
            currency TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'millionTokens',
            input_price REAL,
            output_price REAL,
            cache_read_price REAL,
            cache_write_price REAL,
            context_tokens INTEGER,
            max_output_tokens INTEGER,
            source_url TEXT NOT NULL,
            evidence_path TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'observed',
            notes TEXT NOT NULL DEFAULT '',
            UNIQUE(provider, model_slug, currency, source_url)
        )
            """
        )
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _parse_xls(path: Path) -> dict[str, Any]:
    try:
        import xlrd  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("xlrd 2.x is required to import .xls exports") from exc

    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    rows = [[cell.value for cell in row] for row in sheet.get_rows()]
    title = str(rows[0][1]).strip()
    metrics: dict[str, Any] = {}
    trend: list[dict[str, Any]] = []
    demographics: dict[str, dict[str, Any]] = {}
    section = "summary"
    demographic_section: str | None = None
    for row in rows[1:]:
        values = row[1:]
        first = str(values[0]).strip() if values else ""
        if first == "阅读数据趋势明细":
            section = "trend"
            continue
        if first in {"性别分布", "年龄分布", "地域分布"}:
            demographic_section = first
            section = "demographic"
            demographics.setdefault(first, {})
            continue
        if section == "trend":
            if first == "日期" or not first:
                continue
            if len(values) >= 4 and re.match(r"^\d{4}-\d{2}-\d{2}$", first):
                trend.append({
                    "date": first,
                    "channel": str(values[1]),
                    "reads": _integer(values[2]),
                    "shares": _integer(values[3]),
                })
            continue
        if section == "demographic" and demographic_section:
            if first in {"性别", "年龄", "省份/直辖市"} or not first:
                continue
            demographics[demographic_section][first] = values[1] if len(values) > 1 else None
            continue
        if first in {"数据概况", "阅读转化", "数据指标", ""}:
            continue
        value = values[1] if len(values) > 1 else None
        metrics[first] = value

    def metric(*names: str) -> Any:
        for name in names:
            if name in metrics:
                return metrics[name]
        return None

    first_date = next((item["date"] for item in trend if item.get("date")), None)
    return {
        "title": title,
        "published_at": first_date,
        "measured_at": "2026-07-15",
        "reads": _integer(metric("阅读(人)")),
        "avg_dwell_seconds": _number(metric("平均停留时长(秒)")),
        "completion_rate": _number(metric("完读率")),
        "listen_count": _integer(metric("听全文（人）")),
        "new_followers": _integer(metric("新增关注（人）")),
        "shares": _integer(metric("分享(人)")),
        "wow_count": _integer(metric("在看(人)")),
        "likes": _integer(metric("点赞(人)")),
        "favorites": _integer(metric("收藏(人)")),
        "reward_points": _number(metric("赞赏(分)")),
        "comments": _integer(metric("评论（条）")),
        "delivered": _integer(metric("送达人数")),
        "message_reads": _integer(metric("公众号消息阅读人数")),
        "first_shares": _integer(metric("首次分享人数")),
        "total_shares": _integer(metric("总分享人数")),
        "share_generated_reads": _integer(metric("分享产生的阅读人数")),
        "trend": trend,
        "demographics": demographics,
    }


def _parse_tendency_xls(path: Path) -> list[dict[str, Any]]:
    try:
        import xlrd  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("xlrd 2.x is required to import .xls exports") from exc
    sheet = xlrd.open_workbook(path).sheet_by_index(0)
    rows: list[dict[str, Any]] = []
    for index in range(2, sheet.nrows):
        channel = str(sheet.cell_value(index, 11)).strip()
        raw_date = str(sheet.cell_value(index, 12)).strip()
        title = str(sheet.cell_value(index, 13)).strip()
        if not channel or not title or not raw_date:
            continue
        published_at = raw_date
        if re.fullmatch(r"\d{8}", raw_date):
            published_at = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        rows.append({
            "title": title,
            "published_at": published_at,
            "channel": channel,
            "reads": _integer(sheet.cell_value(index, 14)) or 0,
            "read_share": _number(sheet.cell_value(index, 15)),
            "measured_at": "2026-07-15",
        })
    return rows


def _extract_xls_exports(archive: zipfile.ZipFile, destination: Path) -> list[Path]:
    """Extract bounded, root-level XLS exports without trusting ZIP paths."""

    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"archive has too many members: {len(infos)}")
    declared_total = sum(max(0, info.file_size) for info in infos if not info.is_dir())
    if declared_total > MAX_ARCHIVE_TOTAL_BYTES:
        raise ValueError(f"archive expands beyond {MAX_ARCHIVE_TOTAL_BYTES} bytes")

    exports: list[Path] = []
    extracted_total = 0
    for info in infos:
        name = info.filename
        member = PurePosixPath(name)
        mode = info.external_attr >> 16
        if (
            not name
            or "\x00" in name
            or "\\" in name
            or member.is_absolute()
            or ".." in member.parts
            or stat.S_ISLNK(mode)
        ):
            raise ValueError(f"unsafe archive member: {name!r}")
        if info.flag_bits & 0x1:
            raise ValueError(f"encrypted archive member is unsupported: {name!r}")
        if info.is_dir():
            continue
        if info.file_size > MAX_ARCHIVE_FILE_BYTES:
            raise ValueError(f"archive member is too large: {name!r}")
        if info.file_size and (
            info.compress_size <= 0
            or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
        ):
            raise ValueError(f"suspicious compression ratio for archive member: {name!r}")
        if len(member.parts) != 1 or member.suffix.lower() != ".xls":
            continue

        target = destination / member.name
        written = 0
        with archive.open(info, "r") as source, target.open("xb") as sink:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                extracted_total += len(chunk)
                if written > MAX_ARCHIVE_FILE_BYTES or extracted_total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise ValueError("archive exceeded extraction limits")
                sink.write(chunk)
        exports.append(target)
    return sorted(exports)


def import_articles(zip_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not zip_path.is_file():
        raise FileNotFoundError(zip_path)
    source_path = str(zip_path.resolve())
    source_hash = sha256(zip_path)
    with tempfile.TemporaryDirectory(prefix="company-article-stats-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as archive:
            exports = _extract_xls_exports(archive, tmp_path)
        rows: list[dict[str, Any]] = []
        source_rows: list[dict[str, Any]] = []
        for path in exports:
            if path.name.startswith("tendency_"):
                source_rows.extend(_parse_tendency_xls(path))
                continue
            item = _parse_xls(path)
            item["source_path"] = source_path
            item["source_sha256"] = source_hash
            rows.append(item)
    db = article_db()
    try:
        for item in rows:
            db.execute(
                """
                INSERT INTO article_metrics (
                    article_id,title,published_at,measured_at,reads,avg_dwell_seconds,
                    completion_rate,listen_count,new_followers,shares,wow_count,likes,
                    favorites,reward_points,comments,delivered,message_reads,first_shares,
                    total_shares,share_generated_reads,trend_json,demographics_json,
                    source_path,source_sha256,imported_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(title, measured_at, source_path) DO UPDATE SET
                    published_at=excluded.published_at, reads=excluded.reads,
                    avg_dwell_seconds=excluded.avg_dwell_seconds,
                    completion_rate=excluded.completion_rate, listen_count=excluded.listen_count,
                    new_followers=excluded.new_followers, shares=excluded.shares,
                    wow_count=excluded.wow_count, likes=excluded.likes,
                    favorites=excluded.favorites, reward_points=excluded.reward_points,
                    comments=excluded.comments, delivered=excluded.delivered,
                    message_reads=excluded.message_reads, first_shares=excluded.first_shares,
                    total_shares=excluded.total_shares,
                    share_generated_reads=excluded.share_generated_reads,
                    trend_json=excluded.trend_json, demographics_json=excluded.demographics_json,
                    source_sha256=excluded.source_sha256, imported_at=excluded.imported_at
                """,
                (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, item["title"] + item["source_path"])),
                    item["title"], item["published_at"], item["measured_at"], item["reads"],
                    item["avg_dwell_seconds"], item["completion_rate"], item["listen_count"],
                    item["new_followers"], item["shares"], item["wow_count"], item["likes"],
                    item["favorites"], item["reward_points"], item["comments"], item["delivered"],
                    item["message_reads"], item["first_shares"], item["total_shares"],
                    item["share_generated_reads"], json.dumps(item["trend"], ensure_ascii=False),
                    json.dumps(item["demographics"], ensure_ascii=False), item["source_path"],
                    item["source_sha256"], now(),
                ),
            )
        for item in source_rows:
            source_key = f"{item['title']}:{item['published_at']}:{item['channel']}:{source_path}"
            db.execute(
                """
                INSERT INTO article_source_metrics (
                    source_metric_id,title,published_at,channel,reads,read_share,measured_at,
                    source_path,source_sha256,imported_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(title, published_at, channel, measured_at, source_path) DO UPDATE SET
                    reads=excluded.reads, read_share=excluded.read_share,
                    source_sha256=excluded.source_sha256, imported_at=excluded.imported_at
                """,
                (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, source_key)), item["title"], item["published_at"],
                    item["channel"], item["reads"], item["read_share"], item["measured_at"],
                    source_path, source_hash, now(),
                ),
            )
        db.commit()
    finally:
        db.close()
    return rows, source_rows


MODEL_PRICES = [
    {
        "provider": "ZenMux", "model": "DeepSeek V4 Pro", "model_slug": "deepseek/deepseek-v4-pro",
        "endpoint": "deepseek/chat-completions", "currency": "USD", "input_price": 0.435,
        "output_price": 0.87, "cache_read_price": 0.003625, "context_tokens": 1_000_000,
        "max_output_tokens": 384_000, "source_url": "https://zenmux.ai/api/frontend/model/listByFilter",
        "evidence_path": ZENMUX_EVIDENCE,
    },
    {
        "provider": "ZenMux", "model": "DeepSeek V4 Flash", "model_slug": "deepseek/deepseek-v4-flash",
        "endpoint": "deepseek/chat-completions", "currency": "USD", "input_price": 0.14,
        "output_price": 0.28, "cache_read_price": 0.0028, "source_url": "https://zenmux.ai/api/frontend/model/listByFilter",
        "evidence_path": ZENMUX_EVIDENCE,
    },
    {
        "provider": "ZenMux", "model": "DeepSeek V3.2", "model_slug": "deepseek/deepseek-v3.2",
        "endpoint": "deepseek/chat-completions", "currency": "USD", "input_price": 0.293,
        "output_price": 0.4395, "cache_read_price": 0.0293, "source_url": "https://zenmux.ai/api/frontend/model/listByFilter",
        "evidence_path": ZENMUX_EVIDENCE,
    },
    {
        "provider": "ZenMux", "model": "DeepSeek V3.1", "model_slug": "deepseek/deepseek-v3.1",
        "endpoint": "deepseek/chat-completions", "currency": "USD", "input_price": 0.28,
        "output_price": 1.11, "cache_read_price": 0.056, "source_url": "https://zenmux.ai/api/frontend/model/listByFilter",
        "evidence_path": ZENMUX_EVIDENCE,
    },
    {
        "provider": "ZenMux", "model": "DeepSeek R1 0528", "model_slug": "deepseek/deepseek-r1-0528",
        "endpoint": "deepseek/chat-completions", "currency": "USD", "input_price": 0.56,
        "output_price": 2.23, "cache_read_price": 0.112, "source_url": "https://zenmux.ai/api/frontend/model/listByFilter",
        "evidence_path": ZENMUX_EVIDENCE,
    },
    {
        "provider": "OhMyGPT", "model": "DeepSeek V4 Pro", "model_slug": "deepseek-v4-pro",
        "endpoint": "", "currency": "CNY", "input_price": 3.0, "output_price": 6.0,
        "source_url": "https://www.ohmygpt.com/models", "evidence_path": OHMYGPT_EVIDENCE,
    },
    {
        "provider": "OhMyGPT", "model": "DeepSeek V4 Flash", "model_slug": "deepseek-v4-flash",
        "endpoint": "", "currency": "CNY", "input_price": 1.0, "output_price": 2.0,
        "source_url": "https://www.ohmygpt.com/models", "evidence_path": OHMYGPT_EVIDENCE,
    },
    {
        "provider": "OhMyGPT", "model": "DeepSeek Chat", "model_slug": "deepseek-chat",
        "endpoint": "", "currency": "CNY", "input_price": 1.0, "output_price": 2.0,
        "source_url": "https://www.ohmygpt.com/models", "evidence_path": OHMYGPT_EVIDENCE,
    },
    {
        "provider": "OhMyGPT", "model": "DeepSeek Reasoner", "model_slug": "deepseek-reasoner",
        "endpoint": "", "currency": "CNY", "input_price": 1.0, "output_price": 2.0,
        "source_url": "https://www.ohmygpt.com/models", "evidence_path": OHMYGPT_EVIDENCE,
    },
]


def import_prices(collected_at: str = "2026-07-15") -> None:
    db = finance_db()
    try:
        for item in MODEL_PRICES:
            evidence = Path(item["evidence_path"])
            if not evidence.is_file():
                raise FileNotFoundError(evidence)
            db.execute(
                """
                INSERT INTO model_prices (
                    price_id,provider,model,model_slug,endpoint,currency,unit,input_price,
                    output_price,cache_read_price,context_tokens,max_output_tokens,source_url,
                    evidence_path,evidence_sha256,collected_at,status,notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider, model_slug, currency, source_url) DO UPDATE SET
                    model=excluded.model, endpoint=excluded.endpoint, unit=excluded.unit,
                    input_price=excluded.input_price, output_price=excluded.output_price,
                    cache_read_price=excluded.cache_read_price,
                    context_tokens=excluded.context_tokens, max_output_tokens=excluded.max_output_tokens,
                    evidence_path=excluded.evidence_path, evidence_sha256=excluded.evidence_sha256,
                    collected_at=excluded.collected_at, status=excluded.status, notes=excluded.notes
                """,
                (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{item['provider']}:{item['model_slug']}:{item['currency']}:{item['source_url']}")),
                    item["provider"], item["model"], item["model_slug"], item["endpoint"], item["currency"],
                    "millionTokens", item["input_price"], item["output_price"], item.get("cache_read_price"),
                    item.get("context_tokens"), item.get("max_output_tokens"), item["source_url"],
                    str(evidence.resolve()), sha256(evidence), collected_at, "observed", "公开页面/API采集；AnyRouter 未纳入",
                ),
            )
        db.commit()
    finally:
        db.close()


def write_article_report(rows: list[dict[str, Any]], source_rows: list[dict[str, Any]]) -> Path:
    report = ROOT / "marketing/article-performance-2026-07-15.md"
    lines = [
        "---", "tags: [marketing, article-performance, evidence]", "created: 2026-07-15", "updated: 2026-07-15", "---", "",
        "# 文章发布表现（2026-07-15 导出）", "",
        "> 来源：微信公众号后台导出的 `数据统计.zip`。这里只记录实际存在的明细导出，不把没有明细文件的文章推断为未发布。", "",
        "## 已导入明细", "",
        "| 文章 | 发布日期（趋势首日） | 阅读 | 完读率 | 平均停留(s) | 新关注 | 分享 | 在看 | 点赞 | 收藏 | 评论 | 赞赏 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda x: (x.get("published_at") or "", x["title"])):
        rate = "" if row["completion_rate"] is None else f"{row['completion_rate']:.2%}"
        lines.append(
            f"| {row['title']} | {row.get('published_at') or '—'} | {row.get('reads') or 0} | {rate} | "
            f"{row.get('avg_dwell_seconds') or 0:g} | {row.get('new_followers') or 0} | {row.get('shares') or 0} | "
            f"{row.get('wow_count') or 0} | {row.get('likes') or 0} | {row.get('favorites') or 0} | "
            f"{row.get('comments') or 0} | {row.get('reward_points') or 0:g} |"
        )
    current_titles = {
        item["title"] for item in source_rows
        if str(item.get("published_at") or "").startswith("2026-07")
    }
    lines += [
        "", "## 数据边界", "", f"- 导入明细：{len(rows)} 篇。", "- 原始证据：`evidence/article-stats-2026-07-15/数据统计.zip`。",
        f"- SHA-256：`{sha256(ARTICLE_EVIDENCE)}`。", "- 公众号后台的趋势总表还包含其他历史文章，但本次压缩包没有对应的逐篇明细文件；其发布状态继续以项目追踪表和人工确认结果为准。", "",
        f"- 趋势总表中识别到 2026 年 7 月发布内容 {len(current_titles)} 篇（包括没有逐篇明细导出的文章）。", "",
        "## 结构化存储", "", "- SQLite：`marketing/article_performance.db`。", "- `article_metrics`：逐篇摘要指标、趋势明细和人群分布 JSON。", "- `article_source_metrics`：趋势总表中的文章、发布日期、传播渠道和阅读人数。",
    ]
    atomic_write_text(report, "\n".join(lines) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--article-zip", default=str(ARTICLE_EVIDENCE))
    parser.add_argument("--skip-articles", action="store_true")
    parser.add_argument("--skip-prices", action="store_true")
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    if not args.skip_articles:
        rows, source_rows = import_articles(Path(args.article_zip))
        print(f"imported article metrics: {len(rows)}")
        print(f"imported article source rows: {len(source_rows)}")
        print(f"article report: {write_article_report(rows, source_rows)}")
    if not args.skip_prices:
        import_prices()
        print(f"imported model prices: {len(MODEL_PRICES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
