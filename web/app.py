"""FastAPI Web 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web import db
from web.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SEC,
    admin_password_configured,
    issue_session_cookie_value,
    login_redirect_url,
    safe_admin_next,
    session_is_valid,
    verify_admin_password,
)
from web.api_monitors import router as monitors_router
from web.api_v1 import router as v1_router
from web.monitor_scanner import start_monitor_scanner, stop_monitor_scanner
from web.metrics import metrics_text
from web.step_scheduler import is_scheduler_running
from web.logging_config import configure_logging
from web.worker import start_worker, stop_worker

configure_logging()
logger = logging.getLogger(__name__)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    start_worker()
    start_monitor_scanner()
    yield
    stop_worker()
    stop_monitor_scanner()


app = FastAPI(
    title="vid2text",
    description="视频 URL 转文字 · 获取字幕见 /api/v1/subtitles · 账号监控见 /api/v1/monitors",
    version="0.5.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(v1_router)
app.include_router(monitors_router)


@app.get("/health")
async def health() -> JSONResponse:
    db_status = "ok"
    try:
        db.count_tasks()
    except Exception:
        logger.exception("health: db check failed")
        db_status = "error"

    scheduler_status = "running" if is_scheduler_running() else "stopped"
    all_ok = db_status == "ok" and scheduler_status == "running"
    body = {
        "status": "ok" if all_ok else "degraded",
        "db": db_status,
        "scheduler": scheduler_status,
    }
    return JSONResponse(status_code=200 if all_ok else 503, content=body)


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/login", response_class=HTMLResponse, response_model=None)
async def login_page(
    request: Request,
    next: str = "/monitors",
    error: int = 0,
):
    if session_is_valid(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse(safe_admin_next(next), status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next_path": safe_admin_next(next),
            "error": bool(error),
            "configured": admin_password_configured(),
        },
    )


@app.post("/login")
async def login_submit(
    password: str = Form(...),
    next: str = Form("/monitors"),
) -> RedirectResponse:
    if not admin_password_configured():
        return RedirectResponse("/login?error=1", status_code=303)
    dest = safe_admin_next(next)
    if not verify_admin_password(password):
        return RedirectResponse(login_redirect_url(dest, error=True), status_code=303)
    response = RedirectResponse(dest, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session_cookie_value(),
        max_age=SESSION_TTL_SEC,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/monitors", response_class=HTMLResponse, response_model=None)
async def monitors_page(request: Request):
    if not session_is_valid(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse(login_redirect_url("/monitors"), status_code=303)
    return templates.TemplateResponse(request, "monitors.html")


@app.get("/settings", response_class=HTMLResponse, response_model=None)
async def settings_page(request: Request):
    if not session_is_valid(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse(login_redirect_url("/settings"), status_code=303)
    return templates.TemplateResponse(request, "settings.html")


@app.get("/api-docs", response_class=HTMLResponse)
async def api_docs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "api-docs.html")
