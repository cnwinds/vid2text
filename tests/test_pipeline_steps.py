"""Pipeline 步骤流转与续跑逻辑单元测试（无网络）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from douyin_to_text.pipeline_context import TaskContext
from douyin_to_text.pipeline_steps import (
    resolve_next_step,
    resolve_step_to_run,
    run_pipeline_step,
)
from douyin_to_text.pipeline_types import PipelineOptions
from douyin_to_text.url_parser import Platform


def _noop_progress(_step: str, _metrics=None) -> None:
    pass


class EnsureParsedTests(unittest.TestCase):
    def test_from_task_rebuilds_parsed_for_monitor_task(self) -> None:
        ctx = TaskContext.from_task(
            {
                "id": 1,
                "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "platform": "youtube",
                "video_id": "dQw4w9WgXcQ",
                "title": "Test Video",
            },
            PipelineOptions(),
        )
        self.assertIsNotNone(ctx.parsed)
        self.assertEqual(ctx.parsed.platform, Platform.YOUTUBE)
        self.assertEqual(ctx.parsed.video_id, "dQw4w9WgXcQ")

    def test_from_task_without_platform_raises_on_ensure(self) -> None:
        ctx = TaskContext(url="https://example.com")
        with self.assertRaises(RuntimeError):
            ctx.ensure_parsed()

    def test_douyin_parsed_restore(self) -> None:
        ctx = TaskContext.from_task(
            {
                "id": 2,
                "video_url": "https://www.douyin.com/video/7123456789",
                "platform": "douyin",
                "video_id": "7123456789",
            },
            PipelineOptions(),
        )
        self.assertEqual(ctx.parsed.platform, Platform.DOUYIN)


class ResolveStepTests(unittest.TestCase):
    def test_fresh_task_starts_parse(self) -> None:
        task = {"id": 1, "video_url": "https://youtube.com/watch?v=abc", "progress_step": ""}
        self.assertEqual(resolve_step_to_run(task, PipelineOptions()), "parse")

    def test_monitor_task_skips_parse_goes_fetch_meta(self) -> None:
        task = {
            "id": 2,
            "video_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "video_id": "abc",
            "progress_step": "",
        }
        self.assertEqual(resolve_step_to_run(task, PipelineOptions()), "fetch_meta")

    def test_monitor_task_with_title_goes_fetch_subtitle(self) -> None:
        task = {
            "id": 2,
            "video_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "video_id": "abc",
            "title": "Hello",
            "published_at": "2024-01-01T00:00:00+00:00",
            "progress_step": "fetch_meta",
        }
        self.assertEqual(resolve_step_to_run(task, PipelineOptions()), "fetch_subtitle")

    def test_youtube_title_without_published_at_stays_fetch_meta(self) -> None:
        task = {
            "id": 3,
            "video_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "video_id": "abc",
            "title": "Hello",
            "progress_step": "fetch_meta",
        }
        self.assertEqual(resolve_step_to_run(task, PipelineOptions()), "fetch_meta")

    def test_youtube_title_with_published_at_zero_likes_goes_fetch_subtitle(self) -> None:
        task = {
            "id": 9,
            "video_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "video_id": "abc",
            "title": "Hello",
            "published_at": "2024-01-01T00:00:00+00:00",
            "like_count": 0,
            "progress_step": "fetch_meta",
        }
        self.assertEqual(resolve_step_to_run(task, PipelineOptions()), "fetch_subtitle")

    def test_fetch_subtitle_not_skipped_after_fetch_meta(self) -> None:
        task = {
            "id": 7,
            "video_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "video_id": "abc",
            "title": "T",
            "published_at": "2024-01-01T00:00:00+00:00",
            "progress_step": "fetch_meta",
        }
        self.assertEqual(resolve_step_to_run(task, PipelineOptions()), "fetch_subtitle")

    def test_fetch_subtitle_skipped_after_completed(self) -> None:
        task = {
            "id": 8,
            "video_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "video_id": "abc",
            "title": "T",
            "published_at": "2024-01-01T00:00:00+00:00",
            "progress_step": "fetch_subtitle",
        }
        with tempfile.TemporaryDirectory() as tmp:
            opts = PipelineOptions(work_dir=Path(tmp))
            self.assertEqual(resolve_step_to_run(task, opts), "download")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            task = {
                "id": 4,
                "video_url": "https://youtube.com/watch?v=abc",
                "platform": "youtube",
                "video_id": "abc",
                "title": "Hello",
                "published_at": "2024-01-01T00:00:00+00:00",
                "progress_step": "stt",
            }
            opts = PipelineOptions(work_dir=work)
            self.assertEqual(resolve_step_to_run(task, opts), "download")

    def test_stt_with_raw_goes_correct(self) -> None:
        task = {
            "id": 5,
            "video_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "video_id": "abc",
            "title": "Hello",
            "published_at": "2024-01-01T00:00:00+00:00",
            "progress_step": "stt",
            "raw_transcript": "已有文稿",
        }
        self.assertEqual(resolve_step_to_run(task, PipelineOptions()), "correct")

    def test_done_when_corrected_exists(self) -> None:
        task = {
            "id": 6,
            "corrected_transcript": "完成",
            "progress_step": "correct",
        }
        self.assertIsNone(resolve_step_to_run(task, PipelineOptions()))


class ResolveNextStepTests(unittest.TestCase):
    def test_parse_to_fetch_meta(self) -> None:
        ctx = TaskContext(platform="youtube", video_id="x")
        self.assertEqual(resolve_next_step("parse", ctx, PipelineOptions()), "fetch_meta")

    def test_subtitle_hit_skips_to_correct(self) -> None:
        ctx = TaskContext(platform="youtube", video_id="x", raw_transcript="字幕")
        self.assertEqual(resolve_next_step("fetch_subtitle", ctx, PipelineOptions()), "correct")

    def test_ytdlp_download_to_stt(self) -> None:
        ctx = TaskContext(platform="youtube", video_id="x")
        self.assertEqual(resolve_next_step("download", ctx, PipelineOptions()), "stt")

    def test_douyin_download_to_extract(self) -> None:
        ctx = TaskContext(platform="douyin", video_id="x")
        self.assertEqual(resolve_next_step("download", ctx, PipelineOptions()), "extract_audio")


class RunPipelineStepTests(unittest.TestCase):
    @patch("douyin_to_text.pipeline_steps.download_audio")
    def test_download_without_prior_parse_step(self, mock_dl: MagicMock) -> None:
        """监控任务：DB 已有 platform/video_id，直接跑 download 不应报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            out = work / "abc.wav"
            mock_dl.return_value = out

            ctx = TaskContext.from_task(
                {
                    "id": 10,
                    "video_url": "https://www.youtube.com/watch?v=abc",
                    "platform": "youtube",
                    "video_id": "abc",
                    "title": "T",
                    "progress_step": "fetch_subtitle",
                },
                PipelineOptions(work_dir=work),
            )
            run_pipeline_step(ctx, "download", PipelineOptions(work_dir=work), _noop_progress)
            mock_dl.assert_called_once()

    @patch("douyin_to_text.pipeline_steps.fetch_metadata")
    def test_douyin_fetch_meta_without_parse(self, mock_meta: MagicMock) -> None:
        mock_meta.return_value = MagicMock(
            desc="d",
            caption="title",
            duration_ms=1000,
            video_url="http://v",
            raw_detail={},
            platform_subtitle_text="",
            platform_subtitle_source="",
        )
        ctx = TaskContext.from_task(
            {
                "id": 11,
                "video_url": "https://www.douyin.com/video/999",
                "platform": "douyin",
                "video_id": "999",
            },
            PipelineOptions(),
        )
        run_pipeline_step(ctx, "fetch_meta", PipelineOptions(), _noop_progress)
        self.assertEqual(ctx.title, "title")


class QueuedMetricsTests(unittest.TestCase):
    def test_queued_step_in_metrics(self) -> None:
        m = json.loads(
            '{"step":"download","detail":"排队等待 · 语音识别","queued_step":"stt"}'
        )
        self.assertEqual(m["queued_step"], "stt")
        self.assertIn("排队等待", m["detail"])


if __name__ == "__main__":
    unittest.main()
