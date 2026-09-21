"""M5 灰度接入 2 回归:内容线 v2 stdin 契约 + v2 灰度派发(默认关)。

覆盖任务书 §3 的七组用例:
  1. 默认关 ⇒ 零行为变更(内容派发仍走原路径,零 v2 CLI 调用)。
  2. 灰度命中 ⇒ 进 v2(run create + market publish + worker 参数正确)。
  3. 成对默认闸 ⇒ enabled / run_types / ratio 任一未满足回原路径(逐条)。
  4. stdin 模式契约 ⇒ 建 job 目录(非法 task_id 拒)+ 复用既有执行路径 +
     stdout 单行 JSON;tokens 读不到 ⇒ 无 token_cost(非 0);失败 ⇒ 非零退出。
  5. 既有 --job-dir 模式不回归 ⇒ 退出码恒 0、产物文件与既有语义一致。
  6. 容错回退 ⇒ v2 CLI 非零 / 配置缺失 / worker=judge ⇒ 回原路径 + 记录原因。
  7. 并发闸不绕过 ⇒ v2 路径同样受 max_active_content_jobs_per_session 约束。

全部离线:CLI / worker / Hermes 子进程都被 monkeypatch,不接触任何真实 swarm
库、run 记录或 job 目录。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automation import content_hermes_executor as che
from automation.company_router import (
    RouterState,
    build_v2_content_worker_cmd,
    classify_message,
    handle_hook,
    submit_content_v2,
    v2_gray_config,
    v2_gray_decision,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
ARTICLE_MESSAGE = "写一篇 Agent 工程公众号文章"


def _gray(**overrides):
    block = {
        "enabled": False,
        "run_types": [],
        "task_types": [],
        "ratio_pct": 0,
        "client_source": "",
    }
    block.update(overrides)
    return block


def _config(td, *, gray=None, agent="", judge="", v2_db=None, **extra):
    config = {
        "enabled": True,
        "dispatch_security": False,
        "auto_run_security": False,
        "auto_run_article": True,
        "auto_run_video": True,
        "auto_run_company": True,
        "state_db": str(Path(td) / "router.db"),
        "swarm_repo": SWARM_REPO,
        "swarm_db": str(Path(td) / "swarm.db"),
        "swarm_v2_db": str(Path(td) / "swarm_v2.db") if v2_db is None else v2_db,
        "swarm_v2_agent": agent,
        "swarm_v2_judge": judge,
        "swarm_v2_gray": _gray() if gray is None else gray,
        "log_dir": str(Path(td) / "logs"),
        "executor": "/bin/exec.py",
        "content_executor": str(Path(td) / "content_executor.py"),
        "content_job_dir": str(Path(td) / "content-jobs"),
        "gateway_sessions_index": str(Path(td) / "sessions.json"),
        "max_active_runs_per_session": 2,
        "max_active_content_jobs_per_session": 2,
    }
    config.update(extra)
    return config


def _payload(session="article-session", message=ARTICLE_MESSAGE):
    return {
        "session_id": session,
        "extra": {"user_message": message, "platform": "cli"},
    }


def _hit_gray(**overrides):
    block = {
        "enabled": True,
        "run_types": ["content"],
        "task_types": [],
        "ratio_pct": 100,
        "client_source": "company-router",
    }
    block.update(overrides)
    return _gray(**block)


class DefaultOffTests(unittest.TestCase):
    def test_disabled_uses_original_path_and_never_calls_v2(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_gray(enabled=False))
            with patch("automation.company_router.launch_content_job",
                       return_value=4321) as v1, \
                    patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_content_worker") as v2w:
                result = handle_hook(_payload(), config)

            self.assertIn("文章产线", result["context"])
            v1.assert_called_once()
            v2cli.assert_not_called()
            v2w.assert_not_called()
            state = RouterState(config["state_db"])
            row = state.db.execute(
                "SELECT action,run_id,runner_pid,status FROM route_events").fetchone()
            self.assertEqual(row["action"], "dispatch_article")
            self.assertTrue(row["run_id"])
            self.assertEqual(row["runner_pid"], 4321)
            self.assertEqual(row["status"], "running")
            state.close()

    def test_absent_gray_block_reads_as_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            config.pop("swarm_v2_gray")
            self.assertFalse(v2_gray_config(config)["enabled"])
            with patch("automation.company_router.launch_content_job",
                       return_value=1) as v1, \
                    patch("automation.company_router.v2_swarm_command") as v2cli:
                handle_hook(_payload(), config)
            v1.assert_called_once()
            v2cli.assert_not_called()

    def test_repository_config_gray_integrity(self):
        """仓库配置件不变量:开关形态合法;启用态**不得半配置**。

        2026-09-16 起仓库配置为已启用(content 10%,用户授权):原"默认必须关"的断言
        改为「关 ⇒ 零 v2 调用(见 test_absent_gray_block_reads_as_disabled)」+
        「开 ⇒ run_types/db/agent/judge/client_source 齐备 ∧ judge≠agent」的组合不变量,
        防止启用后漏配身份/来源导致「命中却总回退」或自判。
        """
        config = json.loads(
            (Path(__file__).resolve().parent.parent / "router_config.json")
            .read_text(encoding="utf-8"))
        gray = v2_gray_config(config)
        self.assertIsInstance(gray["enabled"], bool)
        if gray["enabled"]:
            self.assertTrue(gray["run_types"], "启用灰度必须给出 run_types")
            self.assertTrue(gray["db"], "启用灰度必须配置 v2 库")
            self.assertTrue(gray["agent"], "启用灰度必须配置执行身份")
            self.assertTrue(gray["judge"], "启用灰度必须配置判定身份")
            self.assertNotEqual(gray["agent"], gray["judge"], "禁自判(F5.1)")
            self.assertTrue(gray["client_source"], "启用灰度必须配置发布者来源(脱敏标签前置)")


class GrayHitTests(unittest.TestCase):
    def test_hit_runs_run_create_then_market_publish(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(),
                             agent="content-worker", judge="content-judge")
            create_result = {"run_id": "created", "status": "created"}
            publish_result = {"task_id": "t-content-1"}
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command",
                          side_effect=[create_result, publish_result]) as v2cli, \
                    patch("automation.company_router.launch_v2_content_worker",
                          return_value=777) as v2w, \
                    patch("automation.company_router.launch_content_job") as v1:
                result = handle_hook(_payload(), config)

            v1.assert_not_called()
            v2w.assert_called_once()
            self.assertEqual(v2cli.call_count, 2)

            create = v2cli.call_args_list[0].args
            self.assertEqual(create[1:4], ("v2", "run", "create"))
            self.assertEqual(create[create.index("--run-type") + 1], "content")
            self.assertEqual(create[create.index("--intent") + 1], "custom")
            self.assertEqual(create[create.index("--by") + 1], "content-worker")
            self.assertNotIn("--role-counts", create)
            run_id = create[create.index("--run-id") + 1]
            self.assertTrue(run_id.startswith("company-content-"))

            publish = v2cli.call_args_list[1].args
            self.assertEqual(publish[1:3], ("market", "publish"))
            self.assertEqual(publish[publish.index("--run-type") + 1], "content")
            self.assertEqual(publish[publish.index("--task-type") + 1], "custom")
            self.assertEqual(publish[publish.index("--publisher") + 1], "client")
            self.assertEqual(publish[publish.index("--client-source") + 1],
                             "company-router")
            # content subtype travels via focus_params, not the task_type set
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["content_route"], "article")
            self.assertEqual(focus["company_task"], ARTICLE_MESSAGE)
            # task id pinned to run id => executor job dir == content_job_path(run_id)
            self.assertEqual(publish[publish.index("--task-id") + 1], run_id)
            self.assertIn("v2 灰度命中", result["context"])
            v2w.assert_called_once_with(config, run_id)

            state = RouterState(config["state_db"])
            row = state.db.execute(
                "SELECT action,run_id,request_id,runner_pid,status FROM route_events"
            ).fetchone()
            self.assertEqual(row["action"], "dispatch_article")
            self.assertEqual(row["run_id"], run_id)
            self.assertEqual(row["request_id"], "t-content-1")
            self.assertEqual(row["runner_pid"], 777)
            self.assertEqual(row["status"], "running")
            state.close()

    def test_worker_cmd_uses_builtin_runtime_and_distinct_identity(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(),
                             agent="content-worker", judge="content-judge")
            run_id = "company-content-abcdef123456"
            cmd = build_v2_content_worker_cmd(config, run_id)
            self.assertEqual(cmd[2], "worker")
            self.assertEqual(cmd[cmd.index("--db") + 1], config["swarm_v2_db"])
            self.assertEqual(cmd[cmd.index("--agent") + 1], "content-worker")
            self.assertEqual(cmd[cmd.index("--judge-by") + 1], "content-judge")
            # D-22 执行面自给:蜂群内建 agent_runtime(write 档),不外包外部 agent CLI
            self.assertIn("--agent-runtime", cmd)
            self.assertEqual(cmd[cmd.index("--permission") + 1], "write")
            self.assertNotIn("--executor-command", cmd)
            self.assertNotIn(config["content_executor"], cmd)
            # path jail 根 = 本 run 产物目录(默认根是蜂群仓库,不可用于公司任务)
            self.assertEqual(cmd[cmd.index("--repo-root") + 1],
                             str(Path(config["content_job_dir"]) / run_id))
            self.assertNotIn("--role-counts", cmd)

    def test_published_focus_carries_runtime_brief_and_declared_deliverables(self):
        """focus_params 必须带内建运行时的自包含任务书 + 声明式产物清单。"""
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(),
                             agent="content-worker", judge="content-judge")
            decision = classify_message(ARTICLE_MESSAGE)
            gray = v2_gray_decision(config, decision, ARTICLE_MESSAGE)
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
                submit_content_v2(config, decision=decision, message=ARTICLE_MESSAGE,
                                  session_id="sess-1", platform="cli", gray=gray)
            publish = v2cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["content_route"], "article")
            self.assertEqual(focus["company_task"], ARTICLE_MESSAGE)
            self.assertEqual(focus["company_session_id"], "sess-1")
            self.assertEqual(focus["company_platform"], "cli")
            # 规范正文随任务下发(path jail 根 = 产物目录,运行时读不到公司仓库)
            brief = focus["runtime_brief"]
            self.assertIn(ARTICLE_MESSAGE, brief)
            self.assertIn("draft.md", brief)
            self.assertIn("article-quality-constraints.md", brief)
            # CV2(2026-09-21):质量门规范**全文随任务落盘**(内联节选只有 4000 字)
            self.assertIn("content-quality-gates.md", brief)
            self.assertIn("_spec/", brief)
            self.assertIn("fs.append", brief)          # CV-3:长文分段写
            # 声明式产物清单 ⇒ 判定器按声明核验;必须是 jail 内相对路径
            files = focus["content_verify"]["files"]
            # CV2:任务含「公众号」⇒ 追加排版/预览两件(与 hermes 执行器步骤 5/6 同源;
            # 改前 v2 侧从不声明 ⇒ 排版环节在蜂群线上等于没有)
            self.assertEqual(files, ["draft.md", "draft-humanized.md", "qa-report.md",
                                     "draft-formatted.md", "wechat-preview.html"])
            for name in files:
                self.assertFalse(os.path.isabs(name))


class PairedGateTests(unittest.TestCase):
    def _submit(self, config):
        with patch("automation.company_router.launch_content_job",
                   return_value=4321) as v1, \
                patch("automation.company_router.v2_swarm_command") as v2cli, \
                patch("automation.company_router._load_v2_company_router",
                      return_value=None):
            result = handle_hook(_payload(session="gate-session"), config)
        return result, v1, v2cli

    def _assert_fallback(self, config, reason):
        result, v1, v2cli = self._submit(config)
        v1.assert_called_once()
        v2cli.assert_not_called()
        state = RouterState(config["state_db"])
        row = state.db.execute("SELECT status,error,run_id FROM route_events").fetchone()
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["run_id"])
        self.assertIn(reason, row["error"])
        self.assertIn("回退", result["context"])
        state.close()

    def test_enabled_but_empty_run_types_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(run_types=[]),
                        agent="w", judge="j"),
                "v2_gray_run_types_empty")

    def test_enabled_but_ratio_zero_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(ratio_pct=0),
                        agent="w", judge="j"),
                "v2_gray_ratio_zero")

    def test_run_type_not_in_gray_set_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(run_types=["vuln"]),
                        agent="w", judge="j"),
                "run_type_not_gray")

    def test_missing_v2_db_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(), v2_db="", agent="w", judge="j"),
                "v2_db_not_configured")

    def test_missing_agent_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(), agent="", judge="j"),
                "v2_agent_not_configured")

    def test_missing_judge_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(), agent="w", judge=""),
                "v2_judge_not_configured")

    def test_missing_client_source_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(client_source=""), agent="w", judge="j"),
                "v2_client_source_not_configured")

    def test_worker_equals_judge_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self._assert_fallback(
                _config(td, gray=_hit_gray(), agent="same", judge="same"),
                "v2_self_judge_forbidden")


class SkillReviewCooldownTests(unittest.TestCase):
    def test_cooldown_gate_still_short_circuits_before_the_v2_branch(self):
        """The skill-review cooldown runs upstream of dispatch, so it also
        constrains the v2 gray path (no bypass)."""
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), agent="w", judge="j")
            seed_message = "update the skill library: 整理公司流程"
            session = "cooldown-session"
            state = RouterState(config["state_db"])
            seed_decision = classify_message(seed_message)
            self.assertEqual(seed_decision.action, "dispatch_company")
            event_id = state.insert(session, "cli", "seed-skill-hash",
                                    seed_message, seed_decision)
            state.update(event_id, run_id="seed-skill-run", status="completed")
            state.close()

            with patch("automation.company_router.launch_content_job") as v1, \
                    patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_content_worker") as v2w:
                result = handle_hook(
                    _payload(session=session, message=seed_message), config)
            v1.assert_not_called()
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn("seed-skill-run", result["context"])


class FallbackTests(unittest.TestCase):
    def _fallback(self, config, side_effect, *, expected_cli_calls=1):
        with patch("automation.company_router._load_v2_company_router",
                   return_value=None), \
                patch("automation.company_router.v2_swarm_command",
                      side_effect=side_effect) as v2cli, \
                patch("automation.company_router.launch_content_job",
                      return_value=4321) as v1, \
                patch("automation.company_router.launch_v2_content_worker") as v2w:
            result = handle_hook(_payload(session="fb-session"), config)
        self.assertEqual(v2cli.call_count, expected_cli_calls)
        v1.assert_called_once()
        v2w.assert_not_called()
        return result

    def test_v2_cli_nonzero_falls_back_and_records_reason(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), agent="w", judge="j")
            result = self._fallback(config, RuntimeError("v2 cli exploded"))
            self.assertIn("v2 内容灰度回退", result["context"])
            state = RouterState(config["state_db"])
            row = state.db.execute("SELECT status,error,run_id FROM route_events").fetchone()
            self.assertEqual(row["status"], "running")
            self.assertTrue(row["run_id"])
            self.assertIn("v2 content submit failed", row["error"])
            self.assertIn("v2 cli exploded", row["error"])
            state.close()

    def test_v2_publish_failure_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), agent="w", judge="j")
            result = self._fallback(
                config,
                side_effect=[{"run_id": "x"}, RuntimeError("publish rejected")],
                expected_cli_calls=2)
            self.assertIn("publish rejected", result["context"])
            state = RouterState(config["state_db"])
            row = state.db.execute("SELECT status,error FROM route_events").fetchone()
            self.assertEqual(row["status"], "running")
            self.assertIn("publish rejected", row["error"])
            state.close()


class ConcurrencyGateTests(unittest.TestCase):
    def test_v2_path_obeys_active_content_job_cap(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), agent="w", judge="j")
            decision = classify_message(ARTICLE_MESSAGE)
            state = RouterState(config["state_db"])
            for index in range(2):
                event_id = state.insert(
                    "cap-session", "cli", f"seed-hash-{index}",
                    f"seed message {index}", decision)
                state.update(event_id, run_id=f"seed-run-{index}", status="running")
            state.close()
            with patch("automation.company_router.launch_content_job") as v1, \
                    patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_content_worker") as v2w:
                result = handle_hook(_payload(session="cap-session"), config)
            v1.assert_not_called()
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn("并发上限", result["context"])
            state = RouterState(config["state_db"])
            row = state.db.execute(
                "SELECT status FROM route_events WHERE session_id='cap-session' "
                "AND run_id=''").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "deferred")
            state.close()


# ---------------------------------------------------------------------------
# content_hermes_executor --stdin-json contract mode
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, returncode=0, stdout="worker stdout", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _task_payload(task_id="t-content-1", route="article", message=ARTICLE_MESSAGE,
                  run_id="company-content-1"):
    focus = {
        "content_route": route,
        "task_intent": "custom",
        "company_task": message,
        "company_session_id": "sess-1",
        "company_platform": "cli",
    }
    return {
        "task": {
            "task_id": task_id,
            "run_id": run_id,
            "run_type": "content",
            "task_type": "custom",
            "focus_params": focus,
        },
        "context": json.dumps(focus),
    }


class StdinContractTests(unittest.TestCase):
    def _run_stdin(self, payload, *, job_root, files=None, returncode=0,
                   usage=None):
        files = files or {
            "draft.md": "# draft",
            "draft-humanized.md": "# humanized",
            "qa-report.md": "QA",
        }
        task_id = str((payload.get("task") or {}).get("task_id") or "")
        job_dir = Path(job_root) / task_id

        def fake_run(command, **kwargs):  # noqa: ARG001 -- executor subprocess
            for name, text in files.items():
                (job_dir / name).write_text(text, encoding="utf-8")
            return _FakeProc(returncode=returncode,
                             stderr="worker failed" if returncode else "")

        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"COMPANY_CONTENT_JOB_DIR": str(job_root)}), \
                patch.object(sys, "argv",
                             ["content_hermes_executor.py", "--stdin-json"]), \
                patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                patch.object(che.subprocess, "run", side_effect=fake_run), \
                patch.object(che, "worker_usage", return_value=usage or {}), \
                redirect_stdout(out), redirect_stderr(err):
            rc = che.main()
        return rc, out.getvalue(), err.getvalue()

    def test_stdin_creates_job_dir_reuses_path_and_prints_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "content-jobs"
            rc, stdout, _stderr = self._run_stdin(_task_payload(), job_root=root)
            self.assertEqual(rc, 0)
            payload = json.loads(stdout)
            self.assertIn("内容任务 completed", payload["content"])
            self.assertNotIn("token_cost", payload)
            job_dir = root / "t-content-1"
            self.assertTrue((job_dir / "request.json").is_file())
            request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["route"], "article")
            self.assertEqual(request["message"], ARTICLE_MESSAGE)
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")
            self.assertIn(str(job_dir / "draft.md"), status["artifacts"])

    def test_stdin_token_cost_comes_from_measured_usage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "content-jobs"
            usage = {
                "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 30,
                "cache_write_tokens": 0, "reasoning_tokens": 1,
                "estimated_cost_usd": 0.5, "tool_call_count": 3,
            }
            rc, stdout, _stderr = self._run_stdin(
                _task_payload(), job_root=root, usage=usage)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(stdout)["token_cost"], 46)

    def test_stdin_unmeasured_tokens_omit_token_cost(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "content-jobs"
            zero = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                    "cache_write_tokens": 0, "reasoning_tokens": 0}
            rc, stdout, _stderr = self._run_stdin(
                _task_payload(), job_root=root, usage=zero)
            self.assertEqual(rc, 0)
            payload = json.loads(stdout)
            self.assertNotIn("token_cost", payload)
            self.assertIsNone(che.token_cost_from_usage(zero))
            self.assertIsNone(che.token_cost_from_usage({}))

    def test_stdin_rejects_illegal_task_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "content-jobs"
            for task_id in ("../escape", "a/b", ".", "-leading"):
                with self.subTest(task_id=task_id):
                    rc, stdout, stderr = self._run_stdin(
                        _task_payload(task_id=task_id), job_root=root)
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(stdout, "")
                    self.assertIn("rejected", stderr)
            self.assertFalse((Path(td) / "escape").exists())

    def test_stdin_rejects_symlinked_job_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "content-jobs"
            root.mkdir(parents=True)
            outside = Path(td) / "outside"
            outside.mkdir()
            (root / "t-symlink").symlink_to(outside)
            rc, stdout, stderr = self._run_stdin(
                _task_payload(task_id="t-symlink"), job_root=root)
            self.assertNotEqual(rc, 0)
            self.assertEqual(stdout, "")
            self.assertIn("rejected", stderr)
            self.assertTrue("symlink" in stderr or "escapes" in stderr, stderr)

    def test_stdin_missing_task_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            rc, stdout, stderr = self._run_stdin(
                {"task": {"run_id": "x"}, "context": "{}"},
                job_root=Path(td) / "content-jobs")
            self.assertNotEqual(rc, 0)
            self.assertEqual(stdout, "")
            self.assertIn("task_id", stderr)

    def test_stdin_worker_failure_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "content-jobs"
            rc, stdout, stderr = self._run_stdin(
                _task_payload(), job_root=root, returncode=1)
            self.assertNotEqual(rc, 0)
            self.assertEqual(stdout, "")
            self.assertIn("job failed", stderr)
            status = json.loads(
                (root / "t-content-1" / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")

    def test_stdin_missing_artifacts_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "content-jobs"
            rc, stdout, stderr = self._run_stdin(
                _task_payload(), job_root=root, files={"draft.md": "only draft"})
            self.assertNotEqual(rc, 0)
            self.assertEqual(stdout, "")
            self.assertIn("job failed", stderr)


class LegacyJobDirModeTests(unittest.TestCase):
    def _run_job_dir(self, job_dir, *, files=None, returncode=0, usage=None):
        files = files or {
            "draft.md": "# draft",
            "draft-humanized.md": "# humanized",
            "qa-report.md": "QA",
        }

        def fake_run(command, **kwargs):  # noqa: ARG001 -- executor subprocess
            for name, text in files.items():
                (job_dir / name).write_text(text, encoding="utf-8")
            return _FakeProc(returncode=returncode)

        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv",
                          ["content_hermes_executor.py", "--job-dir", str(job_dir)]), \
                patch.object(che.subprocess, "run", side_effect=fake_run), \
                patch.object(che, "worker_usage", return_value=usage or {}), \
                redirect_stdout(out), redirect_stderr(err):
            rc = che.main()
        return rc, out.getvalue(), err.getvalue()

    def test_job_dir_mode_keeps_exit_zero_and_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            (job_dir / "request.json").write_text(json.dumps({
                "run_id": "legacy-1", "route": "article", "message": "写文章",
            }), encoding="utf-8")
            rc, stdout, _stderr = self._run_job_dir(job_dir)
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "")
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["route"], "article")
            self.assertIn(str(job_dir / "draft.md"), status["artifacts"])
            self.assertTrue((job_dir / "events.jsonl").is_file())

    def test_job_dir_mode_invalid_request_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            (job_dir / "request.json").write_text(json.dumps({"route": "nope"}),
                                                 encoding="utf-8")
            rc, stdout, _stderr = self._run_job_dir(job_dir)
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "")
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")

    def test_job_dir_mode_worker_failure_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            (job_dir / "request.json").write_text(json.dumps({
                "run_id": "legacy-2", "route": "article", "message": "写文章",
            }), encoding="utf-8")
            rc, stdout, _stderr = self._run_job_dir(job_dir, returncode=1)
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "")
            status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")


class TokenCostUnitTests(unittest.TestCase):
    def test_token_cost_ignores_non_token_columns(self):
        usage = {"tool_call_count": 9, "estimated_cost_usd": 1.5,
                 "actual_cost_usd": 2.0, "cost_status": "actual"}
        self.assertIsNone(che.token_cost_from_usage(usage))

    def test_token_cost_sums_only_present_positive_counters(self):
        self.assertEqual(
            che.token_cost_from_usage({"input_tokens": 3, "output_tokens": 4}), 7)
        self.assertIsNone(che.token_cost_from_usage(None))


class ResultArtifactSymlinkTests(unittest.TestCase):
    def test_company_result_json_symlink_is_refused(self):
        # result.json lives in the worker-writable job dir.  A symlink planted
        # there must be treated as a missing artifact, not read through (which
        # would deliver an arbitrary host file as the worker summary).
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            (job_dir / "request.json").write_text(json.dumps({
                "run_id": "company-run", "route": "company", "message": "do work",
            }), encoding="utf-8")
            secret = Path(td) / "secret.txt"
            secret.write_text("TOP-SECRET", encoding="utf-8")

            def fake_run(command, **kwargs):  # noqa: ARG001 -- executor subprocess
                (job_dir / "task-report.md").write_text("report", encoding="utf-8")
                link = job_dir / "result.json"
                link.unlink(missing_ok=True)
                link.symlink_to(secret)
                return _FakeProc(returncode=0)

            with patch.object(che.subprocess, "run", side_effect=fake_run), \
                    patch.object(che, "worker_usage", return_value={}):
                status = che.execute_job(job_dir)

            self.assertEqual(status["status"], "failed")
            self.assertIn("result.json", status["error"])
            self.assertNotIn("TOP-SECRET", json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
