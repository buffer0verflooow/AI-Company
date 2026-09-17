#!/usr/bin/env python3
"""Obsidian → Swarm KB 单向桥。

扫描公司 Obsidian 库中标记了 `swarm: capture` frontmatter 的笔记，
提取为知识条目写入蜂群知识库。

使用方法：
  # 扫描并入库（每日 cron 调用）
  python3 capture_from_obsidian.py

  # 只预览哪些笔记会被捕获，不写库
  python3 capture_from_obsidian.py --dry-run

Frontmatter 约定：
  ---
  swarm: capture           # 必填：标记本条可入库
  swarm_tags: [idor, jwt]  # 可选：知识标签
  swarm_agent: obsidian    # 可选：来源签名，默认 obsidian
  swarm_source: article    # 可选：source 类型
  ---

设计原则：
  - 只读标记了 `swarm: capture` 的笔记，不碰其他文件
  - 单向：只写入 KB，不修改 Obsidian 文件
  - 幂等：已入库的笔记不重复写入（通过 content_hash 去重）
  - 低侵入：加 frontmatter 即可，不改变你在 Obsidian 的写作习惯
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from ._safe_io import atomic_write_text, file_lock, read_text_limited
    from .swarm_db_guard import SwarmDbUnavailable, check_v2_db
except ImportError:  # direct script execution
    from _safe_io import atomic_write_text, file_lock, read_text_limited
    from swarm_db_guard import (  # type: ignore[no-redef]
        SwarmDbUnavailable,
        check_v2_db,
    )

OBSIDIAN_VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "workspace" / "company"),
))
#: 2026-09-18 (D-26/D-27): v1 捕获脚本 `swarm-knowledge/scripts/capture.py` 已随 v1
#: 逻辑库整包退役**物理删除**。**没有**可 repoint 的 v2 等价写入入口:
#: `src.swarm_v2.knowledge_loop.capture_run_outcome` 是 **run 终态沉淀**
#: (task_id/run_id/agent/conclusion),不接受原始笔记的
#: content/title/source/tags —— 语义不同构,不得据以发明 KB 写入语义(D-16.1)。
#: 故本写类桥**响亮停用**:默认 None ⇒ `main()` 明确失败 + 非 0,绝不保留
#: rc=0 的静默成功路径。保留该名字仅作为历史单测夹具的注入点(生产恒为 None)。
CAPTURE_PY: Path | None = None
#: 停用文案(唯一来源;README/C-5 与 main() 输出共用)。
V1_CAPTURE_RETIRED = (
    "v1 捕获脚本已随 D-26 退役;v2 知识写入路径未接线"
    "(见 CAPABILITY-AUDIT-2026-09-16 C-5)"
)
# 2026-09-16 (D-16.1): v1 库位是墓碑目录。归档 v1 与 v2 的 knowledge_entries
# 列集/DLL/索引**逐字同构**(见交付报告 §4),所以写类允许 repoint 到 v2 活库;
# 非 v2 schema 仍响亮失败(不发明写入语义)。
SWARM_DB = (
    Path.home() / "workspace" / "research" / "swarm-knowledge" / "swarm_v2.db"
)
TRACKING_FILE = (
    Path.home() / "workspace" / "company" / "operations" / "runtime" / "obsidian-capture-tracking.json"
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
CAPTURE_BOOTSTRAP = """\
import runpy
import sys

capture, db, agent, source, tags, intent, title = sys.argv[1:]
args = [capture, "--db", db, "--content", sys.stdin.read(), "--agent", agent,
        "--source", source, "--tags", tags, "--force-capture"]
if intent:
    args.extend(["--intent", intent])
if title:
    args.extend(["--title", title])
sys.argv = args
runpy.run_path(capture, run_name="__main__")
"""

# Obsidian 中不该自动捕获的路径
EXCLUDE_PATTERNS = [
    ".obsidian/",
    "node_modules/",
    ".git/",
    "operations/runtime/",
    "automation/",
    "scripts/",
    "source-material/",
    "raw/",
    "log.md",
    "index.md",
    "Home.md",
]


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    front_raw = m.group(1)
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            result = yaml.safe_load(front_raw)
        except yaml.YAMLError:
            result = None
        if isinstance(result, dict):
            return result
    # fallback: basic key-value parse for Obsidian frontmatter
    result = {}
    for line in front_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("{{"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # Handle list values like [a, b, c]
            if v.startswith("[") and v.endswith("]"):
                v = [item.strip().strip('"').strip("'") for item in v[1:-1].split(",")]
            result[k] = v
    return result


def find_candidate_notes(vault: Path) -> list[Path]:
    """Find all markdown files with `swarm: capture` frontmatter."""
    candidates = []
    for md_file in vault.rglob("*.md"):
        rel = str(md_file.relative_to(vault))
        # Directory patterns are path fragments; the trailing entries are exact
        # filenames.  A raw substring match dropped any note whose name merely
        # contained one of them ("catalog.md", "blog.md", "myindex.md").
        if any(
            (pat in rel) if pat.endswith("/") else (md_file.name == pat)
            for pat in EXCLUDE_PATTERNS
        ):
            continue
        try:
            raw = read_text_limited(md_file, max_bytes=10 * 1024 * 1024, errors="replace")
        except (OSError, ValueError) as exc:
            # An unreadable note is skipped, but must not vanish silently: it
            # may be a `swarm: capture` candidate the scan would otherwise miss.
            print(f"WARNING: skipping unreadable note {md_file}: {exc}", file=sys.stderr)
            continue
        fm = parse_frontmatter(raw)
        if fm.get("swarm") == "capture" or str(fm.get("swarm", "")).lower() == "capture":
            candidates.append(md_file)
    return candidates


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_tracking() -> dict:
    if TRACKING_FILE.exists():
        try:
            value = json.loads(read_text_limited(TRACKING_FILE, max_bytes=10 * 1024 * 1024))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError) as exc:
            # The tracking file is the only dedup guard against re-capturing a
            # note (the child always runs with --force-capture).  An unreadable
            # file must be visible instead of silently treated as an empty
            # history, which would re-capture every note into the KB.
            print(f"WARNING: unreadable capture tracking file {TRACKING_FILE}: {exc}", file=sys.stderr)
    return {}


def _merge_write_tracking(tracking: dict) -> None:
    """Merge *tracking* into the on-disk history and persist it atomically.

    The caller must already hold ``file_lock(TRACKING_FILE)`` — both
    ``save_tracking`` and the per-note capture path in ``main`` rely on that so
    the read-modify-write is serialized across concurrent scans.
    """
    TRACKING_FILE.parent.mkdir(parents=True, exist_ok=True)
    current: dict = {}
    if TRACKING_FILE.is_file():
        try:
            value = json.loads(read_text_limited(TRACKING_FILE, max_bytes=10 * 1024 * 1024))
            if isinstance(value, dict):
                current = value
        except (OSError, ValueError) as exc:
            # Never atomically replace an unreadable tracking history: the
            # failure is almost always corruption, and overwriting it with
            # just this run's entries would silently re-capture every
            # previously tracked note (duplicates in the KB).
            print(f"WARNING: refusing to overwrite unreadable tracking file {TRACKING_FILE}: {exc}", file=sys.stderr)
            return
    current.update(tracking)
    atomic_write_text(TRACKING_FILE, json.dumps(current, ensure_ascii=False, indent=2))


def save_tracking(tracking: dict) -> None:
    """Merge a tracking update into the file under the tracking-file lock."""
    with file_lock(TRACKING_FILE):
        _merge_write_tracking(tracking)


def get_title_from_note(text: str, path: Path) -> str:
    """Extract title from frontmatter title, first heading, or filename."""
    fm = parse_frontmatter(text)
    title = fm.get("title", "")
    if title:
        return str(title)
    h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if h1:
        return h1.group(1).strip()
    return path.stem


def capture_note(path: Path, dry_run: bool) -> str | None:
    """Capture one Obsidian note to Swarm KB. Returns entry_id or None."""
    try:
        raw = read_text_limited(path, max_bytes=10 * 1024 * 1024, errors="replace")
    except (OSError, ValueError) as exc:
        return f"read_error:{exc}"

    fm = parse_frontmatter(raw)

    # Strip frontmatter for content
    body = FRONTMATTER_RE.sub("", raw, count=1).strip()

    title = get_title_from_note(raw, path)

    # Build capture args
    raw_tags = fm.get("swarm_tags", fm.get("tags", []))
    if isinstance(raw_tags, (list, tuple, set)):
        tag_items = [str(item).strip() for item in raw_tags]
    elif isinstance(raw_tags, str):
        tag_items = [item.strip() for item in raw_tags.strip("[]").split(",")]
    else:
        tag_items = []
    tags = ",".join(item for item in tag_items if item)

    agent = str(fm.get("swarm_agent", "obsidian"))
    source = str(fm.get("swarm_source", "article"))
    intent = str(fm.get("swarm_intent", ""))

    if dry_run:
        rel = path.relative_to(OBSIDIAN_VAULT)
        print(f"  DRY-RUN: {rel}")
        print(f"    title={title!r} tags={tags!r} agent={agent} source={source}")
        print(f"    content: {body[:100]}...")
        return None

    # D-26/D-27: 写类能力已响亮停用;默认 None(生产)。这里显式失败,绝不
    # 退回 "子进程跑一个名为 None 的脚本" 这种静默/怪异路径。
    if CAPTURE_PY is None or not Path(CAPTURE_PY).is_file():
        return f"error:{V1_CAPTURE_RETIRED}"

    cmd = [
        sys.executable, "-c", CAPTURE_BOOTSTRAP, str(CAPTURE_PY), str(SWARM_DB),
        agent, source, tags, intent, title,
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=f"## {title}\n\n{body}", capture_output=True, text=True, timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError as exc:
        return f"error:{exc}"

    output = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return f"error:{proc.stderr.strip() or output[:200]}"

    # ``CAPTURED:<entry_id>`` is the only success signal the child prints.  A
    # zero exit without it means capture.py failed while still exiting 0; the
    # tracking file is the sole dedup/retry guard (the child always runs with
    # --force-capture), so recording this as success would lose the note
    # permanently.  Surface it as an error instead.
    if "CAPTURED:" not in output:
        return f"error:capture exited 0 without CAPTURED marker: {output[:200]}"

    entry_id = output.split("CAPTURED:")[-1].strip()
    return entry_id or "captured"


def main():
    parser = argparse.ArgumentParser(description="Capture Obsidian notes to Swarm KB")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no capture")
    parser.add_argument("--verbose", action="store_true", help="Detailed output")
    args = parser.parse_args()

    vault = OBSIDIAN_VAULT
    if not vault.is_dir():
        print(f"ERROR: vault not found: {vault}")
        sys.exit(1)
    if CAPTURE_PY is None or not Path(CAPTURE_PY).is_file():
        # D-26/D-27/D-16.1: 写类桥没有 v2 等价入口(见 CAPTURE_PY 注释),
        # 故**响亮停用**——明确失败 + 非 0,不留 rc=0 的静默成功路径。
        # 生产 CAPTURE_PY 恒为 None;单测注入临时脚本以覆盖历史写路径断言。
        print(f"ERROR: {V1_CAPTURE_RETIRED}", file=sys.stderr)
        sys.exit(3)
    # 2026-09-16 (D-16.1): 写类目标 repoint 到 v2 活库(列集与归档 v1 同构)。
    # 缺库 / 非 v2 schema 一律硬失败, 避免整轮 unchanged 把桥停摆误报为全部最新。
    try:
        check_v2_db(SWARM_DB)
    except SwarmDbUnavailable as exc:
        print(f"ERROR: v2 swarm KB is not writable/usable: {exc}", file=sys.stderr)
        sys.exit(2)

    candidates = find_candidate_notes(vault)
    if not candidates:
        print("No notes with `swarm: capture` frontmatter found.")
        return

    results = []
    unchanged = 0

    for path in sorted(candidates):
        rel = str(path.relative_to(vault))
        if args.dry_run:
            result = capture_note(path, dry_run=True)
            results.append((rel, "dry-run"))
            continue

        # The tracking file is the sole dedup guard — the child capture.py
        # always runs with --force-capture, so its own dedup is disabled.  A
        # second overlapping scan (cron overlapping a manual run) could pass an
        # unlocked "already captured?" check and insert a duplicate KB row, so
        # the whole check → capture → record sequence holds the tracking-file
        # lock and reloads the tracking state under it.
        with file_lock(TRACKING_FILE):
            try:
                raw = read_text_limited(path, max_bytes=10 * 1024 * 1024, errors="replace")
            except (OSError, ValueError) as exc:
                print(f"  ❌ {rel} → read_error:{exc}")
                results.append((rel, f"read_error:{exc}"))
                continue
            content_hash = compute_content_hash(raw)

            # Reload under the lock instead of trusting a snapshot taken before
            # the loop: another scan may have captured this note meanwhile.
            # A legacy/corrupt tracking entry that is not a dict must degrade
            # to "new" instead of aborting the whole scan with AttributeError.
            tracking = load_tracking()
            existing = tracking.get(rel)
            if not isinstance(existing, dict):
                existing = {}
            if existing.get("content_hash") == content_hash:
                if args.verbose:
                    print(f"  SKIP {rel} (unchanged)")
                unchanged += 1
                continue

            result = capture_note(path, dry_run=False)

            if result and not result.startswith(("read_error:", "error:", "timeout")):
                tracking[rel] = {
                    "content_hash": content_hash,
                    "entry_id": result,
                    "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                # Lock already held — write directly instead of re-locking via
                # save_tracking (a nested flock on a second descriptor would
                # block forever).
                _merge_write_tracking(tracking)
                print(f"  ✅ {rel} → {result[:12]}")
                results.append((rel, "captured"))
            else:
                print(f"  ❌ {rel} → {result}")
                results.append((rel, result))

    # Summary
    captured = sum(1 for _, s in results if s == "captured")
    errors = sum(1 for _, s in results if s and s.startswith(("error:", "read_error:", "timeout")))

    parts = []
    if captured:
        parts.append(f"{captured} captured")
    if errors:
        parts.append(f"{errors} errors")
    if unchanged:
        parts.append(f"{unchanged} unchanged")
    if args.dry_run:
        parts.append(f"{len(candidates)} candidates (dry-run)")
    elif not captured and not errors:
        parts.append("all up to date")
    print(f"\nDone: {', '.join(parts)}" if parts else "Nothing to do.")


if __name__ == "__main__":
    main()
