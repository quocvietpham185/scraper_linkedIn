from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime

from app.config import settings
from app.schemas.request_models import StartWorkflowRequest
from app.services import crawl_task_service
from app.services import ranking_service
from app.services import seeding_task_service
from app.services.apify_crawler_service import run_apify_crawler_for_group, run_apify_crawler_for_groups
from app.services.crawl_orchestrator_service import crawl_group_with_tiers
from app.services.google_sheet_service import build_top_post_row_values, append_top_post_rows, read_top_post_header_row
from app.services.google_sheet_crud import append_error_log_to_sheet
from app.services.telegram_service import (
    format_crawl_finish_message,
    format_crawl_group_error_message,
    format_crawl_group_no_match_message,
    format_crawl_group_success_message,
    format_crawl_start_message,
    send_telegram_message,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def run_background_crawl(session_id: str, request: StartWorkflowRequest) -> None:
    group_urls = request.group_urls or []
    total = len(group_urls)
    success_count = 0
    failed_count = 0
    no_match_count = 0
    target_date = request.target_date or datetime.now().strftime("%Y-%m-%d")
    crawler_type = getattr(request, "crawler_type", "auto")
    max_posts = request.max_posts or 50
    scroll_times = max(settings.default_scroll_times, math.ceil(max_posts / 8))

    crawl_task_service.update_task_status(session_id, "running", f"Dang crawl 0/{total} nhom")
    send_telegram_message(
        format_crawl_start_message(
            session_id=session_id,
            email=request.email,
            total_groups=total,
            target_date=target_date,
            crawler_type=crawler_type,
        ),
    )

    if not group_urls:
        crawl_task_service.update_task_status(session_id, "completed", "Khong co nhom nao de crawl.")
        send_telegram_message(
            format_crawl_finish_message(
                session_id=session_id,
                email=request.email,
                total_groups=0,
                success_count=0,
                failed_count=0,
                no_match_count=0,
            ),
        )
        return

    if crawler_type == "apify" and settings.apify_own_actor_enabled:
        logger.info("Start Apify batch crawl: groups=%d email=%s", total, request.email)
        batch_results = await run_apify_crawler_for_groups(
            group_urls,
            email=request.email,
            max_items=max_posts,
            target_date=target_date,
            scroll_times=scroll_times,
        )

        for index, url in enumerate(group_urls):
            crawl_task_service.update_task_status(
                session_id,
                "running",
                f"Dang crawl {index + 1}/{total} nhom bang Apify batch...",
            )

            try:
                group_item = batch_results.get(url) or {
                    "success": False,
                    "error": "Apify batch did not return a result for this group",
                    "posts": [],
                }

                if not group_item.get("success") and settings.apify_3rd_party_fallback_enabled:
                    logger.warning(
                        "Apify own batch failed for %s, trying third-party fallback: %s",
                        url,
                        group_item.get("error"),
                    )
                    third_result = await run_apify_crawler_for_group(
                        url,
                        kind="third_party",
                        max_items=max_posts,
                        target_date=target_date,
                        scroll_times=scroll_times,
                    )
                    if third_result.get("success"):
                        group_item = third_result
                    else:
                        group_item["error"] = (
                            f"tier2_apify_own_actor[{group_item.get('error_type') or 'failed'}]: "
                            f"{group_item.get('error') or 'Apify own actor failed'} | "
                            f"tier3_apify_3rd_party[{third_result.get('error_type') or 'failed'}]: "
                            f"{third_result.get('error') or 'Apify third-party actor failed'}"
                        )

                if not group_item.get("success"):
                    raise RuntimeError(str(group_item.get("error") or "Apify failed"))

                raw_posts = group_item.get("posts") or []
                group_name = group_item.get("group_name") or "Unknown Group"
                member_count = group_item.get("member_count") or 0
                filter_time = datetime.now()
                filtered_posts, _ = ranking_service.enrich_and_filter_posts(raw_posts, target_date, filter_time)
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
                        session_id=session_id,
                    )
                    append_top_post_rows([row])
                    try:
                        seeding_task_service.add_seeding_tasks_bulk(
                            email=request.email,
                            posts=[top_post],
                            group_name=group_name,
                            group_url=url,
                        )
                    except Exception as seed_exc:
                        logger.warning("Auto-add seeding task failed for %s: %s", url, seed_exc)
                    success_count += 1
                    msg = format_crawl_group_success_message(
                        index=index + 1,
                        total=total,
                        email=request.email,
                        group_name=group_name,
                        group_url=url,
                        target_date=target_date,
                        source=str(group_item.get("source") or "apify_batch"),
                        total_posts=len(raw_posts),
                        member_count=int(member_count or 0),
                        top_post=top_post,
                    )
                else:
                    no_match_count += 1
                    msg = format_crawl_group_no_match_message(
                        index=index + 1,
                        total=total,
                        email=request.email,
                        group_name=group_name,
                        group_url=url,
                        target_date=target_date,
                        source=str(group_item.get("source") or "apify_batch"),
                        total_posts=len(raw_posts),
                    )
                    append_error_log_to_sheet(
                        date=target_date,
                        group_name=group_name,
                        group_url=url,
                        email=request.email,
                        message=msg,
                        error=(
                            "No posts matched target date. Parsed post dates:\n"
                            + ranking_service.summarize_post_dates(raw_posts, filter_time)
                        ),
                    )

                send_telegram_message(msg)
            except Exception as exc:
                failed_count += 1
                logger.exception("Apify batch result failed for %s", url)
                append_error_log_to_sheet(
                    date=target_date,
                    group_name="Unknown Group",
                    group_url=url,
                    email=request.email,
                    message="Loi crawl du lieu qua Apify batch",
                    error=str(exc),
                )
                send_telegram_message(
                    format_crawl_group_error_message(
                        index=index + 1,
                        total=total,
                        email=request.email,
                        group_url=url,
                        target_date=target_date,
                        error=exc,
                    ),
                )

            crawl_task_service.update_task_status(
                session_id,
                "running",
                f"Dang crawl {index + 1}/{total} nhom - OK {success_count}, khong khop {no_match_count}, loi {failed_count}",
            )

        final_status = "completed" if failed_count == 0 else "failed"
        final_message = f"Hoan thanh crawl {total}/{total} nhom. OK {success_count}, khong khop {no_match_count}, loi {failed_count}."
        crawl_task_service.update_task_status(session_id, final_status, final_message)
        send_telegram_message(
            format_crawl_finish_message(
                session_id=session_id,
                email=request.email,
                total_groups=total,
                success_count=success_count,
                failed_count=failed_count,
                no_match_count=no_match_count,
            ),
        )
        return

    for index, url in enumerate(group_urls):
        crawl_task_service.update_task_status(
            session_id,
            "running",
            f"Dang crawl {index + 1}/{total} nhom...",
        )

        try:
            logger.info("Start tiered crawl: url=%s mode=%s", url, crawler_type)
            tiered = await crawl_group_with_tiers(
                group_url=url,
                mode=crawler_type,
                session_id=None,
                email=request.email,
                max_items=max_posts,
                target_date=target_date,
                scroll_times=scroll_times,
            )
            group_item = tiered.group_item
            if not tiered.success or not group_item:
                raise RuntimeError(tiered.error_summary or "All crawler tiers failed")

            raw_posts = group_item.get("posts") or []
            group_name = group_item.get("group_name") or "Unknown Group"
            member_count = group_item.get("member_count") or 0

            filter_time = datetime.now()
            filtered_posts, _ = ranking_service.enrich_and_filter_posts(raw_posts, target_date, filter_time)
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
                    session_id=session_id,
                )
                append_top_post_rows([row])
                try:
                    seeding_task_service.add_seeding_tasks_bulk(
                        email=request.email,
                        posts=[top_post],
                        group_name=group_name,
                        group_url=url,
                    )
                except Exception as seed_exc:
                    logger.warning("Auto-add seeding task failed for %s: %s", url, seed_exc)
                success_count += 1
                msg = format_crawl_group_success_message(
                    index=index + 1,
                    total=total,
                    email=request.email,
                    group_name=group_name,
                    group_url=url,
                    target_date=target_date,
                    source=tiered.source,
                    total_posts=len(raw_posts),
                    member_count=int(member_count or 0),
                    top_post=top_post,
                )
                logger.info("Saved top post: url=%s source=%s", url, tiered.source)
            else:
                no_match_count += 1
                msg = format_crawl_group_no_match_message(
                    index=index + 1,
                    total=total,
                    email=request.email,
                    group_name=group_name,
                    group_url=url,
                    target_date=target_date,
                    source=tiered.source,
                    total_posts=len(raw_posts),
                )
                append_error_log_to_sheet(
                    date=target_date,
                    group_name=group_name,
                    group_url=url,
                    email=request.email,
                    message=msg,
                    error=(
                        "No posts matched target date. Parsed post dates:\n"
                        + ranking_service.summarize_post_dates(raw_posts, filter_time)
                    ),
                )

            send_telegram_message(msg)

        except Exception as exc:
            failed_count += 1
            logger.exception("Tiered crawl failed for %s", url)
            append_error_log_to_sheet(
                date=target_date,
                group_name="Unknown Group",
                group_url=url,
                email=request.email,
                message="Loi crawl du lieu qua tat ca tier",
                error=str(exc),
            )
            send_telegram_message(
                format_crawl_group_error_message(
                    index=index + 1,
                    total=total,
                    email=request.email,
                    group_url=url,
                    target_date=target_date,
                    error=exc,
                ),
            )

        crawl_task_service.update_task_status(
            session_id,
            "running",
            f"Dang crawl {index + 1}/{total} nhom - OK {success_count}, khong khop {no_match_count}, loi {failed_count}",
        )

        if index < total - 1:
            delay_min = min(settings.crawl_batch_group_delay_min_sec, settings.crawl_batch_group_delay_max_sec)
            delay_max = max(settings.crawl_batch_group_delay_min_sec, settings.crawl_batch_group_delay_max_sec)
            delay_sec = random.uniform(delay_min, delay_max)
            logger.info("Waiting %.2fs before crawling the next group for account %s", delay_sec, request.email)
            await asyncio.sleep(delay_sec)

    final_status = "completed" if failed_count == 0 else "failed"
    final_message = f"Hoan thanh crawl {total}/{total} nhom. OK {success_count}, khong khop {no_match_count}, loi {failed_count}."
    crawl_task_service.update_task_status(session_id, final_status, final_message)
    send_telegram_message(
        format_crawl_finish_message(
            session_id=session_id,
            email=request.email,
            total_groups=total,
            success_count=success_count,
            failed_count=failed_count,
            no_match_count=no_match_count,
        ),
    )
