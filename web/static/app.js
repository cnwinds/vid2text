const form = document.getElementById("submit-form");
const urlInput = document.getElementById("url-input");
const submitBtn = document.getElementById("submit-btn");
const statusMsg = document.getElementById("status-msg");
const resultSection = document.getElementById("result-section");
const resultsList = document.getElementById("resultsList");
const historyList = document.getElementById("history-list");
const refreshHistoryBtn = document.getElementById("refresh-history");
const procPanel = document.getElementById("procPanel");
const procCanvas = document.getElementById("procCanvas");
const progFill = document.getElementById("progFill");
const stages = document.querySelectorAll(".stage");

let pollTimer = null;
/** 结果区当前展示的任务 */
let viewingTaskId = null;
/** 后台轮询中的任务（可与 viewing 不同） */
let pollingTaskId = null;
/** @deprecated 兼容重试按钮，等同 viewingTaskId */
let currentTaskId = null;
let procAnim = null;
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
  stages.forEach((el) => {
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

  stages.forEach((el, i) => {
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
  metricDisplay.activity += (metricTarget.activity - metricDisplay.activity) * k;
  metricDisplay.cpu += (metricTarget.cpu - metricDisplay.cpu) * k;
  metricDisplay.network_kbps += (metricTarget.network_kbps - metricDisplay.network_kbps) * k;
  metricDisplay.kind = metricTarget.kind;
  metricDisplay.detail = metricTarget.detail;
}

const STATUS_LABEL = {
  pending: "排队中",
  processing: "处理中",
  done: "成功",
  failed: "失败",
};

const SUBTITLES_API = "/api/v1/subtitles";

const RETRY_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-2.6-6.36M21 3v6h-6"/></svg>';

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
  statusMsg.hidden = false;
  statusMsg.textContent = text;
}

function hideStatus() {
  statusMsg.hidden = true;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderTask(task, animate = false) {
  resultSection.hidden = false;
  viewingTaskId = task.id;
  currentTaskId = task.id;

  const title = task.title || task.video_url;
  const badgeCls = statusBadgeClass(task.status);
  const label = STATUS_LABEL[task.status] || task.status;

  const desc = task.description || "（无描述）";
  const raw = task.raw_transcript || "（暂无）";
  const corrected = task.corrected_transcript || "（暂无）";

  let errorHtml = "";
  if (task.status === "failed" && task.error_message) {
    errorHtml = `
      <div class="field">
        <div class="field-box error">
          <span class="who">错误</span>
          <span>${escapeHtml(task.error_message)}</span>
        </div>
      </div>`;
  }

  const retryHtml =
    task.status === "failed"
      ? `<button type="button" class="retry-btn" id="retry-btn">${RETRY_SVG} 重试</button>`
      : "";

  resultsList.innerHTML = `
    <div class="result-card${animate ? " enter" : ""}" data-task-id="${task.id}">
      <div class="result-head">
        <span class="platform-tag">${escapeHtml(task.platform)}</span>
        <span>·</span>
        <span>${escapeHtml(task.video_id)}</span>
        <span class="status-badge ${badgeCls}">${escapeHtml(label)}</span>
      </div>
      <p class="video-url">
        <a href="${escapeHtml(task.video_url)}" target="_blank" rel="noopener">${escapeHtml(title)}</a>
      </p>
      <div class="field">
        <span class="field-label">视频描述</span>
        <div class="${fieldBoxClass(task.description, "（无描述）")}">${escapeHtml(desc)}</div>
      </div>
      <div class="field">
        <span class="field-label">原始转录</span>
        <div class="${fieldBoxClass(task.raw_transcript, "（暂无）")}">${escapeHtml(raw)}</div>
      </div>
      <div class="field">
        <span class="field-label">修正后文本</span>
        <div class="${fieldBoxClass(task.corrected_transcript, "（暂无）")}">${escapeHtml(corrected)}</div>
      </div>
      ${errorHtml}
      ${retryHtml}
    </div>`;

  const retryBtn = document.getElementById("retry-btn");
  if (retryBtn) {
    retryBtn.addEventListener("click", (e) => {
      miniBurst(e.clientX, e.clientY);
      if (currentTaskId) handleRetry(currentTaskId);
    });
  }
}

function patchTaskStatus(task) {
  const card = resultsList.querySelector(".result-card");
  if (!card || card.dataset.taskId !== String(task.id)) {
    renderTask(task);
    return;
  }
  const badge = card.querySelector(".status-badge");
  if (badge) {
    badge.className = `status-badge ${statusBadgeClass(task.status)}`;
    badge.textContent = STATUS_LABEL[task.status] || task.status;
  }

  // 处理中即可更新标题 / 描述 / 原始转录（STT 完成后不必等全部结束）
  const titleEl = card.querySelector(".video-url a");
  if (titleEl && task.title) {
    titleEl.textContent = task.title;
  }

  const fields = card.querySelectorAll(".field");
  fields.forEach((field) => {
    const label = field.querySelector(".field-label")?.textContent?.trim();
    const box = field.querySelector(".field-box");
    if (!box || !label) return;
    if (label === "视频描述" && task.description) {
      box.className = fieldBoxClass(task.description, "（无描述）");
      box.textContent = task.description;
    } else if (label === "原始转录" && task.raw_transcript) {
      box.className = fieldBoxClass(task.raw_transcript, "（暂无）");
      box.textContent = task.raw_transcript;
    } else if (label === "修正后文本" && task.corrected_transcript) {
      box.className = fieldBoxClass(task.corrected_transcript, "（暂无）");
      box.textContent = task.corrected_transcript;
    }
  });
}

function subtitleToView(data) {
  const v = data.video || {};
  const sub = data.subtitle || {};
  let status = "processing";
  if (data.ready) status = "done";
  else if (data.error) status = "failed";
  else if (data.processing?.status === "pending") status = "pending";

  return {
    id: data.id,
    status,
    platform: v.platform,
    video_id: v.video_id,
    video_url: v.url,
    title: v.title,
    description: v.description,
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

function updateProcProgress(task) {
  const { progress_step: step, status } = task;
  let activeIdx = -1;
  let metrics = null;

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

  animStageIdx = Math.max(0, activeIdx);

  stages.forEach((el, i) => {
    el.classList.remove("active", "done");
    if (status === "done" || i < activeIdx) {
      el.classList.add("done");
    } else if (i === activeIdx) {
      el.classList.add("active");
    }
  });

  updateStageMetas(metrics, activeIdx, status);

  const total = PIPELINE_STEPS.length;
  const pct =
    status === "done"
      ? 100
      : activeIdx < 0
        ? 2
        : Math.min(95, ((activeIdx + 1) / total) * 92);
  progFill.style.width = `${pct}%`;

  if (status === "pending" || status === "processing") {
    const stepLabel =
      activeIdx >= 0 ? PIPELINE_STEPS[activeIdx]?.label : "排队中";
    let statusText = `任务 #${task.id} · ${stepLabel}…`;
    if (task.progress_notice) {
      statusText += ` · ${task.progress_notice.replace(/^resume:/, "")}`;
    }
    showStatus(statusText);
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  pollingTaskId = null;
  if (procAnim) {
    procAnim();
    procAnim = null;
  }
}

function startProcessingAnim() {
  // 只清动画循环，不要走 stopProcessingAnim（会误藏面板）
  if (procAnim) {
    procAnim();
    procAnim = null;
  }
  procPanel.hidden = false;
  stages.forEach((s) => s.classList.remove("active", "done"));
  progFill.style.width = "0%";
  animStageIdx = 0;
  metricTarget = { activity: 0.1, cpu: 0, network_kbps: 0, kind: "idle", detail: "排队中…", facts: [], title_snip: "" };
  metricDisplay = { ...metricTarget };
  resetStageMetas();
  updateStageMetas({ detail: "排队中" }, 0, "pending");

  const ctx = procCanvas.getContext("2d", { alpha: false });
  function fitCanvas() {
    const cw = procCanvas.clientWidth || 300;
    const ch = procCanvas.clientHeight || 160;
    if (procCanvas.width !== cw) procCanvas.width = cw;
    if (procCanvas.height !== ch) procCanvas.height = ch;
  }
  fitCanvas();
  const onResize = () => fitCanvas();
  window.addEventListener("resize", onResize);

  const MAX_PARTICLES = 40;
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

  function stageDrive() {
    const step = PIPELINE_STEPS[animStageIdx]?.key || "parse";
    const cpu = Number(metricDisplay.cpu) || 0;
    const net = Number(metricDisplay.network_kbps) || 0;
    const act = Math.min(1, Math.max(0, Number(metricDisplay.activity) || 0.2));
    let intensity = 0.25;

    if (step === "download") {
      intensity = Math.min(1, 0.15 + Math.min(net, 4000) / 1800);
    } else if (step === "stt" || step === "extract_audio") {
      intensity = Math.min(1, 0.15 + Math.min(cpu, 100) / 100);
    } else if (step === "fetch_meta" || step === "fetch_subtitle" || step === "correct") {
      intensity = Math.min(1, 0.2 + act * 0.75);
      if (metricDisplay.kind === "network" && net > 0) {
        intensity = Math.max(intensity, Math.min(1, 0.2 + Math.min(net, 4000) / 2500));
      }
      if (metricDisplay.kind === "cpu" && cpu > 0) {
        intensity = Math.max(intensity, Math.min(1, 0.2 + Math.min(cpu, 100) / 110));
      }
    } else {
      intensity = Math.min(1, 0.15 + act * 0.55);
    }

    const base = STAGE_RGB[step] || STAGE_RGB.parse;
    const boost = 1 + intensity * 0.12;
    return {
      intensity,
      color: {
        r: Math.min(255, (base.r * boost) | 0),
        g: Math.min(255, (base.g * boost) | 0),
        b: Math.min(255, (base.b * boost) | 0),
      },
    };
  }

  function spawnSoft(drive) {
    if (particles.length >= MAX_PARTICLES) return;
    const { intensity, color } = drive;
    const W = procCanvas.width;
    const H = procCanvas.height;
    const room = MAX_PARTICLES - particles.length;
    const density = Math.min(room, intensity > 0.7 ? 2 : 1);
    const riseBase = 0.32 + intensity * 1.1;

    for (let i = 0; i < density; i++) {
      particles.push({
        x: Math.random() * W,
        y: H + 4 + Math.random() * 6,
        vy: -(riseBase + Math.random() * 0.25),
        vx: (Math.random() - 0.5) * (0.06 + intensity * 0.22),
        r: 0.7 + Math.random() * (1 + intensity * 0.5),
        life: 1,
        fade: 0.008 + Math.random() * 0.004,
        sway: 0.01 + intensity * 0.012,
        phase: Math.random() * Math.PI * 2,
        c: color,
      });
    }
  }

  function frame(now) {
    raf = 0;
    if (!running) return;
    if (document.hidden || procPanel.hidden) return;
    if (now - last < FRAME_MS) {
      raf = requestAnimationFrame(frame);
      return;
    }
    last = now;

    lerpMetrics();
    fitCanvas();
    const W = procCanvas.width;
    const H = procCanvas.height;
    if (W < 2 || H < 2) {
      raf = requestAnimationFrame(frame);
      return;
    }

    const drive = stageDrive();
    const { intensity } = drive;

    ctx.fillStyle = "rgba(5,7,9,0.28)";
    ctx.fillRect(0, 0, W, H);

    // 强度高约每 3 帧补 1–2 个，低强度更疏
    const spawnEvery = intensity > 0.65 ? 3 : intensity > 0.35 ? 5 : 7;
    spawnAcc += 1;
    if (spawnAcc >= spawnEvery) {
      spawnAcc = 0;
      spawnSoft(drive);
    }

    let write = 0;
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      p.phase += 0.02 + intensity * 0.015;
      p.x += p.vx + Math.sin(p.phase) * p.sway;
      p.y += p.vy;
      p.life -= p.fade;
      if (p.life <= 0 || p.y < -12) continue;

      const heightFade = Math.max(0.15, Math.min(1, p.y / (H * 0.85)));
      const alpha = p.life * 0.78 * heightFade;
      ctx.beginPath();
      ctx.fillStyle = `rgba(${p.c.r},${p.c.g},${p.c.b},${alpha})`;
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
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
  if (procAnim) {
    procAnim();
    procAnim = null;
  }
  if (flash) {
    procPanel.classList.remove("flash");
    void procPanel.offsetWidth;
    procPanel.classList.add("flash");
    stages.forEach((s) => s.classList.remove("active"));
    stages.forEach((s) => s.classList.add("done"));
    progFill.style.width = "100%";
    setTimeout(() => {
      procPanel.hidden = true;
    }, 400);
  } else {
    procPanel.hidden = true;
  }
}

function startPolling(taskId) {
  stopPolling();
  pollingTaskId = taskId;
  // 仅当用户尚未在看别的任务时，才把结果区切到该任务
  if (viewingTaskId == null || viewingTaskId === taskId) {
    viewingTaskId = taskId;
    currentTaskId = taskId;
  }
  startProcessingAnim();
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
        submitBtn.disabled = false;
        loadHistory();
        if (viewingTaskId === taskId) {
          stopProcessingAnim(true);
          hideStatus();
          renderTask(task, true);
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
      stopProcessingAnim(true);
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

async function loadHistory() {
  try {
    const res = await fetch(`${SUBTITLES_API}?limit=20`);
    const { data, status } = await parseJsonResponse(res);
    if (status !== 200) {
      historyList.innerHTML = '<div class="empty-history">加载失败</div>';
      return;
    }
    historyList.innerHTML = "";

    if (!data.items?.length) {
      historyList.innerHTML = '<div class="empty-history">暂无记录</div>';
      return;
    }

    for (const item of data.items) {
      const view = subtitleToView(item);
      const row = document.createElement("div");
      row.className = `hist-item ${histItemClass(view.status)}`;
      row.dataset.taskId = view.id;

      const wave = document.createElement("div");
      wave.className = "hwave";
      buildHistWave(wave, randomWaveHeights());

      const tag = document.createElement("span");
      tag.className = "htag";
      tag.textContent = view.platform;

      const hurl = document.createElement("span");
      hurl.className = "hurl";
      hurl.textContent = view.title || view.video_url;
      hurl.title = view.video_url;

      const hstatus = document.createElement("span");
      hstatus.className = "hstatus";
      hstatus.textContent = STATUS_LABEL[view.status] || view.status;

      row.append(wave, tag, hurl, hstatus);

      if (view.status === "failed") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "history-retry";
        btn.textContent = "重试";
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          miniBurst(ev.clientX, ev.clientY);
          handleRetry(view.id);
        });
        row.appendChild(btn);
      }

      row.addEventListener("click", async () => {
        const task = await fetchTask(view.id);
        // 切换查看目标；后台轮询中的其他任务不抢占结果区
        viewingTaskId = task.id;
        currentTaskId = task.id;
        renderTask(task, true);
        resultSection.scrollIntoView({ behavior: "smooth" });
        if (task.status === "pending" || task.status === "processing") {
          // 查看进行中的任务时，进度与结果都跟它对齐
          startPolling(task.id);
        }
        // 若点的是已完成记录，保留原有 polling（进度条继续显示转换中任务）
      });

      historyList.appendChild(row);
    }
  } catch {
    historyList.innerHTML = '<div class="empty-history">加载失败</div>';
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
  historyList.querySelectorAll(".hist-item").forEach((el, i) => {
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

loadHistory();
