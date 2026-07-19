/** 监控页 / 历史记录共用的平台标识与状态点 */

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

function uiEscapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function platformLabel(p) {
  return { douyin: "抖音", bilibili: "B站", youtube: "YouTube" }[p] || p;
}

function platformClass(p) {
  return { douyin: "plat-douyin", bilibili: "plat-bili", youtube: "plat-yt" }[p] || "";
}

function parseProgressMetrics(raw) {
  if (!raw) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) return raw;
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw);
    } catch {
      return {};
    }
  }
  return {};
}

/** processing 状态下是否在等待某步骤的资源池空位（非真正执行中） */
function isTaskStepQueued(task) {
  if (!task || task.status !== "processing") return false;
  const m = parseProgressMetrics(task.progress_metrics);
  return Boolean(m.queued_step || String(m.detail || "").includes("排队等待"));
}

/** 各 pipeline 步骤进行中的短标签（历史卡片 / 进度条） */
const STEP_RUNNING_LABEL = {
  parse: "解析中",
  fetch_meta: "获取信息中",
  fetch_subtitle: "获取字幕中",
  download: "下载中",
  extract_audio: "提取音轨中",
  stt: "语音识别中",
  correct: "修正中",
};

function taskActiveStepKey(task) {
  if (!task) return "";
  const m = parseProgressMetrics(task.progress_metrics);
  if (task.status === "processing" && isTaskStepQueued(task)) {
    return String(m.queued_step || m.step || task.progress_step || "").trim();
  }
  return String(task.progress_step || m.step || "").trim();
}

/** 任务当前状态短文案：排队中 / 下载中 / 等待 · 语音识别中 等 */
function taskRunningStatusLabel(task) {
  if (!task) return "";
  if (task.status === "pending") {
    const ahead = Number(task.queue_ahead) || 0;
    return ahead > 0 ? `排队 · 前 ${ahead}` : "排队中";
  }
  if (task.status !== "processing") return "";
  if (isTaskStepQueued(task)) {
    const m = parseProgressMetrics(task.progress_metrics);
    if (m.detail && String(m.detail).includes("排队等待")) {
      return String(m.detail).replace("排队等待 · ", "等待 · ");
    }
    const qs = String(m.queued_step || "").trim();
    if (qs && STEP_RUNNING_LABEL[qs]) {
      return `等待 · ${STEP_RUNNING_LABEL[qs]}`;
    }
    return "排队等待";
  }
  const key = taskActiveStepKey(task);
  if (key && STEP_RUNNING_LABEL[key]) return STEP_RUNNING_LABEL[key];
  return "处理中";
}

/** 历史/监控卡片外框状态 class */
function histStatusClass(status, task) {
  if (status === "done") return "hist-done";
  if (status === "failed") return "hist-fail";
  if (status === "pending") return "hist-pending";
  if (task && isTaskStepQueued(task)) return "hist-pending";
  return "hist-processing";
}

/** 历史/监控卡片内状态胶囊（进行中任务） */
function histCardStatusHtml(view) {
  if (!view || !view.status) return "";
  if (view.status === "pending") {
    const label = view.task_id ? taskRunningStatusLabel(view) : "待入队";
    return `<span class="hist-card-status hist-card-status-pending">${uiEscapeHtml(label)}</span>`;
  }
  if (view.status === "processing") {
    const label = taskRunningStatusLabel(view);
    const queued = isTaskStepQueued(view);
    const cls = queued ? "hist-card-status-pending" : "hist-card-status-processing";
    return `<span class="hist-card-status ${cls}">${uiEscapeHtml(label)}</span>`;
  }
  return "";
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

function taskDurationLabel(task) {
  const direct = Number(task?.duration_sec);
  if (direct > 0) return fmtDurationSec(direct);
  const m = parseProgressMetrics(task?.progress_metrics);
  if (Number(m.duration_sec) > 0) return fmtDurationSec(m.duration_sec);
  return "";
}

/** 作品发布时间（详情/卡片共用） */
function fmtPublishedAt(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(iso);
  }
}

function taskPublishedLabel(task) {
  return fmtPublishedAt(task?.published_at);
}

function fmtEngagementCount(n) {
  const v = Number(n) || 0;
  if (v <= 0) return "";
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function taskLikeLabel(task) {
  return fmtEngagementCount(task?.like_count);
}

function taskCommentLabel(task) {
  return fmtEngagementCount(task?.comment_count);
}

/** 监控作品条目 → 与历史卡片共用的 view 结构 */
function monitorVideoToView(video, monitor) {
  const hasTask = Boolean(video?.task_id);
  const metrics = parseProgressMetrics(video?.task_progress_metrics);
  return {
    id: video?.task_id || null,
    task_id: video?.task_id || null,
    status: hasTask ? video.task_status || "pending" : "pending",
    platform: video?.platform || monitor?.platform || "",
    video_id: video?.video_id || "",
    video_url: video?.video_url || "",
    title: video?.title || "",
    author_name: video?.task_author_name || monitor?.author_name || "",
    avatar_url: video?.task_avatar_url || monitor?.avatar_url || "",
    duration_sec: Number(video?.task_duration_sec) || 0,
    published_at: video?.published_at || "",
    like_count: Number(video?.like_count) || 0,
    progress_step: video?.task_progress_step || "",
    progress_metrics: metrics,
    queue_ahead: Number(video?.task_queue_ahead) || 0,
    error_message: video?.task_error || "",
  };
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
  return `<span class="m-plat-logo" title="${label}">${uiEscapeHtml(String(label).slice(0, 1))}</span>`;
}

function statusDotHtml(kind, title) {
  return `<span class="m-status-dot is-${kind}" title="${uiEscapeHtml(title)}"></span>`;
}

function authorInitials(name) {
  const s = String(name || "?").trim();
  if (!s) return "?";
  const c = [...s][0];
  return c.toUpperCase();
}

function renderAuthorAvatarInner(name, avatarUrl, fallbackSeed) {
  const initials = uiEscapeHtml(authorInitials(name || fallbackSeed));
  if (avatarUrl) {
    return `
      <img class="m-avatar-img" src="${uiEscapeHtml(avatarUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
      <span class="m-avatar-fallback" aria-hidden="true">${initials}</span>`;
  }
  return `<span class="m-avatar-fallback">${initials}</span>`;
}

function renderPlatformAvatarCol(platform, innerAvatarHtml) {
  const plat = platformClass(platform);
  return `
    <span class="m-avatar-col">
      <span class="m-avatar ${plat}">${innerAvatarHtml}</span>
      ${platformLogoHtml(platform)}
    </span>`;
}

/** 监控 / 设置 API：携带登录 Session Cookie（或页面注入的 Bearer Token） */
function adminFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = window.__ADMIN_API_TOKEN__;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...options, headers, credentials: "same-origin" });
}
