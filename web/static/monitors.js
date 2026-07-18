const form = document.getElementById("monitor-form");
const urlInput = document.getElementById("monitor-url");
const submitBtn = document.getElementById("monitor-submit");
const toastEl = document.getElementById("monitor-toast");
const listEl = document.getElementById("monitor-list");

/** @type {Set<number>} */
const expandedIds = new Set();
/** @type {Map<number, object[]>} */
const videoCache = new Map();
/** @type {Map<number, string>} */
const activeTab = new Map();

let toastTimer = null;

function showStatus(text, isErr = false) {
  if (!toastEl) return;
  toastEl.hidden = false;
  toastEl.textContent = text;
  toastEl.classList.toggle("is-error", isErr);
  toastEl.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.classList.remove("is-visible");
  }, 4200);
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

function platformLogoHtml(platform) {
  const label = platformLabel(platform);
  if (platform === "douyin") {
    return `<span class="m-plat-logo plat-douyin" title="${label}" aria-label="${label}">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="#25F4EE" d="M9.2 3v9.1c-1.6-.8-3.4-1-5.2-.5v3.4c2.6-.1 5 1.4 6.2 3.7V3H9.2z"/>
        <path fill="#FE2C55" d="M14.8 4v8.1c1.7-.7 3.6-.8 5.4-.2v3.4c-2.7-.2-5.2 1.3-6.4 3.5V4h1z"/>
      </svg>
    </span>`;
  }
  if (platform === "bilibili") {
    return `<span class="m-plat-logo plat-bili" title="${label}" aria-label="${label}">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="3" y="5" width="18" height="14" rx="3" fill="#FB7299"/>
        <path fill="#fff" d="M8.2 9.5h1.4l.9 2.2.9-2.2h1.3l-1.6 3.6v2.4h-1.3v-2.4L8.2 9.5zm5.8 0h3.2v1.2h-1.9v.9h1.7v1.1h-1.7v1.1h2v1.2h-3.3V9.5z"/>
      </svg>
    </span>`;
  }
  if (platform === "youtube") {
    return `<span class="m-plat-logo plat-yt" title="${label}" aria-label="${label}">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="2" y="6" width="20" height="12" rx="3" fill="#FF0033"/>
        <path fill="#fff" d="M10 9.5v5l5-2.5-5-2.5z"/>
      </svg>
    </span>`;
  }
  return `<span class="m-plat-logo" title="${label}">${escapeHtml(label.slice(0, 1))}</span>`;
}

function renderAvatarCol(m) {
  const plat = platformClass(m.platform);
  return `
    <span class="m-avatar-col">
      <span class="m-avatar ${plat}">${renderAvatar(m)}</span>
      ${platformLogoHtml(m.platform)}
    </span>`;
}

function platformClass(p) {
  return { douyin: "plat-douyin", bilibili: "plat-bili", youtube: "plat-yt" }[p] || "";
}

function authorInitials(name) {
  const s = String(name || "?").trim();
  if (!s) return "?";
  const c = [...s][0];
  return c.toUpperCase();
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "short",
      day: "numeric",
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

const ICON_LIKE =
  '<path d="M12 21s-6.7-4.1-9.2-8.3C.8 9.2 2.4 5.5 6 5.5c2.2 0 3.6 1.2 4.3 2.3.7-1.1 2.1-2.3 4.3-2.3 3.6 0 5.2 3.7 3.2 7.2C18.7 16.9 12 21 12 21z"/>';
const ICON_COMMENT =
  '<path d="M21 11.5a8.4 8.4 0 0 1-8.4 8.4H7l-4 3V11.5A8.4 8.4 0 0 1 11.4 3h.2A8.4 8.4 0 0 1 21 11.5z"/>';
const ICON_PLAY = '<path d="M7 4v16l13-8z"/>';
const ICON_SHARE =
  '<path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11A2.99 2.99 0 0 0 18 4c1.66 0 3 1.34 3 3s-1.34 3-3 3c-.79 0-1.5-.31-2.04-.81l-7.12 4.16c.05.21.08.43.08.65 0 1.61 1.31 2.92 2.92 2.92s2.92-1.31 2.92-2.92-1.31-2.92-2.92-2.92z"/>';
const ICON_COLLECT =
  '<path d="M12 3.2l2.35 4.76 5.25.76-3.8 3.7.9 5.23L12 15.8l-4.7 2.47.9-5.23-3.8-3.7 5.25-.76L12 3.2z"/>';
const ICON_DETAIL =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';

function videoStatSpan(label, iconPath, value) {
  const v = Number(value) || 0;
  if (v <= 0) return "";
  return `<span title="${label}"><svg viewBox="0 0 24 24" aria-hidden="true">${iconPath}</svg>${fmtCount(v)}</span>`;
}

function videoEngagementHtml(v) {
  const metrics = [
    videoStatSpan("点赞", ICON_LIKE, v.like_count),
    videoStatSpan("评论", ICON_COMMENT, v.comment_count),
  ];
  const play = Number(v.play_count) || 0;
  const share = Number(v.share_count) || 0;
  const collect = Number(v.collect_count) || 0;
  if (play > 0) {
    metrics.push(videoStatSpan("播放", ICON_PLAY, play));
  } else if (share > 0) {
    metrics.push(videoStatSpan("分享", ICON_SHARE, share));
  }
  if (collect > 0) {
    metrics.push(videoStatSpan("收藏", ICON_COLLECT, collect));
  }
  const main = metrics.filter(Boolean).join("");
  return main || '<span class="m-vcard-stats-empty">—</span>';
}

function videoExtractClass(st) {
  if (st === "done") return "m-vcard-done";
  if (st === "failed") return "m-vcard-fail";
  return "m-vcard-pending";
}

function backfillLabel(m) {
  return m.backfill_mode === "all" ? "全量" : `${m.backfill_n} 条`;
}

function scanIntervalMinutes(sec) {
  return Math.max(5, Math.round((Number(sec) || 2700) / 60));
}

function monitorStatusDot(m) {
  if (!m.enabled) {
    return '<span class="m-status-dot is-warn" title="已暂停"></span>';
  }
  if ((m.backfill_status || "") === "done") {
    return '<span class="m-status-dot is-ok" title="补采完成"></span>';
  }
  return '<span class="m-status-dot is-warn" title="补采未完成"></span>';
}

function setCardOpen(block, open) {
  block.classList.toggle("is-open", open);
  const head = block.querySelector(".m-card-toggle");
  if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
}

function renderAvatar(m) {
  const plat = platformClass(m.platform);
  const initials = escapeHtml(authorInitials(m.author_name || m.author_key));
  if (m.avatar_url) {
    return `
      <img class="m-avatar-img" src="${escapeHtml(m.avatar_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
      <span class="m-avatar-fallback" aria-hidden="true">${initials}</span>`;
  }
  return `<span class="m-avatar-fallback">${initials}</span>`;
}

function renderRulesForm(m) {
  const recentChecked = m.backfill_mode !== "all";
  return `
    <form class="m-rules" data-monitor-id="${m.id}">
      <div class="m-rules-row">
        <label class="m-toggle">
          <input type="checkbox" name="enabled" ${m.enabled ? "checked" : ""} />
          <span class="m-toggle-track"><span class="m-toggle-thumb"></span></span>
          <span class="m-toggle-label">启用自动扫描</span>
        </label>
        <label class="m-field-inline">
          <span>间隔</span>
          <input type="number" name="scan_minutes" min="5" max="1440" value="${scanIntervalMinutes(m.scan_interval_sec)}" class="n-input n-input-wide" />
          <span class="m-field-unit">分钟</span>
        </label>
      </div>
      <div class="m-segment-group m-segment-compact">
        <span class="m-segment-label">补采范围</span>
        <div class="m-segment">
          <label class="m-segment-item">
            <input type="radio" name="backfill_mode" value="recent" ${recentChecked ? "checked" : ""} />
            <span>最近 N 条</span>
          </label>
          <label class="m-segment-item">
            <input type="radio" name="backfill_mode" value="all" ${!recentChecked ? "checked" : ""} />
            <span>可见全量</span>
          </label>
        </div>
        <label class="m-n-stepper">
          <span>N</span>
          <input type="number" name="backfill_n" min="1" max="200" value="${m.backfill_n || 10}" class="n-input" />
        </label>
      </div>
      <p class="m-rules-hint">保存后下次扫描生效 · 全量补采会分批进行</p>
      <div class="m-actions">
        <button type="submit" class="m-btn m-btn-primary">保存规则</button>
        <button type="button" class="m-btn m-btn-ghost" data-act="scan">立即扫描</button>
        <button type="button" class="m-btn m-btn-danger" data-act="del">删除</button>
      </div>
    </form>`;
}

function renderVideoCards(items) {
  if (!items.length) {
    return `
      <div class="m-empty">
        <div class="m-empty-icon" aria-hidden="true">◎</div>
        <p>还没有作品记录</p>
        <span>保存规则后点「立即扫描」拉取列表</span>
      </div>`;
  }
  return `<div class="m-vgrid">${items
    .map((v) => {
      const st = v.task_status || "";
      const title = escapeHtml(v.title || v.video_id);
      const url = escapeHtml(v.video_url || "#");
      return `
        <article class="m-vcard ${videoExtractClass(st)}" title="${st === "done" ? "采集完成" : st === "failed" ? "采集失败" : "待采集"}">
          <time class="m-vcard-date" datetime="${escapeHtml(v.published_at || "")}">${fmtDate(v.published_at)}</time>
          <h3 class="m-vcard-title">
            <a href="${url}" target="_blank" rel="noopener noreferrer">${title}</a>
          </h3>
          <div class="m-vcard-stats">
            <div class="m-vcard-stats-main">${videoEngagementHtml(v)}</div>
            <button
              type="button"
              class="m-vcard-detail${v.task_id ? "" : " is-disabled"}"
              data-video-detail
              data-task-id="${v.task_id ? escapeHtml(String(v.task_id)) : ""}"
              ${v.task_id ? "" : "disabled"}
              title="${v.task_id ? "查看提取详情" : "尚未开始提取"}"
              aria-label="${v.task_id ? "查看提取详情" : "尚未开始提取"}"
            >${ICON_DETAIL}</button>
          </div>
          ${v.task_error ? `<p class="m-vcard-err" title="${escapeHtml(v.task_error)}">${escapeHtml(v.task_error)}</p>` : ""}
        </article>`;
    })
    .join("")}</div>`;
}

async function fetchVideos(monitorId) {
  const res = await adminFetch(`/api/v1/monitors/${monitorId}/videos?limit=200`);
  const { data, status } = await parseJson(res);
  if (status >= 400) throw new Error(data.detail || "加载作品失败");
  const items = data.items || [];
  videoCache.set(monitorId, items);
  return items;
}

async function refreshVideosInBlock(monitorId, block) {
  const wrap = block.querySelector("[data-videos]");
  if (!wrap) return;
  wrap.innerHTML = '<div class="m-loading">加载作品中…</div>';
  try {
    const items = await fetchVideos(monitorId);
    wrap.innerHTML = renderVideoCards(items);
    const countEl = block.querySelector("[data-video-count]");
    if (countEl) countEl.textContent = String(items.length);
  } catch (err) {
    wrap.innerHTML = `<div class="m-empty"><p>${escapeHtml(err.message)}</p></div>`;
  }
}

function switchTab(block, monitorId, tab) {
  activeTab.set(monitorId, tab);
  block.querySelectorAll(".m-tab").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.tab === tab);
  });
  block.querySelectorAll(".m-tab-panel").forEach((panel) => {
    panel.hidden = panel.dataset.tab !== tab;
  });
}

async function saveRules(m, rulesForm) {
  const fd = new FormData(rulesForm);
  const enabled = !!rulesForm.querySelector('[name="enabled"]')?.checked;
  const mode = rulesForm.querySelector('[name="backfill_mode"]:checked')?.value || "recent";
  const backfillN = Number(fd.get("backfill_n")) || 10;
  const scanMin = Number(fd.get("scan_minutes")) || 45;
  const body = {
    enabled,
    backfill_mode: mode,
    backfill_n: backfillN,
    scan_interval_sec: Math.max(300, Math.min(86400, scanMin * 60)),
  };
  const res = await adminFetch(`/api/v1/monitors/${m.id}`, {
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
    const res = await adminFetch(`/api/v1/monitors/${m.id}`, { method: "DELETE" });
    if (res.status !== 204 && res.status !== 200) {
      const { data } = await parseJson(res);
      throw new Error(data.detail || "删除失败");
    }
    expandedIds.delete(m.id);
    videoCache.delete(m.id);
    activeTab.delete(m.id);
    showStatus("已删除");
    await loadMonitors();
    return;
  }
  if (act === "scan") {
    const btn = block?.querySelector('[data-act="scan"], [data-act="scan-quick"]');
    btn?.classList.add("is-busy");
    showStatus("正在扫描…");
    try {
      const res = await adminFetch(`/api/v1/monitors/${m.id}/scan`, { method: "POST" });
      const { data, status } = await parseJson(res);
      if (status >= 400) throw new Error(data.detail || "扫描失败");
      showStatus(`扫描完成 · 拉取 ${data.fetched} · 新入队 ${data.enqueued}`);
      Object.assign(m, data.monitor);
      if (block) await refreshVideosInBlock(m.id, block);
      await loadMonitors();
    } finally {
      btn?.classList.remove("is-busy");
    }
  }
}

function createMonitorBlock(m) {
  const block = document.createElement("article");
  const isOpen = expandedIds.has(m.id);
  const tab = activeTab.get(m.id) || "videos";
  const plat = platformClass(m.platform);
  block.className = `m-card ${plat}${m.enabled ? "" : " is-paused"}${isOpen ? " is-open" : ""}`;
  block.dataset.monitorId = String(m.id);

  block.innerHTML = `
    <div class="m-card-shell">
      <button type="button" class="m-card-toggle" aria-expanded="${isOpen}">
        ${renderAvatarCol(m)}
        <span class="m-card-info">
          <span class="m-card-title-row">
            <strong class="m-card-name">${escapeHtml(m.author_name || m.author_key)}</strong>
            ${monitorStatusDot(m)}
          </span>
          <span class="m-card-chips">
            <span class="m-chip">${m.video_count} 作品</span>
            <span class="m-chip">每 ${scanIntervalMinutes(m.scan_interval_sec)} 分</span>
            <span class="m-chip">补采 ${backfillLabel(m)}</span>
          </span>
          <span class="m-card-sub">上次扫描 ${fmtTime(m.last_scan_at)}</span>
          ${m.last_error ? `<span class="m-card-err">${escapeHtml(m.last_error)}</span>` : ""}
        </span>
        <span class="m-chevron" aria-hidden="true"></span>
      </button>
      <button type="button" class="m-btn m-btn-icon m-quick-scan" data-act="scan-quick" title="立即扫描">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.6-6.36M21 3v6h-6"/></svg>
      </button>
    </div>
    <div class="m-card-body">
      <div class="m-card-inner">
        <nav class="m-tabs" role="tablist">
          <button type="button" class="m-tab${tab === "videos" ? " is-active" : ""}" data-tab="videos" role="tab">
            作品 <span class="m-tab-count" data-video-count>${m.video_count}</span>
          </button>
          <button type="button" class="m-tab${tab === "rules" ? " is-active" : ""}" data-tab="rules" role="tab">规则</button>
        </nav>
        <div class="m-tab-panel" data-tab="videos" role="tabpanel"${tab !== "videos" ? " hidden" : ""}>
          <div data-videos>${
            isOpen && videoCache.has(m.id)
              ? renderVideoCards(videoCache.get(m.id))
              : '<div class="m-loading">展开后加载…</div>'
          }</div>
        </div>
        <div class="m-tab-panel" data-tab="rules" role="tabpanel"${tab !== "rules" ? " hidden" : ""}>
          ${renderRulesForm(m)}
        </div>
      </div>
    </div>`;

  const toggle = block.querySelector(".m-card-toggle");
  toggle.addEventListener("click", async () => {
    if (expandedIds.has(m.id)) {
      expandedIds.delete(m.id);
      setCardOpen(block, false);
    } else {
      expandedIds.add(m.id);
      setCardOpen(block, true);
      await refreshVideosInBlock(m.id, block);
    }
  });

  block.querySelectorAll(".m-tab").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      switchTab(block, m.id, btn.dataset.tab);
    });
  });

  block.querySelector('[data-act="scan-quick"]')?.addEventListener("click", (e) => {
    e.stopPropagation();
    handleMonitorAction(m, "scan", block).catch((err) => showStatus(err.message, true));
  });

  const rulesForm = block.querySelector(".m-rules");
  rulesForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await saveRules(m, rulesForm);
    } catch (err) {
      showStatus(err.message || "保存失败", true);
    }
  });

  rulesForm?.querySelector('[data-act="scan"]')?.addEventListener("click", () =>
    handleMonitorAction(m, "scan", block).catch((err) => showStatus(err.message, true))
  );
  rulesForm?.querySelector('[data-act="del"]')?.addEventListener("click", () =>
    handleMonitorAction(m, "del", block).catch((err) => showStatus(err.message, true))
  );

  if (isOpen) refreshVideosInBlock(m.id, block);

  const avImg = block.querySelector(".m-avatar-img");
  if (avImg) {
    avImg.addEventListener("error", () => {
      avImg.remove();
    });
  }

  return block;
}

async function loadMonitors() {
  const res = await adminFetch("/api/v1/monitors?limit=100");
  const { data, status } = await parseJson(res);
  if (status >= 400) {
    listEl.innerHTML = `<div class="m-empty"><p>${escapeHtml(data.detail || "加载失败")}</p></div>`;
    return;
  }
  const items = data.items || [];
  if (!items.length) {
    listEl.innerHTML = `
      <div class="m-empty m-empty-lg">
        <div class="m-empty-icon" aria-hidden="true">◌</div>
        <p>还没有监控对象</p>
        <span>在上方粘贴博主链接，开始追踪更新</span>
      </div>`;
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
  showStatus("正在解析作者…");
  try {
    const res = await adminFetch("/api/v1/monitors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, backfill_mode: mode, backfill_n: n }),
    });
    const { data, status } = await parseJson(res);
    if (status >= 400) throw new Error(data.detail || "创建失败");
    showStatus(`已添加 ${data.author_name || data.author_key}`);
    urlInput.value = "";
    expandedIds.add(data.id);
    activeTab.set(data.id, "videos");
    await loadMonitors();
  } catch (err) {
    showStatus(err.message || "创建失败", true);
  } finally {
    submitBtn.disabled = false;
  }
});

document.getElementById("refresh-monitors")?.addEventListener("click", () => loadMonitors());

document.getElementById("toggle-add-form")?.addEventListener("click", () => {
  const body = document.getElementById("add-monitor-body");
  const btn = document.getElementById("toggle-add-form");
  const open = !body.classList.contains("is-open");
  body.classList.toggle("is-open", open);
  btn?.setAttribute("aria-expanded", open ? "true" : "false");
});

(function initBgCanvas() {
  const canvas = document.getElementById("bgCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) return;
  let w = 0;
  let h = 0;
  let raf = 0;
  const pts = Array.from({ length: 28 }, () => ({
    x: Math.random(),
    y: Math.random(),
    r: 0.6 + Math.random(),
    vy: -(0.0002 + Math.random() * 0.0004),
    ph: Math.random() * 6.28,
  }));

  function resize() {
    w = canvas.width = window.innerWidth;
    h = canvas.height = window.innerHeight;
  }

  function tick() {
    ctx.clearRect(0, 0, w, h);
    for (const p of pts) {
      p.ph += 0.015;
      p.y += p.vy;
      if (p.y < 0) {
        p.y = 1;
        p.x = Math.random();
      }
      const alpha = 0.06 + 0.05 * Math.sin(p.ph);
      ctx.beginPath();
      ctx.fillStyle = `rgba(70,224,201,${alpha})`;
      ctx.arc(p.x * w, p.y * h, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    raf = requestAnimationFrame(tick);
  }

  resize();
  tick();
  window.addEventListener("resize", resize);
})();

if (window.Vid2TaskModal) {
  Vid2TaskModal.configure({
    onStatus: (text) => showStatus(text),
    onStatusHide: () => {},
    onTaskDone: () => {
      for (const id of expandedIds) {
        const block = listEl?.querySelector(`[data-monitor-id="${id}"]`);
        if (block) refreshVideosInBlock(id, block);
      }
    },
    setSubmitDisabled: () => {},
  });
  Vid2TaskModal.init();
}

listEl?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-video-detail]");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const taskId = Number(btn.dataset.taskId);
  if (!taskId) {
    showStatus("该作品尚未开始提取");
    return;
  }
  Vid2TaskModal?.openById(taskId, e);
});

loadMonitors();
