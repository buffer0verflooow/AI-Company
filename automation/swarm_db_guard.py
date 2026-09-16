#!/usr/bin/env python3
"""v2 蜂群活库可用性/schema 护栏(D-16.1,2026-09-16)。

背景:v1 库位 ``swarm-knowledge/swarm_knowledge.db`` 自 M0.2 起已墓碑化为
**目录**(``is_file()=False``),权威活库是 schema v2 的 ``swarm_v2.db``。
读类消费者必须:

  * 缺库 / 非文件(tombstone)/ 非 v2 schema ⇒ **响亮失败**(非零退出 + 明确原因);
  * 目标表为空 ⇒ 由调用方**响亮标注**(不得表现为 rc=0 的"导出成功但没内容");

禁止把"库不可用"降级成"读到 0 条,静默成功"。

本模块只做**只读**探测;不写任何库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

try:
    from ._safe_io import sqlite_uri
except ImportError:  # direct script execution from automation/
    from _safe_io import sqlite_uri


#: v2 专有表(归档的 v1 库不存在这些表)—— 用于识别"这就是 v2 schema"。
#: 见 migrations_v2/build_v2.py 的 EXPECTED_TABLES(CR-22/CR-26/CR-17)。
V2_MARKER_TABLES: tuple[str, ...] = (
    "audit_events",
    "feature_switches",
    "scheduler_policy",
    "verdict_registry",
)

#: v2 knowledge_entries 必须含有的列(读类查询依赖;v1↔v2 当前逐列同构,
#: 见交付报告 §4 差异表:差异 = 空集)。
V2_KNOWLEDGE_COLUMNS: frozenset[str] = frozenset({
    "id",
    "level",
    "knowledge_type",
    "content",
    "title",
    "source_agent",
    "domain",
    "knowledge_intent",
    "trust_vector",
    "status",
    "tags",
    "created_at",
    "last_validated_at",
})


class SwarmDbUnavailable(RuntimeError):
    """v2 活库缺失、不可读、或 schema 不是 v2。"""

    def __init__(self, path: Path | str, reason: str) -> None:
        self.path = str(path)
        self.reason = reason
        super().__init__(f"{self.path}: {reason}")


def check_v2_db(
    path: Path | str,
    *,
    required_columns: frozenset[str] | set[str] | None = V2_KNOWLEDGE_COLUMNS,
) -> None:
    """Assert *path* is a readable schema-v2 swarm DB, else raise.

    Raises:
        SwarmDbUnavailable: on missing file / tombstone directory / non-v2
            schema / unreadable schema.  Never returns silently for a bad DB.
    """
    target = Path(path)
    if not target.is_file():
        if target.exists():
            raise SwarmDbUnavailable(
                target,
                "路径存在但不是文件(疑似 v1 墓碑目录/tombstone);读类消费者已 repoint 到 v2 活库",
            )
        raise SwarmDbUnavailable(target, "v2 活库文件缺失")

    try:
        conn = sqlite3.connect(sqlite_uri(target, mode="ro"), uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - exercised via corrupt file
        raise SwarmDbUnavailable(target, f"无法以只读方式打开: {exc}") from exc

    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_markers = [name for name in V2_MARKER_TABLES if name not in tables]
        if missing_markers:
            raise SwarmDbUnavailable(
                target,
                "非 v2 schema:缺少 v2 专有表 " + ", ".join(missing_markers),
            )
        if "knowledge_entries" not in tables:
            raise SwarmDbUnavailable(target, "非 v2 schema:缺少 knowledge_entries 表")
        if required_columns:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(knowledge_entries)")
            }
            missing_columns = sorted(set(required_columns) - columns)
            if missing_columns:
                raise SwarmDbUnavailable(
                    target,
                    "knowledge_entries 列集与 v2 不符,缺列 " + ", ".join(missing_columns),
                )
    except sqlite3.Error as exc:
        raise SwarmDbUnavailable(target, f"schema 读取失败: {exc}") from exc
    finally:
        conn.close()


def count_knowledge_entries(path: Path | str) -> int:
    """Return the number of rows in v2 ``knowledge_entries`` (read-only)."""
    target = Path(path)
    conn = sqlite3.connect(sqlite_uri(target, mode="ro"), uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0])
    finally:
        conn.close()


#: 空表时使用的统一文案(D-16.1:不得表现为"导出成功但没有内容")。
EMPTY_KB_NOTE = "v2 KB 尚无条目(记忆层未实现,见审计报告 M-1)"
