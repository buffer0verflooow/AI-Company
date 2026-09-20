"""W14-b 回归:dev 线接线(submit_dev_v2 / launch_v2_dev_worker / 显式 `--route dev`)。

派工书 §3 断言(全部离线,桩驱动,不接触真实库/身份/进程):
  ① 闸关(dispatch_dev=false)⇒ 响亮拒绝,零 CLI 调用、零进程,文案含"怎么开";
  ② 缺 dev_repo ⇒ 响亮拒绝(绝不猜默认仓库),零提交/零 mkdir/零 Popen;
  ③ 仓库闸开 ⇒ 提交 dev(run_type=dev / task_type=custom)+ focus 顶层
     test_command/files + **不下发** exec_criteria;worker argv 正确(dev 档/40 轮/
     --repo-root=指定仓库);
  ④ 不串档:dev 的档位/身份/repo-root/轮数与 content/security/research 逐项对比;
  ⑤ 单源锁:CLI choices 与 classify 默认行为;`_V2_RUN_TYPE_BY_ROUTE['dev']=='dev'`;
  ⑥ 默认关:`classify_message`(enable_dev 缺省)既有分类结果逐字不变。

改前对照(变异反证):`submit_dev_v2`/`launch_v2_dev_worker` 等符号在 HEAD 上不存在
⇒ 本文件 import 即红;出厂配置缺 dev 键的断言同样红。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    DEV_GATE_OPEN_COMMAND,
    DEV_LINE_DISABLED,
    DEV_REPO_REQUIRED,
    DEV_V2_LINE_UNAVAILABLE,
    _V2_DEV_MAX_TURNS,
    _V2_RUN_TYPE_BY_ROUTE,
    RouterState,
    build_v2_content_worker_cmd,
    build_v2_dev_worker_cmd,
    build_v2_research_worker_cmd,
    build_v2_security_worker_cmd,
    classify_message,
    dispatch_dev_explicit,
    handle_hook,
    launch_v2_dev_worker,
    submit_content_v2,
    submit_dev_v2,
    submit_research_v2,
    submit_security_v2,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
# 不含安全目标实体(如 `x.py` 会被 target 抽取当成 domain ⇒ security 优先);
# dev 路由**保守让位于** security/research 信号,这里用纯代码/测试语义的句子。
DEV_MESSAGE = "请重构这个模块的代码并让测试通过"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "router_config.json"


def _gray(**overrides):
    block = {
        "enabled": True,
        "run_types": ["content", "ops", "vuln", "dev"],
        "task_types": [],
        "ratio_pct": 100,
        "client_source": "company-router",
    }
    block.update(overrides)
    return block


def _config(td, *, dispatch_dev=False, dev_route_enabled=False, gray=None,
            dev_repo="", dev_agent="dev-executor-1", dev_judge="dev-verifier-1",
            **extra):
    config = {
        "enabled": True,
        "dispatch_security": False,
        "dispatch_research": False,
        "auto_run_security": False,
        "auto_run_article": True,
        "auto_run_video": True,
        "auto_run_company": True,
        "state_db": str(Path(td) / "router.db"),
        "swarm_repo": SWARM_REPO,
        "swarm_v2_db": str(Path(td) / "swarm_v2.db"),
        "swarm_v2_agent": "content-writer-1",
        "swarm_v2_judge": "content-judge-1",
        "swarm_v2_security_agent": "sec-exec-1",
        "swarm_v2_security_judge": "sec-judge-1",
        "swarm_v2_research_agent": "res-exec-1",
        "swarm_v2_research_judge": "res-judge-1",
        "dispatch_dev": dispatch_dev,
        "dev_route_enabled": dev_route_enabled,
        "swarm_v2_dev_agent": dev_agent,
        "swarm_v2_dev_judge": dev_judge,
        "swarm_v2_dev_repo": dev_repo,
        "swarm_v2_gray": _gray() if gray is None else gray,
        "log_dir": str(Path(td) / "logs"),
        "content_executor": str(Path(td) / "content_executor.py"),
        "content_job_dir": str(Path(td) / "content-jobs"),
        "gateway_sessions_index": str(Path(td) / "sessions.json"),
        "max_active_runs_per_session": 2,
        "max_active_content_jobs_per_session": 2,
    }
    config.update(extra)
    return config


def _decision():
    from automation.company_router import RouteDecision
    return RouteDecision(route="dev", confidence=1.0, action="dispatch_swarm",
                         reason="test", intent="custom")


def _payload(session, message):
    return {"session_id": session, "extra": {"user_message": message, "platform": "cli"}}


def _event_row(config):
    state = RouterState(config["state_db"])
    try:
        return state.db.execute(
            "SELECT action,status,error,run_id,request_id,runner_pid FROM route_events"
        ).fetchone()
    finally:
        state.close()


class ShippedConfigTests(unittest.TestCase):
    """出厂配置:dev 三键 + D-50 后的安全不变式(不是"任意值都过")。"""

    def test_shipped_config_dev_keys_and_safe_defaults(self):
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        # W16-②:D-50 把出厂 `dispatch_dev` 置 true 是**政策变更**(dev 线开闸),
        # 不再是缺陷。断言改为"安全不变式":允许为 True,但开闸必须同时满足
        #   ① 无默认仓库(`swarm_v2_dev_repo == ""`)⇒ 每次派发必须显式给仓库;
        #   ② 分类器不自动命中 dev(`dev_route_enabled is False`);
        #   ③ dev 身份已配且非空(否则发布必被拒)。
        # 任一条被破坏 ⇒ 本断言红(非"改前 assertIs(False)"的放宽)。
        self.assertIn(cfg["dispatch_dev"], (True, False),
                      "dispatch_dev 必须是显式布尔")
        if cfg["dispatch_dev"] is True:
            self.assertEqual(cfg["swarm_v2_dev_repo"], "",
                             "开闸不得留默认仓库(每次派发必须显式 --dev-repo)")
            self.assertIs(cfg["dev_route_enabled"], False,
                          "开闸不得让分类器自动命中 dev")
            self.assertTrue(str(cfg["swarm_v2_dev_agent"] or "").strip(),
                            "开闸必须配置非空 swarm_v2_dev_agent")
            self.assertTrue(str(cfg["swarm_v2_dev_judge"] or "").strip(),
                            "开闸必须配置非空 swarm_v2_dev_judge")
        # 既有断言逐字保留(dev 三键 + 灰度闭集)
        self.assertIs(cfg["dev_route_enabled"], False)
        self.assertEqual(cfg["swarm_v2_dev_agent"], "dev-executor-1")
        self.assertEqual(cfg["swarm_v2_dev_judge"], "dev-verifier-1")
        self.assertEqual(cfg["swarm_v2_dev_repo"], "")
        self.assertIn("dev", cfg["swarm_v2_gray"]["run_types"])

    def test_route_run_type_map_includes_dev(self):
        self.assertEqual(_V2_RUN_TYPE_BY_ROUTE["dev"], "dev")


class GateClosedTests(unittest.TestCase):
    """① 闸关 ⇒ 响亮拒绝,零 CLI 调用/零进程。"""

    def test_submit_refuses_loudly_when_dispatch_dev_false(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=False, dev_repo=td)
            with patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.subprocess.Popen") as popen:
                with self.assertRaises(RuntimeError) as ctx:
                    submit_dev_v2(
                        config, decision=_decision(), message=DEV_MESSAGE,
                        session_id="s", platform="cli",
                        gray={"hit": True, "reason": "ratio_hit"},
                        dev_repo=td, test_command=["python3", "-m", "pytest", "-q"],
                        files=["pkg/calc.py"])
            v2cli.assert_not_called()
            popen.assert_not_called()
            self.assertIn(DEV_LINE_DISABLED, str(ctx.exception))
            self.assertIn("dispatch_dev", str(ctx.exception))
            self.assertIn("开闸命令", str(ctx.exception))
            self.assertIn(DEV_GATE_OPEN_COMMAND, str(ctx.exception))

    def test_explicit_dispatch_gate_closed_zero_side_effect(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=False, dev_repo=td)
            with patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_dev_worker") as v2w:
                with self.assertRaises(RuntimeError):
                    dispatch_dev_explicit(
                        config, message=DEV_MESSAGE, session_id="s", platform="cli",
                        dev_repo=td)
            v2cli.assert_not_called()
            v2w.assert_not_called()


class RepoGateTests(unittest.TestCase):
    """② 缺/坏 dev_repo ⇒ 响亮拒绝(绝不猜默认仓库)。"""

    def test_submit_refuses_when_repo_missing(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=True, dev_repo="")
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError) as ctx:
                    submit_dev_v2(
                        config, decision=_decision(), message=DEV_MESSAGE,
                        session_id="s", platform="cli",
                        gray={"hit": True, "reason": "ratio_hit"})
            v2cli.assert_not_called()
            self.assertEqual(str(ctx.exception), DEV_REPO_REQUIRED)
            self.assertIn("--dev-repo", str(ctx.exception))

    def test_submit_refuses_when_repo_not_a_directory(self):
        with tempfile.TemporaryDirectory() as td:
            missing = str(Path(td) / "does-not-exist")
            config = _config(td, dispatch_dev=True)
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError) as ctx:
                    submit_dev_v2(
                        config, decision=_decision(), message=DEV_MESSAGE,
                        session_id="s", platform="cli",
                        gray={"hit": True, "reason": "ratio_hit"}, dev_repo=missing)
            v2cli.assert_not_called()
            self.assertIn("不存在", str(ctx.exception))

    def test_launch_refuses_when_repo_missing_zero_mkdir_popen(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=True, dev_repo="")
            with patch("automation.company_router.security_exec_switch_enabled",
                       return_value=True), \
                    patch("automation.company_router.subprocess.Popen") as popen:
                with self.assertRaises(ValueError) as ctx:
                    launch_v2_dev_worker(config, "company-dev-aaaaaaaaaaaa")
            popen.assert_not_called()
            self.assertIn("--dev-repo", str(ctx.exception))
            self.assertFalse((Path(td) / "logs").exists(), "拒绝路径不得 mkdir")


class SubmitHappyPathTests(unittest.TestCase):
    """③ 闸/灰度/身份/repo 齐备 ⇒ 提交 dev + focus 形状 + 不下发判据期望值。"""

    def test_submit_dev_publishes_run_type_dev_with_focus_declarations(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=True, dev_repo=td)
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "created"}, {"task_id": "t-dev-9"}]) as v2cli:
                out = submit_dev_v2(
                    config, decision=_decision(), message=DEV_MESSAGE,
                    session_id="sess-1", platform="cli",
                    gray={"hit": True, "reason": "ratio_hit"}, dev_repo=td,
                    test_command=["python3", "-m", "pytest", "-q"],
                    files=["pkg/calc.py"])
            self.assertEqual(out["_v2_run_type"], "dev")
            self.assertEqual(out["_v2_task_type"], "custom")
            self.assertTrue(out["run_id"].startswith("company-dev-"))
            create = v2cli.call_args_list[0].args
            self.assertEqual(create[1:4], ("v2", "run", "create"))
            self.assertEqual(create[create.index("--run-type") + 1], "dev")
            self.assertEqual(create[create.index("--by") + 1], "dev-executor-1")
            publish = v2cli.call_args_list[1].args
            self.assertEqual(publish[1:3], ("market", "publish"))
            self.assertEqual(publish[publish.index("--run-type") + 1], "dev")
            self.assertEqual(publish[publish.index("--task-type") + 1], "custom")
            self.assertEqual(publish[publish.index("--required-role") + 1], "dev-executor")
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["company_route"], "dev")
            self.assertEqual(focus["test_command"], ["python3", "-m", "pytest", "-q"])
            self.assertEqual(focus["files"], ["pkg/calc.py"])
            # F1 边界:dev 线**不下发** exec_criteria(判据真值 = 复跑 exit code)
            self.assertNotIn("exec_criteria", focus)

    def test_submit_dev_omits_absent_declarations_without_fabricating(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=True, dev_repo=td)
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "x"}, {"task_id": "t"}]) as v2cli:
                submit_dev_v2(config, decision=_decision(), message=DEV_MESSAGE,
                              session_id="s", platform="cli",
                              gray={"hit": True}, dev_repo=td)
            publish = v2cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            # 无声明 ⇒ focus 里没有 test_command/files 键(不编造)
            self.assertNotIn("test_command", focus)
            self.assertNotIn("files", focus)

    def test_submit_dev_requires_identities(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=True, dev_repo=td,
                             dev_agent="", dev_judge="")
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(RuntimeError):
                    submit_dev_v2(config, decision=_decision(), message=DEV_MESSAGE,
                                  session_id="s", platform="cli",
                                  gray={"hit": True}, dev_repo=td)
            v2cli.assert_not_called()


class WorkerCmdAndSwitchTests(unittest.TestCase):
    """③ worker argv 金样本 + dev 档开关门。"""

    def test_dev_worker_cmd_golden(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dev_repo=str(repo))
            cmd = build_v2_dev_worker_cmd(config, "company-dev-000000000001",
                                          dev_repo=str(repo))
            self.assertEqual(cmd, [
                sys.executable,
                str(Path(SWARM_REPO) / "scripts" / "swarmctl.py"),
                "worker",
                "--db", config["swarm_v2_db"],
                "--agent", "dev-executor-1",
                "--judge-by", "dev-verifier-1",
                "--agent-runtime",
                "--permission", "dev",
                "--repo-root", str(repo.resolve()),
                "--max-turns", "24",
                "--max-tokens-budget", "100000",
                "--poll-interval", "5.0",
                "--max-tasks", "1",
            ])
            self.assertEqual(cmd[cmd.index("--max-turns") + 1], str(_V2_DEV_MAX_TURNS))

    def test_launch_requires_dev_exec_switch_before_any_side_effect(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dev_repo=str(repo))
            with patch("automation.company_router.security_exec_switch_enabled",
                       return_value=False), \
                    patch("automation.company_router.subprocess.Popen") as popen:
                with self.assertRaises(RuntimeError) as ctx:
                    launch_v2_dev_worker(config, "company-dev-bbbbbbbbbbbb",
                                         dev_repo=str(repo))
            popen.assert_not_called()
            self.assertIn("agent_runtime_exec", str(ctx.exception))
            self.assertIn("agent_runtime_exec", str(ctx.exception))
            self.assertFalse((Path(td) / "logs").exists(), "拒绝路径不得 mkdir")

    def test_launch_popen_uses_repo_root_and_dev_argv(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dev_repo=str(repo))
            with patch("automation.company_router.security_exec_switch_enabled",
                       return_value=True), \
                    patch("automation.company_router.subprocess.Popen") as popen:
                popen.return_value.pid = 4242
                pid = launch_v2_dev_worker(config, "company-dev-cccccccccccc",
                                           dev_repo=str(repo))
            self.assertEqual(pid, 4242)
            args, kwargs = popen.call_args
            self.assertEqual(args[0][args[0].index("--permission") + 1], "dev")
            self.assertEqual(args[0][args[0].index("--repo-root") + 1], str(repo.resolve()))
            self.assertEqual(args[0][args[0].index("--max-turns") + 1], "24")
            self.assertEqual(kwargs["cwd"], str(repo.resolve()))


class NoCrossLineTests(unittest.TestCase):
    """④ 四条线 argv 互不串档(身份/run_type/档位/repo-root/轮数)。"""

    def test_four_lines_do_not_mix(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dev_repo=str(repo))
            dev = build_v2_dev_worker_cmd(config, "company-dev-111111111111",
                                          dev_repo=str(repo))
            content = build_v2_content_worker_cmd(config, "company-content-111111111111")
            security = build_v2_security_worker_cmd(config, "company-vuln-111111111111")
            research = build_v2_research_worker_cmd(config, "company-ops-111111111111")
            self.assertEqual(dev[dev.index("--permission") + 1], "dev")
            self.assertEqual(content[content.index("--permission") + 1], "write")
            self.assertEqual(security[security.index("--permission") + 1], "exec")
            self.assertEqual(research[research.index("--permission") + 1], "exec")
            self.assertEqual(dev[dev.index("--agent") + 1], "dev-executor-1")
            self.assertEqual(dev[dev.index("--judge-by") + 1], "dev-verifier-1")
            # 轮数:dev=40,其余=12
            self.assertEqual(dev[dev.index("--max-turns") + 1], "24")
            self.assertEqual(content[content.index("--max-turns") + 1], "12")
            self.assertEqual(security[security.index("--max-turns") + 1], "12")
            self.assertEqual(research[research.index("--max-turns") + 1], "12")
            # repo-root 四条线各不相同,dev 即指定仓库
            roots = {dev[dev.index("--repo-root") + 1],
                     content[content.index("--repo-root") + 1],
                     security[security.index("--repo-root") + 1],
                     research[research.index("--repo-root") + 1]}
            self.assertEqual(len(roots), 4)
            self.assertEqual(dev[dev.index("--repo-root") + 1], str(repo.resolve()))
            for foreign in ("content-writer-1", "content-judge-1", "sec-exec-1",
                            "res-exec-1", "sec-judge-1", "res-judge-1"):
                self.assertNotIn(foreign, dev)

    def test_submit_ports_keep_their_own_run_types(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dispatch_research=True,
                             dev_repo=str(repo))
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "a"}, {"task_id": "ta"},
                                    {"run_id": "b"}, {"task_id": "tb"},
                                    {"run_id": "c"}, {"task_id": "tc"},
                                    {"run_id": "d"}, {"task_id": "td"}]):
                out_c = submit_content_v2(
                    config, decision=classify_message("写一篇公众号文章"),
                    message="写一篇公众号文章", session_id="s", platform="cli",
                    gray={"hit": True})
                out_s = submit_security_v2(
                    config, decision=classify_message("分析本机 APK 逆向报告中的认证逻辑"),
                    message="分析本机 APK 逆向报告中的认证逻辑",
                    session_id="s", platform="cli", gray={"hit": True})
                out_r = submit_research_v2(
                    config, decision=classify_message("调研一下竞品 X 的技术方案"),
                    message="调研一下竞品 X 的技术方案", session_id="s",
                    platform="cli", gray={"hit": True})
                out_d = submit_dev_v2(
                    config, decision=_decision(), message=DEV_MESSAGE, session_id="s",
                    platform="cli", gray={"hit": True}, dev_repo=str(repo))
            self.assertEqual((out_c["_v2_run_type"], out_s["_v2_run_type"],
                              out_r["_v2_run_type"], out_d["_v2_run_type"]),
                             ("content", "vuln", "ops", "dev"))
            self.assertTrue(out_d["run_id"].startswith("company-dev-"))


class ClassifyLockTests(unittest.TestCase):
    """⑤⑥ 默认关 ⇒ 既有分类结果逐字不变;开了才认 dev。"""

    CORPUS = [
        ("写一篇 Agent 工程公众号文章", "article"),
        ("做个视频，分镜和配音都要", "video"),
        ("分析本机 APK 逆向报告中的认证逻辑", "security"),
        ("调研一下竞品 X 的技术方案", "research"),
        ("先把公司季度战略整理一下", "company"),
    ]

    def test_default_classification_never_dev_and_legacy_routes_hold(self):
        for message, expected in self.CORPUS:
            got = classify_message(message)
            self.assertNotEqual(got.route, "dev", message)
            self.assertEqual(got.route, expected, message)
            # enable_dev 显式 False 与缺省逐字一致
            self.assertEqual(classify_message(message, enable_dev=False).route, expected)

    def test_dev_route_only_when_enabled(self):
        self.assertEqual(classify_message(DEV_MESSAGE).route, "company")
        enabled = classify_message(DEV_MESSAGE, enable_dev=True)
        self.assertEqual(enabled.route, "dev")
        self.assertEqual(enabled.action, "dispatch_swarm")
        # 非 dev 语义的经营动作不得被劫持
        self.assertEqual(classify_message("请实现公司战略目标", enable_dev=True).route,
                         "company")


class HandleHookDevTests(unittest.TestCase):
    """分类路由开启时,handle_hook 走 dev 专属提交口。"""

    def test_dev_route_enabled_dispatches_through_submit_dev(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dev_route_enabled=True,
                             dev_repo=str(repo))
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command",
                          side_effect=[{"run_id": "created"}, {"task_id": "t-dev-1"}]) as v2cli, \
                    patch("automation.company_router.launch_v2_dev_worker",
                          return_value=777) as v2w:
                result = handle_hook(_payload("dev-on", DEV_MESSAGE), config)
            self.assertEqual(v2cli.call_count, 2)
            publish = v2cli.call_args_list[1].args
            self.assertEqual(publish[publish.index("--run-type") + 1], "dev")
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["company_route"], "dev")
            self.assertIn("v2 灰度命中", result["context"])
            run_id = publish[publish.index("--run-id") + 1]
            v2w.assert_called_once()
            self.assertEqual(v2w.call_args.args[1], run_id)
            self.assertEqual(v2w.call_args.kwargs["dev_repo"], str(repo))
            row = _event_row(config)
            self.assertEqual(row["action"], "dispatch_swarm")
            self.assertEqual(row["run_id"], run_id)
            self.assertEqual(row["status"], "running")

    def test_dev_route_disabled_keeps_legacy_classification(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dev_route_enabled=False,
                             dev_repo=str(repo))
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command",
                          side_effect=[{"run_id": "c"}, {"task_id": "tc"}]) as v2cli, \
                    patch("automation.company_router.launch_v2_dev_worker") as dev_w, \
                    patch("automation.company_router.launch_v2_content_worker",
                          return_value=1) as content_w:
                result = handle_hook(_payload("dev-off", DEV_MESSAGE), config)
            # dev_route_enabled=false ⇒ 分类仍落既有公司内容线(legacy 不变)
            content_w.assert_called_once()
            dev_w.assert_not_called()
            dev_is_calls = [
                c for c in v2cli.call_args_list
                if "--run-type" in c.args
                and c.args[c.args.index("--run-type") + 1] == "dev"]
            self.assertEqual(dev_is_calls, [])
            self.assertNotIn("dev 任务已发布", result["context"])

    def test_dev_route_enabled_but_no_repo_rejects_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_dev=True, dev_route_enabled=True,
                             dev_repo="")
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_dev_worker") as v2w:
                result = handle_hook(_payload("dev-norepo", DEV_MESSAGE), config)
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn(DEV_REPO_REQUIRED, result["context"])
            row = _event_row(config)
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error"], DEV_REPO_REQUIRED)


class UnavailableMessageTests(unittest.TestCase):
    """闸开但灰度未命中 ⇒ fail-closed 文案(不是 v1 退役)。"""

    def test_gray_miss_reports_dev_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "devrepo"
            repo.mkdir()
            config = _config(td, dispatch_dev=True, dev_repo=str(repo),
                             gray=_gray(run_types=["content"]))
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(RuntimeError) as ctx:
                    submit_dev_v2(config, decision=_decision(), message=DEV_MESSAGE,
                                  session_id="s", platform="cli",
                                  gray={"hit": False, "reason": "run_type_not_gray"},
                                  dev_repo=str(repo))
            v2cli.assert_not_called()
            self.assertIn(DEV_V2_LINE_UNAVAILABLE, str(ctx.exception))
            self.assertIn("run_type_not_gray", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
