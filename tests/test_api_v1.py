"""API 冒烟测试（TestClient + 临时 DB）。"""

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests._test_env import restore_db, use_temp_db


class ApiV1SmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        import web.db as db_mod
        import web.db_connection as conn_mod

        self._orig_db = db_mod.DB_PATH
        self._orig_conn = conn_mod.DB_PATH
        self.db_path = use_temp_db()

        from web.app import app

        self.client = TestClient(app)

    def tearDown(self) -> None:
        import web.db as db_mod
        import web.db_connection as conn_mod

        restore_db(self.db_path, self._orig_db, self._orig_conn)

    def test_health(self) -> None:
        res = self.client.get("/health")
        self.assertIn(res.status_code, (200, 503))
        data = res.json()
        self.assertIn("db", data)
        self.assertIn("scheduler", data)

    def test_metrics(self) -> None:
        res = self.client.get("/metrics")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"vid2text_tasks_pending", res.content)

    def test_subtitles_rejects_ssrf(self) -> None:
        res = self.client.post(
            "/api/v1/subtitles",
            json={"url": "http://127.0.0.1/x"},
        )
        self.assertEqual(res.status_code, 400)

    def test_subtitles_list_empty(self) -> None:
        res = self.client.get("/api/v1/subtitles?limit=5")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["items"], [])


if __name__ == "__main__":
    unittest.main()
