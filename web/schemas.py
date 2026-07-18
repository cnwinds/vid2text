"""API 请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SubtitleRequest(BaseModel):
    url: str = Field(..., min_length=1, description="视频页面 URL")
    wait: bool = Field(
        False,
        description="为 true 时阻塞等待字幕就绪（适合脚本一次性调用）",
    )
    timeout: int = Field(
        300,
        ge=5,
        le=900,
        description="wait=true 时最长等待秒数",
    )
    poll_interval: float = Field(
        2.0,
        ge=0.5,
        le=10.0,
        description="wait=true 时轮询间隔（秒）",
    )


class VideoRef(BaseModel):
    url: str
    platform: str
    video_id: str
    title: str = ""
    description: str = ""
    author_name: str = ""
    avatar_url: str = ""
    download_url: str = Field("", description="视频直链（CDN），可用于下载")
    duration_sec: float = Field(0, description="视频时长（秒）")


class DownloadUrlResponse(BaseModel):
    download_url: str = Field(..., description="视频 CDN 直链，可用于下载")


class SubtitleContent(BaseModel):
    text: str = Field(description="推荐使用的正文（修正后优先，否则原始）")
    raw: str = ""
    corrected: str = ""


class ProcessingInfo(BaseModel):
    status: Literal["pending", "processing"]
    step: str = Field("", description="当前处理步骤，如 download / stt")
    poll_url: str = Field(description="轮询此 URL 直到 ready=true")
    retry_after: float = Field(2.0, description="建议轮询间隔（秒）")
    message: str = "正在提取字幕，请稍后再次请求"
    notice: str = Field("", description="续跑/中断说明（如有）")
    resume_from: str = Field("", description="从哪个步骤续跑（如有缓存）")


class SubtitleResponse(BaseModel):
    ready: bool = Field(description="字幕是否已就绪；false 时请轮询 poll_url")
    cached: bool = Field(False, description="是否直接返回了已有结果（同一视频曾处理过）")
    id: int = Field(description="本次请求 ID，用于轮询")
    video: VideoRef
    subtitle: SubtitleContent | None = None
    processing: ProcessingInfo | None = None
    error: str | None = None
    retry_url: str | None = Field(None, description="失败时可 POST 此 URL 重新提取")
    progress_metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="处理中的资源指标（供 Web 进度展示）",
    )


class PaginationMeta(BaseModel):
    limit: int = Field(description="本页最大条数")
    offset: int = Field(description="本页起始偏移（0-based）")
    total: int = Field(description="符合条件的总记录数")
    has_more: bool = Field(description="是否还有下一页")
    next_offset: int | None = Field(None, description="下一页 offset；无下一页时为 null")


class SubtitleListResponse(BaseModel):
    items: list[SubtitleResponse] = Field(description="当前页字幕记录")
    pagination: PaginationMeta = Field(description="分页信息")


class ErrorResponse(BaseModel):
    detail: str = Field(description="错误说明")


class RateLimitResponse(BaseModel):
    detail: str = Field(description="限流说明")
    code: str = Field("rate_limit_active_task", description="错误码")
    active_id: int = Field(description="当前 IP 进行中的任务 ID")
    poll_url: str = Field(description="请轮询此 URL 等待当前任务完成")


# ---- monitors / settings ----

class MonitorCreateRequest(BaseModel):
    url: str = Field(..., min_length=1, description="作品链接或主页/频道链接")
    backfill_mode: Literal["recent", "all"] = Field(
        "recent", description="历史补采：recent=最近 N 条，all=可见全量"
    )
    backfill_n: int = Field(10, ge=1, le=200, description="backfill_mode=recent 时的条数")
    scan_interval_sec: int | None = Field(
        None, ge=300, le=86400, description="扫描间隔秒数；默认用全局设置"
    )


class MonitorPatchRequest(BaseModel):
    enabled: bool | None = None
    scan_interval_sec: int | None = Field(None, ge=300, le=86400)
    backfill_n: int | None = Field(None, ge=1, le=200)
    backfill_mode: Literal["recent", "all"] | None = None


class MonitorResponse(BaseModel):
    id: int
    platform: str
    author_key: str
    author_name: str = ""
    profile_url: str = ""
    avatar_url: str = ""
    source_url: str = ""
    backfill_mode: str = "recent"
    backfill_n: int = 10
    backfill_status: str = "pending"
    enabled: bool = True
    scan_interval_sec: int = 2700
    last_scan_at: str = ""
    next_scan_at: str = ""
    last_error: str = ""
    video_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class MonitorListResponse(BaseModel):
    items: list[MonitorResponse]
    pagination: PaginationMeta


class MonitorVideoItem(BaseModel):
    id: int
    platform: str
    video_id: str
    video_url: str = ""
    title: str = ""
    published_at: str = ""
    like_count: int = 0
    comment_count: int = 0
    play_count: int = 0
    task_id: int | None = None
    task_status: str | None = None
    task_error: str | None = None
    discovered_at: str = ""


class MonitorVideoListResponse(BaseModel):
    items: list[MonitorVideoItem]
    pagination: PaginationMeta


class SettingsPublicResponse(BaseModel):
    douyin_cookies_set: bool = False
    bilibili_cookies_set: bool = False
    youtube_cookies_set: bool = False
    webhook_url: str = ""
    webhook_secret_set: bool = False
    default_scan_interval_sec: int = 2700


class SettingsUpdateRequest(BaseModel):
    douyin_cookies: str | None = Field(None, description="抖音 Cookie；空字符串表示清除")
    bilibili_cookies: str | None = None
    youtube_cookies: str | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = Field(None, description="可选 HMAC 密钥")
    default_scan_interval_sec: int | None = Field(None, ge=300, le=86400)


class ScanResultResponse(BaseModel):
    fetched: int
    enqueued: int
    monitor: MonitorResponse
