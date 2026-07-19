# vid2text

从视频网站 URL 提取文字内容，支持 **CLI** 与 **Web** 两种使用方式。

**提取优先级：平台字幕 > 标题/描述 > SenseVoice 语音转录（STT，默认）**

| 平台 | 获取方式 | 字幕接口 |
|------|----------|----------|
| **抖音** | Playwright 抓 `aweme/detail` API | `desc`/`caption`；部分视频有 `video_text` / `video.subtitle` |
| **B站** | yt-dlp + B站 player API | `x/player/v2` → CC 字幕 JSON |
| **YouTube** | yt-dlp | 手动/自动字幕 → timedtext API |
| **其他** | yt-dlp 通用 fallback | 取决于站点 |

---

## 依赖

- Python 3.10+
- **ffmpeg**（PATH）
- **yt-dlp**（YouTube / B站 / 通用站点）
- **Playwright Chromium**（仅抖音）

```bash
pip install -r requirements.txt
python -m playwright install chromium   # 抖音需要
```

---

## Web 应用（推荐）

### 启动

```bash
python run_web.py
```

浏览器打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)

或使用 uvicorn：

```bash
uvicorn web.app:app --host 127.0.0.1 --port 8000
```

### API

对外接口见 [`/api-docs`](/api-docs) 或 `GET /api/v1/docs.md`。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/subtitles` | 获取视频字幕；200=就绪，202=处理中 |
| `GET` | `/api/v1/subtitles/{id}` | 轮询字幕结果 |
| `GET` | `/api/v1/subtitles?url=...` | 按 URL 查询 |
| `GET` | `/api/v1/subtitles/{id}/text` | 纯文本字幕 |

### 架构

```
用户浏览器 / API 客户端
    │
    ▼
FastAPI (web/app.py)
    ├── SQLite (data/vid2text.db)
    ├── step_scheduler (web/step_scheduler.py) — 按资源池并发调度 pipeline 步骤
    │       ├── download 池 — 视频下载
    │       ├── stt 池 — 语音转录
    │       ├── correct 池 — LLM 后处理
    │       └── default 池 — parse / fetch_meta / fetch_subtitle 等
    ├── monitor_scanner (web/monitor_scanner.py) — 账号监控后台扫描线程
    │       └── Monitors API (`/api/v1/monitors`，见 web/api_monitors.py)
    └── douyin_to_text/pipeline.py
            ├── url_parser / video_fetcher / yt_dlp_fetcher
            ├── stt_engine.py      ← STT 引擎可切换
            └── postprocess.py       ← 转录后处理（预留）
```

`GET /health` 返回服务、数据库与调度器状态（无需鉴权）。

### 环境变量

| 变量 | 说明 |
|------|------|
| `ADMIN_PASSWORD` | Web 管理页登录密码（`/monitors`、`/settings`） |
| `ADMIN_API_TOKEN` | Monitors / Settings API 鉴权（`Authorization: Bearer …` 或 `X-Admin-Token`） |
| `PUBLIC_API_TOKEN` | （可选）字幕 API 鉴权；设置后 `POST/GET /api/v1/subtitles*` 需 `Bearer` 或 `X-Api-Token` |
| `WORK_CACHE_QUOTA_GB` | work 缓存目录磁盘配额（GB），超限按最旧文件清理 |
| `STEP_CONCURRENCY_JSON` | 资源池并发 JSON，如 `{"download":1,"stt":1,"correct":1,"default":1}`；也可用 `STEP_<POOL>_CONCURRENCY` 单独设置 |

详见 `.env.example`。

### 数据表 `tasks`

| 字段 | 说明 |
|------|------|
| `video_url` | 规范化视频 URL |
| `platform` | douyin / bilibili / youtube / generic |
| `video_id` | 平台视频 ID |
| `title` | 标题 |
| `description` | 描述 |
| `raw_transcript` | 原始转录/字幕 |
| `corrected_transcript` | 后处理修正文本 |
| `status` | pending / processing / done / failed |

---

## CLI 用法

```bash
# YouTube — 拉取平台字幕
python -m douyin_to_text.cli "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --no-stt -o out.txt

# B站 — 仅描述
python -m douyin_to_text.cli "https://www.bilibili.com/video/BV1GJ411x7h7" --desc-only

# 抖音 — 描述（快速）
python -m douyin_to_text.cli "https://www.douyin.com/jingxuan?modal_id=7639590279997132072" --desc-only

# 指定 STT 引擎（默认 sensevoice，中文准确率更高）
python -m douyin_to_text.cli "URL" -o out.txt

# 回退 faster-whisper（输出自带标点）
python -m douyin_to_text.cli "URL" --stt-engine faster-whisper --whisper-model small
```

---

## 项目结构

```
vid2text/
├── douyin_to_text/          # 核心提取模块
│   ├── url_parser.py        # 平台检测 + URL 解析
│   ├── video_fetcher.py     # 抖音 Playwright
│   ├── yt_dlp_fetcher.py    # YouTube/B站/通用
│   ├── subtitle_parser.py   # 字幕解析
│   ├── stt_engine.py        # STT 引擎（默认 sensevoice，可切换 faster-whisper）
│   ├── postprocess.py       # 转录后处理（预留 correct_transcript）
│   ├── pipeline.py          # Web/Worker 复用 pipeline
│   └── cli.py               # CLI 入口
├── web/                     # Web 应用
│   ├── app.py               # FastAPI 路由 + lifespan
│   ├── db.py                # SQLite
│   ├── worker.py            # 启动/停止 step_scheduler
│   ├── step_scheduler.py    # 按资源池并发调度 pipeline 步骤
│   ├── monitor_scanner.py   # 账号监控后台扫描
│   ├── api_monitors.py      # Monitors / Settings API
│   ├── api_v1.py            # 字幕 REST API
│   ├── schemas.py           # Pydantic 模型
│   ├── templates/index.html
│   └── static/
├── data/                    # 运行时生成（gitignore）
├── run_web.py               # Web 启动脚本
├── requirements.txt
└── README.md
```

---

## 协作接口（预留）

### 转录后处理

```python
# douyin_to_text/postprocess.py
def correct_transcript(title: str, description: str, raw_transcript: str, **kwargs) -> str: ...
```

### STT 引擎

```python
# douyin_to_text/stt_engine.py
transcribe(audio_path, engine="whisper"|"faster-whisper", language="zh", model="base")
```

---

## 已知限制

1. **抖音** — 依赖 Playwright；纯 HTTP / yt-dlp 无 Cookie 不可用
2. **B站字幕** — 多数 CC/AI 字幕需 Cookie 登录态
3. **YouTube** — 部分语言字幕可能 429；可换语言或稍后重试
4. **STT 耗时** — 长视频 CPU 转录较慢；有字幕时优先走字幕接口
5. **并发调度** — `step_scheduler` 按资源池限流（默认各池并发 1）；高负载可调 `STEP_CONCURRENCY_JSON` 或换外部队列

---

## 仓库

https://github.com/cnwinds/vid2text.git
