"""Author / feed shared models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FeedVideo:
    video_id: str
    url: str
    title: str = ""
    published_at: str = ""
    like_count: int = 0
    comment_count: int = 0
    play_count: int = 0
    share_count: int = 0
    collect_count: int = 0


@dataclass
class AuthorProfile:
    platform: str
    author_key: str
    author_name: str = ""
    profile_url: str = ""
    avatar_url: str = ""
    source_url: str = ""


@dataclass
class AuthorFeedPage:
    author: AuthorProfile
    videos: list[FeedVideo] = field(default_factory=list)
    next_cursor: str = ""
    has_more: bool = False
