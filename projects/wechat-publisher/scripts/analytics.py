#!/usr/bin/env python3
"""
微信公众号图文分析 — 数据统计脚本

通过微信公众平台 datacube API 获取文章阅读、分享等数据。
凭证来源（优先级）：
  1. 环境变量 WECHAT_APP_ID / WECHAT_APP_SECRET
  2. wenyan credential (~/.config/wenyan-md/credential.json)

Usage:
  python3 scripts/analytics.py                    # 最近7天汇总
  python3 scripts/analytics.py --days 30           # 最近30天
  python3 scripts/analytics.py --start 2026-06-01  # 指定起始日期
  python3 scripts/analytics.py --start 2026-06-01 --end 2026-07-04
  python3 scripts/analytics.py --json               # JSON 输出
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta


# ── 凭证加载 ──────────────────────────────────────────────

def load_credentials():
    """加载 AppID 和 AppSecret"""
    app_id = os.environ.get("WECHAT_APP_ID")
    app_secret = os.environ.get("WECHAT_APP_SECRET")

    if app_id and app_secret:
        return app_id, app_secret

    # 从 wenyan credential 读取
    cred_file = os.path.expanduser("~/.config/wenyan-md/credential.json")
    if os.path.exists(cred_file):
        try:
            with open(cred_file) as f:
                cred = json.load(f)
            wechat = cred.get("wechat", {})
            if wechat:
                app_id = list(wechat.keys())[0]
                app_secret = wechat[app_id].get("appSecret", "")
                return app_id, app_secret
        except Exception:
            pass

    print("❌ 未找到微信公众号凭证", file=sys.stderr)
    print("   请设置环境变量 WECHAT_APP_ID / WECHAT_APP_SECRET", file=sys.stderr)
    print("   或运行: wenyan credential --set", file=sys.stderr)
    sys.exit(1)


# ── Access Token ──────────────────────────────────────────

def get_access_token(app_id, app_secret):
    """获取微信 API access_token"""
    url = (
        f"https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"❌ 网络请求失败: {e}", file=sys.stderr)
        sys.exit(1)

    if "errcode" in data and data["errcode"] != 0:
        print(f"❌ 获取 access_token 失败: {data.get('errmsg', data)}", file=sys.stderr)
        sys.exit(1)

    return data["access_token"]


# ── API 调用 ──────────────────────────────────────────────

def call_datacube(access_token, endpoint, begin_date, end_date):
    """
    调用微信 datacube 接口
    endpoint: getarticletotal / getarticlesummary / getarticletrend /
              getuserread / getuserreadhour / getusershare / getusersharehour
    """
    url = f"https://api.weixin.qq.com/datacube/{endpoint}?access_token={access_token}"
    body = json.dumps({
        "begin_date": begin_date,
        "end_date": end_date,
    }).encode()

    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        result = {"errcode": e.code, "errmsg": error_body}
    except Exception as e:
        print(f"❌ 请求失败: {e}", file=sys.stderr)
        return None

    if "errcode" in result and result["errcode"] != 0:
        print(f"⚠️  {endpoint} 调用失败: {result.get('errmsg', result)}", file=sys.stderr)
        return None

    return result


# ── 数据格式化 ────────────────────────────────────────────

def format_article_total(data):
    """格式化 getarticletotal 返回的图文总阅读数据"""
    if not data or "list" not in data:
        return None

    list_data = data["list"]
    if not list_data:
        return None

    # 汇总
    total = {
        "source": "getarticletotal (图文总阅读)",
        "records": len(list_data),
        "int_page_read_user": sum(d.get("int_page_read_user", 0) for d in list_data),
        "int_page_read_count": sum(d.get("int_page_read_count", 0) for d in list_data),
        "ori_page_read_user": sum(d.get("ori_page_read_user", 0) for d in list_data),
        "ori_page_read_count": sum(d.get("ori_page_read_count", 0) for d in list_data),
        "share_user": sum(d.get("share_user", 0) for d in list_data),
        "share_count": sum(d.get("share_count", 0) for d in list_data),
        "add_to_fav_user": sum(d.get("add_to_fav_user", 0) for d in list_data),
        "add_to_fav_count": sum(d.get("add_to_fav_count", 0) for d in list_data),
        "daily": [],
    }

    for d in list_data:
        total["daily"].append({
            "date": d.get("ref_date", ""),
            "read_user": d.get("int_page_read_user", 0),
            "read_count": d.get("int_page_read_count", 0),
            "share_user": d.get("share_user", 0),
            "share_count": d.get("share_count", 0),
            "fav_user": d.get("add_to_fav_user", 0),
            "fav_count": d.get("add_to_fav_count", 0),
        })

    return total


def format_article_summary(data):
    """格式化 getarticlesummary 返回的图文群发数据"""
    if not data or "list" not in data:
        return None

    list_data = data["list"]
    if not list_data:
        return None

    total = {
        "source": "getarticlesummary (群发图文)",
        "records": len(list_data),
        "articles": [],
    }

    for d in list_data:
        # msgid 字段在汇总接口中有
        total["articles"].append({
            "date": d.get("ref_date", ""),
            "msgid": d.get("msgid", ""),
            "title": d.get("title", ""),
            "int_page_read_user": d.get("int_page_read_user", 0),
            "int_page_read_count": d.get("int_page_read_count", 0),
            "ori_page_read_user": d.get("ori_page_read_user", 0),
            "ori_page_read_count": d.get("ori_page_read_count", 0),
            "share_user": d.get("share_user", 0),
            "share_count": d.get("share_count", 0),
            "add_to_fav_user": d.get("add_to_fav_user", 0),
            "add_to_fav_count": d.get("add_to_fav_count", 0),
            "int_page_from_session_read_user": d.get("int_page_from_session_read_user", 0),
            "int_page_from_hist_msg_read_user": d.get("int_page_from_hist_msg_read_user", 0),
            "int_page_from_feed_read_user": d.get("int_page_from_feed_read_user", 0),
            "int_page_from_friends_read_user": d.get("int_page_from_friends_read_user", 0),
            "int_page_from_other_read_user": d.get("int_page_from_other_read_user", 0),
            "feed_share_from_session_user": d.get("feed_share_from_session_user", 0),
            "feed_share_from_feed_user": d.get("feed_share_from_feed_user", 0),
            "feed_share_from_other_user": d.get("feed_share_from_other_user", 0),
        })

    return total


def format_user_read(data):
    """格式化 getuserread 返回的用户阅读数据"""
    if not data or "list" not in data:
        return None

    list_data = data["list"]
    if not list_data:
        return None

    total = {
        "source": "getuserread (用户阅读来源)",
        "records": len(list_data),
        "int_page_read_user": sum(d.get("int_page_read_user", 0) for d in list_data),
        "int_page_from_session_read_user": sum(d.get("int_page_from_session_read_user", 0) for d in list_data),
        "int_page_from_hist_msg_read_user": sum(d.get("int_page_from_hist_msg_read_user", 0) for d in list_data),
        "int_page_from_feed_read_user": sum(d.get("int_page_from_feed_read_user", 0) for d in list_data),
        "int_page_from_friends_read_user": sum(d.get("int_page_from_friends_read_user", 0) for d in list_data),
        "int_page_from_other_read_user": sum(d.get("int_page_from_other_read_user", 0) for d in list_data),
    }
    return total


def format_user_share(data):
    """格式化 getusershare 返回的用户分享数据"""
    if not data or "list" not in data:
        return None

    list_data = data["list"]
    if not list_data:
        return None

    total = {
        "source": "getusershare (用户分享)",
        "records": len(list_data),
    }
    for d in list_data:
        # 每人每天分享次数分段
        for seg in d.get("list", []):
            key = f"share_{seg.get('share_scene', 'unknown')}"
            total[key + "_user"] = total.get(key + "_user", 0) + seg.get("user_count", 0)
            total[key + "_count"] = total.get(key + "_count", 0) + seg.get("share_count", 0)

    return total


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="微信公众号数据统计分析"
    )
    parser.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD（覆盖 --days）")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--all", action="store_true", help="获取所有可用指标（多接口）")
    args = parser.parse_args()

    # 日期范围
    end_date = args.end or datetime.now().strftime("%Y-%m-%d")
    if args.start:
        begin_date = args.start
    else:
        begin_date = (datetime.now() - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")

    # 凭证
    app_id, app_secret = load_credentials()
    token = get_access_token(app_id, app_secret)

    if not args.json:
        print(f"\n📊 微信公众号数据统计")
        print(f"   时间范围: {begin_date} → {end_date}")
        print(f"   ——\n")

    results = {}

    # 1. 图文总阅读（聚合）
    data = call_datacube(token, "getarticletotal", begin_date, end_date)
    formatted = format_article_total(data)
    if formatted:
        results["article_total"] = formatted

    if args.all:
        # 2. 群发图文按日
        data = call_datacube(token, "getarticlesummary", begin_date, end_date)
        formatted = format_article_summary(data)
        if formatted:
            results["article_summary"] = formatted

        # 3. 用户阅读来源
        data = call_datacube(token, "getuserread", begin_date, end_date)
        formatted = format_user_read(data)
        if formatted:
            results["user_read"] = formatted

        # 4. 用户分享
        data = call_datacube(token, "getusershare", begin_date, end_date)
        formatted = format_user_share(data)
        if formatted:
            results["user_share"] = formatted

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_results(results, begin_date, end_date)


def print_results(results, begin_date, end_date):
    """人类友好格式输出"""

    # ── 图文总阅读 ──
    at = results.get("article_total")
    if at:
        print("━" * 50)
        print("📖 图文总阅读（汇总）")
        print("━" * 50)
        print(f"  统计天数:  {at['records']}")
        print(f"  阅读人数:  {at['int_page_read_user']:,}")
        print(f"  阅读次数:  {at['int_page_read_count']:,}")
        print(f"  分享人数:  {at['share_user']:,}")
        print(f"  分享次数:  {at['share_count']:,}")
        print(f"  收藏人数:  {at['add_to_fav_user']:,}")
        print(f"  收藏次数:  {at['add_to_fav_count']:,}")
        if at.get("daily"):
            print(f"\n  每日明细:")
            print(f"  {'日期':<12} {'阅读人数':>8} {'阅读次数':>8} {'分享':>6} {'收藏':>6}")
            print(f"  {'─'*12} {'─'*8} {'─'*8} {'─'*6} {'─'*6}")
            for d in at["daily"]:
                print(f"  {d['date']:<12} {d['read_user']:>8,} {d['read_count']:>8,} {d['share_user']:>6,} {d['fav_user']:>6,}")
        print()

    # ── 群发图文按日 ──
    s = results.get("article_summary")
    if s and s.get("articles"):
        print("━" * 50)
        print("📰 群发图文明细")
        print("━" * 50)
        for a in s["articles"]:
            print(f"  📅 {a['date']}  「{a['title']}」")
            print(f"     msgid: {a['msgid']}")
            print(f"     阅读: {a['int_page_read_user']:,} 人 / {a['int_page_read_count']:,} 次")
            print(f"     分享: {a['share_user']:,} 人 / {a['share_count']:,} 次")
            print(f"     收藏: {a['add_to_fav_user']:,} 人 / {a['add_to_fav_count']:,} 次")
            if a.get("int_page_from_session_read_user"):
                print(f"     来源 → 公众号会话: {a['int_page_from_session_read_user']:,}")
            if a.get("int_page_from_friends_read_user"):
                print(f"     来源 → 朋友圈:     {a['int_page_from_friends_read_user']:,}")
            if a.get("int_page_from_feed_read_user"):
                print(f"     来源 → 看一看:     {a['int_page_from_feed_read_user']:,}")
            if a.get("int_page_from_hist_msg_read_user"):
                print(f"     来源 → 历史消息:   {a['int_page_from_hist_msg_read_user']:,}")
            print()
        print()

    # ── 用户阅读来源 ──
    ur = results.get("user_read")
    if ur:
        print("━" * 50)
        print("👥 用户阅读来源分布")
        print("━" * 50)
        print(f"  阅读总人数:        {ur['int_page_read_user']:,}")
        print(f"  　├ 公众号会话:     {ur['int_page_from_session_read_user']:,}")
        print(f"  　├ 历史消息:       {ur['int_page_from_hist_msg_read_user']:,}")
        print(f"  　├ 看一看:         {ur['int_page_from_feed_read_user']:,}")
        print(f"  　├ 朋友圈:         {ur['int_page_from_friends_read_user']:,}")
        print(f"  　└ 其他:           {ur['int_page_from_other_read_user']:,}")
        print()

    # ── 用户分享 ──
    us = results.get("user_share")
    if us:
        print("━" * 50)
        print("🔗 用户分享来源")
        print("━" * 50)
        for key, val in us.items():
            if key not in ("source", "records") and "_user" in key:
                scene = key.replace("_user", "")
                count = us.get(scene + "_count", 0)
                print(f"  {scene}: {val:,} 人 / {count:,} 次")
        print()

    if not results:
        print("⚠️  所选时间范围内暂无数据。")
        print("   注意：微信 datacube 数据有 1-2 天延迟，且仅保留近 90 天。")


if __name__ == "__main__":
    main()
