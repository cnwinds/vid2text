# 改造清单进度

分支：`master`

## 已完成（阶段 0–5 + 后续可选项）

### 阶段 0 — 基线
- [x] **0.1** README 架构与环境变量
- [x] **0.2** `GET /health` + Docker `healthcheck`
- [x] **0.3** GitHub Actions CI
- [x] **0.4** FastAPI lifespan + 优雅 shutdown

### 阶段 1 — 安全
- [x] **1.1** `PUBLIC_API_TOKEN` 保护字幕 API
- [x] **1.2** 未配 ADMIN 时启动失败（测试可用 `VID2TEXT_SKIP_ADMIN_CHECK=1`）
- [x] **1.3** SSRF 防护
- [x] **1.4** 历史列表 / 单条访问按 scope（Token/IP）隔离

### 阶段 2 — 数据一致性
- [x] **2.1** 元数据同步集中 + [ADR 001](adr/001-video-metadata.md)
- [x] **2.2** YouTube fetch_meta 续跑（`published_at`）
- [x] **2.3** SQLite WAL
- [x] **2.4** Schema 版本化（`schema_version` + `web/db_migrations.py`）

### 阶段 3 — 可维护性
- [x] **3.1–3.3** db / pipeline / author_feed 模块拆分

### 阶段 4 — 性能与体验
- [x] **4.1–4.4** YouTube enrich、限流、详情评论数、scanner 并行

### 阶段 5 — 测试与观测
- [x] **5.1** API 冒烟 + 集成测试（POST→202→200）
- [x] **5.2** client_scope + scan_monitor 单测
- [x] **5.3** `/metrics` counter + histogram
- [x] **5.4** JSON 日志 + `task_id` / `monitor_id` / `step` 字段
- [x] **5.5** Prometheus histogram（扫描 / pipeline 步骤耗时）

### 前端与 E2E
- [x] **UI.1** `style.css` 拆为 `static/css/{base,api-docs,monitors,modal,history}.css`
- [x] **UI.2** Playwright E2E（`tests/e2e/test_ui_smoke.py`）

### 运维
- [x] **ops.1** `scripts/backfill_youtube_monitors.py`
