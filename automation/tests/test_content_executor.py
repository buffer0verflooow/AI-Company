from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.content_hermes_executor import build_prompt, build_worker_invocation, worker_usage
import sqlite3


class ContentPromptTests(unittest.TestCase):
    def test_company_prompt_requires_execution_report_and_structured_result(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td)
            prompt, expected = build_prompt({
                "route": "company",
                "run_id": "company-run",
                "message": "修改公司路由并运行测试",
            }, job_dir)
            self.assertEqual(expected, ["task-report.md", "result.json"])
            self.assertIn("完成被委派的实际工作", prompt)
            self.assertIn(str(job_dir / "task-report.md"), prompt)
            self.assertIn(str(job_dir / "result.json"), prompt)
            self.assertIn("保留工作区中与本任务无关的现有改动", prompt)
            self.assertIn("needs_approval", prompt)
    def test_article_prompt_requires_draft_qa_and_no_publish(self):
        with tempfile.TemporaryDirectory() as td:
            prompt, expected = build_prompt({"route": "article", "message": "写公众号文章"}, Path(td))
            self.assertEqual(expected, ["draft.md", "draft-humanized.md", "qa-report.md"])
            self.assertTrue(prompt.startswith("[COMPANY_WORKER_INTERNAL]"))
            self.assertIn("Gate 1", prompt)
            self.assertIn("不执行公众号推送", prompt)

    def test_video_prompt_requires_preproduction_and_no_upload(self):
        with tempfile.TemporaryDirectory() as td:
            prompt, expected = build_prompt({"route": "video", "message": "生成视频"}, Path(td))
            self.assertEqual(expected, ["video-script.md", "storyboard.md", "production-plan.md"])
            self.assertIn("严禁声称视频已渲染", prompt)
            self.assertIn("不上传 B站", prompt)

    def test_article_worker_has_no_terminal_and_only_job_write_root(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            command, cwd, env = build_worker_invocation(
                {"route": "article"}, job_dir, "worker prompt"
            )
            self.assertEqual(cwd, job_dir.resolve())
            self.assertEqual(env["HERMES_WRITE_SAFE_ROOT"], str(job_dir.resolve()))
            self.assertEqual(env["TERMINAL_CWD"], str(job_dir.resolve()))
            self.assertEqual(env["COMPANY_ROUTER_BYPASS"], "1")
            self.assertEqual(env["HERMES_SESSION_SOURCE"], "tool")
            toolsets = command[command.index("--toolsets") + 1].split(",")
            self.assertNotIn("terminal", toolsets)
            self.assertNotIn("process", toolsets)
            self.assertNotIn("code_execution", toolsets)
            self.assertNotIn("skills", toolsets)
            self.assertIn("file", toolsets)
            self.assertEqual(command[command.index("--skills") + 1], "humanizer")

    def test_company_worker_keeps_authorized_company_root(self):
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            command, cwd, env = build_worker_invocation(
                {"route": "company"}, job_dir, "worker prompt"
            )
            self.assertEqual(cwd, Path("/home/pwn/workspace/company"))
            self.assertEqual(env["HERMES_WRITE_SAFE_ROOT"], "/home/pwn/workspace/company")
            self.assertNotIn("--toolsets", command)

    def test_worker_usage_resolves_session_by_job_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "state.db"
            db = sqlite3.connect(db_path)
            db.executescript("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
                    cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                    tool_call_count INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT
                );
                CREATE TABLE messages (id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);
            """)
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("s1", "m", 10, 2, 30, 0, 1, 3, 0.0, None, "unknown"))
            db.execute("INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)", ("s1", "user", f"产物目录：{root}", 1.0))
            db.commit()
            db.close()
            usage = worker_usage(root, db_path)
            self.assertEqual(usage["id"], "s1")
            self.assertEqual(usage["input_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
