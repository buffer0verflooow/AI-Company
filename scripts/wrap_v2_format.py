#!/usr/bin/env python3
"""Wrap draft-v2.md content into the 14px WeChat template (style block + body)."""
import re

SRC = "/home/pwn/workspace/company/operations/runtime/content-jobs/a9-fable5-model-downgrade-20260811/draft-v2.md"
OUT = "/home/pwn/workspace/company/operations/runtime/content-jobs/a9-fable5-model-downgrade-20260811/draft-v2-formatted.md"

raw = open(SRC, encoding="utf-8").read()

# split frontmatter — ONLY the leading ---...--- block
lines = raw.split("\n")
fm_lines, body_lines = [], []
fence_hits = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
if len(fence_hits) >= 2:
    fm_lines = lines[fence_hits[0] + 1:fence_hits[1]]
    body_lines = lines[fence_hits[1] + 1:]
else:
    body_lines = lines

body = "\n".join(body_lines).strip()

STYLE = """<style>
body, p, li, table, blockquote {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.8;
  color: #1f2937;
}
p { margin: 0 0 12px; }
h1 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 24px; line-height: 1.45;
  color: #0f172a; margin: 20px 0 18px;
}
h2 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 18px; line-height: 1.5;
  color: #0f172a; border-left: 4px solid #2563eb;
  padding-left: 10px; margin: 24px 0 14px;
}
h3 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 16px; line-height: 1.5;
  color: #0f172a; margin: 18px 0 10px;
}
pre {
  background: #1e293b; color: #e2e8f0;
  border-radius: 6px; padding: 12px 14px;
  overflow-x: auto; font-size: 13px; line-height: 1.6;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
}
code {
  background: #eff6ff; color: #1d4ed8; border-radius: 3px;
  padding: 1px 4px;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 13px;
}
blockquote {
  background: #eff6ff; border-left: 4px solid #3b82f6;
  margin: 14px 0 18px; padding: 10px 14px; color: #1e3a8a;
}
blockquote p { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 14px 0 18px; }
th, td { border: 1px solid #dbeafe; padding: 8px 12px; text-align: left; font-size: 13px; }
th { background: #eff6ff; font-weight: 600; }
img { max-width: 100%; height: auto; margin: 12px 0; }
a { color: #2563eb; text-decoration: none; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 20px 0; }
</style>
"""

out = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + STYLE + "\n" + body + "\n"
open(OUT, "w", encoding="utf-8").write(out)
print(f"written: {OUT} ({len(out)} bytes)")
