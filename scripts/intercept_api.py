"""Intercept Douyin API responses via Playwright."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

VIDEO_ID = "7639590279997132072"
URL = f"https://www.douyin.com/video/{VIDEO_ID}"
OUT = Path(__file__).resolve().parent.parent / "debug"


def main():
    OUT.mkdir(exist_ok=True)
    captured = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()

        def on_response(response):
            url = response.url
            if any(k in url for k in ("aweme", "iteminfo", "detail", "video")):
                try:
                    body = response.text()
                    captured.append({"url": url, "status": response.status, "body": body[:50000]})
                except Exception as e:
                    captured.append({"url": url, "status": response.status, "error": str(e)})

        page.on("response", on_response)
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(10000)
        browser.close()

    (OUT / "api_responses.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Captured {len(captured)} responses")
    for c in captured:
        print(c["status"], c["url"][:120])
        if "body" in c and "aweme" in c["body"].lower():
            try:
                data = json.loads(c["body"])
                print("  -> JSON keys:", list(data.keys())[:10])
            except Exception:
                pass


if __name__ == "__main__":
    main()
