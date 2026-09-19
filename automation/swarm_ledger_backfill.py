#!/usr/bin/env python3
"""C-4 公司侧账本回填接线(v2 run 收口 ⇒ 公司账本 ⇒ 蜂群回填)。

背景(实测):蜂群 `src/swarm_v2/backfill.py` 的三层门实现完好
(总闸 `scheduler_policy` ∧ `company_backfill` 开关 ∧ 已上线),审计
`audit_events('ops_backfill')`;但公司侧**零调用者**。本模块把这条链接上:

    v2 run 收口(库内真值:`swarm_runs` 终态 / `agent_tasks` 终态)
      ⇒ 按 `agent_tasks.token_cost` 实测 token × `finance_ledger.model_prices`
        该 provider/model 的价格换算金额
      ⇒ 写公司账本 `actual_transactions`(source_ref = ``swarm:<run_id>/<task_id>``,
        evidence_path/sha256 指向该 task 的审计导出文件)
      ⇒ 调 `swarmctl company backfill <task_id> --ledger-ref <source_ref> ...`。

**纪律(逐条)**:
  - **可追溯**:金额只来自 ① `agent_tasks.token_cost` 实测值 ②
    `model_prices` 里匹配 provider/model 的价格。缺 token / 缺价格 ⇒
    **标"未定价"并登记待补**,`token_cost<=0` 与无价格一律不发回填请求、
    不写账本、**不编数、不用 0 冒充**。
  - **幂等**:同一 `task_id` 只回填一次 —— 双判定 = 公司账本已有
    `source_ref` **且** 蜂群已有 `ops_backfill` 审计;重跑/重放零写入。
  - **响亮失败**:蜂群三层门关时 `swarmctl` rc=2(不吞)。公司侧落一条
    "待回填"登记(独立 SQLite 文件,**不是**账本 DB,故"零账本写入"
    字面成立),下一次运行重放;CLI 有失败时返回非零,不静默。
  - **只读蜂群库**:读 `agent_tasks`/`swarm_runs`/`audit_events` 一律
    `mode=ro`;唯一对蜂群的写经由 `swarmctl company backfill`(开关决定)。

**边界(自行判断,报告列明)**:只处理 `acceptance_status='accepted'` 的终态任务
(G16:rejected = V=0,背不出可回填对象,重放也不会成功);`pending` 且绑定
`ledger-attribution` 的判定回填路径仍由蜂群 CLI 承接,本模块不代判定。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

try:  # package import (``automation.swarm_ledger_backfill``)
    from . import finance_ledger
    from ._safe_io import sqlite_uri
except ImportError:  # direct ``python automation/swarm_ledger_backfill.py``
    import finance_ledger  # type: ignore[no-redef]
    from _safe_io import sqlite_uri  # type: ignore[no-redef]


#: agent_tasks 终态闭集(run 收口的库内真值;run_finalize.finalize_if_done 同源)。
TERMINAL_TASK_STATUSES: tuple[str, ...] = ("completed", "failed", "timeout")
#: swarm_runs 收口终态(run_finalize 写入)。
FINAL_RUN_STATUSES: tuple[str, ...] = ("completed", "failed")
#: 蜂群审计枚举(backfill.BACKFILL_EVENT 单一来源;此处只读比对)。
BACKFILL_AUDIT_EVENT = "ops_backfill"
#: 幂等/可追溯键:`swarm:<run_id>/<task_id>`。
SOURCE_REF_TEMPLATE = "swarm:{run_id}/{task_id}"
#: 回填操作者(审计留痕)。
DEFAULT_BACKFILL_BY = "company-backfill"
#: model_prices.unit 口径(与 pricing.py 一致:每百万 token)。
PRICE_UNIT = "millionTokens"
PER_MILLION = 1_000_000
#: 待补登记状态。
STATUS_PENDING = "pending"      # 曾被三层门拒绝/CLI 失败,可重放
STATUS_UNPRICED = "unpriced"    # 缺 token / 缺价格,待补数据后才可回放
STATUS_DONE = "done"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class BackfillError(RuntimeError):
    """C-4 接线领域错误(缺路径/缺列等;CLI 干净报错,不吐 traceback)。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_ref_for(run_id: str, task_id: str) -> str:
    return SOURCE_REF_TEMPLATE.format(run_id=run_id, task_id=task_id)


# ---------------------------------------------------------------------------
# 路径解析(所有 DB 位置显式可注入;生产默认来自 router_config)
# ---------------------------------------------------------------------------

def resolve_paths(config: dict[str, Any]) -> dict[str, Any]:
    """把 router_config 解析成 C-4 需要的显式路径。

    - `swarm_v2_db` 缺键时回落 `swarm_db`(与既有 swarm_command 同口径);
    - `finance_ledger_db` 默认公司账本活库;
    - 待补登记 DB / 证据目录默认落在 `state_db` 同级 `operations/runtime/`
      下(**独立于账本 DB**,故开关关时"零账本写入")。
    """
    swarm_repo = str(config.get("swarm_repo") or "").strip()
    swarm_db = str(config.get("swarm_v2_db") or config.get("swarm_db") or "").strip()
    ledger_db = Path(str(config.get("finance_ledger_db") or finance_ledger.DEFAULT_DB))
    state_db = str(config.get("state_db") or "").strip()
    if state_db:
        base = Path(state_db).parent
    else:
        base = Path(finance_ledger.COMPANY_ROOT) / "operations" / "runtime"
    pending_db = Path(
        str(config.get("swarm_backfill_pending_db") or (base / "swarm_backfill_pending.db"))
    )
    evidence_dir = Path(
        str(config.get("swarm_backfill_evidence_dir") or (base / "swarm-backfill-evidence"))
    )
    swarm_python = str(config.get("swarm_python") or "").strip()
    if not swarm_python:
        candidate = Path(swarm_repo) / ".venv" / "bin" / "python"
        swarm_python = str(candidate) if candidate.is_file() else sys.executable
    return {
        "swarm_repo": swarm_repo,
        "swarm_db": swarm_db,
        "swarm_python": swarm_python,
        "swarmctl": str(Path(swarm_repo) / "scripts" / "swarmctl.py") if swarm_repo else "",
        "ledger_db": ledger_db,
        "pending_db": pending_db,
        "evidence_dir": evidence_dir,
    }


def _require_swarm_paths(paths: dict[str, Any]) -> None:
    if not paths["swarm_db"] or not Path(paths["swarm_db"]).is_file():
        raise BackfillError(f"swarm_v2 活库缺失: {paths['swarm_db']!r}")
    if not paths["swarmctl"] or not Path(paths["swarmctl"]).is_file():
        raise BackfillError(f"swarmctl 缺失: {paths['swarmctl']!r}")


@contextmanager
def _ro(path: Path) -> Iterator[sqlite3.Connection]:
    """只读连接(结束即关闭;绝不写库)。"""
    con = sqlite3.connect(sqlite_uri(Path(path), mode="ro"), uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 待补登记(独立 SQLite;可重放;不是账本 DB)
# ---------------------------------------------------------------------------

_PENDING_DDL = """
CREATE TABLE IF NOT EXISTS swarm_backfill_pending (
    task_id       TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL DEFAULT '',
    source_ref    TEXT NOT NULL DEFAULT '',
    amount        REAL,
    currency      TEXT NOT NULL DEFAULT '',
    unit          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    reason        TEXT NOT NULL DEFAULT '',
    attempts      INTEGER NOT NULL DEFAULT 0,
    txn_id        TEXT NOT NULL DEFAULT '',
    evidence_path TEXT NOT NULL DEFAULT '',
    evidence_sha256 TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""

#: 可写入 pending 表的列白名单(防拼接注入)。
_PENDING_COLUMNS = frozenset({
    "run_id", "source_ref", "amount", "currency", "unit", "status", "reason",
    "attempts", "txn_id", "evidence_path", "evidence_sha256",
})


class PendingStore:
    """待回填/未定价登记(幂等 upsert;重放输入)。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10.0)
        con.row_factory = sqlite3.Row
        try:
            con.execute(_PENDING_DDL)
            yield con
        finally:
            con.close()

    def upsert(self, task_id: str, **fields: Any) -> None:
        unknown = set(fields) - _PENDING_COLUMNS
        if unknown:
            raise BackfillError(f"pending 未知列: {sorted(unknown)}")
        now = utc_now()
        with self._open() as con:
            row = con.execute(
                "SELECT attempts FROM swarm_backfill_pending WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO swarm_backfill_pending(task_id, created_at, updated_at)"
                    " VALUES (?,?,?)", (task_id, now, now))
                attempts = int(fields.pop("attempts", 0) or 0)
            else:
                attempts = int(fields.pop("attempts", row["attempts"]) or 0)
            assignments = ["updated_at=?", "attempts=?"]
            values: list[Any] = [now, attempts]
            for key, value in fields.items():
                assignments.append(f"{key}=?")
                values.append(value)
            values.append(task_id)
            con.execute(
                f"UPDATE swarm_backfill_pending SET {', '.join(assignments)}"  # nosec B608 -- fixed whitelist
                " WHERE task_id=?",
                values,
            )
            con.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with self._open() as con:
            row = con.execute(
                "SELECT * FROM swarm_backfill_pending WHERE task_id=?", (task_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def open_rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._open() as con:
            rows = con.execute(
                "SELECT * FROM swarm_backfill_pending WHERE status IN (?,?)"
                " ORDER BY updated_at, task_id",
                (STATUS_PENDING, STATUS_UNPRICED),
            ).fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 蜂群库只读读数
# ---------------------------------------------------------------------------

_TASK_COLUMNS = (
    "task_id", "run_id", "agent_id", "run_type", "task_type", "status",
    "acceptance_status", "token_cost", "estimated_tokens", "value_pack_id",
    "model_profile_id", "judge_provider", "ended_at", "updated_at",
)


def read_task(swarm_db: Path, task_id: str) -> dict[str, Any] | None:
    try:
        with _ro(Path(swarm_db)) as con:
            row = con.execute(
                f"SELECT {', '.join(_TASK_COLUMNS)} FROM agent_tasks WHERE task_id=?",  # nosec B608 -- fixed column list
                (task_id,),
            ).fetchone()
            return dict(row) if row is not None else None
    except sqlite3.Error as exc:
        raise BackfillError(f"读取 agent_tasks 失败: {exc}") from exc


def read_run(swarm_db: Path, run_id: str) -> dict[str, Any] | None:
    try:
        with _ro(Path(swarm_db)) as con:
            row = con.execute(
                "SELECT run_id, run_type, status, ended_at, updated_at,"
                " tokens_spent, token_budget FROM swarm_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            return dict(row) if row is not None else None
    except sqlite3.Error as exc:
        raise BackfillError(f"读取 swarm_runs 失败: {exc}") from exc


def accepted_terminal_tasks(swarm_db: Path, run_id: str) -> list[str]:
    """某 run 内可回填的任务:终态且 accepted(按 backfill 契约)。"""
    placeholders = ",".join("?" * len(TERMINAL_TASK_STATUSES))
    try:
        with _ro(Path(swarm_db)) as con:
            rows = con.execute(
                f"SELECT task_id FROM agent_tasks WHERE run_id=?"  # nosec B608 -- fixed placeholders
                f" AND status IN ({placeholders}) AND acceptance_status='accepted'"
                " AND task_type<>'review' ORDER BY task_id",
                (run_id, *TERMINAL_TASK_STATUSES),
            ).fetchall()
            return [str(r[0]) for r in rows]
    except sqlite3.Error as exc:
        raise BackfillError(f"读取 run 任务失败: {exc}") from exc


def recent_finalized_runs(swarm_db: Path, *, days: int = 30) -> list[str]:
    try:
        with _ro(Path(swarm_db)) as con:
            rows = con.execute(
                "SELECT run_id FROM swarm_runs WHERE status IN ('completed','failed')"
                " AND COALESCE(ended_at, updated_at) >= datetime('now', ?)"
                " ORDER BY COALESCE(ended_at, updated_at) DESC, run_id",
                (f"-{int(days)} day",),
            ).fetchall()
            return [str(r[0]) for r in rows]
    except sqlite3.Error as exc:
        raise BackfillError(f"读取 swarm_runs 失败: {exc}") from exc


def swarm_has_backfill(swarm_db: Path, task_id: str, ledger_ref: str) -> bool:
    """蜂群是否已有该 (task_id, ledger_ref) 的 `ops_backfill` 审计(幂等半判定)。"""
    try:
        with _ro(Path(swarm_db)) as con:
            rows = con.execute(
                "SELECT payload_json FROM audit_events WHERE event_type=?",
                (BACKFILL_AUDIT_EVENT,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise BackfillError(f"读取 audit_events 失败: {exc}") from exc
    for row in rows:
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("task_id") == task_id and payload.get("ledger_ref") == ledger_ref:
            return True
    return False


# ---------------------------------------------------------------------------
# 定价(token_cost × model_prices;缺一即"未定价")
# ---------------------------------------------------------------------------

def _base_slug(slug: Any) -> str:
    return str(slug or "").strip().lower().rsplit("/", 1)[-1]


def _load_price_rows(ledger_db: Path) -> list[dict[str, Any]]:
    path = Path(ledger_db)
    if not path.is_file():
        return []
    try:
        with _ro(path) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(model_prices)")}
            if not {"provider", "model_slug", "currency", "unit"}.issubset(cols):
                return []
            rows = con.execute(
                "SELECT provider, model, model_slug, currency, unit,"
                " input_price, output_price FROM model_prices"
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {k: row[k] for k in row.keys()}
        if str(item.get("unit") or "").strip().lower() != PRICE_UNIT.lower():
            continue
        out.append(item)
    return out


def resolve_model(swarm_db: Path, task: dict[str, Any],
                  fallback: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """解析任务的 provider/model(快照 model_profile 优先,回落 agent_profile)。

    `fallback` = 操作者在 router_config 显式配置的 provider/model
    (`swarm_v2_provider`/`swarm_v2_model`);只在任务无绑定且显式配置时才用,
    来源标 `config_fallback`(可追溯,不是编数)。
    """
    profile_id = task.get("model_profile_id")
    agent_id = task.get("agent_id")
    try:
        with _ro(Path(swarm_db)) as con:
            if profile_id:
                row = con.execute(
                    "SELECT provider, model FROM model_profiles WHERE profile_id=?",
                    (profile_id,),
                ).fetchone()
                if row is not None and (row["model"] or row["provider"]):
                    return {"provider": str(row["provider"] or ""),
                            "model": str(row["model"] or ""),
                            "source": "model_profile"}
            if agent_id:
                row = con.execute(
                    "SELECT model_preference FROM agent_profiles WHERE agent_id=?",
                    (agent_id,),
                ).fetchone()
                if row is not None and row["model_preference"]:
                    pref = str(row["model_preference"]).strip()
                    provider, _, model = pref.partition("/")
                    if not model:      # 只有 model 名,无 provider 前缀
                        provider, model = "", pref
                    return {"provider": provider.strip(), "model": model.strip(),
                            "source": "agent_profile"}
    except sqlite3.Error:
        pass
    if fallback and str(fallback.get("model") or "").strip():
        return {"provider": str(fallback.get("provider") or "").strip(),
                "model": str(fallback["model"]).strip(),
                "source": "config_fallback"}
    return {"provider": "", "model": "", "source": "unknown"}


def resolve_price(ledger_db: Path, provider: str, model: str) -> dict[str, Any] | None:
    """匹配 provider/model 的价格行;返回单一费率(先 output_price 后 input_price)。

    `agent_tasks.token_cost` 是 provider 上报的单一 total(input+output 未分列),
    故只能取一个费率:优先 `output_price`,缺失回落 `input_price`;两者皆缺 ⇒
    None(未定价,不编数)。
    """
    model_norm = str(model or "").strip().lower()
    if not model_norm:
        return None
    rows = _load_price_rows(ledger_db)
    base = _base_slug(model_norm)
    candidates = [
        r for r in rows
        if _base_slug(r.get("model_slug")) == base
        and (not provider
             or str(r.get("provider") or "").strip().lower() == provider.strip().lower())
    ]
    if not candidates:
        candidates = [r for r in rows if _base_slug(r.get("model_slug")) == base]
    if not candidates:
        return None
    usd = [r for r in candidates if str(r.get("currency") or "").upper() == "USD"]
    pool = usd or candidates
    exact = [r for r in pool if str(r.get("model_slug") or "").strip().lower() == model_norm]
    chosen = exact[0] if exact else pool[0]
    for component, column in (("output", "output_price"), ("input", "input_price")):
        value = chosen.get(column)
        if value is None:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if price >= 0:
            return {
                "price_value": price, "price_component": component,
                "currency": str(chosen.get("currency") or "").upper(),
                "model_slug": str(chosen.get("model_slug") or ""),
                "provider": str(chosen.get("provider") or ""),
            }
    return None


def plan_backfill(swarm_db: Path, ledger_db: Path, task: dict[str, Any],
                  model_fallback: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """金额计划:tokens × 单价 / 1e6;缺 token 或缺价格 ⇒ priced=False。"""
    tokens = task.get("token_cost")
    if isinstance(tokens, bool) or not isinstance(tokens, (int, float)) or tokens <= 0:
        return {"priced": False,
                "reason": f"token_cost 未测/为 0({tokens!r});缺实测 token 不编数"}
    model = resolve_model(swarm_db, task, fallback=model_fallback)
    if not model["model"]:
        return {"priced": False,
                "reason": "任务无 provider/model(model_profile/agent_profile 均无)",
                "model": model}
    price = resolve_price(ledger_db, model["provider"], model["model"])
    if price is None:
        return {"priced": False,
                "reason": f"model_prices 无 {model['provider'] or '?'}/"
                          f"{model['model']} 的 {PRICE_UNIT} 价格",
                "model": model}
    amount = round(float(tokens) * price["price_value"] / PER_MILLION, 6)
    if amount <= 0:
        return {"priced": False,
                "reason": f"换算金额为 0({tokens} × {price['price_value']})",
                "model": model}
    return {"priced": True, "tokens": int(tokens), "amount": amount,
            "currency": price["currency"], "unit": price["currency"] or "USD",
            "price": price, "model": model}


# ---------------------------------------------------------------------------
# 公司账本(source_ref 幂等判定)
# ---------------------------------------------------------------------------

def ledger_has_source_ref(ledger_db: Path, source_ref: str) -> bool:
    path = Path(ledger_db)
    if not path.is_file():
        return False
    try:
        with _ro(path) as con:
            row = con.execute(
                "SELECT 1 FROM actual_transactions WHERE source_ref=? LIMIT 1",
                (source_ref,),
            ).fetchone()
            return row is not None
    except sqlite3.Error as exc:
        raise BackfillError(f"读取公司账本失败: {exc}") from exc


def ledger_transaction_id(ledger_db: Path, source_ref: str) -> str:
    path = Path(ledger_db)
    if not path.is_file():
        return ""
    try:
        with _ro(path) as con:
            row = con.execute(
                "SELECT transaction_id FROM actual_transactions WHERE source_ref=?"
                " ORDER BY created_at LIMIT 1", (source_ref,),
            ).fetchone()
            return str(row[0]) if row is not None else ""
    except sqlite3.Error:
        return ""


# ---------------------------------------------------------------------------
# 蜂群回填 CLI(唯一对蜂群的写路径;三层门在蜂群侧)
# ---------------------------------------------------------------------------

def build_backfill_cmd(paths: dict[str, Any], *, task_id: str, source_ref: str,
                       amount: float, unit: str, evidence: str,
                       by: str = DEFAULT_BACKFILL_BY) -> list[str]:
    """`swarmctl company backfill` 命令行(参数面 = backfill.py 的真实契约)。

    注意:`backfill.main` 的 task_id 是**位置参数**(brief 里的 `--task-id`
    是简写);`--db` 由 `company` 分支透传给 backfill.main,故必须放在
    子命令参数之后(swarmctl 在 argparse 前短路 `company`)。
    """
    return [
        paths["swarm_python"], paths["swarmctl"], "company", "backfill", str(task_id),
        "--ledger-ref", str(source_ref),
        "--amount", repr(float(amount)),
        "--unit", str(unit),
        "--evidence", str(evidence),
        "--by", str(by),
        "--db", str(paths["swarm_db"]),
        "--json",
    ]


def _default_runner(cmd: list[str], *, cwd: str, timeout: int) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=cwd or None, capture_output=True, text=True,
                          timeout=timeout, check=False)
    return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


#: runner 注入签名(测试可替换;失败注入用)。
Runner = Callable[..., dict[str, Any]]


def run_swarm_backfill(paths: dict[str, Any], *, task_id: str, source_ref: str,
                       amount: float, unit: str, evidence: str,
                       runner: Optional[Runner] = None,
                       timeout: int = 30) -> dict[str, Any]:
    cmd = build_backfill_cmd(paths, task_id=task_id, source_ref=source_ref,
                             amount=amount, unit=unit, evidence=evidence)
    runner = runner or _default_runner
    try:
        result = runner(cmd, cwd=paths["swarm_repo"], timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"rc": -1, "stdout": "", "stderr": f"{exc.__class__.__name__}: {exc}",
                "cmd": cmd}
    result = dict(result or {})
    result["cmd"] = cmd
    return result


# ---------------------------------------------------------------------------
# 证据导出 + 账本行
# ---------------------------------------------------------------------------

def _safe_name(value: Any) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", str(value or "")).strip("._-")
    return cleaned[:120] or "x"


def write_evidence(paths: dict[str, Any], *, task: dict[str, Any],
                   run: dict[str, Any] | None, plan: dict[str, Any],
                   source_ref: str,
                   swarm_result: dict[str, Any] | None) -> tuple[Path, str]:
    """把该 task 的审计导出写成真实文件(账本 evidence_path 的来源)。"""
    evidence_dir = Path(paths["evidence_dir"])
    evidence_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(task.get("run_id") or "")
    task_id = str(task.get("task_id") or "")
    payload = {
        "kind": "company_swarm_ledger_backfill",
        "generated_at": utc_now(),
        "source_ref": source_ref,
        "task": {k: task.get(k) for k in _TASK_COLUMNS},
        "run": run,
        "pricing": plan,
        "swarm_backfill": swarm_result,
    }
    path = evidence_dir / f"{_safe_name(run_id)}__{_safe_name(task_id)}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def write_ledger_row(paths: dict[str, Any], *, task: dict[str, Any], plan: dict[str, Any],
                     source_ref: str, evidence_path: Path) -> str:
    run_id = str(task.get("run_id") or "")
    task_id = str(task.get("task_id") or "")
    description = (
        f"v2 run {run_id} task {task_id} 模型用量 "
        f"({plan['tokens']} tokens × {plan['price']['price_value']} "
        f"{plan['currency']}/{PRICE_UNIT} [{plan['price']['price_component']}_price], "
        f"provider={plan['model']['provider'] or '?'}, model={plan['model']['model']})"
    )
    occurred_at = str(task.get("ended_at") or task.get("updated_at") or "") or utc_now()
    return finance_ledger.add_actual(
        Path(paths["ledger_db"]),
        product_line="swarm",
        kind="expense",
        category="swarm-model-usage",
        amount=plan["amount"],
        currency=plan["currency"] or "USD",
        description=description,
        source_ref=source_ref,
        evidence_path=Path(evidence_path),
        occurred_at=occurred_at,
    )


# ---------------------------------------------------------------------------
# 单任务处理(幂等;重放同一入口)
# ---------------------------------------------------------------------------

def process_task(config: dict[str, Any], task_id: str, *,
                 runner: Optional[Runner] = None,
                 paths: Optional[dict[str, Any]] = None,
                 pending: Optional[PendingStore] = None) -> dict[str, Any]:
    """处理一个任务:幂等判定 → 定价 → 蜂群回填 → 公司账本。

    返回 `action ∈ {skip, done, unpriced, pending, error}`。
    `pending`/`unpriced` 表示**未完成且已登记待补**(响亮,不静默)。
    """
    paths = paths or resolve_paths(config)
    pending = pending or PendingStore(Path(paths["pending_db"]))
    _require_swarm_paths(paths)
    task = read_task(Path(paths["swarm_db"]), task_id)
    if task is None:
        return {"action": "skip", "task_id": task_id, "reason": "任务不存在"}
    run_id = str(task.get("run_id") or "")
    source_ref = source_ref_for(run_id, task_id)
    base = {"task_id": task_id, "run_id": run_id, "source_ref": source_ref}

    if str(task.get("status") or "") not in TERMINAL_TASK_STATUSES:
        return {**base, "action": "skip", "reason": f"任务非终态({task.get('status')!r})"}
    if str(task.get("acceptance_status") or "") != "accepted":
        return {**base, "action": "skip",
                "reason": f"任务非 accepted({task.get('acceptance_status')!r};G16 不背 V=0)"}

    ledger_done = ledger_has_source_ref(Path(paths["ledger_db"]), source_ref)
    swarm_done = swarm_has_backfill(Path(paths["swarm_db"]), task_id, source_ref)
    if ledger_done and swarm_done:
        return {**base, "action": "done", "duplicate": True,
                "ledger_txn_id": ledger_transaction_id(Path(paths["ledger_db"]), source_ref),
                "reason": "公司账本 source_ref + 蜂群 ops_backfill 审计双判定已回填(零写入)"}

    plan = plan_backfill(Path(paths["swarm_db"]), Path(paths["ledger_db"]), task,
                         model_fallback={
                             "provider": str(config.get("swarm_v2_provider") or ""),
                             "model": str(config.get("swarm_v2_model") or ""),
                         })
    if not plan["priced"]:
        pending.upsert(task_id, run_id=run_id, source_ref=source_ref,
                       status=STATUS_UNPRICED, reason=str(plan["reason"]),
                       amount=None, currency="", unit="", attempts=0)
        return {**base, "action": "unpriced", "priced": False,
                "reason": plan["reason"], "plan": plan,
                "note": "未定价 ⇒ 不发回填请求、不写账本(禁止编数/0 冒充)"}

    evidence = (f"公司账本 {source_ref}:task {task_id} run {run_id} "
                f"{plan['tokens']} tokens × {plan['price']['price_value']} "
                f"{plan['currency']}/{PRICE_UNIT} = {plan['amount']} "
                f"{plan['currency']};provider={plan['model']['provider'] or '?'},"
                f"model={plan['model']['model']}")
    swarm_result = run_swarm_backfill(paths, task_id=task_id, source_ref=source_ref,
                                      amount=plan["amount"], unit=plan["unit"],
                                      evidence=evidence, runner=runner)
    swarm_payload: dict[str, Any] | None = None
    if swarm_result.get("rc") == 0:
        try:
            parsed = json.loads((swarm_result.get("stdout") or "").strip())
            swarm_payload = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            swarm_payload = None
    if swarm_result.get("rc") != 0:
        reason = (swarm_result.get("stderr") or swarm_result.get("stdout") or "").strip()
        previous = pending.get(task_id) or {}
        attempts = int(previous.get("attempts") or 0) + 1
        pending.upsert(task_id, run_id=run_id, source_ref=source_ref,
                       status=STATUS_PENDING, reason=reason, amount=plan["amount"],
                       currency=plan["currency"], unit=plan["unit"], attempts=attempts)
        return {**base, "action": "pending", "ok": False,
                "swarm_rc": swarm_result.get("rc"),
                "reason": reason or f"swarmctl rc={swarm_result.get('rc')}",
                "amount": plan["amount"], "currency": plan["currency"],
                "unit": plan["unit"], "attempts": attempts,
                "note": "蜂群三层门拒绝/CLI 失败未吞;已登记待补,下次运行重放(幂等)"}

    ledger_txn_id = ""
    if not ledger_done:
        run = read_run(Path(paths["swarm_db"]), run_id)
        evidence_path, _sha = write_evidence(
            paths, task=task, run=run, plan=plan, source_ref=source_ref,
            swarm_result=swarm_payload)
        ledger_txn_id = write_ledger_row(paths, task=task, plan=plan,
                                         source_ref=source_ref, evidence_path=evidence_path)
    else:
        ledger_txn_id = ledger_transaction_id(Path(paths["ledger_db"]), source_ref)
    pending.upsert(task_id, run_id=run_id, source_ref=source_ref, status=STATUS_DONE,
                   reason="", amount=plan["amount"], currency=plan["currency"],
                   unit=plan["unit"], txn_id=ledger_txn_id,
                   attempts=int((pending.get(task_id) or {}).get("attempts") or 0))
    return {**base, "action": "done", "duplicate": bool(ledger_done),
            "ledger_txn_id": ledger_txn_id, "amount": plan["amount"],
            "currency": plan["currency"], "unit": plan["unit"],
            "swarm_backfill": swarm_payload, "plan": plan}


def process_finalized_runs(config: dict[str, Any], *, run_id: Optional[str] = None,
                           days: int = 30, runner: Optional[Runner] = None,
                           paths: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """扫描已收口 run 的 accepted 终态任务并逐条回填(幂等;每 tick 可跑)。"""
    paths = paths or resolve_paths(config)
    pending = PendingStore(Path(paths["pending_db"]))
    summary = {"runs": 0, "tasks": 0, "done": 0, "pending": 0, "unpriced": 0,
               "skipped": 0, "error": 0, "results": []}
    try:
        _require_swarm_paths(paths)
        run_ids = ([run_id] if run_id
                   else recent_finalized_runs(Path(paths["swarm_db"]), days=days))
    except BackfillError as exc:
        summary["error"] = 1
        summary["skipped_reason"] = str(exc)
        return summary
    for rid in run_ids:
        if not rid:
            continue
        summary["runs"] += 1
        try:
            task_ids = accepted_terminal_tasks(Path(paths["swarm_db"]), rid)
        except BackfillError as exc:
            summary["error"] += 1
            summary["results"].append({"run_id": rid, "action": "error", "reason": str(exc)})
            continue
        for task_id in task_ids:
            summary["tasks"] += 1
            try:
                result = process_task(config, task_id, runner=runner, paths=paths,
                                      pending=pending)
            except BackfillError as exc:
                summary["error"] += 1
                summary["results"].append({"task_id": task_id, "action": "error",
                                           "reason": str(exc)})
                continue
            action = str(result.get("action"))
            if action in summary:
                summary[action] += 1
            summary["results"].append(result)
    return summary


def replay_pending(config: dict[str, Any], *, runner: Optional[Runner] = None,
                   paths: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """重放待补/未定价登记(开关开后再跑 ⇒ 补上,且只补一次)。"""
    paths = paths or resolve_paths(config)
    pending = PendingStore(Path(paths["pending_db"]))
    summary = {"replayed": 0, "done": 0, "pending": 0, "unpriced": 0,
               "skipped": 0, "error": 0, "results": []}
    try:
        _require_swarm_paths(paths)
    except BackfillError as exc:
        summary["error"] = 1
        summary["skipped_reason"] = str(exc)
        return summary
    for row in pending.open_rows():
        summary["replayed"] += 1
        try:
            result = process_task(config, str(row["task_id"]), runner=runner,
                                  paths=paths, pending=pending)
        except BackfillError as exc:
            summary["error"] += 1
            summary["results"].append({"task_id": row["task_id"], "action": "error",
                                       "reason": str(exc)})
            continue
        action = str(result.get("action"))
        if action in summary:
            summary[action] += 1
        summary["results"].append(result)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python automation/swarm_ledger_backfill.py",
        description="C-4 公司侧账本回填(v2 run 收口 ⇒ 公司账本 ⇒ 蜂群回填;"
                    "幂等;三层门关时响亮失败 + 待补登记)")
    parser.add_argument("--config",
                        default=str(Path(__file__).resolve().parent / "router_config.json"))
    parser.add_argument("--task-id", default="", help="只处理该任务")
    parser.add_argument("--run-id", default="", help="只处理该 run 的 accepted 终态任务")
    parser.add_argument("--process", action="store_true", help="扫描已收口 run(默认动作)")
    parser.add_argument("--replay", action="store_true", help="重放待补登记")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise BackfillError("config 根必须是对象")
        if args.task_id:
            out = {"results": [process_task(config, args.task_id)], "done": 0,
                   "pending": 0, "unpriced": 0, "error": 0}
            action = out["results"][0].get("action")
            if action in out:
                out[action] = 1
        elif args.replay:
            out = replay_pending(config)
        else:
            out = process_finalized_runs(config, run_id=args.run_id or None)
    except (BackfillError, OSError, ValueError, KeyError) as exc:
        print(f"swarm ledger backfill 失败: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"回填: done={out.get('done', 0)} pending={out.get('pending', 0)} "
              f"unpriced={out.get('unpriced', 0)} skipped={out.get('skipped', 0)}")
    # 响亮:任一条未完成/未定价 ⇒ 非零(绝不 rc=0 静默)。
    return 2 if int(out.get("unpriced", 0)) else (1 if int(out.get("pending", 0)) else 0)


if __name__ == "__main__":
    raise SystemExit(main())
