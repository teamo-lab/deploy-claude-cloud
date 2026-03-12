import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main


class ClaudeCodeAPITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.temp_dir = tempfile.TemporaryDirectory()
        main.CONFIG.generated_skills_dir = Path(self.temp_dir.name)
        main.CONFIG.github_token = ""
        main.CONFIG.github_repo = "teamo-lab/clawschool"
        main.CONFIG.github_branch = "main"
        main.CONFIG.github_prefix = "generated-skills"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _payload(token: str = "TOKEN-123"):
        return {
            "token": token,
            "diagnosis": {
                "token": token,
                "lobsterName": "测试龙虾",
                "model": "integration-test",
                "score": 38,
                "iq": 74,
                "title": "冻虾仁",
                "rank": 128,
                "scope": "basic",
                "questionDetails": [
                    {
                        "questionId": "q5",
                        "title": "技能诊断 A",
                        "category": "diagnose",
                        "instructions": "",
                        "evidenceFormat": {},
                        "agentEvidence": {},
                        "score": 1,
                        "maxScore": 10,
                        "reason": "未安装",
                    },
                    {
                        "questionId": "q12",
                        "title": "网页访问失败处理",
                        "category": "resilience",
                        "instructions": "",
                        "evidenceFormat": {},
                        "agentEvidence": {},
                        "score": 2,
                        "maxScore": 10,
                        "reason": "缺少重试策略",
                    },
                ],
            },
        }

    def test_health(self):
        with patch("app.main._claude_auth_status", return_value={"ok": True, "raw": {"loggedIn": True}}):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["claudeAuth"]["ok"])

    def test_generate_skills_claude_success(self):
        mocked_skills = [
            {
                "name": "skill-source-audit",
                "summary": "安装前来源审计",
                "content": "# skill-source-audit\n\n- 审计来源\n",
            }
        ]
        with patch("app.main._call_claude_for_skills", return_value=mocked_skills):
            response = self.client.post("/api/generate-skills", json=self._payload())

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "claude")
        self.assertEqual(len(data["skills"]), 1)
        self.assertEqual(data["skills"][0]["name"], "skill-source-audit")
        self.assertTrue(data["skills"][0]["url"].startswith("https://raw.githubusercontent.com/"))

        local_file = Path(self.temp_dir.name) / "token-123" / "skill-source-audit.md"
        self.assertTrue(local_file.exists())

    def test_generate_skills_fallback(self):
        with patch("app.main._call_claude_for_skills", side_effect=RuntimeError("boom")):
            response = self.client.post("/api/generate-skills", json=self._payload("LOCAL-TOKEN"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "fallback")
        self.assertGreater(len(data["skills"]), 0)
        self.assertIn("name", data["skills"][0])
        self.assertIn("url", data["skills"][0])


if __name__ == "__main__":
    unittest.main()

