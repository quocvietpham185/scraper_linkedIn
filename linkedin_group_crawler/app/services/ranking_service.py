"""Ranking and post filtering logic."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from app.utils.datetime_utils import is_same_day, normalize_relative_time, parse_target_date


def compute_score(post: dict[str, Any]) -> int:
    """Compute post score from engagement values."""
    likes = int(post.get("likes", 0))
    comments = int(post.get("comments", 0))
    reposts = int(post.get("reposts") or post.get("repost") or 0)
    # Trọng số mới: Repost > Comment > Like
    return int(likes * 1.0 + comments * 2.0 + reposts * 3.0)


def enrich_and_filter_posts(
    posts: list[dict[str, Any]],
    target_date: str | None,
    crawl_time: datetime,
) -> tuple[list[dict[str, Any]], datetime.date]:
    """Normalize timestamps, compute score, and keep posts matching the target day."""

    target_day = parse_target_date(target_date, crawl_time)
    filtered_posts: list[dict[str, Any]] = []

    for post in posts:
        normalized_dt = _normalize_post_time(post, crawl_time)
        post["posted_at"] = normalized_dt.isoformat() if normalized_dt else None
        post["score"] = compute_score(post)
        if normalized_dt and is_same_day(normalized_dt, target_day):
            filtered_posts.append(post)

    return filtered_posts, target_day


def summarize_post_dates(posts: list[dict[str, Any]], crawl_time: datetime, *, limit: int = 12) -> str:
    """Return a compact debug summary of parsed post dates."""

    if not posts:
        return "Không parse được bài nào."

    lines: list[str] = []
    for index, post in enumerate(posts[:limit], start=1):
        raw = str(post.get("posted_at_raw") or "").strip() or "(trống)"
        normalized_dt = _normalize_post_time(post, crawl_time)
        normalized = normalized_dt.isoformat(timespec="minutes") if normalized_dt else "không parse được"
        lines.append(f"{index}. {raw} -> {normalized}")
    if len(posts) > limit:
        lines.append(f"... còn {len(posts) - limit} bài khác")
    return "\n".join(lines)


def pick_top_post(posts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the top post using score, then likes as tie-breaker."""

    if not posts:
        return None
    return max(posts, key=lambda post: (post.get("score", 0), post.get("likes", 0)))


def _first_post_time_raw(post: dict[str, Any]) -> str:
    for key in ("posted_at_raw", "day_up", "posted_at", "datetime", "timestamp", "text_date"):
        value = post.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _parse_absolute_datetime(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None

    if text.isdigit():
        value = int(text)
        if value > 10_000_000_000:
            value = value // 1000
        if value > 1_000_000_000:
            return datetime.fromtimestamp(value)

    try:
        iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(iso_text)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue

    return None


def _normalize_post_time(post: dict[str, Any], crawl_time: datetime) -> datetime | None:
    raw = _first_post_time_raw(post)
    if raw:
        absolute_dt = _parse_absolute_datetime(raw)
        if absolute_dt:
            return absolute_dt

        relative_dt = normalize_relative_time(raw, crawl_time)
        if relative_dt:
            return relative_dt

    return _decode_linkedin_id_datetime(post)


def _extract_linkedin_activity_id(value: Any) -> str:
    text = str(value or "")

    match = re.search(r"urn:li:groupPost:\d+-(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r"urn:li:activity:(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r"groupPost:\d+-(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


def _decode_linkedin_id_datetime(post: dict[str, Any]) -> datetime | None:
    for key in ("url_article", "post_url", "url", "group_url"):
        activity_id = _extract_linkedin_activity_id(post.get(key))
        if not activity_id:
            continue
        try:
            timestamp_ms = int(activity_id) >> 22
        except ValueError:
            continue
        if timestamp_ms >= 946684800000:
            return datetime.fromtimestamp(timestamp_ms / 1000)
    return None


def _parse_posted_at_datetime(post: dict[str, Any]) -> datetime | None:
    raw = post.get("posted_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def select_most_recent_posts(posts: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Chọn các bài có ``posted_at`` mới nhất; không suy được thời gian thì xếp cuối, giữ thứ tự crawl."""

    if limit <= 0 or not posts:
        return []

    annotated: list[tuple[datetime | None, int, dict[str, Any]]] = []
    for index, post in enumerate(posts):
        annotated.append((_parse_posted_at_datetime(post), index, post))

    with_dt = [(dt, ix, post) for dt, ix, post in annotated if dt is not None]
    without_dt = [post for dt, ix, post in annotated if dt is None]

    with_dt.sort(key=lambda item: item[0], reverse=True)

    picked: list[dict[str, Any]] = [post for _, _, post in with_dt[:limit]]
    remaining = limit - len(picked)
    if remaining > 0:
        picked.extend(without_dt[:remaining])
    return picked
