"""Parse aweme detail via Playwright response wait."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

VIDEO_ID = "7639590279997132072"
URL = f"https://www.douyin.com/video/{VIDEO_ID}"
OUT = Path(__file__).resolve().parent.parent / "debug"


def main():
    OUT.mkdir(exist_ok=True)
    detail = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="zh-CN").new_page()

        with page.expect_response(
            lambda r: "aweme/v1/web/aweme/detail" in r.url and r.status == 200,
            timeout=90000,
        ) as resp_info:
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)

        resp = resp_info.value
        data = resp.json()
        detail = data.get("aweme_detail", {})
        browser.close()

    (OUT / "aweme_detail.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("desc:", detail.get("desc", ""))
    print("duration:", detail.get("duration"))
    video = detail.get("video", {})
    urls = video.get("play_addr", {}).get("url_list", [])
    print("video urls:", urls[:1])

    for key in sorted(detail.keys()):
        if "sub" in key.lower() or "caption" in key.lower() or "text" in key.lower():
            print(f"{key}:", str(detail[key])[:400])

    for key in sorted(video.keys()):
        if "sub" in key.lower() or "caption" in key.lower():
            print(f"video.{key}:", str(video[key])[:400])


if __name__ == "__main__":
    main()
