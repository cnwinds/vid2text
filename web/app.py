"""FastAPI Web 应用入口。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web import db
from web.api_monitors import router as monitors_router
from web.api_v1 import router as v1_router
from web.monitor_scanner import start_monitor_scanner
from web.worker import start_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def static_version() -> str:
    """静态资源 ?v= 参数，避免浏览器长期使用旧 JS。"""
    static_dir = BASE_DIR / "static"
    latest = 0.0
    for path in static_dir.rglob("*"):
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
    return str(int(latest)) if latest else "1"


templates.env.globals["static_v"] = static_version

app = FastAPI(
    title="vid2text",
    description="视频 URL 转文字 · 获取字幕见 /api/v1/subtitles · 账号监控见 /api/v1/monitors",
    version="0.5.0",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(v1_router)
app.include_router(monitors_router)


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    start_worker()
    start_monitor_scanner()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/monitors", response_class=HTMLResponse)
async def monitors_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "monitors.html")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html")


@app.get("/api-docs", response_class=HTMLResponse)
async def api_docs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "api-docs.html")
