from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from datetime import datetime

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

TASK_DATA_FILE = Path(settings.state_path.parent) / "crawl_tasks.json"

def _load_tasks() -> dict[str, Any]:
    if not TASK_DATA_FILE.exists():
        return {}
    try:
        with open(TASK_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Lỗi đọc file {TASK_DATA_FILE}: {e}")
        return {}

def _save_tasks(data: dict[str, Any]) -> None:
    try:
        with open(TASK_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi ghi file {TASK_DATA_FILE}: {e}")

def create_task(session_id: str, email: str, group_count: int = 0) -> None:
    tasks = _load_tasks()
    tasks[session_id] = {
        "status": "running",
        "email": email,
        "group_count": group_count,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "message": "N8n đang xử lý..."
    }
    _save_tasks(tasks)

def update_task_status(session_id: str, status: str, message: str = "") -> None:
    tasks = _load_tasks()
    if session_id in tasks:
        tasks[session_id]["status"] = status
        if message:
            tasks[session_id]["message"] = message
        tasks[session_id]["updated_at"] = datetime.now().isoformat()
        _save_tasks(tasks)

def get_task_status(session_id: str) -> dict[str, Any] | None:
    tasks = _load_tasks()
    return tasks.get(session_id)
