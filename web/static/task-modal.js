/** 任务详情弹窗（主页 / 监控页共用） */
(function (global) {
  const SUBTITLES_API = "/api/v1/subtitles";

  let taskModal = null;
  let taskModalBody = null;
  let taskModalFoot = null;
  let taskModalTitle = null;

  let hooks = {
    onStatus: () => {},
    onStatusHide: () => {},
    onTaskDone: () => {},
    onPoll: () => {},
    setSubmitDisabled: () => {},
  };

  function configure(h) {
    hooks = { ...hooks, ...h };
  }

  function showStatus(text) {
    hooks.onStatus(String(text || ""));
  }

  function hideStatus() {
    hooks.onStatusHide();
  }

  function bindDom() {
    taskModal = document.getElementById("task-modal");
    taskModalBody = document.getElementById("task-modal-body");
    taskModalFoot = document.getElementById("task-modal-foot");
    taskModalTitle = document.getElementById("task-modal-title");
  }

let pollTimer = null;
/** 结果区当前展示的任务 */
let viewingTaskId = null;
/** 后台轮询中的任务（可与 viewing 不同） */
let pollingTaskId = null;
/** @deprecated 兼容重试按钮，等同 viewingTaskId */
let currentTaskId = null;
let procAnim = null;
let procAnimTaskId = null;
let progressHighWater = { taskId: null, pct: 0, stageIdx: -1 };
let animStageIdx = 0;
let metricTarget = { activity: 0.15, cpu: 0, network_kbps: 0, kind: "idle", detail: "" };
let metricDisplay = { activity: 0.15, cpu: 0, network_kbps: 0, kind: "idle", detail: "" };
/** 各步骤下方展示的速度/大小等缓存 */
let stageMetaCache = {};

const PIPELINE_STEPS = [
  { key: "parse", label: "解析链接" },
  { key: "fetch_meta", label: "获取视频信息" },
  { key: "fetch_subtitle", label: "获取平台字幕" },
  { key: "download", label: "下载视频" },
  { key: "extract_audio", label: "提取音轨" },
  { key: "stt", label: "语音识别" },
  { key: "correct", label: "文本修正" },
];

const STEP_INDEX = Object.fromEntries(PIPELINE_STEPS.map((s, i) => [s.key, i]));

function taskIsActive(status) {
  return status === "pending" || status === "processing";
}

function getProcElements() {
  const panel = taskModalBody?.querySelector(".task-proc-panel");
  if (!panel) return null;
  return {
    panel,
    canvas: panel.querySelector(".proc-canvas"),
    progFill: panel.querySelector(".progress-fill"),
    progPct: panel.querySelector(".progress-bar-pct"),
    progLabel: panel.querySelector(".progress-bar-label"),
    stages: panel.querySelectorAll(".stage"),
  };
}

function buildProcessingPanelHtml() {
  const steps = PIPELINE_STEPS.map(
    (s) => `
        <div class="stage" data-step="${s.key}">
          <span class="stage-name">${s.label}</span>
          <span class="stage-meta" data-meta></span>
        </div>`
  ).join("");
  return `
    <div class="processing-panel task-proc-panel">
      <p class="section-label">转码中</p>
      <div class="proc-canvas-wrap">
        <canvas class="proc-canvas" aria-hidden="true"></canvas>
      </div>
      <div class="progress-bar">
        <div class="progress-bar-head">
          <span class="progress-bar-label">排队中</span>
          <span class="progress-bar-pct">0%</span>
        </div>
        <div class="progress-track"><div class="progress-fill"></div></div>
      </div>
      <div class="stage-row">${steps}</div>
    </div>`;
}

const STEP_DEFAULT_METRICS = {
  parse: { kind: "idle", activity: 0.15 },
  fetch_meta: { kind: "network", activity: 0.32 },
  fetch_subtitle: { kind: "network", activity: 0.38 },
  download: { kind: "network", activity: 0.45 },
  extract_audio: { kind: "cpu", activity: 0.55 },
  stt: { kind: "cpu", activity: 0.72 },
  correct: { kind: "network", activity: 0.42 },
};

function normalizeMetrics(raw, step) {
  const base = STEP_DEFAULT_METRICS[step] || { kind: "idle", activity: 0.2 };
  const m = raw && typeof raw === "object" ? raw : {};
  return {
    kind: m.kind || base.kind,
    activity: Number(m.activity ?? base.activity) || base.activity,
    cpu: Number(m.cpu) || 0,
    network_kbps: Number(m.network_kbps) || 0,
    detail: m.detail || "",
    facts: Array.isArray(m.facts) ? m.facts : [],
    title_snip: m.title_snip || "",
    pct: m.pct ?? null,
  };
}

function fmtKbps(kbps) {
  const n = Number(kbps) || 0;
  if (n <= 0) return "";
  if (n >= 1024) return `${(n / 1024).toFixed(1)} MB/s`;
  return `${Math.round(n)} KB/s`;
}

function formatStageMeta(step, metrics) {
  if (!metrics) return "";
  const facts = Array.isArray(metrics.facts) ? metrics.facts : [];
  const byKey = Object.fromEntries(facts.map((f) => [f.key, f.value]));
  const parts = [];

  if (step === "parse") {
    return byKey.link || metrics.detail || "解析中";
  }
  if (step === "fetch_meta") {
    if (metrics.title_snip) return metrics.title_snip;
    if (byKey.duration) parts.push(byKey.duration);
    if (byKey.platform) parts.push(byKey.platform);
    return parts.join(" · ") || metrics.detail || "获取中";
  }
  if (step === "fetch_subtitle") {
    return byKey.subtitle || metrics.detail || "检查中";
  }
  if (step === "download") {
    const speed = fmtKbps(metrics.network_kbps);
    if (speed) parts.push(speed);
    if (metrics.pct != null) parts.push(`${Math.round(metrics.pct)}%`);
    if (byKey.loaded) parts.push(byKey.loaded);
    if (byKey.target) parts.push(byKey.target);
    if (!parts.length && metrics.detail) return metrics.detail;
    return parts.join(" · ") || "下载中";
  }
  if (step === "extract_audio") {
    if (byKey.track) parts.push(byKey.track);
    if (byKey.video) parts.push(byKey.video);
    if (metrics.cpu) parts.push(`CPU ${Math.round(metrics.cpu)}%`);
    return parts.join(" · ") || metrics.detail || "提取中";
  }
  if (step === "stt") {
    if (byKey.audio) parts.push(byKey.audio);
    if (byKey.wave) parts.push(byKey.wave);
    if (metrics.cpu) parts.push(`CPU ${Math.round(metrics.cpu)}%`);
    return parts.join(" · ") || metrics.detail || "识别中";
  }
  if (step === "correct") {
    if (byKey.draft) parts.push(byKey.draft);
    return parts.join(" · ") || metrics.detail || "修正中";
  }
  return metrics.detail || "";
}

function resetStageMetas() {
  stageMetaCache = {};
  const { stages: stageEls } = getProcElements() || {};
  (stageEls || []).forEach((el) => {
    const meta = el.querySelector("[data-meta]");
    if (meta) meta.textContent = "";
  });
}

function updateStageMetas(metrics, activeIdx, status) {
  if (metrics && activeIdx >= 0 && activeIdx < PIPELINE_STEPS.length) {
    const key = PIPELINE_STEPS[activeIdx].key;
    const text = formatStageMeta(key, metrics);
    if (text) stageMetaCache[key] = text;
  }

  const { stages: stageEls } = getProcElements() || {};
  (stageEls || []).forEach((el, i) => {
    const meta = el.querySelector("[data-meta]");
    if (!meta) return;
    const key = el.dataset.step;
    if (status === "done") {
      meta.textContent = stageMetaCache[key] || "完成";
      return;
    }
    if (i < activeIdx) {
      meta.textContent = stageMetaCache[key] || "完成";
    } else if (i === activeIdx) {
      meta.textContent = stageMetaCache[key] || "进行中";
    } else {
      meta.textContent = stageMetaCache[key] || "";
    }
  });
}

function setMetricTarget(metrics) {
  metricTarget = { ...metricTarget, ...metrics };
}

function lerpMetrics() {
  const k = 0.12;
  const kFast = 0.22;
  metricDisplay.activity += (metricTarget.activity - metricDisplay.activity) * k;
  metricDisplay.cpu += (metricTarget.cpu - metricDisplay.cpu) * kFast;
  metricDisplay.network_kbps += (metricTarget.network_kbps - metricDisplay.network_kbps) * kFast;
  metricDisplay.kind = metricTarget.kind;
  metricDisplay.detail = metricTarget.detail;
}

const STATUS_LABEL = {
  pending: "排队中",
  processing: "转换中",
  done: "成功",
  failed: "失败",
};


const RETRY_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-2.6-6.36M21 3v6h-6"/></svg>';

const ICON_COPY =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

const ICON_DOWNLOAD =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';

const ICON_EXTERNAL =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/></svg>';


function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function copyToClipboard(text, btn) {
  const value = String(text || "").trim();
  if (!value) return false;
  try {
    await navigator.clipboard.writeText(value);
    if (btn) {
      const label = btn.querySelector("span");
      if (label) {
        const orig = label.textContent;
        label.textContent = "已复制";
        btn.disabled = true;
        setTimeout(() => {
          label.textContent = orig;
          btn.disabled = false;
        }, 1200);
      } else {
        const origTitle = btn.title;
        btn.title = "已复制";
        btn.disabled = true;
        setTimeout(() => {
          btn.title = origTitle;
          btn.disabled = false;
        }, 1200);
      }
    } else {
      showStatus("已复制到剪贴板");
    }
    return true;
  } catch {
    showStatus("复制失败，请手动复制");
    return false;
  }
}

function canDownloadVideo(status) {
  return status === "done" || status === "failed";
}

function taskTranscriptText(task) {
  return String(task.corrected_transcript || task.raw_transcript || "").trim();
}

function hasTranscript(task) {
  return !!taskTranscriptText(task);
}

function historyTranscriptText(view) {
  return taskTranscriptText(view);
}

function hasHistoryTranscript(view) {
  return hasTranscript(view);
}

function taskVideoDownloadApi(taskId) {
  return `${SUBTITLES_API}/${taskId}/download`;
}

async function triggerVideoDownload(taskId, btn) {
  if (btn) btn.disabled = true;
  showStatus("正在准备视频（首次可能需要几十秒）…");
  try {
    const checkRes = await fetch(`${taskVideoDownloadApi(taskId)}?check=1`);
    if (!checkRes.ok) {
      const data = await checkRes.json().catch(() => ({}));
      throw new Error(data.detail || `无法下载（HTTP ${checkRes.status}）`);
    }
    window.location.assign(taskVideoDownloadApi(taskId));
    showStatus("下载已开始，请查看浏览器下载栏");
  } catch (err) {
    showStatus(err.message || "下载失败");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function histActionBtn(kind, title, iconSvg) {
  return `<button type="button" class="hist-act hist-act-${kind}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${iconSvg}</button>`;
}

function fmtDurationSec(sec) {
  const total = Math.max(0, Math.round(Number(sec) || 0));
  if (!total) return "";
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function taskDurationSec(task) {
  const direct = Number(task.duration_sec);
  if (direct > 0) return direct;
  const m = task.progress_metrics;
  if (m && typeof m === "object" && Number(m.duration_sec) > 0) {
    return Number(m.duration_sec);
  }
  return 0;
}

function taskDurationLabel(task) {
  return fmtDurationSec(taskDurationSec(task));
}

function buildTaskHeroHtml(task) {
  const author = historyAuthorLabel(task) || "未知播主";
  const title = task.title || task.video_url || `任务 #${task.id}`;
  const durationLabel = taskDurationLabel(task);
  const durationHtml = durationLabel
    ? `<span class="td-hero-duration">${escapeHtml(durationLabel)}</span>`
    : "";
  const publishedLabel = taskPublishedLabel(task);
  const publishedHtml = publishedLabel
    ? `<time class="td-hero-published" datetime="${escapeHtml(task.published_at || "")}">${escapeHtml(publishedLabel)}</time>`
    : "";
  const likeLabel = taskLikeLabel(task);
  const likeHtml = likeLabel
    ? `<span class="td-hero-like" title="点赞">${escapeHtml(likeLabel)}</span>`
    : "";
  return `
    <header class="td-hero">
      ${renderPlatformAvatarCol(task.platform, historyAvatarInner(task))}
      <div class="td-hero-body">
        <h3 class="td-hero-title">${escapeHtml(title)}</h3>
        <p class="td-hero-meta">
          <span class="td-hero-author">${escapeHtml(author)}</span>
          ${publishedHtml}
          ${likeHtml}
          ${durationHtml}
          <span class="td-hero-task-id">#${escapeHtml(String(task.id))}</span>
        </p>
      </div>
    </header>`;
}

function buildTaskActionBarHtml(task) {
  const transcript = taskTranscriptText(task);
  const parts = [];
  if (transcript) {
    parts.push(
      `<button type="button" class="td-act td-act-primary" id="modal-copy-text-btn">${ICON_COPY}<span>复制文稿</span></button>`
    );
  }
  if (canDownloadVideo(task.status)) {
    parts.push(
      `<button type="button" class="td-act" id="modal-dl-btn">${ICON_DOWNLOAD}<span>下载视频</span></button>`
    );
  }
  parts.push(
    `<a class="td-act td-act-link" href="${escapeHtml(task.video_url)}" target="_blank" rel="noopener">${ICON_EXTERNAL}<span>原视频</span></a>`
  );
  return `<div class="td-action-bar">${parts.join("")}</div>`;
}

function buildTranscriptSectionHtml(task) {
  const text = taskTranscriptText(task);
  if (!text) {
    if (taskIsActive(task.status)) return "";
    return `
      <section class="td-section td-section-empty">
        <p>暂无口播文稿${task.status === "failed" ? "（提取失败）" : ""}</p>
      </section>`;
  }
  const live = taskIsActive(task.status);
  const metaPill = live
    ? `<span class="td-meta-pill td-meta-pill-live">转录完成 · 后处理中</span>`
    : `<span class="td-meta-pill">${text.length.toLocaleString()} 字</span>`;
  return `
    <section class="td-section td-section-hero">
      <div class="td-section-head">
        <h4 class="td-section-title">口播文稿</h4>
        ${metaPill}
      </div>
      <div class="td-transcript">${escapeHtml(text)}</div>
    </section>`;
}

function buildTaskExtrasHtml(task) {
  const blocks = [];
  const raw = (task.raw_transcript || "").trim();
  const corrected = (task.corrected_transcript || "").trim();
  if (raw && corrected && raw !== corrected) {
    blocks.push(`
      <details class="td-details">
        <summary>原始转录</summary>
        <div class="td-details-body">${escapeHtml(raw)}</div>
      </details>`);
  }
  const desc = (task.description || "").trim();
  if (desc) {
    blocks.push(`
      <details class="td-details">
        <summary>视频描述</summary>
        <div class="td-details-body">${escapeHtml(desc)}</div>
      </details>`);
  }
  if (!blocks.length) return "";
  return `<div class="td-extras">${blocks.join("")}</div>`;
}

function buildTaskModalFootHtml(task) {
  if (task.status === "failed") {
    return `<div class="m-modal-actions"><button type="button" class="td-act td-act-danger" id="modal-retry-btn">${RETRY_SVG}<span>重新提取</span></button></div>`;
  }
  if (shouldForceMediaRetry(task)) {
    return `<div class="m-modal-actions"><button type="button" class="td-act td-act-danger" id="modal-retry-btn">${RETRY_SVG}<span>重新下载</span></button></div>`;
  }
  return "";
}

function shouldForceMediaRetry(task) {
  if (!task || !taskIsActive(task.status)) return false;
  const step = String(task.progress_step || "").trim();
  return step === "download" || step === "extract_audio";
}

function syncTaskModalFoot(task) {
  if (!taskModalFoot) return;
  const html = buildTaskModalFootHtml(task);
  taskModalFoot.innerHTML = html;
  taskModalFoot.hidden = !html;
}

function historyAuthorLabel(view) {
  return String(view.author_name || "").trim();
}

function historyTitleLabel(view) {
  const title = String(view.title || "").trim();
  if (title) return title;
  return "未命名作品";
}

function historyAvatarInner(view) {
  const name = historyAuthorLabel(view) || historyTitleLabel(view);
  return renderAuthorAvatarInner(name, view.avatar_url, view.video_id);
}

function buildTaskDetailHtml(task) {
  let errorHtml = "";
  if (task.status === "failed" && task.error_message) {
    errorHtml = `
      <div class="td-alert td-alert-error" role="alert">
        <strong>提取失败</strong>
        <p>${escapeHtml(task.error_message)}</p>
      </div>`;
  }

  const procHtml = taskIsActive(task.status) ? buildProcessingPanelHtml() : "";
  const heroHtml = buildTaskHeroHtml(task);
  const hasPartial = hasTranscript(task);
  const actionBarHtml =
    taskIsActive(task.status) && !hasPartial ? "" : buildTaskActionBarHtml(task);
  const transcriptHtml = buildTranscriptSectionHtml(task);
  const extrasHtml = buildTaskExtrasHtml(task);

  return `
    <div class="task-detail${taskIsActive(task.status) ? " is-live" : ""}" data-task-id="${task.id}">
      ${procHtml}
      <div class="task-detail-body">
        ${heroHtml}
        ${errorHtml}
        ${actionBarHtml}
        ${transcriptHtml}
        ${extrasHtml}
      </div>
    </div>`;
}

function bindTaskModalActions(task) {
  const retryBtn = document.getElementById("modal-retry-btn");
  if (retryBtn) {
    retryBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (typeof global.miniBurst === "function") global.miniBurst(e.clientX, e.clientY);
      handleRetryTask(task);
    });
  }

  const copyBtn = document.getElementById("modal-copy-text-btn");
  if (copyBtn) {
    copyBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const text = taskTranscriptText(task);
      if (text) copyToClipboard(text, copyBtn);
    });
  }

  const dlBtn = document.getElementById("modal-dl-btn");
  if (dlBtn) {
    dlBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (typeof global.miniBurst === "function") global.miniBurst(e.clientX, e.clientY);
      triggerVideoDownload(task.id, dlBtn);
    });
  }
}

function openTaskModal(task, animate = false) {
  if (!taskModal || !taskModalBody) return;
  viewingTaskId = task.id;
  currentTaskId = task.id;
  if (taskModalTitle) taskModalTitle.textContent = "任务详情";
  taskModalBody.innerHTML = buildTaskDetailHtml(task);
  syncTaskModalFoot(task);
  if (animate) {
    const detail = taskModalBody.querySelector(".task-detail");
    detail?.classList.add("enter");
  }
  bindTaskModalActions(task);
  taskModal.hidden = false;
  taskModal.setAttribute("aria-hidden", "false");
  document.body.classList.add("m-modal-open");
  if (taskIsActive(task.status)) {
    scheduleProcAnim(task);
  } else {
    stopProcAnim();
  }
}

function closeTaskModal() {
  if (!taskModal) return;
  stopProcAnim();
  if (taskModalFoot) {
    taskModalFoot.innerHTML = "";
    taskModalFoot.hidden = true;
  }
  taskModal.hidden = true;
  taskModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("m-modal-open");
}

function renderTask(task, animate = false) {
  openTaskModal(task, animate);
}

function patchTaskHero(inModal, task) {
  const titleEl = inModal.querySelector(".td-hero-title");
  if (titleEl) {
    titleEl.textContent = task.title || task.video_url || `任务 #${task.id}`;
  }
  const authorEl = inModal.querySelector(".td-hero-author");
  if (authorEl) {
    authorEl.textContent = historyAuthorLabel(task) || "未知播主";
  }
  const durationEl = inModal.querySelector(".td-hero-duration");
  const durationLabel = taskDurationLabel(task);
  if (durationEl) {
    if (durationLabel) durationEl.textContent = durationLabel;
  } else if (durationLabel) {
    const meta = inModal.querySelector(".td-hero-meta");
    meta?.insertAdjacentHTML(
      "beforeend",
      `<span class="td-hero-duration">${escapeHtml(durationLabel)}</span>`
    );
  }
  const avatarCol = inModal.querySelector(".m-avatar-col");
  if (avatarCol) {
    const wrap = document.createElement("div");
    wrap.innerHTML = renderPlatformAvatarCol(task.platform, historyAvatarInner(task));
    avatarCol.replaceWith(wrap.firstElementChild);
  }
}

function ensureTranscriptSection(inModal, task) {
  const body = inModal.querySelector(".task-detail-body");
  if (!body) return;
  const html = buildTranscriptSectionHtml(task);
  if (!html) return;
  const existing = body.querySelector(".td-section-hero, .td-section-empty");
  if (existing) {
    existing.outerHTML = html;
    return;
  }
  const actionBar = body.querySelector(".td-action-bar");
  if (actionBar) actionBar.insertAdjacentHTML("afterend", html);
  else body.insertAdjacentHTML("beforeend", html);
}

function ensureTaskActionBar(inModal, task) {
  const body = inModal.querySelector(".task-detail-body");
  if (!body || body.querySelector(".td-action-bar")) return;
  const html = buildTaskActionBarHtml(task);
  if (!html) return;
  const hero = body.querySelector(".td-hero");
  if (hero) hero.insertAdjacentHTML("afterend", html);
  else body.insertAdjacentHTML("afterbegin", html);
  bindTaskModalActions(task);
}

function patchTaskStatus(task) {
  const inModal =
    taskModal &&
    !taskModal.hidden &&
    viewingTaskId === task.id &&
    taskModalBody?.querySelector(`.task-detail[data-task-id="${task.id}"]`);
  if (!inModal) {
    if (
      viewingTaskId === task.id &&
      (task.status === "done" || task.status === "failed")
    ) {
      openTaskModal(task);
    }
    return;
  }

  patchTaskHero(inModal, task);

  const transcriptEl = inModal.querySelector(".td-transcript");
  const text = taskTranscriptText(task);
  if (transcriptEl && text) {
    transcriptEl.textContent = text;
    const pill = inModal.querySelector(".td-section-head .td-meta-pill");
    if (pill && !pill.classList.contains("td-meta-pill-live")) {
      pill.textContent = `${text.length.toLocaleString()} 字`;
    }
  } else if (text) {
    ensureTaskActionBar(inModal, task);
    ensureTranscriptSection(inModal, task);
    bindTaskModalActions(task);
  }

  if (task.status === "done" || task.status === "failed") {
    stopProcAnim();
    taskModalBody.innerHTML = buildTaskDetailHtml(task);
    syncTaskModalFoot(task);
    bindTaskModalActions(task);
  } else {
    syncTaskModalFoot(task);
    bindTaskModalActions(task);
  }
}

function subtitleToView(data) {
  const v = data.video || {};
  const sub = data.subtitle || {};
  let status = "processing";
  if (data.ready) status = "done";
  else if (data.error) status = "failed";
  else if (data.processing?.status === "failed") status = "failed";
  else if (data.processing?.status === "pending") status = "pending";

  return {
    id: data.id,
    status,
    platform: v.platform,
    video_id: v.video_id,
    video_url: v.url,
    title: v.title,
    description: v.description,
    author_name: v.author_name || "",
    avatar_url: v.avatar_url || "",
    download_url: v.download_url || "",
    duration_sec: Number(v.duration_sec) || 0,
    published_at: v.published_at || "",
    like_count: Number(v.like_count) || 0,
    raw_transcript: sub.raw || "",
    corrected_transcript: sub.corrected || "",
    error_message: data.error || "",
    progress_step: data.processing?.step || "",
    progress_notice: data.processing?.notice || "",
    progress_resume_from: data.processing?.resume_from || "",
    queue_ahead: Number(data.processing?.queue_ahead) || 0,
    progress_metrics: data.progress_metrics || {},
    cached: data.cached,
  };
}

function isTransientFetchError(err) {
  if (!err) return false;
  if (err.transient) return true;
  const msg = String(err.message || "");
  return (
    err.name === "TypeError" ||
    msg.includes("空响应") ||
    msg.includes("无效数据") ||
    msg.includes("Failed to fetch") ||
    msg.includes("NetworkError") ||
    msg.includes("Unexpected end of JSON")
  );
}

async function parseJsonResponse(res) {
  const text = await res.text();
  const trimmed = text.trim();
  if (!trimmed) {
    const err = new Error(
      res.ok
        ? "服务器返回空响应，可能正在重启，请稍后重试"
        : `请求失败（HTTP ${res.status}）`
    );
    err.transient = !res.ok || res.status >= 502;
    throw err;
  }
  try {
    return { data: JSON.parse(trimmed), status: res.status };
  } catch {
    const err = new Error(`服务器返回无效数据（HTTP ${res.status}）`);
    err.transient = true;
    throw err;
  }
}

async function fetchTask(taskId) {
  const res = await fetch(`${SUBTITLES_API}/${taskId}`);
  const { data, status } = await parseJsonResponse(res);
  if (status === 404) throw new Error("记录不存在");
  if (status >= 500) throw new Error(data.detail || `加载失败（HTTP ${status}）`);
  return subtitleToView(data);
}

async function retryTask(taskId, taskHint) {
  const force = taskHint && shouldForceMediaRetry(taskHint);
  const url = `${SUBTITLES_API}/${taskId}/retry${force ? "?force=1" : ""}`;
  const res = await fetch(url, { method: "POST" });
  const { data, status } = await parseJsonResponse(res);
  if (status === 400 || status === 404) {
    throw new Error(data.detail || "重试失败");
  }
  if (status === 429) {
    const activeId = data.active_id || data.processing?.id;
    const msg = data.detail || "当前已有进行中的提取任务";
    throw new Error(activeId ? `${msg}（#${activeId}）` : msg);
  }
  return subtitleToView(data);
}

function resetProgressHighWater(taskId) {
  progressHighWater = { taskId, pct: 0, stageIdx: -1 };
}

function computeProgressPct(task, activeIdx, status) {
  const total = PIPELINE_STEPS.length;
  if (status === "done") return 100;
  if (activeIdx < 0) return 2;
  const stepSpan = 92 / total;
  const base = activeIdx * stepSpan;
  let sub = 0;
  const step = PIPELINE_STEPS[activeIdx]?.key;
  const m = task.progress_metrics && typeof task.progress_metrics === "object" ? task.progress_metrics : {};
  if (step === "download" && m.pct != null && Number(m.pct) > 0) {
    sub = (Math.min(100, Number(m.pct)) / 100) * stepSpan * 0.88;
  } else if ((step === "stt" || step === "extract_audio") && Number(m.cpu) > 0) {
    sub = (Math.min(100, Number(m.cpu)) / 100) * stepSpan * 0.8;
  } else if (Number(m.activity) > 0) {
    sub = Math.min(1, Number(m.activity)) * stepSpan * 0.45;
  }
  return Math.min(95, base + stepSpan * 0.1 + sub);
}

function updateProcProgress(task) {
  const proc = getProcElements();
  const { progress_step: step, status } = task;
  const pm = parseProgressMetrics(task.progress_metrics);
  const queuedStep = pm.queued_step || "";
  const isStepQueued =
    status === "processing" &&
    (queuedStep || String(pm.detail || "").includes("排队等待"));
  let activeIdx = -1;
  let metrics = null;

  if (progressHighWater.taskId !== task.id) {
    resetProgressHighWater(task.id);
  }

  if (status === "done") {
    activeIdx = PIPELINE_STEPS.length;
    metrics = { activity: 1, cpu: 0, network_kbps: 0, kind: "idle", detail: "完成", facts: [] };
    setMetricTarget(metrics);
  } else if (isStepQueued) {
    const waitKey = queuedStep || step;
    activeIdx = STEP_INDEX[waitKey] ?? STEP_INDEX[step] ?? 0;
    metrics = {
      kind: "idle",
      activity: 0.06,
      cpu: 0,
      network_kbps: 0,
      detail: pm.detail || "排队等待",
      facts: [],
    };
    setMetricTarget(metrics);
  } else if (step && STEP_INDEX[step] !== undefined) {
    activeIdx = STEP_INDEX[step];
    metrics = normalizeMetrics(task.progress_metrics, step);
    setMetricTarget(metrics);
  } else if (status === "processing") {
    activeIdx = 0;
    metrics = normalizeMetrics(task.progress_metrics, "parse");
    setMetricTarget(metrics);
  } else if (status === "pending") {
    activeIdx = 0;
    const ahead = Number(task.queue_ahead) || 0;
    const detail = ahead > 0 ? `排队中 · 前面 ${ahead} 个` : "排队中";
    metrics = { kind: "idle", activity: 0.1, cpu: 0, network_kbps: 0, detail, facts: [] };
    setMetricTarget(metrics);
  }

  if (activeIdx >= 0 && status !== "done") {
    activeIdx = Math.max(progressHighWater.stageIdx, activeIdx);
    progressHighWater.stageIdx = activeIdx;
  }

  animStageIdx = Math.max(0, activeIdx);

  if (proc?.stages) {
    proc.stages.forEach((el, i) => {
      el.classList.remove("active", "done", "queued");
      if (status === "done" || i < activeIdx) {
        el.classList.add("done");
      } else if (isStepQueued && i === activeIdx) {
        el.classList.add("queued");
      } else if (i === activeIdx) {
        el.classList.add("active");
      }
    });
  }

  updateStageMetas(metrics, activeIdx, status);

  let pct = computeProgressPct(task, activeIdx, status);
  if (status !== "done") {
    pct = Math.max(progressHighWater.pct, pct);
  }
  progressHighWater.pct = pct;
  const stepLabel = activeIdx >= 0 ? PIPELINE_STEPS[activeIdx]?.label : "排队中";
  if (proc?.progFill) {
    proc.progFill.style.width = `${pct}%`;
    proc.progFill.classList.toggle("is-complete", status === "done" || pct >= 100);
  }
  if (proc?.progPct) proc.progPct.textContent = `${Math.round(pct)}%`;
  if (proc?.panel) {
    proc.panel.classList.toggle("is-pending", status === "pending" || isStepQueued);
    proc.panel.classList.toggle("is-processing", status === "processing" && !isStepQueued);
  }
  if (proc?.progLabel) {
    if (status === "done") proc.progLabel.textContent = "已完成";
    else if (isStepQueued) {
      proc.progLabel.textContent = pm.detail || "排队等待";
    } else {
      proc.progLabel.textContent = taskRunningStatusLabel(task) || stepLabel;
    }
  }

  if (status === "pending" || status === "processing") {
    let statusText = `任务 #${task.id} · ${taskRunningStatusLabel(task) || stepLabel}`;
    if (status === "pending" && (Number(task.queue_ahead) || 0) > 0) {
      statusText = `任务 #${task.id} · 排队中（前面还有 ${task.queue_ahead} 个）`;
    }
    if (task.progress_notice) {
      statusText += ` · ${task.progress_notice.replace(/^resume:/, "")}`;
    }
    showStatus(statusText);
  }
}

function stopProcAnim() {
  if (procAnim) {
    procAnim();
    procAnim = null;
  }
  procAnimTaskId = null;
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  pollingTaskId = null;
}


function scheduleProcAnim(task) {
  if (!task || !taskIsActive(task.status)) return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (viewingTaskId !== task.id) return;
      if (!getProcElements()?.canvas) return;
      if (procAnim && procAnimTaskId === task.id) {
        updateProcProgress(task);
        return;
      }
      procAnimTaskId = task.id;
      if (progressHighWater.taskId !== task.id) {
        resetProgressHighWater(task.id);
      }
      startProcessingAnim();
      updateProcProgress(task);
    });
  });
}

function startProcessingAnim() {
  stopProcAnim();
  procAnimTaskId = viewingTaskId;
  const proc = getProcElements();
  if (!proc?.canvas || !proc.progFill) return;
  proc.stages.forEach((s) => s.classList.remove("active", "done"));
  const resumePct = progressHighWater.taskId === viewingTaskId ? progressHighWater.pct : 0;
  proc.progFill.style.width = `${resumePct}%`;
  proc.progFill.classList.remove("is-complete");
  if (proc.progPct) proc.progPct.textContent = `${Math.round(resumePct)}%`;
  if (proc.progLabel) proc.progLabel.textContent = "排队中";
  animStageIdx = 0;
  metricTarget = { activity: 0.1, cpu: 0, network_kbps: 0, kind: "idle", detail: "排队中…", facts: [], title_snip: "" };
  metricDisplay = { ...metricTarget };
  resetStageMetas();
  updateStageMetas({ detail: "排队中" }, 0, "pending");

  const canvas = proc.canvas;
  const effector = window.ProcEffect?.create(canvas, { wrap: canvas.parentElement });
  if (!effector) return;

  let running = true;
  let loadRaf = 0;

  function tickLoad() {
    loadRaf = 0;
    if (!running) return;
    const liveProc = getProcElements();
    if (!liveProc?.canvas || liveProc.canvas !== canvas) {
      loadRaf = requestAnimationFrame(tickLoad);
      return;
    }
    lerpMetrics();
    const step = PIPELINE_STEPS[animStageIdx]?.key || "parse";
    effector.setLoad(window.ProcEffect.computeLoad(metricDisplay, step));
    loadRaf = requestAnimationFrame(tickLoad);
  }

  function onVis() {
    if (!running) return;
    if (!document.hidden && !loadRaf) loadRaf = requestAnimationFrame(tickLoad);
  }

  document.addEventListener("visibilitychange", onVis);
  loadRaf = requestAnimationFrame(tickLoad);

  procAnim = () => {
    running = false;
    document.removeEventListener("visibilitychange", onVis);
    if (loadRaf) cancelAnimationFrame(loadRaf);
    loadRaf = 0;
    effector.destroy();
  };
}

function stopProcessingAnim(flash = false) {
  const proc = getProcElements();
  if (flash && proc?.panel) {
    proc.panel.classList.remove("flash");
    void proc.panel.offsetWidth;
    proc.panel.classList.add("flash");
    proc.stages.forEach((s) => s.classList.remove("active"));
    proc.stages.forEach((s) => s.classList.add("done"));
    if (proc.progFill) {
      proc.progFill.style.width = "100%";
      proc.progFill.classList.add("is-complete");
    }
    if (proc.progPct) proc.progPct.textContent = "100%";
    if (proc.progLabel) proc.progLabel.textContent = "已完成";
  }
  stopProcAnim();
}


function startPolling(taskId) {
  stopPolling();
  pollingTaskId = taskId;
  // 仅当用户尚未在看别的任务时，才把结果区切到该任务
  if (viewingTaskId == null || viewingTaskId === taskId) {
    viewingTaskId = taskId;
    currentTaskId = taskId;
  }
  let cardRendered = viewingTaskId === taskId;

  const tick = async () => {
    if (pollingTaskId !== taskId) return;
    try {
      const task = await fetchTask(taskId);
      if (pollingTaskId !== taskId) return;

      const viewingThis = viewingTaskId === taskId;
      if (viewingThis) {
        if (!cardRendered) {
          renderTask(task);
          cardRendered = true;
          scheduleProcAnim(task);
        } else if (task.status === "done" || task.status === "failed") {
          renderTask(task, task.status === "done");
        } else {
          patchTaskStatus(task);
        }
      }

      // 进度面板始终跟随后台任务
      updateProcProgress(task);
      hooks.onPoll(task);

      if (task.status === "done") {
        stopPolling();
        hooks.setSubmitDisabled(false);
        hooks.onTaskDone();
      if (viewingTaskId === taskId) {
        stopProcessingAnim(true);
        hideStatus();
        setTimeout(() => openTaskModal(task, true), 380);
      } else {
          stopProcessingAnim(false);
          showStatus(`任务 #${taskId} 已完成（可在历史中查看）`);
        }
      } else if (task.status === "failed") {
        stopPolling();
        stopProcessingAnim(false);
        hooks.setSubmitDisabled(false);
        hooks.onTaskDone();
        const err = task.error_message || "任务失败，可点击重试";
        if (viewingTaskId === taskId) {
          showStatus(`失败：${err}`);
          renderTask(task);
        } else {
          showStatus(`任务 #${taskId} 失败：${err}`);
        }
      }
    } catch (err) {
      if (pollingTaskId !== taskId) return;
      if (isTransientFetchError(err)) {
        if (viewingTaskId === taskId) showStatus("连接中断，正在重试…");
        return;
      }
      stopPolling();
      stopProcessingAnim(false);
      hooks.setSubmitDisabled(false);
      showStatus(err.message || "轮询失败");
    }
  };

  tick();
  pollTimer = setInterval(tick, 800);
}

  async function handleRetryTask(task) {
    const taskId = task.id;
    const retryBtn = document.getElementById("modal-retry-btn");
    hooks.setSubmitDisabled(true);
    if (retryBtn) retryBtn.disabled = true;
    showStatus(shouldForceMediaRetry(task) ? "正在重新下载…" : "正在重新排队…");
    try {
      resetProgressHighWater(taskId);
      const next = await retryTask(taskId, task);
      renderTask(next);
      startPolling(next.id);
      const ahead = Number(next.queue_ahead) || 0;
      if (next.status === "pending" && ahead > 0) {
        showStatus(`已重新排队，前面还有 ${ahead} 个任务…`);
      } else if (taskIsActive(next.status)) {
        showStatus(`任务 #${taskId} 已开始处理…`);
      } else if (next.status === "failed") {
        showStatus(next.error_message ? `失败：${next.error_message}` : "重试失败");
      }
    } catch (err) {
      showStatus(err.message || "重试失败");
      hooks.setSubmitDisabled(false);
    } finally {
      if (retryBtn) retryBtn.disabled = false;
    }
  }

  function initTaskModalOnce() {
    bindDom();
    if (!taskModal || taskModal.dataset.bound) return;
    taskModal.dataset.bound = "1";
    taskModal.querySelectorAll("[data-modal-close]").forEach((el) => {
      el.addEventListener("click", closeTaskModal);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && taskModal && !taskModal.hidden) closeTaskModal();
    });
  }

  async function openById(taskId, ev) {
    if (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (typeof global.miniBurst === "function") global.miniBurst(ev.clientX, ev.clientY);
    }
    initTaskModalOnce();
    bindDom();
    try {
      const task = await fetchTask(taskId);
      viewingTaskId = task.id;
      currentTaskId = task.id;
      openTaskModal(task, true);
      if (taskIsActive(task.status)) {
        updateProcProgress(task);
        startPolling(task.id);
      }
    } catch (err) {
      showStatus(err.message || "加载失败");
    }
  }

  global.Vid2TaskModal = {
    configure,
    init: initTaskModalOnce,
    openById,
    openWithTask: (task, animate = false) => {
      initTaskModalOnce();
      bindDom();
      openTaskModal(task, animate);
    },
    close: closeTaskModal,
    fetchTask,
    subtitleToView,
    parseJsonResponse,
    startPolling,
    stopPolling,
    renderTask,
    taskIsActive,
    hasTranscript,
    taskTranscriptText,
    canDownloadVideo,
    copyToClipboard,
    triggerVideoDownload,
    historyTitleLabel,
    historyAuthorLabel,
    historyAvatarInner,
    SUBTITLES_API,
  };
})(window);
