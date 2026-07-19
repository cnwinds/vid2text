# ADR 001：视频元数据权威源与同步

**状态：** 已采纳  
**日期：** 2026-07-19

## 背景

系统存在两张与视频元数据相关的表：

- `tasks` — 字幕提取任务，含 title、published_at、like_count、author 等
- `monitor_videos` — 监控发现的展示缓存，含互动统计与 task 关联

历史上合并逻辑分散在 `progress_reporter`、`list_monitor_videos`、`enrich_task_row` 等多处，易出现字段不一致（如 `comment_count` 写入错误表）。

## 决策

### 权威源

| 数据 | 权威表 | 说明 |
|------|--------|------|
| 提取进度、文稿 | `tasks` | pipeline 步骤唯一写入点 |
| 标题、作者、published_at、like_count（任务侧） | `tasks` | fetch_meta 写入 |
| 监控卡片展示用互动数据 | `monitor_videos` | 扫描 flat 列表 + lazy enrich |
| comment_count / play_count（展示） | `monitor_videos` | **不**写入 `tasks` 列 |

**原则：** `tasks` 是提取与 API 字幕响应的权威；`monitor_videos` 是监控 UI 的展示缓存，可与 task 合并展示但不应反向污染 tasks schema。

### 同步入口

所有「任务 → 监控作品」回写必须经 **`web/metadata_sync.sync_task_to_monitor_video`**：

- pipeline `fetch_meta` 完成后由 `progress_reporter` / `monitor_service._sync_video_metadata` 调用
- 禁止在其它模块直接 `UPDATE monitor_videos` 写 published_at / 互动字段（`db.sync_monitor_video_engagement` 保留给 legacy 路径，新代码优先 metadata_sync）

### 列表合并（只读）

- `db.enrich_task_row` — 历史/API 读 task 时合并 monitor 侧 published_at、like、comment、play
- `db._enrich_monitor_video_row` — 监控列表读 monitor_videos 时合并 task 侧字段

合并是**读时投影**，不改变权威源。

## 后果

- 新增互动字段时：先明确属于 task 还是 monitor_video，再选写入路径
- YouTube flat 扫描缺字段时：author_feed lazy enrich → scan upsert → 可选 task fetch_meta 二次补全
- Schema 变更走 `web/db_migrations.py` 版本号

## 参考实现

- `web/metadata_sync.py`
- `web/progress_reporter.py`（`_MONITOR_ENGAGEMENT_KEYS` 仅同步 monitor）
- `web/monitor_service.py`（`_sync_video_metadata`）
