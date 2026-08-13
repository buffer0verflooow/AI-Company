"""End-to-end replay of the two real messages that triggered run 49063587."""
import unittest

from automation.company_router import classify_message


class RealMessageReplayTests(unittest.TestCase):
    def test_message1_question_stays_with_main_agent(self):
        # 消息 1: 带问号的信息查询 → main_agent (既有正确行为)
        d = classify_message(
            "检索一下当前利用AI进行二进制漏洞挖掘的方法有哪些？"
            "我要的是比如通过语法树、代码图等方式，要技术细节"
        )
        self.assertEqual(d.route, "company")
        self.assertEqual(d.action, "main_agent")

    def test_message2_methodology_now_routes_to_research(self):
        # 消息 2: 之前被误派为 security/recon 的元凶 —— 现在走 research
        d = classify_message(
            "现在方法是将二进制反编译成伪代码，然后使用SAST/静态代码分析等方法，"
            "来查找漏洞；或者通过动态fuzz，模拟执行来找漏洞"
        )
        self.assertEqual(d.route, "research")
        self.assertEqual(d.action, "dispatch_swarm")
        self.assertEqual(d.intent, "research")

    def test_real_active_tasks_keep_security(self):
        for m in (
            "扫描 example.com 并尝试绕过认证",
            "分析本机 APK 逆向报告中的认证逻辑",
            "给 acme.com 的登录接口写一个 poc",
            "扫描并分析 10.0.0.5 的漏洞",
        ):
            with self.subTest(message=m):
                d = classify_message(m)
                self.assertEqual(d.route, "security")

    def test_research_and_article_unchanged(self):
        d = classify_message("调研一下竞品 X 的技术方案")
        self.assertEqual(d.route, "research")
        d = classify_message("写一篇关于 JWT 安全的公众号文章")
        self.assertEqual(d.route, "article")


if __name__ == "__main__":
    unittest.main()
