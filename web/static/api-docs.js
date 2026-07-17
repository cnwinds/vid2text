/* API docs page — background particles (lightweight) */
(function initBg() {
  const canvas = document.getElementById("bgCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let w, h;
  function resize() {
    w = canvas.width = window.innerWidth;
    h = canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener("resize", resize);
  const n = Math.min(80, Math.round((w * h) / 20000));
  const pts = Array.from({ length: n }, () => ({
    x: Math.random() * w,
    y: Math.random() * h,
    r: 0.8 + Math.random() * 1.5,
    vy: -(0.04 + Math.random() * 0.12),
    ph: Math.random() * 6.28,
  }));
  function tick() {
    ctx.clearRect(0, 0, w, h);
    for (const p of pts) {
      p.ph += 0.02;
      p.y += p.vy;
      if (p.y < 0) p.y = h;
      ctx.beginPath();
      ctx.fillStyle = `rgba(70,224,201,${0.1 + 0.1 * Math.sin(p.ph)})`;
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();

function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "已复制";
    setTimeout(() => {
      btn.textContent = orig;
    }, 1200);
  });
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatExample(example, contentType) {
  if (contentType === "text/plain" || typeof example === "string") {
    return String(example);
  }
  return JSON.stringify(example, null, 2);
}

function statusClass(status) {
  if (status >= 200 && status < 300) return "s2xx";
  if (status === 202) return "s202";
  if (status === 422) return "s422";
  return "s4xx";
}

function renderParamTable(title, params) {
  if (!params || !params.length) return "";
  const rows = params
    .map(
      (p) => `<tr>
        <td><code>${escapeHtml(p.name)}</code></td>
        <td>${escapeHtml(p.type)}</td>
        <td>${p.required ? "是" : "否"}</td>
        <td>${escapeHtml(p.description)}</td>
      </tr>`
    )
    .join("");
  return `
    <div class="api-endpoint-section">
      <h4>${escapeHtml(title)}</h4>
      <table class="api-param-table">
        <thead><tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderResponseBlock(ep, resp, index) {
  const exampleText = formatExample(resp.example, resp.content_type);
  const blockId = `${ep.id}-resp-${resp.status}-${index}`;
  return `
    <div class="api-response-block">
      <div class="api-response-head">
        <span class="api-status-code ${statusClass(resp.status)}">${resp.status}</span>
        <strong>${escapeHtml(resp.title)}</strong>
        <span class="api-content-type">${escapeHtml(resp.content_type)}</span>
      </div>
      <p class="api-response-desc">${escapeHtml(resp.description)}</p>
      <pre class="api-code" id="${blockId}">${escapeHtml(exampleText)}</pre>
      <button type="button" class="copy-btn" data-copy-target="${blockId}">复制示例</button>
    </div>`;
}

function renderEndpoint(ep) {
  const method = ep.method.toLowerCase();
  const primary = ep.primary ? " api-endpoint-card-primary" : "";
  const curlId = `${ep.id}-curl`;
  const reqExampleId = `${ep.id}-req`;

  let requestSection = "";
  if (ep.request_body) {
    requestSection += renderParamTable("请求体字段", ep.request_body);
    if (ep.request_example) {
      requestSection += `
        <p class="api-section-label">请求示例</p>
        <pre class="api-code" id="${reqExampleId}">${escapeHtml(JSON.stringify(ep.request_example, null, 2))}</pre>
        <button type="button" class="copy-btn" data-copy-target="${reqExampleId}">复制 JSON</button>`;
    }
  }
  requestSection += renderParamTable("路径参数", ep.request_path);
  requestSection += renderParamTable("Query 参数", ep.request_query);

  if (ep.curl) {
    requestSection += `
      <p class="api-section-label">cURL</p>
      <pre class="api-code" id="${curlId}">${escapeHtml(ep.curl)}</pre>
      <button type="button" class="copy-btn" data-copy-target="${curlId}">复制 cURL</button>`;
  }

  const responses = (ep.responses || [])
    .map((resp, i) => renderResponseBlock(ep, resp, i))
    .join("");

  return `
    <article class="api-endpoint-card${primary}" id="${ep.id}">
      <header class="api-endpoint-header">
        <span class="api-method ${method}">${escapeHtml(ep.method)}</span>
        <div>
          <h3>${escapeHtml(ep.path)}</h3>
          <p class="api-endpoint-summary">${escapeHtml(ep.summary)}</p>
        </div>
      </header>
      <p class="api-endpoint-desc">${escapeHtml(ep.description)}</p>
      ${requestSection}
      <div class="api-endpoint-section">
        <h4>返回值</h4>
        <div class="api-responses">${responses}</div>
      </div>
    </article>`;
}

function buildNav(endpoints) {
  const navList = document.getElementById("api-nav-list");
  if (!navList) return;
  navList.innerHTML = endpoints
    .map(
      (ep) => `<li>
        <a href="#${ep.id}">
          <span class="api-nav-method ${ep.method.toLowerCase()}">${ep.method}</span>
          <code>${escapeHtml(ep.path)}</code>
        </a>
      </li>`
    )
    .join("");

  navList.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const id = link.getAttribute("href").slice(1);
      const el = document.getElementById(id);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

function bindCopyButtons(root) {
  root.querySelectorAll(".copy-btn[data-copy-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-copy-target");
      const el = document.getElementById(id);
      if (el) copyText(el.textContent, btn);
    });
  });
}

(function initApiDocs() {
  const base = window.location.origin;
  const baseEl = document.getElementById("api-base-url");
  if (baseEl) baseEl.textContent = base;

  const schemaBtn = document.getElementById("copy-schema-btn");
  if (schemaBtn) {
    schemaBtn.addEventListener("click", () => {
      copyText(`${base}/api/v1/schema.json`, schemaBtn);
    });
  }

  const container = document.getElementById("api-endpoints");
  const mdEl = document.getElementById("api-doc-md");
  const mdBtn = document.getElementById("copy-md-btn");

  Promise.all([
    fetch("/api/v1/schema.json").then((r) => r.json()),
    fetch("/api/v1/docs.md").then((r) => r.text()),
  ])
    .then(([schema, markdown]) => {
      const endpoints = schema.page_endpoints || [];
      if (container) {
        container.innerHTML = endpoints.map(renderEndpoint).join("");
        bindCopyButtons(container);
      }
      buildNav(endpoints);
      if (mdEl) mdEl.textContent = markdown;
      if (mdBtn) mdBtn.addEventListener("click", () => copyText(markdown, mdBtn));
    })
    .catch(() => {
      if (container) container.textContent = "文档加载失败，请刷新或访问 /api/v1/docs.md";
      if (mdEl) mdEl.textContent = "文档加载失败";
    });
})();
