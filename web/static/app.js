const form = document.getElementById("submit-form");
const urlInput = document.getElementById("url-input");
const submitBtn = document.getElementById("submit-btn");
const statusMsg = document.getElementById("status-msg");
const taskModal = document.getElementById("task-modal");
const taskModalBody = document.getElementById("task-modal-body");
const taskModalFoot = document.getElementById("task-modal-foot");
const taskModalTitle = document.getElementById("task-modal-title");
const historyList = document.getElementById("history-list");
const refreshHistoryBtn = document.getElementById("refresh-history");

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

const SUBTITLES_API = "/api/v1/subtitles";
const HISTORY_DISPLAY_LIMIT = 20;

const RETRY_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-2.6-6.36M21 3v6h-6"/></svg>';

const ICON_COPY =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

const ICON_DOWNLOAD =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';

const ICON_EXTERNAL =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/></svg>';

/* ================= decorative: hero wave（单 canvas，避免几十个 DOM+阴影动画卡死） ================= */
(function initHeroWave() {
  const hero = document.getElementById("heroWave");
  if (!hero) return;
  hero.replaceChildren();
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  hero.appendChild(canvas);
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) return;

  const N = 48;
  const phases = Float32Array.from({ length: N }, () => Math.random() * Math.PI * 2);
  const speeds = Float32Array.from({ length: N }, () => 1.1 + Math.random() * 1.4);
  const bases = Float32Array.from({ length: N }, (_, i) => {
    const envelope = 0.4 + 0.6 * Math.sin((i / (N - 1)) * Math.PI);
    return (0.35 + Math.random() * 0.55) * envelope;
  });

  let w = 0;
  let h = 0;
  let raf = 0;
  let last = 0;
  const FRAME_MS = 1000 / 30;

  function resize() {
    const cw = Math.max(1, Math.floor(hero.clientWidth));
    const ch = Math.max(1, Math.floor(hero.clientHeight || 56));
    if (cw === w && ch === h) return;
    w = cw;
    h = ch;
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = "100%";
    canvas.style.height = "100%";
  }

  function paint(now) {
    raf = 0;
    if (document.hidden) return;
    if (now - last < FRAME_MS) {
      raf = requestAnimationFrame(paint);
      return;
    }
    last = now;
    resize();
    ctx.clearRect(0, 0, w, h);
    const gap = w / N;
    const barW = Math.max(2, Math.min(4, gap * 0.45));
    for (let i = 0; i < N; i++) {
      phases[i] += 0.045 * speeds[i];
      const wave = 0.28 + 0.72 * (0.5 + 0.5 * Math.sin(phases[i]));
      const bh = Math.max(4, bases[i] * h * wave);
      const x = i * gap + (gap - barW) * 0.5;
      const y = (h - bh) * 0.5;
      const t = i / (N - 1);
      let r; let g; let b;
      if (t < 0.5) {
        const u = t * 2;
        r = 70 + (154 - 70) * u;
        g = 224 + (140 - 224) * u;
        b = 201 + (255 - 201) * u;
      } else {
        const u = (t - 0.5) * 2;
        r = 154 + (255 - 154) * u;
        g = 140 + (111 - 140) * u;
        b = 255 + (168 - 255) * u;
      }
      ctx.fillStyle = `rgba(${r | 0},${g | 0},${b | 0},0.72)`;
      ctx.fillRect(x, y, barW, bh);
    }
    raf = requestAnimationFrame(paint);
  }

  function start() {
    if (raf || document.hidden) return;
    last = 0;
    raf = requestAnimationFrame(paint);
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  resize();
  start();
  window.addEventListener("resize", () => {
    resize();
    start();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });
})();

/* ================= decorative: background particles（轻量，限帧，无连线） ================= */
(function initBgCanvas() {
  const canvas = document.getElementById("bgCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) return;
  let w = 0;
  let h = 0;
  let raf = 0;
  let last = 0;
  const FRAME_MS = 1000 / 24;
  const MAX = 36;

  function resize() {
    const nw = window.innerWidth;
    const nh = window.innerHeight;
    if (nw === w && nh === h) return;
    w = nw;
    h = nh;
    canvas.width = w;
    canvas.height = h;
  }

  function spawn() {
    return {
      x: Math.random() * Math.max(w, 1),
      y: Math.random() * Math.max(h, 1),
      r: 0.7 + Math.random() * 1.4,
      vy: -(0.04 + Math.random() * 0.1),
      vx: (Math.random() - 0.5) * 0.03,
      hue: Math.random() < 0.72 ? 0 : 1,
      phase: Math.random() * Math.PI * 2,
      speed: 0.55 + Math.random() * 0.7,
    };
  }

  resize();
  const particles = Array.from({ length: MAX }, spawn);

  function tick(now) {
    raf = 0;
    if (document.hidden) return;
    if (now - last < FRAME_MS) {
      raf = requestAnimationFrame(tick);
      return;
    }
    const dt = Math.min(2.5, (now - last) / FRAME_MS);
    last = now;
    resize();
    ctx.clearRect(0, 0, w, h);
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      p.phase += 0.012 * p.speed * dt;
      p.x += (p.vx + Math.sin(p.phase) * 0.02) * dt;
      p.y += p.vy * dt;
      if (p.y < -10) {
        p.x = Math.random() * w;
        p.y = h + 10;
        p.r = 0.7 + Math.random() * 1.4;
        p.vy = -(0.04 + Math.random() * 0.1);
        p.vx = (Math.random() - 0.5) * 0.03;
        p.hue = Math.random() < 0.72 ? 0 : 1;
      }
      const alpha = 0.1 + 0.12 * Math.sin(p.phase);
      ctx.beginPath();
      ctx.fillStyle =
        p.hue === 0
          ? `rgba(70,224,201,${alpha})`
          : `rgba(255,111,168,${alpha * 0.65})`;
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    raf = requestAnimationFrame(tick);
  }

  function start() {
    if (raf || document.hidden) return;
    last = 0;
    raf = requestAnimationFrame(tick);
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  start();
  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });
})();

function miniBurst(x, y, rgb = "70,224,201") {
  for (let i = 0; i < 9; i++) {
    const s = document.createElement("span");
    s.className = "spark";
    const angle = Math.random() * Math.PI * 2;
    const dist = 18 + Math.random() * 26;
    s.style.setProperty("--dx", Math.cos(angle) * dist + "px");
    s.style.setProperty("--dy", Math.sin(angle) * dist + "px");
    s.style.left = x + "px";
    s.style.top = y + "px";
    s.style.background = `rgba(${rgb},0.9)`;
    s.style.boxShadow = `0 0 6px rgba(${rgb},0.8)`;
    document.body.appendChild(s);
    setTimeout(() => s.remove(), 650);
  }
}

function buildHistWave(el, heights) {
  el.innerHTML = "";
  heights.forEach((v) => {
    const s = document.createElement("span");
    s.style.height = v + "px";
    el.appendChild(s);
  });
}

function randomWaveHeights() {
  return Array.from({ length: 5 }, () => 4 + Math.round(Math.random() * 13));
}

function statusBadgeClass(status) {
  if (status === "done") return "ok";
  if (status === "failed") return "fail";
  if (status === "processing") return "run";
  return "wait";
}

function histItemClass(status) {
  if (status === "done") return "is-ok";
  if (status === "failed") return "is-fail";
  if (status === "processing") return "is-run";
  return "is-wait";
}

function fieldBoxClass(text, emptyFallback) {
  const val = (text || "").trim();
  if (!val || val === emptyFallback) return "field-box empty";
  return "field-box filled";
}

function showStatus(text) {
  if (!statusMsg) return;
  statusMsg.hidden = false;
  statusMsg.textContent = text;
}

function hideStatus() {
  if (!statusMsg) return;
  statusMsg.hidden = true;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function histStatusClass(status, task) {
  if (status === "done") return "hist-done";
  if (status === "failed") return "hist-fail";
  if (status === "pending") return "hist-pending";
  if (task && isTaskStepQueued(task)) return "hist-pending";
  return "hist-processing";
}

function histCardStatusHtml(view) {
  if (view.status === "pending") {
    const label = taskRunningStatusLabel(view);
    return `<span class="hist-card-status hist-card-status-pending">${escapeHtml(label)}</span>`;
  }
  if (view.status === "processing") {
    const label = taskRunningStatusLabel(view);
    const queued = isTaskStepQueued(view);
    const cls = queued ? "hist-card-status-pending" : "hist-card-status-processing";
    return `<span class="hist-card-status ${cls}">${escapeHtml(label)}</span>`;
  }
  return "";
}

function applyHistCardStatus(block, view) {
  block.classList.remove("hist-done", "hist-fail", "hist-pending", "hist-processing");
  block.classList.add(histStatusClass(view.status, view));
  const html = histCardStatusHtml(view);
  const meta = block.querySelector(".hist-card-meta");
  let pill = block.querySelector(".hist-card-status");
  if (html) {
    if (pill) pill.outerHTML = html;
    else meta?.insertAdjacentHTML("afterbegin", html);
  } else if (pill) {
    pill.remove();
  }
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
      miniBurst(e.clientX, e.clientY);
      handleRetry(task.id);
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
      miniBurst(e.clientX, e.clientY);
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

function initTaskModal() {
  if (!taskModal) return;
  taskModal.querySelectorAll("[data-modal-close]").forEach((el) => {
    el.addEventListener("click", closeTaskModal);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !taskModal.hidden) closeTaskModal();
  });
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

function patchHistoryCard(task) {
  if (!historyList) return;
  const block = historyList.querySelector(`.hist-card[data-task-id="${task.id}"]`);
  if (!block) return;

  applyHistCardStatus(block, task);

  const titleText = historyTitleLabel(task);
  const shortTitle =
    titleText.length > 72 ? `${titleText.slice(0, 72)}…` : titleText;
  const titleEl = block.querySelector(".hist-card-title");
  if (titleEl) titleEl.textContent = shortTitle;

  const author = historyAuthorLabel(task);
  const authorEl = block.querySelector(".hist-card-author");
  if (authorEl) {
    authorEl.innerHTML = author
      ? author.length > 28
        ? `${escapeHtml(author.slice(0, 28))}…`
        : escapeHtml(author)
      : `<span class="hist-card-author-muted">未知播主</span>`;
  }

  const avatarCol = block.querySelector(".m-avatar-col");
  if (avatarCol) {
    const wrap = document.createElement("div");
    wrap.innerHTML = renderPlatformAvatarCol(task.platform, historyAvatarInner(task));
    avatarCol.replaceWith(wrap.firstElementChild);
  }

  const durationLabel = taskDurationLabel(task);
  let durationEl = block.querySelector(".hist-card-duration");
  if (durationLabel) {
    if (durationEl) durationEl.textContent = durationLabel;
    else {
      block.querySelector(".hist-card-meta")?.insertAdjacentHTML(
        "beforeend",
        `<span class="hist-card-duration">${escapeHtml(durationLabel)}</span>`
      );
    }
  }

  if (!hasTranscript(task)) return;
  let actions = block.querySelector(".hist-card-actions");
  if (!actions) {
    actions = document.createElement("div");
    actions.className = "hist-card-actions";
    block.querySelector(".hist-card-top")?.appendChild(actions);
  }
  if (!actions.querySelector(".hist-act-primary")) {
    actions.insertAdjacentHTML(
      "afterbegin",
      histActionBtn("primary", "复制口播文稿", ICON_COPY)
    );
    actions.querySelector(".hist-act-primary")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        const fresh = await fetchTask(task.id);
        copyToClipboard(taskTranscriptText(fresh), e.currentTarget);
      } catch (err) {
        showStatus(err.message || "复制失败");
      }
    });
  }
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
      patchHistoryCard(task);

      if (task.status === "done") {
        stopPolling();
        submitBtn.disabled = false;
        loadHistory();
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
        submitBtn.disabled = false;
        loadHistory();
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
      submitBtn.disabled = false;
      showStatus(err.message || "轮询失败");
    }
  };

  tick();
  pollTimer = setInterval(tick, 800);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) {
    urlInput.classList.remove("shake");
    void urlInput.offsetWidth;
    urlInput.classList.add("shake");
    urlInput.focus();
    return;
  }

  submitBtn.disabled = true;
  showStatus("提交中…");

  try {
    const res = await fetch(SUBTITLES_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const { data, status } = await parseJsonResponse(res);
    if (status === 400) throw new Error(data.detail || "提交失败");
    if (status === 429) {
      const msg = data.detail || "当前已有进行中的提取任务，请等待完成";
      if (data.active_id) {
        showStatus(`${msg}（#${data.active_id}）`);
        startPolling(data.active_id);
      } else {
        showStatus(msg);
      }
      submitBtn.disabled = false;
      return;
    }

    const task = subtitleToView(data);
    const cached = data.cached;
    renderTask(task);

    if (task.status === "done") {
      showStatus(cached ? "命中缓存，直接返回已有结果" : "已完成");
      submitBtn.disabled = false;
      renderTask(task, true);
      loadHistory();
      urlInput.value = "";
    } else if (task.status === "failed") {
      showStatus(task.error_message ? `失败：${task.error_message}` : "任务失败，可点击重试");
      submitBtn.disabled = false;
    } else {
      startPolling(task.id);
    }
  } catch (err) {
    showStatus(err.message);
    submitBtn.disabled = false;
  }
});

async function openHistoryDetail(taskId, ev) {
  if (ev) {
    ev.stopPropagation();
    miniBurst(ev.clientX, ev.clientY);
  }
  try {
    const task = await fetchTask(taskId);
    viewingTaskId = task.id;
    currentTaskId = task.id;
    openTaskModal(task, true);
    if (task.status === "pending" || task.status === "processing") {
      startPolling(task.id);
    }
  } catch (err) {
    showStatus(err.message || "加载失败");
  }
}

function createHistoryBlock(view) {
  const plat = platformClass(view.platform);
  const block = document.createElement("article");
  block.className = `m-card hist-card ${histStatusClass(view.status, view)} ${plat}`;
  block.dataset.taskId = view.id;

  const titleText = historyTitleLabel(view);
  const shortTitle =
    titleText.length > 72 ? `${escapeHtml(titleText.slice(0, 72))}…` : escapeHtml(titleText);
  const author = historyAuthorLabel(view);
  const authorHtml = author
    ? author.length > 28
      ? `${escapeHtml(author.slice(0, 28))}…`
      : escapeHtml(author)
    : `<span class="hist-card-author-muted">未知播主</span>`;

  const transcriptText = historyTranscriptText(view);
  const actionBtns = [];
  if (hasHistoryTranscript(view)) {
    actionBtns.push(histActionBtn("primary", "复制口播文稿", ICON_COPY));
  }
  if (canDownloadVideo(view.status)) {
    actionBtns.push(histActionBtn("ghost", "下载视频", ICON_DOWNLOAD));
  }
  const actionsHtml = actionBtns.length
    ? `<div class="hist-card-actions">${actionBtns.join("")}</div>`
    : "";

  const durationLabel = taskDurationLabel(view);
  const durationHtml = durationLabel
    ? `<span class="hist-card-duration">${escapeHtml(durationLabel)}</span>`
    : "";

  block.innerHTML = `
    <div class="m-card-shell hist-card-shell">
      <div class="hist-card-top">
        ${renderPlatformAvatarCol(view.platform, historyAvatarInner(view))}
        <div class="hist-card-main">
          <strong class="m-card-name hist-card-title">${shortTitle}</strong>
          <div class="hist-card-meta">
            ${histCardStatusHtml(view)}
            <span class="hist-card-author">${authorHtml}</span>
            ${durationHtml}
            <span class="hist-card-task-id">#${escapeHtml(String(view.id))}</span>
          </div>
        </div>
        ${actionsHtml}
      </div>
    </div>`;

  block.addEventListener("click", (e) => openHistoryDetail(view.id, e));

  block.querySelector(".hist-act-primary")?.addEventListener("click", (e) => {
    e.stopPropagation();
    copyToClipboard(transcriptText, e.currentTarget);
  });

  block.querySelector(".hist-act-ghost")?.addEventListener("click", (e) => {
    e.stopPropagation();
    triggerVideoDownload(view.id, e.currentTarget);
  });

  const avImg = block.querySelector(".m-avatar-img");
  if (avImg) {
    avImg.addEventListener("error", () => {
      avImg.remove();
    });
  }

  return block;
}

let historyRefreshTimer = null;

function historyHasActiveCards() {
  return !!historyList?.querySelector(".hist-pending, .hist-processing");
}

function scheduleHistoryAutoRefresh() {
  if (historyRefreshTimer) return;
  historyRefreshTimer = setInterval(async () => {
    if (!historyHasActiveCards()) {
      clearInterval(historyRefreshTimer);
      historyRefreshTimer = null;
      return;
    }
    try {
      await loadHistory();
    } catch {
      /* ignore */
    }
  }, 4000);
}

async function loadHistory() {
  try {
    const res = await fetch(`${SUBTITLES_API}?limit=${HISTORY_DISPLAY_LIMIT}`);
    const { data, status } = await parseJsonResponse(res);
    if (status !== 200) {
      historyList.innerHTML = '<div class="m-empty"><p>加载失败</p></div>';
      return;
    }
    historyList.innerHTML = "";

    if (!data.items?.length) {
      historyList.innerHTML = `
        <div class="m-empty m-empty-lg">
          <div class="m-empty-icon" aria-hidden="true">◌</div>
          <p>暂无记录</p>
          <span>提交视频链接后，提取结果会出现在这里</span>
        </div>`;
      return;
    }

    for (const item of data.items) {
      historyList.appendChild(createHistoryBlock(subtitleToView(item)));
    }
    if (historyHasActiveCards()) scheduleHistoryAutoRefresh();
  } catch {
    historyList.innerHTML = '<div class="m-empty"><p>加载失败</p></div>';
  }
}

async function handleRetry(taskId) {
  submitBtn.disabled = true;
  showStatus("正在重新排队…");
  try {
    const task = await retryTask(taskId);
    renderTask(task);
    startPolling(task.id);
  } catch (err) {
    showStatus(err.message);
    submitBtn.disabled = false;
  }
}

refreshHistoryBtn.addEventListener("click", (e) => {
  miniBurst(e.clientX, e.clientY);
  historyList.querySelectorAll(".hist-card").forEach((el, i) => {
    el.style.transition = "opacity .25s ease";
    el.style.opacity = "0.35";
    setTimeout(() => {
      el.style.opacity = "1";
    }, 120 + i * 40);
  });
  loadHistory();
});

submitBtn.addEventListener("click", (e) => {
  if (!submitBtn.disabled) miniBurst(e.clientX, e.clientY);
});

initTaskModal();
loadHistory();
