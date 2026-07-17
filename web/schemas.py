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
