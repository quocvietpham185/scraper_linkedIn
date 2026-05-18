from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.file_utils import ensure_directory
from app.utils.logger import get_logger

logger = get_logger(__name__)

TASK_DATA_FILE = Path(settings.state_path.parent) / "crawl_tasks.json"
TASK_LOCK_FILE = TASK_DATA_FILE.with_suffix(".json.lock")
ACTIVE_TASK_STATUSES = {"queued", "running"}
ACTIVE_TASK_STALE_AFTER = timedelta(hours=24)
_task_thread_lock = threading.Lock()


@contextmanager
def _task_file_lock(timeout_sec: float = 10.0, stale_after_sec: float = 1800.0):
    """Cross-process lock for crawl_tasks.json updates."""

    ensure_directory(TASK_DATA_FILE.parent)
    deadline = time.monotonic() + timeout_sec
    fd: int | None = None

    with _task_thread_lock:
        while True:
            try:
                fd = os.open(str(TASK_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                break
            except FileExistsError:
                try:
                    age = time.time() - TASK_LOCK_FILE.stat().st_mtime
                    if age > stale_after_sec:
                        TASK_LOCK_FILE.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass

                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for task file lock: {TASK_LOCK_FILE}")
                time.sleep(0.05)

        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                TASK_LOCK_FILE.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove task lock file %s", TASK_LOCK_FILE, exc_info=True)


def _load_tasks_unlocked() -> dict[str, Any]:
    if not TASK_DATA_FILE.exists():
        return {}
    try:
        with open(TASK_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.error("Failed to read task file %s: %s", TASK_DATA_FILE, exc)
        return {}


def _save_tasks_unlocked(data: dict[str, Any]) -> None:
    try:
        ensure_directory(TASK_DATA_FILE.parent)
        tmp_path = TASK_DATA_FILE.with_name(f"{TASK_DATA_FILE.name}.tmp.{os.getpid()}")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, TASK_DATA_FILE)
    except Exception as exc:
        logger.error("Failed to write task file %s: %s", TASK_DATA_FILE, exc)
        raise


def _is_recent_running_task(task: dict[str, Any], email: str) -> bool:
    if str(task.get("email") or "").strip().lower() != email:
        return False
    if str(task.get("status") or "").strip().lower() not in ACTIVE_TASK_STATUSES:
        return False

    updated_at = str(task.get("updated_at") or task.get("created_at") or "").strip()
    try:
        updated_dt = datetime.fromisoformat(updated_at)
    except ValueError:
        return True
    return datetime.now() - updated_dt < ACTIVE_TASK_STALE_AFTER


def _find_active_task_for_email(
    tasks: dict[str, Any],
    email: str,
) -> tuple[str, dict[str, Any]] | None:
    normalized_email = str(email or "").strip().lower()
    if not normalized_email:
        return None
    for session_id, task in tasks.items():
        if isinstance(task, dict) and _is_recent_running_task(task, normalized_email):
            return str(session_id), task
    return None


def create_task(session_id: str, email: str, group_count: int = 0) -> None:
    normalized_email = str(email or "").strip().lower()
    with _task_file_lock():
        tasks = _load_tasks_unlocked()
        active_task = _find_active_task_for_email(tasks, normalized_email)
        if active_task is not None:
            active_session_id, active = active_task
            raise RuntimeError(
                "Email nay dang co job crawl dang chay. "
                f"id_session_crawl={active_session_id}, status={active.get('status')}, "
                f"updated_at={active.get('updated_at')}"
            )

        now = datetime.now().isoformat()
        tasks[session_id] = {
            "status": "running",
            "email": normalized_email or email,
            "group_count": group_count,
            "created_at": now,
            "updated_at": now,
            "message": "Crawl task is running.",
        }
        _save_tasks_unlocked(tasks)


def update_task_status(session_id: str, status: str, message: str = "") -> None:
    with _task_file_lock():
        tasks = _load_tasks_unlocked()
        if session_id in tasks:
            tasks[session_id]["status"] = status
            if message:
                tasks[session_id]["message"] = message
            tasks[session_id]["updated_at"] = datetime.now().isoformat()
            _save_tasks_unlocked(tasks)


def get_task_status(session_id: str) -> dict[str, Any] | None:
    with _task_file_lock():
        return _load_tasks_unlocked().get(session_id)


def get_active_task_for_email(email: str) -> tuple[str, dict[str, Any]] | None:
    with _task_file_lock():
        return _find_active_task_for_email(_load_tasks_unlocked(), email)
