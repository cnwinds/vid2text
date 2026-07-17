"""Quick test: fetch Douyin video page with Playwright."""
import json
import re
import sys

from playwright.sync_api import sync_playwright

VIDEO_ID = "7639590279997132072"
URL = f"https://www.douyin.com/video/{VIDEO_ID}"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)
        html = page.content()
        title = page.title()
        print("TITLE:", title)

        # Try RENDER_DATA
        for pattern in [
            r'<script id="RENDER_DATA" type="application/json">([^<]+)</script>',
            r'window\._ROUTER_DATA\s*=\s*(\{.*?\});',
            r'"desc"\s*:\s*"([^"]{10,})"',
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                print(f"MATCH {pattern[:40]}...:", m.group(1)[:500])

        # Check for subtitle fields
        for kw in ["subtitle", "caption", "desc", "transcript", "aweme_detail"]:
            if kw in html:
                print(f"Found keyword: {kw}")

        # Try API response interception
        api_data = []

        def handle_response(response):
            if "aweme" in response.url or "detail" in response.url:
                try:
                    api_data.append((response.url, response.status()))
                except Exception:
                    pass

        page.on("response", handle_response)
        page.reload(wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        print("API calls:", api_data[:10])

        browser.close()


if __name__ == "__main__":
    main()
