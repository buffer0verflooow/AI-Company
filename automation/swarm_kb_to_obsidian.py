#!/usr/bin/env python3
"""Swarm KB → Obsidian 策略面板。

从蜂群知识库提取高价值条目（L3 Knowledge, L4 Wisdom），
写入公司库 wiki/swarm-strategies.md，形成人类可读的策略面板。

运行：
  python3 swarm_kb_to_obsidian.py

原理：
  - 只提取 L3/L4 活跃条目
  - 按 domain 分组展示
  - 附带来源和信任度
  - 只追加不覆盖，避免你丢失手写内容
"""

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._safe_io import atomic_write_text, file_lock, read_text_limited, sqlite_uri
except ImportError:  # direct script execution
    from _safe_io import atomic_write_text, file_lock, read_text_limited, sqlite_uri

COMPANY_ROOT = Path(__file__).resolve().parent.parent
SWARM_DB = Path.home() / "workspace" / "research" / "swarm-knowledge" / "swarm_knowledge.db"
WIKI_PATH = COMPANY_ROOT / "wiki" / "swarm-strategies.md"

# Keep entries below this threshold from being added
MIN_TRUST = 0.50
# Marker to detect auto-generated sections
AUTO_MARKER = "<!-- swarm-kb-auto -->"


def _parse_trust(raw: str | None) -> float:
    """Parse trust_vector JSON field into a single numeric score (0-1)."""
    if not raw:
        return 0.0
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(obj, dict):
            # Average available sub-scores
            vals = [float(v) for v in obj.values() if isinstance(v, (int, float)) and math.isfinite(float(v))]
            score = sum(vals) / len(vals) if vals else 0.0
        else:
            score = float(obj)
        return max(0.0, min(1.0, score)) if math.isfinite(score) else 0.0
    except (json.JSONDecodeError, TypeError, ValueError, OverflowError):
        return 0.0


def fetch_top_entries(db_path: Path) -> list[dict]:
    """Fetch L3/L4 active knowledge entries from Swarm KB."""
    db = sqlite3.connect(sqlite_uri(db_path, mode="ro"), uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT level, knowledge_type, title, content, source_agent, tags,
                   trust_vector, created_at, id
            FROM knowledge_entries
            WHERE level >= 3 AND status = 'active'
            ORDER BY level DESC, created_at DESC
            LIMIT 3000
            """,
        ).fetchall()
    finally:
        db.close()

    result = []
    for r in rows:
        tags = r["tags"]
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = [tags] if tags else []
        if not isinstance(tags, list):
            tags = []
        trust = _parse_trust(r["trust_vector"])
        if trust < MIN_TRUST:
            continue
        result.append({
            "id": r["id"],
            "level": r["level"],
            "type": r["knowledge_type"],
            "title": r["title"] or "(无标题)",
            "content": (r["content"] or "")[:500],
            "agent": r["source_agent"] or "unknown",
            "tags": tags or [],
            "trust": trust,
            "created": r["created_at"] or "",
        })
    result.sort(
        key=lambda item: (int(item["level"] or 0), float(item["trust"]), str(item["created"])),
        reverse=True,
    )
    return result[:30]


def _md_inline(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())[:limit]
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for marker in ("\\", "*", "_", "[", "]", "(", ")", "`", "#"):
        text = text.replace(marker, "\\" + marker)
    return text


def group_by_domain(entries: list[dict]) -> dict:
    """Group entries by domain inferred from tags."""
    groups = {"uncategorized": []}
    for e in entries:
        domain = "uncategorized"
        for tag in e["tags"]:
            tag_lower = str(tag).lower()
            if tag_lower in ("auth", "jwt", "api", "authentication"):
                domain = "auth-api"
                break
            if tag_lower in ("sqli", "injection", "ssrf", "idor"):
                domain = "web-vuln"
                break
            if tag_lower in ("cloudflare", "waf", "bypass"):
                domain = "waf-bypass"
                break
            if tag_lower in ("recon", "scan", "osint"):
                domain = "recon"
                break
            if tag_lower in ("firmware", "apk", "mobile", "android"):
                domain = "mobile-firmware"
                break
            if tag_lower in ("swarm", "orchestration", "agent"):
                domain = "swarm-ops"
                break
            if tag_lower in ("knowledge-base", "kb", "dikw"):
                domain = "knowledge-mgmt"
                break
        groups.setdefault(domain, []).append(e)
    return groups


DOMAIN_LABELS = {
    "auth-api": "认证与 API 安全",
    "web-vuln": "Web 漏洞模式",
    "waf-bypass": "WAF 绕过",
    "recon": "信息收集与侦察",
    "mobile-firmware": "移动端/固件",
    "swarm-ops": "蜂群系统运营",
    "knowledge-mgmt": "知识管理",
    "uncategorized": "未分类",
}


def level_badge(level: int) -> str:
    return {"3": "🧠 Knowledge", "4": "💡 Wisdom"}.get(str(level), f"L{level}")


def generate_strategy_md(entries: list[dict], timestamp: str) -> str:
    """Generate a strategy panel section from KB entries."""
    groups = group_by_domain(entries)
    sections = []

    for domain, items in sorted(groups.items()):
        label = DOMAIN_LABELS.get(domain, domain)
        sections.append(f"\n### {label} ({len(items)} 条)\n")
        for item in sorted(items, key=lambda x: -x["level"]):
            trust_pct = f"{item['trust'] * 100:.0f}%" if item["trust"] else "—"
            title = _md_inline(item["title"], 240)
            agent = _md_inline(item["agent"], 120)
            content = _md_inline(item["content"], 200)
            sections.append(
                f"- **[{level_badge(item['level'])}] {title}** "
                f"(信任度 {trust_pct}, 来源: {agent})\n"
                f"  - {content}\n"
            )
    body = "\n".join(sections)

    return (
        f"\n\n{AUTO_MARKER}\n"
        f"## 🤖 蜂群策略同步 ({timestamp})\n"
        f"*自动从蜂群知识库 L3/L4 条目生成，"
        f"共计 {len(entries)} 条活跃知识*\n\n"
        f"---\n{body}\n"
        f"<!-- /swarm-kb-auto -->\n"
    )


def update_wiki(md_section: str) -> bool:
    """Append or replace auto-generated section in wiki page."""
    WIKI_PATH.parent.mkdir(parents=True, exist_ok=True)

    with file_lock(WIKI_PATH):
        if WIKI_PATH.exists():
            existing = read_text_limited(WIKI_PATH, max_bytes=10 * 1024 * 1024)
            # Replace existing auto section
            if AUTO_MARKER in existing:
                start = existing.index(AUTO_MARKER)
                end_marker = "<!-- /swarm-kb-auto -->"
                end = existing.index(end_marker) + len(end_marker) if end_marker in existing else len(existing)
                new_content = existing[:start].rstrip() + md_section + existing[end:].rstrip() + "\n"
            else:
                new_content = existing.rstrip() + "\n" + md_section
        else:
            new_content = (
                f"# 蜂群策略面板\n\n"
                f"从蜂群知识库自动同步的高价值策略与模式。\n"
                f"编辑此文件会保留手写内容，自动段标记为 "
                f"`{AUTO_MARKER}`。\n"
                f"{md_section}"
            )
        atomic_write_text(WIKI_PATH, new_content)
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync Swarm KB wisdom to Obsidian strategy panel")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    if not SWARM_DB.is_file():
        print(f"Swarm DB not found: {SWARM_DB}")
        return

    entries = fetch_top_entries(SWARM_DB)
    if not entries:
        print("No L3/L4 entries found in Swarm KB.")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md_section = generate_strategy_md(entries, timestamp)

    if args.dry_run:
        print(f"Would write {len(entries)} entries to {WIKI_PATH}")
        print(md_section[:2000])
        return

    update_wiki(md_section)
    print(f"✅ Synced {len(entries)} entries to {WIKI_PATH}")


if __name__ == "__main__":
    main()
