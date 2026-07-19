"""monitor_service.scan_monitor 单测（mock feed，无网络）。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from douyin_to_text.author_models import FeedVideo
from tests._test_env import restore_db, use_temp_db


class ScanMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        import web.db as db_mod
        import web.db_connection as conn_mod

        self._orig_db = db_mod.DB_PATH
        self._orig_conn = conn_mod.DB_PATH
        self.db_path = use_temp_db()

        from web import db

        self.monitor = db.create_monitor(
            platform="youtube",
            author_key="UCtest",
            author_name="Test Channel",
            profile_url="https://www.youtube.com/channel/UCtest/videos",
            backfill_mode="recent",
            backfill_n=5,
        )

    def tearDown(self) -> None:
        import web.db as db_mod
        import web.db_connection as conn_mod

        restore_db(self.db_path, self._orig_db, self._orig_conn)

    @patch("web.monitor_service.collect_feed_videos")
    def test_scan_syncs_metadata_and_enqueues_new_video(self, mock_collect) -> None:
        from web import db
        from web.monitor_service import scan_monitor

        mock_collect.return_value = (
            [
                FeedVideo(
                    video_id="vid1",
                    url="https://www.youtube.com/watch?v=vid1",
                    title="First",
                    published_at="2026-01-01T00:00:00+00:00",
                    like_count=10,
                    comment_count=2,
                    play_count=100,
                ),
                FeedVideo(
                    video_id="vid2",
                    url="https://www.youtube.com/watch?v=vid2",
                    title="Second",
                    published_at="2026-01-02T00:00:00+00:00",
                    like_count=20,
                    comment_count=3,
                    play_count=200,
                ),
            ],
            "",
            False,
        )

        result = scan_monitor(self.monitor["id"])
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["enqueued"], 2)

        mv1 = db.get_monitor_video("youtube", "vid1")
        self.assertIsNotNone(mv1)
        assert mv1 is not None
        self.assertEqual(mv1["title"], "First")
        self.assertEqual(mv1["like_count"], 10)
        self.assertEqual(mv1["comment_count"], 2)
        self.assertIsNotNone(mv1.get("task_id"))

        updated = db.get_monitor(self.monitor["id"])
        assert updated is not None
        self.assertEqual(updated.get("backfill_status"), "done")

    @patch("web.monitor_service.collect_feed_videos")
    def test_rescan_updates_existing_without_reenqueue(self, mock_collect) -> None:
        from web import db
        from web.monitor_service import scan_monitor

        db.upsert_monitor_video(
            monitor_id=self.monitor["id"],
            platform="youtube",
            video_id="vid1",
            video_url="https://www.youtube.com/watch?v=vid1",
            title="Old",
            like_count=0,
            task_id=db.create_task(
                "https://www.youtube.com/watch?v=vid1",
                "youtube",
                "vid1",
            )["id"],
        )
        db.update_monitor(self.monitor["id"], backfill_status="done")

        mock_collect.return_value = (
            [
                FeedVideo(
                    video_id="vid1",
                    url="https://www.youtube.com/watch?v=vid1",
                    title="Updated",
                    published_at="2026-03-01T00:00:00+00:00",
                    like_count=99,
                    comment_count=5,
                    play_count=999,
                ),
            ],
            "",
            False,
        )

        result = scan_monitor(self.monitor["id"])
        self.assertEqual(result["enqueued"], 0)

        mv = db.get_monitor_video("youtube", "vid1")
        assert mv is not None
        self.assertEqual(mv["title"], "Updated")
        self.assertEqual(mv["like_count"], 99)
        self.assertEqual(mv["comment_count"], 5)


if __name__ == "__main__":
    unittest.main()
