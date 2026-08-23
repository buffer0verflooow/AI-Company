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

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from ._safe_io import atomic_write_text, file_lock, read_text_limited
except ImportError:  # direct script execution
    from _safe_io import atomic_write_text, file_lock, read_text_limited

OBSIDIAN_VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "workspace" / "company"),
))
CAPTURE_PY = (
    Path.home() / "workspace" / "research" / "swarm-knowledge" / "scripts" / "capture.py"
)
SWARM_DB = (
    Path.home() / "workspace" / "research" / "swarm-knowledge" / "swarm_knowledge.db"
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
        if any(pat in rel for pat in EXCLUDE_PATTERNS):
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
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_tracking() -> dict:
    if TRACKING_FILE.exists():
        try:
            value = json.loads(TRACKING_FILE.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_tracking(tracking: dict) -> None:
    TRACKING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(TRACKING_FILE):
        current: dict = {}
        if TRACKING_FILE.is_file():
            try:
                value = json.loads(TRACKING_FILE.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    current = value
            except (OSError, json.JSONDecodeError):
                pass
        current.update(tracking)
        atomic_write_text(TRACKING_FILE, json.dumps(current, ensure_ascii=False, indent=2))


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

    cmd = [
        sys.executable, "-c", CAPTURE_BOOTSTRAP, str(CAPTURE_PY), str(SWARM_DB),
        agent, source, tags or "obsidian", intent, title,
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

    entry_id = output.split("CAPTURED:")[-1].strip() if "CAPTURED:" in output else ""
    return entry_id or "captured"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Capture Obsidian notes to Swarm KB")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no capture")
    parser.add_argument("--verbose", action="store_true", help="Detailed output")
    args = parser.parse_args()

    vault = OBSIDIAN_VAULT
    if not vault.is_dir():
        print(f"ERROR: vault not found: {vault}")
        sys.exit(1)
    if not CAPTURE_PY.is_file():
        print(f"ERROR: capture.py not found at {CAPTURE_PY}")
        sys.exit(1)

    candidates = find_candidate_notes(vault)
    if not candidates:
        print("No notes with `swarm: capture` frontmatter found.")
        return

    tracking = load_tracking()
    results = []
    unchanged = 0

    for path in sorted(candidates):
        rel = str(path.relative_to(vault))
        content_hash = ""
        if not args.dry_run:
            try:
                raw = read_text_limited(path, max_bytes=10 * 1024 * 1024, errors="replace")
            except (OSError, ValueError) as exc:
                print(f"  ❌ {rel} → read_error:{exc}")
                results.append((rel, f"read_error:{exc}"))
                continue
            content_hash = compute_content_hash(raw)

            # Skip if already captured (same content hash)
            existing = tracking.get(rel, {})
            if existing.get("content_hash") == content_hash:
                if args.verbose:
                    print(f"  SKIP {rel} (unchanged)")
                unchanged += 1
                continue

        result = capture_note(path, dry_run=args.dry_run)

        if args.dry_run:
            results.append((rel, "dry-run"))
        elif result and not result.startswith(("read_error:", "error:", "timeout")):
            tracking[rel] = {
                "content_hash": content_hash,
                "entry_id": result,
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            save_tracking(tracking)
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
