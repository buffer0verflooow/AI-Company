#!/usr/bin/env python3
"""Generate unique cover images for articles using PIL.

Usage:
    python3 gen_cover.py --title "文章标题" --category "安全" --output-dir ./assets/
    python3 gen_cover.py --title "文章标题" --subtitle "副标题" --author "作者" --output-dir ./assets/

Output:
    cover-{slug}.png   (900×383, main cover)
    cover-{slug}-thumb.png (200×200, thumbnail)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Color palettes (dark tech-themed) ──────────────────────────────────────
PALETTES = [
    {"bg": "#0f0e17", "accent": "#ff8906", "text": "#fffffe", "sub": "#a7a9be"},
    {"bg": "#1a1a2e", "accent": "#e94560", "text": "#eee", "sub": "#a0a0b0"},
    {"bg": "#0d1117", "accent": "#58a6ff", "text": "#f0f6fc", "sub": "#8b949e"},
    {"bg": "#16161a", "accent": "#7f5af0", "text": "#fffffe", "sub": "#94a1b2"},
    {"bg": "#1e1e2e", "accent": "#f38ba8", "text": "#cdd6f4", "sub": "#6c7086"},
    {"bg": "#11111b", "accent": "#a6e3a1", "text": "#cdd6f4", "sub": "#585b70"},
    {"bg": "#0a0a0a", "accent": "#00ff88", "text": "#e0e0e0", "sub": "#808080"},
    {"bg": "#1b1b2f", "accent": "#ff6b6b", "text": "#f7f7ff", "sub": "#8888aa"},
    {"bg": "#12121a", "accent": "#ffd700", "text": "#ececec", "sub": "#909090"},
    {"bg": "#0c0c1d", "accent": "#00d4aa", "text": "#f5f5f5", "sub": "#777799"},
    {"bg": "#1a1a2e", "accent": "#c084fc", "text": "#e2e8f0", "sub": "#94a3b8"},
    {"bg": "#131320", "accent": "#22d3ee", "text": "#f8fafc", "sub": "#94a3b8"},
]

# ── Font setup ────────────────────────────────────────────────────────────
FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]

def _find_font(size: int = 32) -> ImageFont.FreeTypeFont:
    """Find a CJK-capable font at given size."""
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Fallback to default
    return ImageFont.load_default()


def _title_slug(title: str) -> str:
    """Convert title to a filesystem-safe slug."""
    s = re.sub(r'[^\w\s-]', '', title)
    s = re.sub(r'[-\s]+', '-', s.strip().lower())
    return s[:40] if s else 'article'


def _pick_palette(seed: str) -> dict:
    """Deterministically pick a palette based on a seed string."""
    h = hashlib.sha256(seed.encode()).hexdigest()
    idx = int(h[:8], 16) % len(PALETTES)
    return PALETTES[idx]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont,
               max_width: int) -> list[str]:
    """Wrap Chinese/English text to fit within max_width."""
    lines = []
    current = ""
    for char in text:
        test = current + char
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def generate_cover(title: str, output_dir: str, *,
                   subtitle: str = "",
                   author: str = "",
                   category: str = "",
                   size: tuple[int, int] = (900, 383),
                   thumb_size: tuple[int, int] = (200, 200),
                   ) -> tuple[str, str]:
    """Generate a cover image and thumbnail.

    Returns (cover_path, thumb_path).
    """
    slug = _title_slug(title)
    palette = _pick_palette(title)
    bg_rgb = _hex_to_rgb(palette["bg"])
    accent_rgb = _hex_to_rgb(palette["accent"])
    text_rgb = _hex_to_rgb(palette["text"])
    sub_rgb = _hex_to_rgb(palette["sub"])

    W, H = size
    img = Image.new("RGB", (W, H), bg_rgb)
    draw = ImageDraw.Draw(img)

    # ── Accent stripe on the left ──────────────────────────────────────────
    stripe_w = 6
    draw.rectangle([0, 0, stripe_w, H], fill=accent_rgb)

    # ── Top accent glow bar ────────────────────────────────────────────────
    bar_y = 20
    bar_h = 3
    draw.rectangle([40, bar_y, 40 + 80, bar_y + bar_h], fill=accent_rgb)

    # ── Category label (top right) ─────────────────────────────────────────
    if category:
        cat_font = _find_font(14)
        cat_bbox = draw.textbbox((0, 0), category, font=cat_font)
        cat_w = cat_bbox[2] - cat_bbox[0]
        cat_x = W - cat_w - 40
        cat_y = 18
        # Pill with accent background
        pad = 10
        draw.rounded_rectangle(
            [cat_x - pad, cat_y - 3, cat_x + cat_w + pad, cat_y + cat_bbox[3] - cat_bbox[1] + 3],
            radius=10,
            fill=accent_rgb
        )
        draw.text((cat_x, cat_y), category, fill=bg_rgb, font=cat_font)

    # ── Title ──────────────────────────────────────────────────────────────
    title_font = _find_font(28)
    # Calculate available width for title
    margin_left = 50
    margin_right = 40
    max_title_w = W - margin_left - margin_right

    title_lines = _wrap_text(draw, title, title_font, max_title_w)
    # Cap at 3 lines
    title_lines = title_lines[:3]
    if len(title_lines) == 3 and len(title) > len(''.join(title_lines)):
        title_lines[2] = title_lines[2][:-1] + "…"

    title_start_y = 80
    line_h = 42
    for i, line in enumerate(title_lines):
        y = title_start_y + i * line_h
        draw.text((margin_left, y), line, fill=text_rgb, font=title_font)

    # ── Subtitle ───────────────────────────────────────────────────────────
    if subtitle:
        sub_font = _find_font(16)
        sub_y = title_start_y + len(title_lines) * line_h + 15
        draw.text((margin_left, sub_y), subtitle, fill=sub_rgb, font=sub_font)

    # ── Author ─────────────────────────────────────────────────────────────
    if author:
        auth_font = _find_font(13)
        auth_y = H - 40
        draw.text((margin_left, auth_y), author, fill=sub_rgb, font=auth_font)

    # ── Bottom accent line ─────────────────────────────────────────────────
    line_y = H - 20
    draw.rectangle([40, line_y, W - 40, line_y + 1], fill=sub_rgb)

    # ── Decorative geometric element (bottom right) ────────────────────────
    # A subtle accent circle in the bottom right corner
    cx, cy = W - 50, H - 60
    r = 30
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent_rgb, width=1)

    # Inner dot
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=accent_rgb)

    # ── Save ───────────────────────────────────────────────────────────────
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cover_path = out_dir / f"cover-{slug}.png"
    thumb_path = out_dir / f"cover-{slug}-thumb.png"

    try:
        img.save(cover_path, "PNG")

        # ── Thumbnail ──────────────────────────────────────────────────────
        thumb = img.copy()
        try:
            thumb.thumbnail(thumb_size, Image.LANCZOS)
            # If the result is smaller (non-square aspect), center on a square canvas
            if thumb.size != thumb_size:
                canvas = Image.new("RGB", thumb_size, bg_rgb)
                try:
                    offset_x = (thumb_size[0] - thumb.size[0]) // 2
                    offset_y = (thumb_size[1] - thumb.size[1]) // 2
                    canvas.paste(thumb, (offset_x, offset_y))
                    canvas.save(thumb_path, "PNG")
                finally:
                    canvas.close()
            else:
                thumb.save(thumb_path, "PNG")
        finally:
            thumb.close()
    finally:
        img.close()

    return str(cover_path), str(thumb_path)


def main():
    parser = argparse.ArgumentParser(description="Generate article cover images")
    parser.add_argument("--title", required=True, help="Article title")
    parser.add_argument("--subtitle", default="", help="Article subtitle")
    parser.add_argument("--author", default="", help="Author name")
    parser.add_argument("--category", default="", help="Article category")
    parser.add_argument("--output-dir", default="./assets", help="Output directory")
    args = parser.parse_args()

    cover, thumb = generate_cover(
        title=args.title,
        output_dir=args.output_dir,
        subtitle=args.subtitle,
        author=args.author,
        category=args.category,
    )
    print(f"Cover: {cover}")
    print(f"Thumb: {thumb}")


if __name__ == "__main__":
    main()
