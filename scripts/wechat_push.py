#!/usr/bin/env python3
"""Push markdown article to WeChat draft box with CSS preserved."""
import json, urllib.request, markdown, os

APP_ID = os.environ.get("WEIXIN_APP_ID", "")
APP_SECRET = os.environ.get("WEIXIN_APP_SECRET", "")

COVER_CANDIDATES = ["cover.jpg", "cover.png", "codex-cover.jpg", "codex-cover.png"]

def _find_cover(article_path, cover_path=None):
    """Resolve cover image: explicit arg wins, else auto-discover next to article."""
    if cover_path and os.path.exists(cover_path):
        return cover_path
    d = os.path.dirname(os.path.abspath(article_path))
    for name in COVER_CANDIDATES:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None

def push_to_wechat(article_path, cover_path=None):
    if not APP_ID or not APP_SECRET:
        raise SystemExit("Set WEIXIN_APP_ID and WEIXIN_APP_SECRET environment variables.")
    # Token
    data = json.dumps({"grant_type":"client_credential","appid":APP_ID,"secret":APP_SECRET}).encode()
    req = urllib.request.Request("https://api.weixin.qq.com/cgi-bin/stable_token", data=data,
        headers={"Content-Type":"application/json"})
    token = json.loads(urllib.request.urlopen(req).read())["access_token"]
    
    # Resolve cover: explicit arg wins; else auto-discover next to the article.
    # WeChat draft/add REQUIRES a valid thumb_media_id — empty string fails with
    # 40007 invalid media_id. Fail fast with a clear message instead of pushing
    # a broken draft (2026-08-18: stale default /tmp/cover-08.jpg caused this).
    resolved_cover = _find_cover(article_path, cover_path)
    if not resolved_cover:
        raise SystemExit(
            "ERROR: no cover image found. WeChat drafts require a thumb_media_id.\n"
            "Pass a cover file explicitly or place cover.jpg / codex-cover.jpg next to the article."
        )
    
    # Upload cover
    boundary = "----WXBoundaryPush"
    with open(resolved_cover, "rb") as f:
        img = f.read()
    b = f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"cover.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode() + img + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image",
        data=b, headers={"Content-Type":f"multipart/form-data; boundary={boundary}"})
    resp = json.loads(urllib.request.urlopen(req).read())
    if "media_id" not in resp:
        raise SystemExit(f"Cover upload failed: {resp}")
    thumb_id = resp["media_id"]
    print(f"Cover: {thumb_id} ({resolved_cover})")
    
    # Read article - preserve full body including <style> CSS
    with open(article_path) as f:
        raw = f.read()
    
    # Parse YAML frontmatter — ONLY the leading ---...--- block.  Body may
    # contain its own "---" separator lines (e.g. before 免责声明); naive
    # toggle parsing would swallow the rest of the article as frontmatter.
    lines = raw.split("\n")
    title = ""
    body_lines = []
    fence_hits = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(fence_hits) >= 2:
        fm_zone = lines[fence_hits[0] + 1:fence_hits[1]]
        body_lines = lines[fence_hits[1] + 1:]
        for line in fm_zone:
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip()
                # strip surrounding quotes (single or double) from title
                if len(title) >= 2 and title[0] == title[-1] and title[0] in ('"', "'"):
                    title = title[1:-1]
                break
    else:
        body_lines = lines
    body = "\n".join(body_lines)
    
    if not title:
        print("ERROR: no title in frontmatter")
        return
    
    # Verify CSS is in body
    if "<style>" not in body:
        print("WARNING: <style> tag not found in body!")
    else:
        print("CSS block found in body")
    
    # Render markdown to HTML — NO pygments syntax highlighting for WeChat
    # WeChat's editor doesn't support pygments span classes: colored spans render
    # as fragmented inline styles, and raw chars like *** get parsed as markdown
    # bold markers → broken display. Use plain code blocks with uniform styling.
    import re
    # Defensive: replace *** inside code fences before markdown parsing — WeChat's
    # editor re-parses content and treats *** as an emphasis marker, breaking the line.
    body = re.sub(r'(```[^`\n]*\n)(.*?)(```)', 
        lambda m: m.group(1) + m.group(2).replace('***', '•••') + m.group(3), 
        body, flags=re.S)
    html = markdown.markdown(body, extensions=['fenced_code','tables','nl2br'])
    # De-highlight codehilite spans if any: unwrap pygments span classes
    html = re.sub(r'<span class="[^"]*"[^>]*>', '', html)
    html = re.sub(r'</span>', '', html)
    # Convert <ul>/<ol> lists to WeChat-compatible paragraphs.
    # WeChat's editor mangles <ul><li> markup — bullets render as garbage.
    # Reference style (GAP article, user-approved) uses plain paragraphs per item.
    def _list_to_paras(m):
        tag = m.group(1)
        items = re.findall(r'<li[^>]*>(.*?)</li>', m.group(2), re.S)
        out = []
        for i, it in enumerate(items, 1):
            it = it.strip()
            if not it:
                continue
            prefix = f"{i}. " if tag == 'ol' else "• "
            out.append(f"<p>{prefix}{it}</p>")
        return "\n".join(out)
    html = re.sub(r'<(ul|ol)[^>]*>(.*?)</\1>', _list_to_paras, html, flags=re.S)
    # Convert newlines inside <pre> to <br> — WeChat's editor collapses plain
    # \n inside code blocks to spaces (YARA/long code loses line breaks).
    # <br> tags survive the editor's round-trip; white-space:pre does not.
    html = re.sub(r'(<pre[^>]*>)(.*?)(</pre>)',
                  lambda m: m.group(1) + m.group(2).replace('\n', '<br>') + m.group(3),
                  html, flags=re.S)
    
    # CRITICAL: WeChat strips <style> tags — use premailer for production-grade inline
    from premailer import transform
    html = transform(html)
    # Premailer wraps in <html><body> — WeChat doesn't need these wrappers
    import re
    html = re.sub(r'^<html><head></head><body[^>]*>', '', html)
    html = re.sub(r'</body></html>$', '', html)
    print(f"CSS inlined via premailer: style= count = {html.count('style=')}")

    # CRITICAL (2026-08-12): WeChat editor STRIPS the <pre> wrapper and keeps only
    # <code>. Premailer inlines `pre code { background: transparent; color: #e2e8f0 }`
    # onto <code>, so after <pre> is dropped the block renders as light-gray text on
    # white background — unreadable, and without white-space:pre long lines squash.
    # Fix: force the complete block styling onto <code> itself, independent of <pre>.
    CODE_BLOCK_STYLE = (
        "display:block;background:#1e293b;color:#e2e8f0;border-radius:4px;"
        "padding:14px 16px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;"
        "font-family:'Consolas','Menlo','Courier New',monospace;font-size:13px;line-height:1.65;"
    )
    # Inline code (short, inside <p>) keeps light style; only standalone code blocks get the dark treatment.
    # Standalone blocks: <pre><code ...>...</code></pre>. After WeChat strips <pre>, <code> must stand alone.
    def _style_code_block(m):
        inner = m.group(2)
        return f"<pre><code style=\"{CODE_BLOCK_STYLE}\">{inner}</code></pre>"
    html = re.sub(r'(<pre[^>]*>)(<code[^>]*>)(.*?)(</code>)(</pre>)',
                  lambda m: f"<pre><code style=\"{CODE_BLOCK_STYLE}\">{m.group(3)}</code></pre>",
                  html, flags=re.S)
    # Safety net: any <code> block that lost its <pre> parent (WeChat round-trip)
    # gets the block style too — detect multi-line code via <br> presence.
    def _fix_orphan_code(m):
        inner = m.group(2)
        if "<br" in inner or len(inner) > 120:
            return f"<code style=\"{CODE_BLOCK_STYLE}\">{inner}</code>"
        return m.group(0)
    html = re.sub(r'(<code[^>]*>)(.*?)(</code>)', _fix_orphan_code, html, flags=re.S)
    print(f"code block styling applied: dark blocks = {html.count(CODE_BLOCK_STYLE.split(';')[1][10:])}")
    
    # Verify CSS made it through
    if "body, p, li" not in html:
        print("WARNING: CSS may have been stripped during rendering!")
    
    # Generate digest
    plain = body.replace("<style>","<!--").replace("</style>","-->")
    # strip HTML tags for digest
    import re
    digest = re.sub(r'<[^>]+>', '', plain).strip().split("\n")
    digest_text = ""
    for d in digest:
        d = d.strip()
        if d and not d.startswith("#") and not d.startswith("---") and len(d) > 10:
            digest_text = d[:100]
            break
    
    # Delete existing drafts with same/similar title (clean up)
    list_req = urllib.request.Request(f"https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token={token}",
        data=json.dumps({"offset":0,"count":20,"no_content":1}).encode(),
        headers={"Content-Type":"application/json"})
    for item in json.loads(urllib.request.urlopen(list_req).read()).get("item", []):
        for n in item.get("content", {}).get("news_item", []):
            draft_title = n.get("title", "")
            # Delete only if it matches our article's title exactly.
            # (Historical hardcoded "深度学习的骨架" clause removed 2026-08-18:
            # it deleted unrelated drafts whenever ANY article was pushed.)
            if draft_title == title:
                del_req = urllib.request.Request(f"https://api.weixin.qq.com/cgi-bin/draft/delete?access_token={token}",
                    data=json.dumps({"media_id":item["media_id"]}).encode(),
                    headers={"Content-Type":"application/json"})
                r = json.loads(urllib.request.urlopen(del_req).read())
                print(f"Deleted old: {draft_title} -> {r}")
    
    # Create draft
    draft = {"articles":[{
        "title": title,
        "thumb_media_id": thumb_id,
        "author": "nooooop",
        "digest": digest_text,
        "content": html,
        "content_source_url": "",
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }]}
    req = urllib.request.Request(f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}",
        data=json.dumps(draft, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json"})
    resp = json.loads(urllib.request.urlopen(req).read())
    print(f"Published: {resp.get('media_id', resp)}")
    print(f"Title: {title}")

if __name__ == "__main__":
    import sys
    article = sys.argv[1] if len(sys.argv) > 1 else "/tmp/codex-article-08.md"
    cover = sys.argv[2] if len(sys.argv) > 2 else None
    push_to_wechat(article, cover)
