/** 主页：提交表单 + 历史记录（详情弹窗见 task-modal.js） */
const form = document.getElementById("submit-form");
const urlInput = document.getElementById("url-input");
const submitBtn = document.getElementById("submit-btn");
const statusMsg = document.getElementById("status-msg");
const historyList = document.getElementById("history-list");
const refreshHistoryBtn = document.getElementById("refresh-history");

const TM = () => window.Vid2TaskModal;
const SUBTITLES_API = "/api/v1/subtitles";
const HISTORY_DISPLAY_LIMIT = 20;

const ICON_COPY =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

const ICON_DOWNLOAD =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';

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
  return uiEscapeHtml(str);
}

function histActionBtn(kind, title, iconSvg) {
  return `<button type="button" class="hist-act hist-act-${kind}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${iconSvg}</button>`;
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

function patchHistoryCard(task) {
  if (!historyList || !TM()) return;
  const block = historyList.querySelector(`.hist-card[data-task-id="${task.id}"]`);
  if (!block) return;

  applyHistCardStatus(block, task);

  const titleText = TM().historyTitleLabel(task);
  const shortTitle =
    titleText.length > 72 ? `${titleText.slice(0, 72)}…` : titleText;
  const titleEl = block.querySelector(".hist-card-title");
  if (titleEl) titleEl.textContent = shortTitle;

  const author = TM().historyAuthorLabel(task);
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
    wrap.innerHTML = renderPlatformAvatarCol(task.platform, TM().historyAvatarInner(task));
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

  if (!TM().hasTranscript(task)) return;
  let actions = block.querySelector(".hist-card-actions");
  if (!actions) {
    actions = document.createElement("div");
    actions.className = "hist-card-actions";
    block.querySelector(".hist-card-top")?.appendChild(actions);
  }
  if (!actions.querySelector(".hist-act-primary")) {
    actions.insertAdjacentHTML("afterbegin", histActionBtn("primary", "复制口播文稿", ICON_COPY));
    actions.querySelector(".hist-act-primary")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        const fresh = await TM().fetchTask(task.id);
        TM().copyToClipboard(TM().taskTranscriptText(fresh), e.currentTarget);
      } catch (err) {
        showStatus(err.message || "复制失败");
      }
    });
  }
}

function createHistoryBlock(view) {
  const plat = platformClass(view.platform);
  const block = document.createElement("article");
  block.className = `m-card hist-card ${histStatusClass(view.status, view)} ${plat}`;
  block.dataset.taskId = view.id;

  const titleText = TM().historyTitleLabel(view);
  const shortTitle =
    titleText.length > 72 ? `${escapeHtml(titleText.slice(0, 72))}…` : escapeHtml(titleText);
  const author = TM().historyAuthorLabel(view);
  const authorHtml = author
    ? author.length > 28
      ? `${escapeHtml(author.slice(0, 28))}…`
      : escapeHtml(author)
    : `<span class="hist-card-author-muted">未知播主</span>`;

  const transcriptText = TM().taskTranscriptText(view);
  const actionBtns = [];
  if (TM().hasTranscript(view)) {
    actionBtns.push(histActionBtn("primary", "复制口播文稿", ICON_COPY));
  }
  if (TM().canDownloadVideo(view.status)) {
    actionBtns.push(histActionBtn("ghost", "下载视频", ICON_DOWNLOAD));
  }
  const actionsHtml = actionBtns.length
    ? `<div class="hist-card-actions">${actionBtns.join("")}</div>`
    : "";

  const durationLabel = taskDurationLabel(view);
  const durationHtml = durationLabel
    ? `<span class="hist-card-duration">${escapeHtml(durationLabel)}</span>`
    : "";
  const publishedLabel = taskPublishedLabel(view);
  const publishedHtml = publishedLabel
    ? `<time class="hist-card-published" datetime="${escapeHtml(view.published_at || "")}">${escapeHtml(publishedLabel)}</time>`
    : "";
  const likeLabel = taskLikeLabel(view);
  const likeHtml = likeLabel
    ? `<span class="hist-card-like" title="点赞">${escapeHtml(likeLabel)}</span>`
    : "";

  block.innerHTML = `
    <div class="m-card-shell hist-card-shell">
      <div class="hist-card-top">
        ${renderPlatformAvatarCol(view.platform, TM().historyAvatarInner(view))}
        <div class="hist-card-main">
          <strong class="m-card-name hist-card-title">${shortTitle}</strong>
          <div class="hist-card-meta">
            ${histCardStatusHtml(view)}
            <span class="hist-card-author">${authorHtml}</span>
            ${publishedHtml}
            ${likeHtml}
            ${durationHtml}
            <span class="hist-card-task-id">#${escapeHtml(String(view.id))}</span>
          </div>
        </div>
        ${actionsHtml}
      </div>
    </div>`;

  block.addEventListener("click", (e) => TM()?.openById(view.id, e));

  block.querySelector(".hist-act-primary")?.addEventListener("click", (e) => {
    e.stopPropagation();
    TM().copyToClipboard(transcriptText, e.currentTarget);
  });

  block.querySelector(".hist-act-ghost")?.addEventListener("click", (e) => {
    e.stopPropagation();
    TM().triggerVideoDownload(view.id, e.currentTarget);
  });

  block.querySelector(".m-avatar-img")?.addEventListener("error", (e) => {
    e.currentTarget.remove();
  });

  return block;
}

let historyRefreshTimer = null;
let historyListSnapshotCache = "";

function historyListSnapshot(items) {
  return JSON.stringify(
    items.map((item) => {
      const v = TM().subtitleToView(item);
      return {
        id: v.id,
        status: v.status,
        progress_step: v.progress_step,
        title: v.title,
        author_name: v.author_name,
        duration_sec: v.duration_sec,
        has_transcript: TM().hasTranscript(v) ? 1 : 0,
      };
    })
  );
}

function patchHistoryListFromViews(views) {
  if (!historyList) return false;
  const cards = [...historyList.querySelectorAll(".hist-card")];
  if (cards.length !== views.length) return false;
  const byId = new Map(cards.map((c) => [c.dataset.taskId, c]));
  for (const view of views) {
    if (!byId.has(String(view.id))) return false;
    patchHistoryCard(view);
  }
  return true;
}

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
      await loadHistory({ silent: true });
    } catch {
      /* ignore */
    }
  }, 6000);
}

async function loadHistory(options = {}) {
  const silent = options.silent === true;
  if (!TM()) return;
  try {
    const res = await fetch(`${SUBTITLES_API}?limit=${HISTORY_DISPLAY_LIMIT}`);
    const { data, status } = await TM().parseJsonResponse(res);
    if (status !== 200) {
      if (!silent) historyList.innerHTML = '<div class="m-empty"><p>加载失败</p></div>';
      return;
    }

    const items = data.items || [];
    const snap = historyListSnapshot(items);
    const views = items.map((item) => TM().subtitleToView(item));

    if (silent) {
      if (patchHistoryListFromViews(views)) {
        historyListSnapshotCache = snap;
        return;
      }
      if (historyListSnapshotCache === snap && historyList.querySelector(".hist-card")) {
        return;
      }
    }

    historyListSnapshotCache = snap;
    historyList.innerHTML = "";

    if (!items.length) {
      historyList.innerHTML = `
        <div class="m-empty m-empty-lg">
          <div class="m-empty-icon" aria-hidden="true">◌</div>
          <p>暂无记录</p>
          <span>提交视频链接后，提取结果会出现在这里</span>
        </div>`;
      return;
    }

    for (const view of views) {
      historyList.appendChild(createHistoryBlock(view));
    }
    if (historyHasActiveCards()) scheduleHistoryAutoRefresh();
  } catch {
    if (!silent) historyList.innerHTML = '<div class="m-empty"><p>加载失败</p></div>';
  }
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
    const { data, status } = await TM().parseJsonResponse(res);
    if (status === 400) throw new Error(data.detail || "提交失败");
    if (status === 429) {
      const msg = data.detail || "当前已有进行中的提取任务，请等待完成";
      if (data.active_id) {
        showStatus(`${msg}（#${data.active_id}）`);
        TM().startPolling(data.active_id);
      } else {
        showStatus(msg);
      }
      submitBtn.disabled = false;
      return;
    }

    const task = TM().subtitleToView(data);
    const cached = data.cached;
    TM().renderTask(task);

    if (task.status === "done") {
      showStatus(cached ? "命中缓存，直接返回已有结果" : "已完成");
      submitBtn.disabled = false;
      TM().renderTask(task, true);
      loadHistory();
      urlInput.value = "";
    } else if (task.status === "failed") {
      showStatus(task.error_message ? `失败：${task.error_message}` : "任务失败，可点击重试");
      submitBtn.disabled = false;
    } else {
      TM().startPolling(task.id);
    }
  } catch (err) {
    showStatus(err.message);
    submitBtn.disabled = false;
  }
});

refreshHistoryBtn?.addEventListener("click", (e) => {
  miniBurst(e.clientX, e.clientY);
  historyList?.querySelectorAll(".hist-card").forEach((el, i) => {
    el.style.transition = "opacity .25s ease";
    el.style.opacity = "0.35";
    setTimeout(() => {
      el.style.opacity = "1";
    }, 120 + i * 40);
  });
  loadHistory();
});

submitBtn?.addEventListener("click", (e) => {
  if (!submitBtn.disabled) miniBurst(e.clientX, e.clientY);
});

(function initHeroWave() {
  const hero = document.getElementById("heroWave");
  if (!hero) return;
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  hero.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const N = 12;
  const bases = Array.from({ length: N }, () => 0.25 + Math.random() * 0.55);
  const phases = Array.from({ length: N }, () => Math.random() * Math.PI * 2);
  const speeds = Array.from({ length: N }, () => 0.7 + Math.random() * 0.6);
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
      let r;
      let g;
      let b;
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

  start();
  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });
})();

(function initBgCanvas() {
  const canvas = document.getElementById("bgCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) return;
  const MAX = 28;
  let w = 0;
  let h = 0;
  let raf = 0;
  let last = 0;
  const FRAME_MS = 1000 / 30;

  function resize() {
    w = canvas.width = window.innerWidth;
    h = canvas.height = window.innerHeight;
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
    for (const p of particles) {
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

if (window.Vid2TaskModal) {
  Vid2TaskModal.configure({
    onStatus: (text) => showStatus(text),
    onStatusHide: hideStatus,
    onTaskDone: () => loadHistory(),
    onPoll: (task) => patchHistoryCard(task),
    setSubmitDisabled: (disabled) => {
      submitBtn.disabled = !!disabled;
    },
  });
  Vid2TaskModal.init();
}

loadHistory();
