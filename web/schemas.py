"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubmitRequest(BaseModel):
    url: str = Field(..., min_length=1, description="视频 URL")


class TaskSummary(BaseModel):
    id: int
    video_url: str
    platform: str
    video_id: str
    title: str = ""
    status: str
    created_at: str
    updated_at: str


class TaskDetail(TaskSummary):
    description: str = ""
    raw_transcript: str = ""
    corrected_transcript: str = ""
    error_message: str = ""


class SubmitResponse(BaseModel):
    cached: bool = Field(description="是否命中已有记录")
    task: TaskDetail


class HistoryResponse(BaseModel):
    items: list[TaskSummary]
    total: int
