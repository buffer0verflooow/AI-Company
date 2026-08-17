#!/usr/bin/env python3
"""Hermes-independent swarm executor.

Implements the same stdin/stdout contract as ``swarm_hermes_executor.py`` so
``swarm_runner.py`` can use it as ``--executor-command`` without changing the
worker loop:

    stdin:
        {"task": {...}, "context": "...", "model_profile": {...}}
    stdout:
        JSON dict understood by ``SwarmWorker.normalize_executor_result``.

Backends:
    * ``simulate`` (default): deterministic local worker used for simulation.
    * ``command``: delegate to any external LLM/agent CLI.  Configure with
      ``SWARM_NATIVE_AGENT_COMMAND`` and ``SWARM_NATIVE_BACKEND=command``.

The point is to prove that the company swarm's executor slot is not coupled to
Hermes.  Production can point ``SWARM_NATIVE_AGENT_COMMAND`` at OpenCode ACP,
Codex CLI, or another agent runtime.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from typing import Any

try:
    from ._safe_io import scrub_environment
except ImportError:  # direct script execution
    from _safe_io import scrub_environment


def _payload_error(message: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "capture": False,
    }


def _normalize_backend_output(raw: str, task: dict[str, Any]) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {
            "success": True,
            "content": "",
            "capture": False,
            "token_cost": 0,
        }
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = text
    if isinstance(data, dict):
        return data
    return {
        "success": True,
        "content": text,
        "capture": bool(text),
        "token_cost": 0,
    }


def _simulate(task: dict[str, Any], context: str, profile: dict[str, Any]) -> dict[str, Any]:
    role = str(task.get("required_role") or task.get("task_type") or "custom")
    task_type = str(task.get("task_type") or "analyze")
    reason = str(task.get("reason") or "")

    samples = {
        "scan": "模拟扫描完成：发现 /api、/docs、/health 三个可枚举入口；服务指纹已记录。",
        "analyze": "模拟分析完成：已核查输入边界、数据流与信任边界，暂未形成可提交漏洞。",
        "exploit": "模拟利用完成：在授权范围内验证了输入路径，未执行破坏性动作。",
        "report": "模拟报告完成：输出摘要、证据来源、不确定性与后续建议。",
        "research": "模拟研究完成：已整理多来源事实并标注置信度，给出分维度结论。",
    }
    content = samples.get(task_type, samples["analyze"])
    if reason:
        content = f"{content}\n任务理由: {reason}"

    return {
        "success": True,
        "content": content,
        "capture": True,
        "token_cost": 32,
        "result_summary": {
            "content": content[:500],
            "worker_agent": f"native-{role}",
            "worker_role": role,
            "model_profile": profile or {},
            "backend": "simulate",
        },
        "metadata": {
            "executor": "swarm_native_executor",
            "backend": "simulate",
        },
    }


def _run_command_backend(payload: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    command = os.getenv("SWARM_NATIVE_AGENT_COMMAND", "").strip()
    if not command:
        return _payload_error(
            "SWARM_NATIVE_BACKEND=command requires SWARM_NATIVE_AGENT_COMMAND"
        )
    argv = shlex.split(command)
    env, _dropped = scrub_environment()
    env["SWARM_AGENT_EXEC"] = "1"
    try:
        proc = subprocess.run(
            argv,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=1800,
            env=env,
            check=False,
        )
    except Exception as exc:
        return _payload_error(str(exc))
    if proc.returncode != 0:
        return _payload_error(proc.stderr.strip() or f"agent command exited {proc.returncode}")
    return _normalize_backend_output(proc.stdout, task)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(json.dumps(_payload_error(f"invalid executor input: {exc}"), ensure_ascii=False))
        return 0

    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    context = str(payload.get("context") or "")
    profile = payload.get("model_profile") if isinstance(payload.get("model_profile"), dict) else {}

    backend = os.getenv("SWARM_NATIVE_BACKEND", "simulate").strip().lower()
    if backend == "command":
        result = _run_command_backend(payload, task)
    elif backend == "simulate":
        result = _simulate(task, context, profile)
    else:
        result = _payload_error(f"unknown SWARM_NATIVE_BACKEND: {backend}")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
