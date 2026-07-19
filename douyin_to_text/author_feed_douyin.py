"""抖音作者作品列表。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from douyin_to_text.author_feed_cookies import cookie_header_to_playwright
from douyin_to_text.author_models import AuthorFeedPage, AuthorProfile, FeedVideo

logger = logging.getLogger(__name__)


def _pick_douyin_avatar(user: dict[str, Any]) -> str:
    if not user:
        return ""
    for key in ("avatar_larger", "avatar_medium", "avatar_thumb", "avatar_168x168"):
        thumb = user.get(key) or {}
        if isinstance(thumb, dict):
            urls = thumb.get("url_list") or []
            if urls and urls[0]:
                return str(urls[0])
    return ""



def _stats_from_aweme(aweme: dict[str, Any]) -> tuple[int, int, int, int, int]:
    stat = aweme.get("statistics") or {}
    like = int(stat.get("digg_count") or stat.get("admire_count") or 0)
    comment = int(stat.get("comment_count") or 0)
    play = int(stat.get("play_count") or stat.get("view_count") or 0)
    share = int(stat.get("share_count") or stat.get("forward_count") or 0)
    collect = int(stat.get("collect_count") or 0)
    return like, comment, play, share, collect



def _awemes_to_videos(aweme_list: list[dict[str, Any]], *, limit: int) -> list[FeedVideo]:
    videos: list[FeedVideo] = []
    seen: set[str] = set()
    for aweme in aweme_list:
        aid = str(aweme.get("aweme_id") or aweme.get("awemeId") or "")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        create_time = aweme.get("create_time")
        pub = ""
        if create_time:
            try:
                pub = datetime.fromtimestamp(int(create_time), tz=timezone.utc).isoformat()
            except Exception:
                pub = str(create_time)
        like, comment, play, share, collect = _stats_from_aweme(aweme)
        videos.append(
            FeedVideo(
                video_id=aid,
                url=f"https://www.douyin.com/video/{aid}",
                title=str(aweme.get("desc") or "")[:200],
                published_at=pub,
                like_count=like,
                comment_count=comment,
                play_count=play,
                share_count=share,
                collect_count=collect,
            )
        )
        if len(videos) >= limit:
            break
    return videos



def _douyin_fetch_post_page(page: Any, sec_uid: str, max_cursor: str, count: int) -> dict[str, Any] | None:
    api = (
        "https://www.douyin.com/aweme/v1/web/aweme/post/"
        f"?device_platform=webapp&aid=6383&channel=channel_pc_web"
        f"&sec_user_id={sec_uid}&max_cursor={max_cursor or '0'}"
        f"&count={max(10, min(count, 20))}&publish_video_strategy_type=2"
    )
    try:
        body = page.evaluate(
            """async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                if (!r.ok) return null;
                return await r.json();
            }""",
            api,
        )
        return body if isinstance(body, dict) else None
    except Exception as exc:
        logger.warning("抖音 post API 失败 cursor=%s: %s", max_cursor, exc)
        return None



def collect_douyin_videos(
    author: AuthorProfile,
    *,
    target: int,
    cookies: str | None,
    start_cursor: str,
    headless: bool,
) -> tuple[list[FeedVideo], str, bool]:
    """单次 Playwright 会话内拉取最多 target 条作品。"""
    from playwright.sync_api import sync_playwright

    from douyin_to_text.video_fetcher import _launch_chromium

    if not (cookies or "").strip():
        raise RuntimeError("未配置抖音 Cookie，无法拉取作品列表。请到设置页粘贴后重试。")

    profile = author.profile_url or f"https://www.douyin.com/user/{author.author_key}"
    captured_first: list[dict[str, Any]] = []
    all_awemes: list[dict[str, Any]] = []
    next_cursor = start_cursor if str(start_cursor).isdigit() else "0"
    has_more = True
    nickname = author.author_name

    with sync_playwright() as p:
        browser = _launch_chromium(p, headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        pw_cookies = cookie_header_to_playwright(cookies or "", ".douyin.com")
        if pw_cookies:
            try:
                context.add_cookies(pw_cookies)
            except Exception as exc:
                logger.warning("注入抖音 Cookie 部分失败: %s", exc)
        page = context.new_page()

        def on_response(resp: Any) -> None:
            try:
                u = resp.url
                if "aweme/post" not in u:
                    return
                if resp.status != 200:
                    return
                body = resp.json()
                if isinstance(body, dict) and body.get("aweme_list") is not None:
                    captured_first.append(body)
            except Exception:
                return

        page.on("response", on_response)
        try:
            page.goto(profile, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(4500)
            try:
                page.mouse.wheel(0, 2200)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            if not captured_first:
                try:
                    page.mouse.wheel(0, 2800)
                except Exception:
                    pass
                page.wait_for_timeout(3500)
        except Exception as exc:
            browser.close()
            raise RuntimeError(f"打开抖音用户页失败: {exc}") from exc

        # 首页：优先用拦截结果；若指定了续扫 cursor 则直请求
        if start_cursor and str(start_cursor).isdigit() and start_cursor != "0":
            body = _douyin_fetch_post_page(page, author.author_key, start_cursor, 20)
            pages = [body] if body else []
        else:
            pages = list(captured_first)
            if not pages:
                body = _douyin_fetch_post_page(page, author.author_key, "0", 20)
                if body:
                    pages = [body]

        if not pages:
            browser.close()
            raise RuntimeError(
                "未能获取抖音作品列表。Cookie 可能已过期；请重新复制后重试「立即扫描」。"
            )

        while pages and len(all_awemes) < target:
            body = pages.pop(0)
            if not body:
                break
            alist = body.get("aweme_list") or []
            all_awemes.extend(alist)
            next_cursor = str(body.get("max_cursor") or "")
            has_more = bool(body.get("has_more")) and bool(next_cursor)
            page_user = body.get("user") or {}
            av_page = _pick_douyin_avatar(page_user)
            if av_page:
                author.avatar_url = av_page
            for aweme in alist:
                a = aweme.get("author") or {}
                if a.get("nickname"):
                    nickname = a.get("nickname")
                av = _pick_douyin_avatar(a)
                if av:
                    author.avatar_url = av
            if len(all_awemes) >= target or not has_more:
                break
            more = _douyin_fetch_post_page(page, author.author_key, next_cursor, 20)
            if not more:
                has_more = False
                break
            pages.append(more)

        browser.close()

    if nickname:
        author.author_name = str(nickname)
    videos = _awemes_to_videos(all_awemes, limit=target)
    return videos, (next_cursor if has_more else ""), bool(has_more)



def feed_douyin(
    author: AuthorProfile,
    *,
    cursor: str,
    limit: int,
    cookies: str | None,
    headless: bool,
) -> AuthorFeedPage:
    """单页兼容入口（供 fetch_author_feed）。"""
    videos, next_cursor, has_more = collect_douyin_videos(
        author,
        target=limit,
        cookies=cookies,
        start_cursor=cursor,
        headless=headless,
    )
    return AuthorFeedPage(
        author=author,
        videos=videos,
        next_cursor=next_cursor if has_more else "",
        has_more=has_more,
    )
