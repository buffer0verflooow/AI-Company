#!/usr/bin/env python3
"""Execute one delegated company, article, or video job through an isolated Hermes turn."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._safe_io import (
        locked_append_text,
        locked_atomic_write_text,
        read_text_limited,
        scrub_environment,
        sqlite_connection,
    )
except ImportError:  # direct script execution
    from _safe_io import (
        locked_append_text,
        locked_atomic_write_text,
        read_text_limited,
        scrub_environment,
        sqlite_connection,
    )


WORKSPACE = Path("/home/pwn/workspace")
COMPANY = WORKSPACE / "company"
HERMES_DB = Path("/home/pwn/.hermes/state.db")
INTERNAL_WORKER_PREFIX = "[COMPANY_WORKER_INTERNAL]"
CONTENT_ONLY_TOOLSETS = ("file", "web", "image_gen", "vision")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_status(job_dir: Path, payload: dict[str, Any]) -> None:
    payload = {**payload, "updated_at": utc_now()}
    locked_atomic_write_text(
        job_dir / "status.json",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def write_progress(
    job_dir: Path, stage: str, *, percent: int | None = None, detail: str = ""
) -> None:
    """Record a coarse sub-task progress marker for observers of this job.

    Unlike status.json (terminal state for delivery), progress.json is a cheap
    heartbeat of *where* a long-running job is: stage label, optional percent,
    and a timestamp. Workers may overwrite it with finer-grained stages.
    """
    payload: dict[str, Any] = {"stage": stage, "updated_at": utc_now()}
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
    if detail:
        payload["detail"] = detail
    with suppress(OSError):
        # Progress is advisory; never let a write failure abort the job.
        locked_atomic_write_text(
            job_dir / "progress.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )


def append_event(
    job_dir: Path,
    event: str,
    *,
    detail: str = "",
    state: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one line to events.jsonl (append-only job event log).

    Event log is the observable history of a content job: stage transitions,
    QA results, publish/archive actions, retry/termination reasons. It is
    append-only and survives progress.json/status.json overwrites.
    """
    record: dict[str, Any] = {
        "ts": utc_now(),
        "event": event,
        "state": state,
    }
    if detail:
        record["detail"] = detail
    if payload:
        record.update(payload)
    with suppress(OSError):
        locked_append_text(
            job_dir / "events.jsonl",
            json.dumps(record, ensure_ascii=False),
        )


# --- content-job state machine ---------------------------------------------
# Lifecycle states for a content job (extends executor's terminal states with
# the human-in-the-loop stages that happen AFTER the worker finishes):
#   pending        — request written, not yet run by executor
#   running        — worker executing
#   qa             — worker finished, QA gates being checked (executor writes
#                    "completed" in status.json; qa state is derived from
#                    qa-report.md presence — see job_state.py)
#   review         — human review in progress (main agent / user)
#   published      — pushed to WeChat draft box / published
#   archived       — closed out, kept for reference
#   retrying       — transient failure, scheduled for re-run
#   terminated     — unrecoverable failure, no re-run planned
JOB_STATES = ("pending", "running", "qa", "review", "published", "archived",
              "retrying", "terminated")

# Human-in-the-loop transitions handled by automation/content_job_state.py
# (invoked by main agent / push scripts), not by the worker executor:
#   running   → review       (worker completed, awaiting human)
#   review    → published    (draft pushed to WeChat)
#   review    → archived     (closed without publishing)
#   running   → retrying     (transient failure, re-run planned)
#   running   → terminated   (unrecoverable failure)


def pixelle_runtime_ready() -> bool:
    project = COMPANY / "projects" / "Pixelle-Video"
    return bool(shutil.which("uv") and (project / "config.yaml").exists())


def worker_usage(job_dir: Path, hermes_db: Path = HERMES_DB) -> dict[str, Any]:
    """Resolve the isolated Hermes session and its measured token counters."""
    if not hermes_db.is_file():
        return {}
    try:
        with sqlite_connection(hermes_db, read_only=True) as db:
            row = db.execute(
                """SELECT s.id,s.model,s.input_tokens,s.output_tokens,s.cache_read_tokens,
                          s.cache_write_tokens,s.reasoning_tokens,s.tool_call_count,
                          s.estimated_cost_usd,s.actual_cost_usd,s.cost_status
                   FROM messages m JOIN sessions s ON s.id=m.session_id
                   WHERE m.role='user' AND m.content LIKE ?
                   ORDER BY m.timestamp DESC LIMIT 1""",
                (f"%产物目录：{job_dir}%",),
            ).fetchone()
            return dict(row) if row else {}
    except (OSError, sqlite3.Error):
        return {}


def build_prompt(request: dict[str, Any], job_dir: Path) -> tuple[str, list[str]]:
    route = str(request.get("route") or "")
    message = str(request.get("message") or "")
    if route == "article":
        expected = ["draft.md", "draft-humanized.md", "qa-report.md"]
        prompt = f"""{INTERNAL_WORKER_PREFIX}
你是公司文章创作分享产线的隔离 Worker。直接完成任务，不要只写计划。

用户任务：{message}
产物目录：{job_dir}

必须先读取并遵循：
- {COMPANY / 'operations/business-lines/article-production.md'}
- {COMPANY / 'marketing/article-quality-constraints.md'}（质量约束规范 C1-C9：深度/文体/去AI味/标题/长度/事实/封面/排版/格式一致性——draft 阶段强制执行，QA Gate 逐项核对；**若任务是翻译类文章**——选题为英文一手研究、素材含原文 URL、任务消息含「翻译」——额外执行 C10 翻译约束：忠实原文/结构跟随/术语保留/长段拆短/路径代码化/列表化/图表保留/免责声明保留/来源标注）
- 与选题相关的公司 Wiki、现有文章、跟踪表和源材料

强制交付（按顺序）：
1. `{job_dir / 'draft.md'}`：完整的技术文章初稿。写完初稿后**不要**继续——先停下来，做下一步。
2. **去 AI 味**：humanizer 技能已由 Runner 预加载，严格按 34 条模式逐一检查 draft.md，然后生成 `{job_dir / 'draft-humanized.md'}`。要求：
   - 彻底去掉 AI 词汇（"值得注意的是"、"此外"、"总而言之"、"至关重要"、"在当今时代"等）
   - 去掉教科书结构（"挑战与展望"、"综上所述"等章节模板）
   - 去掉 emoji 标题（🚀💡✅ 等）
   - 变节奏：短句和长句交替，不要每段一样长
   - 加人味：有观点、有态度、有第一人称（"我"），不要中立播报腔
   - 标题风格参考：用具体数字+反转/反差，不要教科书式标题
   - 去掉所有 AI 填充段落（"随着…的发展"、"为…奠定了基础"等开头）
   **文章结构禁令（对标 #07 已验证风格，必须遵守）**：
   - 禁止元数据框：不许出现 "📌这篇文章聊什么"、"⏱️预计阅读"、"🛠️运行环境"、"📖本系列基于"
   - 禁止编号章节：不许用 "01｜" "02｜" 等数字前缀
   - 禁止 "## 结语"、"## 🔥"、"## 总结"、"📚系列下一篇"
   - 禁止教学序词："下面让我们..."、"首先..."、"接下来..."、"值得注意的是"
   - 标题必须有钩子+具体事实，不能是教科书式陈述
3. **生成封面**：如果当前工具集中提供 `image_generate`，在 `{job_dir / 'cover.jpg'}` 生成封面；否则明确记录封面未生成。此隔离 Worker 不提供 terminal/execute_code，不得通过 shell 绕过任务目录限制。
4. `{job_dir / 'qa-report.md'}`：Gate 1 事实核查、Gate 2 内容审校、Gate 3 主编终审，逐项给证据和结论。**QA 对象是 draft-humanized.md**，不是 draft.md。
5. **微信预览检查（新 Gate 4）**：生成 `{job_dir / 'draft-formatted.md'}` 后，转为 HTML 预览文件 `{job_dir / 'wechat-preview.html'}`，然后逐项检查：
   - CSS 样式整体注入到 `<body>` 元素的 inline `style=` 属性中
   - CSS 属性值中没有未转义的双引号（`font-family: "xxx"` → 会截断 HTML style 属性）
   - 代码块使用 `<pre>` 标签且 `white-space: pre` 或 `white-space: pre-wrap`
   - 所有图片有正确的 `src` 和 `alt` 属性
   - 无破损的嵌套标签/未闭合标签
   - 以上检查结果写入 qa-report.md 的「Gate 4 微信预览检查」章节，附 HTML 片段证据
6. 若任务包含「公众号/排版」，生成 `{job_dir / 'draft-formatted.md'}` 用于微信推送。**关键**：必须在正文顶部内联 `<style>` 代码块，CSS 模板见 `{COMPANY / 'projects/wechat-publisher/assets/wechat-article.css'}`。要求：
   - 代码框用 ```python 围栏（不能用缩进），`white-space: pre`
   - 公式单独占行，前后留空行，不混在正文
   - `<style>` 写在 YAML frontmatter 之后、正文之前
   - 不使用 emoji 标题、不使用 `word-break: break-all`

约束：
- 需要外部资料时，若当前隔离环境提供 agentkey_search 能力则优先使用；否则使用当前工具集中的 web_search/web_extract。不得臆造不可用工具或结果。
- 不执行公众号推送、草稿箱写入或公开发布；这些外部动作必须人工审批。
- 不编造链接、数据、测试或已完成动作；无法核验的内容明确标注。
- 不修改公司已有正式文章，只在本任务产物目录写文件。
- 只能使用当前隔离工具集；不得调用 terminal、process、execute_code 或 delegate_task。
- 可选：在关键阶段把进度写入 `{job_dir / 'progress.json'}`（字段 stage、percent、updated_at），便于观察长任务进度，不计入交付物。
- 最终回复只总结完成情况、质量门结果、产物绝对路径和仍需人工决定的事项。
"""
        return prompt, expected

    if route == "company":
        expected = ["task-report.md", "result.json"]
        prompt = f"""{INTERNAL_WORKER_PREFIX}
你是公司的隔离执行 Worker。你的职责是完成被委派的实际工作，而不是继续与用户讨论或只给建议。

用户任务：{message}
任务 Run：{request.get('run_id')}
产物目录：{job_dir}

工作方式：
1. 先读取 {COMPANY / 'Home.md'}、{COMPANY / 'operations/agent-roster.md'} 以及与任务直接相关的 Wiki、项目说明、代码和测试。
2. 判断任务属于产品、工程、市场、运营、财务或知识管理，并以对应职能负责人身份执行。
3. 在用户任务明确授权的范围内，直接修改 /home/pwn/workspace/company 下的相关文件、代码或文档，并运行与风险相称的验证。
4. 保留工作区中与本任务无关的现有改动；不得覆盖或回滚用户改动。

强制交付：
1. `{job_dir / 'task-report.md'}`：写明任务理解、实际完成的修改、验证证据、遗留风险和下一步。
2. `{job_dir / 'result.json'}`：严格 JSON 对象，字段为 `status`（completed/needs_approval/failed）、`department`、`summary`、`changed_files`（数组）、`tests`（数组）、`next_action`。

边界：
- 需要外部资料时，若当前隔离环境提供 agentkey_search 能力则优先使用；否则使用当前工具集中的 web_search/web_extract。不得臆造不可用工具或结果。
- 不执行公开发布、上传、付款、外部消息、删除数据、不可逆操作或未明确授权的外部安全测试。
- 如任务必须越过边界，将 status 设为 needs_approval，生成 `{job_dir / 'approval-request.md'}` 后停止。
- 不编造完成结果、测试、数据或文件修改；无法验证的内容明确标注。
- 可选：在关键阶段把进度写入 `{job_dir / 'progress.json'}`（字段 stage、percent、updated_at），便于观察长任务进度。
- 最终回复只总结完成状态、验证结果、改动文件和产物绝对路径。
"""
        return prompt, expected

    expected = ["video-script.md", "storyboard.md", "production-plan.md"]
    runtime = "可用" if pixelle_runtime_ready() else "未激活（缺少 uv 或正式 config.yaml）"
    prompt = f"""{INTERNAL_WORKER_PREFIX}
你是公司视频创作产线的隔离 Worker。直接完成可在当前环境完成的生产任务，不要只写计划。

用户任务：{message}
产物目录：{job_dir}
Pixelle-Video 运行时：{runtime}

必须先读取并遵循：
- {COMPANY / 'operations/business-lines/video-production.md'}
- {COMPANY / 'strategy/video-production-strategy.md'}
- {COMPANY / 'projects/Pixelle-Video/README.md'}
- 与选题相关的公司文章、Wiki 和视觉规范

强制交付：
1. `{job_dir / 'video-script.md'}`：完整口播/讲解稿，含开场钩子、主体、结尾 CTA。
2. `{job_dir / 'storyboard.md'}`：逐镜头分镜，含时间、旁白、画面、字幕、素材来源/待制作项。
3. `{job_dir / 'production-plan.md'}`：制作参数、Pixelle/FFmpeg/TTS 接入计划、成本/风险、验收清单。
4. 只有 Pixelle 运行时和所需凭证均真实可用时才能生成 MP4；否则在 production-plan 中写明阻断项，严禁声称视频已渲染。

约束：
- 不上传 B站、小红书、抖音或任何外部平台；上传/发布必须人工审批。
- 不下载未授权素材，不编造生成结果。
- 不修改公司已有正式内容，只在本任务产物目录写文件。
- 此隔离 Worker 不提供 terminal/execute_code；只交付文案、分镜和制作计划，不声称已渲染 MP4。
- 可选：在关键阶段把进度写入 `{job_dir / 'progress.json'}`（字段 stage、percent、updated_at），便于观察长任务进度。
- 最终回复只总结完成情况、产物绝对路径、渲染状态和仍需人工决定的事项。
"""
    return prompt, expected


def build_worker_invocation(
    request: dict[str, Any],
    job_dir: Path,
    prompt: str,
) -> tuple[list[str], Path, dict[str, str]]:
    """Build a least-privilege Hermes invocation for one content job.

    Article/video workers only need file, web, skill, and image tools.  In
    particular, omitting the terminal/process/code-execution toolsets closes
    the route by which a prompt-injected content worker previously edited the
    main repository with shell commands.  Company execution is intentionally
    different: it is an explicitly authorized code-changing worker and is
    confined to the company repository as its working/safe root.
    """
    route = str(request.get("route") or "")
    if route not in {"company", "article", "video"}:
        raise ValueError(f"unsupported content route: {route!r}")

    job_dir = Path(job_dir).resolve()
    if route in {"article", "video"}:
        cwd = job_dir
        safe_root = job_dir
    else:
        cwd = COMPANY
        safe_root = COMPANY

    command = [
        "hermes", "chat", "-q", prompt, "-Q",
        "--source", "tool", "--max-turns", "60", "--pass-session-id",
    ]
    if route in {"article", "video"}:
        command.extend(["--toolsets", ",".join(CONTENT_ONLY_TOOLSETS)])
    if route == "article":
        # Preload the read-only humanizer skill instead of exposing the
        # ``skills`` toolset (which also contains global skill mutation APIs).
        command.extend(["--skills", "humanizer"])

    env, _dropped = scrub_environment()
    # The router hook is global and runs inside this process too.  These two
    # markers make the ownership boundary explicit even on older Hermes builds
    # that do not persist a session source in the hook envelope.
    env["COMPANY_ROUTER_BYPASS"] = "1"
    env["HERMES_SESSION_SOURCE"] = "tool"
    env["HERMES_WRITE_SAFE_ROOT"] = str(safe_root)
    env["TERMINAL_CWD"] = str(cwd)
    return command, cwd, env


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a delegated company execution or content-production job")
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    job_dir = Path(args.job_dir).resolve()
    request_path = job_dir / "request.json"
    try:
        request = json.loads(read_text_limited(request_path, max_bytes=2 * 1024 * 1024))
        if not isinstance(request, dict):
            raise ValueError("request root must be an object")
        if request.get("route") not in {"company", "article", "video"}:
            raise ValueError(f"unsupported content route: {request.get('route')!r}")
    except Exception as exc:
        write_status(job_dir, {"status": "failed", "error": f"invalid request: {exc}", "artifacts": []})
        write_progress(job_dir, "failed", percent=100, detail="invalid request")
        append_event(job_dir, "terminated", state="terminated",
                     detail=f"invalid request: {exc}")
        return 0

    prompt, expected = build_prompt(request, job_dir)
    write_status(job_dir, {
        "status": "running",
        "route": request["route"],
        "run_id": request.get("run_id"),
        "started_at": utc_now(),
        "artifacts": [],
    })
    write_progress(job_dir, "worker_running", percent=10, detail=str(request["route"]))
    append_event(job_dir, "started", state="running",
                 detail=f"worker launch ({request['route']})",
                 payload={"run_id": request.get("run_id")})
    command, worker_cwd, env = build_worker_invocation(request, job_dir, prompt)
    try:
        proc = subprocess.run(
            command,
            cwd=str(worker_cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except Exception as exc:
        write_status(job_dir, {
            "status": "failed",
            "route": request["route"],
            "run_id": request.get("run_id"),
            "error": str(exc),
            "artifacts": [],
        })
        write_progress(job_dir, "failed", percent=100, detail="worker launch failed")
        append_event(job_dir, "terminated", state="terminated",
                     detail=f"worker launch failed: {exc}")
        return 0

    write_progress(job_dir, "post_processing", percent=80)
    append_event(job_dir, "worker_finished", state="qa",
                 detail=f"worker exited {proc.returncode}")
    try:
        artifacts = [
            str(path) for path in sorted(job_dir.iterdir())
            if not path.is_symlink() and path.is_file()
            and path.name not in {"request.json", "status.json", "status.json.tmp", "executor.log", "progress.json", "progress.json.tmp"}
        ]
    except OSError as exc:
        write_status(job_dir, {
            "status": "failed", "route": request["route"],
            "run_id": request.get("run_id"), "error": f"artifact scan failed: {exc}",
            "artifacts": [],
        })
        write_progress(job_dir, "failed", percent=100, detail="artifact scan failed")
        return 0
    usage = worker_usage(job_dir)
    missing = [name for name in expected if not (job_dir / name).is_file()]
    content = proc.stdout.strip()[-12000:]
    error = proc.stderr.strip()[-4000:]
    worker_result: dict[str, Any] = {}
    if request["route"] == "company" and not missing:
        try:
            value = json.loads(read_text_limited(job_dir / "result.json", max_bytes=2 * 1024 * 1024))
            if not isinstance(value, dict):
                raise ValueError("result.json must contain an object")
            worker_result = value
            if str(value.get("status") or "") not in {"completed", "needs_approval", "failed"}:
                raise ValueError("result.json status must be completed, needs_approval, or failed")
        except Exception as exc:
            missing.append(f"valid result.json ({exc})")
    if proc.returncode != 0 or missing:
        reasons = []
        if proc.returncode != 0:
            reasons.append(error or f"Hermes exited {proc.returncode}")
        if missing:
            reasons.append(f"missing required artifacts: {', '.join(missing)}")
        write_status(job_dir, {
            "status": "failed",
            "route": request["route"],
            "run_id": request.get("run_id"),
            "result": content,
            "error": "; ".join(reasons),
            "artifacts": artifacts,
            "worker_session_id": str(usage.get("id") or ""),
            "usage": usage,
        })
        write_progress(job_dir, "failed", percent=100, detail="; ".join(reasons)[:200])
        append_event(job_dir, "terminated", state="terminated",
                     detail="; ".join(reasons)[:300])
        return 0

    if request["route"] == "company":
        worker_status = str(worker_result["status"])
        summary = str(worker_result.get("summary") or content or "公司执行任务已完成。")
        if worker_status == "failed":
            write_status(job_dir, {
                "status": "failed",
                "route": request["route"],
                "run_id": request.get("run_id"),
                "result": summary,
                "error": summary,
                "artifacts": artifacts,
                "worker_session_id": str(usage.get("id") or ""),
                "usage": usage,
            })
            write_progress(job_dir, "failed", percent=100, detail="worker reported failed")
            append_event(job_dir, "terminated", state="terminated",
                         detail=f"worker reported failed: {summary[:200]}")
            return 0
        write_status(job_dir, {
            "status": worker_status,
            "route": request["route"],
            "run_id": request.get("run_id"),
            "result": summary,
            "worker_result": worker_result,
            "artifacts": artifacts,
            "worker_session_id": str(usage.get("id") or ""),
            "usage": usage,
            "completed_at": utc_now(),
            "error": "",
        })
        write_progress(job_dir, worker_status, percent=100)
        append_event(job_dir, "completed", state="review",
                     detail=f"company job {worker_status}",
                     payload={"artifacts": len(artifacts)})
        return 0

    write_status(job_dir, {
        "status": "completed",
        "route": request["route"],
        "run_id": request.get("run_id"),
        "result": content or "内容产线任务已完成。",
        "artifacts": artifacts,
        "worker_session_id": str(usage.get("id") or ""),
        "usage": usage,
        "completed_at": utc_now(),
        "error": "",
    })
    write_progress(job_dir, "completed", percent=100)
    append_event(job_dir, "completed", state="review",
                 detail="content job completed, awaiting human review",
                 payload={"artifacts": len(artifacts)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
