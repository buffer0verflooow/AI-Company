#!/usr/bin/env python3
"""Route company conversations to the correct product line.

The primary integration is a Hermes ``pre_llm_call`` shell hook. Hermes sends
one JSON payload on stdin; this program emits either ``{}`` or a
``{"context": "..."}`` response. Security work is submitted to the existing
swarm client API. Other product lines are annotated for the main company agent
until their own execution adapters are enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from ._safe_io import (
        apply_worker_proxy,
        file_lock,
        locked_atomic_write_text,
        quote_identifier,
        read_text_limited,
        read_text_limited_nofollow,
        resolve_worker_proxy,
        scrub_environment,
        sqlite_uri,
    )
except ImportError:  # direct ``python automation/company_router.py`` invocation
    from _safe_io import (
        apply_worker_proxy,
        file_lock,
        locked_atomic_write_text,
        quote_identifier,
        read_text_limited,
        read_text_limited_nofollow,
        resolve_worker_proxy,
        scrub_environment,
        sqlite_uri,
    )


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "router_config.json"
INTERNAL_WORKER_PREFIX = "[COMPANY_WORKER_INTERNAL]"
TVCR_INTERNAL_PREFIX = "[COMPANY_TVCR_INTERNAL]"
OPERATOR_INTERNAL_PREFIX = "[COMPANY_OPERATOR_INTERNAL]"
LOGGER = logging.getLogger(__name__)
INTERNAL_MESSAGE_PREFIXES = (INTERNAL_WORKER_PREFIX, TVCR_INTERNAL_PREFIX, OPERATOR_INTERNAL_PREFIX)
INTERNAL_MESSAGE_PREFIX_RE = re.compile(
    rf"^(?:\s*(?:{'|'.join(re.escape(prefix) for prefix in INTERNAL_MESSAGE_PREFIXES)})\s*)+",
    re.IGNORECASE,
)
NON_USER_SESSION_SOURCES = {"tool", "cron", "subagent"}
SYNTHETIC_MESSAGE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"\[(?:IMPORTANT:\s*)?Background process\b"
    r"|\[Async Delegation\b"
    r"|\[ASYNC DELEGATION BATCH COMPLETE\b"
    r"|\[AGENTKEY_RADAR_PROBE\]"
    r"|\[CONTEXT COMPACTION\b"
    r"|\[公司\s+Research\s+完成通知\]"
    r"|\[公司\s+TVCR\s+经营复盘\]"
    r"|\[IMPORTANT:\s*"
    r"|\[IMPORTANT:\s*The user has invoked the .* skill\b"
    r"|\[The user sent an image but I couldn't quite see it\b"
    r"|\[(?:cron|定时任务)(?:\s|:|：|\])"
    r"|Review the conversation above and (?:update the skill library|consider saving to memory)"
    r"|\[(?:image|图片)[^\]\n]{0,32}(?:fallback|降级)[^\]\n]*\]"
    r")",
    re.IGNORECASE,
)
MODEL_SWITCH_NOTICE_RE = re.compile(
    r"^\s*\[Note:\s*model was just switched[^\]]*\]\s*",
    re.IGNORECASE,
)

EXPLICIT_NEW_SWARM_RE = re.compile(
    r"(?:new|fresh|another)\s+swarm|(?:新的?|新建|另开|再开|重新(?:提交|分发|启动))[^，。；\n]{0,8}(?:蜂群|swarm)",
    re.IGNORECASE,
)
SKILL_REVIEW_MARKER = "update the skill library"
HERMES_STATE_DB = Path("/home/pwn/.hermes/state.db")
PRE_EVAL_MIN_PRIOR_MESSAGES = 3  # user+assistant messages before this dispatch
PRE_EVAL_MIN_PRIOR_USER_MESSAGES = 1  # the current message doesn't count
MAX_HOOK_STDIN_BYTES = 8 * 1024 * 1024  # bound the external pre_llm_call payload


# ── Routing term tables ──────────────────────────────────────────────────────
# These word lists drive classify_message. They live in router_config.json under
# "routing_terms" so they can be tuned without code changes; the built-in
# defaults below are the exact previous values and act as a fallback when the
# file omits a table, so classification behaviour is unchanged if the config is
# absent or partial.
_DEFAULT_ROUTING_TERMS: dict[str, list] = {
    "security": [
        "安全", "漏洞", "赏金", "hackerone", "bug bounty", "渗透", "红队",
        "recon", "exploit", "cve", "apk", "逆向", "攻击面", "扫描", "蜂群",
        "swarm", "poc", "idor", "xss", "sqli", "ssrf", "jwt", "cors",
    ],
    "article": ["文章", "公众号", "写稿", "排版", "选题", "草稿箱", "润色", "发布文章"],
    # Compatibility export for existing operators/tests.  The classifier no
    # longer treats these words as a blanket veto; it uses the action/object
    # gates below so a real research article request is not discarded.
    "article_blocking": ["研究", "调研", "分析报告", "codex", "研究报告", "蜂群", "swarm"],
    "video": ["视频", "pixelle", "b站", "分镜", "配音", "tts", "字幕", "剪辑"],
    "company": ["公司", "战略", "财务", "销售", "运营", "产品", "流程", "知识库", "仪表盘"],
    "management": ["状态", "进度", "流程", "路由", "架构", "能力", "管理", "如何", "怎么", "是否", "当前"],
    "company_execution": [
        "开始", "执行", "修改", "实现", "开发", "完善", "更新", "新增", "接入",
        "搭建", "创建", "生成", "整理", "迁移", "重构", "验证", "落地", "推进",
        "调研", "排查", "修复", "诊断", "制定", "编写", "补充", "删除",
        "implement", "build", "update", "create", "refactor",
    ],
    # 蜂群研究路由 (2026-08-10): 公司职能研究/调研类任务 → dispatch_swarm。
    # 与 security 的区别: research 不涉及外部目标授权 (research intent,
    # 无 scope 概念); 与 company 的区别: research 是多 agent 并行研究 (蜂群
    # benchmark 证明: 研究/分析类任务蜂群 > 单 agent)。
    "research": [
        "竞品", "调研", "研究", "分析报告", "对比", "评估", "趋势", "选型",
        "市场机会", "技术调研", "行业分析", "情报", "benchmark", "survey",
        "竞品分析", "可行性", "方案对比", "技术选型",
    ],
    # Unioned with the company terms to form COMPANY_TASK_TERMS.
    "company_task_extra": [
        "项目", "任务", "路由", "代码", "测试", "仓库", "配置", "系统", "文件",
        "文档", "竞品", "市场", "需求", "方案", "计划", "bug", "问题", "错误",
        "模型", "会话", "hermes", "codex", "sandbox", "规则", "分支", "自动化",
    ],
    "active_security": [
        "扫描", "探测", "枚举", "爆破", "利用", "攻击", "绕过", "验证漏洞",
        "recon", "scan", "exploit", "brute", "probe", "fuzz",
    ],
    # retained for reference only — NOT trusted for authorization (see classify_message)
    "authorization": [
        "已授权", "明确授权", "授权范围", "in scope", "in-scope", "scope内",
        "hackerone项目", "hackerone program", "赏金项目", "自有系统", "本地靶场",
    ],
    "external_action": ["发布", "推送", "提交hackerone", "发送", "删除", "付款", "转账", "上线"],
}


def _load_routing_terms(path: Path | None = None) -> dict[str, set]:
    """Load routing term tables from router_config.json, over built-in defaults.

    A missing file, unreadable JSON, or a missing/renamed table all fall back to
    the defaults so a malformed config can never silently empty a classifier gate.
    """
    resolved: dict[str, list] = {key: list(value) for key, value in _DEFAULT_ROUTING_TERMS.items()}
    target = Path(path) if path is not None else DEFAULT_CONFIG
    try:
        data = json.loads(read_text_limited(target, max_bytes=5 * 1024 * 1024))
        table = data.get("routing_terms") if isinstance(data, dict) else None
        if isinstance(table, dict):
            for key, value in table.items():
                if key in resolved and isinstance(value, list):
                    resolved[key] = [str(item) for item in value]
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        pass
    return {key: set(value) for key, value in resolved.items()}


def _apply_routing_terms(terms: dict[str, set]) -> None:
    """Publish resolved term tables (and their derived regexes) as module globals."""
    global SECURITY_TERMS, ARTICLE_TERMS, ARTICLE_BLOCKING_TERMS, VIDEO_TERMS
    global COMPANY_TERMS, MANAGEMENT_TERMS, COMPANY_EXECUTION_TERMS, COMPANY_TASK_TERMS
    global ACTIVE_SECURITY_TERMS, AUTHORIZATION_TERMS, EXTERNAL_ACTION_TERMS
    global RESEARCH_TERMS
    global COMPANY_EXECUTION_PATTERN, COMPANY_DIRECTIVE_RE, NEGATED_EXTERNAL_ACTION_RE
    SECURITY_TERMS = terms["security"]
    ARTICLE_TERMS = terms["article"]
    ARTICLE_BLOCKING_TERMS = terms["article_blocking"]
    VIDEO_TERMS = terms["video"]
    COMPANY_TERMS = terms["company"]
    MANAGEMENT_TERMS = terms["management"]
    COMPANY_EXECUTION_TERMS = terms["company_execution"]
    COMPANY_TASK_TERMS = COMPANY_TERMS | terms["company_task_extra"]
    ACTIVE_SECURITY_TERMS = terms["active_security"]
    AUTHORIZATION_TERMS = terms["authorization"]
    EXTERNAL_ACTION_TERMS = terms["external_action"]
    RESEARCH_TERMS = terms.get("research") or set()
    COMPANY_EXECUTION_PATTERN = "|".join(
        re.escape(term) for term in sorted(COMPANY_EXECUTION_TERMS, key=len, reverse=True)
    )
    COMPANY_DIRECTIVE_RE = re.compile(
        rf"^\s*(?:(?:请(?:你)?|帮我|麻烦|现在|直接|立即|继续|先|着手|需要你|让你|让(?:codex|code|你|它))\s*)*"
        rf"(?:开始\s*)?(?:{COMPANY_EXECUTION_PATTERN})",
        re.IGNORECASE,
    )
    external_pattern = "|".join(
        re.escape(term) for term in sorted(EXTERNAL_ACTION_TERMS, key=len, reverse=True)
    )
    # Only strip an external-action term when the negator is directly attached
    # ("不发布", "禁止推送"). A wide window used to swallow "不要忘记发布" and let a
    # real publish slip through; a near-miss now stays flagged (fail toward approval).
    NEGATED_EXTERNAL_ACTION_RE = re.compile(
        rf"(?:不|不要|无需|禁止|不得|暂不|先不|仅生成|只生成)[^，。；\n]{{0,1}}(?:{external_pattern})",
        re.IGNORECASE,
    )


def reload_routing_terms(path: Path | None = None) -> None:
    """Re-read routing term tables from disk (called at startup and on demand)."""
    _apply_routing_terms(_load_routing_terms(path))


# Load the term tables at import so classify_message sees config-driven values.
_apply_routing_terms(_load_routing_terms())
# 检测用户抱怨、纠错或清理误生成文章的元模式（非文章生产请求）。
ARTICLE_NEGATION_PATTERNS = re.compile(
    r"(?:怎么|为什么|为何)又?[^。；\n]{0,24}(?:文章产线|写(?:了|成)?[^。；\n]{0,4}文章|生成[^。；\n]{0,4}文章)"
    r"|我(?:有|什么时候)?让你[^。；\n]{0,12}(?:写文章|写稿|发文章|生成文章)"
    r"|(?:检讨|反省|误判|误触发|误分类|误分发|误路由)[^。；\n]{0,12}文章"
    r"|文章[^。；\n]{0,12}(?:误判|误触发|误分发|误路由)"
    r"|(?:这|那|刚才)[^。；\n]{0,4}(?:篇)?文章[^。；\n]{0,16}(?:清除|删掉|删除|不是我想要|根本不是|不需要)",
    re.IGNORECASE,
)
ARTICLE_TOOL_OPERATION_RE = re.compile(
    r"(?:mineru|ocr|提取|解析|识别|读取|抽取)[^。；\n]{0,24}(?:文章|稿件|文档|报告)"
    r"|(?:文章|稿件|文档|报告)[^。；\n]{0,24}(?:mineru|ocr|提取|解析|识别|读取|抽取)",
    re.IGNORECASE,
)
ARTICLE_OBJECT_PATTERN = r"(?:公众号(?:文章)?|技术文章|文章|稿件|稿子|写稿|草稿)"
ARTICLE_DIRECT_REQUEST_RE = re.compile(
    rf"(?:写(?:一篇|篇)?|撰写|创作|改写|润色|排版|发布|推送)[^。；\n]{{0,24}}{ARTICLE_OBJECT_PATTERN}"
    rf"|{ARTICLE_OBJECT_PATTERN}[^。；\n]{{0,16}}(?:改写|润色|排版|发布|推送)",
    re.IGNORECASE,
)
ARTICLE_DESTINATION_RE = re.compile(
    rf"(?:改|整理|转换|转化|加工)[^。；\n]{{0,8}}(?:成|为)[^。；\n]{{0,8}}{ARTICLE_OBJECT_PATTERN}",
    re.IGNORECASE,
)
VIDEO_DATA_CONTEXT_RE = re.compile(
    r"音视频(?:数据|流|内容|传输)?|视频(?:数据|流|传输|通话|会议|联网)",
    re.IGNORECASE,
)
VIDEO_OBJECT_PATTERN = r"(?:视频|短片|短视频|成片|分镜|口播稿|配音|字幕|剪辑|mp4|pixelle)"
VIDEO_REQUEST_RE = re.compile(
    rf"(?:生成|制作|创作|剪辑|配音|加字幕|做成|转成|改成|渲染|输出)[^。；\n]{{0,24}}{VIDEO_OBJECT_PATTERN}"
    rf"|{VIDEO_OBJECT_PATTERN}[^。；\n]{{0,16}}(?:制作|剪辑|配音|加字幕|做成|转成|改成|渲染|输出)",
    re.IGNORECASE,
)
COMPANY_OBJECT_FIRST_RE = re.compile(
    r"^\s*(?:(?:请(?:你)?|帮我|麻烦|现在|直接|立即|先)\s*)*(?:把|将)",
    re.IGNORECASE,
)
COMPANY_CONTEXTUAL_EXECUTION_RE = re.compile(
    r"^\s*(?:开始|继续|直接|立即)\s*(?:修改|执行|实现|开发|完善|更新|重构|排查|修复|验证)(?:吧|了|一下)?\s*$",
    re.IGNORECASE,
)
QUESTION_RE = re.compile(
    r"[?？]|为什么|为何|怎么(?:样)?|如何|是否|能否|可否|有没有|有无|什么|哪些?|哪(?:个|些)|借鉴意义|支持吗|下载吗|"
    r"[^。；\n]{0,10}吗(?:[?？]|$)",
    re.IGNORECASE,
)
SECURITY_ANALYSIS_RE = re.compile(
    r"(?:分析|审计|逆向|检查|评估|研究|排查|测试|验证|复现|定位)",
    re.IGNORECASE,
)
SECURITY_REPORT_RE = re.compile(
    r"(?:生成|写|整理|输出|出)[^。；\n]{0,8}(?:安全|漏洞|渗透|赏金|逆向)[^。；\n]{0,8}报告",
    re.IGNORECASE,
)

# 方法论研究门控 (2026-08-12): 技术方法论讨论 (无目标实体、无主动攻击动词)
# 不得被 security 词 (fuzz/漏洞/反编译) 劫持成 recon 扫描任务。
# 强信号词 — 消息中必须出现至少一个, 才可能是"讨论方法"而非"执行任务"。
METHODOLOGY_STRONG_RE = re.compile(
    r"(方法|方法论|技术细节|语法树|代码图|全景|原理|主流|盘点|梳理|现状|有哪些|怎么做|如何实现|技术方案)",
    re.IGNORECASE,
)
# 技术/安全上下文 — 与强信号词同时出现才构成"方法论讨论"。
METHODOLOGY_CONTEXT_RE = re.compile(
    r"(漏洞|逆向|fuzz|反编译|伪代码|二进制|SAST|静态分析|动态分析|模拟执行|exploit|渗透|安全|解析器)",
    re.IGNORECASE,
)
# 主动攻击/探测动词 — 出现即视为实际任务 (含 fuzz 作动词的用法),
# 即使措辞里也带"方法"等词, 仍按 security 处理。
ACTIVE_TASK_VERB_RE = re.compile(
    r"(扫描|探测|枚举|爆破|绕过|攻击|验证漏洞|写.{0,8}poc|recon|probe|brute|"
    r"fuzz\s*(?:一下|目标|这个|那个|本机|本地|[:：]|\s+[a-zA-Z0-9./-]+))",
    re.IGNORECASE,
)

# 蜂群自身系统元讨论门控 (2026-08-13): "蜂群"/"swarm" 在 SECURITY_TERMS 里
# 表示安全蜂群产品，但 "蜂群算法/蜂群架构/蜂群调度机制" 这类词描述的是蜂群系统
# 自身。此类消息是公司内部讨论，不得被 "蜂群"+"分析" 误派成 security 蜂群任务。
META_SWARM_DISCUSSION_RE = re.compile(
    r"(?:蜂群|swarm)[^，。；\n]{0,6}(?:算法|架构|系统|机制|框架|平台|原理|设计|实现|流程|代码|策略|调度|模型|方案)"
    r"|(?:讨论|聊聊|交流|探讨)[^，。；\n]{0,12}(?:蜂群|swarm)",
    re.IGNORECASE,
)

# URL 作为"待抓取/阅读的内容来源"的强信号 (2026-08-13): "抓取分析一下
# https://zeropath.com/blog/... 中提到的漏洞挖掘方法" 里的 URL 是阅读对象，
# 不是攻击目标。出现抓取/阅读动词，或 URL 后带"提到的/中提到的"等引用语时，
# 方法论门控不应再把该 URL 当作 attack target 而放行到 security。
CONTENT_SOURCE_URL_SIGNAL_RE = re.compile(
    r"(?:抓取|爬取|阅读|读取|看下|看看|fetch|crawl|read)"
    r"|(?:提到的|中提到的|里提到的|上提到的|中说的|里说的|中介绍的|里介绍的|中写的|里写的)",
    re.IGNORECASE,
)

DOMAIN_RE = re.compile(r"(?<![@\w-])(?:https?://)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)(?::\d+)?", re.IGNORECASE)
IP_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
APK_RE = re.compile(r"(?:^|\s)(/[^\s]+\.apk|[^\s]+\.apk)(?:$|\s)", re.IGNORECASE)


@dataclass(frozen=True)
class RouteDecision:
    route: str
    confidence: float
    action: str
    reason: str
    intent: str = "custom"
    target_type: str = "unknown"
    target: str = ""
    profile: str = "balanced"
    authorization_required: bool = False
    external_action: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int_config(config: dict[str, Any], key: str, default: int) -> int:
    """Coerce a config value to int, falling back on malformed values.

    The router config is hand-edited JSON; a single non-numeric value must
    not crash the whole pre_llm_call hook or a cron tick.
    """
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default


def _float_config(config: dict[str, Any], key: str, default: float) -> float:
    """Coerce a config value to float, falling back on malformed values."""
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) else default


def _safe_counter(value: Any) -> int:
    """Coerce a DB/JSON counter to int, degrading to 0 on malformed values.

    State rows are written by sibling subsystems; a corrupt attempt/restart
    counter must not crash the routing hook.
    """
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _stored_decision(json_text: Any) -> RouteDecision | None:
    """Rehydrate a stored route decision, returning None on corrupt cells.

    State rows are written by sibling subsystems; a corrupt ``decision_json``
    must not crash the routing hook.  Callers fall back to fresh classification
    when this returns None.
    """
    try:
        parsed = json.loads(str(json_text or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        decision = RouteDecision(**parsed)
        # ``RouteDecision`` does not type-check: a corrupt stored row with a
        # non-numeric confidence must be rejected here, not crash the numeric
        # formatting in build_context later.
        float(decision.confidence)
        return decision
    except (TypeError, ValueError):
        return None


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    data = json.loads(read_text_limited(path, max_bytes=5 * 1024 * 1024))
    if not isinstance(data, dict):
        raise TypeError("router config must be an object")
    data["config_path"] = str(path)
    return data


def resolve_session_origin(index_path: str, session_id: str) -> dict[str, str]:
    """Resolve a Hermes agent session ID back to its messaging destination."""
    if not index_path or not session_id:
        return {}
    try:
        payload = json.loads(read_text_limited(Path(index_path), max_bytes=10 * 1024 * 1024))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    for entry in payload.values():
        if not isinstance(entry, dict) or str(entry.get("session_id") or "") != session_id:
            continue
        origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
        chat_id = str(origin.get("chat_id") or "")
        platform = str(origin.get("platform") or entry.get("platform") or "")
        if not chat_id or not platform:
            return {}
        return {
            "platform": platform,
            "chat_id": chat_id,
            "thread_id": str(origin.get("thread_id") or ""),
            "user_id": str(origin.get("user_id") or ""),
        }
    return {}


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _has_external_action(text: str) -> bool:
    cleaned = NEGATED_EXTERNAL_ACTION_RE.sub("", text)
    return _contains_any(cleaned, EXTERNAL_ACTION_TERMS)


def _looks_like_question(text: str) -> bool:
    return bool(QUESTION_RE.search(text or ""))


def _is_article_request(text: str) -> bool:
    return bool(ARTICLE_DIRECT_REQUEST_RE.search(text) or ARTICLE_DESTINATION_RE.search(text))


def _is_video_request(text: str) -> bool:
    production_text = VIDEO_DATA_CONTEXT_RE.sub("", text or "")
    return bool(VIDEO_REQUEST_RE.search(production_text))


def _is_security_request(text: str) -> bool:
    lowered = (text or "").lower()
    has_security_context = _contains_any(text, SECURITY_TERMS) or bool(extract_target(text))
    if _contains_any(text, ACTIVE_SECURITY_TERMS):
        return True
    if re.search(r"\b(?:poc|exploit|recon|scan|probe|fuzz)\b", lowered):
        return True
    if SECURITY_REPORT_RE.search(text):
        return True
    return bool(has_security_context and SECURITY_ANALYSIS_RE.search(text))


def _is_research_request(text: str) -> bool:
    """蜂群研究路由 (2026-08-10): 公司职能研究/调研类任务 → dispatch_swarm。

    判定: 研究词 + 执行意图 (不要只问"竞品是谁"——那走 main_agent 问答)。
    与 security 的区分: 研究任务不含外部目标 (无 scope 授权概念);
    与 company 的区分: 研究是明确的分析产出任务, 交给蜂群多 agent 并行。
    """
    if not _contains_any(text, RESEARCH_TERMS):
        return False
    if _contains_any(text, {"调研", "研究", "分析", "评估", "对比", "选型", "survey"}):
        return True
    # 研究词 + 公司执行动作 (如 "做一份竞品分析报告")
    return bool(_contains_any(text, COMPANY_EXECUTION_TERMS) and _contains_any(text, {"报告", "方案", "分析", "梳理", "总结"}))


def _is_methodology_research_request(text: str) -> bool:
    """方法论研究门控 (2026-08-12): 技术方法论讨论不得被 security 词劫持。

    背景: "现在方法是将二进制反编译成伪代码…或者通过动态fuzz，模拟执行来找漏洞"
    这类讨论"怎么做"的陈述句, 会因 fuzz/漏洞/反编译 命中 security 词表,
    以 intent=recon 形态派发蜂群 → 无目标全 BLOCKED, 白烧 token。

    判定三条件 (全部满足才放行到 research):
    1. 有方法论强信号词 (方法/技术细节/语法树/全景/盘点/现状…)
    2. 有技术/安全上下文 (漏洞/fuzz/反编译/二进制…)
    3. 无主动攻击动词 (扫描/探测/绕过/写poc/fuzz 目标…)
       且无具体目标实体 (IP/域名/APK → 那是有授权语义的真实任务)
    """
    if not text or not METHODOLOGY_STRONG_RE.search(text):
        return False
    if not METHODOLOGY_CONTEXT_RE.search(text):
        return False
    if ACTIVE_TASK_VERB_RE.search(text):
        return False
    if _reads_url_as_source(text):
        return True
    return not extract_target(text)


def _reads_url_as_source(text: str) -> bool:
    """URL 是"待抓取/阅读的内容来源"而非攻击目标。

    用于方法论门控: 当消息里有 URL，但它前面带抓取/阅读动词，或后面带
    "中提到的/里介绍的"等引用语时，说明这是读一篇文章、分析其中方法的研究
    请求，而不是把该域名当作扫描/分析目标。此类消息应放行到 research，
    不能因为 URL 的存在就落到 security。
    """
    if not extract_target(text):
        return False
    return bool(CONTENT_SOURCE_URL_SIGNAL_RE.search(text or ""))


def _is_meta_swarm_discussion(text: str) -> bool:
    """蜂群自身系统元讨论: 不是安全任务，不得派发蜂群。

    背景: "分析一下当前系统的蜂群算法，我们进行讨论" 因命中 SECURITY_TERMS 的
    "蜂群" 与 SECURITY_ANALYSIS_RE 的 "分析"，被误派成 security/analyze 蜂群。
    "蜂群算法/蜂群架构/蜂群调度机制" 描述的是蜂群系统本身，应走 main_agent 讨论。

    保护真实安全任务: 出现主动攻击动词、具体目标实体，或显式安全对象
    (漏洞/exploit/poc/攻击面/渗透/recon/赏金) 时，仍按 security 处理。
    """
    if not META_SWARM_DISCUSSION_RE.search(text or ""):
        return False
    if ACTIVE_TASK_VERB_RE.search(text):
        return False
    if extract_target(text):
        return False
    return not _contains_any(text, {"漏洞", "exploit", "poc", "攻击面", "渗透", "recon", "赏金"})


def _is_company_execution_request(text: str) -> bool:
    if COMPANY_CONTEXTUAL_EXECUTION_RE.fullmatch(text or ""):
        return True
    if not _contains_any(text, COMPANY_EXECUTION_TERMS):
        return False
    if not _contains_any(text, COMPANY_TASK_TERMS):
        return False
    return bool(COMPANY_DIRECTIVE_RE.search(text) or COMPANY_OBJECT_FIRST_RE.search(text))


# ── W14-b:dev 路由词表(保守;**仅当 `dev_route_enabled=true` 才生效**)──────
# 默认关 ⇒ `classify_message` 走原路径,既有分类结果逐字不变(锁测试)。
# 判定口径 = "开发动作词" ∧ "代码/测试上下文词"(两者齐备才判 dev),避免把
# "实现公司战略"这类经营动作误判进代码线。
_DEV_ACTION_TERMS = {
    "实现", "写代码", "编写代码", "改代码", "修改代码", "重构", "修 bug", "修bug",
    "修复bug", "修复 bug", "加测试", "添加测试", "补测试", "写测试", "编写测试",
    "让测试通过", "使测试通过", "跑测试", "运行测试", "单元测试", "implement",
    "refactor", "fix bug", "add test", "write test", "make test pass",
}
_DEV_CONTEXT_TERMS = {
    "代码", "测试", "函数", "模块", "脚本", "程序", "仓库", "repo", "pytest",
    "单元测试", "接口", "类", "bug", "编译", "构建", "build", "test",
}


def _is_dev_request(text: str) -> bool:
    """保守 dev 意图判定(仅 `enable_dev=True` 时被调用)。

    要求"开发动作词 ∧ 代码/测试上下文词"同时命中;单一动作词(如仅"实现")不足以
    进 dev 线。`dev_route_enabled=false`(出厂缺省)时本函数根本不会被咨询。
    """
    return (_contains_any(text, _DEV_ACTION_TERMS)
            and _contains_any(text, _DEV_CONTEXT_TERMS))


def _main_agent_decision(
    reason: str,
    *,
    external_action: bool = False,
    confidence: float = 0.72,
) -> RouteDecision:
    return RouteDecision(
        route="company",
        confidence=confidence,
        action="approval_required" if external_action else "main_agent",
        reason=reason,
        external_action=external_action,
    )


def _internal_metadata_value(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {
        "1", "true", "yes", "internal", "internal_call", "internal-routing",
        "internal_routing", "routing",
    }


def _is_internal_hermes_hook(payload: dict[str, Any], extra: dict[str, Any]) -> bool:
    """Trust internal prefixes only when hook provenance explicitly says so."""
    source_present = "source" in payload or "source" in extra
    source = payload.get("source") if "source" in payload else extra.get("source")
    if source_present and str(source or "").strip().lower() != "hook":
        return False

    metadata: list[dict[str, Any]] = []
    for container in (payload, extra):
        for key in ("hook_metadata", "metadata"):
            value = container.get(key)
            if isinstance(value, dict):
                metadata.append(value)

    flag_keys = {
        "internal", "is_internal", "internal_call", "is_internal_call",
        "internal_routing_event", "is_internal_routing_event", "router_internal",
    }
    type_keys = {"call_type", "event_type", "routing_event", "origin"}
    internal_marker_present = bool(metadata)
    for container in (payload, extra, *metadata):
        internal_marker_present = internal_marker_present or any(
            key in container for key in flag_keys | type_keys
        )
        if any(_internal_metadata_value(container.get(key)) for key in flag_keys):
            return True
        if any(_internal_metadata_value(container.get(key)) for key in type_keys):
            return True

    # Hermes workers are launched with ``--source tool``. The shell hook
    # inherits that trusted process-local source even on Hermes versions that
    # do not yet include source metadata in the JSON payload.
    if (
        not source_present
        and str(payload.get("hook_event_name") or "").lower() == "pre_llm_call"
        and not internal_marker_present
        and os.getenv("HERMES_SESSION_SOURCE", "").strip().lower() == "tool"
    ):
        return True

    # Preserve compatibility for legacy in-process worker calls that predate
    # the shell-hook envelope. Real Hermes hook payloads include an event name.
    return (
        not source_present
        and "hook_event_name" not in payload
        and not internal_marker_present
        and str(payload.get("session_id") or "") == "worker"
    )


def _strip_internal_message_prefixes(message: str) -> str:
    return INTERNAL_MESSAGE_PREFIX_RE.sub("", message or "", count=1).lstrip()


def _is_non_user_hermes_session(
    payload: dict[str, Any],
    extra: dict[str, Any],
    session_id: str,
    *,
    hermes_db_path: Path = HERMES_STATE_DB,
) -> bool:
    """Return True for worker/cron/subagent turns that must never auto-route."""
    candidates = [
        payload.get("session_source"), payload.get("source"),
        extra.get("session_source"), extra.get("source"),
        os.getenv("HERMES_SESSION_SOURCE"),
    ]
    if any(str(value or "").strip().lower() in NON_USER_SESSION_SOURCES for value in candidates):
        return True
    if str(session_id or "").lower().startswith("cron_"):
        return True
    if not session_id or not hermes_db_path.is_file():
        return False
    try:
        db = sqlite3.connect(sqlite_uri(hermes_db_path), uri=True, timeout=0.2)
        try:
            row = db.execute("SELECT source FROM sessions WHERE id=?", (session_id,)).fetchone()
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return False
    return bool(row and str(row[0] or "").strip().lower() in NON_USER_SESSION_SOURCES)


def extract_target(message: str) -> list[tuple[str, str]]:
    candidates: list[tuple[int, int, int, str, str]] = []

    for match in APK_RE.finditer(message or ""):
        candidates.append((match.start(1), match.end(1), 0, "apk", match.group(1)))

    for match in IP_RE.finditer(message or ""):
        raw = match.group(0)
        try:
            ipaddress.ip_address(raw)
            candidates.append((match.start(), match.end(), 0, "ip", raw))
        except ValueError:
            continue

    for match in DOMAIN_RE.finditer(message or ""):
        candidates.append((match.start(1), match.end(1), 1, "domain", match.group(1).lower()))

    targets: list[tuple[str, str]] = []
    accepted_spans: list[tuple[int, int]] = []
    seen: set[tuple[str, str]] = set()
    for start, end, _priority, target_type, target in sorted(candidates):
        if any(start < accepted_end and end > accepted_start for accepted_start, accepted_end in accepted_spans):
            continue
        accepted_spans.append((start, end))
        key = (target_type, target.lower())
        if key in seen:
            continue
        targets.append((target_type, target))
        seen.add(key)
    return targets


def _normalize_target(target_type: str, target: str) -> tuple[str, str]:
    normalized_type = str(target_type or "unknown").strip().lower()
    normalized_target = str(target or "").strip().lower()
    if normalized_type == "domain":
        normalized_target = normalized_target.rstrip(".")
    elif normalized_type == "ip":
        try:
            normalized_target = str(ipaddress.ip_address(normalized_target))
        except ValueError:
            pass
    return normalized_type, normalized_target


def _build_dedup_key(session_id: str, targets: Iterable[tuple[str, str]], intent: str) -> str:
    normalized_targets = sorted({
        _normalize_target(target_type, target)
        for target_type, target in targets
        if str(target or "").strip()
    })
    target_key = ",".join(f"{target_type}:{target}" for target_type, target in normalized_targets)
    return f"{session_id}|{target_key or 'no-target'}|{str(intent or 'custom').strip().lower()}"


def _is_internal_target(target_type: str, target: str) -> bool:
    """Local/own targets that need no external scope authorization."""
    if target_type == "apk":
        return True
    value = (target or "").lower()
    if value.startswith("/home/pwn/workspace/"):
        return True
    # Exact local name only: a prefix match would classify an external domain
    # such as ``localhost.evil.com`` as internal and skip the scope gate.
    if value == "localhost" or value.startswith("localhost:"):
        return True
    if target_type == "ip":
        try:
            addr = ipaddress.ip_address(target)
            return addr.is_private or addr.is_loopback
        except ValueError:
            return False
    return False


def classify_message(message: str, authorized_targets: Iterable[str] = (),
                     *, enable_dev: bool = False) -> RouteDecision:
    text = " ".join((message or "").split())
    # A hand-edited scalar allowlist must not iterate into characters or raise
    # TypeError out of classification.
    if isinstance(authorized_targets, str):
        authorized_targets = [authorized_targets]
    elif not isinstance(authorized_targets, (list, tuple, set)):
        authorized_targets = []

    # Keep the pure classifier safe when it is used for replay/CLI diagnostics
    # without going through handle_hook's envelope gate.
    while MODEL_SWITCH_NOTICE_RE.match(text):
        text = MODEL_SWITCH_NOTICE_RE.sub("", text, count=1).lstrip()
    if not text:
        return _main_agent_decision("空消息，不自动派发。", confidence=0.0)
    if SYNTHETIC_MESSAGE_PREFIX_RE.match(text):
        return _main_agent_decision("Hermes 合成通知，不作为用户任务派发。", confidence=0.0)
    lowered = text.lower()

    explicit = ""
    explicit_prefixes = [
        ("/research", "research"), ("/security", "security"),
        ("研究：", "research"), ("安全：", "security"),
        ("/article", "article"), ("文章：", "article"),
        ("/video", "video"), ("视频：", "video"),
        ("/company", "company"), ("公司：", "company"),
    ]
    if enable_dev:
        # dev 显式前缀只在开关打开时可识别(默认关 ⇒ 参数面/行为逐字不变)
        explicit_prefixes += [("/dev", "dev"), ("开发：", "dev")]
    for prefix, route in explicit_prefixes:
        if lowered.startswith(prefix.lower()):
            explicit = route
            break

    external_action = _has_external_action(text)

    # Synthetic/background turns must be handled by their owner.  They are
    # deliberately fail-closed here: even if a notification contains words
    # such as "文章" or "扫描", it is not a new user task.
    if not explicit and (ARTICLE_NEGATION_PATTERNS.search(text) or ARTICLE_TOOL_OPERATION_RE.search(text)):
        return _main_agent_decision(
            "用户正在纠正、清理或提取既有内容，而不是请求文章生产。",
            external_action=external_action,
        )
    # Questions and capability/information requests are not sufficient evidence
    # for an autonomous production or security run.  A user can still opt in
    # with /article, /video, /security, etc. via the explicit prefixes above.
    if not explicit and _looks_like_question(text):
        return _main_agent_decision(
            "信息查询或反问，交由公司主 Agent 先回答，不自动派发产线。",
            external_action=external_action,
        )

    if explicit:
        route = explicit
        confidence = 0.99
    elif _is_article_request(text):
        # Article production wins over a security adjective (for example,
        # "写一篇关于 JWT 安全的公众号文章").
        route = "article"
        confidence = 0.86
    elif _is_video_request(text):
        route = "video"
        confidence = 0.86
    elif _is_methodology_research_request(text):
        # 方法论研究门控 (2026-08-12): 在 security 判定之前拦截技术方法论
        # 陈述句 ("…反编译成伪代码…动态fuzz…找漏洞"), 避免被 security 词
        # 劫持成 recon 扫描。真实安全任务 (含主动攻击动词/目标实体) 不受影响。
        route = "research"
        confidence = 0.84
    elif _is_meta_swarm_discussion(text):
        # 蜂群自身系统元讨论门控 (2026-08-13): 讨论蜂群算法/架构/调度机制
        # 等公司自身系统，不是安全蜂群任务，交由主 Agent 讨论。
        return _main_agent_decision(
            "讨论公司自身蜂群/系统架构，交由公司主 Agent 处理。",
            external_action=external_action,
        )
    elif _is_security_request(text):
        route = "security"
        confidence = 0.86
    elif _is_research_request(text):
        route = "research"
        confidence = 0.84
    elif enable_dev and _is_dev_request(text):
        # W14-b:dev 线分类路由(**仅当 dev_route_enabled=true 才被咨询**)。
        route = "dev"
        confidence = 0.84
    elif _is_company_execution_request(text):
        route = "company"
        confidence = 0.84
    else:
        return _main_agent_decision(
            "未识别到明确的生产/执行动作，交由公司主 Agent 判断。",
            external_action=external_action,
            confidence=0.45,
        )

    if route != "security":
        action = {
            "article": "dispatch_article",
            "video": "dispatch_video",
            "research": "dispatch_swarm",  # 蜂群研究路由 (2026-08-10): 复用蜂群执行链路
            "dev": "dispatch_swarm",       # W14-b:dev 线复用 dispatch_swarm 分支(专属提交口)
        }.get(route, "main_agent")
        if route == "company" and _is_company_execution_request(text):
            action = "dispatch_company"
        if external_action:
            action = "approval_required"
        extra = {}
        if route == "research":
            # 蜂群研究路由 (2026-08-12): 统一 research intent, 由蜂群侧按
            # research 产品线播种 (researcher×2 + reporter)。不再压成
            # analyze/report——旧映射会让 research 任务命中二进制分析技能
            # (nm/objdump/readelf), 对市场/技术调研是语义错配。
            extra = {
                "intent": "research",
                "target_type": "unknown",
            }
        return RouteDecision(
            route=route,
            confidence=confidence,
            action=action,
            reason=f"matched {route} product-line vocabulary",
            external_action=external_action,
            **extra,
        )

    targets = extract_target(text)
    target_type, target = targets[0] if targets else ("unknown", "")
    if any(term in lowered for term in ("生成报告", "写报告", "整理报告", "输出报告", "出报告", "write report", "writeup")):
        intent = "report"
    elif any(term in lowered for term in ("利用", "exploit", "poc")):
        intent = "exploit"
    elif _contains_any(text, ACTIVE_SECURITY_TERMS):
        intent = "recon"
    else:
        intent = "analyze"

    # exploit (incl. poc) and recon are active testing; report/analyze are passive.
    active = intent in {"exploit", "recon"}
    # Authorization comes from a trusted scope allowlist (config), NEVER from
    # in-band user text like "已授权". Internal/local targets need no scope.
    allow = {str(item).strip().lower() for item in authorized_targets if str(item).strip()}
    unauthorized_targets = [
        (candidate_type, candidate)
        for candidate_type, candidate in targets
        if candidate.lower() not in allow and not _is_internal_target(candidate_type, candidate)
    ]
    authorization_required = bool(active and unauthorized_targets)

    profile = "breadth" if intent == "recon" else "depth" if intent in {"analyze", "exploit"} else "balanced"
    action = "approval_required" if authorization_required or external_action else "dispatch_swarm"
    reason = "security task requires explicit scope authorization" if authorization_required else "security task routed to research swarm"
    return RouteDecision(
        route="security",
        confidence=confidence,
        action=action,
        reason=reason,
        intent=intent,
        target_type=target_type,
        target=target,
        profile=profile,
        authorization_required=authorization_required,
        external_action=external_action,
    )


# Routes the LLM fallback may promote a message into.  Each maps to the explicit
# prefix the deterministic classifier already understands, so a fallback result
# is re-run through classify_message rather than hand-building a RouteDecision —
# security stays behind the same scope-authorization gate.
_LLM_FALLBACK_PREFIX = {
    "security": "/security ",
    "article": "/article ",
    "video": "/video ",
    "company": "/company ",
    "research": "/research ",
}
_LLM_FALLBACK_JSON_RE = re.compile(r"\{[^{}]*\"route\"[^{}]*\}", re.DOTALL)


def _parse_llm_fallback(stdout: str) -> dict[str, Any] | None:
    """Pull the last {"route":...,"confidence":...} object out of an LLM reply."""
    if not stdout:
        return None
    matches = _LLM_FALLBACK_JSON_RE.findall(stdout)
    for chunk in reversed(matches):
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "route" in payload:
            return payload
    return None


def _llm_fallback_classify(message: str, config: dict[str, Any], *, timeout: int = 45) -> dict[str, Any] | None:
    """One cheap Hermes turn to classify a message the keyword router was unsure of.

    Returns ``{"route": <security|article|video|company|none>, "confidence": float}``
    or ``None`` when the call fails or its output is unparseable.  The turn runs
    with ``COMPANY_ROUTER_BYPASS=1`` so the classifier call is never itself routed.
    """
    prompt = (
        f"{INTERNAL_WORKER_PREFIX}\n"
        "你是公司消息路由器的低置信兜底分类器。只判断下面这条用户消息应交给哪条产品线，"
        "不要执行任务，也不要追问。\n"
        "候选：security（安全研究/漏洞）、article（公众号文章生产）、video（视频生产）、"
        "company（公司经营执行）、none（闲聊/信息查询/无明确生产动作）。\n"
        "只输出一行 JSON：{\"route\":\"...\",\"confidence\":0-1 之间的小数}。\n"
        f"用户消息：{message}"
    )
    env, _dropped = scrub_environment()
    env = apply_worker_proxy(env, resolve_worker_proxy(config))
    env["COMPANY_ROUTER_BYPASS"] = "1"
    env["HERMES_SESSION_SOURCE"] = "tool"
    cmd = [
        str(config.get("hermes_executable") or "hermes"), "chat", "-q", prompt, "-Q",
        "--source", "tool", "--max-turns", "1", "--toolsets", "none",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(config.get("hermes_repo") or Path.cwd()),
            env=env,
            capture_output=True,
            text=True,
            timeout=_int_config(config, "llm_fallback_timeout_seconds", timeout),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        LOGGER.debug("LLM fallback classification failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    return _parse_llm_fallback(proc.stdout)


def classify_with_fallback(
    message: str,
    config: dict[str, Any],
    authorized_targets: Iterable[str] = (),
    *,
    fallback=None,
) -> RouteDecision:
    """Keyword classification, backed by a cheap LLM tie-break when unsure.

    两段式路由机制（用户可见行为）：
    1. 第一段 = 确定性业务线判定（``classify_message``）：纯正则/规则匹配，
       显式前缀（/article、文章：、/security 等）与意图规则（写/生成/排版+
       文章对象）直接产出 route + 置信度（写文章→article 0.86 等）。
    2. 第二段 = LLM 兜底（仅当第一段置信度低于阈值时触发）：把完整用户
       消息原文塞进提示词，让 LLM 从 security/article/video/company/none
       五选一，输出 {route, confidence}。LLM 看的是整句话语义，不是关键字。

    为什么 LLM 兜底会误判（2026-08-09 修复的历史教训，勿回退）：
    - 误判不是"信息不足"所致：LLM 对高频词做语义联想过度。实测案例
      "你能自动下载公众号统计信息吗" → LLM 判 article 0.95（高置信误判），
      真实意图是运营数据动作。信息完全充足时照样误判。
    - 二次放大机制（原 bug）：确定性规则判 company 0.84（正确）→ hybrid
      阈值 0.86 触发 LLM 兜底 → LLM 判 article → 代码用 "/article " 显式
      前缀重跑确定性规则 → 前缀强制命中 article 0.99 → 正确判定被覆盖，
      任务被送进文章产线。规则是对的，LLM 是错的，结果 LLM 赢了。
    - 修复原则：规则说了算，直觉只负责规则没认出来的情况。
      (a) 确定性业务线判定（0.84+）直接信任，不再触发 LLM；
      (b) main_agent 但置信度 ≥0.6（公司相关/管理/数据问题）也不走 LLM；
      (c) LLM 兜底禁止产生 article/video 路由——内容生产必须由规则识别，
          真正的文章请求（"写一篇关于 JWT 安全的公众号文章"）确定性规则
          已能 0.86 识别，无需 LLM 直觉。

    When the deterministic classifier lands on its low-confidence "unrecognised"
    verdict, ask an LLM which product line the message belongs to.  A confident
    answer is applied by re-running ``classify_message`` with that route's
    explicit prefix, so every downstream guard (target extraction, security
    authorization, external-action approval) still applies.  Any failure,
    ``none`` verdict, or low LLM confidence keeps the original decision.
    """
    decision = classify_message(
        message, authorized_targets,
        enable_dev=bool(config.get(_V2_DEV_ROUTE_ENABLED_KEY, False)))
    if not config.get("llm_fallback_enabled", True):
        return decision

    router_mode = str(config.get("router_mode", "keyword")).strip().lower()

    # Common fast paths for both modes: empty/synthetic (0.0) and external action
    if decision.confidence <= 0.0 or decision.external_action:
        return decision

    # 确定性判定已锁定业务线（article/video/security/company 均为强模式匹配
    # 结果，置信度 0.84-0.99），不允许 LLM 兜底推翻。
    # 历史教训：hybrid 模式下 0.84 的 company 判定被 LLM 兜底改判为 article
    # （“先将公司文章产线整理好”→article 0.95），造成产线被误分发污染。
    # main_agent 判定用 action 标记（route 恒为 company/产品线，不存在
    # route="main_agent"），此处按 action 判断，避免 LLM 兜底整段成为死代码。
    if decision.action != "main_agent":
        return decision
    # main_agent 判定但置信度不低（≥0.6，即已识别为公司相关但缺执行指令，
    # 或管理/数据/流程问题），同样保持主 Agent 处理，不交给 LLM 改判。
    if decision.confidence >= 0.6:
        return decision

    if router_mode == "hybrid":
        skip_threshold = _float_config(config, "hybrid_high_confidence_skip", 0.86)
    else:
        skip_threshold = _float_config(config, "llm_fallback_confidence", 0.5)
    if decision.confidence >= skip_threshold:
        return decision

    classifier = fallback or _llm_fallback_classify
    try:
        result = classifier(message, config)
    except Exception as exc:  # noqa: BLE001 -- a broken injected classifier must not crash the hook
        LOGGER.debug("fallback classifier raised: %s", exc)
        return decision
    if not isinstance(result, dict):
        return decision
    route = str(result.get("route") or "").strip().lower()
    try:
        llm_confidence = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return decision
    # ``json.loads`` accepts NaN/Infinity and ``nan < threshold`` is False, so
    # a malformed reply would otherwise be accepted as high-confidence.
    if not math.isfinite(llm_confidence):
        return decision
    prefix = _LLM_FALLBACK_PREFIX.get(route)
    threshold = _float_config(config, "llm_fallback_confidence", 0.5)
    if not prefix or llm_confidence < threshold:
        return decision

    # LLM 兜底不允许产生 article/video 路由：内容生产是产线级动作，必须由
    # 确定性规则（写/生成/排版等强模式）识别。历史误分发全部由此产生
    # （“你能自动下载公众号统计信息吗”→“文章”→article 0.95），而真正
    # 的文章请求确定性规则已能识别（0.86+），无需 LLM 兜底。
    if route in ("article", "video"):
        return decision

    upgraded = classify_message(
        prefix + message, authorized_targets,
        enable_dev=bool(config.get(_V2_DEV_ROUTE_ENABLED_KEY, False)))

    # Security dispatch gate: if the security product line is disabled,
    # keep the original low-confidence decision even if LLM says security.
    # Prevents misclassification of management/stop instructions as security.
    if route == "security" and not config.get("dispatch_security", True):
        return decision

    if upgraded.action in {"main_agent", "approval_required"} and route != "security":
        # The LLM chose a line the explicit prefix still would not auto-dispatch
        # (e.g. an external action surfaced): trust the deterministic outcome.
        return upgraded
    return RouteDecision(**{
        **asdict(upgraded),
        "confidence": min(llm_confidence, upgraded.confidence),
        "reason": f"低置信 LLM 兜底改判为 {route}：{upgraded.reason}",
    })


class RouterState:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db: sqlite3.Connection | None = None
        try:
            # Schema creation/migrations are writes too; serialize first
            # startup so concurrent hooks cannot race on ALTER TABLE/indices.
            with file_lock(self.path):
                self.db = sqlite3.connect(self.path, timeout=5.0)
                self.db.row_factory = sqlite3.Row
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA busy_timeout=5000")
                self.db.executescript(
                    """
            CREATE TABLE IF NOT EXISTS route_events (
                route_event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                platform TEXT DEFAULT '',
                message_hash TEXT NOT NULL,
                message_excerpt TEXT DEFAULT '',
                route TEXT NOT NULL,
                action TEXT NOT NULL,
                dedup_key TEXT DEFAULT '',
                decision_json TEXT NOT NULL,
                run_id TEXT DEFAULT '',
                request_id TEXT DEFAULT '',
                runner_pid INTEGER,
                status TEXT DEFAULT 'routed',
                result_delivered INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, message_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_route_events_session
            ON route_events(session_id, created_at DESC);
            """
                )
                columns = {row[1] for row in self.db.execute("PRAGMA table_info(route_events)")}
                migrations = {
                    "delivery_platform": "TEXT DEFAULT ''",
                    "delivery_chat_id": "TEXT DEFAULT ''",
                    "delivery_thread_id": "TEXT DEFAULT ''",
                    "delivery_user_id": "TEXT DEFAULT ''",
                    "proactive_delivered": "INTEGER DEFAULT 0",
                    "delivery_attempts": "INTEGER DEFAULT 0",
                    "delivery_error": "TEXT DEFAULT ''",
                    "last_delivery_at": "TEXT DEFAULT ''",
                    "runner_restarts": "INTEGER DEFAULT 0",
                    "quality_status": "TEXT DEFAULT ''",
                    "dedup_key": "TEXT DEFAULT ''",
                    "last_heartbeat": "TEXT DEFAULT ''",
                }
                for column, definition in migrations.items():
                    if column not in columns:
                        safe_column = quote_identifier(column, allowed=migrations)
                        self.db.execute(
                            f"ALTER TABLE route_events ADD COLUMN {safe_column} {definition}"
                        )
                self.db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_route_events_dedup ON route_events(dedup_key, created_at DESC)"
                )
                self.db.commit()
        except BaseException:
            if self.db is not None:
                self.db.close()
                self.db = None
            raise

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # nosec B110 -- destructor must never raise  # noqa: BLE001, S110
            pass

    def existing(self, session_id: str, message_hash: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM route_events WHERE session_id=? AND message_hash=?",
            (session_id, message_hash),
        ).fetchone()

    def active_for_session(self, session_id: str, action: str = "dispatch_swarm") -> list[sqlite3.Row]:
        return list(self.db.execute(
            """SELECT * FROM route_events
               WHERE session_id=? AND action=? AND run_id<>''
                 AND status IN ('submitted','running','completed')
               ORDER BY created_at DESC LIMIT 8""",
            (session_id, action),
        ))

    def recent_for_session(
        self,
        session_id: str,
        action: str,
        since: datetime,
        *,
        completed_only: bool = False,
        message_marker: str = "",
        dedup_key: str = "",
    ) -> sqlite3.Row | None:
        conditions = ["session_id=?", "action=?", "run_id<>''", "created_at>=?"]
        params: list[Any] = [session_id, action, since.isoformat(timespec="seconds")]
        if completed_only:
            conditions.append("status='completed'")
        else:
            conditions.append("status IN ('submitted','running','completed')")
        if message_marker:
            conditions.append("LOWER(message_excerpt) LIKE ?")
            params.append(f"%{message_marker.lower()}%")
        if dedup_key:
            conditions.append("(dedup_key=? OR dedup_key='')")
            params.append(dedup_key)
        # Conditions are fixed literals; only the bound params vary per query.
        rows = self.db.execute(
            f"SELECT * FROM route_events WHERE {' AND '.join(conditions)} "  # nosec B608 -- fixed literals, values bound
            "ORDER BY created_at DESC",
            params,
        ).fetchall()
        if not dedup_key:
            return rows[0] if rows else None
        for row in rows:
            stored_key = str(row["dedup_key"] or "")
            if stored_key == dedup_key:
                return row
            if stored_key:
                continue
            try:
                stored_decision = json.loads(row["decision_json"])
            except (TypeError, json.JSONDecodeError):
                stored_decision = {}
            if not isinstance(stored_decision, dict):
                # A parseable non-object decision_json (e.g. a JSON array) is
                # as unusable as a corrupt one; treat it as empty so the
                # dedup-key comparison below cannot raise AttributeError.
                stored_decision = {}
            stored_targets = extract_target(str(row["message_excerpt"] or ""))
            if not stored_targets and stored_decision.get("target"):
                stored_targets = [(
                    str(stored_decision.get("target_type") or "unknown"),
                    str(stored_decision.get("target") or ""),
                )]
            candidate_key = _build_dedup_key(
                str(row["session_id"] or ""),
                stored_targets,
                str(stored_decision.get("intent") or "custom"),
            )
            if candidate_key == dedup_key:
                return row
        return None

    def insert(
        self,
        session_id: str,
        platform: str,
        message_hash: str,
        message: str,
        decision: RouteDecision,
        origin: dict[str, str] | None = None,
        dedup_key: str = "",
    ) -> str:
        event_id, _created = self.insert_or_existing(
            session_id, platform, message_hash, message, decision,
            origin=origin, dedup_key=dedup_key,
        )
        return event_id

    def insert_or_existing(
        self,
        session_id: str,
        platform: str,
        message_hash: str,
        message: str,
        decision: RouteDecision,
        origin: dict[str, str] | None = None,
        dedup_key: str = "",
    ) -> tuple[str, bool]:
        """Insert a route event, returning ``(event_id, created)``.

        ``created`` is False when a concurrent hook for the same
        session+message won the race between the caller's ``existing()`` check
        and this insert.  Callers must reuse that event id and must NOT
        dispatch a second run for the same message.
        """
        event_id = str(uuid.uuid4())
        now = utc_now()
        origin = origin or {}
        if not dedup_key:
            targets = extract_target(message)
            if not targets and decision.target:
                targets = [(decision.target_type, decision.target)]
            dedup_key = _build_dedup_key(session_id, targets, decision.intent)
        values = (
            event_id, session_id, platform, message_hash, message[:500], decision.route,
            decision.action, dedup_key, json.dumps(asdict(decision), ensure_ascii=False),
            str(origin.get("platform") or ""), str(origin.get("chat_id") or ""),
            str(origin.get("thread_id") or ""), str(origin.get("user_id") or ""),
            now, now,
        )
        with file_lock(self.path):
            try:
                self.db.execute(
                    """INSERT INTO route_events
                       (route_event_id,session_id,platform,message_hash,message_excerpt,route,action,dedup_key,
                        decision_json,delivery_platform,delivery_chat_id,delivery_thread_id,
                        delivery_user_id,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                self.db.commit()
            except sqlite3.IntegrityError:
                # A concurrent hook may have inserted the same session/hash.
                # Reuse that event so only one downstream run is created.
                existing = self.db.execute(
                    "SELECT route_event_id FROM route_events WHERE session_id=? AND message_hash=?",
                    (session_id, message_hash),
                ).fetchone()
                self.db.rollback()
                if existing:
                    return str(existing["route_event_id"]), False
                raise
        return event_id, True

    def update(self, event_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        allowed = {
            "run_id", "request_id", "runner_pid", "status", "result_delivered",
            "error", "delivery_platform", "delivery_chat_id", "delivery_thread_id",
            "delivery_user_id", "proactive_delivered", "delivery_attempts",
            "delivery_error", "last_delivery_at", "runner_restarts",
            "quality_status", "updated_at", "last_heartbeat",
        }
        if set(fields) - allowed:
            raise ValueError("unsupported route state field")
        assignments = ", ".join(
            f"{quote_identifier(key, allowed=allowed)}=?" for key in fields
        )
        with file_lock(self.path):
            # Identifiers are validated against the ``allowed`` whitelist by
            # quote_identifier; only values are interpolated as parameters.
            self.db.execute(
                f"UPDATE route_events SET {assignments} WHERE route_event_id=?",  # nosec B608 -- whitelisted identifiers
                (*fields.values(), event_id),
            )
            self.db.commit()

    def pending_notifications(self, max_attempts: int = 10) -> list[sqlite3.Row]:
        return list(self.db.execute(
            """SELECT * FROM route_events
               WHERE action='dispatch_swarm' AND run_id<>''
                 AND proactive_delivered=0 AND result_delivered=0
                 AND delivery_attempts<?
               ORDER BY created_at ASC LIMIT 50""",
            (max_attempts,),
        ))

    def pending_content_notifications(self, max_attempts: int = 10) -> list[sqlite3.Row]:
        return list(self.db.execute(
            """SELECT * FROM route_events
               WHERE action IN ('dispatch_article','dispatch_video','dispatch_company') AND run_id<>''
                 AND proactive_delivered=0 AND result_delivered=0
                 AND delivery_attempts<?
               ORDER BY created_at ASC LIMIT 50""",
            (max_attempts,),
        ))


def _parse_json_output(output: str) -> dict[str, Any]:
    text = (output or "").strip()
    # Every caller treats the result as an object (``.get(...)``), so a
    # top-level JSON array/scalar must not be returned verbatim: fall through
    # to the per-line scan and, failing that, raise the same error as a
    # non-JSON body instead of handing back a value that crashes the caller.
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"command did not return a JSON object: {text[-500:]}")


def swarm_command(config: dict[str, Any], *args: str, timeout: int = 30) -> dict[str, Any]:
    # 2026-09-18 (W1-a/D-27): ``router_config.swarm_db``(v1 墓碑库位)已删除,
    # 不再直接索引该键(缺键会 KeyError)。v1 执行/逻辑库面已退役,这里退化到
    # v2 活库位,保持"缺键不崩"。
    cmd = [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarmctl.py"),
        "--db", config.get("swarm_db") or config.get("swarm_v2_db", ""),
        *args,
        "--json",
    ]
    proc = subprocess.run(cmd, cwd=config["swarm_repo"], capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"swarmctl exited {proc.returncode}")
    return _parse_json_output(proc.stdout)


#: D-16.2: 安全线 v1 入口的停用说明 —— v1 库位已是墓碑目录,而安全线 v2 分支
#: 代码未落库(仅存于归档补丁)。本批只在 v1 入口加护栏,**不实现** v2 分支。
V1_SECURITY_LINE_DISABLED = (
    "v1 已停用;安全线 v2 分支未落库,见 "
    "~/workspace/swarm-progress/archive-gray1/gray1-wip.patch"
)

#: D-25(2026-09-17):v1 **执行面**整体退役 —— 蜂群 `scripts/swarm_runner.py` /
#: `scripts/agent_worker.py`、公司 `swarm_hermes_executor.py`(opencode/hermes
#: chat)、`swarm_native_executor.py` 与 `router_config.executor` 键均已删除。
#: 命中 `dispatch_swarm` 的请求从此 fail-closed 拒绝:v1 没有执行器了,
#: 接单也只会挂死。要真跑,走 v2 市场 + `swarmctl worker --agent … [--agent-runtime]`。
V1_EXECUTION_SURFACE_RETIRED = (
    "v1 执行面已退役(D-25):swarm_runner.py / agent_worker.py / "
    "swarm_hermes_executor.py / swarm_native_executor.py 均已删除;"
    "蜂群任务改走 v2 市场 + `swarmctl worker --agent … --judge-by …`"
)


def _v1_swarm_db_unavailable(config: dict[str, Any]) -> str | None:
    """Return a loud reason when the v1 swarm DB is a tombstone/unusable.

    2026-09-18 (W1-a/D-27): ``router_config.json:swarm_db`` 键已作为纯废键删除,
    故这里的 ``.get`` 缺键分支就是常态(返回明确原因,不抛 KeyError)。该 v1 库位
    自 M0.2 起是墓碑目录。安全线 v1 入口在提交/启动前显式拒绝,替代 sqlite 裸错
    (``unable to open database file``)。
    """
    raw = config.get("swarm_db")
    if not isinstance(raw, str) or not raw.strip():
        return f"router_config.swarm_db 未配置;{V1_SECURITY_LINE_DISABLED}"
    if not Path(raw).is_file():
        return f"v1 swarm DB 不可用({raw});{V1_SECURITY_LINE_DISABLED}"
    return None


# ── v2 蜂群灰度接入 (M5 灰度接入 2: 内容线; 默认关, 未命中/异常一律回原路径) ──
#
# 复用批 1 的 `swarm_v2_gray` / `swarm_v2_db` / `swarm_v2_agent` /
# `swarm_v2_judge` 配置键与口径:
#   * `enabled=false`(缺省)⇒ 本模块不导入 v2 代码、不发起任何 v2 CLI 调用;
#     内容派发保持既有 `launch_content_job(... content_hermes_executor.py
#     --job-dir)` 逐字不变。
#   * 灰度判定优先复用 v2 侧 `src.swarm_v2.company_router` 的纯函数
#     (`GrayPolicy` / `gray_decision` / `route_key_of` / `gray_factor`),
#     仅在 v2 模块不可导入时退回等价本地实现(公式一致性由测试锁定)。
#   * 任一前置条件不满足(开关 / run_types / ratio / 库位 / 身份 / 自判 /
#     来源标识)或 v2 CLI 非零退出 ⇒ 记录回退原因并落回原内容路径;绝不丢任务。
V2_GRAY_DEFAULT_TOKEN_BUDGET = 100000
V2_GRAY_DEFAULT_EST_TOKENS = 100000
V2_GRAY_DEFAULT_BASE_PRIORITY = 0
V2_GRAY_DEFAULT_POLL_INTERVAL = 5.0

#: 公司 route → v2 run_type(v2 `value_params.RUN_TYPES` = vuln|content|ops)。
#: 内容三子线(article/video/company)统一进 content,子类走 focus_params。
_V2_RUN_TYPE_BY_ROUTE = {
    "security": "vuln", "research": "ops",
    "article": "content", "video": "content", "company": "content",
    "dev": "dev",
}
#: 公司 intent → v2 task_type(v2 `verdicts.TASK_TYPES`;内容线默认 custom)。
_V2_TASK_TYPE_BY_INTENT = {
    "recon": "scan", "exploit": "exploit", "report": "report",
    "analyze": "analyze", "research": "research", "custom": "custom",
}
#: v2 `run_create.INTENTS` 闭集(内容线固定 custom;子类在 focus_params)。
_V2_RUN_INTENTS = frozenset({
    "recon", "exploit", "analyze", "defend", "report", "research", "custom",
})

# ── W15-b②:预算/轮数按任务复杂度(纯函数 + 三处同源)────────────────────────
#
# 动机(实测,2026-09-19):6.4KB 任务书连跑三单 —— 单 3 = 5 轮吃 104,526 token,
# 单 1 = 6 轮 67,731 ⇒ ~11.3k–20.9k token/轮;而改前的 `--max-turns 12` ∧
# `--token-budget 100000` 在 12 轮里必然撞墙(`stop_reason=budget_exceeded`)。
#
# 三处同源 = 公司侧发布(`v2 run create --token-budget`)、挂单(`market publish
# --est`)与拉起 worker(`--max-turns`/`--max-tokens-budget`),全部读同一个
# `v2_task_plan(...)` 结果;计划随 `focus_params.budget_plan` 落库,拉起侧优先
# 复用(进程内登记 → 库内 focus_params),取不到才回退到**改前固定口径**
# (空消息会把任务误判成最小档 ⇒ 不猜,宁可保守)。
#: run_type → 蜂群权限档位(content=write;安全/research=exec;dev=dev)。
_V2_PERMISSION_BY_RUN_TYPE = {
    "content": "write", "vuln": "exec", "ops": "exec", "dev": "dev",
}
#: 轮数上夹 = 蜂群该档硬顶(**跨仓对拍锁测试**:test_w15_budget_plan.py 对拍
#: `src.swarm_v2.agent_runtime.HARD_MAX_TURNS_BY_PERMISSION`,改一处必红)。
_V2_HARD_MAX_TURNS = {"read-only": 12, "write": 12, "exec": 24, "dev": 40}
#: 分档起点(复杂度 → (max_turns, 名义 token))。任务书字符数按 UTF-8 字节
#: 语义的 `len(message)`(公司消息本来就是 str,按字符数计)。
_V2_BUDGET_TIERS = (
    (2048, 12, 100000),      # <2KB
    (8192, 18, 180000),      # 2–8KB
    (24576, 24, 280000),     # 8–24KB
    (None, 24, 360000),      # >24KB(24 = exec 档硬顶)
)
#: 每轮 token 下限(下夹依据;实测 11.3k/20.9k 每轮 ⇒ 取 15k)。
_V2_BUDGET_MIN_PER_TURN = 15000
#: 预算上夹缺省(config `swarm_v2_budget_cap` 可覆盖)。
_V2_BUDGET_CAP_DEFAULT = 400000
#: 发布计划进程内登记(run_id → plan);拉起侧优先复用,避免"发布/拉起不同值"。
_V2_BUDGET_PLANS: dict[str, dict[str, Any]] = {}
_V2_BUDGET_PLANS_MAX = 512

#: 安全线 / research 线灰度身份(**新增可配键**;D-28)。活库当前**没有** vuln 线
#: 身份(content-writer-1/dev-executor-1 等 4 个身份均非 vuln),故缺省留空 ⇒
#: 灰度前置不满足 ⇒ 不命中,回落 fail-closed。**不写死身份名。**
_V2_SECURITY_AGENT_KEY = "swarm_v2_security_agent"
_V2_SECURITY_JUDGE_KEY = "swarm_v2_security_judge"
#: 安全线产物目录(config 可配;缺省 = content-jobs 的兄弟目录 security-jobs)。
_V2_SECURITY_JOB_DIR_KEY = "swarm_v2_security_job_dir"


def _v2_security_identities(config: dict[str, Any]) -> tuple[str, str]:
    """Return (agent, judge) for the security line; "" when unset."""
    agent = str(config.get(_V2_SECURITY_AGENT_KEY) or "").strip()
    judge = str(config.get(_V2_SECURITY_JUDGE_KEY) or "").strip()
    return agent, judge


# ── W11-b:research 线独立提交口(submit_research_v2) ─────────────────────────
#
# 事实(2026-09-19):`research` 路由仍走 `dispatch_swarm`,而该 action 在 D-25 后
# 只剩 fail-closed 的 v1 退役文案 ⇒ 公司定时调研需求打不到 v2 市场。本批新增
# `submit_research_v2`(与 `submit_security_v2` 同构),research 路由改走它。
# **身份/产物根/启动档位各自独立键位**(三条线互不串档,锁测试锁定):
#   * content 线:`swarm_v2_agent`/`swarm_v2_judge`,write,content-jobs;
#   * security 线:`swarm_v2_security_agent`/`swarm_v2_security_judge`,exec,security-jobs;
#   * research 线:`swarm_v2_research_agent`/`swarm_v2_research_judge`,exec,research-jobs。
# 缺省留空 ⇒ 灰度前置不满足 ⇒ 响亮拒绝(生产行为逐字不变,闸仍 `dispatch_research=false`)。
_V2_RESEARCH_AGENT_KEY = "swarm_v2_research_agent"
_V2_RESEARCH_JUDGE_KEY = "swarm_v2_research_judge"
#: research 线产物目录(config 可配;缺省 = content-jobs 的兄弟目录 research-jobs)。
_V2_RESEARCH_JOB_DIR_KEY = "swarm_v2_research_job_dir"
#: research 线 worker 启动档位(与安全线同构:调研任务书可声明命令面/MCP 面能力)。
_V2_RESEARCH_WORKER_PERMISSION = "exec"

#: research 线闸未开时给出的**开闸命令原文**(公司侧闸 = router_config.dispatch_research;
#: 需主代理裁决后才执行 —— 本批不执行、不改配置)。
RESEARCH_GATE_OPEN_COMMAND = (
    "python3 -c \"import json,pathlib; "
    "p=pathlib.Path('automation/router_config.json'); "
    "c=json.loads(p.read_text(encoding='utf-8')); "
    "c['dispatch_research']=True; "
    "g=c.setdefault('swarm_v2_gray',{}); "
    "g['run_types']=sorted(set(g.get('run_types',[]))|{'ops'}); "
    "p.write_text(json.dumps(c,ensure_ascii=False,indent=2)+chr(10),encoding='utf-8')\""
)
#: 闸关(= 生产现状)时的响亮拒绝文案:讲清"已迁 v2 + 闸未开 + 开闸命令",
#: 不再是 v1 退役、无路可走的旧口径。保留旧子串以便既有断言仍成立。
RESEARCH_LINE_MIGRATED_TO_V2 = (
    "research 线已迁 v2 市场(submit_research_v2);自动分发闸未开"
    "(dispatch_research=false)。开闸命令 = " + RESEARCH_GATE_OPEN_COMMAND +
    "(同时需配置 swarm_v2_research_agent / swarm_v2_research_judge 身份,否则灰度不命中)"
)
#: 闸开但灰度/身份前置不满足(或提交失败)时的 fail-closed 文案(同样含"已迁 v2")。
RESEARCH_V2_LINE_UNAVAILABLE = (
    "research 线已迁 v2 市场(submit_research_v2);本次未提交(灰度/身份前置不满足)"
    "⇒ fail-closed 交回主 Agent(不是 v1 退役、无路可走)"
)


def _v2_research_identities(config: dict[str, Any]) -> tuple[str, str]:
    """Return (agent, judge) for the research line; "" when unset."""
    agent = str(config.get(_V2_RESEARCH_AGENT_KEY) or "").strip()
    judge = str(config.get(_V2_RESEARCH_JUDGE_KEY) or "").strip()
    return agent, judge



def _v2_gray_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def v2_gray_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize the optional ``swarm_v2_gray`` block (batch-1 keys).

    Returns a stable dict even when the block is absent (older deployment) or
    hand-edited into a bad shape; a missing/malformed block reads as disabled,
    which is exactly the pre-v2 behavior.
    """
    raw = config.get("swarm_v2_gray") if isinstance(config, dict) else None
    if not isinstance(raw, dict):
        raw = {}

    def _str_list(value: Any) -> list:
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, (list, tuple, set, frozenset)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _text(key: str) -> str:
        return str(config.get(key) or "").strip() if isinstance(config, dict) else ""

    return {
        "enabled": bool(raw.get("enabled", False)),
        "run_types": _str_list(raw.get("run_types", [])),
        "task_types": _str_list(raw.get("task_types", [])),
        "ratio_pct": _v2_gray_int(raw.get("ratio_pct", 0), 0),
        "client_source": str(raw.get("client_source") or "").strip(),
        "token_budget": _v2_gray_int(
            raw.get("token_budget"), V2_GRAY_DEFAULT_TOKEN_BUDGET),
        "est_tokens": _v2_gray_int(
            raw.get("est_tokens"), V2_GRAY_DEFAULT_EST_TOKENS),
        "base_priority": _v2_gray_int(
            raw.get("base_priority"), V2_GRAY_DEFAULT_BASE_PRIORITY),
        "poll_interval": V2_GRAY_DEFAULT_POLL_INTERVAL,
        "db": _text("swarm_v2_db"),
        "agent": _text("swarm_v2_agent"),
        "judge": _text("swarm_v2_judge"),
    }


def _v2_budget_mode(config: dict[str, Any]) -> str:
    """`swarm_v2_budget_mode`(缺省 `auto`;未知值 ⇒ 响亮拒绝,不静默当 auto)。"""
    raw = str(config.get("swarm_v2_budget_mode") or "auto").strip().lower()
    if raw not in {"auto", "fixed"}:
        raise ValueError(
            f"swarm_v2_budget_mode ∈ auto|fixed;实得 {raw!r}"
            "(未知模式不猜:auto=按复杂度分档,fixed=改前固定口径)")
    return raw


def _v2_budget_cap(config: dict[str, Any]) -> int:
    cap = _v2_gray_int(config.get("swarm_v2_budget_cap"), _V2_BUDGET_CAP_DEFAULT)
    if cap <= 0:
        raise ValueError(
            f"swarm_v2_budget_cap 须为正整数;实得 {cap!r}(上夹不能是负/零)")
    return cap


def _v2_fixed_budget_plan(config: dict[str, Any], *, run_type: str) -> dict[str, Any]:
    """改前固定口径(回归锁):轮数 12(dev 40)+ 灰度块的 token_budget/est_tokens。

    显式 `config['max_turns']`/`config['token_budget']` 可覆盖(仍受该档硬顶
    上夹);**不做** auto 的上下夹,以保证与 W15 之前逐字一致。
    """
    cfg = v2_gray_config(config)
    permission = _V2_PERMISSION_BY_RUN_TYPE.get(run_type)
    if permission is None:
        raise ValueError(f"未知 run_type: {run_type!r}(闭集 "
                         f"{sorted(_V2_PERMISSION_BY_RUN_TYPE)})")
    preset_turns = _V2_DEV_MAX_TURNS if permission == "dev" else 12
    max_turns = _v2_gray_int(config.get("max_turns"), preset_turns)
    hard_cap = _V2_HARD_MAX_TURNS[permission]
    why = [f"fixed 模式(改前口径):max_turns={max_turns}"]
    if max_turns < 1:
        max_turns = 1
        why.append("max_turns 下限 1")
    if max_turns > hard_cap:
        why.append(f"显式 max_turns 超 {permission} 档硬顶 {hard_cap} ⇒ 压到硬顶")
        max_turns = hard_cap
    token_budget = _v2_gray_int(config.get("token_budget"), cfg["token_budget"])
    if token_budget <= 0:
        raise ValueError(f"token_budget 须为正整数;实得 {token_budget!r}")
    return {
        "max_turns": max_turns,
        "token_budget": token_budget,
        "est_tokens": cfg["est_tokens"],
        "why": "; ".join(why),
    }


def v2_task_plan(config: dict[str, Any], *, message: str, task_book: dict | None,
                 run_type: str) -> dict[str, Any]:
    """按任务复杂度给出 (max_turns, token_budget, est_tokens, why)(纯函数)。

    复杂度信号(全部来自已有输入,不猜):任务书字符数、`exec_criteria` 条数、
    `required_capabilities` 是否声明、`runtime_brief` 是否声明。

    分档起点(`_V2_BUDGET_TIERS`):<2KB→12 轮、2–8KB→18 轮、8–24KB→24 轮、
    >24KB→24 轮;判据 ≥4 条 **或** 声明 `mcp` 能力 ⇒ 上一档。
    **下夹** `token_budget ≥ max_turns × 15000`;**上夹** `≤ swarm_v2_budget_cap`。
    轮数上夹 = 蜂群该档硬顶(`_V2_HARD_MAX_TURNS`,跨仓对拍锁定)。

    `swarm_v2_budget_mode="fixed"` ⇒ 走 :func:`_v2_fixed_budget_plan`(与改前
    逐字一致);任何未配置/非法输入都必须响亮失败,不静默降级。
    """
    mode = _v2_budget_mode(config)
    if mode == "fixed":
        return _v2_fixed_budget_plan(config, run_type=run_type)
    cfg = v2_gray_config(config)
    permission = _V2_PERMISSION_BY_RUN_TYPE.get(run_type)
    if permission is None:
        raise ValueError(f"未知 run_type: {run_type!r}(闭集 "
                         f"{sorted(_V2_PERMISSION_BY_RUN_TYPE)})")
    cap = _v2_budget_cap(config)
    hard_cap = _V2_HARD_MAX_TURNS[permission]

    size = len(message or "")
    tier_idx = len(_V2_BUDGET_TIERS) - 1
    for idx, (bound, _turns, _budget) in enumerate(_V2_BUDGET_TIERS):
        if bound is not None and size < bound:
            tier_idx = idx
            break
    why = [f"任务书 {size} 字节 → 档 {tier_idx}({_V2_BUDGET_TIERS[tier_idx][1]} 轮起点)"]

    criteria = security_exec_criteria(message, task_book)
    capabilities = security_required_capabilities(message, task_book)
    brief = bool(task_book.get("runtime_brief")) if isinstance(task_book, dict) else False
    if not brief:
        brief = bool(security_declared_task_book(message).get("runtime_brief"))
    escalate = (bool(criteria) and len(criteria) >= 4) \
        or ("mcp" in set(capabilities or ()))
    if escalate and tier_idx < len(_V2_BUDGET_TIERS) - 1:
        tier_idx += 1
        why.append("判据≥4 条或声明 mcp 能力 ⇒ 上一档")
    if brief:
        why.append("声明 runtime_brief")

    max_turns = min(_V2_BUDGET_TIERS[tier_idx][1], hard_cap)
    if max_turns < _V2_BUDGET_TIERS[tier_idx][1]:
        why.append(f"{permission} 档硬顶 {hard_cap} 上夹")
    token_budget = _V2_BUDGET_TIERS[tier_idx][2]

    floor = max_turns * _V2_BUDGET_MIN_PER_TURN
    if token_budget < floor:
        why.append(f"下夹 {max_turns}×{_V2_BUDGET_MIN_PER_TURN}={floor}")
        token_budget = floor
    if token_budget > cap:
        why.append(f"上夹 swarm_v2_budget_cap={cap}")
        token_budget = cap
    # `est_tokens` = 市场 escrow 驱动项(`market publish --est` ⇒ escrow=ceil(est×1.3),
    # F1.1/L5#15),保持**配置口径**(缺省 100000)而不是复杂度放大的 token_budget:
    # 实测 2026-09-19 当日已承诺 187,488,若按 360k 预算发 escrow=468k 会撞
    # 蜂群硬编码 NFR1 日顶(500k)被拒发。复杂度放大的只是 worker/run 的**硬顶**;
    # escrow 估计值不动,任务才发得出去(fixed 模式本就同源同一配置值)。
    est_tokens = cfg["est_tokens"]
    why.append(f"est=配置口径 {est_tokens}(escrow=ceil(est×1.3),不随复杂度放大)")
    return {
        "max_turns": int(max_turns),
        "token_budget": int(token_budget),
        "est_tokens": int(est_tokens),
        "why": "; ".join(why),
    }


def _v2_register_budget_plan(run_id: str, plan: dict[str, Any]) -> None:
    """登记发布计划(进程内);超上限丢最旧,避免长驻进程无界增长。"""
    if len(_V2_BUDGET_PLANS) >= _V2_BUDGET_PLANS_MAX:
        for stale in list(_V2_BUDGET_PLANS)[:len(_V2_BUDGET_PLANS)
                                          - _V2_BUDGET_PLANS_MAX + 1]:
            _V2_BUDGET_PLANS.pop(stale, None)
    _V2_BUDGET_PLANS[run_id] = dict(plan)


def _v2_plan_from_focus_params(config: dict[str, Any],
                               run_id: str) -> dict[str, Any] | None:
    """从库内 `agent_tasks.focus_params.budget_plan` 复用计划(取不到 ⇒ None)。

    只读、失败即 None(不猜、不改库);拉起侧因此可跨进程拿到发布侧写下的计划。
    """
    db = str(config.get("swarm_v2_db") or "").strip()
    if not db or not Path(db).exists():
        return None
    try:
        con = sqlite3.connect(sqlite_uri(Path(db), mode="ro"), uri=True, timeout=1.0)
        try:
            row = con.execute(
                "SELECT focus_params FROM agent_tasks WHERE task_id=?", (run_id,)
            ).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError):
        return None
    if not row or not row[0]:
        return None
    try:
        focus = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    plan = focus.get("budget_plan") if isinstance(focus, dict) else None
    if not isinstance(plan, dict):
        return None
    if not {"max_turns", "token_budget", "est_tokens"} <= set(plan):
        return None
    return {k: plan[k] for k in ("max_turns", "token_budget", "est_tokens", "why")
            if k in plan}


def v2_worker_plan(config: dict[str, Any], run_id: str, *, run_type: str) -> dict[str, Any]:
    """拉起侧解析计划:进程内登记 > 库内 focus_params > 改前固定口径(保守回退)。"""
    plan = _V2_BUDGET_PLANS.get(run_id)
    if plan:
        return dict(plan)
    plan = _v2_plan_from_focus_params(config, run_id)
    if plan:
        _v2_register_budget_plan(run_id, plan)
        return dict(plan)
    return _v2_fixed_budget_plan(config, run_type=run_type)


def _v2_plan_argv(plan: dict[str, Any]) -> tuple[str, str, str]:
    """(token_budget, est_tokens, max_turns) 的字符串形;三处同一来源。"""
    return (str(int(plan["token_budget"])), str(int(plan["est_tokens"])),
            str(int(plan["max_turns"])))


def _v2_route_key(client_source: str, message: str) -> str:
    return hashlib.sha256(f"{client_source}|{message}".encode()).hexdigest()[:16]


def _v2_gray_factor(route_key: str) -> float:
    digest = hashlib.sha256(route_key.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / 2**64 * 100.0


def _local_v2_gray_decision(*, run_types, task_types, ratio_pct, run_type,
                            task_type, route_key) -> dict[str, Any]:
    """Fallback mirror of v2 ``company_router.gray_decision`` (same formula).

    Only used when the v2 module cannot be imported; the equivalence test locks
    it to the upstream implementation.
    """
    factor = _v2_gray_factor(route_key)
    if run_type not in run_types:
        return {"path": "legacy_v1", "basis": "run_type_not_gray", "factor": factor}
    if task_types and task_type not in task_types:
        return {"path": "legacy_v1", "basis": "task_type_not_gray", "factor": factor}
    if factor >= ratio_pct:
        return {"path": "legacy_v1", "basis": "ratio_miss", "factor": factor}
    return {"path": "market_v2", "basis": "ratio_hit", "factor": factor}


def _load_v2_company_router(config: dict[str, Any]):
    """Best-effort import of the v2 decision port; ``None`` when unavailable."""
    repo = config.get("swarm_repo") if isinstance(config, dict) else None
    if not repo:
        return None
    try:
        import importlib

        repo_path = str(Path(repo).resolve())
        inserted = repo_path not in sys.path
        if inserted:
            sys.path.insert(0, repo_path)
        try:
            return importlib.import_module("src.swarm_v2.company_router")
        finally:
            if inserted and repo_path in sys.path:
                sys.path.remove(repo_path)
    except Exception:  # noqa: BLE001 -- reuse is best-effort; local mirror below
        return None


def _v2_gray_eval(config: dict[str, Any], *, run_type: str, task_type: str,
                  client_source: str, message: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the gray ratio, preferring the v2 module's pure functions."""
    route_key = _v2_route_key(client_source, message)
    module = _load_v2_company_router(config)
    if module is not None and hasattr(module, "GrayPolicy") \
            and hasattr(module, "gray_decision"):
        policy = module.GrayPolicy(
            run_types=frozenset(cfg["run_types"]),
            task_types=frozenset(cfg["task_types"]),
            ratio_pct=cfg["ratio_pct"],
        )
        decision = module.gray_decision(
            policy, run_type=run_type, task_type=task_type, route_key=route_key)
    else:
        decision = _local_v2_gray_decision(
            run_types=frozenset(cfg["run_types"]),
            task_types=frozenset(cfg["task_types"]),
            ratio_pct=cfg["ratio_pct"], run_type=run_type,
            task_type=task_type, route_key=route_key)
    decision = dict(decision)
    decision["route_key"] = route_key
    return decision


def v2_gray_decision(config: dict[str, Any], decision: RouteDecision, message: str,
                     *, agent: str | None = None, judge: str | None = None) -> dict[str, Any]:
    """Decide whether a company task should enter the v2 market.

    ``enabled=False`` returns immediately without importing v2 code or calling
    any CLI.  Every unmet precondition or evaluation error yields ``hit=False``
    plus a stable reason so the caller can record the fallback.  Never raises.

    ``agent``/``judge`` 覆盖 ``swarm_v2_agent``/``swarm_v2_judge``:安全线 /
    research 线用**专用身份键**(``_v2_security_identities``),缺省留空 ⇒ 视为
    灰度前置不满足(``v2_agent_not_configured`` / ``v2_judge_not_configured`` /
    ``v2_self_judge_forbidden``)⇒ 不命中回退。内容线不传 ⇒ 行为逐字不变。
    """
    cfg = v2_gray_config(config)
    if agent is not None:
        cfg["agent"] = str(agent).strip()
    if judge is not None:
        cfg["judge"] = str(judge).strip()
    result: dict[str, Any] = {
        "hit": False,
        "enabled": cfg["enabled"],
        "reason": "",
        "run_type": "",
        "task_type": "",
        "policy": {
            "run_types": list(cfg["run_types"]),
            "task_types": list(cfg["task_types"]),
            "ratio_pct": cfg["ratio_pct"],
        },
    }
    if not cfg["enabled"]:
        result["reason"] = "v2_gray_disabled"
        return result
    run_type = _V2_RUN_TYPE_BY_ROUTE.get(getattr(decision, "route", ""), "")
    task_type = "custom" if run_type == "content" else \
        _V2_TASK_TYPE_BY_INTENT.get(getattr(decision, "intent", ""), "custom")
    result["run_type"] = run_type
    result["task_type"] = task_type
    if not run_type:
        result["reason"] = "route_not_applicable"
        return result
    if not cfg["run_types"]:
        result["reason"] = "v2_gray_run_types_empty"
        return result
    if cfg["ratio_pct"] <= 0:
        result["reason"] = "v2_gray_ratio_zero"
        return result
    if run_type not in cfg["run_types"]:
        result["reason"] = "run_type_not_gray"
        return result
    if not cfg["db"]:
        result["reason"] = "v2_db_not_configured"
        return result
    if not cfg["agent"]:
        result["reason"] = "v2_agent_not_configured"
        return result
    if not cfg["judge"]:
        result["reason"] = "v2_judge_not_configured"
        return result
    if cfg["agent"] == cfg["judge"]:
        result["reason"] = "v2_self_judge_forbidden"
        return result
    if not cfg["client_source"]:
        result["reason"] = "v2_client_source_not_configured"
        return result
    try:
        evaluated = _v2_gray_eval(
            config, run_type=run_type, task_type=task_type,
            client_source=cfg["client_source"], message=message, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 -- a decision failure must fall back
        result["reason"] = f"v2_gray_decision_error: {type(exc).__name__}"
        return result
    result["reason"] = str(evaluated.get("basis") or "ratio_miss")
    result["factor"] = evaluated.get("factor")
    result["route_key"] = evaluated.get("route_key", "")
    if evaluated.get("path") == "market_v2":
        result["hit"] = True
    return result


def _v2_subprocess_env(cmd: list[str]) -> dict[str, str] | None:
    """v2 子进程环境(盐兜底)。

    发布路径要 `SWARM_CLIENT_SALT`(fail-closed:未设置即拒 client 发布)。调用方可能是
    cron/systemd/其它 launcher,不保证继承了登录 shell 的环境变量;故这里在**进程环境缺失**时
    从 `~/.company-env`(600,`KEY=value`)读一次。
    两处都没有 ⇒ 返回 None(子进程沿用当前环境):v2 侧照旧 fail-closed 拒绝,不静默降级/不伪造盐。
    """
    if os.environ.get("SWARM_CLIENT_SALT"):
        return None
    env_file = Path.home() / ".company-env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("SWARM_CLIENT_SALT="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return {**os.environ, "SWARM_CLIENT_SALT": value}
    except OSError:
        return None
    return None


def v2_swarm_command(config: dict[str, Any], *args: str, timeout: int = 30) -> dict[str, Any]:
    """Run a v2 `swarmctl` subcommand against the v2 live DB.

    `swarmctl` short-circuits the `v2`/`worker`/`company` namespaces before
    argparse, so the v2 DB flag is appended after the subcommand arguments
    (both `v2 run create` and `market publish` accept `--db` there).  The v1
    global `--db`/`swarm_db` is intentionally not reused.
    """
    cmd = [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarmctl.py"),
        *args,
        "--db", config["swarm_v2_db"],
        "--json",
    ]
    proc = subprocess.run(
        cmd, cwd=config["swarm_repo"], capture_output=True, text=True,
        timeout=timeout, check=False, env=_v2_subprocess_env(cmd))
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip()
            or f"swarmctl v2 exited {proc.returncode}")
    return _parse_json_output(proc.stdout)


# ── v1 外部执行面退役 (D-25) ──────────────────────────────────────────
# 本块原有 `submit_security` / `runner_role_counts` / `build_runner_cmd` /
# `launch_runner` 四个函数,是把公司任务交给 v1 蜂群(line)执行面的完整接线:
# submit_security 写 v1 库 → build_runner_cmd 拼出 `swarm_runner.py
# --executor-command <外部 agent 执行器>` → launch_runner 起进程。
# 该执行面(蜂群 scripts/swarm_runner.py、scripts/agent_worker.py、公司
# swarm_hermes_executor.py(opencode/hermes chat)、swarm_native_executor.py)
# 已于 2026-09-17 按「执行面自给、不外包外部 agent」口径整体删除;
# 命中 dispatch_swarm 的请求现在由下面的分支 fail-closed 拒绝并说明。
# 真实执行面 = v2 市场 + `swarmctl worker --agent … --judge-by … [--agent-runtime]`。
# ────────────────────────────────────────────────────────────────────


def content_job_path(config: dict[str, Any], run_id: str) -> Path:
    value = str(run_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError(f"invalid content run id: {value!r}")
    root = Path(config["content_job_dir"]).resolve()
    candidate = root / value
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"content run escapes job root: {value!r}") from exc
    if candidate.is_symlink():
        raise ValueError(f"content run directory may not be a symlink: {value!r}")
    return candidate


def launch_content_job(
    config: dict[str, Any],
    run_id: str,
    *,
    route: str = "",
    message: str = "",
    session_id: str = "",
    platform: str = "",
) -> int:
    job_dir = content_job_path(config, run_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    request_path = job_dir / "request.json"
    if route and message:
        locked_atomic_write_text(
            request_path,
            json.dumps({
                "run_id": run_id,
                "route": route,
                "message": message,
                "session_id": session_id,
                "platform": platform,
                "created_at": utc_now(),
            }, ensure_ascii=False, indent=2),
        )
    if not request_path.exists():
        raise RuntimeError(f"content job request missing: {request_path}")

    log_path = job_dir / "executor.log"
    executor_env, _dropped = scrub_environment()
    executor_env = apply_worker_proxy(executor_env, resolve_worker_proxy(config))
    executor_env["COMPANY_ROUTER_BYPASS"] = "1"
    executor_env["HERMES_SESSION_SOURCE"] = "tool"
    executor_env["HERMES_WRITE_SAFE_ROOT"] = str(job_dir.resolve())
    executor_env["TERMINAL_CWD"] = str(job_dir.resolve())
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [sys.executable, config["content_executor"], "--job-dir", str(job_dir)],
            cwd=str(HERE.parent),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=executor_env,
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


def _v2_content_intent(decision: RouteDecision) -> str:
    intent = str(getattr(decision, "intent", "") or "custom")
    return intent if intent in _V2_RUN_INTENTS else "custom"


#: 内容子线 → 强制交付清单(声明式产物;content 判定器按 `content_verify.files` 核验)
_V2_CONTENT_DELIVERABLES: dict[str, list[str]] = {
    "article": ["draft.md", "draft-humanized.md", "qa-report.md"],
    "company": ["task-report.md", "result.json"],
    "video": ["video-script.md", "storyboard.md", "production-plan.md"],
}
#: 随任务下发的公司规范(内建运行时 path jail 根 = 产物目录 ⇒ 读不到公司仓库)
_V2_CONTENT_SPECS: dict[str, list[str]] = {
    "article": ["operations/business-lines/article-production.md",
                "marketing/article-quality-constraints.md"],
    "company": ["Home.md", "operations/agent-roster.md"],
    "video": ["operations/business-lines/video-production.md",
              "strategy/video-production-strategy.md"],
}
#: 单份规范内联上限(超出截断并如实标注)
_V2_SPEC_EXCERPT_CHARS = 4000


def _v2_spec_excerpts(route: str) -> list[tuple[str, str]]:
    """读该内容子线的规范正文(节选);缺/读不到 ⇒ 明确标注,不假装已下发。"""
    root = HERE.parent
    out: list[tuple[str, str]] = []
    for rel in _V2_CONTENT_SPECS.get(route, []):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            out.append((rel, "(规范文件不可读;按通用质量要求执行并在 answer 中说明)"))
            continue
        if len(text) > _V2_SPEC_EXCERPT_CHARS:
            text = text[:_V2_SPEC_EXCERPT_CHARS] + "\n…（节选，超出部分略）"
        out.append((rel, text))
    return out


def build_runtime_brief(decision: RouteDecision, message: str, job_dir: Path) -> str:
    """公司侧拼给蜂群**内建 agent 运行时**的自包含任务书(D-22;F14 执行面自给)。

    为什么规范要内联:内建运行时的 path jail 根 = `--repo-root`(= 本任务产物目录),
    它读不到公司仓库里的规范/素材 ⇒ 规范正文必须随任务下发,否则"换执行面"等于把
    内容质量要求整段丢掉。产物清单同样随任务声明,供 content 判定器按声明核验。
    """
    route = str(getattr(decision, "route", "") or "")
    deliverables = _V2_CONTENT_DELIVERABLES.get(route, ["draft.md"])
    lines = [
        "你是公司内容产线的执行体，由蜂群内建 agent 运行时承载。直接完成任务，不要只写计划。",
        "",
        f"用户任务：{message}",
        f"内容子线：{route or 'unknown'}",
        f"产物目录：{job_dir}（只能在此目录内读写）",
        "",
        "可用工具：fs.list / fs.read / fs.write / fs.edit（路径须相对产物目录）。",
        "产物目录初始为空；公司仓库文件不在你的可见范围内（本节已内联全部必需规范），",
        "不要尝试列出/读取公司仓库路径，直接开始写文件；先落产物骨架再迭代打磨。",
        "无网络、无 shell 写操作、无外部 CLI —— 需要外部资料而实现不了时，如实说明并标注「未获取」，不得臆造。",
        "",
        "强制交付（按顺序，全部落在产物目录内）：",
    ]
    for idx, name in enumerate(deliverables, 1):
        lines.append(f"  {idx}) {job_dir / name}")
    lines.append("  若任务明确要求排版/封面，在产物目录内一并产出对应文件（如 draft-formatted.md）并复核。")
    lines += [
        "",
        "交付方式（硬规则）：",
        "- 必须用 fs.write 把产物写进产物目录；**只给 answer 不算完成** —— 判定器只认文件写入的变更摘要。",
        "- 你的**第一次输出必须是 fs.write 工具调用**；全部强制产物写完之前，不要输出 answer。",
        "- 第一步就直接落文件（先骨架后补全），每写完一个产物再写下一个；不要反复探查目录。",
        '- 例：{"tool_call": {"tool": "fs.write", "args": {"path": "draft.md", "content": "……"}}}',
        "- answer 只用于收尾：产物绝对路径 + 质量门结论 + 仍需人工决定的事项。",
    ]
    lines += ["", "必须遵循的公司规范（原文随任务下发）："]
    for rel, text in _v2_spec_excerpts(route):
        lines += [f"--- {rel} ---", text, ""]
    lines += [
        "边界：",
        "- 只写产物目录内的文件；不修改公司仓库其它任何文件；",
        "- 不执行公众号推送/草稿箱写入/公开发布等外部动作；",
        "- 不编造链接、数据、测试或已完成动作；无法核验的内容明确标注；",
        "- 最终 answer 给出产物绝对路径、质量门结论与仍需人工决定的事项。",
    ]
    return "\n".join(lines)


def submit_content_v2(
    config: dict[str, Any],
    *,
    decision: RouteDecision,
    message: str,
    session_id: str,
    platform: str,
    gray: dict[str, Any],
) -> dict[str, Any]:
    """Publish one content task into the v2 market (gray hit only).

    Writes a v2 run (`v2 run create`) then publishes the task (`market
    publish`).  Any non-zero CLI exit raises; the caller turns that into the
    original content path, so a failed v2 submit never drops the task.  The
    content subtype (article/video/company) travels in ``focus_params`` rather
    than expanding the v2 ``task_type`` closed set.
    """
    cfg = v2_gray_config(config)
    run_type = "content"
    task_type = "custom"
    run_id = f"company-{run_type}-{uuid.uuid4().hex[:12]}"
    intent = _v2_content_intent(decision)
    target = str(getattr(decision, "target", "") or "company-internal")
    route = str(getattr(decision, "route", "") or "")
    plan = v2_task_plan(config, message=message, task_book=None, run_type=run_type)
    _v2_register_budget_plan(run_id, plan)
    job_dir = content_job_path(config, run_id)
    focus = json.dumps(
        {
            "content_route": route,
            "task_intent": intent,
            "company_task": message,
            "company_session_id": session_id,
            "company_platform": platform,
            "client_source": cfg["client_source"],
            # W15-b②:发布侧算出的预算/轮数计划,拉起侧原样复用(单一来源)。
            "budget_plan": plan,
            # 内建运行时的任务书(D-22):规范正文随任务下发 —— path jail 根 =
            # 产物目录,运行时读不到公司仓库里的规范文件。
            "runtime_brief": build_runtime_brief(decision, message, job_dir),
            # 声明式产物清单 ⇒ content 判定器按声明核验(空声明只要求"有过写动作")
            "content_verify": {"files": list(
                _V2_CONTENT_DELIVERABLES.get(route, []))},
        },
        ensure_ascii=False, sort_keys=True)
    by = cfg["agent"]
    v2_swarm_command(
        config, "v2", "run", "create",
        "--run-id", run_id,
        "--run-type", run_type,
        "--intent", intent,
        "--target-type", "unknown",
        "--target", target,
        "--token-budget", _v2_plan_argv(plan)[0],
        "--by", by,
    )
    publication = v2_swarm_command(
        config, "market", "publish",
        "--run-id", run_id,
        "--run-type", run_type,
        "--task-type", task_type,
        "--publisher", "client",
        "--est", _v2_plan_argv(plan)[1],
        "--base", str(cfg["base_priority"]),
        "--by", by,
        "--client-source", cfg["client_source"],
        "--focus", focus,
        # Pin the market task id to the run id: the stdin executor builds its job
        # directory as content_job_dir/<task_id>, so this keeps it exactly at
        # content_job_path(config, run_id) and leaves the existing
        # refresh_session_content_jobs() status lookup working unchanged.
        "--task-id", run_id,
    )
    task_id = str(publication.get("task_id") or run_id)
    return {
        "run_id": run_id,
        "request_id": task_id,
        "status": "submitted",
        "_v2_dispatch": "v2",
        "_v2_run_type": run_type,
        "_v2_task_type": task_type,
        "_v2_task_id": task_id,
    }


def build_v2_content_worker_cmd(config: dict[str, Any], run_id: str,
                                *, plan: dict[str, Any] | None = None) -> list:
    """Build the v2 content worker command (pure; testable).

    执行面自给(D-22/F14):worker 用蜂群**内建 agent_runtime**(write 档)执行任务,
    不再把执行外包给外部 agent CLI(``content_hermes_executor.py`` 内部会起
    ``hermes chat``,而 content 判定器只认内建运行时的 ``agent_trace`` 写行)。
    ``--repo-root`` = 本 run 的产物目录 ⇒ 产物落 ``content-jobs/<run_id>/``,且
    path jail 限定在该目录内(内建运行时默认根 = 蜂群仓库,不可用于公司任务)。
    身份取自 ``swarm_v2_agent``/``swarm_v2_judge``;``--max-tasks 1`` = 一次派发一个任务。

    W15-b②:*plan* 缺省 = 改前固定口径(直接调用/无发布计划时逐字不变);
    拉起侧 :func:`launch_v2_content_worker` 传入发布侧计划 ⇒ 三处同一来源。
    """
    cfg = v2_gray_config(config)
    job_dir = content_job_path(config, run_id)
    plan = plan if plan is not None else _v2_fixed_budget_plan(
        config, run_type="content")
    token_budget, _est, max_turns = _v2_plan_argv(plan)
    return [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarmctl.py"),
        "worker",
        "--db", config["swarm_v2_db"],
        "--agent", cfg["agent"],
        "--judge-by", cfg["judge"],
        "--agent-runtime",
        "--permission", "write",
        "--repo-root", str(job_dir),
        # 轮数/预算 = 发布计划(内容产出多文件;默认 8 轮实测写不完)
        "--max-turns", max_turns,
        "--max-tokens-budget", token_budget,
        "--poll-interval", str(cfg["poll_interval"]),
        "--max-tasks", "1",
    ]


def launch_v2_content_worker(config: dict[str, Any], run_id: str) -> int:
    """Launch the v2 content worker detached (mirrors ``launch_content_job``)."""
    value = str(run_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError(f"invalid swarm v2 content run id: {value!r}")
    job_dir = content_job_path(config, run_id)
    job_dir.mkdir(parents=True, exist_ok=True)      # 内建运行时在产物目录内写文件
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"swarm-v2-content-{value}.log"
    plan = v2_worker_plan(config, run_id, run_type="content")
    cmd = build_v2_content_worker_cmd(config, run_id, plan=plan)
    worker_env, _dropped = scrub_environment()
    worker_env = apply_worker_proxy(worker_env, resolve_worker_proxy(config))
    worker_env["COMPANY_ROUTER_BYPASS"] = "1"
    worker_env["HERMES_SESSION_SOURCE"] = "tool"
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(job_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=worker_env,
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


# ── 安全线 / research 线 v2 派发 (D-28;按新 head 重写,非合并归档补丁) ──────
#
# 与内容线(`submit_content_v2` / `launch_v2_content_worker`)同构,差异:
#   * run_type 由 `_V2_RUN_TYPE_BY_ROUTE` 取(security→vuln / research→ops);
#     task_type 由 `_V2_TASK_TYPE_BY_INTENT` 取。
#   * 身份取安全线专用配置键(缺省留空 ⇒ 灰度不命中,见 `_v2_security_identities`)。
#   * focus_params **显式**声明 vuln 判定面口径(`vuln_verify`):vuln 判定器
#     `p5-exec-verify` 读 `focus_params.exec_criteria`(声明式 argv+expect_exit
#     真跑);W3-b 起**任务书声明判据**时走 `mode="exec-criteria"` 并原样携带
#     `focus_params.exec_criteria`;任务书**未声明**判据 ⇒ 逐字保持 M1.5
#     **绑定记录**口径(不声明 argv、不执行、判定结论由外部提交)。
#     **绝不**硬编码 `{"argv": [...], "expect_exit": 0}` 之类假判据。
# 异常一律上抛,由 `dispatch_swarm` 记录回退原因并继续 fail-closed(不丢任务)。


def _v2_security_run_intent(decision: RouteDecision, task_type: str) -> str:
    """v2 `run create --intent` 取值。

    需求书写作「``--intent <v2 task_type>``」;但 `run_create.INTENTS` 不接受
    `scan`(recon→scan)。故:task_type ∈ 闭集时直接用,否则退回公司 intent
    (∈ 闭集),再否则 custom —— 始终不越 v2 闭集。
    """
    if task_type in _V2_RUN_INTENTS:
        return task_type
    intent = str(getattr(decision, "intent", "") or "")
    return intent if intent in _V2_RUN_INTENTS else "custom"


def _v2_security_job_root(config: dict[str, Any]) -> Path:
    configured = str(config.get(_V2_SECURITY_JOB_DIR_KEY) or "").strip()
    if configured:
        return Path(configured)
    content_dir = str(config.get("content_job_dir") or "").strip()
    if content_dir:
        return Path(content_dir).parent / "security-jobs"
    return HERE.parent / "operations" / "runtime" / "security-jobs"


def security_job_path(config: dict[str, Any], run_id: str) -> Path:
    """Path-jail a security run id under the security job root (mirrors content)."""
    value = str(run_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError(f"invalid security run id: {value!r}")
    root = _v2_security_job_root(config).resolve()
    candidate = root / value
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"security run escapes job root: {value!r}") from exc
    if candidate.is_symlink():
        raise ValueError(f"security run directory may not be a symlink: {value!r}")
    return candidate


def _v2_research_job_root(config: dict[str, Any]) -> Path:
    configured = str(config.get(_V2_RESEARCH_JOB_DIR_KEY) or "").strip()
    if configured:
        return Path(configured)
    content_dir = str(config.get("content_job_dir") or "").strip()
    if content_dir:
        return Path(content_dir).parent / "research-jobs"
    return HERE.parent / "operations" / "runtime" / "research-jobs"


def research_job_path(config: dict[str, Any], run_id: str) -> Path:
    """Path-jail a research run id under the research job root (mirrors security)."""
    value = str(run_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError(f"invalid research run id: {value!r}")
    root = _v2_research_job_root(config).resolve()
    candidate = root / value
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"research run escapes job root: {value!r}") from exc
    if candidate.is_symlink():
        raise ValueError(f"research run directory may not be a symlink: {value!r}")
    return candidate


# ── 安全线声明式判据(W3-b;exec-criteria 口径) ────────────────────────────
#
# 判据来源 = **任务书声明**,不是代码生成:公司侧只做"发布前形状预检"并原样
# 透传,执行期仍由 v2 判定器 `exec_verify` 按白名单/path jail 复核(双重门)。
# **绝不在代码里硬编码 `{"argv": [...], "expect_exit": 0}`** —— 那种判据必然
# 自证通过,等于把判定变成走过场(红旗)。
#
# 判据生成规则(写任务书的人照此声明;离线任务只用产物自身可核验的断言):
#   1. 断言"产物里确实有某个声明事实"   → grep -F -q '<fact>' <artifact>
#      (用 -F 字面匹配:`.` 在正则里匹配任意字符,`grep -q com.waze` 会被产物里
#       的 `com/waze` 误命中;W3 排练实测踩到过这个坑,负例因此一度假通过。)
#   2. 断言"产物与声明指纹一致"          → sha256sum <artifact>(对拍声明值)
#   3. 断言"两份产物逐字节一致"          → cmp <artifact-a> <artifact-b>
#   4. 断言"产物非空/行数符合声明"        → wc -l <artifact>(对拍声明值)
#   反例(不可判定,禁止写进任务书):"跑一遍某工具再下结论"、"人工确认无误"、
#   "结果看起来正确" —— 这些没有可核验的 argv+expect_exit,写成判据即红旗。
#   示例(APK 离线静态分析,产物 report.md 内写包名与结论行):
#     [{"argv": ["grep", "-F", "-q", "package=com.example.app", "report.md"],
#       "expect_exit": 0},
#      {"argv": ["grep", "-F", "-q", "offline-analysis=done", "report.md"],
#       "expect_exit": 0}]
#   负例纪律:判据必须能**红** —— 把产物改坏一个字节(如包名改一位)后同一
#   判据须转 fail ⇒ 判定 rejected。做不到这一点的判据是走过场,不得声明。
#
#: 判据命令白名单(发布前预检;单一来源 = swarm 侧
#: `src/swarm_v2/exec_verify.WHITELIST`,由回归测试跨仓对拍锁定,不新增命令面)。
_V2_EXEC_WHITELIST = frozenset({
    "cat", "grep", "diff", "cmp", "sha256sum", "wc", "head", "tail",
    "sort", "uniq", "stat", "file", "ls", "sleep",
})
#: 判据参数字符集(与 exec_verify._ARG_RE 同口径;无空格/引号/shell 元字符)
_V2_EXEC_ARG_RE = re.compile(r"[A-Za-z0-9._/=@,:+%-]{1,256}")
#: 单判据硬超时(秒;与 exec_verify.HARD_TIMEOUT_SECONDS 一致)
_V2_EXEC_HARD_TIMEOUT = 60
#: 任务书里的机器可读声明:```json { ... } ```(只认带 exec_criteria/runtime_brief 的块)
_V2_TASK_BOOK_FENCE_RE = re.compile(r"```(?:json)?[ \t]*\n(.*?)\n[ \t]*```", re.S)


def security_declared_task_book(message: str) -> dict[str, Any]:
    """从安全线任务书正文里解析可选的机器可读声明(```json 代码块)。

    只接受**对象**且至少含 `exec_criteria`/`runtime_brief` 之一;坏 JSON / 非对象
    / 无关块一律跳过(视为未声明 ⇒ 回落 binding-record,**不猜**)。自然语言正文
    里的裸 `exec_criteria` 字样不会被当作声明(必须走 fenced JSON),避免误触发。
    """
    text = str(message or "")
    for block in _V2_TASK_BOOK_FENCE_RE.findall(text):
        try:
            data = json.loads(block)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and ("exec_criteria" in data
                                       or "runtime_brief" in data):
            return data
    return {}


def validate_security_exec_criteria(raw: Any) -> list[dict[str, Any]]:
    """严格校验任务书声明的 `exec_criteria`;不合法 ⇒ ValueError(不静默降级)。

    形状与 v2 `exec_verify.parse_criteria` 同口径:`[{"argv": [...非空字符串...],
    "expect_exit": 0..255, "timeout": 正整数}]`,argv[0] ∈ 白名单、禁绝对路径与
    `..`、参数 ≤256 且字符集白名单内。声明了**坏**判据 = 任务书缺陷,发布前即拒
    (绝不"改成 binding-record 假装没声明",也绝不编造 argv 让判定能过)。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("exec_criteria 须为非空数组(每个判据 = per-task argv)")
    criteria: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"exec_criteria[{i}] 须为对象({{argv, expect_exit?, timeout?}})")
        extra = set(item) - {"argv", "expect_exit", "timeout"}
        if extra:
            raise ValueError(f"exec_criteria[{i}] 含未声明键 {sorted(extra)}")
        argv = item.get("argv")
        if (not isinstance(argv, list) or not argv
                or not all(isinstance(a, str) for a in argv)):
            raise ValueError(f"exec_criteria[{i}].argv 须为非空字符串数组")
        if argv[0] not in _V2_EXEC_WHITELIST:
            raise ValueError(
                f"exec_criteria[{i}].argv[0]={argv[0]!r} 不在判定器白名单 "
                f"{sorted(_V2_EXEC_WHITELIST)}")
        for a in argv:
            if len(a) > 256:
                raise ValueError(f"exec_criteria[{i}] 参数超长(>256): {a!r}")
            if not _V2_EXEC_ARG_RE.fullmatch(a):
                raise ValueError(f"exec_criteria[{i}] 参数含白名单外字符: {a!r}")
            if a.startswith("/"):
                raise ValueError(f"exec_criteria[{i}] 禁绝对路径: {a!r}")
            if ".." in a:
                raise ValueError(f"exec_criteria[{i}] 禁路径越界(..): {a!r}")
        expect = item.get("expect_exit", 0)
        if isinstance(expect, bool) or not isinstance(expect, int) or not 0 <= expect <= 255:
            raise ValueError(f"exec_criteria[{i}].expect_exit 须为 0..255 整数: {expect!r}")
        timeout = item.get("timeout", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError(f"exec_criteria[{i}].timeout 须为正整数秒: {timeout!r}")
        criteria.append({"argv": list(argv), "expect_exit": expect,
                         "timeout": min(timeout, _V2_EXEC_HARD_TIMEOUT)})
    return criteria


def security_exec_criteria(
        message: str,
        task_book: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    """取任务书声明的判据;未声明 ⇒ None(回落 binding-record)。

    显式 `task_book` 参数优先于正文 fenced JSON(供编程调用/测试);两者都无
    `exec_criteria` 键 ⇒ None。有键但不合法 ⇒ 直接上抛(发布前 fail-closed)。
    """
    book = dict(task_book) if isinstance(task_book, dict) else {}
    for k, v in security_declared_task_book(message).items():
        book.setdefault(k, v)
    if "exec_criteria" not in book:
        return None
    return validate_security_exec_criteria(book.get("exec_criteria"))


# ── W6/G3:安全线任务书的能力声明(required_capabilities) ──────────────────
#
# 事实(2026-09-18):`write` 档工具面只有 fs.*;要求"跑命令/用 MCP 工具"的任务
# 在 `write` 档 worker 上会 12 轮空转后判负,发布方无从得知档位能力边界。
# 裁定:任务书要求命令面/MCP 面 ⇒ **必须同时声明** `required_capabilities`
# (闭集),随 `focus_params` 下发;v2 worker 认领后执行前确定性校验,不覆盖 ⇒
# 零 token 拒跑并写明该用哪个档位。闭集单一来源 = swarm 侧
# `agent_runtime.CAPABILITIES`(发布前形状预检 + 回归跨仓对拍锁定)。
#: 能力闭集(与 swarm 侧 `agent_runtime.CAPABILITIES` 对拍)
_V2_CAPABILITIES = frozenset({"command", "mcp"})
#: 任务书正文提及这些工具名 ⇒ 必须派生对应能力(命令面 / MCP 面)
_V2_CAPABILITY_TOOL_HINTS = (
    ("sh.run", "command"),
    ("mcp.call", "mcp"),
    ("mcp.list", "mcp"),
)


def validate_security_required_capabilities(raw: Any) -> list[str]:
    """严格校验任务书声明的能力;非法/闭集外 ⇒ ValueError(发布前 fail-closed)。

    闭集外的能力无法被 worker 的档位映射校验(不猜),故发布前即拒,绝不静默
    丢弃(丢弃 = 发布方以为已声明,worker 却不知道 ⇒ 又回到撞墙)。
    """
    if not isinstance(raw, list) or not raw \
            or not all(isinstance(x, str) and x for x in raw):
        raise ValueError("required_capabilities 须为非空字符串数组")
    unknown = sorted(set(raw) - _V2_CAPABILITIES)
    if unknown:
        raise ValueError(
            f"required_capabilities 含闭集外能力 {unknown}"
            f"(闭集 = {sorted(_V2_CAPABILITIES)})")
    return list(dict.fromkeys(raw))


def security_required_capabilities(
        message: str,
        task_book: dict[str, Any] | None = None) -> list[str] | None:
    """取任务书要求的能力;无 ⇒ None(向后兼容,不发 `required_capabilities`)。

    来源(合并去重):① 显式声明 `required_capabilities`(显式 task_book 或正文
    fenced JSON;闭集校验);② 任务书正文提及命令面/MCP 面工具名(`sh.run` /
    `mcp.call` / `mcp.list`)⇒ 派生对应能力 —— "要求用工具"即"必须声明能力"。
    """
    book = dict(task_book) if isinstance(task_book, dict) else {}
    for k, v in security_declared_task_book(message).items():
        book.setdefault(k, v)
    declared = None
    if "required_capabilities" in book:
        declared = validate_security_required_capabilities(book.get("required_capabilities"))
    text = str(book.get("runtime_brief") or message or "")
    derived = [cap for token, cap in _V2_CAPABILITY_TOOL_HINTS if token in text]
    merged = list(dict.fromkeys((declared or []) + derived))
    return merged or None


# ── W7-b:安全线 worker 启动档位(单一来源)+ 能力/档位对齐 + exec 开关门 ──────
#
# 用户批准的 batchW7 派工书 §2:安全线任务书声明的 required_capabilities 需要
# 命令面/MCP 面(⇒ exec 档),而公司侧安全线 worker 长期以 `--permission write`
# 启动(W6 §6-4 登记),两者不一致会让 worker 零 token 拒跑。裁定:
#   ① 安全线启动档位升为 exec(内容线一字不动);
#   ② 任务书生成器在**发布前**用同一张能力→档位映射校验对齐,不一致 ⇒ 显式
#      拒绝(不静默发布后被 worker 拒跑);
#   ③ `agent_runtime_exec` 开关=0 ⇒ exec 档启动在**起进程前**拒绝(三重门之②,
#      幂等文案含恢复路径);内容线/read-only/write 路径不受影响。
#: 安全线 worker 启动档位(**单一来源**;内容线仍是 write,不得混用)
_V2_SECURITY_WORKER_PERMISSION = "exec"
#: 档位偏序(与 swarm 侧 `agent_runtime._PERMISSION_ORDER` 同口径,跨仓对拍)
_V2_PERMISSION_ORDER = {"read-only": 0, "write": 1, "exec": 2, "dev": 3}
#: 能力 → 所需最低档位(**单一来源** = swarm 侧 `agent_runtime.CAPABILITY_PERMISSION`)
_V2_CAPABILITY_PERMISSION = {"command": "exec", "mcp": "exec"}
#: exec 档开关(与 swarm 侧 `agent_runtime.EXEC_SWITCH` 同名同义,跨仓对拍锁定)
_V2_EXEC_SWITCH = "agent_runtime_exec"
#: 开关关闭时的恢复路径(幂等文案展示;实际写库仍走 swarmctl,不在此处改库)
_V2_EXEC_SWITCH_RECOVERY = (
    "swarmctl switch on agent_runtime_exec --by <who> --reason <why>")


def capability_permission_gap(capabilities: Any, permission: str) -> list[str]:
    """声明的能力未被该档位覆盖 ⇒ 返回缺失能力列表(空 = 全覆盖)。

    非法档位/闭集外能力一律 ValueError(不猜);未声明能力 ⇒ 空列表(向后兼容)。
    """
    if permission not in _V2_PERMISSION_ORDER:
        raise ValueError(
            f"权限档位 ∈ {sorted(_V2_PERMISSION_ORDER)};实得 {permission!r}")
    missing: list[str] = []
    for cap in capabilities or []:
        need = _V2_CAPABILITY_PERMISSION.get(cap)
        if need is None:
            raise ValueError(f"required_capabilities 含闭集外能力 {cap!r}")
        if _V2_PERMISSION_ORDER[permission] < _V2_PERMISSION_ORDER[need]:
            missing.append(cap)
    return sorted(set(missing))


def security_worker_permission_gap(capabilities: Any,
                                   permission: str | None = None) -> list[str]:
    """安全线任务书声明的能力相对**安全线启动档位**的缺口(发布前校验口)。"""
    return capability_permission_gap(
        capabilities, permission if permission is not None
        else _V2_SECURITY_WORKER_PERMISSION)


def security_exec_switch_enabled(config: dict[str, Any]) -> bool:
    """安全线 exec 档开关是否打开(swarm `agent_runtime_exec`)。

    读**蜂群单一来源**(`swarmctl switch list --json`);缺行/未知 ⇒ False(视为关,
    fail-closed)。查询本身失败 ⇒ 异常上抛,由调用方 fail-closed(绝不静默放行)。
    """
    out = swarm_command(config, "switch", "list")
    for row in out.get("switches") or []:
        if isinstance(row, dict) and row.get("name") == _V2_EXEC_SWITCH:
            return int(row.get("enabled") or 0) == 1
    return False


def require_security_exec_switch(config: dict[str, Any]) -> None:
    """exec 档启动门(F14.2.3 三重门之②)。

    安全线启动档位 ≥ exec 时,`agent_runtime_exec` 开关必须=1;否则在**起任何
    进程/建任何目录之前**拒绝(幂等:重复调用同一配置得到同一拒绝,零副作用),
    文案含恢复路径。档位低于 exec(如内容线 write)直接放行,行为逐字不变。
    """
    if _V2_PERMISSION_ORDER[_V2_SECURITY_WORKER_PERMISSION] \
            < _V2_PERMISSION_ORDER["exec"]:
        return
    if security_exec_switch_enabled(config):
        return
    raise RuntimeError(
        f"安全线 worker 以 `{_V2_SECURITY_WORKER_PERMISSION}` 档启动,但开关 "
        f"`{_V2_EXEC_SWITCH}`=0(F14.2.3 三重门之②;默认 0)⇒ 拒绝启动(零进程/零"
        f"写入);恢复路径:`{_V2_EXEC_SWITCH_RECOVERY}`"
        f"(--db {config.get('swarm_v2_db', '')})")


# ── W5-b-1:exec-criteria 任务书的硬性交付要求(交付物必须落盘) ─────────────
#
# 用户裁定(2026-09-18):交付物必须落盘,判据按产物文件校验。声明了判据却
# 不要求产出被判据校验的文件,等于把判定变成走过场 ⇒ 任务书正文**必须写死**
# 这条要求,并给出"判据 ↔ 产物"示例对应。
_V2_SECURITY_DELIVERABLE_RULE = (
    "交付物必须用 fs.write 落盘到工作目录（判据按产物文件校验；"
    "只给 answer、不落盘 ⇒ 判负）。"
)
#: 判据 ↔ 产物示例(写任务书的人照此声明并产出对应文件)
_V2_SECURITY_CRITERIA_EXAMPLE = (
    '例：判据 ["grep","-F","<事实>","<产物文件>"] ⇒ 任务书必须要求用 '
    "fs.write 产出 <产物文件>"
)


def security_criteria_artifacts(criteria: list[dict[str, Any]]) -> list[str]:
    """从声明判据里提取被判据引用的产物文件(生成任务书要求用;不改判据)。

    只读命令(exec_verify 白名单)对产物文件断言;路径型参数即被判据校验的产物。
    仅用于把"必须产出哪些文件"写进任务书,不新增/不改写判据,也不参与校验。
    """
    out: list[str] = []
    for c in criteria:
        for a in (c.get("argv") or [])[1:]:
            if not a or a.startswith("-") or "=" in a or a.startswith("/"):
                continue
            if "." not in a:
                continue            # 无扩展名更像模式/选项值,不当作产物文件
            if a not in out:
                out.append(a)
    return out


def build_security_exec_deliverable_requirement(
        criteria: list[dict[str, Any]]) -> str:
    """生成安全线 exec-criteria 任务书的**硬性交付要求**正文(W5-b-1)。

    用户裁定:交付物必须落盘、判据按产物文件校验;本函数把它写死进任务书,并
    逐条给出"判据引用 `<产物文件>` ⇒ 必须用 fs.write 产出 `<产物文件>`"的对应。
    """
    artifacts = security_criteria_artifacts(criteria)
    lines = [
        "交付要求（硬规则；判定器 p5-exec-verify 按产物文件真跑声明判据）：",
        f"- {_V2_SECURITY_DELIVERABLE_RULE}",
        "- 判据 ↔ 产物对应：逐条判据引用的文件参数即必须产出的产物文件：",
    ]
    if artifacts:
        for name in artifacts:
            lines.append(
                f"  - 判据引用 `{name}` ⇒ 任务书必须要求用 fs.write 产出 `{name}`")
    else:
        lines.append("  - 判据未含可直接识别的产物文件名；逐条判据的文件参数"
                     "即产物文件，须用 fs.write 产出。")
    lines.append(f"  - {_V2_SECURITY_CRITERIA_EXAMPLE}")
    return "\n".join(lines)


def submit_security_v2(
    config: dict[str, Any],
    *,
    decision: RouteDecision,
    message: str,
    session_id: str,
    platform: str,
    gray: dict[str, Any],
    task_book: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one security/research task into the v2 market (gray hit only).

    Writes a v2 run (`v2 run create`) then publishes the task (`market
    publish`).  Any non-zero CLI exit raises; the caller records the fallback
    reason and continues down the fail-closed v1 path, so a failed v2 submit
    never drops the task.  Identity comes from the security-line config keys;
    the vuln judge face is declared explicitly via ``focus_params.vuln_verify``:
    ``mode="exec-criteria"`` + ``focus_params.exec_criteria`` when the task book
    declares criteria (W3-b), otherwise the verbatim M1.5 ``binding-record``
    fallback.  No ``argv``/``expect_exit`` is ever fabricated here.  When the
    task book asks for the command/MCP tool face (W6/G3), the corresponding
    ``focus_params.required_capabilities`` is declared so the v2 worker can
    deterministically refuse zero-token instead of spinning on a tier it cannot
    cover.
    """
    cfg = v2_gray_config(config)
    agent, _judge = _v2_security_identities(config)
    route = str(getattr(decision, "route", "") or "")
    run_type = _V2_RUN_TYPE_BY_ROUTE.get(route, "")
    if run_type not in {"vuln", "ops"}:
        raise ValueError(f"security/research v2 requires vuln|ops route; got {route!r}")
    task_type = _V2_TASK_TYPE_BY_INTENT.get(
        str(getattr(decision, "intent", "") or ""), "custom")
    intent = _v2_security_run_intent(decision, task_type)
    run_id = f"company-{run_type}-{uuid.uuid4().hex[:12]}"
    target = str(getattr(decision, "target", "") or "company-internal")
    # 判据来自任务书声明(显式 task_book 或正文 fenced JSON);缺 ⇒ None ⇒
    # 逐字保持 M1.5 binding-record(不执行、判定结论由外部提交)。声明了坏判据
    # 由 security_exec_criteria 直接上抛 ⇒ 调用方 fail-closed(不静默降级)。
    criteria = security_exec_criteria(message, task_book)
    # W6/G3:任务书要求命令面/MCP 面 ⇒ 必须同时声明 required_capabilities(闭集,
    # 发布前校验);随 focus_params 下发,由 v2 worker 执行前确定性校验档位覆盖。
    capabilities = security_required_capabilities(message, task_book)
    # W7-b:发布前把任务书声明的能力与**安全线启动档位**对齐;不一致 ⇒ 显式拒绝
    # (不静默发布:否则 worker 认领后因档位不覆盖而零 token 拒跑)。启动档位是
    # 单一来源常量,改它必须同步能力→档位映射,否则本闸当场拒。
    _gap = security_worker_permission_gap(capabilities)
    if _gap:
        _needed = sorted({_V2_CAPABILITY_PERMISSION[c] for c in _gap})
        raise ValueError(
            f"required_capabilities {_gap} 需要 {_needed} 档,但安全线 worker "
            f"启动档位 = {_V2_SECURITY_WORKER_PERMISSION};发布前拒绝(把启动档位"
            f"对齐到 {_needed})")
    # W15-b②:发布侧算一次计划,随 focus_params.budget_plan 下发并登记,
    # 拉起侧原样复用 ⇒ run create / market publish / worker 三处同一值。
    plan = v2_task_plan(config, message=message, task_book=task_book,
                        run_type=run_type)
    _v2_register_budget_plan(run_id, plan)
    declared_brief = None
    if isinstance(task_book, dict) and task_book.get("runtime_brief"):
        declared_brief = str(task_book["runtime_brief"])
    else:
        declared_brief = security_declared_task_book(message).get("runtime_brief")
        declared_brief = str(declared_brief) if declared_brief else None
    focus_body: dict[str, Any] = {
        "company_route": route,
        "task_intent": intent,
        "company_task": message,
        "company_session_id": session_id,
        "company_platform": platform,
        "client_source": cfg["client_source"],
    }
    if declared_brief:
        # 内建运行时 path jail 根 = 产物目录;任务书正文随任务下发(自包含)。
        focus_body["runtime_brief"] = declared_brief
    if capabilities:
        # 未声明 ⇒ 不发该键(向后兼容:既有任务 focus 形状逐字不变)
        focus_body["required_capabilities"] = capabilities
    if criteria is not None:
        focus_body["exec_criteria"] = criteria
        # W5-b-1:声明了判据 ⇒ 任务书正文必须写死"交付物必须落盘"硬要求,并给出
        # 判据 ↔ 产物示例对应。用户已裁定声明式 runtime_brief 逐字保留(不改写用户
        # 正文),故要求另随 `deliverable_requirement` 下发;未声明正文时用它作正文。
        _requirement = build_security_exec_deliverable_requirement(criteria)
        focus_body["deliverable_requirement"] = _requirement
        if not declared_brief:
            focus_body["runtime_brief"] = _requirement
        focus_body["vuln_verify"] = {
            "mode": "exec-criteria",
            "provider": "p5-exec-verify",
            "note": ("任务书声明判据 ⇒ 判定器按 exec_verify 白名单在产物根内真跑;"
                     "任一 fail/timeout/refused ⇒ rejected 或不判定;"
                     "交付物须用 fs.write 落盘(判据按产物文件校验)"),
        }
    else:
        # 见上:未声明判据 ⇒ 逐字保持现状(binding-record,无 argv / 无真跑)。
        focus_body["vuln_verify"] = {
            "mode": "binding-record",
            "provider": "p5-exec-verify",
            "note": (
                "vuln 判定器读 focus_params.exec_criteria;任务书未声明 "
                "⇒ 不执行、回落 M1.5 绑定记录"
            ),
        }
    focus_body["budget_plan"] = plan
    focus = json.dumps(focus_body, ensure_ascii=False, sort_keys=True)
    v2_swarm_command(
        config, "v2", "run", "create",
        "--run-id", run_id,
        "--run-type", run_type,
        "--intent", intent,
        "--target-type", "unknown",
        "--target", target,
        "--token-budget", _v2_plan_argv(plan)[0],
        "--by", agent,
    )
    publication = v2_swarm_command(
        config, "market", "publish",
        "--run-id", run_id,
        "--run-type", run_type,
        "--task-type", task_type,
        "--publisher", "client",
        "--est", _v2_plan_argv(plan)[1],
        "--base", str(cfg["base_priority"]),
        "--by", agent,
        "--client-source", cfg["client_source"],
        "--focus", focus,
        "--task-id", run_id,
    )
    task_id = str(publication.get("task_id") or run_id)
    return {
        "run_id": run_id,
        "request_id": task_id,
        "status": "submitted",
        "_v2_dispatch": "v2",
        "_v2_run_type": run_type,
        "_v2_task_type": task_type,
        "_v2_task_id": task_id,
    }


def build_v2_security_worker_cmd(config: dict[str, Any], run_id: str,
                                 *, plan: dict[str, Any] | None = None) -> list:
    """Build the v2 security/research worker command (pure; testable).

    Mirrors ``build_v2_content_worker_cmd``: 蜂群**内建 agent_runtime**,
    **不走**外部 `--executor-command`(该档已随 D-25 退役,v2 侧起即
    `ExecutorWorkerError`)。`--repo-root` = 本 run 的产物目录(内建运行时 path
    jail 根)。身份取安全线专用配置键;`--max-tasks 1` = 一次派发一个任务。

    W7-b:启动档位 = `_V2_SECURITY_WORKER_PERMISSION`(**exec**)—— 安全线任务书
    声明的命令面/MCP 面能力(`required_capabilities`)只有在 exec 档才被覆盖;
    内容线 `build_v2_content_worker_cmd` 仍是 write,两线不得混用。

    W15-b②:*plan* 缺省 = 改前固定口径(12 轮/灰度 token_budget);拉起侧传入
    发布计划 ⇒ run create / publish / worker 三处同一来源。
    """
    cfg = v2_gray_config(config)
    agent, judge = _v2_security_identities(config)
    job_dir = security_job_path(config, run_id)
    plan = plan if plan is not None else _v2_fixed_budget_plan(
        config, run_type=_V2_RUN_TYPE_BY_ROUTE["security"])
    token_budget, _est, max_turns = _v2_plan_argv(plan)
    return [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarmctl.py"),
        "worker",
        "--db", config["swarm_v2_db"],
        "--agent", agent,
        "--judge-by", judge,
        "--agent-runtime",
        "--permission", _V2_SECURITY_WORKER_PERMISSION,
        "--repo-root", str(job_dir),
        "--max-turns", max_turns,
        "--max-tokens-budget", token_budget,
        "--poll-interval", str(cfg["poll_interval"]),
        "--max-tasks", "1",
    ]


def launch_v2_security_worker(config: dict[str, Any], run_id: str) -> int:
    """Launch the v2 security/research worker detached (mirrors content).

    W7-b:起进程**之前**先过 exec 开关门(三重门之②):`agent_runtime_exec`=0 ⇒
    拒绝启动,零进程/零目录/零写入(恢复路径见 :func:`require_security_exec_switch`;
    内容线 `launch_v2_content_worker` 不受影响)。
    """
    value = str(run_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError(f"invalid swarm v2 security run id: {value!r}")
    require_security_exec_switch(config)          # 零副作用:拒绝先于 mkdir/Popen
    job_dir = security_job_path(config, run_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"swarm-v2-security-{value}.log"
    plan = v2_worker_plan(config, run_id, run_type="vuln")
    cmd = build_v2_security_worker_cmd(config, run_id, plan=plan)
    worker_env, _dropped = scrub_environment()
    worker_env = apply_worker_proxy(worker_env, resolve_worker_proxy(config))
    worker_env["COMPANY_ROUTER_BYPASS"] = "1"
    worker_env["HERMES_SESSION_SOURCE"] = "tool"
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(job_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=worker_env,
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


# ── W11-b:research 线 v2 提交口(与 submit_security_v2 同构) ────────────────
#
# 迁移动机:research 路由过去走 `dispatch_swarm` 的 fail-closed 分支(D-25 v1
# 执行面退役后无路可走)⇒ 公司定时调研需求打不到 v2 市场。本函数是 research
# 线**专属**提交口:run_type=ops(write 路径与安全线同构,vuln 判定面口径原样
# 复用 submit_security_v2 的 `vuln_verify` 声明),身份/产物根/启动档位独立。
#
# 闸/灰度过闸(函数内自证,便于直接调用测试):
#   ① 公司闸 `dispatch_research=false` ⇒ 响亮拒绝(文案含"已迁 v2"+开闸命令);
#   ② 灰度未命中(身份/run_types/ratio 任一不满足)⇒ 响亮拒绝;
#   ③ 声明判据非法 / 能力档位不覆盖 ⇒ ValueError(发布前 fail-closed)。
# 异常一律上抛,由调用方记录回退原因并继续 fail-closed(不丢任务)。
def submit_research_v2(
    config: dict[str, Any],
    *,
    decision: RouteDecision,
    message: str,
    session_id: str,
    platform: str,
    gray: dict[str, Any],
    task_book: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one research task into the v2 market (research-line port, W11-b).

    与 :func:`submit_security_v2` 同构:任务书构造 + 可选 ``exec_criteria`` +
    ``required_capabilities`` 派生 + 闸/灰度过闸 + 专属身份。**本函数只处理
    ``route='research'`(run_type='ops')**;security 仍走原提交口,行为逐字不变。
    """
    cfg = v2_gray_config(config)
    route = str(getattr(decision, "route", "") or "")
    if route != "research":
        raise ValueError(f"submit_research_v2 requires route='research'; got {route!r}")
    run_type = _V2_RUN_TYPE_BY_ROUTE.get(route, "")
    if run_type != "ops":
        raise ValueError(f"research v2 requires run_type='ops'; got {run_type!r}")
    # ① 公司闸:未开 ⇒ 响亮拒绝(不再"静默 deferred"给调用方,函数自证)。
    if not config.get("dispatch_research", False):
        raise RuntimeError(RESEARCH_LINE_MIGRATED_TO_V2)
    # ② 灰度闸:未命中 ⇒ 响亮拒绝(原因逐字带上,便于调用方记录)。
    if not gray.get("hit"):
        reason = str(gray.get("reason") or "v2_gray_not_selected")
        raise RuntimeError(f"{RESEARCH_V2_LINE_UNAVAILABLE} (v2_gray={reason})")
    agent, judge = _v2_research_identities(config)
    if not agent or not judge:
        raise RuntimeError(
            "research v2 requires swarm_v2_research_agent / swarm_v2_research_judge")
    task_type = _V2_TASK_TYPE_BY_INTENT.get(
        str(getattr(decision, "intent", "") or ""), "research")
    intent = _v2_security_run_intent(decision, task_type)
    run_id = f"company-{run_type}-{uuid.uuid4().hex[:12]}"
    target = str(getattr(decision, "target", "") or "company-internal")
    # 判据来自任务书声明;缺 ⇒ None ⇒ 逐字保持绑定记录口径(不编造 argv)。
    criteria = security_exec_criteria(message, task_book)
    capabilities = security_required_capabilities(message, task_book)
    _gap = capability_permission_gap(capabilities, _V2_RESEARCH_WORKER_PERMISSION)
    if _gap:
        _needed = sorted({_V2_CAPABILITY_PERMISSION[c] for c in _gap})
        raise ValueError(
            f"required_capabilities {_gap} 需要 {_needed} 档,但 research 线 worker "
            f"启动档位 = {_V2_RESEARCH_WORKER_PERMISSION};发布前拒绝(把启动档位"
            f"对齐到 {_needed})")
    # W15-b②:research 线同样"发布侧算一次计划、拉起侧复用"(与安全线同源函数)。
    plan = v2_task_plan(config, message=message, task_book=task_book,
                        run_type=run_type)
    _v2_register_budget_plan(run_id, plan)
    declared_brief = None
    if isinstance(task_book, dict) and task_book.get("runtime_brief"):
        declared_brief = str(task_book["runtime_brief"])
    else:
        declared_brief = security_declared_task_book(message).get("runtime_brief")
        declared_brief = str(declared_brief) if declared_brief else None
    focus_body: dict[str, Any] = {
        "company_route": route,
        "task_intent": intent,
        "company_task": message,
        "company_session_id": session_id,
        "company_platform": platform,
        "client_source": cfg["client_source"],
    }
    if declared_brief:
        focus_body["runtime_brief"] = declared_brief
    if capabilities:
        focus_body["required_capabilities"] = capabilities
    if criteria is not None:
        focus_body["exec_criteria"] = criteria
        _requirement = build_security_exec_deliverable_requirement(criteria)
        focus_body["deliverable_requirement"] = _requirement
        if not declared_brief:
            focus_body["runtime_brief"] = _requirement
        focus_body["vuln_verify"] = {
            "mode": "exec-criteria",
            "provider": "p5-exec-verify",
            "note": ("任务书声明判据 ⇒ 判定器按 exec_verify 白名单在产物根内真跑;"
                     "任一 fail/timeout/refused ⇒ rejected 或不判定;"
                     "交付物须用 fs.write 落盘(判据按产物文件校验)"),
        }
    else:
        focus_body["vuln_verify"] = {
            "mode": "binding-record",
            "provider": "p5-exec-verify",
            "note": (
                "vuln 判定器读 focus_params.exec_criteria;任务书未声明 "
                "⇒ 不执行、回落 M1.5 绑定记录"
            ),
        }
    focus_body["budget_plan"] = plan
    focus = json.dumps(focus_body, ensure_ascii=False, sort_keys=True)
    v2_swarm_command(
        config, "v2", "run", "create",
        "--run-id", run_id,
        "--run-type", run_type,
        "--intent", intent,
        "--target-type", "unknown",
        "--target", target,
        "--token-budget", _v2_plan_argv(plan)[0],
        "--by", agent,
    )
    publication = v2_swarm_command(
        config, "market", "publish",
        "--run-id", run_id,
        "--run-type", run_type,
        "--task-type", task_type,
        "--publisher", "client",
        "--est", _v2_plan_argv(plan)[1],
        "--base", str(cfg["base_priority"]),
        "--by", agent,
        "--client-source", cfg["client_source"],
        "--focus", focus,
        "--task-id", run_id,
    )
    task_id = str(publication.get("task_id") or run_id)
    return {
        "run_id": run_id,
        "request_id": task_id,
        "status": "submitted",
        "_v2_dispatch": "v2",
        "_v2_run_type": run_type,
        "_v2_task_type": task_type,
        "_v2_task_id": task_id,
    }


def build_v2_research_worker_cmd(config: dict[str, Any], run_id: str,
                                 *, plan: dict[str, Any] | None = None) -> list:
    """Build the v2 research worker command (pure; testable).

    与 :func:`build_v2_security_worker_cmd` 同构:蜂群内建 ``agent_runtime``,
    身份取 research 线专用配置键,``--repo-root`` = research 产物目录,
    启动档位 = ``_V2_RESEARCH_WORKER_PERMISSION``。内容线/安全线不共用本函数,
    三条线 argv 互不串档(锁测试锁定)。

    W15-b②:*plan* 缺省 = 改前固定口径(12 轮/灰度 token_budget);拉起侧传入
    发布计划 ⇒ 三处同一来源。
    """
    cfg = v2_gray_config(config)
    agent, judge = _v2_research_identities(config)
    job_dir = research_job_path(config, run_id)
    plan = plan if plan is not None else _v2_fixed_budget_plan(
        config, run_type=_V2_RUN_TYPE_BY_ROUTE["research"])
    token_budget, _est, max_turns = _v2_plan_argv(plan)
    return [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarmctl.py"),
        "worker",
        "--db", config["swarm_v2_db"],
        "--agent", agent,
        "--judge-by", judge,
        "--agent-runtime",
        "--permission", _V2_RESEARCH_WORKER_PERMISSION,
        "--repo-root", str(job_dir),
        "--max-turns", max_turns,
        "--max-tokens-budget", token_budget,
        "--poll-interval", str(cfg["poll_interval"]),
        "--max-tasks", "1",
    ]


def launch_v2_research_worker(config: dict[str, Any], run_id: str) -> int:
    """Launch the v2 research worker detached (mirrors security/content).

    exec 档启动同样先过 `agent_runtime_exec` 开关门(零副作用:拒绝先于
    mkdir/Popen);research 线的产物目录/日志/argv 全部独立。
    """
    value = str(run_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError(f"invalid swarm v2 research run id: {value!r}")
    require_security_exec_switch(config)          # 零副作用:拒绝先于 mkdir/Popen
    job_dir = research_job_path(config, run_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"swarm-v2-research-{value}.log"
    plan = v2_worker_plan(config, run_id, run_type="ops")
    cmd = build_v2_research_worker_cmd(config, run_id, plan=plan)
    worker_env, _dropped = scrub_environment()
    worker_env = apply_worker_proxy(worker_env, resolve_worker_proxy(config))
    worker_env["COMPANY_ROUTER_BYPASS"] = "1"
    worker_env["HERMES_SESSION_SOURCE"] = "tool"
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(job_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=worker_env,
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


# ── W14-b:dev 线 v2 提交口(submit_dev_v2)─────────────────────────────────
#
# 与 security/research 同构,但有两条硬差异:
#   * 默认**关**且不接受猜路径:`dispatch_dev=false`;`swarm_v2_dev_repo` 缺省为空
#     ⇒ 提交/启动**响亮拒绝**(绝不猜默认仓库);
#   * 启动档位 = dev(蜂群侧三重门:声明 dev ∧ `agent_runtime_exec`=1 ∧ `bwrap`),
#     轮数按 dev 档**硬顶 40**(不沿用 12)。
#
# 触发两条路(默认只走第一条):
#   ① 显式:`--route dev --dispatch --dev-repo …`(main() 直接调本模块,不过分类器);
#   ② 分类路由:仅当 `dev_route_enabled=true` 时 `classify_message` 才产出 dev 路由
#      (默认关 ⇒ 既有分类结果逐字不变,锁测试锁定)。
#
# F1 边界(dev 线结论):`test_command`/`files` 是**执行体必须知道**的声明(它要自己
# 跑命令、要写那些文件),故原样随 `focus_params` 下发、**不是**判据期望值;真值 =
# 判定器在沙箱内独立复跑的 exit code。因此 dev 线**不下发** `exec_criteria`
# (`exec_verify.JUDGE_PRIVATE_KEYS` 会在执行体视图里删掉它的期望值,放它只会让
# 执行体看不到本应看到的命令声明)。
_V2_DEV_AGENT_KEY = "swarm_v2_dev_agent"
_V2_DEV_JUDGE_KEY = "swarm_v2_dev_judge"
_V2_DEV_REPO_KEY = "swarm_v2_dev_repo"
_V2_DEV_ROUTE_ENABLED_KEY = "dev_route_enabled"
#: dev 线 worker 启动档位(**单一来源**;内容线 write / 安全线 exec 不得混用)
_V2_DEV_WORKER_PERMISSION = "dev"
#: dev 档轮数硬顶(与 swarm 侧 `agent_runtime.HARD_MAX_TURNS_DEV` 同值,跨仓对拍)
_V2_DEV_MAX_TURNS = 40

#: dev 线闸未开时的**开闸命令原文**(公司侧闸 = router_config.dispatch_dev;
#: 同时把 dev 并入灰度 run_types;本批不执行、不改活配置 —— 需主代理裁决)。
DEV_GATE_OPEN_COMMAND = (
    "python3 -c \"import json,pathlib; "
    "p=pathlib.Path('automation/router_config.json'); "
    "c=json.loads(p.read_text(encoding='utf-8')); "
    "c['dispatch_dev']=True; "
    "g=c.setdefault('swarm_v2_gray',{}); "
    "g['run_types']=sorted(set(g.get('run_types',[]))|{'dev'}); "
    "p.write_text(json.dumps(c,ensure_ascii=False,indent=2)+chr(10),encoding='utf-8')\""
)
#: 闸关(= 生产现状)时的响亮拒绝文案:讲清"已接线 v2 + 闸未开 + 开闸命令"。
DEV_LINE_DISABLED = (
    "dev 线已接线到蜂群 v2 市场(submit_dev_v2);自动分发闸未开"
    "(dispatch_dev=false)。开闸命令 = " + DEV_GATE_OPEN_COMMAND +
    "(另需配置 swarm_v2_dev_repo 指定被测仓库,否则提交会响亮拒绝——绝不猜默认仓库)"
)
#: 缺仓库位的响亮拒绝文案(绝不猜默认仓库)。
DEV_REPO_REQUIRED = (
    "dev 线拒绝提交:未指定被测仓库(swarm_v2_dev_repo 为空且 --dev-repo 缺省)"
    "—— dev 线绝不猜默认仓库。指定方式:CLI 加 `--dev-repo <已存在的仓库目录>`,"
    "或在 router_config.json 设 `swarm_v2_dev_repo`(零提交/零进程)"
)
#: 闸开但灰度/身份前置不满足(或提交失败)时的 fail-closed 文案。
DEV_V2_LINE_UNAVAILABLE = (
    "dev 线已接线到蜂群 v2 市场(submit_dev_v2);本次未提交"
    "(灰度/身份前置不满足)⇒ fail-closed 交回主 Agent"
)


def _v2_dev_identities(config: dict[str, Any]) -> tuple[str, str]:
    """Return (agent, judge) for the dev line; "" when unset."""
    agent = str(config.get(_V2_DEV_AGENT_KEY) or "").strip()
    judge = str(config.get(_V2_DEV_JUDGE_KEY) or "").strip()
    return agent, judge


def _normalize_dev_argv(value: Any) -> list[str] | None:
    """dev `test_command` → argv 数组(缺/空 ⇒ None,不编造)。

    接受 argv 数组,或 JSON 数组字符串,或空白分隔字符串(仅 CLI 便捷输入);
    **不做** shell 解析:执行/判定侧都是 `argv` 数组 + `shell=False`。
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"--dev-test-cmd 不是合法 JSON 数组: {exc}") from exc
        else:
            value = text.split()
    if not isinstance(value, (list, tuple)):
        raise ValueError("test_command 须为 argv 数组(或空白分隔/JSON 数组字符串)")
    argv = [str(item) for item in value if str(item).strip()]
    return argv or None


def _normalize_dev_files(value: Any) -> list[str] | None:
    """dev `files` → 相对路径数组(缺/空 ⇒ None,不编造)。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"--dev-files 不是合法 JSON 数组: {exc}") from exc
        else:
            value = [part for part in re.split(r"[,\s]+", text) if part]
    if not isinstance(value, (list, tuple)):
        raise ValueError("files 须为相对路径数组(或逗号/空白分隔/JSON 数组字符串)")
    files = [str(item).strip() for item in value if str(item).strip()]
    return files or None


def require_dev_exec_switch(config: dict[str, Any]) -> None:
    """dev 档启动门(F14.2.3 三重门之②,跨仓复用 `agent_runtime_exec`)。

    dev 档在蜂群侧复用 `agent_runtime_exec` 开关(与 exec 档同一把);本函数读
    **同一来源**(`security_exec_switch_enabled` → `swarmctl switch list`),只是
    把拒绝文案改成 dev 口径。缺开关 ⇒ 在起进程/建目录之前拒绝(零副作用)。
    """
    if security_exec_switch_enabled(config):
        return
    raise RuntimeError(
        f"dev 线 worker 以 `{_V2_DEV_WORKER_PERMISSION}` 档启动,但开关 "
        f"`{_V2_EXEC_SWITCH}`=0(F14.2.3 三重门之②;默认 0)⇒ 拒绝启动(零进程/"
        f"写入);恢复路径:`{_V2_EXEC_SWITCH_RECOVERY}`"
        f"(--db {config.get('swarm_v2_db', '')})"
    )


def submit_dev_v2(
    config: dict[str, Any],
    *,
    decision: RouteDecision,
    message: str,
    session_id: str,
    platform: str,
    gray: dict[str, Any],
    dev_repo: str | None = None,
    test_command: Any = None,
    files: Any = None,
) -> dict[str, Any]:
    """Publish one dev task into the v2 market (dev-line port, W14-b).

    三道前置门(任一不满足 ⇒ 响亮拒绝,零 CLI 调用/零进程):
      ① 公司闸 `dispatch_dev=false` ⇒ RuntimeError(含开闸命令);
      ② 灰度未命中(身份/run_types/ratio 任一不满足)⇒ RuntimeError;
      ③ 身份/repo 缺失或 repo 不存在 ⇒ RuntimeError/ValueError。
    `focus_params` **顶层**写 `test_command`(argv 数组)/`files`(相对路径数组);
    二者可缺(缺 ⇒ 只做产物复算/只做复跑,按 `dev_verify` 既有口径),**缺了不编造**。
    `task_type` 取 v2 闭集里的 `custom`:dev 工作 = 改代码 + 跑测试,不是
    scan/analyze/exploit/report 等专用桶;判定口径由 `run_type=dev` 绑定的
    `dev-trace-verify` 决定,故 `custom` 是语义正确的最小选择。
    **不下发** `exec_criteria`(见本节 F1 边界说明)。
    """
    cfg = v2_gray_config(config)
    route = str(getattr(decision, "route", "") or "")
    if route != "dev":
        raise ValueError(f"submit_dev_v2 requires route='dev'; got {route!r}")
    # ① 公司闸:未开 ⇒ 响亮拒绝(含"怎么开");先于任何 v2 CLI 调用。
    if not config.get("dispatch_dev", False):
        raise RuntimeError(DEV_LINE_DISABLED)
    # ② 灰度闸:未命中 ⇒ 响亮拒绝(原因逐字带上)。
    if not gray.get("hit"):
        reason = str(gray.get("reason") or "v2_gray_not_selected")
        raise RuntimeError(f"{DEV_V2_LINE_UNAVAILABLE} (v2_gray={reason})")
    # ③ 身份 + 仓库(专属键;缺省留空 ⇒ 拒绝,不猜)。
    agent, judge = _v2_dev_identities(config)
    if not agent or not judge:
        raise RuntimeError(
            "dev v2 requires swarm_v2_dev_agent / swarm_v2_dev_judge"
            "(缺省 = dev-executor-1 / dev-verifier-1;须为已注册身份)")
    repo_raw = dev_repo if dev_repo is not None else config.get(_V2_DEV_REPO_KEY)
    repo = str(repo_raw or "").strip()
    if not repo:
        raise ValueError(DEV_REPO_REQUIRED)
    repo_path = Path(repo).expanduser()
    if not repo_path.is_dir():
        raise ValueError(
            f"dev 线拒绝提交:被测仓库不存在或不是目录: {repo_path}"
            "(--dev-repo/swarm_v2_dev_repo;零提交/零进程)")
    argv = _normalize_dev_argv(test_command)
    declared_files = _normalize_dev_files(files)

    run_type = _V2_RUN_TYPE_BY_ROUTE.get(route, "")
    if run_type != "dev":
        raise ValueError(f"dev v2 requires run_type='dev'; got {run_type!r}")
    task_type = "custom"
    intent = "custom"
    run_id = f"company-{run_type}-{uuid.uuid4().hex[:12]}"
    target = str(getattr(decision, "target", "") or "company-internal")
    # W15-b②:dev 线同样走同一计划函数(轮数上夹 = dev 档硬顶 40)。
    plan = v2_task_plan(config, message=message, task_book=None, run_type=run_type)
    _v2_register_budget_plan(run_id, plan)
    focus_body: dict[str, Any] = {
        "company_route": route,
        "task_intent": intent,
        "company_task": message,
        "company_session_id": session_id,
        "company_platform": platform,
        "client_source": cfg["client_source"],
    }
    if argv:
        focus_body["test_command"] = argv
    if declared_files:
        focus_body["files"] = declared_files
    # 注意:**不写** exec_criteria —— dev 判据的真值 = 沙箱复跑 exit code,
    # 不是可抄的常量;执行体只需知道要跑什么命令、要写哪些文件。
    focus_body["budget_plan"] = plan
    focus = json.dumps(focus_body, ensure_ascii=False, sort_keys=True)
    v2_swarm_command(
        config, "v2", "run", "create",
        "--run-id", run_id,
        "--run-type", run_type,
        "--intent", intent,
        "--target-type", "unknown",
        "--target", target,
        "--token-budget", _v2_plan_argv(plan)[0],
        "--by", agent,
    )
    publication = v2_swarm_command(
        config, "market", "publish",
        "--run-id", run_id,
        "--run-type", run_type,
        "--task-type", task_type,
        "--publisher", "client",
        "--est", _v2_plan_argv(plan)[1],
        "--base", str(cfg["base_priority"]),
        "--by", agent,
        "--required-role", "dev-executor",
        "--client-source", cfg["client_source"],
        "--focus", focus,
        "--task-id", run_id,
    )
    task_id = str(publication.get("task_id") or run_id)
    return {
        "run_id": run_id,
        "request_id": task_id,
        "status": "submitted",
        "_v2_dispatch": "v2",
        "_v2_run_type": run_type,
        "_v2_task_type": task_type,
        "_v2_task_id": task_id,
    }


def build_v2_dev_worker_cmd(config: dict[str, Any], run_id: str, *,
                            dev_repo: str,
                            plan: dict[str, Any] | None = None) -> list:
    """Build the v2 dev worker command (pure; testable).

    蜂群内建 `agent_runtime`,`--permission dev`,`--repo-root` = **指定的被测仓库**
    (不是猜的产物目录;不存在/非目录在调用方已拒)。轮数/预算 W15-b② 起取发布
    计划;*plan* 缺省 = 改前固定口径(40 轮 / dev 档硬顶 + 灰度 token_budget)。
    """
    cfg = v2_gray_config(config)
    agent, judge = _v2_dev_identities(config)
    repo = str(Path(str(dev_repo)).expanduser().resolve())
    plan = plan if plan is not None else _v2_fixed_budget_plan(
        config, run_type=_V2_RUN_TYPE_BY_ROUTE["dev"])
    token_budget, _est, max_turns = _v2_plan_argv(plan)
    return [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarmctl.py"),
        "worker",
        "--db", config["swarm_v2_db"],
        "--agent", agent,
        "--judge-by", judge,
        "--agent-runtime",
        "--permission", _V2_DEV_WORKER_PERMISSION,
        "--repo-root", repo,
        "--max-turns", max_turns,
        "--max-tokens-budget", token_budget,
        "--poll-interval", str(cfg["poll_interval"]),
        "--max-tasks", "1",
    ]


def launch_v2_dev_worker(config: dict[str, Any], run_id: str, *,
                         dev_repo: str | None = None) -> int:
    """Launch the v2 dev worker detached (mirrors security/research).

    起进程**之前**依次校验:run_id 形状 → 仓库位非空且为已存在目录 → dev 档开关
    (`agent_runtime_exec`,复用 `require_dev_exec_switch`)。任一不满足 ⇒ 响亮拒绝,
    零 mkdir / 零 Popen。
    """
    value = str(run_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError(f"invalid swarm v2 dev run id: {value!r}")
    repo_raw = dev_repo if dev_repo is not None else config.get(_V2_DEV_REPO_KEY)
    repo = str(repo_raw or "").strip()
    if not repo:
        raise ValueError(DEV_REPO_REQUIRED)
    repo_path = Path(repo).expanduser()
    if not repo_path.is_dir():
        raise ValueError(
            f"dev 线拒绝启动:被测仓库不存在或不是目录: {repo_path}"
            "(--dev-repo/swarm_v2_dev_repo;零 mkdir/零 Popen)")
    require_dev_exec_switch(config)          # 零副作用:拒绝先于 mkdir/Popen
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"swarm-v2-dev-{value}.log"
    plan = v2_worker_plan(config, run_id, run_type="dev")
    cmd = build_v2_dev_worker_cmd(config, run_id, dev_repo=str(repo_path), plan=plan)
    worker_env, _dropped = scrub_environment()
    worker_env = apply_worker_proxy(worker_env, resolve_worker_proxy(config))
    worker_env["COMPANY_ROUTER_BYPASS"] = "1"
    worker_env["HERMES_SESSION_SOURCE"] = "tool"
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_path.resolve()),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=worker_env,
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


def select_company_result(result: dict[str, Any]) -> str:
    """Prefer the latest completed worker result over reporter-first ordering.

    The swarm client intentionally favors reporter tasks for generic callers,
    but company routing must account for a later analyst correcting an earlier
    synthesis. Diff previews are self-reports from an isolated worker and are
    lower priority than a direct evidence-backed conclusion.
    """
    candidates = []
    task_results = result.get("task_results")
    # A corrupt/older swarmctl payload may carry task_results as a dict or a
    # list of non-dicts; degrade to no candidates instead of crashing the hook.
    if not isinstance(task_results, list):
        task_results = []
    for task in task_results:
        if not isinstance(task, dict):
            continue
        if task.get("status") != "completed":
            continue
        summary = task.get("result_summary") or {}
        content = ""
        if isinstance(summary, dict):
            for key in ("content", "summary", "result", "output"):
                if summary.get(key):
                    content = str(summary[key])
                    break
        if not content:
            continue
        is_diff_preview = content.lstrip().startswith("┊ review diff")
        candidates.append((not is_diff_preview, str(task.get("ended_at") or ""), content))

    if candidates:
        # Evidence-backed conclusions outrank diff previews regardless of
        # finish order; among same-class results the newest wins.
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]
    return str(result.get("result") or result.get("summary") or "")


def refresh_session_runs(config: dict[str, Any], state: RouterState, session_id: str) -> list[str]:
    updates: list[str] = []
    for row in state.active_for_session(session_id):
        run_id = row["run_id"]
        try:
            result = swarm_command(config, "task", "result", "--run-id", run_id, timeout=15)
        except Exception as exc:  # noqa: BLE001 -- a failing status query must not abort the session refresh
            updates.append(f"- 蜂群 {run_id[:8]} 状态查询失败：{exc}")
            continue
        # ``status`` comes from the external swarmctl JSON: coerce it to text
        # so a list/object can never reach the SQLite bind or set membership.
        status = str(result.get("status") or "unknown")
        state.update(row["route_event_id"], status=status)
        if status == "completed" and not row["result_delivered"]:
            limit = _int_config(config, "result_context_chars", 6000)
            content = select_company_result(result)[:limit]
            updates.append(f"- 蜂群 {run_id[:8]} 已完成。结果：\n{content}")
            state.update(row["route_event_id"], result_delivered=1)
            # Classify output quality for security runs.  ``row`` is a
            # sqlite3.Row (RouterState.active_for_session returns raw rows), so
            # it is indexed with ``[]`` — it has no ``.get()``.
            if not row["quality_status"]:
                try:
                    from .operations_control import _classify_security_findings
                except ImportError:
                    from operations_control import _classify_security_findings
                try:
                    quality = _classify_security_findings(
                        run_id,
                        # D-16.1: 读类统计面 repoint 到 v2 活库;v1 库位是墓碑目录。
                        swarm_db=Path(config.get("swarm_v2_db") or config.get("swarm_db", "")),
                        log_dir=Path(config.get("log_dir", "")),
                    )
                    state.update(row["route_event_id"], quality_status=quality)
                except Exception as exc:  # noqa: BLE001 -- best-effort classification
                    # Best-effort quality classification — must not block result notification
                    LOGGER.debug("security quality classification failed for run %s: %s", run_id, exc)
        elif status in {"running", "submitted"}:
            task_counts = result.get("tasks") or {}
            updates.append(f"- 蜂群 {run_id[:8]} 正在运行，任务状态：{json.dumps(task_counts, ensure_ascii=False)}")
        elif status in {"failed", "cancelled"}:
            updates.append(f"- 蜂群 {run_id[:8]} 状态为 {status}，需要主 Agent 检查日志。")
    return updates


def refresh_session_content_jobs(config: dict[str, Any], state: RouterState, session_id: str) -> list[str]:
    updates: list[str] = []
    for action, label in (
        ("dispatch_article", "文章产线"),
        ("dispatch_video", "视频产线"),
        ("dispatch_company", "公司执行 Worker"),
    ):
        for row in state.active_for_session(session_id, action=action):
            try:
                status_path = content_job_path(config, row["run_id"]) / "status.json"
            except ValueError as exc:
                updates.append(f"- {label}任务路径无效：{exc}")
                continue
            if not status_path.exists():
                updates.append(f"- {label}任务 {row['run_id'][:8]} 正在启动。")
                continue
            try:
                # O_NOFOLLOW makes the symlink rejection atomic: the content
                # jobs directory is written by an untrusted worker, and a swap
                # between the is_symlink() check and the open would otherwise
                # be followed (leaking file content into the LLM context).
                payload = json.loads(read_text_limited_nofollow(status_path, max_bytes=2 * 1024 * 1024))
                if not isinstance(payload, dict):
                    raise TypeError("status root must be an object")
            except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                updates.append(f"- {label}任务 {row['run_id'][:8]} 状态读取失败：{exc}")
                continue
            status = str(payload.get("status") or "unknown")
            state.update(row["route_event_id"], status=status)
            if status == "completed" and not row["result_delivered"]:
                result = str(payload.get("result") or "任务已完成。")
                raw_artifacts = payload.get("artifacts")
                artifacts = raw_artifacts if isinstance(raw_artifacts, list) else []
                artifact_text = "、".join(str(item) for item in artifacts)
                suffix = f"\n产物：{artifact_text}" if artifact_text else ""
                updates.append(f"- {label}任务 {row['run_id'][:8]} 已完成：\n{result[:4000]}{suffix}")
                state.update(row["route_event_id"], result_delivered=1)
            elif status == "running":
                updates.append(f"- {label}任务 {row['run_id'][:8]} 正在运行。")
            elif status == "needs_approval":
                updates.append(f"- {label}任务 {row['run_id'][:8]} 已完成内部准备，等待人工审批：{payload.get('result') or '请查看审批请求。'}")
                state.update(row["route_event_id"], result_delivered=1)
            elif status in {"failed", "cancelled"}:
                updates.append(f"- {label}任务 {row['run_id'][:8]} 状态为 {status}：{payload.get('error') or '请检查日志。'}")
    return updates


def build_context(
    decision: RouteDecision,
    *,
    run: dict[str, Any] | None = None,
    existing_run_id: str = "",
    existing_status: str = "",
    status_updates: list[str] | None = None,
) -> str:
    lines = [
        "[公司 Router 私有上下文]",
        f"产品线路由：{decision.route}；动作：{decision.action}；置信度：{decision.confidence:.2f}。",
    ]
    if status_updates:
        lines.append("本会话已有后台任务更新：")
        lines.extend(status_updates)

    if decision.action == "approval_required":
        if decision.authorization_required:
            lines.append(
                "该请求涉及主动安全测试，但消息中缺少足够的授权/Scope 证明。"
                "不要执行或自动分发；请主 Agent 向用户确认授权目标和范围。"
            )
        else:
            lines.append(
                "该请求涉及发布、上传、付款、删除或其他外部动作，必须人工审批。"
                "可以说明待审批内容，但不要自动执行或分发外部动作。"
            )
    elif decision.action == "dispatch_swarm":
        run_id = (run or {}).get("run_id") or existing_run_id
        if run_id:
            run_status = str((run or {}).get("status") or existing_status)
            completed = run_status == "completed" or any("已完成" in update for update in (status_updates or []))
            if completed:
                lines.append(
                    f"该任务对应的安全蜂群 run_id={run_id} 已完成。"
                    "请主 Agent 使用本轮注入结果或会话中此前已交付的结果回答，不要重新提交。"
                )
            else:
                lines.append(f"任务已自动提交至安全蜂群，run_id={run_id}。不要在主 Agent 中重复执行同一任务。")
                lines.append("请向用户简短确认已分发；后续回合 Router 会注入运行状态或最终结果。")
        else:
            lines.append("任务应提交至安全蜂群，但当前未获得 run_id；主 Agent 应报告路由失败。")
    elif decision.action in {"dispatch_article", "dispatch_video", "dispatch_company"}:
        run_id = (run or {}).get("run_id") or existing_run_id
        run_status = str((run or {}).get("status") or existing_status)
        destination = {
            "dispatch_article": "文章产线",
            "dispatch_video": "视频产线",
            "dispatch_company": "公司执行 Worker",
        }[decision.action]
        if run_id and run_status == "completed":
            lines.append(f"{destination}任务 run_id={run_id} 已完成。请使用本轮或此前注入的产物回答，不要重复提交。")
        elif run_id:
            lines.append(f"任务已自动分发至{destination}，run_id={run_id}。不要在主 Agent 中重复执行。")
            lines.append("外部动作和不可逆操作仍需人工审批；后台完成后会主动回传原会话。")
        else:
            lines.append(f"任务应提交至{destination}，但当前未获得 run_id；主 Agent 应报告路由失败。")
    else:
        lines.append("该请求由公司主 Agent 处理，并以公司 Wiki 为事实来源。")
    return "\n".join(lines)


def handle_tvcr_decision(message: str, config: dict[str, Any], actor: str) -> str | None:
    """Apply an explicit user decision to a pending operating proposal."""
    operations_db = str(config.get("operations_db") or "").strip()
    if not operations_db:
        return None
    try:
        try:
            from .operations_control import apply_user_decision
        except ImportError:
            from operations_control import apply_user_decision
        result = apply_user_decision(Path(operations_db), message, actor=actor)
    except Exception as exc:  # noqa: BLE001 -- approval handling failure must be reported, not raised
        return f"[TVCR 审批上下文]\n审批处理失败：{exc}。不要实施任何修改，请向用户报告失败。"
    if result is None:
        return None
    if not result.get("ok"):
        return f"[TVCR 审批上下文]\n{result.get('message') or '审批未生效。'}不要实施任何修改。"
    if result.get("decision") == "rejected":
        return (
            "[TVCR 审批上下文]\n"
            f"用户已拒绝经营提案 {result['proposal_id']}（{result['proposal_title']}）。"
            "不要实施该提案；向用户确认已记录决定即可。"
        )
    return "\n".join([
        "[TVCR 审批上下文]",
        f"用户已批准经营提案 {result['proposal_id']}（{result['proposal_title']}）。",
        f"已创建运营实验 {result['experiment_id']}，当前状态为 planned。",
        f"批准的经营动作：{result['recommended_action']}",
        f"可能涉及层级：{json.dumps(result.get('change_scopes') or [], ensure_ascii=False)}",
        f"成功指标：{json.dumps(result.get('success_metrics') or [], ensure_ascii=False)}",
        "主 Agent 应开始落实运营实验：先处理业务/产品/流程/资源决策，再判断 Prompt、配置或代码是否需要改变。",
        f"实施前运行：python3 /home/pwn/workspace/company/automation/operations_control.py experiment {result['experiment_id']} running",
        f"完成安全验证后运行：python3 /home/pwn/workspace/company/automation/operations_control.py experiment {result['experiment_id']} evaluating",
        "实施前将实验标记 running；完成安全验证后标记 evaluating。不得把“已批准经营实验”解释为任意外部发布或付款授权。",
    ])


def _session_has_meaningful_content(
    session_id: str,
    *,
    hermes_db_path: Path = HERMES_STATE_DB,
    min_user_messages: int = PRE_EVAL_MIN_PRIOR_USER_MESSAGES,
    min_total_messages: int = PRE_EVAL_MIN_PRIOR_MESSAGES,
) -> bool:
    """Check whether a session has enough prior conversation to justify analysis.

    Returns True only if the session contains at least *min_user_messages*
    user messages AND *min_total_messages* user+assistant messages beyond
    the current dispatch request itself.
    """
    if not hermes_db_path.is_file():
        return False
    try:
        db = sqlite3.connect(sqlite_uri(hermes_db_path, mode="ro"), uri=True)
        db.row_factory = sqlite3.Row
        try:
            user_count = db.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE session_id=? AND role='user'",
                (session_id,),
            ).fetchone()
            total_count = db.execute(
                "SELECT COUNT(*) AS c FROM messages "
                "WHERE session_id=? AND role IN ('user','assistant')",
                (session_id,),
            ).fetchone()
            if not user_count or not total_count:
                return False
            # The current dispatch message may already be stored; be conservative
            # and require one extra message on BOTH counts (the user bound then
            # means at least 2 user rows, the total bound at least
            # min_total_messages+1 rows), so the documented "beyond the current
            # dispatch request" minima still hold when the current message is
            # part of the same snapshot both COUNT(*) queries see.
            return (int(user_count["c"]) >= (min_user_messages + 1)
                    and int(total_count["c"]) >= (min_total_messages + 1))
        finally:
            db.close()
    except sqlite3.Error:
        return False


def _pre_evaluate_task(
    decision: RouteDecision,
    message: str,
    session_id: str,
    config: dict[str, Any],
) -> str:
    """Determines if a task should proceed or be skipped BEFORE dispatch.

    Returns one of: 'proceed', 'skip', 'skip_low_confidence'
    """
    pre_eval_enabled = bool(config.get("pre_evaluation_enabled", True))
    if not pre_eval_enabled:
        return "proceed"

    # Status notifications: messages that are purely reporting status/progress,
    # NOT requesting security analysis. These should never dispatch swarm.
    lowered = message.lower()
    status_markers = ("已发表", "已发布", "已完成", "已推送", "已更新",
                      "以下文章", "以下是",
                      "[async delegation", "[importan")
    if decision.action == "dispatch_swarm" and any(marker in lowered for marker in status_markers):
        return "skip"

    # Conversation vs. task disambiguation for security-classified messages.
    # A message that was keyword-scored as security but reads like a conversation
    # (long text, meta-discourse, past-tense references) should not dispatch swarm.
    # Real security tasks are short, imperative, and reference a specific target.
    # Conversational signals — skip if strong indicators present
    # Common to all actions
    conversation_markers = (
        "我认为", "我觉得", "你可以", "能不能", "是不是", "应该是",
        "我认为是", "废弃", "如果", "可以寻找", "我来", "我想",
    )
    if decision.action in {"dispatch_swarm", "dispatch_company"}:
        conv_hits = sum(1 for m in conversation_markers if m in lowered)
        if conv_hits >= 2:
            return "skip"
        # Long meandering text (3+ sentences without a clear target) is likely chat
        sentence_count = len(re.split(r'[。！？\n]', message))
        words = len(message.split())
        if sentence_count >= 3 and words >= 15 and conv_hits >= 1:
            return "skip"

    # Company-specific disambiguation: "给我一个方案", "建议", "你觉得怎么样"
    # are requests for the main agent to design/propose, not execution commands.
    if decision.action == "dispatch_company":
        company_conv_markers = (
            "给我", "给我一个", "给我一份", "给我列出", "给我总结", "给我说",
            "你觉得", "你有什么建议", "有什么想法", "方案", "建议",
        )
        if any(m in lowered for m in company_conv_markers):
            return "skip"

    # Confidence gate: baseline confidence (0.45, no terms matched) should
    # never trigger an auto-dispatch — the classification was a fallback.
    # Fall back to main_agent so the conversation continues naturally.
    if decision.confidence < 0.5 and decision.action in {"dispatch_company", "dispatch_article", "dispatch_video"}:
        return "skip_low_confidence"

    # Skill review: check for actual conversation content to review
    if decision.action == "dispatch_company" and SKILL_REVIEW_MARKER in message.lower():
        min_messages = _int_config(config, "pre_eval_skill_review_min_messages", PRE_EVAL_MIN_PRIOR_MESSAGES)
        min_user = _int_config(config, "pre_eval_skill_review_min_user_messages", PRE_EVAL_MIN_PRIOR_USER_MESSAGES)
        hermes_db = Path(str(config.get("hermes_state_db", HERMES_STATE_DB)) or HERMES_STATE_DB)
        if not _session_has_meaningful_content(
            session_id,
            hermes_db_path=hermes_db,
            min_user_messages=min_user,
            min_total_messages=min_messages,
        ):
            return "skip"

    return "proceed"


# Canonical product-line names as recorded in operational_runs; mirrors the map
# in operations_control so the circuit breaker keys on the same dimension.
_ROUTE_PRODUCT_LINE = {
    "article": "article-production",
    "video": "video-production",
    "security": "security-exploration",
    "company": "company",
}


def _route_product_line(route: str) -> str:
    return _ROUTE_PRODUCT_LINE.get(route, route or "unknown")


def _circuit_breaker_state(
    config: dict[str, Any], route: str, *, now: datetime | None = None
) -> tuple[str, int] | None:
    """Return ``(product_line, failures)`` when a product line should be tripped.

    Reuses the digest's failure clustering so the breaker keys on the exact same
    recent-failure signal the daily readout reports. Only consulted when an
    operations DB is configured, so classification and tests stay unaffected.
    """
    if not config.get("circuit_breaker_enabled", True):
        return None
    threshold = _int_config(config, "circuit_breaker_threshold", 3)
    if threshold <= 0:
        return None
    operations_db_value = str(config.get("operations_db") or "").strip()
    if not operations_db_value:
        return None
    window_hours = _int_config(config, "circuit_breaker_window_hours", 24)
    product_line = _route_product_line(route)
    current = now or datetime.now(timezone.utc)
    try:
        from .company_daily_digest import _failure_clusters
    except ImportError:
        from company_daily_digest import _failure_clusters
    _lines, payload = _failure_clusters(
        Path(operations_db_value), current, window_hours=window_hours
    )
    failures = sum(
        int(entry["count"]) for entry in payload if entry["product_line"] == product_line
    )
    return (product_line, failures) if failures >= threshold else None


def handle_hook(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    """Route one Hermes hook message.

    Bypass gates run before the ``RouterState`` connection is opened, so a
    message that should not be routed never touches the state DB.  Once routing
    is needed, ``_handle_hook`` runs under a ``try/finally`` that closes the
    connection on every return path (including exceptions) instead of relying
    on ``__del__``/GC.
    """
    if not config.get("enabled", True):
        return {}

    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    message = str(extra.get("user_message") or "").strip()
    if not message:
        return {}
    if os.getenv("COMPANY_ROUTER_BYPASS") == "1":
        return {}
    session_id = str(payload.get("session_id") or "unknown-session")
    hermes_db = Path(str(config.get("hermes_state_db", HERMES_STATE_DB)) or HERMES_STATE_DB)
    # The global pre_llm_call hook also runs inside Hermes workers, cron jobs,
    # and delegated subagents.  Those turns are already owned by another
    # executor; routing them again is the source of recursive/duplicate jobs.
    if _is_non_user_hermes_session(payload, extra, session_id, hermes_db_path=hermes_db):
        return {}
    if INTERNAL_MESSAGE_PREFIX_RE.match(message):
        if _is_internal_hermes_hook(payload, extra):
            return {}
        message = _strip_internal_message_prefixes(message)
        if not message:
            return {}
    # Model-switch notices are prepended by some gateways to a real user
    # message. Strip all consecutive notices, but retain the user text that
    # follows them for normal routing.
    while MODEL_SWITCH_NOTICE_RE.match(message):
        message = MODEL_SWITCH_NOTICE_RE.sub("", message, count=1).lstrip()
    if not message:
        return {}
    # Completion notices, background-process diagnostics, compaction handoffs,
    # and radar probes are synthetic user-shaped messages.  They may contain
    # strong routing vocabulary, so reject them before any state is created.
    if SYNTHETIC_MESSAGE_PREFIX_RE.match(message):
        return {}
    platform = str(extra.get("platform") or "unknown")
    decision_context = handle_tvcr_decision(message, config, actor=f"{platform}:{session_id}")
    if decision_context is not None:
        return {"context": decision_context}

    state = RouterState(config["state_db"])
    try:
        return _handle_hook(state, payload, config, message, session_id, platform)
    finally:
        state.close()


def _stored_decision_fallback(row: sqlite3.Row) -> RouteDecision:
    """Build a no-dispatch decision for a row whose stored decision_json is corrupt.

    Reusing the row's recorded action (with its run_id/status passed separately
    to build_context) makes the hook tell the main agent the task already exists
    instead of falling through to a fresh classification and duplicate dispatch.
    """
    return RouteDecision(
        route=str(row["route"] or "unknown"),
        confidence=0.0,
        action=str(row["action"] or "main_agent"),
        reason="stored decision unreadable; not re-dispatching",
    )


def _handle_hook(
    state: RouterState,
    payload: dict[str, Any],
    config: dict[str, Any],
    message: str,
    session_id: str,
    platform: str,
) -> dict[str, str]:
    message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    updates = refresh_session_runs(config, state, session_id)
    updates.extend(refresh_session_content_jobs(config, state, session_id))

    existing = state.existing(session_id, message_hash)
    if existing:
        decision = _stored_decision(existing["decision_json"])
        if decision is None:
            # A corrupt stored decision must NOT fall through to a fresh
            # classification: RouterState.insert would reuse this event id and
            # the dispatch below would overwrite its run_id/status and submit a
            # duplicate run (token burn / duplicate external work).
            LOGGER.warning("corrupt stored decision_json for session %s; not re-dispatching", session_id)
            return {"context": build_context(
                _stored_decision_fallback(existing),
                existing_run_id=existing["run_id"],
                existing_status=existing["status"],
                status_updates=updates,
            )}
        else:
            existing_updates = updates
            if (
                str(existing["status"] or "") == "skipped"
                and "低置信度分发" in str(existing["error"] or "")
            ):
                decision = RouteDecision(**{
                    **asdict(decision),
                    "action": "main_agent",
                    "reason": "低置信度分发",
                })
                existing_updates = updates + [
                    (
                        f"- [预评估] 未自动分发：低置信度分发（置信度 {decision.confidence:.2f}）。"
                        "未生成 run_id，已交由公司主 Agent 继续处理。"
                    )
                ]
            return {"context": build_context(
                decision,
                existing_run_id=existing["run_id"],
                existing_status=existing["status"],
                status_updates=existing_updates,
            )}

    targets = extract_target(message)
    decision = classify_with_fallback(message, config, config.get("authorized_targets") or [])
    dedup_key = _build_dedup_key(session_id, targets, decision.intent)
    if decision.action == "dispatch_swarm" and not EXPLICIT_NEW_SWARM_RE.search(message):
        dedup_minutes = _int_config(config, "swarm_dedup_window_minutes", 10)
        recent = state.recent_for_session(
            session_id,
            decision.action,
            datetime.now(timezone.utc) - timedelta(minutes=dedup_minutes),
            dedup_key=dedup_key,
        )
        if recent:
            recent_decision = _stored_decision(recent["decision_json"])
            if recent_decision is None:
                # Same guard as the existing-event branch: an unreadable stored
                # decision must not disable the dedup window, or a repeated
                # message would spawn a duplicate swarm run.
                LOGGER.warning(
                    "corrupt stored decision_json for route event %s; deduping without re-dispatch",
                    recent["route_event_id"],
                )
                return {"context": build_context(
                    _stored_decision_fallback(recent),
                    existing_run_id=recent["run_id"],
                    existing_status=recent["status"],
                    status_updates=updates,
                )}
            else:
                state.update(
                    recent["route_event_id"],
                    delivery_attempts=_safe_counter(recent["delivery_attempts"]) + 1,
                )
                return {"context": build_context(
                    recent_decision,
                    existing_run_id=recent["run_id"],
                    existing_status=recent["status"],
                    status_updates=updates,
                )}

    is_skill_review = decision.action == "dispatch_company" and SKILL_REVIEW_MARKER in message.lower()
    if is_skill_review:
        cooldown_hours = _int_config(config, "content_job_skill_review_cooldown_hours", 4)
        recent = state.recent_for_session(
            session_id,
            decision.action,
            datetime.now(timezone.utc) - timedelta(hours=cooldown_hours),
            completed_only=True,
            message_marker=SKILL_REVIEW_MARKER,
        )
        if recent:
            recent_decision = _stored_decision(recent["decision_json"])
            if recent_decision is None:
                LOGGER.warning(
                    "corrupt stored decision_json for route event %s; deduping without re-dispatch",
                    recent["route_event_id"],
                )
                return {"context": build_context(
                    _stored_decision_fallback(recent),
                    existing_run_id=recent["run_id"],
                    existing_status=recent["status"],
                    status_updates=updates,
                )}
            else:
                state.update(
                    recent["route_event_id"],
                    delivery_attempts=_safe_counter(recent["delivery_attempts"]) + 1,
                )
                return {"context": build_context(
                    recent_decision,
                    existing_run_id=recent["run_id"],
                    existing_status=recent["status"],
                    status_updates=updates,
                )}

    origin = resolve_session_origin(str(config.get("gateway_sessions_index") or ""), session_id)
    event_id, created = state.insert_or_existing(
        session_id,
        platform,
        message_hash,
        message,
        decision,
        origin=origin,
        dedup_key=dedup_key,
    )
    if not created:
        # A concurrent hook inserted the same session+message between the
        # existing() check above and this insert.  Reuse that event exactly
        # like the existing() branch: the winner owns the downstream dispatch,
        # and dispatching again here would duplicate the run (token burn /
        # duplicate external work) and orphan the losing run_id.
        row = state.existing(session_id, message_hash)
        stored = _stored_decision(row["decision_json"]) if row is not None else None
        if stored is None:
            stored = _stored_decision_fallback(row) if row is not None else decision
        return {"context": build_context(
            stored,
            existing_run_id=str(row["run_id"] or "") if row is not None else "",
            existing_status=str(row["status"] or "") if row is not None else "",
            status_updates=updates,
        )}
    run: dict[str, Any] | None = None

    # Pre-evaluation gate: check task value BEFORE dispatch
    pre_eval = _pre_evaluate_task(decision, message, session_id, config)
    if pre_eval == "skip":
        state.update(event_id, status="skipped", error="pre-evaluation: no meaningful work to perform")
        # Mirrors the sibling skip/defer branches: hand the decision back to the
        # main agent instead of returning the original dispatch action without a
        # run_id, which would append a contradictory "report routing failure" line.
        return {"context": build_context(
            RouteDecision(**{
                **asdict(decision), "action": "main_agent",
                "reason": "pre-evaluation: skip",
            }),
            status_updates=updates + [
                "- [预评估] 任务已跳过：当前会话无足够的对话历史可供分析。任务未派发，无 Token 消耗。"
            ],
        )}
    elif pre_eval == "skip_low_confidence":
        state.update(event_id, status="skipped", error="pre-evaluation: 低置信度分发")
        fallback_decision = RouteDecision(**{
            **asdict(decision),
            "action": "main_agent",
            "reason": "低置信度分发",
        })
        return {"context": build_context(
            fallback_decision,
            status_updates=updates + [
                (
                    f"- [预评估] 未自动分发：低置信度分发（置信度 {decision.confidence:.2f}）。"
                    "未生成 run_id，已交由公司主 Agent 继续处理。"
                )
            ],
        )}

    # Circuit breaker: a product line that has failed repeatedly in the recent
    # window is short-circuited to the main agent instead of burning more tokens
    # on autonomous dispatch until the failure cluster is cleared.
    if decision.action in {"dispatch_swarm", "dispatch_article", "dispatch_video", "dispatch_company"}:
        breaker = _circuit_breaker_state(config, decision.route)
        if breaker is not None:
            product_line, failures = breaker
            state.update(
                event_id, status="skipped",
                error=f"circuit breaker open: {product_line} {failures} recent failures",
            )
            return {"context": build_context(
                RouteDecision(**{
                    **asdict(decision), "action": "main_agent",
                    "reason": f"该产线频繁失败，已降级（{product_line} 近窗口 {failures} 次失败）",
                }),
                status_updates=updates + [
                    (
                        f"- [熔断] 产线 {product_line} 近窗口连续失败 {failures} 次，已降级由主 Agent 处理，"
                        "避免持续消耗 Token。"
                    )
                ],
            )}

    if decision.action == "dispatch_swarm":
        # W14-b:dev 路由走专属提交口(submit_dev_v2),与 research/security 并列;
        # W11-b:research 路由迁到 v2 专属提交口(submit_research_v2);security
        # 仍走原提交口(submit_security_v2),行为逐字不变。
        if decision.route == "dev":
            enabled = config.get("dispatch_dev", False)
            if not enabled:
                state.update(event_id, status="deferred",
                             error="product line dispatch disabled")
                return {"context": build_context(
                    RouteDecision(**{
                        **asdict(decision), "action": "main_agent",
                        "reason": "product line dispatch disabled",
                    }),
                    status_updates=updates + [
                        "- dev 自动分发已禁用 (dispatch_dev=false)，已交由主 Agent。"
                        f" {DEV_LINE_DISABLED}"
                    ],
                )}
            dev_repo = str(config.get(_V2_DEV_REPO_KEY) or "").strip()
            if not dev_repo or not Path(dev_repo).expanduser().is_dir():
                # 缺仓库位/仓库不存在 ⇒ **响亮拒绝**(绝不猜默认仓库),零提交零进程
                state.update(event_id, status="failed", error=DEV_REPO_REQUIRED)
                return {"context": build_context(
                    RouteDecision(**{**asdict(decision), "action": "main_agent",
                                     "reason": "dev repo not configured"}),
                    status_updates=updates + [f"- {DEV_REPO_REQUIRED}"],
                )}
            active = [row for row in state.active_for_session(session_id)
                      if row["status"] in {"submitted", "running"}]
            if len(active) >= _int_config(config, "max_active_runs_per_session", 2):
                state.update(event_id, status="deferred", error="active run limit reached")
                return {"context": build_context(
                    RouteDecision(**{**asdict(decision), "action": "main_agent",
                                     "reason": "active run limit reached"}),
                    status_updates=updates + ["- 已达到本会话并发蜂群上限，新任务暂未提交。"],
                )}
            d_agent, d_judge = _v2_dev_identities(config)
            gray = v2_gray_decision(
                config, decision, message, agent=d_agent, judge=d_judge)
            fallback_reason = ""
            if gray["hit"]:
                try:
                    v2_run = submit_dev_v2(
                        config,
                        decision=decision,
                        message=message,
                        session_id=session_id,
                        platform=platform,
                        gray=gray,
                        dev_repo=dev_repo,
                    )
                    run_id = str(v2_run.get("run_id") or "")
                    pid = launch_v2_dev_worker(config, run_id, dev_repo=dev_repo)
                    run = {"run_id": run_id, "status": "running"}
                    state.update(event_id, run_id=run_id,
                                 request_id=str(v2_run.get("request_id") or ""),
                                 runner_pid=pid, status="running",
                                 last_heartbeat=utc_now())
                    updates.append(
                        "- v2 灰度命中：dev 任务已发布至蜂群 v2 市场"
                        f"（run_id={run_id}）。"
                    )
                except Exception as exc:  # noqa: BLE001 -- v2 must never drop the task
                    fallback_reason = (
                        f"v2 dev submit failed: {type(exc).__name__}: {exc}")
                    LOGGER.warning(
                        "company_router dev v2 fallback: %s", fallback_reason)
            elif gray["enabled"]:
                fallback_reason = str(gray.get("reason") or "v2_gray_not_selected")
                LOGGER.info("company_router dev v2 not selected: %s", fallback_reason)

            if run is None:
                state.update(event_id, status="failed", error=DEV_V2_LINE_UNAVAILABLE)
                return {"context": build_context(
                    RouteDecision(**{**asdict(decision), "action": "main_agent",
                                     "reason": "dev line moved to v2; run not submitted"}),
                    status_updates=updates + (
                        [f"- v2 dev 灰度回退：{fallback_reason}"]
                        if fallback_reason else []
                    ) + [f"- {DEV_V2_LINE_UNAVAILABLE}"],
                )}
        elif decision.route == "research":
            enabled = config.get("dispatch_research", True)
            if not enabled:
                # 闸关 = 生产现状:响亮拒绝(保留旧子串便于既有断言,附
                # "已迁 v2 + 开闸命令" —— 不再是 v1 退役/无路可走)。
                state.update(event_id, status="deferred", error="product line dispatch disabled")
                return {"context": build_context(
                    RouteDecision(**{
                        **asdict(decision), "action": "main_agent",
                        "reason": "product line dispatch disabled",
                    }),
                    status_updates=updates + [
                        "- research 自动分发已禁用 (dispatch_research=false)，已交由主 Agent。"
                        f" {RESEARCH_LINE_MIGRATED_TO_V2}"
                    ],
                )}
            active = [row for row in state.active_for_session(session_id) if row["status"] in {"submitted", "running"}]
            if len(active) >= _int_config(config, "max_active_runs_per_session", 2):
                state.update(event_id, status="deferred", error="active run limit reached")
                return {"context": build_context(
                    RouteDecision(**{**asdict(decision), "action": "main_agent", "reason": "active run limit reached"}),
                    status_updates=updates + ["- 已达到本会话并发蜂群上限，新任务暂未提交。"],
                )}
            # research 线**专用**身份键(缺省留空 ⇒ 灰度不命中 ⇒ 响亮拒绝;
            # 生产 `dispatch_research=false` ⇒ 本分支不可达,配置逐字不变)。
            r_agent, r_judge = _v2_research_identities(config)
            gray = v2_gray_decision(
                config, decision, message, agent=r_agent, judge=r_judge)
            fallback_reason = ""
            if gray["hit"]:
                try:
                    v2_run = submit_research_v2(
                        config,
                        decision=decision,
                        message=message,
                        session_id=session_id,
                        platform=platform,
                        gray=gray,
                    )
                    run_id = str(v2_run.get("run_id") or "")
                    pid = launch_v2_research_worker(config, run_id)
                    run = {"run_id": run_id, "status": "running"}
                    state.update(event_id, run_id=run_id,
                                 request_id=str(v2_run.get("request_id") or ""),
                                 runner_pid=pid, status="running",
                                 last_heartbeat=utc_now())
                    updates.append(
                        "- v2 灰度命中：research 任务已发布至蜂群 v2 市场"
                        f"（run_id={run_id}）。"
                    )
                except Exception as exc:  # noqa: BLE001 -- v2 must never drop the task
                    fallback_reason = (
                        f"v2 research submit failed: {type(exc).__name__}: {exc}")
                    LOGGER.warning(
                        "company_router research v2 fallback: %s", fallback_reason)
            elif gray["enabled"]:
                fallback_reason = str(gray.get("reason") or "v2_gray_not_selected")
                LOGGER.info(
                    "company_router research v2 not selected: %s", fallback_reason)

            if run is None:
                # 闸开但灰度/身份前置不满足(或提交失败)⇒ 仍 fail-closed 交回
                # 主 Agent;口径 = "已迁 v2",不再是 v1 退役、无路可走。
                state.update(event_id, status="failed", error=RESEARCH_V2_LINE_UNAVAILABLE)
                return {"context": build_context(
                    RouteDecision(**{**asdict(decision), "action": "main_agent",
                                     "reason": "research line moved to v2; run not submitted"}),
                    status_updates=updates + (
                        [f"- v2 research 灰度回退：{fallback_reason}"]
                        if fallback_reason else []
                    ) + [f"- {RESEARCH_V2_LINE_UNAVAILABLE}"],
                )}
        else:
            # 2026-08-10: security 路由复用蜂群链路, 由 dispatch_security 控制
            enabled = config.get("dispatch_security", True)
            if not enabled:
                # Mirrors the content disabled branch below: the product line is
                # switched off, not failing — defer and hand back to the main agent
                # instead of falling through to the run_id-less dispatch context,
                # which would append a contradictory "report routing failure" line.
                state.update(event_id, status="deferred", error="product line dispatch disabled")
                return {"context": build_context(
                    RouteDecision(**{
                        **asdict(decision), "action": "main_agent",
                        "reason": "product line dispatch disabled",
                    }),
                    status_updates=updates + [
                        f"- {decision.route} 自动分发已禁用 (dispatch_{decision.route}=false)，已交由主 Agent。"
                    ],
                )}
            else:
                active = [row for row in state.active_for_session(session_id) if row["status"] in {"submitted", "running"}]
                if len(active) >= _int_config(config, "max_active_runs_per_session", 2):
                    state.update(event_id, status="deferred", error="active run limit reached")
                    return {"context": build_context(
                        RouteDecision(**{**asdict(decision), "action": "main_agent", "reason": "active run limit reached"}),
                        status_updates=updates + ["- 已达到本会话并发蜂群上限，新任务暂未提交。"],
                    )}
                # v2 灰度接入(D-28;安全线):身份取安全线**专用**配置键 ——
                # 活库无 vuln 线身份 ⇒ 缺省留空 ⇒ `v2_gray_decision` 视为前置
                # 不满足 ⇒ 不命中,原样 fail-closed。生产 `swarm_v2_gray.run_types`
                # 当前只有 content ⇒ 本分支在生产**不可达**(见交付报告)。
                sec_agent, sec_judge = _v2_security_identities(config)
                gray = v2_gray_decision(
                    config, decision, message, agent=sec_agent, judge=sec_judge)
                fallback_reason = ""
                if gray["hit"]:
                    try:
                        v2_run = submit_security_v2(
                            config,
                            decision=decision,
                            message=message,
                            session_id=session_id,
                            platform=platform,
                            gray=gray,
                        )
                        run_id = str(v2_run.get("run_id") or "")
                        pid = launch_v2_security_worker(config, run_id)
                        run = {"run_id": run_id, "status": "running"}
                        state.update(event_id, run_id=run_id,
                                     request_id=str(v2_run.get("request_id") or ""),
                                     runner_pid=pid, status="running",
                                     last_heartbeat=utc_now())
                        updates.append(
                            f"- v2 灰度命中：{decision.route} 任务已发布至蜂群 v2 市场"
                            f"（run_id={run_id}）。"
                        )
                    except Exception as exc:  # noqa: BLE001 -- v2 must never drop the task
                        fallback_reason = (
                            f"v2 {decision.route} submit failed: {type(exc).__name__}: {exc}")
                        LOGGER.warning(
                            "company_router %s v2 fallback: %s", decision.route, fallback_reason)
                elif gray["enabled"]:
                    fallback_reason = str(gray.get("reason") or "v2_gray_not_selected")
                    LOGGER.info(
                        "company_router %s v2 not selected: %s", decision.route, fallback_reason)

                if run is None:
                    # D-25(2026-09-17): v1 执行面已整体退役(runner / executor 文件已删),
                    # 这里再也没有可起的进程 —— 不再写一条永远不会被执行的 submitted 行,
                    # 直接 fail-closed 交回主 Agent,并把"为什么"讲清楚。
                    state.update(event_id, status="failed", error=V1_EXECUTION_SURFACE_RETIRED)
                    return {"context": build_context(
                        RouteDecision(**{**asdict(decision), "action": "main_agent",
                                         "reason": "v1 execution surface retired"}),
                        status_updates=updates + (
                            [f"- v2 {decision.route} 灰度回退：{fallback_reason}"]
                            if fallback_reason else []
                        ) + [f"- {V1_EXECUTION_SURFACE_RETIRED}"],
                    )}
    elif decision.action in {"dispatch_article", "dispatch_video", "dispatch_company"}:
        enabled_key = {
            "dispatch_article": "auto_run_article",
            "dispatch_video": "auto_run_video",
            "dispatch_company": "auto_run_company",
        }[decision.action]
        if not config.get(enabled_key, True):
            # Mirrors the dispatch_swarm disabled branch: the product line is
            # switched off, not failing — defer and hand back to the main agent
            # instead of letting the run_id-less context below report a routing
            # failure.
            state.update(event_id, status="deferred", error="content product line dispatch disabled")
            return {"context": build_context(
                RouteDecision(**{
                    **asdict(decision), "action": "main_agent",
                    "reason": "content product line dispatch disabled",
                }),
                status_updates=updates + [
                    f"- {decision.route} 自动分发已禁用 ({enabled_key}=false)，已交由主 Agent。"
                ],
            )}
        active = state.active_for_session(session_id, action=decision.action)
        running = [row for row in active if row["status"] in {"submitted", "running"}]
        if len(running) >= _int_config(config, "max_active_content_jobs_per_session", 2):
            # Mirrors the dispatch_swarm cap branch: return the deferred
            # decision now, so the context explains the cap instead of also
            # appending a contradictory "unable to dispatch" failure line.
            state.update(event_id, status="deferred", error="active content job limit reached")
            return {"context": build_context(
                RouteDecision(**{
                    **asdict(decision), "action": "main_agent",
                    "reason": "active content job limit reached",
                }),
                status_updates=updates + ["- 已达到本会话内容产线并发上限，新任务暂未提交。"],
            )}
        fallback_reason = ""
        gray = v2_gray_decision(config, decision, message)
        if gray["hit"]:
            try:
                v2_run = submit_content_v2(
                    config,
                    decision=decision,
                    message=message,
                    session_id=session_id,
                    platform=platform,
                    gray=gray,
                )
                run_id = str(v2_run.get("run_id") or "")
                pid = launch_v2_content_worker(config, run_id)
                run = {"run_id": run_id, "status": "running"}
                state.update(event_id, run_id=run_id,
                             request_id=str(v2_run.get("request_id") or ""),
                             runner_pid=pid, status="running",
                             last_heartbeat=utc_now())
                updates.append(
                    f"- v2 灰度命中：{decision.route} 任务已发布至蜂群 v2 市场"
                    f"（run_id={run_id}）。"
                )
            except Exception as exc:  # noqa: BLE001 -- v2 must never drop the task
                fallback_reason = (
                    f"v2 content submit failed: {type(exc).__name__}: {exc}")
                LOGGER.warning("company_router content v2 fallback: %s", fallback_reason)
        elif gray["enabled"]:
            # v2 was administratively enabled but not selected: keep the original
            # content path and record why (same style as batch 1's fallback).
            fallback_reason = str(gray.get("reason") or "v2_gray_not_selected")
            LOGGER.info("company_router content v2 not selected: %s", fallback_reason)

        if run is None:
            try:
                run_id = str(uuid.uuid4())
                pid = launch_content_job(
                    config,
                    run_id,
                    route=decision.route,
                    message=message,
                    session_id=session_id,
                    platform=platform,
                )
                run = {"run_id": run_id, "status": "running"}
                fields: dict[str, Any] = {
                    "run_id": run_id, "runner_pid": pid, "status": "running",
                    "last_heartbeat": utc_now(),
                }
                if fallback_reason:
                    fields["error"] = f"v2 content gray fallback: {fallback_reason}"
                    updates.append(f"- v2 内容灰度回退：{fallback_reason}")
                state.update(event_id, **fields)
            except Exception as exc:  # noqa: BLE001 -- one failed auto-submit must not abort the sweep
                state.update(event_id, status="failed", error=str(exc))
                updates.append(f"- 内容产线自动提交失败：{exc}")

    return {"context": build_context(decision, run=run, status_updates=updates)}


def dispatch_dev_explicit(
    config: dict[str, Any],
    *,
    message: str,
    session_id: str,
    platform: str,
    dev_repo: str | None = None,
    test_command: Any = None,
    files: Any = None,
) -> dict[str, Any]:
    """显式 dev 派发(W14-b 触发①;不过分类器,用户测试用这条)。

    构造一条 dev `RouteDecision`,由 `submit_dev_v2` 自证三道门(公司闸/灰度/
    身份)与仓库位,再由 `launch_v2_dev_worker` 过 dev 档开关门并起进程。任一
    前置不满足 ⇒ 异常上抛,CLI 捕获后响亮报错(退出码 2),不产生任何提交/进程。
    显式路径把 `gray` 以 `hit=True` 传入(显式选择 = 100% 命中,与比例灰度无关)。
    """
    decision = RouteDecision(
        route="dev", confidence=1.0, action="dispatch_swarm",
        reason="explicit --route dev", intent="custom")
    submitted = submit_dev_v2(
        config, decision=decision, message=message, session_id=session_id,
        platform=platform,
        gray={"hit": True, "enabled": True, "reason": "explicit_route"},
        dev_repo=dev_repo, test_command=test_command, files=files)
    run_id = str(submitted.get("run_id") or "")
    pid = launch_v2_dev_worker(config, run_id, dev_repo=dev_repo)
    return {**submitted, "runner_pid": pid}


def parse_hook_stdin() -> dict[str, Any]:
    # The payload is written by an external process: bound the read so a
    # runaway writer to the hook pipe cannot exhaust memory.
    raw = sys.stdin.read(MAX_HOOK_STDIN_BYTES + 1)
    if len(raw) > MAX_HOOK_STDIN_BYTES:
        raise ValueError(f"hook payload exceeds {MAX_HOOK_STDIN_BYTES} bytes")
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("hook payload must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Company product-line router")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--hook", action="store_true", help="Read Hermes hook JSON from stdin")
    parser.add_argument("--message", default="", help="Classify one message")
    parser.add_argument("--session-id", default="cli-test")
    parser.add_argument("--platform", default="cli")
    parser.add_argument("--dispatch", action="store_true", help="Submit eligible security message")
    parser.add_argument("--llm-fallback", action="store_true", help="Allow the low-confidence LLM tie-break")
    parser.add_argument("--route", default="", choices=["", "dev"],
                        help="显式路由:dev = 直接走 dev 提交口(不依赖分类器,W14-b 触发①)")
    parser.add_argument("--dev-repo", default=None,
                        help="dev 线被测仓库目录(缺省回退 swarm_v2_dev_repo;绝不猜默认仓库)")
    parser.add_argument("--dev-test-cmd", default=None,
                        help="dev 线 test_command(argv JSON 数组或空白分隔字符串)")
    parser.add_argument("--dev-files", default=None,
                        help="dev 线 files(JSON 数组或逗号/空白分隔的相对路径)")
    args = parser.parse_args()
    config = load_config(Path(args.config))

    if args.hook:
        print(json.dumps(handle_hook(parse_hook_stdin(), config), ensure_ascii=False))
        return 0

    if args.route == "dev":
        # 显式 dev 派发:不依赖分类器;闸/灰度/身份/仓库/开关任一不满足 ⇒ 响亮拒绝。
        if not args.dispatch:
            print(json.dumps({
                "route": "dev",
                "action": "dispatch_swarm",
                "dev_repo": args.dev_repo or config.get(_V2_DEV_REPO_KEY) or "",
                "dispatch_dev": bool(config.get("dispatch_dev", False)),
            }, ensure_ascii=False, indent=2))
            return 0
        try:
            out = dispatch_dev_explicit(
                config, message=args.message, session_id=args.session_id,
                platform=args.platform, dev_repo=args.dev_repo,
                test_command=args.dev_test_cmd, files=args.dev_files)
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"dev 显式派发失败: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.llm_fallback:
        decision = classify_with_fallback(args.message, config, config.get("authorized_targets") or [])
    else:
        decision = classify_message(args.message, config.get("authorized_targets") or [])
    if not args.dispatch:
        print(json.dumps(asdict(decision), ensure_ascii=False, indent=2))
        return 0

    payload = {
        "session_id": args.session_id,
        "extra": {"user_message": args.message, "platform": args.platform},
    }
    print(json.dumps(handle_hook(payload, config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
