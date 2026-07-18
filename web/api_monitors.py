"""账号监控与设置 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from web import db
from web.monitor_service import (
    create_monitor_from_url,
    monitor_to_view,
    scan_monitor,
)
from web.schemas import (
    MonitorCreateRequest,
    MonitorListResponse,
    MonitorPatchRequest,
    MonitorResponse,
    MonitorVideoItem,
    MonitorVideoListResponse,
    PaginationMeta,
    ScanResultResponse,
    SettingsPublicResponse,
    SettingsUpdateRequest,
)

router = APIRouter(prefix="/api/v1", tags=["monitors"])


def _pagination(limit: int, offset: int, total: int) -> PaginationMeta:
    end = offset + limit
    has_more = end < total
    return PaginationMeta(
        limit=limit,
        offset=offset,
        total=total,
        has_more=has_more,
        next_offset=end if has_more else None,
    )


@router.post("/monitors", response_model=MonitorResponse, status_code=201)
def create_monitor(body: MonitorCreateRequest) -> MonitorResponse:
    try:
        row = create_monitor_from_url(
            body.url,
            backfill_mode=body.backfill_mode,
            backfill_n=body.backfill_n,
            scan_interval_sec=body.scan_interval_sec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析作者失败: {exc}") from exc
    if not row:
        raise HTTPException(status_code=500, detail="创建监控失败")
    return MonitorResponse(**monitor_to_view(row))


@router.get("/monitors", response_model=MonitorListResponse)
def list_monitors(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> MonitorListResponse:
    total = db.count_monitors()
    rows = db.list_monitors(limit=limit, offset=offset)
    return MonitorListResponse(
        items=[MonitorResponse(**monitor_to_view(r)) for r in rows],
        pagination=_pagination(limit, offset, total),
    )


@router.get("/monitors/{monitor_id}", response_model=MonitorResponse)
def get_monitor(monitor_id: int) -> MonitorResponse:
    row = db.get_monitor(monitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="监控不存在")
    return MonitorResponse(**monitor_to_view(row))


@router.patch("/monitors/{monitor_id}", response_model=MonitorResponse)
def patch_monitor(monitor_id: int, body: MonitorPatchRequest) -> MonitorResponse:
    row = db.get_monitor(monitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="监控不存在")
    fields: dict = {}
    if body.enabled is not None:
        fields["enabled"] = 1 if body.enabled else 0
    if body.scan_interval_sec is not None:
        fields["scan_interval_sec"] = body.scan_interval_sec
    if body.backfill_n is not None:
        fields["backfill_n"] = body.backfill_n
    if body.backfill_mode is not None:
        fields["backfill_mode"] = body.backfill_mode
    updated = db.update_monitor(monitor_id, **fields) if fields else row
    return MonitorResponse(**monitor_to_view(updated))  # type: ignore[arg-type]


@router.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int) -> Response:
    if not db.delete_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="监控不存在")
    return Response(status_code=204)


@router.post("/monitors/{monitor_id}/scan", response_model=ScanResultResponse)
def trigger_scan(monitor_id: int) -> ScanResultResponse:
    row = db.get_monitor(monitor_id)
    if not row:
        raise HTTPException(status_code=404, detail="监控不存在")
    try:
        result = scan_monitor(monitor_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScanResultResponse(
        fetched=result["fetched"],
        enqueued=result["enqueued"],
        monitor=MonitorResponse(**monitor_to_view(result["monitor"])),
    )


@router.get("/monitors/{monitor_id}/videos", response_model=MonitorVideoListResponse)
def list_videos(
    monitor_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> MonitorVideoListResponse:
    if not db.get_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="监控不存在")
    total = db.count_monitor_videos(monitor_id)
    rows = db.list_monitor_videos(monitor_id, limit=limit, offset=offset)
    items = [
        MonitorVideoItem(
            id=r["id"],
            platform=r["platform"],
            video_id=r["video_id"],
            video_url=r.get("video_url") or "",
            title=r.get("title") or "",
            published_at=r.get("published_at") or "",
            like_count=int(r.get("like_count") or 0),
            comment_count=int(r.get("comment_count") or 0),
            play_count=int(r.get("play_count") or 0),
            task_id=r.get("task_id"),
            task_status=r.get("task_status"),
            task_error=r.get("task_error"),
            discovered_at=r.get("discovered_at") or "",
        )
        for r in rows
    ]
    return MonitorVideoListResponse(
        items=items, pagination=_pagination(limit, offset, total)
    )


def _settings_public() -> SettingsPublicResponse:
    interval_raw = db.get_setting("default_scan_interval_sec", str(db.DEFAULT_SCAN_INTERVAL_SEC))
    try:
        interval = max(300, int(interval_raw))
    except ValueError:
        interval = db.DEFAULT_SCAN_INTERVAL_SEC
    return SettingsPublicResponse(
        douyin_cookies_set=bool(db.get_setting("douyin_cookies", "").strip()),
        bilibili_cookies_set=bool(db.get_setting("bilibili_cookies", "").strip()),
        youtube_cookies_set=bool(db.get_setting("youtube_cookies", "").strip()),
        webhook_url=db.get_setting("webhook_url", ""),
        webhook_secret_set=bool(db.get_setting("webhook_secret", "").strip()),
        default_scan_interval_sec=interval,
    )


@router.get("/settings", response_model=SettingsPublicResponse)
def get_settings() -> SettingsPublicResponse:
    return _settings_public()


@router.put("/settings", response_model=SettingsPublicResponse)
def put_settings(body: SettingsUpdateRequest) -> SettingsPublicResponse:
    pairs: dict[str, str] = {}
    if body.douyin_cookies is not None:
        pairs["douyin_cookies"] = body.douyin_cookies.strip()
    if body.bilibili_cookies is not None:
        pairs["bilibili_cookies"] = body.bilibili_cookies.strip()
    if body.youtube_cookies is not None:
        pairs["youtube_cookies"] = body.youtube_cookies.strip()
    if body.webhook_url is not None:
        pairs["webhook_url"] = body.webhook_url.strip()
    if body.webhook_secret is not None:
        pairs["webhook_secret"] = body.webhook_secret.strip()
    if body.default_scan_interval_sec is not None:
        pairs["default_scan_interval_sec"] = str(body.default_scan_interval_sec)
    if pairs:
        db.set_settings(pairs)
    return _settings_public()
