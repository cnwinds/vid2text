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
  processing: "处理中",
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

function historyTranscriptText(view) {
  return taskTranscriptText(view);
}

function hasHistoryTranscript(view) {
  return view.status === "done" && !!historyTranscriptText(view);
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
  return `
    <header class="td-hero">
      ${renderPlatformAvatarCol(task.platform, historyAvatarInner(task))}
      <div class="td-hero-body">
        <h3 class="td-hero-title">${escapeHtml(title)}</h3>
        <p class="td-hero-meta">
          <span class="td-hero-author">${escapeHtml(author)}</span>
          ${durationHtml}
          <span class="td-hero-task-id">#${escapeHtml(String(task.id))}</span>
        </p>
      </div>
    </header>`;
}

function buildTaskActionBarHtml(task) {
  const transcript = taskTranscriptText(task);
  const parts = [];
  if (task.status === "done" && transcript) {
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
  if (taskIsActive(task.status)) return "";
  const text = taskTranscriptText(task);
  if (!text) {
    return `
      <section class="td-section td-section-empty">
        <p>暂无口播文稿${task.status === "failed" ? "（提取失败）" : ""}</p>
      </section>`;
  }
  return `
    <section class="td-section td-section-hero">
      <div class="td-section-head">
        <h4 class="td-section-title">口播文稿</h4>
        <span class="td-meta-pill">${text.length.toLocaleString()} 字</span>
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
  if (task.status !== "failed") return "";
  return `<div class="m-modal-actions"><button type="button" class="td-act td-act-danger" id="modal-retry-btn">${RETRY_SVG}<span>重新提取</span></button></div>`;
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
  const actionBarHtml = taskIsActive(task.status) ? "" : buildTaskActionBarHtml(task);
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
      global.miniBurst(e.clientX, e.clientY);
      handleRetryTask(task.id);
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
      global.miniBurst(e.clientX, e.clientY);
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
  const durationEl = inModal.querySelector(".td-hero-duration");
  const durationLabel = taskDurationLabel(task);
  if (durationEl && durationLabel) {
    durationEl.textContent = durationLabel;
  }

  const transcriptEl = inModal.querySelector(".td-transcript");
  const text = taskTranscriptText(task);
  if (transcriptEl && text) {
    transcriptEl.textContent = text;
    const pill = inModal.querySelector(".td-meta-pill");
    if (pill) pill.textContent = `${text.length.toLocaleString()} 字`;
  }

  if (task.status === "done" || task.status === "failed") {
    stopProcAnim();
    taskModalBody.innerHTML = buildTaskDetailHtml(task);
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
    raw_transcript: sub.raw || "",
    corrected_transcript: sub.corrected || "",
    error_message: data.error || "",
    progress_step: data.processing?.step || "",
    progress_notice: data.processing?.notice || "",
    progress_resume_from: data.processing?.resume_from || "",
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
  return subtitleToView(data);
}

async function retryTask(taskId) {
  const res = await fetch(`${SUBTITLES_API}/${taskId}/retry`, { method: "POST" });
  const { data, status } = await parseJsonResponse(res);
  if (status === 400 || status === 404) {
    throw new Error(data.detail || "重试失败");
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
  let activeIdx = -1;
  let metrics = null;

  if (progressHighWater.taskId !== task.id) {
    resetProgressHighWater(task.id);
  }

  if (status === "done") {
    activeIdx = PIPELINE_STEPS.length;
    metrics = { activity: 1, cpu: 0, network_kbps: 0, kind: "idle", detail: "完成", facts: [] };
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
    metrics = { kind: "idle", activity: 0.1, cpu: 0, network_kbps: 0, detail: "排队中", facts: [] };
    setMetricTarget(metrics);
  }

  if (activeIdx >= 0 && status !== "done") {
    activeIdx = Math.max(progressHighWater.stageIdx, activeIdx);
    progressHighWater.stageIdx = activeIdx;
  }

  animStageIdx = Math.max(0, activeIdx);

  if (proc?.stages) {
    proc.stages.forEach((el, i) => {
      el.classList.remove("active", "done");
      if (status === "done" || i < activeIdx) {
        el.classList.add("done");
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
  if (proc?.progLabel) {
    proc.progLabel.textContent = status === "done" ? "已完成" : stepLabel;
  }

  if (status === "pending" || status === "processing") {
    let statusText = `任务 #${task.id} · ${stepLabel}…`;
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
  const ctx = canvas.getContext("2d", { alpha: false });
  function fitCanvas() {
    const wrap = canvas.parentElement;
    const cw = canvas.clientWidth || wrap?.clientWidth || 300;
    const ch = canvas.clientHeight || 160;
    const w = Math.max(2, Math.floor(cw));
    const h = Math.max(2, Math.floor(ch));
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
  }
  fitCanvas();
  ctx.fillStyle = "#05070a";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const onResize = () => fitCanvas();
  window.addEventListener("resize", onResize);

  const FRAME_MS = 1000 / 30;
  let particles = [];
  let spawnAcc = 0;
  let running = true;
  let raf = 0;
  let last = 0;

  const STAGE_RGB = {
    parse: { r: 90, g: 200, b: 190 },
    fetch_meta: { r: 70, g: 180, b: 220 },
    fetch_subtitle: { r: 120, g: 160, b: 255 },
    download: { r: 70, g: 224, b: 201 },
    extract_audio: { r: 154, g: 140, b: 255 },
    stt: { r: 255, g: 111, b: 168 },
    correct: { r: 255, g: 160, b: 120 },
  };

  /** 各阶段主指标：network=带宽 kbps，cpu=占用%，activity=通用活动度 */
  const STAGE_METRIC = {
    parse: "activity",
    fetch_meta: "network",
    fetch_subtitle: "network",
    download: "network",
    extract_audio: "cpu",
    stt: "cpu",
    correct: "network",
  };
  const NET_FULL_KBPS = 3200;
  const INTENSITY_FLOOR = 0.16;

  function norm01(value, full) {
    return Math.min(1, Math.max(0, (Number(value) || 0) / full));
  }

  function applyIntensityFloor(raw) {
    return INTENSITY_FLOOR + Math.min(1, Math.max(0, raw)) * (1 - INTENSITY_FLOOR);
  }

  function stageDrive() {
    const step = PIPELINE_STEPS[animStageIdx]?.key || "parse";
    const metricKind = STAGE_METRIC[step] || "activity";
    const cpuNorm = norm01(metricDisplay.cpu, 100);
    const netNorm = norm01(metricDisplay.network_kbps, NET_FULL_KBPS);
    const act = Math.min(1, Math.max(0, Number(metricDisplay.activity) || 0.2));

    let raw = 0;
    if (metricKind === "network") {
      raw = netNorm;
    } else if (metricKind === "cpu") {
      raw = cpuNorm;
    } else {
      raw = act * 0.5;
    }

    // 指标尚未上报时（如刚进入步骤），用 activity 托底，避免完全静止
    if (raw < 0.04) {
      raw = Math.max(raw, act * 0.28);
    }

    const intensity = applyIntensityFloor(raw);
    const base = STAGE_RGB[step] || STAGE_RGB.parse;
    const boost = 0.7 + intensity * 0.65;
    return {
      intensity,
      raw,
      metricKind,
      color: {
        r: Math.min(255, (base.r * boost) | 0),
        g: Math.min(255, (base.g * boost) | 0),
        b: Math.min(255, (base.b * boost) | 0),
      },
    };
  }

  function drawTadpole(ctx, x, y, angle, tailPhase, size, alpha, c, glowAlpha) {
    const headR = size * 0.42;
    const tailLen = size * 1.05;
    const tailW = Math.max(1.2, size * 0.16);
    const wag = Math.sin(tailPhase * 6.8) * size * 0.14;

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);

    if (glowAlpha > 0) {
      ctx.beginPath();
      ctx.fillStyle = `rgba(${c.r},${c.g},${c.b},${glowAlpha})`;
      ctx.arc(0, -headR * 0.35, headR * 1.5, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.beginPath();
    ctx.moveTo(0, headR * 0.08);
    ctx.quadraticCurveTo(wag, tailLen * 0.48, wag * 0.4, tailLen);
    ctx.strokeStyle = `rgba(${c.r},${c.g},${c.b},${alpha * 0.82})`;
    ctx.lineWidth = tailW;
    ctx.lineCap = "round";
    ctx.stroke();

    ctx.beginPath();
    ctx.fillStyle = `rgba(${c.r},${c.g},${c.b},${alpha})`;
    ctx.arc(0, -headR * 0.38, headR, 0, Math.PI * 2);
    ctx.fill();

    ctx.beginPath();
    ctx.fillStyle = `rgba(255,255,255,${alpha * 0.14})`;
    ctx.arc(-headR * 0.2, -headR * 0.52, headR * 0.22, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
  }

  function spawnSoft(drive) {
    const { intensity, color } = drive;
    const W = canvas.width;
    const H = canvas.height;
    const cap = Math.round(4 + intensity * 14);
    if (particles.length >= cap) return;
    const room = cap - particles.length;
    const batch = Math.min(room, 1 + (intensity * 2) | 0);
    const riseBase = 0.18 + intensity * 1.95;

    for (let i = 0; i < batch; i++) {
      const swayAmp = (0.35 + Math.random() * (0.45 + intensity * 3.0)) * 0.22;
      particles.push({
        x: Math.random() * W,
        y: H + 4 + Math.random() * 10,
        vy: -(riseBase + Math.random() * (0.3 + intensity * 0.85)),
        drift: (Math.random() - 0.5) * (0.025 + intensity * 0.08),
        r: (0.55 + Math.random() * (0.5 + intensity * 1.95)) * 5,
        swayAmp,
        swayFreq: 0.45 + Math.random() * (1.0 + intensity * 1.5),
        swayPhase: Math.random() * Math.PI * 2,
        wobbleAmp: (0.1 + Math.random() * (0.1 + intensity * 0.32)) * 0.22,
        wobbleFreq: 1.3 + Math.random() * 2.4,
        phase: Math.random() * Math.PI * 2,
        tailPhase: Math.random() * Math.PI * 2,
        vxSmooth: (Math.random() - 0.5) * 0.08,
        vySmooth: -(0.4 + Math.random() * 0.35),
        c: color,
      });
    }
  }

  function frame(now) {
    raf = 0;
    if (!running) return;
    if (document.hidden) {
      raf = requestAnimationFrame(frame);
      return;
    }
    const liveProc = getProcElements();
    if (!liveProc?.canvas || liveProc.canvas !== canvas) {
      raf = requestAnimationFrame(frame);
      return;
    }
    if (now - last < FRAME_MS) {
      raf = requestAnimationFrame(frame);
      return;
    }
    last = now;

    lerpMetrics();
    fitCanvas();
    const W = canvas.width;
    const H = canvas.height;
    if (W < 2 || H < 2) {
      raf = requestAnimationFrame(frame);
      return;
    }

    const drive = stageDrive();
    const { intensity } = drive;

    ctx.fillStyle = `rgba(5,7,9,${0.1 + intensity * 0.34})`;
    ctx.fillRect(0, 0, W, H);

    const spawnEvery = Math.max(1, Math.round(9 - intensity * 7));
    spawnAcc += 1;
    if (spawnAcc >= spawnEvery) {
      spawnAcc = 0;
      spawnSoft(drive);
    }

    let write = 0;
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      p.phase += (0.028 + intensity * 0.05) * (0.75 + p.swayFreq * 0.25);
      p.tailPhase += 0.11 + intensity * 0.14;
      const sway =
        Math.sin(p.phase * p.swayFreq + p.swayPhase) * p.swayAmp * (0.35 + intensity * 0.35);
      const wobble =
        Math.sin(p.phase * p.wobbleFreq + p.swayPhase * 1.7) *
        p.wobbleAmp *
        (0.25 + intensity * 0.35);
      const dx = p.drift + sway + wobble;
      const dy = p.vy * (0.65 + intensity * 0.55);
      p.vxSmooth = p.vxSmooth * 0.78 + dx * 0.22;
      p.vySmooth = p.vySmooth * 0.78 + dy * 0.22;
      p.x += dx;
      p.y += dy;
      const size = p.r * (0.85 + intensity * 0.45);
      if (p.y < -size * 1.6) continue;

      const baseAlpha = 0.38 + intensity * 0.62;
      const fadeTop = size * 1.8;
      let alpha = baseAlpha;
      if (p.y < fadeTop) {
        alpha = baseAlpha * Math.max(0, p.y / fadeTop);
        if (alpha <= 0.01) continue;
      }

      const swimTilt = Math.sin(p.phase * p.swayFreq + p.swayPhase) * 0.42;
      const angle = Math.atan2(p.vxSmooth, -p.vySmooth) + swimTilt;
      const glowAlpha = intensity > 0.45 ? alpha * 0.2 * intensity : 0;

      drawTadpole(ctx, p.x, p.y, angle, p.tailPhase, size, alpha, p.c, glowAlpha);
      particles[write++] = p;
    }
    particles.length = write;

    raf = requestAnimationFrame(frame);
  }

  function onVis() {
    if (!running) return;
    if (document.hidden) {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    } else if (!raf) {
      last = 0;
      raf = requestAnimationFrame(frame);
    }
  }

  document.addEventListener("visibilitychange", onVis);
  raf = requestAnimationFrame(frame);

  procAnim = () => {
    running = false;
    window.removeEventListener("resize", onResize);
    document.removeEventListener("visibilitychange", onVis);
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    particles.length = 0;
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

  async function handleRetryTask(taskId) {
    hooks.setSubmitDisabled(true);
    showStatus("正在重新排队…");
    try {
      const task = await retryTask(taskId);
      renderTask(task);
      startPolling(task.id);
    } catch (err) {
      showStatus(err.message);
      hooks.setSubmitDisabled(false);
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
      if (taskIsActive(task.status)) startPolling(task.id);
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
    startPolling,
    stopPolling,
    renderTask,
    taskIsActive,
  };
})(window);
