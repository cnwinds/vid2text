/** 监控页 / 历史记录共用的平台标识与状态点 */

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
