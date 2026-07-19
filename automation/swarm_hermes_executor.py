#!/usr/bin/env python3
"""Execute one swarm market task through an isolated Hermes one-shot turn."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict


WORKSPACE = "/home/pwn/workspace"
INTERNAL_WORKER_PREFIX = "[COMPANY_WORKER_INTERNAL]"


def build_prompt(payload: Dict[str, Any]) -> str:
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
3. 优先读取本机已有文件、知识库和证据；不要重复已经完成的测试。
4. 对任何“已写文件、已验证、已发现”声明给出可核验依据。
5. 不执行外部发布、HackerOne 提交、付款、删除或不可逆操作。
6. 最终直接返回结构化结果，不要只描述计划。
"""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(json.dumps({"success": False, "error": f"invalid executor input: {exc}", "capture": False}))
        return 0

    prompt = build_prompt(payload)
    env = dict(os.environ)
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
    except Exception as exc:
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
