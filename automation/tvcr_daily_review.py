#!/usr/bin/env python3
"""Run the company's daily TVCR operating review through an isolated agent."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict

try:
    from . import pricing
    from .operations_control import (
        DEFAULT_TIMEZONE,
        auto_approve_proposals,
        backfill_outcomes,
        business_period,
        create_review,
        escalate_stale_proposals,
        import_proposals,
        latest_origin,
        previous_business_day,
        runs_for_period,
        sync_operational_runs,
        update_review,
        utc_now,
    )
except ImportError:
    import pricing  # type: ignore[no-redef]
    from operations_control import (
        DEFAULT_TIMEZONE,
        auto_approve_proposals,
        backfill_outcomes,
        business_period,
        create_review,
        escalate_stale_proposals,
        import_proposals,
        latest_origin,
        previous_business_day,
        runs_for_period,
        sync_operational_runs,
        update_review,
        utc_now,
    )


HERE = Path(__file__).resolve().parent
COMPANY_ROOT = HERE.parent
DEFAULT_CONFIG = HERE / "operations_control_config.json"
TVCR_INTERNAL_PREFIX = "[COMPANY_TVCR_INTERNAL]"


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("operations control config must be a JSON object")
    return value


def _safe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def build_evidence_pack(
    runs: list[Dict[str, Any]],
    *,
    review_day: date,
    period_start: str,
    period_end: str,
    thresholds: Dict[str, Any],
) -> Dict[str, Any]:
    """Aggregate operating evidence while keeping observations separate from decisions."""
    lines: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    signals: list[Dict[str, Any]] = []
    evidence_runs: list[Dict[str, Any]] = []
    for run in runs:
        product_line = str(run.get("product_line") or "unknown")
        direct_tokens = int(run.get("input_tokens") or 0) + int(run.get("output_tokens") or 0) + int(run.get("reasoning_tokens") or 0)
        item = {
            "run_id": run.get("run_id"),
            "product_line": product_line,
            "request_excerpt": str(run.get("request_text") or "")[:500],
            "status": run.get("status"),
            "result_delivered": int(run.get("result_delivered") or 0),
            "proactive_delivered": int(run.get("proactive_delivered") or 0),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "duration_seconds": run.get("duration_seconds"),
            "worker_session_id": run.get("worker_session_id"),
            "model": run.get("model"),
            "direct_tokens": direct_tokens,
            "input_tokens": int(run.get("input_tokens") or 0),
            "output_tokens": int(run.get("output_tokens") or 0),
            "cache_read_tokens": int(run.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(run.get("cache_write_tokens") or 0),
            "reasoning_tokens": int(run.get("reasoning_tokens") or 0),
            "tool_call_count": int(run.get("tool_call_count") or 0),
            "actual_cost_usd": run.get("actual_cost_usd"),
            "estimated_cost_usd": run.get("estimated_cost_usd"),
            "estimated_cost_native": run.get("estimated_cost_native"),
            "estimated_cost_currency": run.get("estimated_cost_currency"),
            "cost_status": run.get("cost_status"),
            "output_bytes": int(run.get("output_bytes") or 0),
            "quality_status": run.get("quality_status"),
            "outcome_status": run.get("outcome_status"),
            "accepted": run.get("accepted"),
            "published": run.get("published"),
            "value_score": run.get("value_score"),
            "human_minutes": run.get("human_minutes"),
            "reach": run.get("reach"),
            "revenue_amount": run.get("revenue_amount"),
            "artifacts": _safe_json(run.get("artifacts_json") or "[]"),
            "evidence": _safe_json(run.get("evidence_json") or "{}"),
        }
        evidence_runs.append(item)
        lines[product_line].append(item)
        limits = thresholds.get(product_line) if isinstance(thresholds.get(product_line), dict) else {}
        for metric, actual in (
            ("direct_tokens", direct_tokens),
            ("cache_read_tokens", item["cache_read_tokens"]),
            ("tool_calls", item["tool_call_count"]),
            ("duration_seconds", item["duration_seconds"] or 0),
        ):
            threshold = float(limits.get(metric) or 0)
            if threshold and float(actual) >= threshold:
                signals.append({
                    "kind": "resource_threshold_exceeded",
                    "run_id": item["run_id"],
                    "product_line": product_line,
                    "metric": metric,
                    "actual": actual,
                    "threshold": threshold,
                    "interpretation_rule": "仅为异常信号，必须结合业务价值判断，不能直接推导为代码问题。",
                })
        if item["status"] == "completed" and item["outcome_status"] == "unmeasured":
            signals.append({
                "kind": "business_outcome_missing",
                "run_id": item["run_id"],
                "product_line": product_line,
                "interpretation_rule": "缺少采用、发布、触达或收入数据时，不能声称投入产出为正或为负。",
            })

    summaries: Dict[str, Any] = {}
    for product_line, items in lines.items():
        completed = [item for item in items if item["status"] == "completed"]
        measured = [item for item in items if item["outcome_status"] != "unmeasured"]
        line_cost = pricing.cost_rollup(items)
        summaries[product_line] = {
            "runs": len(items),
            "completed": len(completed),
            "failed": sum(1 for item in items if item["status"] == "failed"),
            "direct_tokens": sum(item["direct_tokens"] for item in items),
            "cache_read_tokens": sum(item["cache_read_tokens"] for item in items),
            "tool_calls": sum(item["tool_call_count"] for item in items),
            "output_bytes": sum(item["output_bytes"] for item in items),
            # Cost is now a real signal: provider-confirmed + estimated (tokens×price),
            # with the still-unpriced token volume kept explicit so ROI is never
            # computed against a hidden $0 basis.
            "confirmed_cost_usd": line_cost["confirmed_cost_usd"],
            "estimated_cost_usd": line_cost["estimated_cost_usd"],
            "estimated_cost_native": line_cost["estimated_cost_native"],
            "unpriced_runs": line_cost["unpriced_runs"],
            "unpriced_token_volume": line_cost["unpriced_token_volume"],
            "business_outcomes_measured": len(measured),
            "accepted": sum(1 for item in measured if item["accepted"] == 1),
            "published": sum(1 for item in measured if item["published"] == 1),
            "reach": sum(int(item["reach"] or 0) for item in measured),
            "revenue_amounts_by_currency": {},
        }

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "review_day": review_day.isoformat(),
        "period_start": period_start,
        "period_end": period_end,
        "cost_rollup": pricing.cost_rollup(evidence_runs),
        "evidence_policy": [
            "技术运行记录是经营分析证据，不是修改代码的指令。",
            "高 Token、高耗时本身不是问题；必须与采用、发布、触达、收入、战略价值和机会成本共同判断。",
            "未知价格不得按零成本计算，缺失业务结果不得推断 ROI。",
            "技术结果已回传只代表交付成功，不代表用户采用；业务结果未记录也不代表价值为零。",
            "改进选项必须先覆盖业务、产品、流程和资源层，再判断是否需要技术实现。",
        ],
        "line_summaries": summaries,
        "signals": signals,
        "runs": evidence_runs,
    }


def build_prompt(review_id: str, evidence_path: Path, report_path: Path, proposals_path: Path) -> str:
    return f"""{TVCR_INTERNAL_PREFIX}
你是公司独立的 TVCR 经营评估 Agent，职责类似经营分析委员会，而不是代码审查员。

复盘 ID：{review_id}
证据包：{evidence_path}
经营报告：{report_path}
结构化提案：{proposals_path}

必须执行：
1. 读取证据包、公司业务产线文档和必要的战略资料。
2. 从真实公司运营角度分析 Time、Value、Cost、Risk；明确区分事实、缺失数据和假设。
3. 高 Token 或高耗时只能作为信号，先判断业务价值；方案必须先覆盖业务、产品、流程和资源层，再考虑供应商或模型调整，最后才是代码/配置实现。
4. 将精简中文经营复盘写入 `{report_path}`：正文不超过 1200 个中文字符，只保留“结论、关键事实、风险/数据缺口、建议”四部分；不复述任务背景，不堆运行明细，不重复同一证据。
5. 将需要用户决策的事项写入 `{proposals_path}`，必须是合法 JSON：

{{
  "executive_summary": "不超过60字的一句话经营结论",
  "proposals": [
    {{
      "product_line": "article-production|video-production|security-exploration|company",
      "priority": "P0|P1|P2",
      "title": "经营改进事项",
      "problem_statement": "有证据的问题，不把技术现象当根因",
      "business_impact": "对价值、成本、时间或风险的影响",
      "root_cause_hypotheses": ["需要验证的原因"],
      "options": [
        {{"scope": "business|product|process|resource|technology", "action": "选项", "expected_value": "预期", "cost": "投入", "risk": "风险"}}
      ],
      "recommended_action": "建议先做的运营实验，不得直接修改线上代码",
      "change_scopes": ["business", "process"],
      "expected_value": "预期经营收益",
      "expected_cost": "实验投入",
      "risk": "主要风险与护栏",
      "success_metrics": [{{"metric": "指标", "baseline": "当前基线或未知", "target": "目标", "window": "验证周期"}}],
      "evidence_run_ids": ["run_id"]
    }}
  ]
}}

强制边界：
- 不修改任何现有代码、Prompt、配置、SOP 或业务文档；本次只生成报告和待审批提案。
- 没有足够证据时 proposals 必须为空，并写明需要补采哪些经营数据。
- 不得把 estimated/forecast 当作实际成本或收入。
- outcome_status=unmeasured 时只能写“价值未知/尚未计量”，不得写“价值为 0”；result_delivered=1 时不得声称“没有到达用户”，但也不能据此声称已被采用。
- 公司文档中的历史指标必须标注快照日期，不能覆盖 evidence.json 中的本周期事实；时间必须使用证据包中的 generated_at，不得自行编造时区。
- 不得为了降低 Token 而牺牲未定义的业务质量；必须给出实验指标和停止条件。
- proposals 只保留最多 3 个最高优先级、确实需要用户决定的事项；title 不超过 18 字，recommended_action 不超过 60 字。
- 面向用户的文字先给结论，短句表达；技术运行明细留在 evidence.json，不复制进日报。
- 最终回复只说明报告与提案文件是否成功生成。
"""


def validate_outputs(evidence: Dict[str, Any], report_text: str, payload: Dict[str, Any]) -> list[str]:
    """Reject common business-accounting errors before a report can be delivered."""
    errors: list[str] = []
    proposals = payload.get("proposals")
    if not isinstance(proposals, list):
        return ["proposals must be an array"]
    combined = report_text + "\n" + json.dumps(payload, ensure_ascii=False)
    if all(str(run.get("outcome_status") or "unmeasured") == "unmeasured" for run in evidence.get("runs") or []):
        if re.search(r"(?:实际)?价值(?:就是|为|=)\s*(?:\$?0|零)", combined, re.I):
            errors.append("unmeasured business value was incorrectly stated as zero")
    if evidence.get("runs") and all(
        str(run.get("cost_status") or "unknown").lower()
        not in {"actual", "confirmed", "provider_reported", "billed"}
        for run in evidence.get("runs") or []
    ):
        if re.search(r"实际(?:模型)?成本\s*(?:为|是|=)\s*\$?0", combined, re.I):
            errors.append("unknown model cost was incorrectly stated as zero")
    if any(int(run.get("result_delivered") or 0) == 1 for run in evidence.get("runs") or []):
        if any(phrase in combined for phrase in ("没有一件到达用户", "没有任何产出到达用户", "全部没有到达用户")):
            errors.append("delivered technical results were incorrectly described as not reaching the user")
    known_run_ids = {str(run.get("run_id") or "") for run in evidence.get("runs") or []}
    for index, proposal in enumerate(proposals, 1):
        if not isinstance(proposal, dict):
            errors.append(f"proposal {index} must be an object")
            continue
        metrics = proposal.get("success_metrics")
        if not isinstance(metrics, list) or not metrics:
            errors.append(f"proposal {index} has no success metrics")
        scopes = proposal.get("change_scopes")
        if scopes == ["technology"] or scopes == ["code"]:
            errors.append(f"proposal {index} jumps directly to a technology-only change")
        evidence_ids = proposal.get("evidence_run_ids") or []
        unknown = [item for item in evidence_ids if str(item) not in known_run_ids]
        if unknown:
            errors.append(f"proposal {index} references unknown run ids: {unknown}")
    return errors


def run_daily_review(config: Dict[str, Any], review_day: date, *, invoke_agent: bool = True) -> Dict[str, Any]:
    db_path = Path(config["operations_db"])
    router_db = Path(config["router_db"])
    jobs = Path(config["content_job_dir"])
    hermes_db = Path(config["hermes_state_db"])
    sync = sync_operational_runs(db_path, router_db, jobs, hermes_db)
    # Close the outcome loop from evidence before analysing (never invents value).
    backfill = backfill_outcomes(db_path)
    period_start, period_end = business_period(review_day, str(config.get("timezone") or DEFAULT_TIMEZONE))
    runs = runs_for_period(db_path, period_start, period_end)
    review_dir = Path(config["review_root"]) / review_day.isoformat()
    review_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = review_dir / "evidence.json"
    report_path = review_dir / "tvcr-report.md"
    proposals_path = review_dir / "proposals.json"
    evidence = build_evidence_pack(
        runs,
        review_day=review_day,
        period_start=period_start,
        period_end=period_end,
        thresholds=config.get("token_warning_thresholds") or {},
    )
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    review_id = create_review(
        db_path,
        review_day=review_day,
        period_start=period_start,
        period_end=period_end,
        evidence_path=str(evidence_path),
        origin=latest_origin(router_db),
    )
    # A repaired/manual rerun replaces any earlier failed notification for the
    # same business day and must become deliverable again.
    update_review(
        db_path, review_id,
        delivered=0, delivery_attempts=0, delivery_error="", last_delivery_at="",
    )

    if not runs:
        report_path.write_text(
            f"# TVCR 每日经营复盘 {review_day.isoformat()}\n\n本周期没有可归集的产品线运行记录，未形成经营改进提案。\n",
            encoding="utf-8",
        )
        proposals_path.write_text(json.dumps({
            "executive_summary": "本周期没有可归集的产品线运行记录。",
            "proposals": [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        ids = import_proposals(db_path, review_id, json.loads(proposals_path.read_text(encoding="utf-8")))
        update_review(db_path, review_id, report_path=str(report_path))
        return {"review_id": review_id, "status": "no_action", "proposals": ids, "runs": 0, "sync": sync}

    if not invoke_agent:
        update_review(db_path, review_id, status="collecting", report_path=str(report_path))
        return {"review_id": review_id, "status": "evidence_ready", "runs": len(runs), "sync": sync, "evidence_path": str(evidence_path)}

    update_review(db_path, review_id, status="analyzing", error="")
    prompt = build_prompt(review_id, evidence_path, report_path, proposals_path)
    env = dict(os.environ)
    try:
        proc = subprocess.run(
            [
                str(config.get("hermes_executable") or "hermes"), "chat", "-q", prompt, "-Q",
                "--source", "tool", "--max-turns", str(int(config.get("tvcr_max_turns", 20))),
                "--pass-session-id",
            ],
            cwd=str(COMPANY_ROOT.parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except Exception as exc:
        update_review(db_path, review_id, status="failed", error=str(exc), report_path=str(report_path))
        return {"review_id": review_id, "status": "failed", "error": str(exc), "runs": len(runs), "sync": sync}

    if proc.returncode != 0:
        error = proc.stderr.strip()[-4000:] or proc.stdout.strip()[-4000:] or f"Hermes exited {proc.returncode}"
        update_review(db_path, review_id, status="failed", error=error, report_path=str(report_path))
        return {"review_id": review_id, "status": "failed", "error": error, "runs": len(runs), "sync": sync}
    if not report_path.is_file() or not proposals_path.is_file():
        missing = [str(path) for path in (report_path, proposals_path) if not path.is_file()]
        error = f"TVCR agent missing required artifacts: {', '.join(missing)}"
        update_review(db_path, review_id, status="failed", error=error, report_path=str(report_path))
        return {"review_id": review_id, "status": "failed", "error": error, "runs": len(runs), "sync": sync}
    try:
        proposal_payload = json.loads(proposals_path.read_text(encoding="utf-8"))
        if not isinstance(proposal_payload, dict):
            raise ValueError("proposals root must be an object")
        validation_errors = validate_outputs(
            evidence,
            report_path.read_text(encoding="utf-8"),
            proposal_payload,
        )
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        ids = import_proposals(db_path, review_id, proposal_payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        update_review(db_path, review_id, status="failed", error=f"invalid proposals: {exc}", report_path=str(report_path))
        return {"review_id": review_id, "status": "failed", "error": str(exc), "runs": len(runs), "sync": sync}
    update_review(db_path, review_id, report_path=str(report_path), error="")
    # Governance: expire superseded backlog / escalate stale P0s, then policy-approve
    # the narrow slice (P2, low-risk, scopes already sanctioned) so the user only
    # decides what genuinely needs a decision.
    escalation = escalate_stale_proposals(db_path)
    auto_approved = auto_approve_proposals(db_path)
    return {
        "review_id": review_id,
        "status": "pending_approval" if ids else "no_action",
        "proposals": ids,
        "runs": len(runs),
        "sync": sync,
        "backfill": backfill,
        "escalation": escalation,
        "auto_approved": auto_approved,
        "report_path": str(report_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily TVCR operating review")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--date", default="", help="Business date (YYYY-MM-DD); defaults to previous day")
    parser.add_argument("--evidence-only", action="store_true")
    args = parser.parse_args()
    config = load_config(Path(args.config))
    review_day = date.fromisoformat(args.date) if args.date else previous_business_day(str(config.get("timezone") or DEFAULT_TIMEZONE))
    result = run_daily_review(config, review_day, invoke_agent=not args.evidence_only)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
