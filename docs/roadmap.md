# 改造清单进度

分支：`master`（已于 2026-07-19 合并 `refactor/roadmap-phase0-2`）

## 已完成

### 阶段 0 — 基线
- [x] **0.1** README 架构与环境变量
- [x] **0.2** `GET /health` + Docker `healthcheck`
- [x] **0.3** GitHub Actions CI
- [x] **0.4** FastAPI lifespan + 优雅 shutdown

### 阶段 1 — 安全
- [x] **1.1** `PUBLIC_API_TOKEN` 保护字幕 API
- [x] **1.2** 移除 session 弱密钥回退
- [x] **1.3** SSRF 防护
- [x] **1.4** 历史列表 / 单条访问按 scope（Token/IP）隔离

### 阶段 2 — 数据一致性
- [x] **2.1** 元数据同步集中（`web/metadata_sync.py`）
- [x] **2.2** YouTube fetch_meta 续跑（`published_at`）
- [x] **2.3** SQLite WAL
- [x] **2.4** Schema 版本化（`schema_version` + `web/db_migrations.py`）

### 阶段 3 — 可维护性
- [x] **3.1** 拆出 `web/db_connection.py`（连接/WAL）
- [x] **3.2** 拆出 `web/db_migrations.py`
- [x] **3.3** 拆分大模块：
  - `pipeline_douyin_steps.py` / `pipeline_ytdlp_steps.py`
  - `author_feed_{cookies,bilibili,youtube,douyin}.py`
  - `db_{common,tasks,settings,monitors}.py`（`db.py` 为 facade）

### 阶段 4 — 性能与体验
- [x] **4.1** YouTube 扫描 flat + lazy enrich
- [x] **4.2** YouTube enrich 并发上限（`YOUTUBE_ENRICH_MAX_CONCURRENT`）+ yt-dlp 全局限流
- [x] **4.3** 详情弹窗显示评论数
- [x] **4.4** 监控 scanner 并行（`MONITOR_SCANNER_MAX_WORKERS`，默认 2）

### 阶段 5 — 测试与观测
- [x] **5.1** API 冒烟测试（`tests/test_api_v1.py`）
- [x] **5.2** client_scope 单测 + `scan_monitor` mock 测试
- [x] **5.3** `GET /metrics` Prometheus 文本指标
- [x] **5.4** 结构化 JSON 日志（`LOG_FORMAT=json`）

### 运维脚本
- [x] **ops.1** YouTube 元数据回填：`scripts/backfill_youtube_monitors.py`

## 后续可选（未纳入本轮）

- [ ] **1.2+** 未配置 ADMIN 时进程启动即失败（当前仅 session 签发时报错）
- [ ] **2.1+** 元数据权威源 ADR 文档
- [ ] **5.1+** 完整 API 集成测试（POST → 202 → mock 完成 → 200）
- [ ] **5.4+** 业务日志统一 `task_id` / `monitor_id` extra 字段
- [ ] **5.5** Prometheus histogram（扫描耗时等）
- [ ] 前端 `style.css` 拆分、E2E 测试
