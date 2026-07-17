"""FastAPI Web 应用入口。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from douyin_to_text.url_parser import parse_video_url, resolve_short_url
from web import db
from web.schemas import HistoryResponse, SubmitRequest, SubmitResponse, TaskDetail, TaskSummary
from web.worker import start_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(
    title="vid2text",
    description="视频 URL 转文字 Web 服务",
    version="0.2.0",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _task_detail(row: dict) -> TaskDetail:
    return TaskDetail(**row)


def _task_summary(row: dict) -> TaskSummary:
    return TaskSummary(
        id=row["id"],
        video_url=row["video_url"],
        platform=row["platform"],
        video_id=row["video_id"],
        title=row.get("title") or "",
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _resolve_and_parse(url: str):
    url = url.strip()
    if any(x in url for x in ("v.douyin.com", "b23.tv")):
        url = resolve_short_url(url)
    return parse_video_url(url)


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    start_worker()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/submit", response_model=SubmitResponse)
async def submit_video(body: SubmitRequest) -> SubmitResponse:
    try:
        parsed = _resolve_and_parse(body.url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = db.find_by_platform_video(parsed.platform.value, parsed.video_id)
    if existing:
        cached = existing["status"] == "done"
        return SubmitResponse(cached=cached, task=_task_detail(existing))

    try:
        task = db.create_task(
            video_url=parsed.canonical_url,
            platform=parsed.platform.value,
            video_id=parsed.video_id,
        )
    except Exception:
        # 并发重复提交时可能触发 UNIQUE 约束
        existing = db.find_by_platform_video(parsed.platform.value, parsed.video_id)
        if existing:
            cached = existing["status"] == "done"
            return SubmitResponse(cached=cached, task=_task_detail(existing))
        raise

    return SubmitResponse(cached=False, task=_task_detail(task))


@app.get("/api/task/{task_id}", response_model=TaskDetail)
async def get_task(task_id: int) -> TaskDetail:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_detail(task)


@app.get("/api/history", response_model=HistoryResponse)
async def history(limit: int = 50, offset: int = 0) -> HistoryResponse:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    items = db.list_history(limit=limit, offset=offset)
    return HistoryResponse(
        items=[_task_summary(row) for row in items],
        total=len(items),
    )
