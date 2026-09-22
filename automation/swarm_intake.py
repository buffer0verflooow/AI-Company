#!/usr/bin/env python3
"""选题候选 → 素材/订单**准备器**(BG-02 / D2;**手动工具,不自动下发**)。

> **用户裁决(2026-09-22)**:**不需要自动下发市场雷达任务**。因此本模块
> **没有**定时器、**不**自动投递、**不会**被任何 cron 调用 —— 它只做"把候选选题
> 变成可人工审阅的素材 + 订单草稿",投递与否由人决定。自动 tick 曾拟交付,
> 已按此裁决撤除(未安装、未建 job)。

**为什么要它**:市场雷达每天往 `market_signals` 写信号(实测 957 条,932 clean),
而蜂群 v2 的**唯一人写入口**是 `inbox/` ⇒ 需求侧零转化(BG-02 根因:
"机制再全也是空转")。本桥把**高分 clean 信号**变成一张 content 单。

**流程(逐条可复跑)**:

1. 选候选:`market_signals` 里 `content_risk='clean'` ∧ `total_score ≥ --min-score`
   ∧ 近 `--lookback-days` 天首见 ∧ **未投递过**(state 文件);按分数降序取
   `--max-per-run` 条(默认 1 ⇒ 一天最多一张单,与实测候选量 ~1 条/1-2 天匹配)。
2. 取素材:复用 `security_intel.fetch` + `strip_html` 抓来源正文(实测正文字符数不足
   `MIN_FETCHED_CHARS` ⇒ 回退信号自带的长 `snippet`);两者都过薄 ⇒ **跳过该信号**
   (宁缺勿滥:不投"内容单薄"的空单,原因入 state,不静默丢)。
3. 落素材:写 `<swarm_repo>/materials/<slug>.md`(与 m10x 单同形:先备素材、再派单)。
4. 落订单:写 `<swarm_repo>/inbox-intake/<run_id>.json`(`tmp + rename` 原子落盘)。
   **`inbox-intake/` 没有任何定时器在扫** ⇒ 写到那里**不会**被执行;人审阅并决定
   投递时,手工跑一次:
   `swarmctl inbox poll --inbox-dir inbox-intake --by inbox-relay --spawn-worker
    --worker-agent content-writer-1 --worker-judge content-judge-1
    --worker-permission write`
   (人写 `inbox/` 的既有链路**不受影响**,也不擅自打开 W18 的 `--spawn-worker` 默认)。
   质量门规范 `marketing/content-quality-gates.md` **全文内联**进 instruction ——
   蜂群运行时无网络、读不到公司仓库;并按 D3 **显式声明 5 件产物**。

**纪律**:默认 dry-run(`--apply` 才写候选/素材);**不自动下发**(无 cron);
幂等 = signal_id 进 state ⇒ 同一信号不重复准备
(`run_id` = `intake-<signal 后 12 位>-<UTC 日>`;被日顶拒发时 run 会占位为 `cancelled`,
故带日期才能次日重投不撞 id);
单张单的预算/est 取自 `docs/INBOX-ENTRY-POLICY.md` 的实测派生表。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
COMPANY = HERE.parent
sys.path.insert(0, str(HERE))

from security_intel import fetch, strip_html  # noqa: E402  (复用既有抓取/清洗单一来源)

#: 实测派生(见 swarm-knowledge/docs/INBOX-ENTRY-POLICY.md 预算表):content 12 轮 × 20k
DEFAULT_TOKEN_BUDGET = 240_000
DEFAULT_EST = 40_000
DEFAULT_MIN_SCORE = 60.0          # total_score 量纲 0–100(实测 max 85)
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_MAX_PER_RUN = 1
MIN_FETCHED_CHARS = 1500          # 抓到的正文低于此 ⇒ 回退 snippet
MIN_SNIPPET_CHARS = 600           # snippet 也低于此 ⇒ 跳过该信号(宁缺勿滥)
MAX_MATERIAL_CHARS = 60_000       # 素材上限(避免把超长页面整篇塞进任务书)

#: 机器 feed 子目录(与人写 `inbox/` **分离**:机器单要 spawn worker 执行,人写单保持现状)
INTAKE_SUBDIR = "inbox-intake"
#: 质量门规范(单一来源;内联进订单 instruction)
QUALITY_SPEC = COMPANY / "marketing" / "content-quality-gates.md"
#: 公众号单的强制产物(CV2:含排版/预览;D3 要求显式声明)
DELIVERABLES = ["draft.md", "draft-humanized.md", "qa-report.md",
                "draft-formatted.md", "wechat-preview.html"]
_SLUG = re.compile(r"[^0-9A-Za-z_-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or (HERE / "router_config.json")
    return json.loads(Path(cfg_path).read_text(encoding="utf-8"))


def signals_db(config: dict[str, Any]) -> Path:
    return Path(config.get("market_signals_db")
                or (COMPANY / "marketing" / "market_signals.db"))


def state_path(config: dict[str, Any]) -> Path:
    return Path(config.get("operations_db") or "").parent / "swarm_intake_state.json" \
        if config.get("operations_db") else COMPANY / "operations" / "runtime" / "swarm_intake_state.json"


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def select_candidates(con: sqlite3.Connection, *, min_score: float,
                      lookback_days: int, limit: int) -> list[dict[str, Any]]:
    """高分 clean 信号(按分数降序);`seen` 过滤由调用方按 state 做。"""
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT signal_id, theme, theme_title, title, url, snippet, channel,"
        " source_domain, total_score, content_risk, first_seen_at"
        " FROM market_signals"
        " WHERE content_risk='clean' AND total_score >= ?"
        "   AND first_seen_at >= datetime('now', ?)"
        " ORDER BY total_score DESC, first_seen_at DESC LIMIT ?",
        (float(min_score), f"-{int(lookback_days)} day", int(limit))).fetchall()
    return [dict(r) for r in rows]


def material_text(signal: dict[str, Any]) -> tuple[str, str]:
    """返回 `(素材正文, 来源说明)`;抓不到/过薄则回退 snippet;两者都薄 ⇒ `("", 原因)`。"""
    url = str(signal.get("url") or "")
    if url:
        try:
            body = strip_html(fetch(url))
        except Exception as exc:                      # noqa: BLE001 —— 抓取失败不是致命错
            body = ""
            reason = f"fetch 失败({type(exc).__name__})"
        else:
            reason = ""
            if len(body) >= MIN_FETCHED_CHARS:
                return body[:MAX_MATERIAL_CHARS], f"正文抓取({url};{len(body)} 字)"
    else:
        reason = "无 url"
    snippet = str(signal.get("snippet") or "").strip()
    if len(snippet) >= MIN_SNIPPET_CHARS:
        return snippet[:MAX_MATERIAL_CHARS], f"信号摘要回退({len(snippet)} 字;{reason})"
    return "", (f"素材过薄(正文 {reason or '不足'} ∧ snippet {len(snippet)} 字 < "
                f"{MIN_SNIPPET_CHARS})")


def build_instruction(signal: dict[str, Any], *, material_rel: str,
                      source_note: str, spec_text: str) -> str:
    """订单的任务书(**自包含**:素材位置 + 来源 + 质量门规范全文 + 硬规则)。"""
    return "\n".join([
        "你是公司内容产线的执行体(蜂群内建 agent 运行时承载)。直接完成任务,不要只写计划。",
        "",
        f"选题(来自市场雷达信号 {signal.get('signal_id')}):{signal.get('theme_title')}",
        f"素材标题:{signal.get('title')}",
        f"来源:{signal.get('url')}",
        f"素材已备好(先读它,再动笔):{material_rel}(相对仓库根;{source_note})",
        "",
        "任务:基于素材写一篇**中文公众号技术文章**(不是翻译、不是摘要):",
        "- 一篇一个核心观点,讲透;有观点、有态度、有第一人称,不要中立播报腔;",
        "- 引用来源时保留原文出处链接与作者(如素材里有);",
        "- 素材没写到的数据/结论一律不得编造;无法核验处标注「未获取」;",
        "- 无网络:不要尝试访问 URL,素材文件就是你能拿到的全部外部资料。",
        "",
        "强制交付(按顺序,全部落在仓库根内):",
        "  1) draft.md —— 完整初稿(写完先停,不要顺手做下一步)",
        "  2) draft-humanized.md —— 按去 AI 味规范逐条处理 draft.md",
        "  3) qa-report.md —— Gate 1/2/3 逐项给证据与结论(QA 对象是 draft-humanized.md)",
        "  4) draft-formatted.md —— 微信排版稿(正文顶部内联 style,代码块用 ```python 围栏)",
        "  5) wechat-preview.html —— 微信预览 HTML,并做 Gate 4 检查(结果写进 qa-report.md)",
        "",
        "硬规则:",
        "- 长文用 `fs.append` **分段追加**(不存在则新建);单次 fs.write 有输出上限,"
        "整篇一次写会被截断;同一个文件不要与 fs.write 混用;",
        "- 第一次写动作前先把素材读完;不要反复探查目录;",
        "- 产物必须留在仓库根内;不执行任何推送/发布动作。",
        "",
        "必须遵循的质量门规范(全文):",
        spec_text,
    ])


def build_order(signal: dict[str, Any], *, instruction: str,
                token_budget: int = DEFAULT_TOKEN_BUDGET,
                est: int = DEFAULT_EST) -> dict[str, Any]:
    sid = str(signal.get("signal_id") or "")
    # run_id 含 UTC 日期:被日顶拒发 ⇒ run 已 `cancelled` 占位,同日不重投;
    # 次日若手工清 state 重投,日期不同 ⇒ 不会撞已存在的 run_id(幂等且可重试)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    run_id = f"intake-{sid[-12:]}-{day}" if sid else f"intake-{day}-{_now()}"
    return {
        "run_type": "content",
        "task_type": "report",
        "title": f"[选题] {signal.get('theme_title')}:{str(signal.get('title'))[:60]}",
        "run_id": run_id,
        "intent": "custom",
        "target_type": "unknown",
        "target": str(signal.get("url") or signal.get("title") or ""),
        "token_budget": int(token_budget),
        "est": int(est),
        "base": 10,
        "focus_params": {
            "instruction": instruction,
            "deliverables": list(DELIVERABLES),        # D3:显式声明(缺键会被拒发)
        },
    }


def material_rel(signal: dict[str, Any]) -> str:
    """素材在蜂群仓库内的相对路径(确定性 ⇒ dry-run 与 --apply 显示一致)。"""
    slug = _SLUG.sub("-", f"{signal.get('theme') or 'theme'}-{signal.get('signal_id')}").strip("-")
    return f"materials/{slug[:80]}.md"


def _write_material(swarm_repo: Path, signal: dict[str, Any], text: str) -> str:
    rel = material_rel(signal)
    dest = swarm_repo / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join([
        f"# {signal.get('title')}",
        "",
        f"- 来源:{signal.get('url')}",
        f"- 信号:{signal.get('signal_id')} / 主题:{signal.get('theme_title')}",
        f"- 抓取时间:{_now()}",
        "",
        "---",
        "",
        text,
    ])
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(dest)
    return rel


def _write_order(inbox_dir: Path, run_id: str, order: dict[str, Any]) -> str:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    dest = inbox_dir / f"{_SLUG.sub('-', run_id)}.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(order, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dest)                                  # 原子落盘:轮询不会读到半截
    return dest.name


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="swarm_intake",
        description="选题 → 蜂群 inbox 自动投递桥(默认 dry-run;--apply 才写盘)")
    ap.add_argument("--apply", action="store_true", help="真写素材/订单/state(默认只预演)")
    ap.add_argument("--max-per-run", type=int, default=DEFAULT_MAX_PER_RUN,
                    help=f"单次最多投几张单(默认 {DEFAULT_MAX_PER_RUN})")
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                    help=f"信号分下限(0–100;默认 {DEFAULT_MIN_SCORE})")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    ap.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    ap.add_argument("--est", type=int, default=DEFAULT_EST)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    swarm_repo = Path(config.get("swarm_repo") or "")
    if not swarm_repo.is_dir():
        print(f"swarm_repo 不可用:{swarm_repo}", file=sys.stderr)
        return 2
    inbox_dir = swarm_repo / INTAKE_SUBDIR
    sp = state_path(config)
    state = load_state(sp)
    spec_text = QUALITY_SPEC.read_text(encoding="utf-8") if QUALITY_SPEC.is_file() else \
        "(质量门规范文件不可读;按通用质量要求执行并在 answer 中说明)"

    con = sqlite3.connect(str(signals_db(config)))
    try:
        candidates = select_candidates(con, min_score=args.min_score,
                                       lookback_days=args.lookback_days,
                                       limit=max(1, args.max_per_run) * 3)
    finally:
        con.close()

    out: dict[str, Any] = {"dry_run": not args.apply, "state": str(sp),
                           "candidates": len(candidates), "dispatched": [],
                           "skipped": [], "already_seen": []}
    for signal in candidates:
        if len(out["dispatched"]) >= max(1, args.max_per_run):
            break
        sid = str(signal.get("signal_id"))
        if state.get(sid, {}).get("status") == "dispatched":
            out["already_seen"].append(sid)
            continue
        text, note = material_text(signal)
        if not text:
            state[sid] = {"status": "skipped", "reason": note, "at": _now()}
            out["skipped"].append({"signal_id": sid, "reason": note})
            continue
        rel = material_rel(signal)
        instruction = build_instruction(signal, material_rel=rel, source_note=note,
                                        spec_text=spec_text)
        order = build_order(signal, instruction=instruction,
                            token_budget=args.token_budget, est=args.est)
        if not args.apply:
            out["dispatched"].append({"signal_id": sid, "run_id": order["run_id"],
                                      "score": signal.get("total_score"),
                                      "source_note": note, "order": order})
            continue
        written = _write_material(swarm_repo, signal, text)
        assert written == rel, (written, rel)     # 预览路径 = 实际落盘路径(单一来源)
        name = _write_order(inbox_dir, order["run_id"], order)
        state[sid] = {"status": "dispatched", "run_id": order["run_id"],
                      "material": rel, "order_file": name, "at": _now()}
        out["dispatched"].append({"signal_id": sid, "run_id": order["run_id"],
                                  "order_file": name, "material": rel,
                                  "score": signal.get("total_score"),
                                  "source_note": note})
    if args.apply:
        save_state(sp, state)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    else:
        mode = "dry-run " if not args.apply else ""
        print(f"swarm_intake {mode}完成:候选 {out['candidates']} 条,"
              f"投递 {len(out['dispatched'])} 条,跳过 {len(out['skipped'])} 条,"
              f"已投过 {len(out['already_seen'])} 条")
        for d in out["dispatched"]:
            print(f"  [投递] {d['order_file'] if 'order_file' in d else d['run_id']}"
                  f" ← {d['signal_id']}(score {d['score']};{d['source_note']})")
        for s in out["skipped"]:
            print(f"  [跳过] {s['signal_id']}:{s['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
