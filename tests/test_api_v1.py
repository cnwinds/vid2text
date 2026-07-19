"""API 冒烟测试（TestClient + 临时 DB）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class ApiV1SmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.addCleanup(lambda: os.unlink(self.db_path) if self.db_path.exists() else None)

        import web.db as db_mod
        import web.db_connection as conn_mod

        self._orig_db = db_mod.DB_PATH
        self._orig_conn = conn_mod.DB_PATH
        db_mod.DB_PATH = self.db_path
        conn_mod.DB_PATH = self.db_path
        db_mod.init_db()

        from web.app import app

        self.client = TestClient(app)

    def tearDown(self) -> None:
        import web.db as db_mod
        import web.db_connection as conn_mod

        db_mod.DB_PATH = self._orig_db
        conn_mod.DB_PATH = self._orig_conn

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
