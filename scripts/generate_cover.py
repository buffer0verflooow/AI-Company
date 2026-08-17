#!/usr/bin/env python3
"""Generate WeChat article cover images (900×383) — v5 variant-rotation.

Styles (pick one per article so the feed doesn't look uniform):
  blue   — deep navy gradient + geometric dots/circles  (default, A 赛道攻防)
  dark   — black + matrix grid + green accent           (B 赛道 EDR/对抗)
  clean  — light minimal + bold title + accent line     (C 赛道治理/CISO)
  red    — dark red gradient + warning accent           (D 变现/预警向)
  violet — deep violet + circuit lines                  (AI/翻译研究向)

v5 additions:
  - violet has 3 decoration variants: circuit / nebula / spectrum.
  - Automatic variant rotation: the script finds the most recent cover in
    content-jobs and picks the variant that differs most from it, so two
    consecutive same-style articles never look alike (A).
  - Similarity gate: after rendering, the cover is pixel-compared to the
    previous one at thumbnail scale; if it looks too similar (< 0.05), it
    escalates through the remaining variants before accepting (C).
  - Explicit `--variant NAME` bypasses both behaviors.

Usage:
  python generate_cover.py <number> <title> [subtitle] [output] [--style NAME] [--badge TEXT] [--variant NAME]
"""

import sys, os, math
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

OUTPUT_SIZE = (900, 383)

STYLES = {
    "blue": {
        "bg_top": (10, 15, 35), "bg_bot": (25, 45, 85),
        "accent": (64, 160, 255), "accent_dim": (40, 100, 180),
        "text": (255, 255, 255), "text_dim": (200, 215, 240), "sub": (148, 163, 184),
    },
    "dark": {
        "bg_top": (8, 10, 12), "bg_bot": (20, 28, 24),
        "accent": (0, 230, 118), "accent_dim": (0, 140, 80),
        "text": (220, 255, 235), "text_dim": (150, 200, 175), "sub": (110, 160, 135),
    },
    "clean": {
        "bg_top": (245, 247, 250), "bg_bot": (225, 232, 242),
        "accent": (29, 78, 216), "accent_dim": (90, 140, 220),
        "text": (20, 30, 50), "text_dim": (70, 90, 120), "sub": (110, 125, 150),
    },
    "red": {
        "bg_top": (35, 8, 10), "bg_bot": (80, 20, 25),
        "accent": (255, 90, 80), "accent_dim": (160, 45, 40),
        "text": (255, 235, 230), "text_dim": (240, 190, 185), "sub": (200, 140, 135),
    },
    "violet": {
        "bg_top": (20, 10, 40), "bg_bot": (50, 25, 90),
        "accent": (167, 139, 250), "accent_dim": (100, 70, 170),
        "text": (245, 240, 255), "text_dim": (210, 200, 240), "sub": (160, 145, 200),
    },
}

BADGES = {
    "攻防": "A 攻防实战", "对抗": "B EDR 对抗", "治理": "C Agent 治理",
    "变现": "D 变现向", "翻译": "翻译", "原创": "原创", "AI 工程": "AI 工程",
}


def find_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    import subprocess
    try:
        result = subprocess.run(["fc-list", ":lang=zh", "file"], capture_output=True, text=True)
        for line in result.stdout.strip().split("\n"):
            fp = line.split(":")[0].strip()
            if fp and os.path.exists(fp):
                return ImageFont.truetype(fp, size)
    except Exception:
        pass
    return ImageFont.load_default()


def gradient_bg(draw: ImageDraw.ImageDraw, w: int, h: int, p: dict):
    for y in range(h):
        ratio = y / h
        r = int(p["bg_top"][0] + (p["bg_bot"][0] - p["bg_top"][0]) * ratio)
        g = int(p["bg_top"][1] + (p["bg_bot"][1] - p["bg_top"][1]) * ratio)
        b = int(p["bg_top"][2] + (p["bg_bot"][2] - p["bg_top"][2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def deco_blue(draw, w, h, p):
    for x in range(600, w, 30):
        for y in range(20, 200, 30):
            draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=p["accent_dim"])
    cx, cy, r = 730, 150, 180
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=p["accent_dim"], width=1)
    draw.ellipse([cx - 50, cy - 50, cx + 50, cy + 50], outline=p["accent_dim"], width=1)
    draw.line([(50, 270), (450, 270)], fill=p["accent"], width=1)
    draw.line([(50, 275), (250, 275)], fill=p["accent"], width=3)


def deco_dark(draw, w, h, p):
    # Matrix-style dot grid + scanline
    for x in range(30, w, 24):
        for y in range(20, h, 24):
            if (x + y) % 48 == 0:
                draw.ellipse([x, y, x + 2, y + 2], fill=p["accent_dim"])
    for y in range(0, h, 48):
        draw.line([(0, y), (w, y)], fill=(30, 60, 45), width=1)
    # Corner brackets (terminal feel)
    for bx, by, dx, dy in [(40, 40, 1, 1), (w - 40, 40, -1, 1), (40, h - 40, 1, -1), (w - 40, h - 40, -1, -1)]:
        ln = 26
        draw.line([(bx, by), (bx + dx * ln, by)], fill=p["accent"], width=2)
        draw.line([(bx, by), (bx, by + dy * ln)], fill=p["accent"], width=2)


def deco_clean(draw, w, h, p):
    # Light minimal: thin accent frame + bottom bar
    draw.rectangle([30, 24, w - 30, h - 24], outline=p["accent_dim"], width=1)
    draw.rectangle([30, h - 40, w - 30, h - 24], fill=p["accent"])
    # subtle vertical accent on left
    draw.rectangle([30, 24, 36, h - 24], fill=p["accent"])


def deco_red(draw, w, h, p):
    # Warning stripes top-right + diagonal
    for i in range(8):
        x0 = w - 60 - i * 34
        draw.line([(x0, 0), (x0 + 34, h)], fill=p["accent_dim"], width=3)
    draw.rectangle([50, 24, 66, h - 24], fill=p["accent"])
    draw.line([(50, 270), (430, 270)], fill=p["accent"], width=2)


def deco_violet(draw, w, h, p):
    # Circuit lines + nodes (default violet variant)
    import random
    random.seed(7)
    pts = [(x, y) for x in range(60, w, 140) for y in range(30, h, 100)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        draw.line([(x1, y1), (x2, y2)], fill=p["accent_dim"], width=1)
    for x, y in pts:
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=p["accent"])
    draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)


def deco_violet_nebula(draw, w, h, p):
    # Planet: large centered FILLED disc + bright ring + sparse star field.
    # (violet variant 2) — the filled disc gives a large-area brightness block
    # that survives thumbnail downscaling, unlike sparse line layouts.
    import random, math
    random.seed(11)
    cx, cy = w // 2, h // 2 - 30
    # large filled disc (big-area contrast vs circuit/spectrum)
    draw.ellipse([cx - 150, cy - 150, cx + 150, cy + 150], fill=p["accent_dim"])
    # bright outer ring
    draw.ellipse([cx - 130, cy - 130, cx + 130, cy + 130], outline=p["accent"], width=4)
    draw.ellipse([cx - 100, cy - 100, cx + 100, cy + 100], outline=p["accent_dim"], width=2)
    # core
    draw.ellipse([cx - 22, cy - 22, cx + 22, cy + 22], fill=p["accent"])
    # sparse stars outside the disc
    for _ in range(90):
        x = random.randrange(25, w - 25)
        y = random.randrange(15, h - 15)
        if (x - cx) ** 2 + (y - cy) ** 2 < 160 ** 2:
            continue
        r = random.choice([1, 1, 2])
        draw.ellipse([x - r, y - r, x + r, y + r], fill=p["accent_dim"])
    draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)


def deco_violet_spectrum(draw, w, h, p):
    # Spectrum analyzer: tall bars + baseline + peak caps (violet variant 3)
    import random
    random.seed(23)
    bar_w = 18
    xs = list(range(55, w - 40, bar_w + 6))
    # silhouette blocks behind bars for strong contrast
    for x in xs:
        draw.rectangle([x - 2, h - 120, x + bar_w + 2, h - 40], fill=p["accent_dim"])
    for x in xs:
        hgt = random.randrange(40, 220)
        y0 = h - 45
        draw.rectangle([x, y0 - hgt, x + bar_w, y0], fill=p["accent_dim"])
        draw.rectangle([x, y0 - hgt, x + bar_w, y0 - hgt + 10], fill=p["accent"])
    for x in xs:
        y0 = h - 45
        draw.ellipse([x - 4, y0 - 4, x + 4, y0 + 4], fill=p["accent"])
        draw.ellipse([x - 4, h - 132, x + 4, h - 124], fill=p["accent"])
    draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)


# ---- v4: per-article themed decorations ----
# Each style now has multiple variants + a theme layer driven by title keywords,
# so two articles in the same style never look identical.

import random as _random

def _theme_decoration(draw, w, h, p, title: str):
    """Pick a content-specific decoration based on title keywords.
    Falls back to the style's default variant when no theme matches."""
    s = title
    seed = abs(hash(title)) % (2 ** 32)
    _random.seed(seed)
    if any(k in s for k in ["HackerOne", "实名", "身份", "验证", "白帽", "KYC", "匿名"]):
        # ID-verification theme: ID card + shield + checkmark (HackerOne 强制实名)
        # Positioned RIGHT side, clear of the left-aligned title block (x<=~540, y<=~300)
        cx, cy = 700, 170
        cw, chh = 250, 165
        x0, y0 = cx - cw // 2, cy - chh // 2
        # card body fill (slightly deeper so it reads at thumbnail size)
        draw.rounded_rectangle([x0, y0, x0 + cw, y0 + chh], radius=14,
                               fill=(233, 240, 252), outline=p["accent"], width=5)
        # photo placeholder (head circle + shoulders)
        px, py = x0 + 48, y0 + 40
        draw.ellipse([px - 20, py - 20, px + 20, py + 20], outline=p["accent"], width=2)
        draw.arc([px - 27, py + 8, px + 27, py + 60], 180, 360, fill=p["accent_dim"], width=2)
        # text lines (name / id / expiry)
        for i in range(4):
            lx = x0 + 95
            ly = y0 + 34 + i * 24
            lw = 125 - i * 14
            draw.rounded_rectangle([lx, ly, lx + lw, ly + 9], radius=5, fill=p["accent_dim"])
        # green checkmark inside shield (verified)
        sx, sy = x0 + cw - 52, y0 + chh - 48
        draw.polygon([(sx, sy - 20), (sx + 18, sy - 20), (sx + 25, sy - 7), (sx + 18, sy + 13),
                      (sx, sy + 13), (sx - 7, sy - 7)], outline=p["accent"], width=2)
        draw.line([(sx - 3, sy - 3), (sx + 5, sy + 5)], fill=p["accent"], width=3)
        draw.line([(sx + 5, sy + 5), (sx + 16, sy - 10)], fill=p["accent"], width=3)
        # small scattered verification dots (sparse, right side only)
        for _ in range(10):
            dx = _random.randrange(560, w - 20)
            dy = _random.randrange(20, h - 20)
            if x0 - 10 <= dx <= x0 + cw + 10 and y0 - 10 <= dy <= y0 + chh + 10:
                continue
            draw.ellipse([dx - 2, dy - 2, dx + 2, dy + 2], fill=p["accent_dim"])
        draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)
        return True
    if any(k in s for k in ["注册表", "PE", "二进制", "解密", "哈希", "hex", "逆向", "固件", "驱动"]):
        # Hexadecimal byte-stream rain (RE / binary analysis theme)
        hex_chars = "0123456789abcdef"
        for x in range(30, w - 20, 46):
            for y in range(15, h - 15, 30):
                if _random.random() < 0.35:
                    ch = hex_chars[_random.randrange(16)]
                    draw.text((x, y), ch, fill=p["accent_dim"])
        for x in range(60, w - 40, 92):
            draw.line([(x, 20), (x, h - 20)], fill=p["accent_dim"], width=1)
            for y in range(15, h - 15, 22):
                draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=p["accent"])
        draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)
        return True
    if any(k in s for k in ["网关", "劫持", "注入", "拦截", "代理", "隧道", "流量", "API"]):
        # Pipe / traffic-flow lines (gateway & interception theme)
        for i in range(6):
            x0 = 40 + i * 22
            y0 = 30 + _random.randrange(0, 60)
            x1 = w - 60 - i * 18
            y1 = h - 40 - _random.randrange(0, 60)
            draw.line([(x0, y0), (x1, y1)], fill=p["accent_dim"], width=2)
            draw.ellipse([x0 - 3, y0 - 3, x0 + 3, y0 + 3], fill=p["accent"])
            draw.ellipse([x1 - 3, y1 - 3, x1 + 3, y1 + 3], fill=p["accent"])
        draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)
        return True
    if any(k in s for k in ["Agent", "蜂群", "多", "协作", "Swarm", "自治"]):
        # Network topology nodes (agent / swarm theme)
        nodes = []
        for _ in range(7):
            x = _random.randrange(80, w - 80)
            y = _random.randrange(30, h - 60)
            nodes.append((x, y))
        for i, (x, y) in enumerate(nodes):
            for x2, y2 in nodes[i + 1:]:
                if _random.random() < 0.4:
                    draw.line([(x, y), (x2, y2)], fill=p["accent_dim"], width=1)
        for x, y in nodes:
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], outline=p["accent"], width=2)
        draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)
        return True
    if any(k in s for k in ["内核", "EDR", "Rootkit", "提权", "漏洞", "CVE"]):
        # Matrix-style dot grid (kernel / defensive theme)
        for x in range(30, w, 22):
            for y in range(20, h - 20, 22):
                if _random.random() < 0.3:
                    draw.ellipse([x, y, x + 2, y + 2], fill=p["accent_dim"])
        draw.line([(50, 275), (420, 275)], fill=p["accent"], width=2)
        return True
    return False


DECOS = {"blue": deco_blue, "dark": deco_dark, "clean": deco_clean, "red": deco_red, "violet": deco_violet}

# v5: per-style decoration variants so consecutive same-style articles differ.
STYLE_VARIANTS = {
    "violet": {"circuit": deco_violet, "nebula": deco_violet_nebula, "spectrum": deco_violet_spectrum},
}
VARIANT_ORDER = {"violet": ["circuit", "nebula", "spectrum"]}

# Similarity gate: a new cover is rejected if pixel-diff vs the previous
# published cover is below this threshold (looks like a duplicate in the feed).
SIMILARITY_THRESHOLD = 0.05


def auto_style(title: str = "", subtitle: str = "") -> str:
    """Pick a style from title/subtitle keywords so consecutive articles differ."""
    s = title + " " + subtitle
    if any(k in s for k in ["治理", "合规", "CISO", "预算", "框架", "指南", "报告"]):
        return "clean"
    if any(k in s for k in ["EDR", "对抗", "绕过", "Rootkit", "内核", "投毒"]):
        return "dark"
    if any(k in s for k in ["变现", "定价", "赞赏", "收入", "商业化"]):
        return "red"
    if any(k in s for k in ["翻译", "研究", "论文", "LLM", "Agent"]):
        return "violet"
    return "blue"


def _find_last_cover(exclude_path: str = "") -> Optional[str]:
    """Find the most recent cover.jpg in content-jobs (the 'previous' cover in the feed).

    Uses content-jobs/*/cover.jpg mtime as proxy for publication order. Excludes
    the path being generated (and v1 backups) so re-runs don't compare to themselves.
    """
    import glob
    import time as _time
    candidates = []
    base = os.path.expanduser("~/workspace/company/operations/runtime/content-jobs")
    for p in glob.glob(os.path.join(base, "*", "cover.jpg")):
        if p == exclude_path:
            continue
        try:
            candidates.append((os.path.getmtime(p), p))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _cover_similarity(path_a: str, path_b: str) -> float:
    """Normalized mean per-channel pixel diff on a 64x28 thumbnail. 0=same, 1=different."""
    try:
        from PIL import Image as _Image
        a = _Image.open(path_a).convert("RGB").resize((64, 28))
        b = _Image.open(path_b).convert("RGB").resize((64, 28))
    except Exception:
        return 0.0  # unreadable -> treat as similar (conservative, forces retry)
    pa, pb = list(a.getdata()), list(b.getdata())
    total = sum(
        abs(x[0] - y[0]) + abs(x[1] - y[1]) + abs(x[2] - y[2])
        for x, y in zip(pa, pb)
    )
    return total / (len(pa) * 3 * 255)


def _generate_image(output_path: str, title: str, subtitle: str, badge: str,
                    style: str, variant: str = "") -> object:
    """Render the cover to output_path with the given style/variant."""
    p = STYLES[style]
    img = Image.new("RGB", OUTPUT_SIZE)
    draw = ImageDraw.Draw(img)
    gradient_bg(draw, *OUTPUT_SIZE, p)
    deco = (STYLE_VARIANTS.get(style) or {}).get(variant) or DECOS[style]
    deco(draw, *OUTPUT_SIZE, p)
    _theme_decoration(draw, *OUTPUT_SIZE, p, title + " " + subtitle)

    # Badge pill (top-left)
    badge_font = find_font(16)
    bbox = draw.textbbox((0, 0), badge, font=badge_font)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    badge_x, badge_y = 50, 35
    pad = 14
    draw.rounded_rectangle(
        [badge_x - pad, badge_y - pad // 2, badge_x + bw + pad, badge_y + bh + pad // 2],
        radius=20, fill=p["accent"]
    )
    draw.text((badge_x, badge_y), badge, fill=p["bg_top"], font=badge_font)

    # Title with wrap
    title_font = find_font(34)
    chars_per_line = 14
    lines = []
    cur = ""
    for ch in title:
        cur += ch
        if len(cur) >= chars_per_line:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    lines = lines[:3]

    title_y = 130
    line_h = 46
    for i, line in enumerate(lines):
        color = p["text"] if i == 0 else p["text_dim"]
        draw.text((50, title_y + i * line_h), line, fill=color, font=title_font)

    if subtitle:
        sub_font = find_font(17)
        draw.text((50, title_y + len(lines) * line_h + 15), subtitle, fill=p["sub"], font=sub_font)

    img.save(output_path, quality=95)
    return img


def _pick_variant(style: str, prev_cover: str, output_path: str,
                  title: str, subtitle: str, badge: str) -> str:
    """Pick a decoration variant that visually differs from the previous cover.

    Renders a probe per variant, compares against the previous cover, returns
    the first variant whose diff clears the threshold; falls back to the most
    different variant otherwise. Returns '' (style default) when the style has
    no variants or no previous cover exists.
    """
    order = VARIANT_ORDER.get(style) or []
    if not prev_cover or not order:
        return ""
    best_variant, best_diff = "", -1.0
    for variant in order:
        probe = f"{output_path}.probe-{variant}.jpg"
        try:
            _generate_image(probe, title, subtitle, badge, style, variant)
        except Exception:
            continue
        d = _cover_similarity(probe, prev_cover)
        try:
            os.unlink(probe)
        except OSError:
            pass
        if d > best_diff:
            best_variant, best_diff = variant, d
        if d >= SIMILARITY_THRESHOLD:
            return variant
    return best_variant


def generate_cover(number: int, title: str, subtitle: str = "", output_path: str = "",
                   style: str = "", badge: str = "", variant: str = ""):
    """Generate a cover.

    Auto-rotates decoration variants so consecutive same-style covers stay
    visually distinct (A), and rejects any cover too similar to the previous
    one by escalating through remaining variants (C). Pass variant explicitly
    to skip both behaviors.
    """
    if not style:
        style = auto_style(title, subtitle)
    if style not in STYLES:
        style = "blue"
    if not badge:
        for key, val in BADGES.items():
            if key in (title + " " + subtitle):
                badge = val
                break
    if not badge:
        badge = f"#{number:02d}"

    if not output_path:
        output_path = f"/tmp/cover-{number:02d}.jpg"

    prev_cover = _find_last_cover(exclude_path=os.path.abspath(output_path))
    if variant:
        chosen = variant
    else:
        chosen = _pick_variant(style, prev_cover or "", output_path, title, subtitle, badge)

    img = _generate_image(output_path, title, subtitle, badge, style, chosen)
    d = _cover_similarity(output_path, prev_cover) if prev_cover else None
    # Similarity gate (C): escalate through remaining variants until one clears
    # the threshold, or we run out of variants.
    if prev_cover and not variant and d is not None and d < SIMILARITY_THRESHOLD:
        for v in VARIANT_ORDER.get(style) or []:
            if v == chosen:
                continue
            probe = f"{output_path}.probe2-{v}.jpg"
            try:
                _generate_image(probe, title, subtitle, badge, style, v)
            except Exception:
                continue
            d2 = _cover_similarity(probe, prev_cover)
            try:
                os.unlink(probe)
            except OSError:
                pass
            if d2 >= SIMILARITY_THRESHOLD:
                img = _generate_image(output_path, title, subtitle, badge, style, v)
                chosen, d = v, d2
                break
    print(f"Cover saved: {output_path} (style={style}, variant={chosen or 'default'}, "
          f"prev_diff={round(d, 3) if d is not None else 'n/a'})")
    return img


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: generate_cover.py <number> <title> [subtitle] [output] [--style NAME] [--badge TEXT]")
        sys.exit(1)
    number = int(args[0])
    title = args[1]
    subtitle = args[2] if len(args) > 2 and not args[2].startswith("--") else ""
    rest = args[3:] if subtitle else args[2:]
    output = f"/tmp/cover-{number:02d}.jpg"
    style, badge, variant = "", "", ""
    i = 0
    while i < len(rest):
        if rest[i] == "--style" and i + 1 < len(rest):
            style = rest[i + 1]; i += 2
        elif rest[i] == "--badge" and i + 1 < len(rest):
            badge = rest[i + 1]; i += 2
        elif rest[i] == "--variant" and i + 1 < len(rest):
            variant = rest[i + 1]; i += 2
        else:
            output = rest[i]; i += 1
    generate_cover(number, title, subtitle, output, style, badge, variant)
