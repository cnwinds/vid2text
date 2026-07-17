const form = document.getElementById("submit-form");
const urlInput = document.getElementById("url-input");
const submitBtn = document.getElementById("submit-btn");
const statusMsg = document.getElementById("status-msg");
const resultSection = document.getElementById("result-section");
const historyList = document.getElementById("history-list");
const refreshHistoryBtn = document.getElementById("refresh-history");

let pollTimer = null;
let currentTaskId = null;

const STATUS_LABEL = {
  pending: "排队中",
  processing: "处理中",
  done: "完成",
  failed: "失败",
};

function showStatus(text) {
  statusMsg.hidden = false;
  statusMsg.textContent = text;
}

function hideStatus() {
  statusMsg.hidden = true;
}

function renderTask(task) {
  resultSection.hidden = false;
  document.getElementById("result-platform").textContent =
    `${task.platform} · ${task.video_id}`;
  const badge = document.getElementById("result-status");
  badge.textContent = STATUS_LABEL[task.status] || task.status;
  badge.className = `badge ${task.status}`;

  document.getElementById("result-title").textContent =
    task.title || task.video_url;

  document.getElementById("result-description").textContent =
    task.description || "（无描述）";
  document.getElementById("result-raw").textContent =
    task.raw_transcript || "（暂无）";
  document.getElementById("result-corrected").textContent =
    task.corrected_transcript || "（暂无）";

  const errEl = document.getElementById("result-error");
  if (task.status === "failed" && task.error_message) {
    errEl.hidden = false;
    errEl.textContent = task.error_message;
  } else {
    errEl.hidden = true;
  }
}

async function fetchTask(taskId) {
  const res = await fetch(`/api/task/${taskId}`);
  if (!res.ok) throw new Error("获取任务失败");
  return res.json();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling(taskId) {
  stopPolling();
  currentTaskId = taskId;

  const tick = async () => {
    try {
      const task = await fetchTask(taskId);
      renderTask(task);
      if (task.status === "done" || task.status === "failed") {
        stopPolling();
        submitBtn.disabled = false;
        hideStatus();
        loadHistory();
      } else {
        showStatus(`任务 #${taskId} ${STATUS_LABEL[task.status] || task.status}…`);
      }
    } catch (err) {
      stopPolling();
      submitBtn.disabled = false;
      showStatus(err.message);
    }
  };

  tick();
  pollTimer = setInterval(tick, 2000);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  submitBtn.disabled = true;
  showStatus("提交中…");

  try {
    const res = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "提交失败");
    }

    const { cached, task } = data;
    renderTask(task);

    if (task.status === "done") {
      showStatus(cached ? "命中缓存，直接返回已有结果" : "已完成");
      submitBtn.disabled = false;
      loadHistory();
    } else if (task.status === "failed") {
      showStatus("任务失败");
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
    const res = await fetch("/api/history?limit=20");
    const data = await res.json();
    historyList.innerHTML = "";

    if (!data.items.length) {
      historyList.innerHTML =
        '<li class="empty-history">暂无记录</li>';
      return;
    }

    for (const item of data.items) {
      const li = document.createElement("li");
      li.dataset.taskId = item.id;
      li.innerHTML = `
        <span class="history-title">${escapeHtml(item.title || item.video_url)}</span>
        <span class="history-meta">#${item.id} · ${item.platform} · ${STATUS_LABEL[item.status] || item.status}</span>
      `;
      li.addEventListener("click", async () => {
        const task = await fetchTask(item.id);
        renderTask(task);
        resultSection.scrollIntoView({ behavior: "smooth" });
        if (task.status === "pending" || task.status === "processing") {
          startPolling(task.id);
        }
      });
      historyList.appendChild(li);
    }
  } catch {
    historyList.innerHTML =
      '<li class="empty-history">加载失败</li>';
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

refreshHistoryBtn.addEventListener("click", loadHistory);
loadHistory();
