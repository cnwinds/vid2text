"""YouTube enrich 并发与限流。"""

from __future__ import annotations

import unittest

from douyin_to_text.author_feed_youtube import enrich_max_concurrent
from douyin_to_text.author_models import FeedVideo


class EnrichConcurrencyTests(unittest.TestCase):
    def test_enrich_max_concurrent_default(self) -> None:
        self.assertGreaterEqual(enrich_max_concurrent(), 1)

    def test_skip_enrich_when_complete(self) -> None:
        from douyin_to_text.author_feed_youtube import enrich_youtube_feed_videos

        videos = [
            FeedVideo(
                video_id="x",
                url="https://www.youtube.com/watch?v=x",
                title="t",
                published_at="2024-01-01T00:00:00+00:00",
                like_count=10,
            )
        ]
        out = enrich_youtube_feed_videos(videos, cookies=None)
        self.assertEqual(out[0].like_count, 10)


if __name__ == "__main__":
    unittest.main()
