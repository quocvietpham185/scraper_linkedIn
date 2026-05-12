"""KPI service to track and persist employee comment tasks."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from app.config import settings
from app.utils.file_utils import ensure_directory
from app.utils.logger import get_logger

logger = get_logger(__name__)

_kpi_lock = threading.Lock()

def _get_kpi_file_path() -> Path:
    storage_dir = settings.raw_data_dir.parent / "storage"
    ensure_directory(storage_dir)
    return storage_dir / "kpi_data.json"

def _load_kpi_data() -> Dict[str, Any]:
    path = _get_kpi_file_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse kpi_data.json, returning empty dict", exc_info=True)
        return {}

def _save_kpi_data(data: Dict[str, Any]) -> None:
    path = _get_kpi_file_path()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def report_task_seeded(email: str, post_url: str) -> Dict[str, Any]:
    """Report a post as seeded (Pending)."""
    email = email.strip().lower()
    post_url = post_url.strip()

    with _kpi_lock:
        data = _load_kpi_data()
        user_data = data.setdefault(email, {"daily_goal": 20, "tasks": {}})
        
        # Add or update task
        user_data["tasks"][post_url] = {
            "status": "pending",
            "reported_at": datetime.now().isoformat(),
            "verified_at": None,
        }
        _save_kpi_data(data)
        return user_data["tasks"][post_url]

def add_personal_task(email: str, title: str, priority: str = "medium", deadline: str = "Hôm nay") -> Dict[str, Any]:
    """Thêm nhiệm vụ cá nhân thủ công."""
    email = email.strip().lower()
    task_id = str(int(datetime.now().timestamp() * 1000))
    
    with _kpi_lock:
        data = _load_kpi_data()
        user_data = data.setdefault(email, {"daily_goal": 20, "tasks": {}, "personal_tasks": []})
        if "personal_tasks" not in user_data:
            user_data["personal_tasks"] = []
            
        new_task = {
            "id": task_id,
            "title": title.strip(),
            "status": "pending",
            "priority": priority,
            "deadline": deadline,
            "created_at": datetime.now().isoformat()
        }
        user_data["personal_tasks"].insert(0, new_task)
        _save_kpi_data(data)
        return new_task

def update_personal_task(email: str, task_id: str, updates: Dict[str, Any]) -> bool:
    """Cập nhật trạng thái hoặc thông tin nhiệm vụ cá nhân."""
    email = email.strip().lower()
    with _kpi_lock:
        data = _load_kpi_data()
        if email not in data or "personal_tasks" not in data[email]:
            return False
            
        for task in data[email]["personal_tasks"]:
            if task["id"] == task_id:
                task.update(updates)
                _save_kpi_data(data)
                return True
        return False

def delete_personal_task(email: str, task_id: str) -> bool:
    """Xóa nhiệm vụ cá nhân."""
    email = email.strip().lower()
    with _kpi_lock:
        data = _load_kpi_data()
        if email not in data or "personal_tasks" not in data[email]:
            return False
            
        initial_len = len(data[email]["personal_tasks"])
        data[email]["personal_tasks"] = [t for t in data[email]["personal_tasks"] if t["id"] != task_id]
        
        if len(data[email]["personal_tasks"]) < initial_len:
            _save_kpi_data(data)
            return True
        return False

def update_task_status(email: str, post_url: str, status: str) -> None:
    """Update task status after verification."""
    email = email.strip().lower()
    post_url = post_url.strip()

    with _kpi_lock:
        data = _load_kpi_data()
        if email in data and "tasks" in data[email] and post_url in data[email]["tasks"]:
            data[email]["tasks"][post_url]["status"] = status
            if status == "verified":
                data[email]["tasks"][post_url]["verified_at"] = datetime.now().isoformat()
            _save_kpi_data(data)

def get_kpi_status(email: str) -> Dict[str, Any]:
    """Get the current KPI status and task list for a user."""
    email = email.strip().lower()
    
    with _kpi_lock:
        data = _load_kpi_data()
        user_data = data.get(email, {"daily_goal": 20, "tasks": {}})
        
        tasks = user_data.get("tasks", {})
        
        # Calculate daily verified count
        today_str = datetime.now().date().isoformat()
        verified_today = 0
        
        task_list = []
        for url, t in tasks.items():
            task_info = {"post_url": url, **t}
            task_list.append(task_info)
            
            if t.get("status") == "verified" and t.get("verified_at"):
                if t["verified_at"].startswith(today_str):
                    verified_today += 1
                    
        # Sort tasks by reported_at desc
        task_list.sort(key=lambda x: x.get("reported_at", ""), reverse=True)

        return {
            "daily_goal": user_data.get("daily_goal", 20),
            "verified_today": verified_today,
            "tasks": task_list,
            "personal_tasks": user_data.get("personal_tasks", [])
        }
