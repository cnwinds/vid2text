const form = document.getElementById("settings-form");
const statusEl = document.getElementById("settings-status");

function showStatus(text, isErr = false) {
  if (!statusEl) return;
  statusEl.hidden = false;
  statusEl.textContent = text;
  statusEl.classList.toggle("is-error", isErr);
}

async function parseJson(res) {
  const text = await res.text();
  return { data: text ? JSON.parse(text) : {}, status: res.status };
}

function setFlag(id, on) {
  const el = document.getElementById(id);
  el.textContent = on ? "已配置" : "未配置";
  el.classList.toggle("is-on", on);
}

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024 ** 2) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 ** 3) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  return `${(v / 1024 ** 3).toFixed(2)} GB`;
}

function renderWorkCache(wc) {
  const el = document.getElementById("work-cache-summary");
  if (!el || !wc) return;
  const quota = fmtBytes(wc.quota_bytes);
  const used = fmtBytes(wc.used_bytes);
  const pct = wc.quota_bytes > 0 ? Math.min(100, (wc.used_bytes / wc.quota_bytes) * 100) : 0;
  const status = wc.enabled ? "已启用配额回收" : "配额回收已关闭";
  el.innerHTML = `
    <div class="work-cache-bar" role="progressbar" aria-valuenow="${pct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100">
      <div class="work-cache-fill" style="width:${pct.toFixed(1)}%"></div>
    </div>
    <p class="work-cache-meta">${used} / ${quota}（上限 ${wc.quota_gb} GB）· ${status}</p>`;
}

async function loadSettings() {
  const res = await adminFetch("/api/v1/settings");
  const { data, status } = await parseJson(res);
  if (status >= 400) {
    showStatus(data.detail || "加载失败", true);
    return;
  }
  setFlag("douyin-flag", data.douyin_cookies_set);
  setFlag("bilibili-flag", data.bilibili_cookies_set);
  setFlag("youtube-flag", data.youtube_cookies_set);
  setFlag("secret-flag", data.webhook_secret_set);
  document.getElementById("webhook-url").value = data.webhook_url || "";
  document.getElementById("scan-interval").value = data.default_scan_interval_sec || 2700;
  renderWorkCache(data.work_cache);
}

form?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    webhook_url: document.getElementById("webhook-url").value.trim(),
    default_scan_interval_sec: Number(document.getElementById("scan-interval").value) || 2700,
  };

  const dy = document.getElementById("douyin-cookies").value.trim();
  const bi = document.getElementById("bilibili-cookies").value.trim();
  const yt = document.getElementById("youtube-cookies").value.trim();
  const secret = document.getElementById("webhook-secret").value.trim();

  if (document.getElementById("clear-douyin").checked) body.douyin_cookies = "";
  else if (dy) body.douyin_cookies = dy;

  if (document.getElementById("clear-bilibili").checked) body.bilibili_cookies = "";
  else if (bi) body.bilibili_cookies = bi;

  if (document.getElementById("clear-youtube").checked) body.youtube_cookies = "";
  else if (yt) body.youtube_cookies = yt;

  if (document.getElementById("clear-secret").checked) body.webhook_secret = "";
  else if (secret) body.webhook_secret = secret;

  try {
    const res = await adminFetch("/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const { data, status } = await parseJson(res);
    if (status >= 400) throw new Error(data.detail || "保存失败");
    showStatus("已保存");
    document.getElementById("douyin-cookies").value = "";
    document.getElementById("bilibili-cookies").value = "";
    document.getElementById("youtube-cookies").value = "";
    document.getElementById("webhook-secret").value = "";
    document.getElementById("clear-douyin").checked = false;
    document.getElementById("clear-bilibili").checked = false;
    document.getElementById("clear-youtube").checked = false;
    document.getElementById("clear-secret").checked = false;
    await loadSettings();
  } catch (err) {
    showStatus(err.message || "保存失败", true);
  }
});

loadSettings();
