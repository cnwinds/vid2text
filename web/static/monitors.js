const form = document.getElementById("monitor-form");
const urlInput = document.getElementById("monitor-url");
const submitBtn = document.getElementById("monitor-submit");
const statusEl = document.getElementById("monitor-status");
const listEl = document.getElementById("monitor-list");

/** @type {Set<number>} */
const expandedIds = new Set();
/** @type {Map<number, object[]>} */
const videoCache = new Map();

function showStatus(text, isErr = false) {
  if (!statusEl) return;
  statusEl.hidden = false;
  statusEl.textContent = text;
  statusEl.classList.toggle("is-error", isErr);
}

async function parseJson(res) {
  const text = await res.text();
  try {
    return { data: text ? JSON.parse(text) : {}, status: res.status };
  } catch {
    throw new Error(`无效响应 HTTP ${res.status}`);
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function platformLabel(p) {
  return { douyin: "抖音", bilibili: "B站", youtube: "YouTube" }[p] || p;
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function fmtCount(n) {
  const v = Number(n) || 0;
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function statusClass(st) {
  if (st === "done") return "ok";
  if (st === "failed") return "fail";
  if (st === "processing") return "run";
  return "wait";
}

function statusLabel(st) {
  return (
    { done: "已完成", failed: "失败", processing: "提取中", pending: "排队中" }[st] ||
    st ||
    "—"
  );
}

function backfillLabel(m) {
  return m.backfill_mode === "all" ? "全量补采" : `最近 ${m.backfill_n} 条`;
}

function scanIntervalMinutes(sec) {
  return Math.max(5, Math.round((Number(sec) || 2700) / 60));
}

function setExpandOpen(block, open) {
  block.classList.toggle("is-open", open);
  const head = block.querySelector(".expand-head");
  if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
}

function bindExpandHead(block, onToggle) {
  const head = block.querySelector(".expand-head");
  if (!head) return;
  head.addEventListener("click", (e) => {
    if (e.target.closest(".expand-body, .monitor-rules, .monitor-videos-section")) return;
    if (e.target.closest("a, input, select, textarea")) return;
    if (e.target.closest("button") && e.target.closest("button") !== head) return;
    onToggle();
  });
}

function renderRulesForm(m) {
  const recentChecked = m.backfill_mode !== "all";
  return `
    <form class="monitor-rules" data-monitor-id="${m.id}">
      <p class="rules-label">监控规则</p>
      <div class="rules-grid">
        <label class="rules-field">
          <span>启用监控</span>
          <input type="checkbox" name="enabled" ${m.enabled ? "checked" : ""} />
        </label>
        <label class="rules-field">
          <span>扫描间隔（分钟）</span>
          <input type="number" name="scan_minutes" min="5" max="1440" value="${scanIntervalMinutes(m.scan_interval_sec)}" class="n-input-wide" />
        </label>
      </div>
      <div class="monitor-options rules-backfill">
        <label class="radio-line">
          <input type="radio" name="backfill_mode" value="recent" ${recentChecked ? "checked" : ""} />
          补采最近
          <input type="number" name="backfill_n" min="1" max="200" value="${m.backfill_n || 10}" class="n-input" />
          条
        </label>
        <label class="radio-line">
          <input type="radio" name="backfill_mode" value="all" ${!recentChecked ? "checked" : ""} />
          可见范围内全量补采
        </label>
      </div>
      <p class="rules-hint">修改规则后保存；补采策略变更会在下次扫描时生效。</p>
      <div class="rules-actions">
        <button type="submit" class="refresh-btn">保存规则</button>
        <button type="button" class="refresh-btn" data-act="scan">立即扫描</button>
        <button type="button" class="refresh-btn danger" data-act="del">删除监控</button>
      </div>
    </form>`;
}

function renderVideoCards(items) {
  if (!items.length) {
    return '<div class="empty-history">尚未发现作品，保存规则后点「立即扫描」</div>';
  }
  return `<div class="video-card-grid">${items
    .map((v) => {
      const st = v.task_status || "";
      const title = escapeHtml(v.title || v.video_id);
      const url = escapeHtml(v.video_url || "#");
      return `
        <article class="video-card">
          <h3 class="video-card-title">
            <a href="${url}" target="_blank" rel="noopener">${title}</a>
          </h3>
          <dl class="video-card-stats">
            <div><dt>发布</dt><dd>${fmtTime(v.published_at)}</dd></div>
            <div><dt>点赞</dt><dd>${fmtCount(v.like_count)}</dd></div>
            <div><dt>评论</dt><dd>${fmtCount(v.comment_count)}</dd></div>
            <div><dt>播放</dt><dd>${fmtCount(v.play_count)}</dd></div>
          </dl>
          <div class="video-card-foot">
            <span class="status-badge ${statusClass(st)}">${escapeHtml(statusLabel(st))}</span>
            ${v.task_id ? `<span class="video-task-id">#${v.task_id}</span>` : ""}
            ${v.task_error ? `<span class="video-task-err" title="${escapeHtml(v.task_error)}">提取失败</span>` : ""}
          </div>
        </article>`;
    })
    .join("")}</div>`;
}

async function fetchVideos(monitorId) {
  const res = await fetch(`/api/v1/monitors/${monitorId}/videos?limit=200`);
  const { data, status } = await parseJson(res);
  if (status >= 400) throw new Error(data.detail || "加载作品失败");
  const items = data.items || [];
  videoCache.set(monitorId, items);
  return items;
}

async function refreshVideosInBlock(monitorId, block) {
  const wrap = block.querySelector("[data-videos]");
  if (!wrap) return;
  wrap.innerHTML = '<div class="empty-history">加载中…</div>';
  try {
    const items = await fetchVideos(monitorId);
    wrap.innerHTML = renderVideoCards(items);
  } catch (err) {
    wrap.innerHTML = `<div class="empty-history">${escapeHtml(err.message)}</div>`;
  }
}

async function saveRules(m, rulesForm) {
  const fd = new FormData(rulesForm);
  const enabled = !!rulesForm.querySelector('[name="enabled"]')?.checked;
  const mode =
    rulesForm.querySelector('[name="backfill_mode"]:checked')?.value || "recent";
  const backfillN = Number(fd.get("backfill_n")) || 10;
  const scanMin = Number(fd.get("scan_minutes")) || 45;
  const body = {
    enabled,
    backfill_mode: mode,
    backfill_n: backfillN,
    scan_interval_sec: Math.max(300, Math.min(86400, scanMin * 60)),
  };
  const res = await fetch(`/api/v1/monitors/${m.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const { data, status } = await parseJson(res);
  if (status >= 400) throw new Error(data.detail || "保存失败");
  Object.assign(m, data);
  showStatus("规则已保存");
  await loadMonitors();
}

async function handleMonitorAction(m, act, block) {
  if (act === "del") {
    if (!confirm(`删除监控「${m.author_name || m.author_key}」？`)) return;
    const res = await fetch(`/api/v1/monitors/${m.id}`, { method: "DELETE" });
    if (res.status !== 204 && res.status !== 200) {
      const { data } = await parseJson(res);
      throw new Error(data.detail || "删除失败");
    }
    expandedIds.delete(m.id);
    videoCache.delete(m.id);
    showStatus("已删除");
    await loadMonitors();
    return;
  }
  if (act === "scan") {
    showStatus("扫描中…");
    const res = await fetch(`/api/v1/monitors/${m.id}/scan`, { method: "POST" });
    const { data, status } = await parseJson(res);
    if (status >= 400) throw new Error(data.detail || "扫描失败");
    showStatus(`扫描完成：拉取 ${data.fetched}，新入队 ${data.enqueued}`);
    Object.assign(m, data.monitor);
    await refreshVideosInBlock(m.id, block);
    await loadMonitors();
  }
}

function createMonitorBlock(m) {
  const block = document.createElement("div");
  const isOpen = expandedIds.has(m.id);
  block.className = `expand-block monitor-block${m.enabled ? "" : " is-off"}${isOpen ? " is-open" : ""}`;
  block.dataset.monitorId = String(m.id);

  block.innerHTML = `
    <button type="button" class="expand-head monitor-head" aria-expanded="${isOpen}">
      <span class="expand-chevron" aria-hidden="true">${isOpen ? "▾" : "▸"}</span>
      <span class="monitor-head-main">
        <span class="platform-tag">${platformLabel(m.platform)}</span>
        <strong class="monitor-name">${escapeHtml(m.author_name || m.author_key)}</strong>
        <span class="monitor-meta">${m.video_count} 作品 · ${backfillLabel(m)} · 每 ${scanIntervalMinutes(m.scan_interval_sec)} 分钟</span>
        <span class="monitor-meta">上次 ${fmtTime(m.last_scan_at)} · 补采 ${escapeHtml(m.backfill_status || "—")}</span>
        ${m.last_error ? `<span class="monitor-err">${escapeHtml(m.last_error)}</span>` : ""}
      </span>
    </button>
    <div class="expand-body">
      ${renderRulesForm(m)}
      <div class="monitor-videos-section">
        <p class="rules-label">作品列表 <span class="monitor-meta">按发布时间倒序</span></p>
        <div data-videos>${isOpen && videoCache.has(m.id) ? renderVideoCards(videoCache.get(m.id)) : '<div class="empty-history">展开后加载…</div>'}</div>
      </div>
    </div>`;

  bindExpandHead(block, async () => {
    if (expandedIds.has(m.id)) {
      expandedIds.delete(m.id);
      setExpandOpen(block, false);
      block.querySelector(".expand-chevron").textContent = "▸";
    } else {
      expandedIds.add(m.id);
      setExpandOpen(block, true);
      block.querySelector(".expand-chevron").textContent = "▾";
      await refreshVideosInBlock(m.id, block);
    }
  });

  const rulesForm = block.querySelector(".monitor-rules");
  rulesForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await saveRules(m, rulesForm);
    } catch (err) {
      showStatus(err.message || "保存失败", true);
    }
  });

  rulesForm.querySelector('[data-act="scan"]')?.addEventListener("click", () =>
    handleMonitorAction(m, "scan", block).catch((err) => showStatus(err.message, true))
  );
  rulesForm.querySelector('[data-act="del"]')?.addEventListener("click", () =>
    handleMonitorAction(m, "del", block).catch((err) => showStatus(err.message, true))
  );

  if (isOpen) {
    refreshVideosInBlock(m.id, block);
  }

  return block;
}

async function loadMonitors() {
  const res = await fetch("/api/v1/monitors?limit=100");
  const { data, status } = await parseJson(res);
  if (status >= 400) {
    listEl.innerHTML = `<div class="empty-history">${escapeHtml(data.detail || "加载失败")}</div>`;
    return;
  }
  const items = data.items || [];
  if (!items.length) {
    listEl.innerHTML = '<div class="empty-history">暂无监控，展开上方「添加监控」开始</div>';
    return;
  }
  listEl.innerHTML = "";
  for (const m of items) {
    listEl.appendChild(createMonitorBlock(m));
  }
}

form?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;
  const mode = form.querySelector('input[name="backfill_mode"]:checked')?.value || "recent";
  const n = Number(document.getElementById("backfill-n").value) || 10;
  submitBtn.disabled = true;
  showStatus("解析作者并创建监控…");
  try {
    const res = await fetch("/api/v1/monitors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, backfill_mode: mode, backfill_n: n }),
    });
    const { data, status } = await parseJson(res);
    if (status >= 400) throw new Error(data.detail || "创建失败");
    showStatus(`已添加：${data.author_name || data.author_key}（${platformLabel(data.platform)}）`);
    urlInput.value = "";
    expandedIds.add(data.id);
    await loadMonitors();
  } catch (err) {
    showStatus(err.message || "创建失败", true);
  } finally {
    submitBtn.disabled = false;
  }
});

document.getElementById("refresh-monitors")?.addEventListener("click", () => loadMonitors());

document.getElementById("toggle-add-form")?.addEventListener("click", () => {
  const block = document.getElementById("add-monitor-block");
  const open = !block.classList.contains("is-open");
  setExpandOpen(block, open);
  block.querySelector(".expand-chevron").textContent = open ? "▾" : "▸";
});

loadMonitors();
