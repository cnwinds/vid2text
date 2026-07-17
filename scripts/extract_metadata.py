"""Extract Douyin video metadata via Playwright."""
import json
import re
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

VIDEO_ID = "7639590279997132072"
URL = f"https://www.douyin.com/video/{VIDEO_ID}"
OUT = Path(__file__).resolve().parent.parent / "debug"


def extract_render_data(html: str) -> dict | None:
    m = re.search(
        r'<script id="RENDER_DATA" type="application/json">([^<]+)</script>',
        html,
    )
    if not m:
        return None
    raw = urllib.parse.unquote(m.group(1))
    return json.loads(raw)


def find_keys(obj, target_keys: set, path=""):
    """Recursively find all values for target keys."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in target_keys:
                results.append((p, v))
            results.extend(find_keys(v, target_keys, p))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            results.extend(find_keys(item, target_keys, f"{path}[{i}]"))
    return results


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="zh-CN").new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)
        html = page.content()
        title = page.title()
        browser.close()

    data = extract_render_data(html)
    if data:
        (OUT / "render_data.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    targets = {"desc", "subtitle", "caption", "transcript", "text", "content"}
    found = find_keys(data or {}, targets) if data else []

    print("TITLE:", title)
    print("\n--- Found metadata keys ---")
    for path, val in found:
        preview = str(val)[:300]
        print(f"{path}: {preview}")

    # Look for video URL
    url_keys = {"play_addr", "download_addr", "playApi", "src", "video"}
    urls = find_keys(data or {}, url_keys) if data else []
    print("\n--- Video URL hints ---")
    for path, val in urls[:15]:
        print(f"{path}: {str(val)[:200]}")


if __name__ == "__main__":
    main()
