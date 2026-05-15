from __future__ import annotations
from html import escape
import httpx
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _safe_text(value: object, fallback: str = "") -> str:
    text = str(value if value is not None else fallback).strip()
    return escape(text or fallback)


def _safe_url(value: object) -> str:
    return escape(str(value or "").strip(), quote=True)


def _source_label(source: str | None) -> str:
    labels = {
        "playwright_local": "Playwright local",
        "apify_own_actor": "Apify Actor nội bộ",
        "apify_3rd_party": "Apify Actor dự phòng",
        "apify_direct": "Apify thủ công",
    }
    return labels.get(str(source or "").strip(), str(source or "Không xác định"))


def format_crawl_start_message(
    *,
    session_id: str,
    email: str,
    total_groups: int,
    target_date: str,
    crawler_type: str,
) -> str:
    return (
        "<b>Bắt đầu phiên cào LinkedIn</b>\n\n"
        f"<b>Nhân viên:</b> {_safe_text(email, 'Không xác định')}\n"
        f"<b>Số nhóm:</b> {total_groups}\n"
        f"<b>Ngày lọc:</b> {_safe_text(target_date)}\n"
        f"<b>Chế độ:</b> {_safe_text(crawler_type)}\n"
        f"<b>Mã phiên:</b> <code>{_safe_text(session_id)}</code>"
    )


def format_crawl_group_success_message(
    *,
    index: int,
    total: int,
    email: str,
    group_name: str,
    group_url: str,
    target_date: str,
    source: str,
    total_posts: int,
    member_count: int,
    top_post: dict,
) -> str:
    post_url = _safe_url(top_post.get("post_url"))
    content = _safe_text(top_post.get("content"))[:500]
    post_line = f'<a href="{post_url}">Mở bài viết</a>' if post_url else "Không có link bài viết"
    return (
        f"<b>Đã cào xong nhóm {index}/{total}</b>\n\n"
        f"<b>Nhân viên:</b> {_safe_text(email, 'Không xác định')}\n"
        f"<b>Nhóm:</b> {_safe_text(group_name, 'Không rõ tên nhóm')}\n"
        f"<b>Link nhóm:</b> <a href=\"{_safe_url(group_url)}\">Mở nhóm</a>\n"
        f"<b>Ngày lọc:</b> {_safe_text(target_date)}\n"
        f"<b>Nguồn cào:</b> {_safe_text(_source_label(source))}\n"
        f"<b>Tổng bài đã đọc:</b> {total_posts}\n"
        f"<b>Số thành viên:</b> {member_count}\n\n"
        "<b>Bài tốt nhất:</b>\n"
        f"{post_line}\n"
        f"<b>Nội dung:</b> {content or 'Không có nội dung'}\n"
        f"<b>Like:</b> {_safe_text(top_post.get('likes'), '0')} | "
        f"<b>Bình luận:</b> {_safe_text(top_post.get('comments'), '0')} | "
        f"<b>Điểm:</b> {_safe_text(top_post.get('score'), '0')}"
    )


def format_crawl_group_no_match_message(
    *,
    index: int,
    total: int,
    email: str,
    group_name: str,
    group_url: str,
    target_date: str,
    source: str,
    total_posts: int,
) -> str:
    return (
        f"<b>Không có bài phù hợp ở nhóm {index}/{total}</b>\n\n"
        f"<b>Nhân viên:</b> {_safe_text(email, 'Không xác định')}\n"
        f"<b>Nhóm:</b> {_safe_text(group_name, 'Không rõ tên nhóm')}\n"
        f"<b>Link nhóm:</b> <a href=\"{_safe_url(group_url)}\">Mở nhóm</a>\n"
        f"<b>Ngày lọc:</b> {_safe_text(target_date)}\n"
        f"<b>Nguồn cào:</b> {_safe_text(_source_label(source))}\n"
        f"<b>Tổng bài đã đọc:</b> {total_posts}\n\n"
        "Hệ thống đã ghi log để kiểm tra lại."
    )


def format_crawl_group_error_message(
    *,
    index: int,
    total: int,
    email: str,
    group_url: str,
    target_date: str,
    error: object,
) -> str:
    return (
        f"<b>Lỗi khi cào nhóm {index}/{total}</b>\n\n"
        f"<b>Nhân viên:</b> {_safe_text(email, 'Không xác định')}\n"
        f"<b>Link nhóm:</b> <a href=\"{_safe_url(group_url)}\">Mở nhóm</a>\n"
        f"<b>Ngày lọc:</b> {_safe_text(target_date)}\n"
        f"<b>Lỗi:</b> {_safe_text(error)}\n\n"
        "Hệ thống sẽ tiếp tục xử lý các nhóm còn lại nếu còn trong danh sách."
    )


def format_crawl_finish_message(
    *,
    session_id: str,
    email: str,
    total_groups: int,
    success_count: int,
    failed_count: int,
    no_match_count: int = 0,
) -> str:
    status = "Hoàn thành" if failed_count == 0 else "Hoàn thành có lỗi"
    return (
        f"<b>{status} phiên cào LinkedIn</b>\n\n"
        f"<b>Nhân viên:</b> {_safe_text(email, 'Không xác định')}\n"
        f"<b>Tổng số nhóm:</b> {total_groups}\n"
        f"<b>Thành công:</b> {success_count}\n"
        f"<b>Không có bài phù hợp:</b> {no_match_count}\n"
        f"<b>Cần kiểm tra:</b> {failed_count}\n"
        f"<b>Mã phiên:</b> <code>{_safe_text(session_id)}</code>"
    )

def send_telegram_message(message: str, chat_id: Optional[str] = None, message_thread_id: Optional[str] = None) -> bool:
    """Gửi tin nhắn Telegram bằng Bot API."""
    bot_token = settings.telegram_bot_token
    target_chat_id = chat_id or settings.telegram_chat_id
    target_thread_id = message_thread_id or settings.telegram_thread_id

    if not bot_token or not target_chat_id:
        logger.warning("Không thể gửi Telegram: Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID trong .env")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    if target_thread_id:
        try:
            payload["message_thread_id"] = int(target_thread_id)
        except ValueError:
            payload["message_thread_id"] = target_thread_id

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            logger.info("Đã gửi tin nhắn Telegram thành công.")
            return True
    except httpx.RequestError as exc:
        logger.error(f"Lỗi mạng khi gửi Telegram: {exc}")
    except httpx.HTTPStatusError as exc:
        logger.error(f"Lỗi API Telegram ({exc.response.status_code}): {exc.response.text}")
    except Exception as exc:
        logger.exception("Lỗi không xác định khi gửi Telegram")
        
    return False
