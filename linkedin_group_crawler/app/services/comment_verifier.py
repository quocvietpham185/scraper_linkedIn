from __future__ import annotations
"""Background worker to verify LinkedIn comments."""

import asyncio
import json
from typing import Optional

from playwright.sync_api import Error, sync_playwright
from app.config import settings
from app.services.auth_service import build_session_state_path, build_session_metadata_path
from app.services.kpi_service import update_task_status
from app.services import live_feed_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

verify_queue: asyncio.Queue = asyncio.Queue()

async def _to_thread_helper(func, *args, **kwargs):
    import functools
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

async def background_verification_worker():
    """Continuously processes the verification queue."""
    logger.info("Starting KPI background verification worker...")
    while True:
        try:
            task = await verify_queue.get()
            email = task["email"]
            post_url = task["post_url"]
            
            logger.info("Verifying comment for %s on %s", email, post_url)
            # Run in a separate thread so Playwright sync doesn't block the async loop
            is_verified = await _to_thread_helper(_verify_comment_sync, email, post_url)
            
            status = "verified" if is_verified else "failed"
            update_task_status(email, post_url, status)
            
            if is_verified:
                live_feed_service.add_event("verify_success", "Hệ thống", f"Xác minh THÀNH CÔNG seeding cho {email} tại {post_url}")
            else:
                live_feed_service.add_event("verify_fail", "Hệ thống", f"Xác minh THẤT BẠI seeding cho {email} tại {post_url}")
                
            logger.info("Verification complete for %s: %s", email, status)
            
            verify_queue.task_done()
        except Exception:
            logger.exception("Error in background verification worker")
            await asyncio.sleep(5)  # Wait before retrying loop

def queue_verification(email: str, post_url: str):
    """Add a verification task to the background queue."""
    verify_queue.put_nowait({"email": email, "post_url": post_url})

def _verify_comment_sync(email: str, post_url: str) -> bool:
    """Sync Playwright logic to open the post and check for the user's comment."""
    _, state_path = build_session_state_path(session_id=None, email=email)
    
    if not state_path.exists():
        logger.warning("No session found for %s to verify comment.", email)
        return False
        
    # Try to load user's real name from metadata
    full_name: Optional[str] = None
    metadata_path = build_session_metadata_path(session_id=None, email=email)
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            full_name = metadata.get("full_name")
        except Exception:
            pass
        
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=settings.headless,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        
        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("load", timeout=15000)
            
            # Scroll down to ensure comments load
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(3000)
            
            try:
                load_more = page.locator("button.comments-comments-list__load-more-comments-button")
                if load_more.count() > 0 and load_more.is_visible():
                    load_more.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass
            
            comment_articles = page.locator("article.comments-comment-item")
            count = comment_articles.count()
            
            for i in range(count):
                comment = comment_articles.nth(i)
                text = comment.inner_text().lower()
                first_line = text.split("\n")[0].lower() if text else ""
                
                # Check for "You" or "Bạn" or real name in the author name area
                if "bạn" == first_line.strip() or "you" == first_line.strip():
                    return True
                
                if full_name and full_name.lower() in first_line:
                    return True
                    
                # Look for the control menu inside the comment. Only authors have the option to edit/delete their comment.
                controls = comment.locator("button[aria-label*='Control menu'], button[aria-label*='Options']")
                if controls.count() > 0:
                    return True
                    
            return False
            
        except Error as exc:
            logger.warning("Failed to verify comment: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Exception during verification: %s", exc)
            return False
        finally:
            context.close()
            browser.close()
