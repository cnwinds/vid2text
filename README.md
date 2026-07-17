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

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/submit` | 提交 URL，body: `{"url": "..."}`；命中已有记录则直接返回 |
| `GET` | `/api/task/{id}` | 查询任务状态与转录结果 |
| `GET` | `/api/history` | 历史记录列表 |

### 架构

```
用户浏览器
    │
    ▼
FastAPI (web/app.py)
    ├── SQLite (data/vid2text.db)
    └── 后台 Worker 线程 (web/worker.py)
            └── douyin_to_text/pipeline.py
                    ├── url_parser / video_fetcher / yt_dlp_fetcher
                    ├── stt_engine.py      ← STT 引擎可切换
                    └── postprocess.py       ← 转录后处理（预留）
```

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
│   ├── app.py               # FastAPI 路由
│   ├── db.py                # SQLite
│   ├── worker.py            # 后台任务
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
5. **Web Worker** — 单线程轮询，适合本地 MVP；生产环境可换 Celery/RQ

---

## 仓库

https://github.com/cnwinds/vid2text.git
