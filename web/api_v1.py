"""REST API v1 — 以「获取视频字幕」为核心的对接接口。"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from web import db
from web.api_docs import (
    ERROR_RESPONSES,
    GET_LIST_RESPONSES,
    GET_SUBTITLE_RESPONSES,
    POST_SUBTITLE_RESPONSES,
    RETRY_RESPONSES,
    TEXT_RESPONSES,
    build_docs_markdown,
    build_schema_dict,
)
from web.schemas import (
    DownloadCheckResponse,
    DownloadUrlResponse,
    PaginationMeta,
    ProcessingInfo,
    RateLimitResponse,
    SubtitleContent,
    SubtitleListResponse,
    SubtitleRequest,
    SubtitleResponse,
    SystemInfoResponse,
    VideoRef,
    WorkCachePublic,
)
from web.services import (
    check_ip_rate_limit,
    enrich_task_duration,
    find_by_url,
    rate_limit_payload,
    resolve_download_url,
    prepare_video_file,
    row_to_subtitle,
    submit_url,
    subtitle_http_status,
)
from web.rate_limit import RateLimitError, get_client_ip
from web.work_cache import work_cache_public, clear_video_cache

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _subtitle_payload(row: dict, request: Request, *, cached: bool = False) -> dict:
    return row_to_subtitle(row, cached=cached, base_url=_base_url(request))


def _subtitle_response(row: dict, request: Request, *, cached: bool = False) -> JSONResponse:
    payload = _subtitle_payload(row, request, cached=cached)
    return JSONResponse(status_code=subtitle_http_status(payload), content=payload)


def _as_subtitle_model(row: dict, request: Request, *, cached: bool = False) -> SubtitleResponse:
    data = _subtitle_payload(row, request, cached=cached)
    subtitle = data.get("subtitle")
    processing = data.get("processing")
    return SubtitleResponse(
        ready=data["ready"],
        cached=data["cached"],
        id=data["id"],
        video=VideoRef(**data["video"]),
        subtitle=SubtitleContent(**subtitle) if subtitle else None,
        processing=ProcessingInfo(**processing) if processing else None,
        error=data.get("error"),
        retry_url=data.get("retry_url"),
        progress_metrics=data.get("progress_metrics") or {},
    )


def _build_pagination(limit: int, offset: int, total: int) -> PaginationMeta:
    end = offset + limit
    has_more = end < total
    return PaginationMeta(
        limit=limit,
        offset=offset,
        total=total,
        has_more=has_more,
        next_offset=end if has_more else None,
    )


def _lookup_subtitle_by_url(url: str, request: Request) -> Response:
    try:
        row = find_by_url(url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not row:
        raise HTTPException(
            status_code=404,
            detail="该视频尚无提取记录，请先 POST /api/v1/subtitles",
        )
    enrich_task_duration(row, persist=True)
    cached = row["status"] == "done"
    return _subtitle_response(row, request, cached=cached)


async def _wait_until_done(
    req_id: int,
    *,
    timeout: float,
    poll_interval: float,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = db.get_task(req_id)
        if not row:
            raise HTTPException(status_code=404, detail="请求不存在")
        if row["status"] == "done":
            return row
        if row["status"] == "failed":
            raise HTTPException(
                status_code=422,
                detail=row.get("error_message") or "字幕提取失败",
            )
        await asyncio.sleep(poll_interval)
    raise HTTPException(
        status_code=408,
        detail=f"等待超时（{int(timeout)}s），请稍后 GET /api/v1/subtitles/{req_id} 继续获取",
    )


@router.post(
    "/subtitles",
    summary="获取视频字幕",
    description=(
        "提交视频 URL 获取字幕/口播文稿。\n\n"
        "**响应说明：**\n"
        "- `200` — `ready: true`，读取 `subtitle.text`；响应含 `id`（任务编号）\n"
        "- `202` — `ready: false`，读取 `id` 并轮询 `processing.poll_url`（或 GET /subtitles/{id}）\n"
        "- `422` — 提取失败，查看 `error` / `retry_url`\n"
        "- `408` — `wait=true` 超时\n"
        "- `400` — URL 无效\n"
        "- `429` — 当前 IP 已有进行中的任务，需等待完成"
    ),
    response_model=SubtitleResponse,
    responses=POST_SUBTITLE_RESPONSES,
)
async def obtain_subtitles(body: SubtitleRequest, request: Request) -> Response:
    client_ip = get_client_ip(request)
    try:
        row, cached = submit_url(body.url, client_ip=client_ip)
    except RateLimitError as exc:
        payload = rate_limit_payload(exc.active_task, _base_url(request))
        return JSONResponse(status_code=429, content=payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.wait and row["status"] in ("pending", "processing"):
        row = await _wait_until_done(
            row["id"],
            timeout=float(body.timeout),
            poll_interval=body.poll_interval,
        )
        cached = False

    return _subtitle_response(row, request, cached=cached)


@router.get(
    "/subtitles/by-url",
    summary="按视频 URL 查询单条",
    description=(
        "推荐用法：传 `url` 查询该视频最新任务，响应同 GET /subtitles/{id}。\n\n"
        "**响应说明：**\n"
        "- `200` / `202` / `422` — 同 GET /subtitles/{id}\n"
        "- `404` — 该视频尚无记录"
    ),
    response_model=SubtitleResponse,
    responses={**GET_SUBTITLE_RESPONSES, 400: ERROR_RESPONSES[400]},
)
async def get_subtitles_by_url(
    request: Request,
    url: str = Query(..., min_length=1, description="视频页面 URL"),
) -> Response:
    return _lookup_subtitle_by_url(url, request)


@router.get(
    "/subtitles/{req_id}",
    summary="继续获取字幕",
    description=(
        "轮询字幕结果，语义同 POST /subtitles 的返回。\n\n"
        "**响应说明：**\n"
        "- `200` — 字幕已就绪\n"
        "- `202` — 仍在提取\n"
        "- `422` — 已失败\n"
        "- `404` — id 不存在"
    ),
    response_model=SubtitleResponse,
    responses=GET_SUBTITLE_RESPONSES,
)
async def get_subtitles(req_id: int, request: Request) -> Response:
    row = db.get_task(req_id)
    if not row:
        raise HTTPException(status_code=404, detail="请求不存在")
    enrich_task_duration(row, persist=True)
    return _subtitle_response(row, request)


@router.get(
    "/subtitles",
    summary="历史列表（分页）",
    description=(
        "分页返回历史提取记录。\n\n"
        "- `limit` 每页条数，默认 20，最大 100\n"
        "- `offset` 偏移量，默认 0；下一页用 `pagination.next_offset`\n"
        "- `pagination.total` 为总记录数\n\n"
        "按 URL 查单条请用 **GET /subtitles/by-url?url=...**（"
        "旧版 `?url=` 仍兼容，将在后续版本移除）。"
    ),
    response_model=SubtitleListResponse,
    responses=GET_LIST_RESPONSES,
)
async def list_subtitles(
    request: Request,
    url: str | None = Query(
        None,
        description="[已弃用] 请改用 GET /subtitles/by-url；若提供则按 URL 查询单条",
        deprecated=True,
    ),
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
) -> SubtitleListResponse | Response:
    if url:
        return _lookup_subtitle_by_url(url, request)

    total = db.count_tasks()
    rows = db.list_history(limit=limit, offset=offset)
    for row in rows:
        enrich_task_duration(row, persist=False)
    items = [_as_subtitle_model(r, request) for r in rows]
    return SubtitleListResponse(
        items=items,
        pagination=_build_pagination(limit, offset, total),
    )


@router.get(
    "/subtitles/{req_id}/text",
    summary="获取纯文本字幕",
    description=(
        "返回 `text/plain` 正文。\n\n"
        "**响应说明：**\n"
        "- `200` — 纯文本 body\n"
        "- `202` — 仍在提取（JSON body，含 poll_url）\n"
        "- `422` — 失败（JSON body）\n"
        "- `404` — 无记录或无文本\n\n"
        "Query `field`: `text`（默认，修正后优先）| `raw` | `corrected`"
    ),
    responses=TEXT_RESPONSES,
)
async def get_subtitle_text(
    req_id: int,
    request: Request,
    field: Literal["text", "raw", "corrected"] = Query(
        "text",
        description="text=修正后优先；raw=原始；corrected=仅修正版",
    ),
) -> Response:
    row = db.get_task(req_id)
    if not row:
        raise HTTPException(status_code=404, detail="请求不存在")

    if row["status"] in ("pending", "processing"):
        payload = _subtitle_payload(row, request)
        return JSONResponse(status_code=202, content=payload)

    if row["status"] == "failed":
        payload = _subtitle_payload(row, request)
        return JSONResponse(status_code=422, content=payload)

    raw = (row.get("raw_transcript") or "").strip()
    corrected = (row.get("corrected_transcript") or "").strip()
    if field == "raw":
        content = raw
    elif field == "corrected":
        content = corrected
    else:
        content = corrected or raw

    if not content:
        raise HTTPException(status_code=404, detail="无字幕内容")
    return PlainTextResponse(content)


@router.post(
    "/subtitles/{req_id}/download-url",
    summary="获取视频下载直链",
    description=(
        "解析并返回视频 CDN 直链（非页面 URL）。\n\n"
        "**响应说明：**\n"
        "- `200` — 返回 `download_url`\n"
        "- `404` — id 不存在\n"
        "- `422` — 解析失败"
    ),
    response_model=DownloadUrlResponse,
)
async def fetch_download_url(req_id: int) -> DownloadUrlResponse:
    row = db.get_task(req_id)
    if not row:
        raise HTTPException(status_code=404, detail="请求不存在")
    url, err = await asyncio.to_thread(resolve_download_url, req_id)
    if not url:
        raise HTTPException(status_code=422, detail=err or "未能解析视频直链")
    return DownloadUrlResponse(download_url=url)


@router.get(
    "/subtitles/{req_id}/download",
    summary="下载视频文件",
    description=(
        "通过服务端下载视频（自动携带 Referer / Cookie），返回 MP4 文件。\n\n"
        "Query `check=1` 时仅校验任务是否可下载，不传输文件。\n\n"
        "**响应说明：**\n"
        "- `200` — MP4 文件流\n"
        "- `400` — 任务未完成\n"
        "- `404` — id 不存在\n"
        "- `422` — 下载失败"
    ),
    response_model=None,
)
async def download_subtitle_video(
    req_id: int,
    check: bool = Query(False, description="为 true 时仅校验，不下载文件"),
) -> Response:
    row = db.get_task(req_id)
    if not row:
        raise HTTPException(status_code=404, detail="请求不存在")
    if row["status"] not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="任务尚未完成，暂不可下载视频")
    if check:
        return DownloadCheckResponse(ok=True)

    try:
        path, filename = await asyncio.to_thread(prepare_video_file, req_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="请求不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 文件名只用 ASCII，避免 Content-Disposition 编码问题
    ascii_name = f"{row['video_id']}.mp4"
    return FileResponse(path, media_type="video/mp4", filename=ascii_name)


@router.post(
    "/subtitles/{req_id}/retry",
    summary="重新提取字幕",
    description=(
        "将失败记录重新排队。\n\n"
        "Query **`fresh=true`** 时清空进度与本地缓存元数据，从头提取。\n\n"
        "**响应说明：**\n"
        "- `202` — 已重新排队，轮询 poll_url\n"
        "- `400` — 非失败状态，不可重试\n"
        "- `404` — id 不存在"
    ),
    response_model=SubtitleResponse,
    responses=RETRY_RESPONSES,
)
async def retry_subtitles(
    req_id: int,
    request: Request,
    fresh: bool = Query(False, description="为 true 时清空进度与缓存元数据，从头提取"),
) -> Response:
    client_ip = get_client_ip(request)
    existing = db.get_task(req_id)
    if not existing:
        raise HTTPException(status_code=404, detail="请求不存在")
    if existing["status"] in ("pending", "processing"):
        return _subtitle_response(existing, request)
    if existing["status"] != "failed":
        raise HTTPException(status_code=400, detail="仅失败记录可重试")
    try:
        check_ip_rate_limit(client_ip, exclude_id=req_id)
    except RateLimitError as exc:
        payload = rate_limit_payload(exc.active_task, _base_url(request))
        return JSONResponse(status_code=429, content=payload)

    if fresh:
        clear_video_cache(existing.get("video_id") or "")
    row = db.retry_task(req_id, fresh=fresh)
    if not row:
        raise HTTPException(status_code=400, detail="重试失败")
    return _subtitle_response(row, request)


@router.get(
    "/system/info",
    summary="服务端运行信息",
    description="只读摘要：work 缓存配额与占用等（不含 Cookie / Webhook 密钥）。",
    response_model=SystemInfoResponse,
)
async def system_info() -> SystemInfoResponse:
    return SystemInfoResponse(work_cache=WorkCachePublic(**work_cache_public()))


@router.get("/docs.md", summary="API 说明（Markdown）")
async def api_docs_markdown(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        build_docs_markdown(_base_url(request)),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/schema.json", summary="API 结构（JSON，含响应说明）")
async def api_schema_json(request: Request) -> JSONResponse:
    return JSONResponse(build_schema_dict(_base_url(request)))

