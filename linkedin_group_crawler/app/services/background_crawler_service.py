from __future__ import annotations
import asyncio
import random
from typing import Any
from datetime import datetime

from app.schemas.request_models import StartWorkflowRequest
from app.services import crawl_task_service
from app.services import crawler_service
from app.services import ranking_service
from app.services.google_sheet_service import build_top_post_row_values, append_top_post_rows, read_top_post_header_row
from app.services.google_sheet_crud import append_error_log_to_sheet
from app.services.telegram_service import send_telegram_message
from app.utils.logger import get_logger

logger = get_logger(__name__)

async def _to_thread(func, *args, **kwargs):
    """Compatibility helper for Python 3.8 (asyncio.to_thread was added in 3.9)."""
    import functools
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

async def run_background_crawl(session_id: str, request: StartWorkflowRequest) -> None:
    group_urls = request.group_urls or []
    total = len(group_urls)
    
    crawl_task_service.update_task_status(session_id, "running", f"Đang cào 0/{total} nhóm")
    
    if not group_urls:
        crawl_task_service.update_task_status(session_id, "completed", "Không có nhóm nào để cào.")
        return
        
    for index, url in enumerate(group_urls):
        crawl_task_service.update_task_status(session_id, "running", f"Đang cào {index}/{total} nhóm...")
        
        try:
            # crawl data
            logger.info(f"Bắt đầu cào nhóm: {url}")
            
            scraped_items = None
            crawl_error = None
            
            try:
                if getattr(request, "crawler_type", "playwright") == "apify":
                    # Ép buộc dùng Apify ngay từ đầu
                    from app.services.apify_crawler_service import run_apify_crawler_for_group
                    apify_res = await run_apify_crawler_for_group(url)
                    if apify_res.get("success"):
                        group_item = apify_res
                    else:
                        raise Exception(apify_res.get("error", "Apify failed"))
                else:
                    # Mặc định dùng Playwright
                    group_item = await _to_thread(
                        crawler_service.open_group_and_collect_posts,
                        session_id=None, # Để session_id=None để hệ thống tự resolve từ email (tránh lỗi random suffix)
                        email=request.email,
                        group_url=url,
                        max_items=request.max_posts or 50,
                    )
            except Exception as crawl_exc:
                crawl_error = str(crawl_exc)
                group_item = None
                
            # Xử lý Fallback Apify
            if not group_item and crawl_error:
                from app.config import settings
                if settings.apify_fallback_enabled:
                    logger.warning(f"Playwright lỗi cào nhóm {url}: {crawl_error}. Kích hoạt Apify Fallback...")
                    from app.services.apify_crawler_service import run_apify_crawler_for_group
                    send_telegram_message(f"⚠️ Playwright gặp lỗi tại nhóm {url}, đang kích hoạt Apify Fallback...")
                    apify_res = await run_apify_crawler_for_group(url)
                    if apify_res.get("success"):
                        group_item = apify_res
                    else:
                        logger.error(f"Apify Fallback cũng thất bại cho {url}: {apify_res.get('error')}")
                        group_item = None
                else:
                    raise Exception(crawl_error)

            if not group_item:
                raise Exception("Không thu thập được bài viết nào từ nhóm (cả Playwright và Apify đều thất bại).")
                
            raw_posts = group_item.get("posts") or []
            group_name = group_item.get("group_name") or "Unknown Group"
            member_count = group_item.get("member_count") or 0
            
            target_date = request.target_date or datetime.now().strftime("%Y-%m-%d")
            filtered_posts, dt = ranking_service.enrich_and_filter_posts(raw_posts, target_date, datetime.now())
            top_post = ranking_service.pick_top_post(filtered_posts)
            
            if top_post:
                headers = read_top_post_header_row()
                row = build_top_post_row_values(
                    headers=headers,
                    email_crawl=request.email,
                    crawl_date=target_date,
                    group_name=group_name,
                    group_url=url,
                    total_posts_in_run=len(raw_posts),
                    member_count=member_count,
                    post=top_post,
                    session_id=session_id
                )
                append_top_post_rows([row])
                msg = (
                    f"✅ <b>Thông báo kết quả cào LinkedIn</b>\n\n"
                    f"👥 <b>Nhóm:</b> {group_name}\n"
                    f"🔗 <b>Link Group:</b> {url}\n\n"
                    f"📝 <b>Top Post:</b>\n"
                    f"🔗 <a href='{top_post.get('post_url')}'>Link to post</a>\n"
                    f"📄 <b>Nội dung:</b> {str(top_post.get('content'))[:200]}...\n"
                    f"👍 <b>Likes:</b> {top_post.get('likes')}\n"
                    f"💬 <b>Comments:</b> {top_post.get('comments')}\n"
                    f"📊 <b>Score:</b> {top_post.get('score')}"
                )
                logger.info(f"Đã lưu top post cho nhóm {url}")
            else:
                msg = (
                    f"⚠️ Nhóm <b>{group_name}</b> không có bài viết nào phù hợp trong ngày {target_date}.\n"
                    f"🔗 <b>Link:</b> {url}"
                )
                append_error_log_to_sheet(
                    date=target_date,
                    group_name=group_name,
                    group_url=url,
                    email=request.email,
                    message=msg,
                    error="No posts matched"
                )
            
            # Send Telegram 
            send_telegram_message(msg)

        except Exception as e:
            logger.exception(f"Lỗi khi cào nhóm {url}")
            date_str = request.target_date or datetime.now().strftime("%Y-%m-%d")
            append_error_log_to_sheet(
                date=date_str,
                group_name="Unknown Group",
                group_url=url,
                email=request.email,
                message="Lỗi cào dữ liệu",
                error=str(e)
            )
            send_telegram_message(f"❌ Lỗi cào nhóm {url}\n{str(e)}")
            
        if index < total - 1:
            delay = random.randint(5, 10)
            logger.info(f"Nghỉ {delay} giây trước khi cào nhóm tiếp theo...")
            await asyncio.sleep(delay)
            
    crawl_task_service.update_task_status(session_id, "completed", f"Hoàn thành cào {total}/{total} nhóm.")
    send_telegram_message("🎉 Đã hoàn thành tiến trình cào dữ liệu toàn bộ nhóm!")
