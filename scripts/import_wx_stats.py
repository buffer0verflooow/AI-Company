#!/usr/bin/env python3
"""
微信后台数据统计 zip → article_performance.db 自动解析入库

背景：个人主体公众号无 datacube API 权限（48001），文章统计数据只能从
mp.weixin.qq.com 后台手动导出（统计 → 图文分析 → 导出）。导出的 zip 含
每篇文章一个 .xls（数据明细）+ tendency_*.xls（趋势总表）。

本脚本把手动导出的 zip 自动解析入库，让"手动导出 → 自动入库"链路跑通。

Usage:
  python3 import_wx_stats.py <导出的zip路径>
  python3 import_wx_stats.py --dir <含多个zip的目录>
  python3 import_wx_stats.py --last   # 解析 evidence 下最新一个 zip

依赖: xlrd（python3 -m pip install xlrd）
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime

try:
    import xlrd
except ImportError:
    sys.exit("需要 xlrd: python3 -m pip install xlrd")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "marketing", "article_performance.db")


# ── 单篇明细 xls 解析 ──────────────────────────────────────────

def parse_article_xls(path):
    """解析单篇数据明细 xls，返回指标 dict + 趋势明细 list"""
    wb = xlrd.open_workbook(path)
    s = wb.sheets()[0]
    rows = [[s.cell_value(r, c) for c in range(s.ncols)] for r in range(s.nrows)]

    title = ""
    metrics = {}
    trend = []  # (date, channel, reads, shares)

    section = None
    for row in rows:
        # 行结构: ['', 标签, 值, ...]
        label = str(row[1]).strip() if len(row) > 1 else ""
        val = row[2] if len(row) > 2 else ""

        if label and not metrics and not title:
            title = label  # 第一行是标题（r0 col1）

        if label == "数据概况":
            section = "overview"
            continue
        if label == "阅读转化":
            section = "funnel"
            continue
        if label == "阅读数据趋势明细":
            section = "trend"
            continue

        if section == "trend":
            # 趋势明细: [日期, 传播渠道, 阅读人数, 分享人数]
            date = str(label)
            # 严格匹配 YYYY-MM-DD 日期（避免"18-25岁"等含 - 的行误判）
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date) and len(row) >= 4:
                trend.append((date, str(row[2]).strip(), _num(row[3]), _num(row[4]) if len(row) > 4 else 0))
            continue

        if section in ("overview", "funnel") and label not in ("数据指标", ""):
            metrics[label] = _num(val)

    return title, metrics, trend


def _num(v):
    """转 float，空/非数字返回 0"""
    try:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace(",", "").strip()
        return float(s) if s else 0.0
    except (ValueError, TypeError):
        return 0.0


# ── 趋势总表解析 ──────────────────────────────────────────────

def parse_tendency_xls(path):
    """解析趋势总表，返回 (日期, 渠道, 阅读人数) 列表"""
    wb = xlrd.open_workbook(path)
    s = wb.sheets()[0]
    out = []
    for r in range(s.nrows):
        date = str(s.cell_value(r, 1)).strip()
        channel = str(s.cell_value(r, 2)).strip()
        reads = s.cell_value(r, 3)
        if "-" in date and channel and channel not in ("渠道", ""):
            out.append((date, channel, _num(reads)))
    return out


# ── 入库 ──────────────────────────────────────────────────────

def import_zip(zip_path, conn):
    """解析一个 zip 并入库，返回 (文章数, 趋势条数, 去重跳过数)"""
    z = zipfile.ZipFile(zip_path)
    names = [n for n in z.namelist() if n.endswith(".xls") and "__MACOSX" not in n]

    # 单篇明细文件（文件名 = 标题，非 tendency 前缀）
    article_files = [n for n in names if not os.path.basename(n).startswith("tendency_")]
    tendency_files = [n for n in names if os.path.basename(n).startswith("tendency_")]

    articles = 0
    trend_rows = 0
    skipped = 0
    source_rows = 0

    for n in article_files:
        raw = z.read(n)
        sha = hashlib.sha256(raw).hexdigest()
        # 去重：source_sha256 相同则跳过
        dup = conn.execute(
            "SELECT 1 FROM article_source_metrics WHERE source_sha256=?", (sha,)
        ).fetchone()
        if dup:
            skipped += 1
            continue

        tmp = os.path.join("/tmp", f"_wx_{os.path.basename(n)}")
        with open(tmp, "wb") as fh:
            fh.write(raw)
        title, metrics, trend = parse_article_xls(tmp)
        os.remove(tmp)

        if not title:
            skipped += 1
            continue

        now = datetime.utcnow().isoformat()
        # article_metrics（逐篇指标）—— 完整 schema 适配
        trend_json = json.dumps(trend, ensure_ascii=False)
        conn.execute(
            """INSERT INTO article_metrics
               (article_id, title, platform, published_at, measured_at, reads,
                avg_dwell_seconds, completion_rate, listen_count, new_followers,
                shares, wow_count, likes, favorites, reward_points, comments,
                delivered, message_reads, first_shares, total_shares,
                share_generated_reads, trend_json, demographics_json,
                source_path, source_sha256, imported_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"wx-{sha[:12]}", title, "wechat", trend[0][0] if trend else "", now,
                int(metrics.get("阅读(人)", 0)), metrics.get("平均停留时长(秒)", 0),
                metrics.get("完读率", 0), int(metrics.get("听全文（人）", 0)),
                int(metrics.get("新增关注（人）", 0)), int(metrics.get("分享(人)", 0)),
                int(metrics.get("在看(人)", 0)), int(metrics.get("点赞(人)", 0)),
                int(metrics.get("收藏(人)", 0)), metrics.get("赞赏(分)", 0),
                int(metrics.get("评论（条）", 0)), int(metrics.get("送达人数", 0)),
                int(metrics.get("公众号消息阅读人数", 0)),
                int(metrics.get("首次分享人数", 0)), int(metrics.get("总分享人数", 0)),
                int(metrics.get("分享产生的阅读人数", 0)),
                trend_json, "{}", zip_path, sha, now,
            ),
        )
        # article_source_metrics（原始文件留痕）
        conn.execute(
            """INSERT INTO article_source_metrics
               (title, published_at, channel, reads, read_share, measured_at,
                source_path, source_sha256, imported_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                title, trend[0][0] if trend else "", "wechat-export",
                metrics.get("阅读(人)", 0), metrics.get("分享(人)", 0),
                now, zip_path, sha, now,
            ),
        )
        articles += 1

        for date, channel, reads, shares in trend:
            conn.execute(
                """INSERT INTO article_source_metrics
                   (title, published_at, channel, reads, read_share, measured_at,
                    source_path, source_sha256, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (title, date, channel, reads, 0, now, zip_path, sha, now),
            )
            trend_rows += 1

    # 趋势总表（渠道级汇总）
    for n in tendency_files:
        raw = z.read(n)
        sha = hashlib.sha256(raw).hexdigest()
        # 去重：同一趋势文件的 sha 已入库则跳过
        dup = conn.execute(
            "SELECT 1 FROM article_source_metrics WHERE source_sha256=? AND title='[趋势总表]'", (sha,)
        ).fetchone()
        if dup:
            skipped += 1
            continue
        tmp = os.path.join("/tmp", "_wx_tendency.xls")
        with open(tmp, "wb") as fh:
            fh.write(raw)
        rows = parse_tendency_xls(tmp)
        os.remove(tmp)
        for date, channel, reads in rows:
            conn.execute(
                """INSERT INTO article_source_metrics
                   (title, published_at, channel, reads, read_share, measured_at,
                    source_path, source_sha256, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                ("[趋势总表]", date, channel, reads, 0,
                 datetime.utcnow().isoformat(), zip_path, sha, datetime.utcnow().isoformat()),
            )
            source_rows += 1

    conn.commit()
    return articles, trend_rows + source_rows, skipped


def main():
    ap = argparse.ArgumentParser(description="微信数据统计 zip 自动入库")
    ap.add_argument("zip", nargs="?", help="zip 文件路径")
    ap.add_argument("--dir", help="目录，解析其中所有 zip")
    ap.add_argument("--last", action="store_true", help="解析 evidence 下最新 zip")
    args = ap.parse_args()

    evidence = os.path.join(os.path.dirname(__file__), "..", "marketing", "evidence")
    if args.last:
        zips = sorted(glob.glob(os.path.join(evidence, "**", "*.zip"), recursive=True), key=os.path.getmtime)
        if not zips:
            sys.exit("evidence 下没有 zip")
        targets = [zips[-1]]
        print(f"解析最新 zip: {targets[0]}")
    elif args.dir:
        targets = sorted(glob.glob(os.path.join(args.dir, "*.zip")))
    elif args.zip:
        targets = [args.zip]
    else:
        ap.print_help()
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    for z in targets:
        try:
            a, t, s = import_zip(z, conn)
            print(f"  {os.path.basename(z)}: 文章 {a} | 趋势 {t} | 去重跳过 {s}")
        except Exception as e:
            print(f"  {os.path.basename(z)}: 失败 {e}")

    # 汇总
    n_art = conn.execute("SELECT COUNT(*) FROM article_metrics").fetchone()[0]
    n_src = conn.execute("SELECT COUNT(*) FROM article_source_metrics").fetchone()[0]
    print(f"\n入库后总量: article_metrics={n_art} | article_source_metrics={n_src}")
    conn.close()


if __name__ == "__main__":
    main()
