#!/usr/bin/env python3
"""Push markdown article with inline images to WeChat draft box.

Extends wechat_push.py: uploads local images referenced by ![alt](path) via
media/uploadimg, swaps paths for WeChat CDN URLs, then reuses the original
push pipeline (premailer inline CSS, ul/li→bullet, pre \n→<br>, author=nooooop).
"""
import json
import os
import re
import sys
import urllib.request
from contextlib import suppress

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_push as base

APP_ID = os.environ.get("WEIXIN_APP_ID", "")
APP_SECRET = os.environ.get("WEIXIN_APP_SECRET", "")


def get_token() -> str:
    data = json.dumps({"grant_type": "client_credential", "appid": APP_ID,
                       "secret": APP_SECRET}).encode()
    req = urllib.request.Request("https://api.weixin.qq.com/cgi-bin/stable_token",
                                 data=data, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["access_token"]


def upload_image(token: str, path: str) -> str:
    """Upload one image to WeChat, return permanent CDN URL (media/uploadimg)."""
    with open(path, "rb") as f:
        img = f.read()
    boundary = "----WXImgBoundary"
    b = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; "
         f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: image/png\r\n\r\n"
         ).encode() + img + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}",
        data=b, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    resp = json.loads(urllib.request.urlopen(req).read())
    url = resp.get("url")
    if not url:
        raise RuntimeError(f"uploadimg failed: {resp}")
    return url


def swap_local_images(token: str, body: str, base_dir: str) -> str:
    """Replace ![alt](local/path) with ![alt](https://cdn...) for WeChat body."""
    def _swap(m):
        alt, path = m.group(1), m.group(2)
        if path.startswith("http"):
            return m.group(0)
        full = os.path.join(base_dir, path)
        if not os.path.exists(full):
            print(f"  [skip] missing image: {full}")
            return m.group(0)
        url = upload_image(token, full)
        print(f"  [img] {path} -> {url[:60]}...")
        return f"![{alt}]({url})"
    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _swap, body)


def main() -> int:
    article = sys.argv[1]
    cover = sys.argv[2] if len(sys.argv) > 2 else ""
    base_dir = os.path.dirname(os.path.abspath(article))

    with open(article, encoding="utf-8") as f:
        raw = f.read()

    # split frontmatter from body — ONLY the leading ---...--- block
    lines = raw.split("\n")
    title = ""
    body_lines = []
    fence_hits = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(fence_hits) >= 2:
        fm_zone = lines[fence_hits[0] + 1:fence_hits[1]]
        body_lines = lines[fence_hits[1] + 1:]
        for line in fm_zone:
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
    else:
        body_lines = lines
    body = "\n".join(body_lines)

    token = get_token()
    print(f"[token] ok")
    body = swap_local_images(token, body, base_dir)

    # write temp article with swapped images, then reuse base push pipeline
    tmp = "/tmp/a9-v2-swapped.md"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"---\ntitle: {title}\n---\n\n" + body)

    # temporarily point base module env at our token-free call path
    base.push_to_wechat(tmp, cover)

    # record publish in job lifecycle (best-effort; job dir may be unrelated)
    import subprocess
    job_dir = os.path.dirname(os.path.abspath(article))
    if os.path.isdir(job_dir) and os.path.exists(os.path.join(job_dir, "request.json")):
        state_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "automation", "content_job_state.py")
        if os.path.exists(state_py):
            with suppress(Exception):
                subprocess.run(
                    [sys.executable, state_py,
                     job_dir, "publish", f"pushed draft to WeChat ({title[:40]})"],
                    capture_output=True, text=True, timeout=30,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
