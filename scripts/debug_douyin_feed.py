#!/usr/bin/env python3
"""Debug Douyin author feed interception."""

from __future__ import annotations

from douyin_to_text.author_feed import _cookie_header_to_playwright
from douyin_to_text.author_models import AuthorProfile
from douyin_to_text.video_fetcher import _launch_chromium
from playwright.sync_api import sync_playwright
from web import db
from web.monitor_service import cookies_for_platform


def main() -> None:
    db.init_db()
    cookies = cookies_for_platform("douyin")
    print("cookie_len", len(cookies))
    pw = _cookie_header_to_playwright(cookies, ".douyin.com")
    print("pw_cookies", len(pw))
    print("names", [c["name"] for c in pw[:12]])

    monitors = db.list_monitors(limit=5)
    mon = next((m for m in monitors if m["platform"] == "douyin"), None)
    if not mon:
        print("no douyin monitor")
        return
    author = AuthorProfile(
        platform="douyin",
        author_key=mon["author_key"],
        profile_url=mon.get("profile_url")
        or f"https://www.douyin.com/user/{mon['author_key']}",
        author_name=mon.get("author_name") or "",
    )
    print("author", author.author_key, author.profile_url)

    urls: list[tuple[int, str]] = []
    post_hits: list = []

    with sync_playwright() as p:
        browser = _launch_chromium(p, headless=True)
        context = browser.new_context(
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        if pw:
            context.add_cookies(pw)
        page = context.new_page()

        def on_response(resp) -> None:
            u = resp.url
            if "aweme" in u:
                urls.append((resp.status, u[:200]))
            if "aweme/post" in u or "aweme/v1/web/aweme/post" in u:
                try:
                    data = resp.json() if resp.status == 200 else None
                    post_hits.append(
                        {
                            "status": resp.status,
                            "keys": list(data.keys()) if isinstance(data, dict) else None,
                            "aweme_n": len((data or {}).get("aweme_list") or [])
                            if isinstance(data, dict)
                            else 0,
                            "status_code": (data or {}).get("status_code")
                            if isinstance(data, dict)
                            else None,
                            "status_msg": (data or {}).get("status_msg")
                            if isinstance(data, dict)
                            else None,
                        }
                    )
                except Exception as exc:
                    post_hits.append({"status": resp.status, "err": str(exc)})

        page.on("response", on_response)
        print("goto...")
        page.goto(author.profile_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(6000)
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(4000)
        print("title", page.title())
        print("url", page.url)
        body = page.locator("body").inner_text()
        print("body_snip", repr(body[:250]))
        browser.close()

    print("aweme responses", len(urls))
    for s, u in urls[:40]:
        print(s, u)
    print("post_hits", post_hits)


if __name__ == "__main__":
    main()
