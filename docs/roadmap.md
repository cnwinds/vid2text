# 改造清单进度

分支：`refactor/roadmap-phase0-2`

## 已完成（本分支）

- [x] **0.1** README 架构与环境变量更新
- [x] **0.2** `GET /health` 健康检查
- [x] **0.3** GitHub Actions CI（`.github/workflows/tests.yml`）
- [x] **0.4** FastAPI lifespan（替代 `on_event`）+ 优雅 shutdown
- [x] **1.1** `PUBLIC_API_TOKEN` 保护字幕 API（可选，未配置则本地兼容）
- [x] **1.2** 移除 session 弱密钥回退
- [x] **1.3** SSRF 防护（`douyin_to_text/url_safety.py`）
- [x] **2.2** YouTube fetch_meta 续跑条件（`published_at` 而非 `like_count>0`）
- [x] **2.3** SQLite WAL + busy_timeout
- [x] **2.1** 元数据同步集中（`web/metadata_sync.py`）

## 待办（后续 PR）

- [ ] **1.4** 历史列表按 API Key / IP 隔离
- [ ] **2.4** Schema 版本化（Alembic）
- [ ] **3.x** 拆分 db.py / pipeline_steps.py / author_feed.py
- [ ] **4.1** YouTube 扫描 lazy enrich（性能）
- [ ] **4.3** 详情弹窗显示评论数
- [ ] **5.x** API 集成测试、Prometheus metrics
