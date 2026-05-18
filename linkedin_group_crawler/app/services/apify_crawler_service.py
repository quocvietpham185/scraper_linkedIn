"""Apify Actor client for LinkedIn group crawling.

The backend owns business logic. Apify only crawls and returns normalized posts.
Primary mode calls the in-house Actor. A third-party Actor can be enabled as an
emergency fallback with a separate env flag.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any, Literal, Optional

import httpx

from app.config import settings
from app.services.auth_service import build_session_state_path
from app.services.google_sheet_service import get_apify_token_from_settings_sheet
from app.utils.logger import get_logger

logger = get_logger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
_POLL_INTERVAL_SEC = 10
_POLL_MAX_ATTEMPTS = 360

ActorKind = Literal["own", "third_party"]

APIFY_SESSION_REJECTED_MESSAGE = (
    "Không crawl được do LinkedIn không chấp nhận session trên Apify.\n"
    "Vui lòng chạy bằng Playwright local VM hoặc đăng nhập lại profile.\n"
    "Cấu hình Apify nếu vẫn muốn thử: maxItems=20, scrollTimes=3, "
    "delayMinMs=5000, delayMaxMs=12000, maxConcurrency=1, maxRequestRetries=1."
)

APIFY_SESSION_REJECTED_ERROR_TYPES = {
    "SESSION_INVALID",
    "SESSION_INVALID_GLOBAL",
}


def extract_group_id_from_url(url: str) -> str:
    url = (url or "").strip()
    match = re.search(r"groups/(\d+)", url)
    if match:
        return match.group(1)
    raise ValueError(f"Khong tim thay Group ID trong URL: {url!r}")


def _resolve_apify_token(kind: ActorKind) -> str:
    """Resolve token per tier so own and third-party Actors can use different accounts."""

    sheet_token = get_apify_token_from_settings_sheet().strip()
    if kind == "own":
        return (
            (settings.apify_own_token or "").strip()
            or (settings.apify_token or "").strip()
            or sheet_token
        )
    return (
        (settings.apify_3rd_party_token or "").strip()
        or sheet_token
        or (settings.apify_token or "").strip()
    )


def _get_post_time_ms(post: dict[str, Any], now_ms: int) -> int:
    dt = post.get("posted_at") or post.get("day_up") or post.get("datetime") or post.get("date")
    if dt and dt not in ("null", "empty", ""):
        try:
            return int(datetime.fromisoformat(str(dt).replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            pass

    ts = post.get("timestamp")
    if ts and ts not in ("null", "empty", ""):
        try:
            raw = int(float(ts))
            return raw if raw > 10_000_000_000 else raw * 1000
        except Exception:
            pass

    text_date = str(post.get("posted_at_raw") or post.get("text_date") or "").strip().lower()
    min_match = re.match(r"^(\d+)\s*m$", text_date)
    hour_match = re.match(r"^(\d+)\s*h$", text_date)
    day_match = re.match(r"^(\d+)\s*d$", text_date)
    if min_match:
        return now_ms - int(min_match.group(1)) * 60_000
    if hour_match:
        return now_ms - int(hour_match.group(1)) * 3_600_000
    if day_match:
        return now_ms - int(day_match.group(1)) * 86_400_000
    return 0


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, str):
            value = re.sub(r"[^\d-]", "", value)
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_storage_state_for_actor(
    *,
    email: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any] | None:
    if not email and not session_id:
        return None

    candidate_paths: list[Any] = []
    if email:
        candidate_paths.append(build_session_state_path(session_id=None, email=email)[1])
    if session_id:
        candidate_paths.append(build_session_state_path(session_id=session_id, email=None)[1])

    state_path = next((path for path in candidate_paths if path.exists()), None)
    if state_path is None:
        logger.info(
            "No LinkedIn storage state found for Apify payload. Tried: %s",
            ", ".join(str(path) for path in candidate_paths),
        )
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not read LinkedIn storage state for Apify payload: %s", state_path, exc_info=True)
        return None

    cookies = state.get("cookies") if isinstance(state, dict) else None
    if not isinstance(cookies, list):
        return None
    if not any(cookie.get("name") == "li_at" and cookie.get("value") for cookie in cookies if isinstance(cookie, dict)):
        logger.info("LinkedIn storage state for Apify is missing li_at: %s", state_path)
        return None
    origins = state.get("origins") if isinstance(state, dict) else None
    logger.info(
        "Apify storage state loaded: path=%s cookies=%d origins=%d hasLiAt=true",
        state_path,
        len(cookies),
        len(origins) if isinstance(origins, list) else 0,
    )
    return state


def normalize_apify_post(post: dict[str, Any], original_url: str) -> dict[str, Any]:
    """Normalize own or third-party Actor output to the backend post shape."""

    post_url = (
        post.get("post_url")
        or post.get("url_article")
        or post.get("url")
        or post.get("postUrl")
        or post.get("activityUrl")
        or ""
    )
    reposts = post.get("reposts")
    if reposts is None:
        reposts = post.get("repost")
    if reposts is None:
        reposts = post.get("share")
    if reposts is None:
        reposts = post.get("shares")

    return {
        "author": str(post.get("author") or post.get("author_name") or post.get("actorName") or ""),
        "content": str(post.get("content") or post.get("text") or post.get("description") or ""),
        "likes": _to_int(post.get("likes") or post.get("likesCount") or post.get("reactionCount")),
        "comments": _to_int(post.get("comments") or post.get("commentsCount") or post.get("commentCount")),
        "reposts": _to_int(reposts),
        "post_url": str(post_url),
        "group_url": str(post.get("group_url") or post.get("url_groups") or post.get("groupUrl") or original_url),
        "group_name": str(post.get("group_name") or post.get("groupName") or ""),
        "member_count": _to_int(post.get("member_count") or post.get("members") or post.get("memberCount")),
        "posted_at_raw": str(post.get("posted_at_raw") or post.get("day_up") or post.get("text_date") or post.get("timestamp") or ""),
    }


def _extract_group_metadata(raw_posts: list[dict[str, Any]], group_url: str) -> tuple[str, int]:
    group_id = extract_group_id_from_url(group_url)
    group_name = f"Group {group_id}"
    member_count = 0

    for post in raw_posts:
        candidate_name = (
            post.get("group_name")
            or post.get("groupName")
            or post.get("group_title")
            or post.get("groupTitle")
            or post.get("title")
        )
        if candidate_name and group_name == f"Group {group_id}":
            group_name = str(candidate_name)

        candidate_members = (
            post.get("member_count")
            or post.get("members")
            or post.get("memberCount")
            or post.get("membersCount")
            or post.get("groupMembers")
            or post.get("groupMembersCount")
            or post.get("members")
            or post.get("totalMembers")
            or post.get("followerCount")
        )
        if candidate_members and member_count == 0:
            member_count = _to_int(candidate_members)

        if member_count > 0 and group_name != f"Group {group_id}":
            break

    return group_name, member_count


def _normalize_url_for_compare(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _first_actor_group_summary(summary: dict[str, Any] | None, group_url: str) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    groups = summary.get("groups")
    if not isinstance(groups, list):
        return {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        if _normalize_url_for_compare(str(item.get("groupUrl") or "")) == _normalize_url_for_compare(group_url):
            return item
    for item in groups:
        if isinstance(item, dict):
            return item
    return {}


def _actor_group_summary_is_failure(group_summary: dict[str, Any]) -> bool:
    status = str(group_summary.get("status") or "").strip().lower()
    if status and status not in {"success", "cache"}:
        return True
    return (
        bool(group_summary.get("authRequired"))
        or group_summary.get("reachedGroup") is False
    )


def _actor_summary_error_type(group_summary: dict[str, Any]) -> str:
    explicit = str(group_summary.get("errorType") or "").strip()
    if explicit:
        return explicit
    status = str(group_summary.get("status") or "").strip().lower()
    status_map = {
        "auth_required": "AUTH_REQUIRED",
        "session_invalid": "SESSION_INVALID",
        "session_invalid_global": "SESSION_INVALID",
        "group_no_access": "GROUP_NO_ACCESS",
        "empty_unverified": "EMPTY_UNVERIFIED_RESULT",
        "timeout": "TIMEOUT",
        "group_redirected": "GROUP_REDIRECTED",
    }
    return status_map.get(status, "CRAWL_ERROR")


def _group_raw_posts_by_group_url(raw_posts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for post in raw_posts:
        if not isinstance(post, dict):
            continue
        raw_group_url = str(post.get("group_url") or post.get("url_groups") or post.get("groupUrl") or "").strip()
        if not raw_group_url:
            continue
        grouped.setdefault(_normalize_url_for_compare(raw_group_url), []).append(post)
    return grouped


def _classify_apify_error(message: str) -> str:
    text = (message or "").lower()
    if any(token in text for token in ("group_no_access", "request to join", "not a member", "private group")):
        return "GROUP_NO_ACCESS"
    if any(token in text for token in ("session invalid", "session_invalid_global", "/uas/login", "uas/login")):
        return "SESSION_INVALID"
    if "final url: https://www.linkedin.com/" in text or "final url: /" in text:
        return "GROUP_REDIRECTED"
    if any(token in text for token in ("login", "checkpoint", "authwall", "requires login")):
        return "AUTH_REQUIRED"
    if "too many redirects" in text:
        return "TOO_MANY_REDIRECTS"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    if "navigating and changing the content" in text:
        return "PAGE_NAVIGATING"
    return "CRAWL_ERROR"


def _is_apify_session_rejected_error(error_type: str | None, message: str = "") -> bool:
    if error_type in APIFY_SESSION_REJECTED_ERROR_TYPES:
        return True
    return _classify_apify_error(message) in APIFY_SESSION_REJECTED_ERROR_TYPES


def _build_apify_proxy_configuration() -> dict[str, Any] | None:
    groups = [group for group in settings.apify_proxy_groups if str(group or "").strip()]
    country_code = (settings.apify_proxy_country_code or "").strip().upper()
    if not groups and not country_code:
        return None

    proxy_configuration: dict[str, Any] = {"useApifyProxy": True}
    if groups:
        proxy_configuration["groups"] = groups
    if country_code:
        proxy_configuration["countryCode"] = country_code
    return proxy_configuration


def _build_actor_account_id(email: Optional[str], session_id: Optional[str]) -> str:
    return (email or session_id or "default").strip()


def _humanize_apify_error(error_type: str | None, message: str) -> str:
    if _is_apify_session_rejected_error(error_type, message):
        return (
            f"{APIFY_SESSION_REJECTED_MESSAGE}\n"
            f"Chi tiết: {message}"
        )
    if error_type == "GROUP_NO_ACCESS":
        return (
            "Tai khoan LinkedIn khong co quyen xem group nay hoac group dang yeu cau join/duyet thanh vien. "
            f"Chi tiet: {message}"
        )
    if error_type == "TIMEOUT":
        return (
            "Apify Actor da vao duoc LinkedIn nhung bi timeout khi tai hoac parse trang. "
            "Nen thu lai voi batch nho hon, navigationTimeoutMs=30000, waitUntil=commit, "
            "va proxy residential sticky. "
            f"Chi tiet: {message}"
        )
    if error_type == "AUTH_REQUIRED":
        return (
            "LinkedIn yêu cầu đăng nhập/xác minh riêng khi mở group này trên Apify. "
            "Nếu /feed vẫn hợp lệ thì đây là lỗi theo group, không nhất thiết là hỏng toàn bộ session. "
            f"Chi tiết: {message}"
        )
    if error_type == "GROUP_REDIRECTED":
        return (
            "LinkedIn redirect group về trang chủ/login khi chạy trên Apify. "
            "Có thể account chưa có quyền xem group, group bị hạn chế, hoặc LinkedIn chặn bề mặt group trên môi trường Apify. "
            f"Chi tiết: {message}"
        )
    if error_type == "EMPTY_UNVERIFIED_RESULT":
        return (
            "Apify Actor không xác thực được nội dung group hoặc không parse được bài nào. "
            f"Chi tiết: {message}"
        )
    return message


def _build_actor_payload(
    *,
    group_url: str,
    kind: ActorKind,
    max_items: Optional[int],
    target_date: Optional[str],
    scroll_times: Optional[int],
    email: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    if kind == "third_party":
        return {
            "username": extract_group_id_from_url(group_url),
            "type": "group",
            "start": 1,
            "iterations": max(1, int((scroll_times or settings.apify_default_scroll_times + 1) / 2)),
        }

    payload = {
        "groupUrls": [group_url],
        # FIX bug #5: enable API pagination mode
        "mode": "api",
        "apiMode": True,
        "apiStart": 0,
        "iterations": max(6, int(scroll_times or 6)),
        "apiPageSize": 40,
        "continueWithoutPaginationToken": True,
        "maxItems": min(max(max_items or settings.apify_default_max_items or 250, 250), 500),
        "maxRetries": 5,
        "maxConsecutiveFailedPages": 3,
        "pageDelayMinMs": 500,
        "pageDelayMaxMs": 1500,
        "fetchTimeoutMs": 20000,
        # Session/identity
        "sessionId": session_id or "",
        "emailCrawl": email or "",
        "accountId": _build_actor_account_id(email, session_id),
        "stateStoreName": "linkedin-actor-state",
        "allowCacheFallback": False,
        "cacheTtlHours": 6,
        "groupStateTtlHours": 24,
        "sessionInvalidCooldownHours": 6,
        "targetDate": target_date or "",
        # Browser scroll params (kept for backward compat, not used in apiMode)
        "scrollTimes": min(scroll_times or settings.apify_default_scroll_times, 3),
        "delayMinMs": max(settings.apify_delay_min_ms, 5000),
        "delayMaxMs": max(settings.apify_delay_max_ms, 12000),
        "navigationTimeoutMs": 30000,
        "groupDelayMinMs": int(max(settings.apify_group_delay_min_sec, 0) * 1000),
        "groupDelayMaxMs": int(max(settings.apify_group_delay_max_sec, settings.apify_group_delay_min_sec, 0) * 1000),
        "maxConcurrency": 1,
        "maxRequestRetries": 1,
    }
    proxy_configuration = _build_apify_proxy_configuration()
    if proxy_configuration:
        payload["proxyConfiguration"] = proxy_configuration
    storage_state = _load_storage_state_for_actor(email=email, session_id=session_id)
    if storage_state:
        payload["storageStateJson"] = json.dumps(storage_state, ensure_ascii=False)
    return payload


def _build_own_actor_payload(
    *,
    group_urls: list[str],
    max_items: Optional[int],
    target_date: Optional[str],
    scroll_times: Optional[int],
    email: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    payload = {
        "groupUrls": group_urls,
        # FIX bug #5: enable API pagination mode
        "mode": "api",
        "apiMode": True,
        "apiStart": 0,
        "iterations": max(6, int(scroll_times or 6)),
        "apiPageSize": 40,
        "continueWithoutPaginationToken": True,
        "maxItems": min(max(max_items or settings.apify_default_max_items or 250, 250), 500),
        "maxRetries": 5,
        "maxConsecutiveFailedPages": 3,
        "pageDelayMinMs": 500,
        "pageDelayMaxMs": 1500,
        "fetchTimeoutMs": 20000,
        # Session/identity
        "sessionId": session_id or "",
        "emailCrawl": email or "",
        "accountId": _build_actor_account_id(email, session_id),
        "stateStoreName": "linkedin-actor-state",
        "allowCacheFallback": False,
        "cacheTtlHours": 6,
        "groupStateTtlHours": 24,
        "sessionInvalidCooldownHours": 6,
        "targetDate": target_date or "",
        # Browser scroll params (kept for backward compat, not used in apiMode)
        "scrollTimes": min(scroll_times or settings.apify_default_scroll_times, 3),
        "delayMinMs": max(settings.apify_delay_min_ms, 5000),
        "delayMaxMs": max(settings.apify_delay_max_ms, 12000),
        "navigationTimeoutMs": 30000,
        "groupDelayMinMs": int(max(settings.apify_group_delay_min_sec, 0) * 1000),
        "groupDelayMaxMs": int(max(settings.apify_group_delay_max_sec, settings.apify_group_delay_min_sec, 0) * 1000),
        "maxConcurrency": 1,
        "maxRequestRetries": 1,
    }
    proxy_configuration = _build_apify_proxy_configuration()
    if proxy_configuration:
        payload["proxyConfiguration"] = proxy_configuration
    storage_state = _load_storage_state_for_actor(email=email, session_id=session_id)
    if storage_state:
        payload["storageStateJson"] = json.dumps(storage_state, ensure_ascii=False)
    return payload


async def _run_actor_and_get_items(
    *,
    actor_id: str,
    token: str,
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=900) as client:
        run_resp = await client.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            json=payload,
            headers=headers,
        )
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        run_data: dict[str, Any] = {}

        for _ in range(_POLL_MAX_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL_SEC)
            status_resp = await client.get(f"{APIFY_BASE}/actor-runs/{run_id}", headers=headers)
            status_resp.raise_for_status()
            run_data = status_resp.json()["data"]
            status_value = run_data["status"]
            if status_value == "SUCCEEDED":
                break
            if status_value in ("FAILED", "ABORTED", "TIMED-OUT"):
                raise RuntimeError(f"Apify run status={status_value}, run_id={run_id}")
        else:
            raise RuntimeError(f"Apify run timeout, run_id={run_id}")

        dataset_resp = await client.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            headers=headers,
        )
        dataset_resp.raise_for_status()
        items = dataset_resp.json()
        normalized_items = items if isinstance(items, list) else []

        summary: dict[str, Any] | None = None
        store_id = run_data.get("defaultKeyValueStoreId")
        if store_id:
            summary_resp = await client.get(
                f"{APIFY_BASE}/key-value-stores/{store_id}/records/SUMMARY",
                headers=headers,
            )
            if summary_resp.status_code == 200:
                parsed_summary = summary_resp.json()
                if isinstance(parsed_summary, dict):
                    summary = parsed_summary
        return normalized_items, summary


def _build_own_actor_group_result(
    *,
    group_url: str,
    raw_posts: list[dict[str, Any]],
    actor_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    group_summary = _first_actor_group_summary(actor_summary, group_url)

    if group_summary and _actor_group_summary_is_failure(group_summary):
        error_type = _actor_summary_error_type(group_summary)
        message = str(group_summary.get("message") or "Apify own actor failed")
        if error_type == "CRAWL_ERROR" and _is_apify_session_rejected_error(error_type, message):
            error_type = _classify_apify_error(message)
        return {
            "success": False,
            "source": "apify_own",
            "status": "failed",
            "error_type": error_type,
            "reached_group": bool(group_summary.get("reachedGroup")),
            "auth_required": bool(group_summary.get("authRequired")) or _is_apify_session_rejected_error(error_type, message),
            "error": _humanize_apify_error(error_type, message),
            "posts": [],
            "actor_summary": actor_summary,
        }

    if not raw_posts:
        post_candidate_count = _to_int(group_summary.get("postCandidateCount"), 0) if group_summary else 0
        message = (
            "Apify own actor returned 0 posts"
            + (f" from {post_candidate_count} post candidates" if post_candidate_count else "")
            + ". The backend will not treat this as a valid no-match result."
        )
        return {
            "success": False,
            "source": "apify_own",
            "status": "failed",
            "error_type": "EMPTY_UNVERIFIED_RESULT",
            "reached_group": bool(group_summary.get("reachedGroup", False)) if group_summary else False,
            "auth_required": False,
            "error": _humanize_apify_error("EMPTY_UNVERIFIED_RESULT", message),
            "posts": [],
            "actor_summary": actor_summary,
        }

    now_ms = int(time.time() * 1000)
    normalized_posts: list[dict[str, Any]] = []
    for raw_post in raw_posts:
        if not isinstance(raw_post, dict):
            continue
        norm = normalize_apify_post(raw_post, group_url)
        merged_post = dict(raw_post)
        merged_post.update(norm)
        t_ms = _get_post_time_ms(merged_post, now_ms)
        if t_ms > 0:
            norm["posted_at"] = datetime.fromtimestamp(t_ms / 1000).isoformat()
        normalized_posts.append(norm)

    group_name, member_count = _extract_group_metadata(raw_posts, group_url)
    if group_summary:
        group_name = str(group_summary.get("groupName") or group_name)
        member_count = _to_int(group_summary.get("memberCount"), member_count)

    return {
        "success": True,
        "source": "apify_own",
        "status": "success",
        "error_type": None,
        "reached_group": bool(group_summary.get("reachedGroup", True)) if group_summary else True,
        "auth_required": bool(group_summary.get("authRequired", False)) if group_summary else False,
        "posts": normalized_posts,
        "group_name": group_name,
        "member_count": member_count,
        "raw_count": len(raw_posts),
        "actor_summary": actor_summary,
    }


async def run_apify_crawler_for_groups(
    group_urls: list[str],
    *,
    email: Optional[str] = None,
    session_id: Optional[str] = None,
    max_items: Optional[int] = None,
    target_date: Optional[str] = None,
    scroll_times: Optional[int] = None,
) -> dict[str, dict[str, Any]]:
    """Run the in-house Apify Actor once for many groups and return one result per group."""

    cleaned_urls = [url.strip() for url in group_urls if str(url or "").strip()]
    if not cleaned_urls:
        return {}

    token = _resolve_apify_token("own")
    actor_id = (settings.apify_actor_id or "").strip()
    if not token:
        return {
            url: {"success": False, "error": "APIFY_TOKEN missing", "posts": [], "source": "apify_own"}
            for url in cleaned_urls
        }
    if not actor_id:
        return {
            url: {"success": False, "error": "APIFY actor id missing for kind=own", "posts": [], "source": "apify_own"}
            for url in cleaned_urls
        }

    try:
        payload = _build_own_actor_payload(
            group_urls=cleaned_urls,
            max_items=max_items,
            target_date=target_date,
            scroll_times=scroll_times,
            email=email,
            session_id=session_id,
        )
        logger.info("Apify batch crawl start: actor=%s groups=%d", actor_id, len(cleaned_urls))
        raw_posts, actor_summary = await _run_actor_and_get_items(actor_id=actor_id, token=token, payload=payload)
        grouped_posts = _group_raw_posts_by_group_url(raw_posts)
        return {
            url: _build_own_actor_group_result(
                group_url=url,
                raw_posts=grouped_posts.get(_normalize_url_for_compare(url), []),
                actor_summary=actor_summary,
            )
            for url in cleaned_urls
        }
    except Exception as exc:
        logger.exception("Apify batch crawler failed: groups=%d", len(cleaned_urls))
        message = str(exc)
        error_type = _classify_apify_error(message)
        return {
            url: {
                "success": False,
                "source": "apify_own",
                "status": "failed",
                "error_type": error_type,
                "reached_group": False,
                "auth_required": _is_apify_session_rejected_error(error_type, message),
                "error": _humanize_apify_error(error_type, message),
                "posts": [],
            }
            for url in cleaned_urls
        }


async def run_apify_crawler_for_group(
    group_url: str,
    *,
    kind: ActorKind = "own",
    email: Optional[str] = None,
    session_id: Optional[str] = None,
    max_items: Optional[int] = None,
    target_date: Optional[str] = None,
    scroll_times: Optional[int] = None,
) -> dict[str, Any]:
    """Run an Apify Actor and return normalized posts for one LinkedIn group."""

    token = _resolve_apify_token(kind)
    actor_id = (
        settings.apify_3rd_party_actor_id if kind == "third_party" else settings.apify_actor_id
    ).strip()

    if not token:
        return {"success": False, "error": "APIFY_TOKEN missing", "posts": []}
    if not actor_id:
        return {"success": False, "error": f"APIFY actor id missing for kind={kind}", "posts": []}

    try:
        payload = _build_actor_payload(
            group_url=group_url,
            kind=kind,
            max_items=max_items,
            target_date=target_date,
            scroll_times=scroll_times,
            email=email,
            session_id=session_id,
        )
        logger.info("Apify crawl start: kind=%s actor=%s url=%s", kind, actor_id, group_url)
        raw_posts, actor_summary = await _run_actor_and_get_items(actor_id=actor_id, token=token, payload=payload)
        group_summary = _first_actor_group_summary(actor_summary, group_url)

        if group_summary and _actor_group_summary_is_failure(group_summary):
            error_type = _actor_summary_error_type(group_summary)
            message = str(group_summary.get("message") or "Apify own actor failed")
            if error_type == "CRAWL_ERROR" and _is_apify_session_rejected_error(error_type, message):
                error_type = _classify_apify_error(message)
            human_message = _humanize_apify_error(error_type, message)
            return {
                "success": False,
                "source": f"apify_{kind}",
                "status": "failed",
                "error_type": error_type,
                "reached_group": bool(group_summary.get("reachedGroup")),
                "auth_required": bool(group_summary.get("authRequired")) or _is_apify_session_rejected_error(error_type, message),
                "error": human_message,
                "posts": [],
                "actor_summary": actor_summary,
            }

        if kind == "third_party" and not raw_posts and not group_summary:
            message = (
                "Apify third-party actor returned 0 items and no SUMMARY, "
                "so the backend cannot verify that LinkedIn group content was reached."
            )
            return {
                "success": False,
                "source": f"apify_{kind}",
                "status": "failed",
                "error_type": "EMPTY_UNVERIFIED_RESULT",
                "reached_group": False,
                "auth_required": False,
                "error": message,
                "posts": [],
                "actor_summary": actor_summary,
            }

        if kind == "own" and not raw_posts:
            post_candidate_count = _to_int(group_summary.get("postCandidateCount"), 0) if group_summary else 0
            message = (
                "Apify own actor returned 0 posts"
                + (f" from {post_candidate_count} post candidates" if post_candidate_count else "")
                + ". The backend will not treat this as a valid no-match result."
            )
            return {
                "success": False,
                "source": f"apify_{kind}",
                "status": "failed",
                "error_type": "EMPTY_UNVERIFIED_RESULT",
                "reached_group": bool(group_summary.get("reachedGroup", False)) if group_summary else False,
                "auth_required": False,
                "error": _humanize_apify_error("EMPTY_UNVERIFIED_RESULT", message),
                "posts": [],
                "actor_summary": actor_summary,
            }

        now_ms = int(time.time() * 1000)
        normalized_posts: list[dict[str, Any]] = []
        for raw_post in raw_posts:
            if not isinstance(raw_post, dict):
                continue
            norm = normalize_apify_post(raw_post, group_url)
            merged_post = dict(raw_post)
            merged_post.update(norm)
            t_ms = _get_post_time_ms(merged_post, now_ms)
            if t_ms > 0:
                norm["posted_at"] = datetime.fromtimestamp(t_ms / 1000).isoformat()
            normalized_posts.append(norm)

        group_name, member_count = _extract_group_metadata(raw_posts, group_url)
        if group_summary:
            group_name = str(group_summary.get("groupName") or group_name)
            member_count = _to_int(group_summary.get("memberCount"), member_count)
        return {
            "success": True,
            "source": f"apify_{kind}",
            "status": "success",
            "error_type": None,
            "reached_group": bool(group_summary.get("reachedGroup", True)) if group_summary else True,
            "auth_required": bool(group_summary.get("authRequired", False)) if group_summary else False,
            "posts": normalized_posts,
            "group_name": group_name,
            "member_count": member_count,
            "raw_count": len(raw_posts),
            "actor_summary": actor_summary,
        }
    except Exception as exc:
        logger.exception("Apify crawler failed: kind=%s url=%s", kind, group_url)
        message = str(exc)
        error_type = _classify_apify_error(message)
        return {
            "success": False,
            "source": f"apify_{kind}",
            "status": "failed",
            "error_type": error_type,
            "reached_group": False,
            "auth_required": _is_apify_session_rejected_error(error_type, message),
            "error": _humanize_apify_error(error_type, message),
            "posts": [],
        }
