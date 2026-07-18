"""OpenAPI / schema.json / docs.md 共用的响应说明与示例。"""

from __future__ import annotations

import json
from typing import Any

from web.schemas import (
    ErrorResponse,
    RateLimitResponse,
    SubtitleListResponse,
    SubtitleResponse,
)

# ---- 响应示例（单一数据源） ----

VIDEO_EXAMPLE = {
    "url": "https://www.douyin.com/video/7652629874158406931",
    "platform": "douyin",
    "video_id": "7652629874158406931",
    "title": "示例标题",
    "description": "视频描述",
}

VIDEO_EXAMPLE_BRIEF = {
    "url": "https://www.douyin.com/video/7652629874158406931",
    "platform": "douyin",
    "video_id": "7652629874158406931",
    "title": "",
    "description": "",
}


def subtitle_ready_example(*, task_id: int = 1, cached: bool = False) -> dict[str, Any]:
    return {
        "ready": True,
        "cached": cached,
        "id": task_id,
        "video": VIDEO_EXAMPLE if cached else {**VIDEO_EXAMPLE_BRIEF, "title": VIDEO_EXAMPLE["title"]},
        "subtitle": {
            "text": "修正后的正文（推荐使用）",
            "raw": "原始转录",
            "corrected": "LLM 修正文本",
        },
        "processing": None,
        "error": None,
        "retry_url": None,
        "progress_metrics": {},
    }


def subtitle_processing_example(base: str, *, task_id: int = 3) -> dict[str, Any]:
    poll = f"{base.rstrip('/')}/api/v1/subtitles/{task_id}"
    return {
        "ready": False,
        "cached": False,
        "id": task_id,
        "video": VIDEO_EXAMPLE_BRIEF,
        "subtitle": None,
        "processing": {
            "status": "processing",
            "step": "download",
            "poll_url": poll,
            "retry_after": 2.0,
            "message": "正在提取字幕，请稍后再次请求",
        },
        "error": None,
        "retry_url": None,
        "progress_metrics": {"kind": "network", "detail": "2.1 MB/s · 45%"},
    }


def subtitle_failed_example(base: str, *, task_id: int = 1) -> dict[str, Any]:
    prefix = base.rstrip("/")
    return {
        "ready": False,
        "cached": False,
        "id": task_id,
        "video": VIDEO_EXAMPLE_BRIEF,
        "subtitle": None,
        "processing": None,
        "error": "视频下载失败：CDN 返回了 HTML 错误页",
        "retry_url": f"{prefix}/api/v1/subtitles/{task_id}/retry",
        "progress_metrics": {},
    }


def error_example(detail: str) -> dict[str, str]:
    return {"detail": detail}


def rate_limit_example(base: str, *, active_id: int = 3) -> dict[str, Any]:
    prefix = base.rstrip("/")
    return {
        "detail": "当前 IP 已有进行中的提取任务，请等待完成后再提交新视频",
        "code": "rate_limit_active_task",
        "active_id": active_id,
        "poll_url": f"{prefix}/api/v1/subtitles/{active_id}",
    }


def list_example() -> dict[str, Any]:
    return {
        "items": [
            {
                "ready": True,
                "cached": True,
                "id": 2,
                "video": {
                    "url": "https://www.bilibili.com/video/BV1xx",
                    "platform": "bilibili",
                    "video_id": "BV1xx",
                    "title": "示例",
                    "description": "",
                },
                "subtitle": {"text": "字幕正文", "raw": "原始", "corrected": "修正"},
                "processing": None,
                "error": None,
                "retry_url": None,
                "progress_metrics": {},
            }
        ],
        "pagination": {
            "limit": 20,
            "offset": 0,
            "total": 42,
            "has_more": True,
            "next_offset": 20,
        },
    }


def _json_block(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_docs_markdown(base: str) -> str:
    """生成按接口分节的 Markdown 文档，每节单独描述返回值。"""
    b = base.rstrip("/")
    ex_200 = subtitle_ready_example()
    ex_202 = subtitle_processing_example(b)
    ex_422 = subtitle_failed_example(b)
    ex_429 = rate_limit_example(b)
    ex_list = list_example()

    return f"""# vid2text API v1

> **给一个视频链接，拿回字幕。**

- **Base URL:** `{b}`
- **OpenAPI:** `{b}/openapi.json`
- **schema（含响应模型）:** `{b}/api/v1/schema.json`

---

## POST /api/v1/subtitles

提交视频 URL，获取字幕（主入口）。

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 视频页面 URL |
| `wait` | boolean | 否 | 为 `true` 时阻塞等待完成（默认 `false`） |
| `timeout` | integer | 否 | `wait=true` 时最长等待秒数（5–900，默认 300） |
| `poll_interval` | number | 否 | `wait=true` 时轮询间隔秒（0.5–10，默认 2） |

```json
{{"url": "https://www.douyin.com/video/7652629874158406931", "wait": false}}
```

### 返回值

#### 200 — 字幕已就绪

`ready: true`，读取 `subtitle.text`。

```json
{_json_block(ex_200)}
```

#### 202 — 处理中

`ready: false`。响应含 **`id`**（任务编号）与 **`processing.poll_url`**（轮询地址；路径末尾数字即 `id`）。

```json
{_json_block(ex_202)}
```

#### 422 — 提取失败

`ready: false`，查看 `error`；可 POST `retry_url` 重试。

```json
{_json_block(ex_422)}
```

#### 400 — 请求参数错误

```json
{_json_block(error_example("无法解析视频 URL"))}
```

#### 408 — wait 模式超时

`wait=true` 时在 `timeout` 内未完成，改轮询 `GET /api/v1/subtitles/{{id}}`。

```json
{_json_block(error_example("等待超时（300s），请稍后 GET /api/v1/subtitles/3 继续获取"))}
```

#### 429 — IP 限流

当前 IP 已有 `pending` / `processing` 任务，需等待完成后再提交。

```json
{_json_block(ex_429)}
```

---

## GET /api/v1/subtitles/{{id}}

按任务编号轮询字幕结果。`id` 来自 POST 响应的 `id` 或 `processing.poll_url` 末尾数字。

### 请求

路径参数 `id`：整数任务编号。

```bash
curl -s "{b}/api/v1/subtitles/3"
```

### 返回值

#### 200 — 字幕已就绪

响应体同 POST，结构为 `SubtitleResponse`。

```json
{_json_block(ex_200)}
```

#### 202 — 仍在处理

```json
{_json_block(ex_202)}
```

#### 422 — 已失败

```json
{_json_block(ex_422)}
```

#### 404 — 任务不存在

```json
{_json_block(error_example("请求不存在"))}
```

---

## GET /api/v1/subtitles

两种模式，由是否传 `url` 区分。

### 模式 A：历史列表（无 `url`）

#### Query 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `limit` | 20 | 每页 1–100 条 |
| `offset` | 0 | 偏移；下一页用 `pagination.next_offset` |

#### 返回值

##### 200 — 分页列表

```json
{_json_block(ex_list)}
```

### 模式 B：按 URL 查询（有 `url`）

返回**单条** `SubtitleResponse`（不是列表）。

```bash
curl -s "{b}/api/v1/subtitles?url=https://www.bilibili.com/video/BV1xx411c7mD"
```

#### 返回值

##### 200 / 202 / 422 — 同 GET /api/v1/subtitles/{{id}}

##### 404 — 该视频尚无记录

```json
{_json_block(error_example("该视频尚无提取记录，请先 POST /api/v1/subtitles"))}
```

---

## GET /api/v1/subtitles/{{id}}/text

返回纯文本字幕（`text/plain`），适合脚本直接落盘。

### Query 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `field` | `text` | `text`（修正后优先） / `raw` / `corrected` |

### 返回值

#### 200 — 字幕正文

- **Content-Type:** `text/plain`
- **Body:** 纯文本，无 JSON 包装

```
这是字幕正文…
```

#### 202 — 仍在处理

- **Content-Type:** `application/json`
- **Body:** 同 GET /api/v1/subtitles/{{id}} 的 202

```json
{_json_block(ex_202)}
```

#### 422 — 已失败

- **Content-Type:** `application/json`

```json
{_json_block(ex_422)}
```

#### 404 — 无记录或无文本

```json
{_json_block(error_example("请求不存在"))}
```

---

## POST /api/v1/subtitles/{{id}}/retry

将**失败**记录重新排队提取。

### 返回值

#### 202 — 已重新排队

响应体同处理中状态（含 `id`、`processing.poll_url`）。

```json
{_json_block(ex_202)}
```

#### 400 — 不可重试

非 `failed` 状态（如已在处理中且非本接口允许的重复提交场景）。

```json
{_json_block(error_example("仅失败记录可重试"))}
```

#### 404 — 任务不存在

```json
{_json_block(error_example("请求不存在"))}
```

#### 429 — IP 限流

当前 IP 已有其他进行中的任务。

```json
{_json_block(ex_429)}
```

---

## 公共模型 SubtitleResponse

| 字段 | 何时出现 | 说明 |
|------|----------|------|
| `id` | 始终 | 服务端任务编号，用于 `GET /subtitles/{{id}}` |
| `ready` | 始终 | `true` 表示字幕已就绪 |
| `cached` | 始终 | 是否直接返回了已有结果 |
| `video` | 始终 | 视频元信息（url / platform / video_id 等） |
| `subtitle.text` | `ready: true` | 推荐使用的字幕正文 |
| `processing.poll_url` | 处理中 | 轮询地址，末尾数字即 `id` |
| `processing.retry_after` | 处理中 | 建议轮询间隔（秒） |
| `error` / `retry_url` | 失败 | 失败原因与重试地址 |
| `progress_metrics` | 处理中 | 资源指标（供 Web 进度展示） |

---

## 账号监控

### POST /api/v1/monitors

粘贴作品或主页链接，解析作者并创建监控。`backfill_mode=recent|all`，`backfill_n` 为最近条数。

### GET /api/v1/monitors

分页列出监控。另有 `GET/PATCH/DELETE /api/v1/monitors/{{id}}`、`POST …/scan`、`GET …/videos`。

### GET/PUT /api/v1/settings

配置各平台 Cookie（写入不回显）、Webhook URL/密钥、默认扫描间隔。监控任务完成或失败时，若配置了 `webhook_url` 会 POST JSON 通知。
"""


# ---- OpenAPI responses（引用上方示例） ----

SUBTITLE_RESPONSES: dict[int | str, dict] = {
    200: {
        "description": "字幕已就绪（`ready: true`），可直接读取 `subtitle.text`",
        "model": SubtitleResponse,
        "content": {"application/json": {"example": subtitle_ready_example()}},
    },
    202: {
        "description": (
            "正在提取字幕（`ready: false`）。响应含 `id` 与 `processing.poll_url`"
        ),
        "model": SubtitleResponse,
        "content": {
            "application/json": {
                "example": subtitle_processing_example("https://example.com")
            }
        },
    },
    422: {
        "description": "提取失败（`ready: false`），查看 `error`，可 POST `retry_url` 重试",
        "model": SubtitleResponse,
        "content": {
            "application/json": {
                "example": subtitle_failed_example("https://example.com")
            }
        },
    },
}

ERROR_RESPONSES: dict[int | str, dict] = {
    400: {
        "description": "请求参数无效（如 URL 无法解析）",
        "model": ErrorResponse,
        "content": {
            "application/json": {"example": error_example("无法解析视频 URL")}
        },
    },
    404: {
        "description": "记录不存在",
        "model": ErrorResponse,
        "content": {"application/json": {"example": error_example("请求不存在")}},
    },
    408: {
        "description": "wait=true 阻塞等待超时，请改轮询 poll_url",
        "model": ErrorResponse,
        "content": {
            "application/json": {
                "example": error_example(
                    "等待超时（300s），请稍后 GET /api/v1/subtitles/1 继续获取"
                )
            }
        },
    },
    429: {
        "description": "限流：当前 IP 已有 pending/processing 任务，需等待完成后再提交新视频",
        "model": RateLimitResponse,
        "content": {
            "application/json": {
                "example": rate_limit_example("https://example.com")
            }
        },
    },
}

LIST_RESPONSES: dict[int | str, dict] = {
    200: {
        "description": "分页历史列表，见 `pagination` 字段翻页",
        "model": SubtitleListResponse,
        "content": {"application/json": {"example": list_example()}},
    },
}

TEXT_RESPONSES: dict[int | str, dict] = {
    200: {
        "description": "纯文本字幕（text/plain），字段由 query `field` 决定",
        "content": {"text/plain": {"example": "这是字幕正文…"}},
    },
    202: {
        "description": "仍在提取，响应体为 JSON（与 GET /subtitles/{id} 相同）",
        "model": SubtitleResponse,
    },
    422: {
        "description": "提取失败，响应体为 JSON",
        "model": SubtitleResponse,
    },
    404: ERROR_RESPONSES[404],
}

POST_SUBTITLE_RESPONSES = {
    **SUBTITLE_RESPONSES,
    400: ERROR_RESPONSES[400],
    408: ERROR_RESPONSES[408],
    429: ERROR_RESPONSES[429],
}

GET_SUBTITLE_RESPONSES = {
    **SUBTITLE_RESPONSES,
    404: ERROR_RESPONSES[404],
}

GET_LIST_RESPONSES = {
    **LIST_RESPONSES,
    **SUBTITLE_RESPONSES,
    404: ERROR_RESPONSES[404],
}

RETRY_RESPONSES = {
    **SUBTITLE_RESPONSES,
    400: ERROR_RESPONSES[400],
    404: ERROR_RESPONSES[404],
    429: ERROR_RESPONSES[429],
}


def build_endpoint_specs(base: str) -> list[dict[str, Any]]:
    """供 Web 文档页渲染：每个接口含说明、请求、逐状态码返回值。"""
    b = base.rstrip("/")
    sample_url = "https://www.douyin.com/video/7652629874158406931"
    bilibili_url = "https://www.bilibili.com/video/BV1xx411c7mD"

    def resp(
        status: int,
        title: str,
        description: str,
        example: Any,
        *,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        return {
            "status": status,
            "title": title,
            "description": description,
            "content_type": content_type,
            "example": example,
        }

    return [
        {
            "id": "post-subtitles",
            "method": "POST",
            "path": "/api/v1/subtitles",
            "summary": "获取视频字幕",
            "description": "提交视频 URL，获取字幕（主入口）。200 立即返回；202 需轮询 processing.poll_url。",
            "primary": True,
            "curl": (
                f"curl -i -s -X POST {b}/api/v1/subtitles \\\n"
                f'  -H "Content-Type: application/json" \\\n'
                f'  -d \'{{"url":"{sample_url}"}}\''
            ),
            "request_body": [
                {"name": "url", "type": "string", "required": True, "description": "视频页面 URL"},
                {
                    "name": "wait",
                    "type": "boolean",
                    "required": False,
                    "description": "为 true 时阻塞等待完成（默认 false）",
                },
                {
                    "name": "timeout",
                    "type": "integer",
                    "required": False,
                    "description": "wait=true 时最长等待秒数（5–900，默认 300）",
                },
                {
                    "name": "poll_interval",
                    "type": "number",
                    "required": False,
                    "description": "wait=true 时轮询间隔秒（0.5–10，默认 2）",
                },
            ],
            "request_example": {"url": sample_url, "wait": False},
            "responses": [
                resp(
                    200,
                    "字幕已就绪",
                    "ready: true，读取 subtitle.text。",
                    subtitle_ready_example(),
                ),
                resp(
                    202,
                    "处理中",
                    "ready: false。响应含 id（任务编号）与 processing.poll_url（路径末尾数字即 id）。",
                    subtitle_processing_example(b),
                ),
                resp(
                    422,
                    "提取失败",
                    "ready: false，查看 error；可 POST retry_url 重试。",
                    subtitle_failed_example(b),
                ),
                resp(400, "请求参数错误", "如 URL 无法解析。", error_example("无法解析视频 URL")),
                resp(
                    408,
                    "wait 模式超时",
                    "wait=true 时在 timeout 内未完成，改轮询 GET /api/v1/subtitles/{id}。",
                    error_example("等待超时（300s），请稍后 GET /api/v1/subtitles/3 继续获取"),
                ),
                resp(
                    429,
                    "IP 限流",
                    "当前 IP 已有 pending/processing 任务，需等待完成后再提交。",
                    rate_limit_example(b),
                ),
            ],
        },
        {
            "id": "get-subtitles-id",
            "method": "GET",
            "path": "/api/v1/subtitles/{id}",
            "summary": "继续获取字幕",
            "description": "按任务编号轮询字幕结果。id 来自 POST 响应的 id 或 processing.poll_url 末尾数字。",
            "curl": f"curl -i -s {b}/api/v1/subtitles/3",
            "request_path": [
                {"name": "id", "type": "integer", "required": True, "description": "任务编号"},
            ],
            "responses": [
                resp(200, "字幕已就绪", "响应体同 POST，结构为 SubtitleResponse。", subtitle_ready_example()),
                resp(202, "仍在处理", "继续轮询直至 ready: true。", subtitle_processing_example(b)),
                resp(422, "已失败", "查看 error，可 POST retry_url。", subtitle_failed_example(b)),
                resp(404, "任务不存在", "该 id 无记录。", error_example("请求不存在")),
            ],
        },
        {
            "id": "get-subtitles-list",
            "method": "GET",
            "path": "/api/v1/subtitles",
            "summary": "历史列表（分页）",
            "description": "无 url 参数时返回分页历史；items 中每条为 SubtitleResponse。",
            "curl": f"curl -i -s '{b}/api/v1/subtitles?limit=20&offset=0'",
            "request_query": [
                {"name": "limit", "type": "integer", "required": False, "description": "每页 1–100 条，默认 20"},
                {"name": "offset", "type": "integer", "required": False, "description": "偏移量，默认 0；下一页用 pagination.next_offset"},
            ],
            "responses": [
                resp(200, "分页列表", "返回 items 数组与 pagination 分页信息。", list_example()),
            ],
        },
        {
            "id": "get-subtitles-url",
            "method": "GET",
            "path": "/api/v1/subtitles?url=...",
            "summary": "按 URL 查询单条",
            "description": "传 url 时返回单条 SubtitleResponse（不是列表），无需记住 id。",
            "curl": f'curl -i -s "{b}/api/v1/subtitles?url={bilibili_url}"',
            "request_query": [
                {"name": "url", "type": "string", "required": True, "description": "视频页面 URL"},
            ],
            "responses": [
                resp(200, "字幕已就绪", "该视频曾处理完成。", subtitle_ready_example(cached=True)),
                resp(202, "处理中", "该视频正在提取。", subtitle_processing_example(b)),
                resp(422, "已失败", "该视频提取失败。", subtitle_failed_example(b)),
                resp(
                    404,
                    "无记录",
                    "该视频尚无提取记录，需先 POST /api/v1/subtitles。",
                    error_example("该视频尚无提取记录，请先 POST /api/v1/subtitles"),
                ),
            ],
        },
        {
            "id": "get-subtitles-text",
            "method": "GET",
            "path": "/api/v1/subtitles/{id}/text",
            "summary": "纯文本字幕",
            "description": "返回 text/plain 正文，适合脚本直接落盘。处理中/失败时仍返回 JSON。",
            "curl": f"curl -i -s {b}/api/v1/subtitles/3/text",
            "request_path": [
                {"name": "id", "type": "integer", "required": True, "description": "任务编号"},
            ],
            "request_query": [
                {
                    "name": "field",
                    "type": "string",
                    "required": False,
                    "description": "text（默认，修正后优先）| raw | corrected",
                },
            ],
            "responses": [
                resp(
                    200,
                    "字幕正文",
                    "Content-Type: text/plain，Body 为纯文本。",
                    "这是字幕正文…",
                    content_type="text/plain",
                ),
                resp(
                    202,
                    "仍在处理",
                    "Content-Type: application/json，Body 同 GET /api/v1/subtitles/{id} 的 202。",
                    subtitle_processing_example(b),
                ),
                resp(422, "已失败", "Content-Type: application/json。", subtitle_failed_example(b)),
                resp(404, "无记录或无文本", "任务不存在或尚无字幕内容。", error_example("请求不存在")),
            ],
        },
        {
            "id": "post-subtitles-retry",
            "method": "POST",
            "path": "/api/v1/subtitles/{id}/retry",
            "summary": "重新提取",
            "description": "将 failed 状态的记录重新排队提取。",
            "curl": f"curl -i -s -X POST {b}/api/v1/subtitles/3/retry",
            "request_path": [
                {"name": "id", "type": "integer", "required": True, "description": "失败任务编号"},
            ],
            "responses": [
                resp(202, "已重新排队", "响应体同处理中状态，含 id 与 processing.poll_url。", subtitle_processing_example(b)),
                resp(400, "不可重试", "非 failed 状态。", error_example("仅失败记录可重试")),
                resp(404, "任务不存在", "该 id 无记录。", error_example("请求不存在")),
                resp(429, "IP 限流", "当前 IP 已有其他进行中的任务。", rate_limit_example(b)),
            ],
        },
        {
            "id": "post-monitors",
            "method": "POST",
            "path": "/api/v1/monitors",
            "summary": "添加账号监控",
            "description": "粘贴作品或主页链接，解析作者并开始监控；可选补采最近 N 条或可见全量。",
            "curl": (
                f"curl -i -s -X POST {b}/api/v1/monitors "
                f"-H 'Content-Type: application/json' "
                f"-d '{{\"url\":\"{sample_url}\",\"backfill_mode\":\"recent\",\"backfill_n\":10}}'"
            ),
            "request_body": [
                {"name": "url", "type": "string", "required": True, "description": "作品或主页/频道 URL"},
                {"name": "backfill_mode", "type": "string", "required": False, "description": "recent | all"},
                {"name": "backfill_n", "type": "integer", "required": False, "description": "recent 时条数，默认 10"},
                {"name": "scan_interval_sec", "type": "integer", "required": False, "description": "扫描间隔秒"},
            ],
            "request_example": {
                "url": sample_url,
                "backfill_mode": "recent",
                "backfill_n": 10,
            },
            "responses": [
                resp(
                    201,
                    "已创建",
                    "返回监控对象；后台将按 next_scan_at 扫描并入队提取。",
                    {
                        "id": 1,
                        "platform": "douyin",
                        "author_key": "MS4wLjABAAAA…",
                        "author_name": "示例作者",
                        "backfill_mode": "recent",
                        "backfill_n": 10,
                        "backfill_status": "pending",
                        "enabled": True,
                        "scan_interval_sec": 2700,
                        "video_count": 0,
                    },
                ),
                resp(400, "解析失败", "URL 无效或无法解析作者。", error_example("解析作者失败: …")),
            ],
        },
        {
            "id": "get-monitors",
            "method": "GET",
            "path": "/api/v1/monitors",
            "summary": "监控列表",
            "description": "分页列出已添加的账号监控。",
            "curl": f"curl -s '{b}/api/v1/monitors?limit=20'",
            "request_query": [
                {"name": "limit", "type": "integer", "required": False, "description": "每页条数"},
                {"name": "offset", "type": "integer", "required": False, "description": "偏移"},
            ],
            "responses": [
                resp(200, "列表", "items + pagination。", {"items": [], "pagination": {"limit": 20, "offset": 0, "total": 0, "has_more": False, "next_offset": None}}),
            ],
        },
        {
            "id": "get-settings",
            "method": "GET",
            "path": "/api/v1/settings",
            "summary": "读取设置",
            "description": "Cookie 只返回是否已配置，不回显明文；含 Webhook 与默认扫描间隔。",
            "curl": f"curl -s {b}/api/v1/settings",
            "responses": [
                resp(
                    200,
                    "设置摘要",
                    "不含 Cookie 明文。",
                    {
                        "douyin_cookies_set": False,
                        "bilibili_cookies_set": False,
                        "youtube_cookies_set": False,
                        "webhook_url": "",
                        "webhook_secret_set": False,
                        "default_scan_interval_sec": 2700,
                    },
                ),
            ],
        },
        {
            "id": "put-settings",
            "method": "PUT",
            "path": "/api/v1/settings",
            "summary": "更新设置",
            "description": "写入各平台 Cookie、Webhook URL/密钥、默认扫描间隔。传空字符串可清除 Cookie。",
            "curl": (
                f"curl -i -s -X PUT {b}/api/v1/settings "
                f"-H 'Content-Type: application/json' "
                f"-d '{{\"webhook_url\":\"https://example.com/hook\",\"default_scan_interval_sec\":3600}}'"
            ),
            "request_body": [
                {"name": "douyin_cookies", "type": "string", "required": False, "description": "抖音 Cookie"},
                {"name": "bilibili_cookies", "type": "string", "required": False, "description": "B站 Cookie"},
                {"name": "youtube_cookies", "type": "string", "required": False, "description": "YouTube Cookie"},
                {"name": "webhook_url", "type": "string", "required": False, "description": "完成后 POST 的地址"},
                {"name": "webhook_secret", "type": "string", "required": False, "description": "可选 HMAC 密钥"},
                {"name": "default_scan_interval_sec", "type": "integer", "required": False, "description": "默认扫描间隔"},
            ],
            "responses": [
                resp(200, "已更新", "返回与 GET /settings 相同的摘要。", {"webhook_url": "https://example.com/hook", "default_scan_interval_sec": 3600}),
            ],
        },
    ]


def build_schema_dict(base: str) -> dict[str, Any]:
    """schema.json：每个端点单独列出 responses 与 response_examples。"""
    b = base.rstrip("/")
    endpoints = build_endpoint_specs(b)
    return {
        "name": "vid2text",
        "version": "1.3.0",
        "description": "从视频 URL 获取字幕；支持账号监控自动采文案。",
        "base_url": b,
        "openapi_url": f"{b}/openapi.json",
        "docs_markdown": f"{b}/api/v1/docs.md",
        "mental_model": "POST url → 字幕；POST /monitors → 盯账号新作自动提取",
        "endpoints": [
            {
                **ep,
                "responses": {str(r["status"]): r["title"] for r in ep["responses"]},
                "response_examples": {
                    str(r["status"]): r["example"] for r in ep["responses"]
                },
            }
            for ep in endpoints
        ],
        "page_endpoints": endpoints,
        "models": {
            "SubtitleResponse": SubtitleResponse.model_json_schema(),
            "SubtitleListResponse": SubtitleListResponse.model_json_schema(),
            "ErrorResponse": {"type": "object", "properties": {"detail": {"type": "string"}}},
            "RateLimitResponse": {
                "type": "object",
                "properties": {
                    "detail": {"type": "string"},
                    "code": {"type": "string", "const": "rate_limit_active_task"},
                    "active_id": {"type": "integer"},
                    "poll_url": {"type": "string"},
                },
            },
        },
    }

