"""pipeline_helpers 单元测试（无网络）。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from douyin_to_text.pipeline_helpers import (
    engagement_from_ytdlp_info,
    like_count_from_ytdlp_info,
    published_at_from_ytdlp_info,
)


class PublishedAtTests(unittest.TestCase):
    def test_timestamp_to_iso(self) -> None:
        ts = int(datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc).timestamp())
        self.assertTrue(
            published_at_from_ytdlp_info({"timestamp": ts}).startswith("2024-03-15")
        )

    def test_upload_date_yyyymmdd(self) -> None:
        self.assertTrue(
            published_at_from_ytdlp_info({"upload_date": "20240315"}).startswith("2024-03-15")
        )

    def test_empty_info(self) -> None:
        self.assertEqual(published_at_from_ytdlp_info({}), "")
        self.assertEqual(published_at_from_ytdlp_info(None), "")


class EngagementTests(unittest.TestCase):
    def test_like_count_aliases(self) -> None:
        self.assertEqual(like_count_from_ytdlp_info({"like_count": 42}), 42)
        self.assertEqual(like_count_from_ytdlp_info({"likes": "100"}), 100)

    def test_engagement_tuple(self) -> None:
        like, comment, play = engagement_from_ytdlp_info(
            {"like_count": 10, "comment_count": 2, "view_count": 999}
        )
        self.assertEqual((like, comment, play), (10, 2, 999))

    def test_avatar_prefers_avatar_thumbnail_id(self) -> None:
        from douyin_to_text.pipeline_helpers import avatar_from_ytdlp_info

        info = {
            "thumbnails": [
                {"url": "https://example.com/banner.jpg", "id": "banner_uncropped"},
                {"url": "https://example.com/avatar.jpg", "id": "avatar_uncropped"},
            ]
        }
        self.assertEqual(avatar_from_ytdlp_info(info), "https://example.com/avatar.jpg")


if __name__ == "__main__":
    unittest.main()
