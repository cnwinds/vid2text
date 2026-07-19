# 改造清单进度

分支：`refactor/roadmap-phase0-2`

## 已完成

### 阶段 0 — 基线
- [x] **0.1** README 架构与环境变量
- [x] **0.2** `GET /health`
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

### 阶段 3 — 可维护性（部分）
- [x] **3.1** 拆出 `web/db_connection.py`（连接/WAL）
- [x] **3.2** 拆出 `web/db_migrations.py`
- [ ] **3.3** 拆分 `pipeline_steps.py` / `author_feed.py`（体量大，留后续 PR）

### 阶段 4 — 性能与体验
- [x] **4.1** YouTube 扫描 flat + lazy enrich
- [x] **4.3** 详情弹窗显示评论数

### 阶段 5 — 测试与观测
- [x] **5.1** API 冒烟测试（`tests/test_api_v1.py`）
- [x] **5.2** client_scope 单测
- [x] **5.3** `GET /metrics` Prometheus 文本指标

## 后续可选

- [ ] 完整拆分 `db.py` / `pipeline_steps.py` / `author_feed.py`
- [ ] YouTube enrich 并发上限与限流
- [ ] 结构化 JSON 日志
