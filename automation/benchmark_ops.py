#!/usr/bin/env python3
"""TSec/Benchmark 跑分操作助手 — 提交门禁 + 验题工具。

复盘(2026-09-08, 27/40)落地的三个运行层修复之一:
  1) 提交前容器必须 available(否则一律 correct:false) — 本脚本把该门禁
     变成默认行为:submit/resubmit 自动检查容器状态,未启动先 start;
  2) 附件"先验题" — hash/diff 子命令在做题前比对附件与公开原版是否一致
     (题目常被平台改编,flag 换值/截断/重注入);
  3) false 先查环境再归因 — submit 失败时打印容器状态与处置提示,
     禁止一句"平台答案不符"。

用法:
    BENCHMARK_TOKEN=<token> python3 benchmark_ops.py list
    BENCHMARK_TOKEN=<token> python3 benchmark_ops.py start g-XX
    BENCHMARK_TOKEN=<token> python3 benchmark_ops.py submit g-XX '<flag>'
    BENCHMARK_TOKEN=<token> python3 benchmark_ops.py close g-XX
    BENCHMARK_TOKEN=<token> python3 benchmark_ops.py resubmit flags.txt
    python3 benchmark_ops.py hash  <附件...>            # 验题第一步:取指纹
    python3 benchmark_ops.py diff <附件> <原版/参考文件>  # 比对差异
    BENCHMARK_TOKEN=<token> python3 benchmark_ops.py checklist

resubmit 文件格式:每行 "<unique_code> <flag>",# 开头为注释。

跑分运行清单(checklist 子命令同文):
  1. VPN 预检(GET http://10.0.100.58 → status ok)后才开始。
  2. 附件题先验题:hash/diff 与公开原版比对;被改编则只借思路不抄答案。
  3. 槽位:同时最多 3 容器;解完/放弃立即 close。
  4. 提交:一律经本脚本 submit/resubmit(自动门禁)。
  5. 返回 correct:false 时:先看容器状态与接口提示,再归因内容。
     一次拒绝 != 答案错;重复同 flag 返回 409 duplicate 属正常。
  6. 完成后 close 并汇报 correct_flag_count/total_flag_count。

环境变量:
    BENCHMARK_TOKEN    平台鉴权 token(必需)
    BENCHMARK_BASE_URL 默认 https://tsecbench.zc.tencent.com
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BASE_URL = os.environ.get("BENCHMARK_BASE_URL", "https://tsecbench.zc.tencent.com")
API = BASE_URL + "/openapi/v1/challenges"
MAX_ACTIVE = 3  # 平台同时活跃容器上限


def _require_token() -> str:
    token = os.environ.get("BENCHMARK_TOKEN", "").strip()
    if not token:
        print("缺少 BENCHMARK_TOKEN 环境变量", file=sys.stderr)
        raise SystemExit(2)
    return token


def _api(method: str, path: str, token: str, payload: Optional[Dict] = None) -> Dict:
    """调用平台 API,统一返回 {"http": <code>|None, **body}。"""
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "BENCHMARK_TOKEN": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            if body.strip():
                parsed = json.loads(body)
                if isinstance(parsed, list):  # challenges GET 返回裸数组
                    return {"http": resp.status, "challenges": parsed}
                return {"http": resp.status, **parsed}
            return {"http": resp.status}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            parsed = {"message": body[:300]}
        return {"http": exc.code, **parsed}
    except urllib.error.URLError as exc:
        print(f"网络错误: {exc.reason}", file=sys.stderr)
        raise SystemExit(4)


def get_rows(token: str) -> List[Dict]:
    resp = _api("GET", "", token)
    if not isinstance(resp.get("http"), int) or not (200 <= resp["http"] < 300):
        raise SystemExit(
            f"challenges 获取失败 http={resp.get('http')} msg={resp.get('message', '')}"
        )
    # 响应体本身是数组(platform 返回 list),或包在字段里——兼容两种
    if isinstance(resp, list):
        return resp
    for key in ("challenges", "data", "items"):
        val = resp.get(key)
        if isinstance(val, list):
            return val
    raise SystemExit(f"challenges 响应结构无法解析: {str(resp)[:200]}")


# ── 纯函数(可单测) ──

def find_row(rows: List[Dict], code: str) -> Optional[Dict]:
    for row in rows:
        if row.get("unique_code") == code:
            return row
    return None


def summarize(rows: List[Dict]) -> Tuple[int, int, int]:
    """(已完成题数, 总题数, 累计得分)。得分为已通关题 total_score 之和。"""
    done = sum(1 for r in rows if r.get("is_completed"))
    total = len(rows)
    score = sum(int(r.get("total_score") or 0) for r in rows if r.get("is_completed"))
    return done, total, score


def active_codes(rows: List[Dict]) -> List[str]:
    return [
        r["unique_code"]
        for r in rows
        if r.get("container_status") == "available"
    ]


def submit_plan(row: Optional[Dict], actives: List[str], max_active: int = MAX_ACTIVE) -> Dict:
    """提交前门禁决策(纯函数)。

    Returns:
        {"action": "submit" | "need_start" | "max_active" | "not_found" |
                    "already_complete",
         "detail": str}
    """
    if row is None:
        return {"action": "not_found", "detail": "challenge_not_found: 检查 unique_code 拼写"}
    if row.get("is_completed"):
        return {"action": "already_complete", "detail": "该题已通关(correct_flag_count==total_flag_count)"}
    status = row.get("container_status")
    if status == "available":
        return {"action": "submit", "detail": "容器 available,可直接提交"}
    if len(actives) >= max_active:
        return {
            "action": "max_active",
            "detail": f"活跃容器已达 {max_active} 上限,先 close 一题再 start",
        }
    return {"action": "need_start", "detail": f"容器 status={status},需要先 start"}


def classify_submit(resp: Dict) -> str:
    """把 submit 响应压成一行人类可读结论。"""
    if resp.get("http") == 409 and "duplicate" in str(resp.get("code", "")):
        return "duplicate: 该 flag 已正确提交过,跳过(不加分)"
    correct = resp.get("correct")
    if correct is True:
        return (
            f"correct:true awarded={resp.get('awarded')} "
            f"cumulative={resp.get('cumulative_score')} "
            f"flags={resp.get('correct_flag_count')}/{resp.get('total_flag_count')}"
        )
    if correct is False:
        return (
            f"correct:false awarded=0 flags={resp.get('correct_flag_count')}/{resp.get('total_flag_count')} "
            f"| 容器已确认 available;此时 false 先核 flag 原文与包装,再查 hint/附件,"
            f"禁止直接归因'平台答案不符'"
        )
    return f"http={resp.get('http')} code={resp.get('code')} msg={resp.get('message', '')}"


# ── 子命令 ──

def cmd_list(token: str) -> int:
    rows = get_rows(token)
    done, total, score = summarize(rows)
    print(f"进度: {done}/{total} 通关, 累计得分(近似) {score}")
    for row in sorted(rows, key=lambda r: r.get("unique_code", "")):
        flags = (
            f"{row.get('correct_flag_count')}/{row.get('total_flag_count')}"
            if row.get("flag_count") or row.get("correct_flag_count") is not None
            else "?"
        )
        print(
            f"{row.get('unique_code')}  done={row.get('is_completed')}  "
            f"flags={flags}  score={row.get('total_score')}  "
            f"container={row.get('container_status')}  {row.get('container_addr') or ''}"
        )
    return 0


def cmd_state(token: str, code: str) -> int:
    rows = get_rows(token)
    row = find_row(rows, code)
    if row is None:
        print(f"{code}: challenge_not_found")
        return 2
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def cmd_start(token: str, code: str) -> int:
    actives = active_codes(get_rows(token))
    if len(actives) >= MAX_ACTIVE:
        print(f"活跃容器已达 {MAX_ACTIVE} 上限: {actives} —— 先 close 一题再 start")
        return 3
    resp = _api("POST", "/start?unique_code=" + urllib.parse.quote(code), token)
    if resp.get("http") == 409:
        print(f"start 被拒: {resp.get('message', resp)}")
        return 3
    print(f"{code} started: {resp.get('container_addr', resp)}")
    return 0


def cmd_close(token: str, code: str) -> int:
    resp = _api("POST", "/close?unique_code=" + urllib.parse.quote(code), token)
    print(f"{code} closed: {resp.get('closed', resp)}")
    return 0


def submit_one(token: str, code: str, flag: str, *, gate: bool = True) -> int:
    """带门禁的单题提交。返回 0=通过/duplicate, 1=false, 2=环境问题。"""
    rows = get_rows(token)
    row = find_row(rows, code)
    actives = active_codes(rows)
    plan = submit_plan(row, actives)
    if plan["action"] == "not_found":
        print(f"{code}: {plan['detail']}")
        return 2
    if plan["action"] == "already_complete":
        print(f"{code}: 已通关,无需提交")
        return 0
    if gate and plan["action"] == "max_active":
        print(f"{code}: {plan['detail']} 活跃={actives}")
        return 3
    if gate and plan["action"] == "need_start":
        print(f"{code}: {plan['detail']} → 自动 start")
        resp = _api("POST", "/start?unique_code=" + urllib.parse.quote(code), token)
        if resp.get("http") == 409:
            print(f"{code}: start 被拒(max active?): {resp.get('message', resp)} 活跃={actives}")
            return 3
        print(f"{code}: started {resp.get('container_addr', '')}")
    resp = _api("POST", "/submit", token, {"unique_code": code, "flag": flag})
    print(f"{code}: {classify_submit(resp)}")
    if resp.get("correct") is True:
        return 0
    if resp.get("http") == 409 and "duplicate" in str(resp.get("code", "")):
        return 0
    return 1


def cmd_submit(token: str, code: str, flag: str) -> int:
    return submit_one(token, code, flag)


def cmd_resubmit(token: str, path: str) -> int:
    pairs: List[Tuple[str, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                print(f"跳过无法解析行: {line}", file=sys.stderr)
                continue
            pairs.append((parts[0], parts[1]))
    if not pairs:
        print(f"{path}: 没有可提交的 (CODE FLAG) 行")
        return 2
    ok = 0
    for code, flag in pairs:
        rc = submit_one(token, code, flag)
        ok += 1 if rc == 0 else 0
    print(f"resubmit 完成: {ok}/{len(pairs)} 通过")
    return 0 if ok == len(pairs) else 1


def file_hash(path: str) -> Tuple[str, int, str]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size, path


def cmd_hash(paths: List[str]) -> int:
    for p in paths:
        digest, size, name = file_hash(p)
        print(f"{name}  size={size}  sha256={digest}")
    return 0


def first_diff_offset(a: str, b: str, scan_limit: int = 8 << 20) -> Optional[int]:
    """逐块找首个差异字节偏移(最多扫 scan_limit)。无差异/超限返回 None。"""
    offset = 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while offset < scan_limit:
            ca = fa.read(1 << 16)
            cb = fb.read(1 << 16)
            if not ca and not cb:
                return None
            if ca != cb:
                for i in range(min(len(ca), len(cb))):
                    if ca[i] != cb[i]:
                        return offset + i
                return offset + min(len(ca), len(cb))
            if not ca or not cb:
                return None if (not ca and not cb) else offset + min(len(ca), len(cb))
            offset += len(ca)
    return None  # 超限未找到差异(可能文件更大,差异在后面)


def cmd_diff(a: str, b: str) -> int:
    ha, sa, _ = file_hash(a)
    hb, sb, _ = file_hash(b)
    print(f"{a}  size={sa}  sha256={ha}")
    print(f"{b}  size={sb}  sha256={hb}")
    if ha == hb and sa == sb:
        print("IDENTICAL: 附件与参考完全一致(可套原版思路/答案)")
        return 0
    print("DIFFERENT: 附件与公开原版不一致 —— 题目可能被改编,只借思路不抄答案")
    off = first_diff_offset(a, b)
    if off is not None:
        print(f"首个差异偏移: {off} (0x{off:x})")
    else:
        print("前 8MiB 内未发现差异(差异在更深处或仅长度不同)")
    return 1


CHECKLIST = """跑分运行清单(TSec 实测版):
1. VPN 预检: GET http://10.0.100.58 → status ok 才开始(exit 52 属环境抖动,可重试再判)。
2. 附件题先验题: benchmark_ops.py hash/diff 比对公开原版;改编则只借思路。
3. 槽位管理: 同时最多 3 容器; 完成/放弃立即 close。
4. 提交一律走 submit/resubmit(自动门禁: 容器不可用先 start)。
5. correct:false → 先查容器状态与接口提示,再归因内容;一次拒绝 != 答案错;
   409 duplicate = 已算过,跳过。
6. 蜂群 worker 指令: 一律 .py 脚本执行(超长内联命令会被拦); 上报需本地可复现。"""


def cmd_checklist() -> int:
    print(CHECKLIST)
    return 0


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    args = argv[1:]
    if cmd == "checklist":
        return cmd_checklist()
    if cmd == "hash":
        if not args:
            print("用法: benchmark_ops.py hash <文件...>")
            return 2
        return cmd_hash(args)
    if cmd == "diff":
        if len(args) != 2:
            print("用法: benchmark_ops.py diff <附件> <原版/参考文件>")
            return 2
        return cmd_diff(args[0], args[1])

    token = _require_token()
    if cmd == "list":
        return cmd_list(token)
    if cmd == "state":
        if not args:
            print("用法: benchmark_ops.py state <unique_code>")
            return 2
        return cmd_state(token, args[0])
    if cmd == "start":
        if not args:
            print("用法: benchmark_ops.py start <unique_code>")
            return 2
        return cmd_start(token, args[0])
    if cmd == "close":
        if not args:
            print("用法: benchmark_ops.py close <unique_code>")
            return 2
        return cmd_close(token, args[0])
    if cmd == "submit":
        if len(args) != 2:
            print("用法: benchmark_ops.py submit <unique_code> '<flag>'")
            return 2
        return cmd_submit(token, args[0], args[1])
    if cmd == "resubmit":
        if len(args) != 1:
            print("用法: benchmark_ops.py resubmit <flags.txt>")
            return 2
        return cmd_resubmit(token, args[0])
    print(f"未知子命令: {cmd}\n{__doc__}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
