"""公开字幕 API 鉴权（PUBLIC_API_TOKEN）。"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)
_api_header = APIKeyHeader(name="X-Api-Token", auto_error=False)


def public_api_token() -> str:
    return (os.environ.get("PUBLIC_API_TOKEN") or "").strip()


def require_public_api_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    header_token: str | None = Depends(_api_header),
) -> None:
    """未配置 PUBLIC_API_TOKEN 时放行（本地开发兼容）；配置后要求 Bearer 或 X-Api-Token。"""
    expected = public_api_token()
    if not expected:
        return

    provided = ""
    if creds and creds.scheme.lower() == "bearer":
        provided = creds.credentials or ""
    elif header_token:
        provided = header_token

    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="无效或缺少 API Token")
