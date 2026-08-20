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
import os
import re
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
        file_lock,
        locked_atomic_write_text,
        quote_identifier,
        read_text_limited,
        scrub_environment,
        sqlite_uri,
    )
except ImportError:  # direct ``python automation/company_router.py`` invocation
    from _safe_io import (
        file_lock,
        locked_atomic_write_text,
        quote_identifier,
        read_text_limited,
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


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    data = json.loads(read_text_limited(path, max_bytes=5 * 1024 * 1024))
    if not isinstance(data, dict):
        raise ValueError("router config must be an object")
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


def _score(text: str, terms: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term.lower() in lowered)


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
    if value.startswith(("/home/pwn/workspace/", "localhost")):
        return True
    if target_type == "ip":
        try:
            addr = ipaddress.ip_address(target)
            return addr.is_private or addr.is_loopback
        except ValueError:
            return False
    return False


def classify_message(message: str, authorized_targets: Iterable[str] = ()) -> RouteDecision:
    text = " ".join((message or "").split())

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
    for prefix, route in (
        ("/research", "security"), ("/security", "security"),
        ("研究：", "security"), ("安全：", "security"),
        ("/article", "article"), ("文章：", "article"),
        ("/video", "video"), ("视频：", "video"),
        ("/company", "company"), ("公司：", "company"),
    ):
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
    elif _is_company_execution_request(text):
        route = "company"
        confidence = 0.84
    else:
        return _main_agent_decision(
            "未识别到明确的生产/执行动作，交由公司主 Agent 判断。",
            external_action=external_action,
            confidence=0.45,
        )

    # Management questions that happen not to contain a question mark remain
    # with the main agent unless they also contain a clear execution directive.
    if (
        not explicit
        and route == "company"
        and _contains_any(text, COMPANY_TERMS)
        and _contains_any(text, MANAGEMENT_TERMS)
        and not _is_company_execution_request(text)
    ):
        return _main_agent_decision(
            "公司状态/流程管理问题，交由公司主 Agent 处理。",
            external_action=external_action,
        )

    if route != "security":
        action = {
            "article": "dispatch_article",
            "video": "dispatch_video",
            "research": "dispatch_swarm",  # 蜂群研究路由 (2026-08-10): 复用蜂群执行链路
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
            timeout=int(config.get("llm_fallback_timeout_seconds", timeout)),
            check=False,
        )
    except Exception:
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
    decision = classify_message(message, authorized_targets)
    if not config.get("llm_fallback_enabled", True):
        return decision

    router_mode = str(config.get("router_mode", "keyword")).strip().lower()

    # Common fast paths for both modes: empty/synthetic (0.0) and external action
    if decision.confidence <= 0.0 or decision.external_action:
        return decision
    if not " ".join((message or "").split()):
        return decision

    # 确定性判定已锁定业务线（article/video/security/company 均为强模式匹配
    # 结果，置信度 0.84-0.99），不允许 LLM 兜底推翻。
    # 历史教训：hybrid 模式下 0.84 的 company 判定被 LLM 兜底改判为 article
    # （“先将公司文章产线整理好”→article 0.95），造成产线被误分发污染。
    if decision.route != "main_agent":
        return decision
    # main_agent 判定但置信度不低（≥0.6，即已识别为公司相关但缺执行指令，
    # 或管理/数据/流程问题），同样保持主 Agent 处理，不交给 LLM 改判。
    if decision.confidence >= 0.6:
        return decision

    if router_mode == "hybrid":
        skip_threshold = float(config.get("hybrid_high_confidence_skip", 0.86))
    else:
        skip_threshold = float(config.get("llm_fallback_confidence", 0.5))
    if decision.confidence >= skip_threshold:
        return decision

    classifier = fallback or _llm_fallback_classify
    try:
        result = classifier(message, config)
    except Exception:
        return decision
    if not isinstance(result, dict):
        return decision
    route = str(result.get("route") or "").strip().lower()
    try:
        llm_confidence = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return decision
    prefix = _LLM_FALLBACK_PREFIX.get(route)
    threshold = float(config.get("llm_fallback_confidence", 0.5))
    if not prefix or llm_confidence < threshold:
        return decision

    # LLM 兜底不允许产生 article/video 路由：内容生产是产线级动作，必须由
    # 确定性规则（写/生成/排版等强模式）识别。历史误分发全部由此产生
    # （“你能自动下载公众号统计信息吗”→“文章”→article 0.95），而真正
    # 的文章请求确定性规则已能识别（0.86+），无需 LLM 兜底。
    if route in ("article", "video"):
        return decision

    upgraded = classify_message(prefix + message, authorized_targets)

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
        except Exception:  # noqa: BLE001, S110 -- destructor must never raise
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
                    return str(existing["route_event_id"])
                raise
        return event_id

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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise RuntimeError(f"command did not return JSON: {text[-500:]}")


def swarm_command(config: dict[str, Any], *args: str, timeout: int = 30) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarmctl.py"),
        "--db", config["swarm_db"],
        *args,
        "--json",
    ]
    proc = subprocess.run(cmd, cwd=config["swarm_repo"], capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"swarmctl exited {proc.returncode}")
    return _parse_json_output(proc.stdout)


def submit_security(config: dict[str, Any], session_id: str, platform: str, message: str, decision: RouteDecision, product_line: str = "security-exploration") -> dict[str, Any]:
    metadata = json.dumps(
        {
            "company_product_line": product_line,
            "company_session_id": session_id,
            "company_platform": platform,
            "router_version": 1,
            "authorization_marker_present": not decision.authorization_required,
        },
        ensure_ascii=False,
    )
    args = [
        "task", "submit",
        "--source", "company-router",
        "--task", message,
        "--intent", decision.intent,
        "--target-type", decision.target_type,
        "--profile", decision.profile,
        "--name", f"company-{decision.intent}-{session_id[-8:] or 'session'}",
        "--metadata", metadata,
    ]
    if decision.target:
        args.extend(["--target", decision.target])
    return swarm_command(config, *args)


def runner_role_counts(intent: str) -> str:
    return {
        "recon": "scanner=2,analyst=1,reporter=1",
        "exploit": "analyst=1,exploiter=1,reporter=1",
        "report": "reporter=1",
        "analyze": "analyst=1,reporter=1",
        # 蜂群研究路由 (2026-08-12): research 产品线使用独立 researcher 角色
        "research": "researcher=2,reporter=1",
    }.get(intent, "analyst=1,reporter=1")


def build_runner_cmd(config: dict[str, Any], run_id: str, intent: str) -> list:
    """构造 swarm runner 启动命令 (纯函数, 可测)。

    2026-08-10 教训: swarm_runner.py 从仓库根目录移到 scripts/ 后,
    此处引用未同步, 导致 dispatch_swarm 全部失败 (can't open file)。
    该函数由集成测试覆盖路径有效性, 防止重构回归。
    """
    return [
        sys.executable,
        str(Path(config["swarm_repo"]) / "scripts" / "swarm_runner.py"),
        "--db", config["swarm_db"],
        "--run-id", run_id,
        "--executor-command", config["executor"],
        "--role-counts", runner_role_counts(intent),
        "--max-rounds", "30",
        "--idle-rounds", "2",
        "--json",
    ]


def launch_runner(config: dict[str, Any], run_id: str, intent: str) -> int:
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"swarm-{run_id}.log"
    cmd = build_runner_cmd(config, run_id, intent)
    runner_env, _dropped = scrub_environment()
    runner_env["COMPANY_ROUTER_BYPASS"] = "1"
    runner_env["HERMES_SESSION_SOURCE"] = "tool"
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=config["swarm_repo"],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=runner_env,
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


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


def select_company_result(result: dict[str, Any]) -> str:
    """Prefer the latest completed worker result over reporter-first ordering.

    The swarm client intentionally favors reporter tasks for generic callers,
    but company routing must account for a later analyst correcting an earlier
    synthesis. Diff previews are self-reports from an isolated worker and are
    lower priority than a direct evidence-backed conclusion.
    """
    candidates = []
    for task in result.get("task_results") or []:
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
        candidates.append((str(task.get("ended_at") or ""), not is_diff_preview, content))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]
    return str(result.get("result") or result.get("summary") or "")


def refresh_session_runs(config: dict[str, Any], state: RouterState, session_id: str) -> list[str]:
    updates: list[str] = []
    for row in state.active_for_session(session_id):
        run_id = row["run_id"]
        try:
            result = swarm_command(config, "task", "result", "--run-id", run_id, timeout=15)
        except Exception as exc:
            updates.append(f"- 蜂群 {run_id[:8]} 状态查询失败：{exc}")
            continue
        status = result.get("status", "unknown")
        state.update(row["route_event_id"], status=status)
        if status == "completed" and not row["result_delivered"]:
            limit = int(config.get("result_context_chars", 6000))
            content = select_company_result(result)[:limit]
            updates.append(f"- 蜂群 {run_id[:8]} 已完成。结果：\n{content}")
            state.update(row["route_event_id"], result_delivered=1)
            # Classify output quality for security runs
            if not row.get("quality_status"):
                try:
                    from .operations_control import _classify_security_findings
                except ImportError:
                    from operations_control import _classify_security_findings
                try:
                    quality = _classify_security_findings(
                        run_id,
                        swarm_db=Path(config.get("swarm_db", "")),
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
                if status_path.is_symlink():
                    raise ValueError("status file may not be a symlink")
                payload = json.loads(read_text_limited(status_path, max_bytes=2 * 1024 * 1024))
                if not isinstance(payload, dict):
                    raise ValueError("status root must be an object")
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                updates.append(f"- {label}任务 {row['run_id'][:8]} 状态读取失败：{exc}")
                continue
            status = str(payload.get("status") or "unknown")
            state.update(row["route_event_id"], status=status)
            if status == "completed" and not row["result_delivered"]:
                result = str(payload.get("result") or "任务已完成。")
                artifacts = payload.get("artifacts") or []
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
    except Exception as exc:
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
            # and require at least 1 extra user message (meaning at least 2 total).
            return int(user_count["c"]) >= (min_user_messages + 1) and int(total_count["c"]) >= min_total_messages
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
    """Determines if a task should proceed, be skipped, or require approval BEFORE dispatch.

    Returns one of: 'proceed', 'skip', 'skip_low_confidence', 'needs_approval'
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
        min_messages = int(config.get("pre_eval_skill_review_min_messages", PRE_EVAL_MIN_PRIOR_MESSAGES))
        min_user = int(config.get("pre_eval_skill_review_min_user_messages", PRE_EVAL_MIN_PRIOR_USER_MESSAGES))
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
    threshold = int(config.get("circuit_breaker_threshold", 3))
    if threshold <= 0:
        return None
    operations_db_value = str(config.get("operations_db") or "").strip()
    if not operations_db_value:
        return None
    window_hours = int(config.get("circuit_breaker_window_hours", 24))
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
    message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    state = RouterState(config["state_db"])
    updates = refresh_session_runs(config, state, session_id)
    updates.extend(refresh_session_content_jobs(config, state, session_id))

    existing = state.existing(session_id, message_hash)
    if existing:
        decision = RouteDecision(**json.loads(existing["decision_json"]))
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
        dedup_minutes = int(config.get("swarm_dedup_window_minutes", 10))
        recent = state.recent_for_session(
            session_id,
            decision.action,
            datetime.now(timezone.utc) - timedelta(minutes=dedup_minutes),
            dedup_key=dedup_key,
        )
        if recent:
            state.update(
                recent["route_event_id"],
                delivery_attempts=int(recent["delivery_attempts"] or 0) + 1,
            )
            return {"context": build_context(
                RouteDecision(**json.loads(recent["decision_json"])),
                existing_run_id=recent["run_id"],
                existing_status=recent["status"],
                status_updates=updates,
            )}

    is_skill_review = decision.action == "dispatch_company" and SKILL_REVIEW_MARKER in message.lower()
    if is_skill_review:
        cooldown_hours = int(config.get("content_job_skill_review_cooldown_hours", 4))
        recent = state.recent_for_session(
            session_id,
            decision.action,
            datetime.now(timezone.utc) - timedelta(hours=cooldown_hours),
            completed_only=True,
            message_marker=SKILL_REVIEW_MARKER,
        )
        if recent:
            state.update(
                recent["route_event_id"],
                delivery_attempts=int(recent["delivery_attempts"] or 0) + 1,
            )
            return {"context": build_context(
                RouteDecision(**json.loads(recent["decision_json"])),
                existing_run_id=recent["run_id"],
                existing_status=recent["status"],
                status_updates=updates,
            )}

    origin = resolve_session_origin(str(config.get("gateway_sessions_index") or ""), session_id)
    event_id = state.insert(
        session_id,
        platform,
        message_hash,
        message,
        decision,
        origin=origin,
        dedup_key=dedup_key,
    )
    run: dict[str, Any] | None = None

    # Pre-evaluation gate: check task value BEFORE dispatch
    pre_eval = _pre_evaluate_task(decision, message, session_id, config)
    if pre_eval == "skip":
        state.update(event_id, status="skipped", error="pre-evaluation: no meaningful work to perform")
        return {"context": build_context(
            decision,
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
    elif pre_eval == "needs_approval":
        state.update(event_id, status="waiting_approval")
        return {"context": build_context(
            RouteDecision(**{**asdict(decision), "action": "approval_required",
                           "reason": "pre-evaluation: task requires user review before execution"}),
            status_updates=updates + [
                "- [预评估] 此任务被标记为需要用户审批，已暂停派发。"
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
        # 2026-08-10: research 路由复用蜂群链路, 由独立开关控制
        enabled = config.get(
            "dispatch_research" if decision.route == "research" else "dispatch_security", True
        )
        if not enabled:
            state.update(event_id, status="deferred", error="product line dispatch disabled")
            updates.append(
                f"- {decision.route} 自动分发已禁用 (dispatch_{decision.route}=false)，已交由主 Agent。"
            )
        else:
            active = [row for row in state.active_for_session(session_id) if row["status"] in {"submitted", "running"}]
            if len(active) >= int(config.get("max_active_runs_per_session", 2)):
                state.update(event_id, status="deferred", error="active run limit reached")
                return {"context": build_context(
                    RouteDecision(**{**asdict(decision), "action": "main_agent", "reason": "active run limit reached"}),
                    status_updates=updates + ["- 已达到本会话并发蜂群上限，新任务暂未提交。"],
                )}
            try:
                run = submit_security(
                    config, session_id, platform, message, decision,
                    product_line="research" if decision.route == "research" else "security-exploration",
                )
                fields: dict[str, Any] = {
                    "run_id": str(run.get("run_id") or ""),
                    "request_id": str(run.get("request_id") or ""),
                    "status": "submitted",
                    "last_heartbeat": utc_now(),
                }
                if config.get("auto_run_security", True) and fields["run_id"]:
                    fields["runner_pid"] = launch_runner(config, fields["run_id"], decision.intent)
                    fields["status"] = "running"
                state.update(event_id, **fields)
            except Exception as exc:
                state.update(event_id, status="failed", error=str(exc))
                updates.append(f"- 自动提交失败：{exc}")
    elif decision.action in {"dispatch_article", "dispatch_video", "dispatch_company"}:
        enabled_key = {
            "dispatch_article": "auto_run_article",
            "dispatch_video": "auto_run_video",
            "dispatch_company": "auto_run_company",
        }[decision.action]
        if config.get(enabled_key, True):
            active = state.active_for_session(session_id, action=decision.action)
            running = [row for row in active if row["status"] in {"submitted", "running"}]
            if len(running) >= int(config.get("max_active_content_jobs_per_session", 2)):
                state.update(event_id, status="deferred", error="active content job limit reached")
                updates.append("- 已达到本会话内容产线并发上限，新任务暂未提交。")
            else:
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
                    state.update(event_id, run_id=run_id, runner_pid=pid, status="running", last_heartbeat=utc_now())
                except Exception as exc:
                    state.update(event_id, status="failed", error=str(exc))
                    updates.append(f"- 内容产线自动提交失败：{exc}")

    return {"context": build_context(decision, run=run, status_updates=updates)}


def parse_hook_stdin() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("hook payload must be a JSON object")
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
    args = parser.parse_args()
    config = load_config(Path(args.config))

    if args.hook:
        print(json.dumps(handle_hook(parse_hook_stdin(), config), ensure_ascii=False))
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
