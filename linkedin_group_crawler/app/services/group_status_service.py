from __future__ import annotations
import asyncio
import httpx
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from app.config import settings
from app.services import google_sheet_service as gsheet
from app.utils.logger import get_logger
from app.utils.file_utils import ensure_directory

logger = get_logger(__name__)

_status_cache: Dict[str, Dict[str, Any]] = {}
_status_lock = asyncio.Lock()


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def _get_cache_file_path() -> Path:
    storage_dir = settings.raw_data_dir.parent / "storage"
    ensure_directory(storage_dir)
    return storage_dir / "group_status_cache.json"


def _load_cache_from_disk():
    path = _get_cache_file_path()
    if path.exists():
        try:
            global _status_cache
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Normalize tất cả key khi load
            _status_cache = {_normalize_url(k): v for k, v in raw.items()}
        except Exception:
            logger.warning("Failed to load group status cache from disk")


def _save_cache_to_disk():
    path = _get_cache_file_path()
    try:
        path.write_text(
            json.dumps(_status_cache, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception:
        logger.warning("Failed to save group status cache to disk")


_load_cache_from_disk()


async def check_single_group_status(url: str, update_cache: bool = True) -> str:
    key = _normalize_url(url)
    status = "error"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.head(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            if resp.status_code in (200, 301, 302, 303, 307, 308):
                status = "live"
            elif resp.status_code == 404:
                status = "dead"
            elif resp.status_code in (403, 999):
                status = "blocked"
            else:
                logger.info(f"Group {url} returned status code {resp.status_code}")
                status = "live"
    except Exception as e:
        logger.warning(f"Status check failed for {url}: {e}")
        status = "error"

    if update_cache:
        async with _status_lock:
            _status_cache[key] = {
                "status": status,
                "checked_at": datetime.now().isoformat(),
            }
            _save_cache_to_disk()

    return status


async def background_group_status_worker():
    logger.info("Starting Background Group Status Worker...")
    while True:
        try:
            if gsheet.spreadsheet_configured():
                logger.info("Starting periodic group status check...")
                rows = gsheet.read_group_url_rows()
                for row in rows:
                    url = row.get("url_group")
                    if not url:
                        continue
                    await check_single_group_status(url, update_cache=True)
                    await asyncio.sleep(1)
                logger.info(f"Periodic group status check complete. Checked {len(rows)} groups.")
            else:
                logger.warning("Spreadsheet not configured, skipping group status check.")
            await asyncio.sleep(12 * 3600)
        except Exception:
            logger.exception("Error in background group status worker")
            await asyncio.sleep(60)


def get_all_cached_statuses() -> Dict[str, Any]:
    return _status_cache