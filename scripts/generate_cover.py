#!/usr/bin/env python3
"""Generate WeChat article cover images (900×383) — v2 with gradient + geometric design."""

import sys, os, math
from PIL import Image, ImageDraw, ImageFont

OUTPUT_SIZE = (900, 383)

# Color palette — deep tech blue gradient
BG_TOP = (10, 15, 35)
BG_BOT = (25, 45, 85)
ACCENT = (64, 160, 255)   # bright blue
ACCENT_DIM = (40, 100, 180)
WHITE = (255, 255, 255)
SLATE = (148, 163, 184)


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
    except:
        pass
    return ImageFont.load_default()


def gradient_bg(draw: ImageDraw.ImageDraw, w: int, h: int):
    """Vertical gradient from dark navy to deep blue."""
    for y in range(h):
        ratio = y / h
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * ratio)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * ratio)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def draw_geometric(draw: ImageDraw.ImageDraw, w: int, h: int):
    """Geometric accents: grid dots, circles, diagonal lines."""
    # Subtle grid of dots (top-right quadrant)
    for x in range(600, w, 30):
        for y in range(20, 200, 30):
            alpha = 30 + int(20 * (1 - (y - 20) / 180))
            draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(ACCENT[0], ACCENT[1], ACCENT[2], alpha) if hasattr(ImageDraw, 'RGBA') else ACCENT_DIM)

    # Large faint circle (top right)
    cx, cy, r = 730, 150, 180
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT_DIM, width=1)

    # Small circle
    draw.ellipse([cx - 50, cy - 50, cx + 50, cy + 50], outline=ACCENT_DIM, width=1)

    # Accent lines
    draw.line([(50, 270), (450, 270)], fill=ACCENT, width=1)
    draw.line([(50, 275), (250, 275)], fill=ACCENT, width=3)


def generate_cover(number: int, title: str, subtitle: str = "", output_path: str = ""):
    img = Image.new("RGB", OUTPUT_SIZE)
    draw = ImageDraw.Draw(img)
    gradient_bg(draw, *OUTPUT_SIZE)
    draw_geometric(draw, *OUTPUT_SIZE)

    # Series badge (top-left pill)
    badge_font = find_font(16)
    badge_text = f"AI 工程 #{number}"
    bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    badge_x, badge_y = 50, 35
    pad = 14
    draw.rounded_rectangle(
        [badge_x - pad, badge_y - pad // 2, badge_x + bw + pad, badge_y + bh + pad // 2],
        radius=20, fill=ACCENT
    )
    draw.text((badge_x, badge_y), badge_text, fill=WHITE, font=badge_font)

    # Title — large, bold, with character-level word wrap
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
        # Slight opacity fade on second/third lines for depth
        color = WHITE if i == 0 else (200, 215, 240)
        draw.text((50, title_y + i * line_h), line, fill=color, font=title_font)

    # Subtitle
    if subtitle:
        sub_font = find_font(17)
        draw.text((50, title_y + len(lines) * line_h + 15), subtitle, fill=SLATE, font=sub_font)

    if output_path:
        img.save(output_path, quality=95)
        print(f"Cover saved: {output_path}")
    return img


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: generate_cover.py <number> <title> [subtitle] [output_path]")
        sys.exit(1)
    generate_cover(int(args[0]), args[1], args[2] if len(args) > 2 else "",
                    args[3] if len(args) > 3 else f"/tmp/cover-{int(args[0]):02d}.jpg")
