"""Apify API fallback crawler service for LinkedIn groups.

Đây là phương án dự phòng khi Playwright crawler thất bại.
Logic được port từ linkedin-crawler/backend/crawler.py và điều chỉnh
để phù hợp với cấu trúc module của project này.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.services.google_sheet_service import get_apify_token_from_settings_sheet
from app.utils.logger import get_logger

logger = get_logger(__name__)

APIFY_BASE = "https://api.apify.com/v2"

# Poll interval và số lần thử tối đa (10 giây * 48 lần = 8 phút)
_POLL_INTERVAL_SEC = 10
_POLL_MAX_ATTEMPTS = 48


def extract_group_id_from_url(url: str) -> str:
    """Extract LinkedIn Group ID từ URL.

    Ví dụ: https://www.linkedin.com/groups/1234567/ → "1234567"
    """
    url = (url or "").strip()
    match = re.search(r"groups/(\d+)", url)
    if match:
        return match.group(1)
    raise ValueError(f"Không tìm thấy Group ID trong URL: {url!r}")


def _get_post_time_ms(post: dict[str, Any], now_ms: int) -> int:
    """Convert timestamp của post sang milliseconds.

    Ưu tiên: datetime ISO → unix timestamp → text_date heuristic.
    Trả về 0 nếu không parse được.
    """
    # 1. datetime ISO string
    dt = post.get("datetime", "")
    if dt and dt not in ("null", "empty", ""):
        try:
            t = int(datetime.fromisoformat(dt.replace("Z", "+00:00")).timestamp() * 1000)
            if t > 0:
                return t
        except Exception:
            pass

    # 2. Unix timestamp string
    ts = post.get("timestamp", "")
    if ts and ts not in ("null", "empty", ""):
        try:
            t = int(ts) * 1000
            if t > 0:
                return t
        except Exception:
            pass

    # 3. Text date heuristic ("5m", "2h") — chỉ tin ≤23h
    text_date = post.get("text_date", "").strip().lower()
    min_match = re.match(r"^(\d+)m$", text_date)
    hour_match = re.match(r"^(\d+)h$", text_date)
    if min_match:
        return now_ms - int(min_match.group(1)) * 60_000
    if hour_match:
        h = int(hour_match.group(1))
        if h <= 23:
            return now_ms - h * 3_600_000

    return 0


def normalize_apify_post(post: dict[str, Any], original_url: str) -> dict[str, Any]:
    """Chuẩn hóa 1 post từ Apify sang format nội bộ."""
    return {
        "content": str(post.get("content", "")),
        "likes": int(post.get("likes", 0) or 0),
        "comments": int(post.get("comments", 0) or 0),
        "reposts": int(post.get("share", 0) or 0),  # Map share -> reposts
        "post_url": str(post.get("url", "")),
        "group_url": original_url,
        "posted_at_raw": str(post.get("text_date") or post.get("timestamp") or ""),
    }


async def run_apify_crawler_for_group(group_url: str) -> dict[str, Any]:
    """Crawl LinkedIn Group qua Apify API.

    Returns:
        dict với keys: success, posts, group_name, member_count
    """
    # Ưu tiên đọc token từ Google Sheet Settings, fallback về .env
    token = get_apify_token_from_settings_sheet().strip()
    actor_id = (settings.apify_actor_id or "").strip()

    if not token:
        logger.error("APIFY_TOKEN chưa được cấu hình.")
        return {"success": False, "error": "APIFY_TOKEN missing", "posts": []}
    if not actor_id:
        logger.error("APIFY_ACTOR_ID chưa được cấu hình.")
        return {"success": False, "error": "APIFY_ACTOR_ID missing", "posts": []}

    try:
        group_id = extract_group_id_from_url(group_url)
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "username": group_id,
            "type": "group",
            "start": 1,
            "iterations": 5, # Tăng lên 5 để lấy được nhiều bài hơn, tránh sót bài top
        }

        logger.info("Apify crawl start: group_id=%s url=%s", group_id, group_url)

        async with httpx.AsyncClient(timeout=600) as client:
            # 1. Khởi động Apify Actor run
            run_resp = await client.post(
                f"{APIFY_BASE}/acts/{actor_id}/runs",
                json=payload,
                headers=headers,
            )
            run_resp.raise_for_status()
            run_data = run_resp.json()
            run_id = run_data["data"]["id"]

            # 2. Poll cho đến khi xong
            for attempt in range(_POLL_MAX_ATTEMPTS):
                await asyncio.sleep(_POLL_INTERVAL_SEC)
                status_resp = await client.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    headers=headers,
                )
                status_data = status_resp.json()
                apify_status = status_data["data"]["status"]
                if apify_status == "SUCCEEDED":
                    break
                elif apify_status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    raise RuntimeError(f"Apify run status={apify_status}")
            else:
                raise RuntimeError("Apify run timeout")

            # 3. Lấy dataset posts
            dataset_resp = await client.get(
                f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
                headers=headers,
            )
            dataset_resp.raise_for_status()
            raw_posts: list[dict[str, Any]] = dataset_resp.json()
            
            # Chuẩn hóa posts (có bổ sung parse thời gian)
            now_ms = int(time.time() * 1000)
            normalized_posts = []
            for p in raw_posts:
                norm = normalize_apify_post(p, group_url)
                # Tính toán posted_at chuẩn ISO để ranking chính xác
                t_ms = _get_post_time_ms(p, now_ms)
                if t_ms > 0:
                    norm["posted_at"] = datetime.fromtimestamp(t_ms / 1000).isoformat()
                normalized_posts.append(norm)
            
            # Cố gắng lấy thông tin nhóm từ các bài post (nếu actor trả về)
            group_name = f"Group {group_id}"
            member_count = 0
            if raw_posts:
                # Log thử keys của bài post đầu tiên để debug
                logger.debug(f"Apify raw post sample keys: {list(raw_posts[0].keys())}")
                
                for p in raw_posts:
                    # Tìm tên nhóm
                    g_title = p.get("groupName") or p.get("title") or p.get("group_name") or p.get("group_title")
                    if g_title and group_name == f"Group {group_id}":
                        group_name = g_title
                    
                    # Tìm số thành viên (nhiều actor dùng tên field khác nhau)
                    m_count = (
                        p.get("memberCount") or 
                        p.get("membersCount") or 
                        p.get("groupMembers") or 
                        p.get("member_count") or
                        p.get("subscriberCount") or
                        p.get("groupMembersCount") or
                        p.get("members") or
                        p.get("totalMembers") or
                        p.get("followerCount")
                    )
                    if m_count and member_count == 0:
                        try:
                            # Xử lý trường hợp chuỗi "46,151 members"
                            if isinstance(m_count, str):
                                m_count = re.sub(r"[^\d]", "", m_count)
                            member_count = int(m_count)
                        except:
                            pass
                    
                    # Nếu đã lấy đủ thì break sớm
                    if member_count > 0 and group_name != f"Group {group_id}":
                        break

            return {
                "success": True,
                "posts": normalized_posts,
                "group_name": group_name,
                "member_count": member_count,
            }

    except Exception as e:
        logger.exception(f"Lỗi Apify cho {group_url}")
        return {"success": False, "error": str(e), "posts": []}
