"""API 集成测试：POST → 202 → mock 完成 → 200。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from douyin_to_text.url_parser import ParsedVideoUrl, Platform
from tests._test_env import restore_db, use_temp_db


class ApiIntegrationTests(unittest.TestCase):
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

    @patch("web.services.resolve_and_parse")
    def test_submit_processing_then_done(self, mock_parse) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        mock_parse.return_value = ParsedVideoUrl(
            platform=Platform.YOUTUBE,
            video_id="dQw4w9WgXcQ",
            original_url=url,
            canonical_url=url,
        )
        res = self.client.post("/api/v1/subtitles", json={"url": url})
        self.assertEqual(res.status_code, 202)
        body = res.json()
        self.assertFalse(body["ready"])
        task_id = int(body["id"])
        self.assertIn("processing", body)

        import web.db as db

        db.update_task(
            task_id,
            status="done",
            title="Test Video",
            corrected_transcript="Hello world transcript.",
            progress_step="correct",
        )

        res2 = self.client.get(f"/api/v1/subtitles/{task_id}")
        self.assertEqual(res2.status_code, 200)
        done = res2.json()
        self.assertTrue(done["ready"])
        self.assertEqual(done["subtitle"]["text"], "Hello world transcript.")

    def test_metrics_includes_histogram_types(self) -> None:
        from web.metrics_registry import observe_monitor_scan, observe_pipeline_step

        observe_monitor_scan(1.5)
        observe_pipeline_step("parse", 0.2)
        res = self.client.get("/metrics")
        self.assertEqual(res.status_code, 200)
        text = res.content.decode()
        self.assertIn("vid2text_monitor_scan_seconds_bucket", text)
        self.assertIn("vid2text_pipeline_step_seconds_bucket", text)


if __name__ == "__main__":
    unittest.main()
