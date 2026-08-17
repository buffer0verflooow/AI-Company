#!/usr/bin/env python3
"""Generate 3 infographic images for the A9 Fable 5 article (900px wide, WeChat-ready).

图 1: 攻击流程图 — 图片谜题 → Opus 分析 → 被劫持 → memory view + add×4 → 虚假记忆持久化
图 2: 降级机制图 — forbidden topic → 分类器误报 → 静默降级 Opus 4.8 → 旧研究复活
图 3: 数据图 — 5/10 成功率 vs Mythos k=100 21.7% vs k=1 0.2% + 24h 归零
"""
from PIL import Image, ImageDraw, ImageFont
import os

OUT_DIR = "/home/pwn/workspace/company/operations/runtime/content-jobs/a9-fable5-model-downgrade-20260811"
W = 900

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]

def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for cand in FONT_CANDIDATES:
        if os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                continue
    return ImageFont.load_default()

# color palette (violet theme, matches cover)
BG = (24, 20, 40)
PANEL = (38, 32, 62)
ACCENT = (167, 139, 250)      # violet
ACCENT2 = (250, 204, 21)      # amber
TEXT = (235, 232, 248)
DIM = (160, 155, 190)
RED = (248, 113, 113)
GREEN = (52, 211, 153)

def panel(d: ImageDraw.ImageDraw, xy, fill=PANEL, radius=14):
    d.rounded_rectangle(xy, radius=radius, fill=fill)

def text_center(d, xy, s, size=26, fill=TEXT, bold=True):
    f = font(size, bold)
    bbox = d.textbbox((0, 0), s, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((xy[0] - tw / 2, xy[1] - th / 2), s, font=f, fill=fill)

def text_left(d, xy, s, size=24, fill=TEXT, bold=True):
    f = font(size, bold)
    d.text(xy, s, font=f, fill=fill)

def arrow_down(d, cx, y0, y1, color=ACCENT, width=4):
    d.line([(cx, y0), (cx, y1)], fill=color, width=width)
    d.polygon([(cx - 10, y1 - 14), (cx + 10, y1 - 14), (cx, y1)], fill=color)

# ---------------------------------------------------------------- 图 1 攻击流程
def fig1():
    H = 760
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    text_center(d, (W // 2, 50), "攻击链：一张图片，劫持记忆", 34, ACCENT)
    text_center(d, (W // 2, 92), "ChatGPT 生成的对抗图片 → Claude Opus 4.7 的 memory 工具", 22, DIM, bold=False)

    steps = [
        ("① 生成谜题", "ChatGPT 生成图片谜题\n谜底 = 用户真实信息"),
        ("② 图片内嵌指令", "黑底深色文字 + 高亮\nantml memory 引导调工具"),
        ("③ Opus 分析图片", "开着 Adaptive Thinking\n\"分析这张图片\""),
        ("④ 被社交工程劫持", "模型中途察觉可疑\n仍调用了 memory 工具"),
    ]
    y = 140
    for title, desc in steps:
        panel(d, (60, y, W - 60, y + 110))
        text_left(d, (90, y + 16), title, 26, ACCENT2)
        for i, line in enumerate(desc.split("\n")):
            text_left(d, (90, y + 54 + i * 28), line, 21, TEXT, bold=False)
        arrow_down(d, W // 2, y + 118, y + 152)
        y += 152

    # final box
    panel(d, (60, y, W - 60, y + 150), fill=(58, 30, 60))
    text_left(d, (90, y + 16), "⑤ memory 工具：view + add × 4", 27, RED)
    for i, line in enumerate([
        "先 view 检查已有记忆 → 再 add 四次写入：",
        "「用户叫 Neo」「43 岁」「NASA 宇航员」「喜欢冰淇淋和饼干」",
    ]):
        text_left(d, (90, y + 58 + i * 30), line, 21, TEXT, bold=False)
    text_center(d, (W // 2, y + 128), "虚假记忆进入上下文 → 所有未来对话都会用到", 23, ACCENT2)
    img.save(f"{OUT_DIR}/fig1-attack-chain.png")
    print("saved fig1-attack-chain.png")

# ---------------------------------------------------------------- 图 2 降级机制
def fig2():
    H = 640
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    text_center(d, (W // 2, 50), "降级攻击：为什么 Fable 5 会退回 Opus 4.8", 32, ACCENT)
    text_center(d, (W // 2, 92), "2026-08-09 @wunderwuzzi23 推文 + 社区 issue #66728 佐证", 21, DIM, bold=False)

    # left chain
    panel(d, (50, 140, 430, 560))
    text_center(d, (240, 165), "正常路径", 26, GREEN)
    steps = [
        "攻击 + forbidden topic",
        "→ 安全分类器标记话题",
        "→ 静默降级 (1M → Opus 4.8)",
        "→ 4 个月前的记忆劫持研究\n原地复活",
    ]
    y = 210
    for s in steps:
        for i, line in enumerate(s.split("\n")):
            text_center(d, (240, y + i * 26), line, 21, TEXT, bold=False)
        y += 78

    # right chain
    panel(d, (470, 140, 850, 560))
    text_center(d, (660, 165), "本质", 26, ACCENT2)
    rights = [
        "降级 = 把新模型的防御\n带回旧模型水平",
        "旧模型的已知漏洞/研究\n全部重新可用",
        "分类器误报是触发点\n(非用户可控话题)",
        "防御面随模型版本\n来回横跳",
    ]
    y = 210
    for s in rights:
        for i, line in enumerate(s.split("\n")):
            text_center(d, (660, y + i * 26), line, 21, TEXT, bold=False)
        y += 78

    text_center(d, (W // 2, 600), "模型降级不是 bug，是攻击面", 25, RED)
    img.save(f"{OUT_DIR}/fig2-downgrade.png")
    print("saved fig2-downgrade.png")

# ---------------------------------------------------------------- 图 3 数据
def fig3():
    H = 560
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    text_center(d, (W // 2, 50), "攻击成功率：定向利用 vs 基准平均", 32, ACCENT)

    # bar chart
    bars = [
        ("本文定向对抗样本", 50, RED),       # 5/10
        ("Mythos k=100 累计", 21.7, ACCENT),
        ("Mythos k=1", 0.2, GREEN),
        ("发布 24h 后", 0, DIM),
    ]
    chart_x0, chart_y0 = 140, 150
    chart_w, chart_h = 660, 320
    # axes
    d.line([(chart_x0, chart_y0), (chart_x0, chart_y0 + chart_h)], fill=DIM, width=3)
    d.line([(chart_x0, chart_y0 + chart_h), (chart_x0 + chart_w, chart_y0 + chart_h)], fill=DIM, width=3)
    maxv = 50
    for i, (label, val, color) in enumerate(bars):
        bw = 110
        bx = chart_x0 + 60 + i * (bw + 60)
        bh = int(val / maxv * (chart_h - 40))
        d.rounded_rectangle([bx, chart_y0 + chart_h - bh, bx + bw, chart_y0 + chart_h],
                            radius=8, fill=color)
        text_center(d, (bx + bw // 2, chart_y0 + chart_h - bh - 26),
                    f"{val}%" if val else "0%", 24, TEXT)
        text_center(d, (bx + bw // 2, chart_y0 + chart_h + 34), label, 19, DIM, bold=False)

    text_center(d, (W // 2, H - 40), "5/10 是单个定向对抗样本重复试验；基准平均值 ≠ 定向利用潜力", 21, ACCENT2, bold=False)
    img.save(f"{OUT_DIR}/fig3-success-rate.png")
    print("saved fig3-success-rate.png")

fig1()
fig2()
fig3()
