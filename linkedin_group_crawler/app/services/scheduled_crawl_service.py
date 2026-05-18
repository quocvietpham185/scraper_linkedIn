from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app.schemas.request_models import StartWorkflowRequest
from app.services import crawl_task_service
from app.services.background_crawler_service import run_background_crawl
from app.services.google_sheet_service import read_group_url_rows
from app.utils.logger import get_logger

logger = get_logger(__name__)

_scheduler_started = False
_STATE_FILE = Path(settings.state_path.parent) / "scheduled_crawl_state.json"


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    lower_map = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        value = row.get(key)
        if value:
            return str(value).strip()
        value = lower_map.get(key.lower())
        if value:
            return str(value).strip()
    return ""


def _build_tasks_by_email() -> dict[str, list[str]]:
    rows = read_group_url_rows()
    tasks: dict[str, set[str]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _pick(row, ("URL_Nhóm", "URL nhóm", "url_group", "url", "Link", "group_url", "Group URL"))
        email = _pick(row, ("Email", "Email_crawl", "email", "userEmail", "user_email"))
        if not url or not email or "linkedin.com/groups/" not in url:
            continue
        tasks.setdefault(email.lower(), set()).add(url)

    return {email: sorted(urls) for email, urls in tasks.items()}


def _seconds_until_next_run(time_text: str) -> float:
    try:
        hour_text, minute_text = time_text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except Exception:
        logger.warning("Invalid SCHEDULED_CRAWL_TIME=%r, fallback to 08:00", time_text)
        hour = 8
        minute = 0

    now = datetime.now()
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


async def run_scheduled_crawl_once() -> None:
    today_key = datetime.now().strftime("%Y-%m-%d")
    state = _load_state()
    if state.get("last_completed_date") == today_key or state.get("last_started_date") == today_key:
        logger.info("Scheduled crawl: already ran for %s, skipping duplicate run", today_key)
        return

    _save_state({"last_started_date": today_key, "last_started_at": datetime.now().isoformat()})
    tasks_by_email = _build_tasks_by_email()
    if not tasks_by_email:
        logger.warning("Scheduled crawl: no valid LinkedIn group rows found in Google Sheet")
        return

    logger.info("Scheduled crawl: found %d account(s)", len(tasks_by_email))
    for index, (email, group_urls) in enumerate(tasks_by_email.items(), start=1):
        crawl_started_token = datetime.now().strftime("%Y%m%d%H%M%S")
        crawl_id = f"scheduled_{email.replace('@', '_').replace('.', '_')}_{crawl_started_token}_{random.randint(1000, 9999)}"
        request = StartWorkflowRequest(
            email=email,
            password="scheduled_not_used",
            force_relogin=False,
            max_posts=settings.default_max_items,
            crawler_type=settings.scheduled_crawler_type,  # type: ignore[arg-type]
            group_urls=group_urls,
        )

        logger.info(
            "Scheduled crawl: starting account %d/%d email=%s groups=%d crawler=%s",
            index,
            len(tasks_by_email),
            email,
            len(group_urls),
            settings.scheduled_crawler_type,
        )
        try:
            crawl_task_service.create_task(crawl_id, email, len(group_urls))
            await run_background_crawl(crawl_id, request)
        except RuntimeError as exc:
            logger.warning("Scheduled crawl skipped for account %s: %s", email, exc)
        except Exception:
            logger.exception("Scheduled crawl failed for account %s", email)

        if index < len(tasks_by_email) and settings.scheduled_account_delay_sec > 0:
            await asyncio.sleep(settings.scheduled_account_delay_sec)

    _save_state(
        {
            "last_started_date": today_key,
            "last_completed_date": today_key,
            "last_completed_at": datetime.now().isoformat(),
            "account_count": len(tasks_by_email),
        }
    )


async def scheduled_crawl_worker() -> None:
    if settings.scheduled_run_on_startup:
        logger.info("Scheduled crawl: SCHEDULED_RUN_ON_STARTUP=true, running once after startup")
        await run_scheduled_crawl_once()

    while True:
        delay_sec = _seconds_until_next_run(settings.scheduled_crawl_time)
        logger.info(
            "Scheduled crawl enabled: next run at %s, sleeping %.0fs",
            settings.scheduled_crawl_time,
            delay_sec,
        )
        await asyncio.sleep(delay_sec)
        await run_scheduled_crawl_once()


def start_scheduled_crawl_worker() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    asyncio.create_task(scheduled_crawl_worker())


def _load_state() -> dict[str, Any]:
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        logger.warning("Scheduled crawl: could not read state file %s", _STATE_FILE, exc_info=True)
    return {}


def _save_state(data: dict[str, Any]) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("Scheduled crawl: could not write state file %s", _STATE_FILE, exc_info=True)
