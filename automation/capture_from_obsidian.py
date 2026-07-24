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

OBSIDIAN_VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "workspace" / "company"),
))
CAPTURE_PY = (
    Path.home() / "workspace" / "research" / "swarm-knowledge" / "capture.py"
)
SWARM_DB = (
    Path.home() / "workspace" / "research" / "swarm-knowledge" / "swarm_knowledge.db"
)
TRACKING_FILE = (
    Path.home() / "workspace" / "company" / "operations" / "runtime" / "obsidian-capture-tracking.json"
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

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
        result = yaml.safe_load(front_raw)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
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
            raw = md_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
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
            return json.loads(TRACKING_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_tracking(tracking: dict) -> None:
    TRACKING_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRACKING_FILE.write_text(
        json.dumps(tracking, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
        raw = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError) as exc:
        return f"read_error:{exc}"

    fm = parse_frontmatter(raw)

    # Strip frontmatter for content
    body = FRONTMATTER_RE.sub("", raw, count=1).strip()

    title = get_title_from_note(raw, path)

    # Build capture args
    tags = json.dumps(fm.get("swarm_tags", fm.get("tags", [])))
    if isinstance(tags, list):
        tags = ",".join(tags)
    tags = tags.strip("[]").replace('"', "").replace("'", "").replace(" ", "")

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
        sys.executable, str(CAPTURE_PY),
        "--db", str(SWARM_DB),
        "--content", f"## {title}\n\n{body}",
        "--agent", agent,
        "--source", source,
        "--tags", tags or "obsidian",
        "--force-capture",
    ]
    if intent:
        cmd.extend(["--intent", intent])
    if title:
        cmd.extend(["--title", title])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "timeout"

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

    for path in sorted(candidates):
        rel = str(path.relative_to(vault))
        content_hash = ""
        if not args.dry_run:
            raw = path.read_text(encoding="utf-8", errors="replace")
            content_hash = compute_content_hash(raw)

            # Skip if already captured (same content hash)
            existing = tracking.get(rel, {})
            if existing.get("content_hash") == content_hash:
                if args.verbose:
                    print(f"  SKIP {rel} (unchanged)")
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
    skipped = sum(1 for _, s in results if s == "dry-run")
    errors = sum(1 for _, s in results if s and s.startswith(("error:", "read_error:", "timeout")))
    unchanged = sum(1 for _, s in results if s == "skip" or (s or "").startswith("skip"))

    parts = []
    if captured:
        parts.append(f"{captured} captured")
    if errors:
        parts.append(f"{errors} errors")
    if args.dry_run:
        parts.append(f"{len(candidates)} candidates (dry-run)")
    elif not captured and not errors:
        parts.append("all up to date")
    print(f"\nDone: {', '.join(parts)}" if parts else "Nothing to do.")


if __name__ == "__main__":
    main()
