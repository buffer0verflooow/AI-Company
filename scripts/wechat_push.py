#!/usr/bin/env python3
"""Push markdown article to WeChat draft box with CSS preserved."""
import json, urllib.request, markdown, os

APP_ID = os.environ.get("WEIXIN_APP_ID", "")
APP_SECRET = os.environ.get("WEIXIN_APP_SECRET", "")

def push_to_wechat(article_path, cover_path=None):
    if not APP_ID or not APP_SECRET:
        raise SystemExit("Set WEIXIN_APP_ID and WEIXIN_APP_SECRET environment variables.")
    # Token
    data = json.dumps({"grant_type":"client_credential","appid":APP_ID,"secret":APP_SECRET}).encode()
    req = urllib.request.Request("https://api.weixin.qq.com/cgi-bin/stable_token", data=data,
        headers={"Content-Type":"application/json"})
    token = json.loads(urllib.request.urlopen(req).read())["access_token"]
    
    # Upload cover
    if cover_path and os.path.exists(cover_path):
        boundary = "----WXBoundaryPush"
        with open(cover_path, "rb") as f:
            img = f.read()
        b = f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"cover.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode() + img + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image",
            data=b, headers={"Content-Type":f"multipart/form-data; boundary={boundary}"})
        thumb_id = json.loads(urllib.request.urlopen(req).read())["media_id"]
        print(f"Cover: {thumb_id}")
    else:
        thumb_id = ""
        print("No cover, using default")
    
    # Read article - preserve full body including <style> CSS
    with open(article_path) as f:
        raw = f.read()
    
    # Parse YAML frontmatter
    lines = raw.split("\n")
    title = ""
    body_lines = []
    in_fm = False
    for line in lines:
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm:
            if line.startswith("title:"):
                title = line.split(":",1)[1].strip()
        else:
            body_lines.append(line)
    body = "\n".join(body_lines)
    
    if not title:
        print("ERROR: no title in frontmatter")
        return
    
    # Verify CSS is in body
    if "<style>" not in body:
        print("WARNING: <style> tag not found in body!")
    else:
        print("CSS block found in body")
    
    # Render markdown to HTML with syntax highlighting + CSS inline
    html = markdown.markdown(body, extensions=['fenced_code','tables','codehilite','nl2br'])
    
    # Inject pygments syntax highlighting CSS into style block if present
    import re
    style_match = re.search(r'<style>(.*?)</style>', html, re.S)
    if style_match:
        from pygments.formatters import HtmlFormatter
        pygments_css = HtmlFormatter(style='monokai').get_style_defs('.codehilite')
        # Merge pygments CSS into existing style
        new_style = style_match.group(1) + '\n' + pygments_css
        html = html[:style_match.start(1)] + new_style + html[style_match.end(1):]
    
    # CRITICAL: WeChat strips <style> tags — use premailer for production-grade inline
    from premailer import transform
    html = transform(html)
    # Premailer wraps in <html><body> — WeChat doesn't need these wrappers
    import re
    html = re.sub(r'^<html><head></head><body[^>]*>', '', html)
    html = re.sub(r'</body></html>$', '', html)
    print(f"CSS inlined via premailer: style= count = {html.count('style=')}")
    
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
            # Delete if it matches our article's topic
            if draft_title == title or ("深度学习的骨架" in draft_title):
                del_req = urllib.request.Request(f"https://api.weixin.qq.com/cgi-bin/draft/delete?access_token={token}",
                    data=json.dumps({"media_id":item["media_id"]}).encode(),
                    headers={"Content-Type":"application/json"})
                r = json.loads(urllib.request.urlopen(del_req).read())
                print(f"Deleted old: {draft_title} -> {r}")
    
    # Create draft
    draft = {"articles":[{
        "title": title,
        "thumb_media_id": thumb_id,
        "author": "pwn",
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
    cover = sys.argv[2] if len(sys.argv) > 2 else "/tmp/cover-08.jpg"
    push_to_wechat(article, cover)
