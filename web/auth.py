"""管理区鉴权：UI 登录（ADMIN_PASSWORD）与 API Token（ADMIN_API_TOKEN）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)
_admin_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)

SESSION_COOKIE = "vid2text_admin"
SESSION_TTL_SEC = 7 * 24 * 3600


def admin_api_token() -> str:
    return (os.environ.get("ADMIN_API_TOKEN") or "").strip()


def admin_password() -> str:
    return (os.environ.get("ADMIN_PASSWORD") or "").strip()


def _session_secret() -> bytes:
    raw = admin_api_token() or admin_password()
    if not raw:
        raise RuntimeError(
            "未配置 ADMIN_API_TOKEN 或 ADMIN_PASSWORD，无法签发管理 Session"
        )
    return hashlib.sha256(raw.encode()).digest()


def _sign(payload_b64: str) -> str:
    return hmac.new(_session_secret(), payload_b64.encode(), hashlib.sha256).hexdigest()


def issue_session_cookie_value() -> str:
    exp = int(time.time()) + SESSION_TTL_SEC
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}, separators=(",", ":")).encode()
    ).decode()
    return f"{payload_b64}.{_sign(payload_b64)}"


def session_is_valid(raw: str | None) -> bool:
    if not raw or "." not in raw:
        return False
    payload_b64, sig = raw.rsplit(".", 1)
    if not secrets.compare_digest(_sign(payload_b64), sig):
        return False
    try:
        pad = "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
        return int(data.get("exp") or 0) > int(time.time())
    except (json.JSONDecodeError, ValueError, TypeError):
        return False


def verify_admin_password(password: str) -> bool:
    expected = admin_password()
    if not expected:
        return False
    return secrets.compare_digest(password or "", expected)


def admin_password_configured() -> bool:
    return bool(admin_password())


def safe_admin_next(path: str | None) -> str:
    p = (path or "").strip()
    if p in ("/monitors", "/settings"):
        return p
    return "/monitors"


def login_redirect_url(next_path: str | None = None, *, error: bool = False) -> str:
    n = safe_admin_next(next_path)
    q = f"next={quote(n)}"
    if error:
        q += "&error=1"
    return f"/login?{q}"


def require_admin_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    header_token: str | None = Depends(_admin_header),
) -> None:
    """API：有效登录 Session Cookie，或 Bearer / X-Admin-Token。"""
    if session_is_valid(request.cookies.get(SESSION_COOKIE)):
        return

    expected = admin_api_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="未配置 ADMIN_API_TOKEN，请在 .env 中设置后重启服务",
        )

    provided = ""
    if creds and creds.scheme.lower() == "bearer":
        provided = creds.credentials or ""
    elif header_token:
        provided = header_token

    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="无效或缺少 Admin API Token")

