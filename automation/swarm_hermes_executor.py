#!/usr/bin/env python3
"""Execute one swarm market task through an isolated Hermes one-shot turn."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

try:
    from ._safe_io import scrub_environment
except ImportError:  # direct script execution
    from _safe_io import scrub_environment


WORKSPACE = "/home/pwn/workspace"
INTERNAL_WORKER_PREFIX = "[COMPANY_WORKER_INTERNAL]"


def _safe_counter(value: Any) -> int:
    """Coerce a JSON event counter to int, degrading to 0 on malformed values.

    The opencode JSON event stream is external, untrusted input; a corrupt
    tokens.total must not crash the executor mid-run.
    """
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def build_prompt(payload: dict[str, Any]) -> str:
    task = payload.get("task") or {}
    context = str(payload.get("context") or "")
    profile = payload.get("model_profile") or {}
    reason = str(task.get("reason") or task.get("task_type") or "")
    return f"""{INTERNAL_WORKER_PREFIX}
你是公司安全探索产品线的蜂群 Worker。

任务角色：{task.get('required_role', 'analyst')}
任务类型：{task.get('task_type', 'analyze')}
任务说明：{reason}
模型画像：{json.dumps(profile, ensure_ascii=False)}

共享上下文：
{context}

执行约束：
1. 只处理任务说明中明确授权的目标与范围，不扩大 Scope。
2. 未明确授权的外部主动探测必须停止并报告缺少授权。
3. 需要外部资料时优先使用 agentkey skill 搜索（`execute_tool(name="agentkey_search", params={{"query": "...", "num": 5}})`），其次才是内置 web_search/web_extract。
4. 优先读取本机已有文件、知识库和证据；不要重复已经完成的测试。
5. 对任何“已写文件、已验证、已发现”声明给出可核验依据。
6. 不执行外部发布、HackerOne 提交、付款、删除或不可逆操作。
7. 最终直接返回结构化结果，不要只描述计划。
"""


def _run_opencode(profile: dict, prompt: str, env: dict) -> dict:
    """免费池执行引擎: opencode run --format json --model <free-model>。

    模型对照表 (migration 020): tier='free' 的任务由 opencode 调用
    ZenMux / OpenCode Zen 免费模型。输出解析:
      - 文本事件拼接为 content
      - step_finish 事件的 tokens.total 上报为 token_cost (尽力)
    """
    model = profile.get("resolved_model") or profile.get("model") or ""
    if not model:
        return {"success": False, "error": "free model missing resolved_model", "capture": False}
    cmd = ["opencode", "run", "--format", "json", "--model", model, prompt]
    try:
        proc = subprocess.run(
            cmd,
            cwd=WORKSPACE,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 -- worker crash -> clean JSON failure
        return {"success": False, "error": str(exc), "capture": False}

    if proc.returncode != 0:
        return {
            "success": False,
            "error": proc.stderr.strip() or f"opencode exited {proc.returncode}",
            "capture": False,
        }

    content_parts: list[str] = []
    token_cost = 0
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type", "")
        if etype == "text":
            part = event.get("part") or {}
            if part.get("type") == "text":
                content_parts.append(str(part.get("text") or ""))
        elif etype == "step_finish":
            tokens = event.get("tokens") or {}
            token_cost = _safe_counter(tokens.get("total"))

    content = "\n".join(content_parts).strip()
    return {
        "success": True,
        "content": content,
        "capture": bool(content),
        "token_cost": token_cost,
        "metadata": {
            "executor": "opencode",
            "model": model,
            "tier": "free",
            "router_bypass": True,
        },
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001 -- invalid executor input -> clean JSON failure
        print(json.dumps({"success": False, "error": f"invalid executor input: {exc}", "capture": False}))
        return 0

    prompt = build_prompt(payload)
    env, _dropped = scrub_environment()
    env["COMPANY_ROUTER_BYPASS"] = "1"
    env["HERMES_SESSION_SOURCE"] = "tool"
    env["HERMES_WRITE_SAFE_ROOT"] = WORKSPACE
    env["TERMINAL_CWD"] = WORKSPACE
    # 蜂群 agent 执行环境门（审计 A2）：capture.py --force-capture 仅接受该标记，
    # 防止任意本机进程伪造 agent 身份强制入库
    env["SWARM_AGENT_EXEC"] = "1"

    profile = payload.get("model_profile") or {}
    # 模型对照表分流 (migration 020): tier='free' → opencode 免费池,
    # 其余 → hermes chat (付费, 现状)。
    if (profile.get("tier") == "free") or (profile.get("engine") == "opencode"):
        result = _run_opencode(profile, prompt, env)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    cmd = [
        "hermes", "chat", "-q", prompt, "-Q",
        "--source", "tool", "--max-turns", "40",
        "--pass-session-id",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=WORKSPACE,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 -- worker crash -> clean JSON failure
        print(json.dumps({"success": False, "error": str(exc), "capture": False}, ensure_ascii=False))
        return 0

    if proc.returncode != 0:
        print(json.dumps({
            "success": False,
            "error": proc.stderr.strip() or f"Hermes exited {proc.returncode}",
            "capture": False,
        }, ensure_ascii=False))
        return 0

    content = proc.stdout.strip()
    print(json.dumps({
        "success": True,
        "content": content,
        "capture": bool(content),
        "metadata": {"executor": "hermes", "router_bypass": True},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
