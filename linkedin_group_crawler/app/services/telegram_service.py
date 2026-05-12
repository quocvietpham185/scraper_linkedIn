from __future__ import annotations
import httpx
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

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
