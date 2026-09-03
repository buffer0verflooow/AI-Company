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
    * ``llm`` (default): 自实现 LLM worker — 直接 HTTP 调 OpenAI-compatible
      /chat/completions (ZenMux / deepseek-official, 配置来自
      ~/.hermes/config.yaml custom_providers 或 model_profile), 零外部 CLI
      依赖 (不调 hermes/opencode/任何 agent 二进制)。
    * ``simulate``: deterministic local worker used for simulation.
    * ``command``: delegate to any external LLM/agent CLI.  Configure with
      ``SWARM_NATIVE_AGENT_COMMAND`` and ``SWARM_NATIVE_BACKEND=command``.

The point is to prove that the company swarm's executor slot is not coupled to
Hermes.  Production can point ``SWARM_NATIVE_AGENT_COMMAND`` at OpenCode ACP,
Codex CLI, or another agent runtime.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from ._safe_io import scrub_environment
except ImportError:  # direct script execution
    from _safe_io import scrub_environment

try:
    from .swarm_hermes_executor import build_prompt
except ImportError:  # direct script execution
    from swarm_hermes_executor import build_prompt

DEFAULT_BASE_URL = "https://zenmux.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
_ABSTRACT_TIERS = {"balanced", "reasoning", "writer", "fast", "careful", "client", "custom"}
# opencode 免费池模型只存在于外部 CLI 生态 (model_profiles 里 provider=opencode
# 的 tier=free 条目), 自实现 executor 无法调用 — 映射到 zenmux 免费池等价模型
# (z-ai/glm-5.3-free 2026-08-31 实测 404, 用 config.yaml 明确列出的免费模型)。
_OPCODE_FREE_MODEL_MAP = {
    "nemotron-3-ultra-free": "z-ai/glm-4.7-flash-free",
    "nemotron-3-ultra": "z-ai/glm-4.7-flash-free",
}

SWARM_REPO = os.environ.get("SWARM_REPO", "/home/pwn/workspace/research/swarm-knowledge")
MAX_TOOL_ROUNDS = 14

_AGENT_SYSTEM_PROMPT = """你是蜂群分析 agent。你有只读工具可通过 mcp_tool.py 调用, 用于真实执行分析 (APK 逆向等), 输出必须以证据为准, 禁止编造文件内容、命令输出或漏洞。

输出必须是单个合法 JSON 对象 (不要输出 JSON 以外的任何文本):
1) 需要调用工具时:
{"tool_call": {"server": "<服务器名>", "tool": "<工具名>", "args": {<参数对象>}}}
2) 分析完成时 (content 是最终交付文本, 可长可短, 引用工具输出的真实证据):
{"answer": "<最终分析结果>"}

规则:
- 每次只输出一个 JSON。工具结果会在下一轮以「工具结果: ...」回传。
- 用工具获取真实证据: 包信息/权限/manifest/文件列表/字符串检索/反编译源码 grep。
- 反编译 (jadx_decompile) 一次即可, 后续用 grep_sources 检索。
- 工具报错时换参数重试或换工具, 最多尝试 3 次。
- 无法获取证据的结论标注「推测」, 不要把推测写成已执行的事实。
- 3-6 次工具调用后必须收敛: 用 answer 输出完整分析报告, 不要无限调用工具。"""


def _tools_prompt_block() -> str:
    """静态注入 MCP 工具清单 (不 spawn 服务器)。"""
    try:
        sys.path.insert(0, SWARM_REPO)
        from src.swarm.mcp_client import registry_tool_prompt
        block = registry_tool_prompt(["apk"])
        return block or ""
    except Exception:  # noqa: BLE001 -- optional tool registry must not break the executor
        return ""


def _run_mcp_tool(server: str, tool: str, args: dict, timeout: int = 400) -> str:
    """调 mcp_tool.py 执行 MCP 工具, 返回工具输出文本 (证据)。"""
    argv = [
        sys.executable,
        str(Path(SWARM_REPO) / "scripts" / "mcp_tool.py"),
        "call", server, tool,
        "--args", json.dumps(args or {}, ensure_ascii=False),
    ]
    proc = subprocess.run(
        argv,
        cwd=SWARM_REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        return f"[工具执行失败 exit={proc.returncode}] {proc.stderr.strip() or proc.stdout.strip()}"
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return f"[工具输出非 JSON] {proc.stdout[:500]}"
    if not payload.get("success"):
        return f"[工具错误] {payload.get('error') or payload}"
    contents = payload.get("content") or []
    texts = [c.get("text", "") for c in contents if isinstance(c, dict)]
    return "\n".join(texts).strip() or f"[工具 {server}.{tool} 无输出]"


def _payload_error(message: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "capture": False,
    }


def _is_abstract_tier(model: str) -> bool:
    """client 抽象档位模型名 (balanced/reasoning/...) 无法直接调 API, 需解析为具体模型。"""
    return model.strip().lower() in _ABSTRACT_TIERS


def _resolve_llm_config(profile: dict[str, Any]) -> tuple[str, str, str]:
    """从 model_profile + ~/.hermes/config.yaml 解析 (base_url, api_key, model)。

    优先级:
      provider: model_profile.provider > zenmux > deepseek-official
      api_key:  model_profile.api_key > provider.api_key > ZENMUX_API_KEY env
      model:    model_profile.model (非抽象档位) > provider.model > SWARM_MODEL env > 默认
    """
    prov = str((profile or {}).get("provider") or "").strip().lower()
    p_model = str((profile or {}).get("model") or "").strip()

    providers: dict[str, dict[str, Any]] = {}
    config_path = Path.home() / ".hermes" / "config.yaml"
    try:
        import yaml  # optional dependency; may be absent in minimal envs
    except ImportError:
        yaml = None
    cfg: dict[str, Any] = {}
    if yaml is not None:
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            # Optional config: a missing/corrupt file must not break the
            # executor — fall back to env-based defaults.
            print(f"swarm_native_executor: ignore unreadable optional config {config_path}: {exc}", file=sys.stderr)
    if isinstance(cfg, dict):
        for p in cfg.get("custom_providers", []) or []:
            name = str(p.get("name") or "").strip().lower()
            if name:
                providers[name] = p

    def _pick_provider(prov: str) -> dict[str, Any]:
        # 精确/包含匹配 (config 里名字可能是 "Zenmux.ai"/"deepseek-official" 等)
        if prov:
            for name, p in providers.items():
                if prov in name or name in prov:
                    return p
        for name, p in providers.items():
            if "zenmux" in name:
                return p
        for name, p in providers.items():
            if "deepseek" in name:
                return p
        return {}

    chosen = _pick_provider(prov)

    base_url = str(
        profile.get("base_url")
        or chosen.get("base_url")
        or os.environ.get("ZENMUX_BASE_URL", DEFAULT_BASE_URL)
    ).rstrip("/")
    api_key = str(
        profile.get("api_key")
        or chosen.get("api_key")
        or os.environ.get("ZENMUX_API_KEY", "")
    )

    if p_model and not _is_abstract_tier(p_model):
        model = _OPCODE_FREE_MODEL_MAP.get(p_model.lower(), p_model)
    else:
        model = str(
            chosen.get("model")
            or os.environ.get("SWARM_MODEL")
            or DEFAULT_MODEL
        )

    return base_url, api_key, model


def _chat_once(base_url: str, api_key: str, model: str,
               messages: list[dict[str, Any]], max_tokens: int,
               temperature: float) -> tuple[dict | None, str]:
    """单次 OpenAI-compatible /chat/completions 调用。返回 (data, error_str)。"""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # deepseek v4-flash 复杂任务推理模式会无限展开 (finish_reason=length,
    # content 空) — 必须显式关闭 thinking (ARCHITECTURE-BLINDSPOTS §1.3)。
    if "deepseek" in model.lower():
        body["thinking"] = {"type": "disabled"}

    url = f"{base_url}/chat/completions"
    # Restrict to http/https before touching the network: a misconfigured
    # base_url (e.g. file:// or a custom scheme) must never be opened by
    # urllib, which would read local files / trigger unexpected handlers.
    if urllib.parse.urlparse(url).scheme.lower() not in ("http", "https"):
        return None, f"unsupported LLM URL scheme: {urllib.parse.urlparse(url).scheme or '(none)'!r}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:  # nosec B310 -- scheme restricted to http/https above
            return json.loads(resp.read()), ""
    except urllib.error.HTTPError as exc:
        return None, f"LLM HTTP {exc.code}: {exc.read()[:300]!r}"
    except Exception as exc:  # noqa: BLE001 -- LLM network failure -> error string, never crash the loop
        return None, f"LLM call failed: {exc}"


def _extract_content(data: dict | None) -> str:
    if not isinstance(data, dict):
        return ""
    try:
        return str(data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_json_object(text: str):
    """从 LLM 输出中提取 JSON 对象 (容忍自然语言前缀/后缀/markdown 代码围栏)。

    实测 LLM 常输出 "好的，已获取... {JSON}" 或 ```json {...} ``` —
    纯 json.loads 会全部打回导致 tool loop 耗尽。
    """
    t = text.strip()
    # 去 markdown 代码围栏
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # 括号平衡扫描: 提取第一个 {...} 完整块
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _run_llm_backend(payload: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """自实现 LLM agent worker: 直接 HTTP 调 /chat/completions + MCP 工具循环。

    Agent loop: LLM 输出 tool_call JSON → executor 调 mcp_tool.py 执行
    (jadx/aapt 等只读工具) → 结果回填 → 继续, 直到 LLM 输出 answer。
    免费池模型 (glm-4.7-flash-free 等) 限流严重 (429/空内容, 2026-08-31 实测)
    → 每轮调用失败或空内容时自动降级默认模型 (deepseek-v4-flash)。
    """
    profile: dict[str, Any] = {}
    if isinstance(payload.get("model_profile"), dict):
        profile = payload["model_profile"]
    base_url, api_key, model = _resolve_llm_config(profile)
    if not api_key:
        return _payload_error("no API key found (model_profile / ~/.hermes/config.yaml / ZENMUX_API_KEY)")

    max_tokens = int(profile.get("max_tokens") or 16000)
    temperature = float(profile.get("temperature") or 0.2)
    tool_block = _tools_prompt_block()
    system_prompt = _AGENT_SYSTEM_PROMPT + ("\n\n可用工具:\n" + tool_block if tool_block else "")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_prompt(payload)},
    ]
    used_model, used_base, used_key, fallback = model, base_url, api_key, False
    total_tokens = 0

    def _llm_round() -> tuple[str, str]:  # (content, err)
        nonlocal used_model, used_base, used_key, fallback, total_tokens
        data, err = _chat_once(used_base, used_key, used_model, messages, max_tokens, temperature)
        content = _extract_content(data)
        if (err or not content) and used_model != DEFAULT_MODEL:
            fb_base, fb_key, fb_model = _resolve_llm_config({})
            if fb_model and fb_model != used_model and fb_key:
                data2, err2 = _chat_once(fb_base, fb_key, fb_model, messages, max_tokens, temperature)
                content2 = _extract_content(data2)
                if not err2 and content2:
                    data, err, content = data2, err2, content2
                    used_model, used_base, used_key, fallback = fb_model, fb_base, fb_key, True
        if isinstance(data, dict):
            usage = data.get("usage") or {}
            try:
                total_tokens += int(usage.get("total_tokens") or 0)
            except (TypeError, ValueError):
                pass
        return content, err

    tool_errors = 0
    trace_path = os.environ.get("SWARM_EXECUTOR_TRACE", "")

    def _trace(round_no: int, content: str, parsed_type: str, detail: str = "") -> None:
        # Open per write with a context manager so the handle can never leak,
        # even when an early return skips the explicit close paths.
        if not trace_path:
            return
        with open(trace_path, "a", encoding="utf-8") as trace_fh:
            trace_fh.write(f"[r{round_no}] type={parsed_type} len={len(content)} {detail}\n")
            trace_fh.write(f"  content: {content[:400]!r}\n")

    for _round in range(MAX_TOOL_ROUNDS):
        content, err = _llm_round()
        if err:
            return _payload_error(err)
        if not content.strip():
            _trace(_round, content, "empty")
            messages.append({"role": "user", "content": "[executor] 上一轮输出为空, 请重新输出 JSON。"})
            continue

        try:
            parsed = _extract_json_object(content)
        except Exception:  # noqa: BLE001
            parsed = None
        if parsed is None:
            _trace(_round, content, "nojson")
            messages.append({
                "role": "user",
                "content": "[executor] 未提取到合法 JSON。必须输出 {\"tool_call\": {...}} 或 {\"answer\": \"...\"} (可带简短过渡语, 但 JSON 必须完整)。",
            })
            continue
        if not isinstance(parsed, dict):
            _trace(_round, content, "notdict")
            messages.append({"role": "user", "content": "[executor] JSON 必须是对象。"})
            continue

        if "answer" in parsed:
            answer = str(parsed["answer"] or "")
            _trace(_round, content, "answer", f"answer_len={len(answer)}")
            role = str(task.get("required_role") or task.get("task_type") or "custom")
            return {
                "success": True,
                "content": answer,
                "capture": bool(answer),
                "token_cost": total_tokens,  # nosec B105 -- numeric cost counter, not a credential
                "result_summary": {
                    # 完整落库: worker.py 把 result_summary 直接写 agent_tasks,
                    # 截断会导致完整报告丢失 (2026-08-31 实测 500 字符截断丢报告)。
                    "content": answer,
                    "worker_agent": f"native-{role}",
                    "worker_role": role,
                    "model_profile": profile or {},
                    "backend": "llm",
                    "model": used_model,
                    "fallback": fallback,
                    "tool_rounds": _round + 1,
                },
                "metadata": {
                    "executor": "swarm_native_executor",
                    "backend": "llm",
                    "model": used_model,
                    "fallback": fallback,
                },
            }

        if "tool_call" in parsed and isinstance(parsed["tool_call"], dict):
            # 强制收敛兜底: 临近轮次上限时不再执行新工具,
            # 直接用已收集的工具证据让 LLM 输出最终 answer (防耗尽无产出)。
            if _round >= MAX_TOOL_ROUNDS - 2:
                _trace(_round, content, "forced-answer")
                evidence_parts = [
                    m["content"] for m in messages
                    if isinstance(m.get("content"), str) and m["content"].startswith("工具结果:")
                ]
                evidence = "\n\n".join(evidence_parts)[-8000:]
                messages.append({
                    "role": "user",
                    "content": "[executor] 已达轮次上限, 不再执行工具。"
                               f"基于以下已收集的工具证据直接输出 {{\"answer\": ...}} 完整汇总报告"
                               f"(若证据不足, 明确说明哪些结论是推测)。\n\n已收集证据:\n{evidence}",
                })
                content, err = _llm_round()
                if err:
                    return _payload_error(err)
                parsed2 = _extract_json_object(content)
                # 强制轮 LLM 常输出纯文本报告而不包 {"answer": ...} —
                # 此时把整段内容直接当作最终 answer (比失败丢报告好)。
                if isinstance(parsed2, dict) and "answer" in parsed2:
                    answer = str(parsed2["answer"] or "")
                    forced_text = False
                elif content.strip():
                    answer = content
                    forced_text = True
                else:
                    return _payload_error("forced answer round produced empty output")
                if forced_text or isinstance(parsed2, dict):
                    _trace(_round, content, "forced-answer-ok", f"len={len(answer)} text={forced_text}")
                    role = str(task.get("required_role") or task.get("task_type") or "custom")
                    return {
                        "success": True,
                        "content": answer,
                        "capture": bool(answer),
                        "token_cost": total_tokens,  # nosec B105
                        "result_summary": {
                            "content": answer,
                            "worker_agent": f"native-{role}",
                            "worker_role": role,
                            "model_profile": profile or {},
                            "backend": "llm",
                            "model": used_model,
                            "fallback": fallback,
                            "tool_rounds": _round + 1,
                            "forced_answer": True,
                            "forced_text": forced_text,
                        },
                        "metadata": {
                            "executor": "swarm_native_executor",
                            "backend": "llm",
                            "model": used_model,
                            "fallback": fallback,
                            "forced_answer": True,
                            "forced_text": forced_text,
                        },
                    }
                return _payload_error("forced answer round produced no valid answer JSON")

            tc = parsed["tool_call"]
            server = str(tc.get("server") or "apk")
            tool = str(tc.get("tool") or "")
            raw_args = tc.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            messages.append({"role": "assistant", "content": content})
            try:
                result_text = _run_mcp_tool(server, tool, args)
                tool_errors = 0
                _trace(_round, content, "tool", f"{server}.{tool} result_len={len(result_text)}")
            except subprocess.TimeoutExpired:
                result_text = "[工具超时 400s]"
                tool_errors += 1
            except Exception as exc:  # noqa: BLE001
                result_text = f"[工具调用异常] {type(exc).__name__}: {exc}"
                tool_errors += 1
            # 临近轮次上限: 强制收敛, 避免 LLM 无限挖掘 (实测加固 APK 会陷分析泥潭)
            if _round >= MAX_TOOL_ROUNDS - 2:
                messages.append({
                    "role": "user",
                    "content": f"工具结果: {result_text}\n\n[executor] 已接近轮次上限 ({MAX_TOOL_ROUNDS})。"
                               "下一步必须直接输出 {\"answer\": ...} 汇总当前已获得的全部证据与结论, 不得再调用工具。",
                })
            else:
                messages.append({"role": "user", "content": f"工具结果: {result_text}"})
            if tool_errors >= 3:
                return _payload_error(f"tool loop failed: 连续 3 次工具执行异常, 最后结果: {result_text[:300]}")
            continue

        _trace(_round, content, "nokey")
        messages.append({
            "role": "user",
            "content": "[executor] JSON 必须含 \"answer\" 或 \"tool_call\" 字段。",
        })

    return _payload_error(f"tool loop exhausted after {MAX_TOOL_ROUNDS} rounds (no answer)")


def _normalize_backend_output(raw: str, task: dict[str, Any]) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {
            "success": True,
            "content": "",
            "capture": False,
            "token_cost": 0,  # nosec B105 -- numeric cost counter, not a credential
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
        "token_cost": 0,  # nosec B105 -- numeric cost counter, not a credential
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
        "token_cost": 32,  # nosec B105 -- numeric cost counter, not a credential
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
    env, _dropped = scrub_environment()
    env["SWARM_AGENT_EXEC"] = "1"
    try:
        # A malformed SWARM_NATIVE_AGENT_COMMAND (e.g. an unbalanced quote)
        # makes shlex.split raise ValueError; it must return a clean JSON
        # failure instead of a raw traceback with no stdout payload, so the
        # executor's stdin/stdout contract holds on any configuration error.
        argv = shlex.split(command)
        proc = subprocess.run(
            argv,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=1800,
            env=env,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 -- command backend error -> clean JSON failure (stdin/stdout contract)
        return _payload_error(str(exc))
    if proc.returncode != 0:
        return _payload_error(proc.stderr.strip() or f"agent command exited {proc.returncode}")
    return _normalize_backend_output(proc.stdout, task)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001 -- invalid executor input -> clean JSON failure
        print(json.dumps(_payload_error(f"invalid executor input: {exc}"), ensure_ascii=False))
        return 0

    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    context = str(payload.get("context") or "")
    profile = payload.get("model_profile") if isinstance(payload.get("model_profile"), dict) else {}

    backend = os.getenv("SWARM_NATIVE_BACKEND", "llm").strip().lower()
    if backend == "llm":
        result = _run_llm_backend(payload, task)
    elif backend == "command":
        result = _run_command_backend(payload, task)
    elif backend == "simulate":
        result = _simulate(task, context, profile)
    else:
        result = _payload_error(f"unknown SWARM_NATIVE_BACKEND: {backend}")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
