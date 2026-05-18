from __future__ import annotations

import asyncio
import math
import random
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.config import settings
from app.schemas.request_models import StartWorkflowRequest
from app.services import crawl_task_service
from app.services import ranking_service
from app.services import seeding_task_service
from app.services.apify_crawler_service import run_apify_crawler_for_group, run_apify_crawler_for_groups
from app.services.crawl_orchestrator_service import crawl_group_with_tiers
from app.services.google_sheet_service import (
    append_top_post_rows,
    build_top_post_row_values,
    read_group_url_rows,
    read_top_post_header_row,
)
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


def _chunks(items: list[str], size: int) -> list[list[str]]:
    chunk_size = max(1, size)
    return [items[index:index + chunk_size] for index in range(0, len(items), chunk_size)]


def _post_date_yyyy_mm_dd(post: dict) -> str:
    value = (
        post.get("posted_at")
        or post.get("day_up")
        or post.get("datetime")
        or post.get("timestamp")
        or post.get("posted_at_raw")
        or post.get("text_date")
        or ""
    )

    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    # ISO: 2026-05-15T10:48:46.221Z
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]

    # Unix seconds / milliseconds
    if text.isdigit():
        try:
            ts = int(text)
            if ts > 10_000_000_000:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return ""

    return ""


def _filter_posts_by_target_date(posts: list[dict], target_date: str) -> list[dict]:
    matched = []
    for post in posts:
        post_date = _post_date_yyyy_mm_dd(post)
        post["_parsed_date"] = post_date
        if post_date == target_date:
            matched.append(post)
    return matched


def _normalize_lookup_text(value: object) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _pick_row_value(row: dict, aliases: set[str]) -> str:
    alias_keys = {_normalize_lookup_text(alias) for alias in aliases}
    for key, value in row.items():
        if _normalize_lookup_text(key) in alias_keys:
            return str(value or "").strip()
    return ""


def _normalize_group_url_key(url: object) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme and not parsed.netloc:
        text = "https://" + text.lstrip("/")
        parsed = urlparse(text)
    path = (parsed.path or "").rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower()


def _parse_member_count(value: object) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _load_group_metadata_by_url(email: str) -> dict[str, dict[str, object]]:
    try:
        rows = read_group_url_rows()
    except Exception as exc:
        logger.warning("Cannot read group metadata rows from sheet: %s", exc)
        return {}

    metadata: dict[str, dict[str, object]] = {}
    wanted_email = str(email or "").strip().lower()
    for row in rows:
        row_email = _pick_row_value(row, {"email", "email_crawl", "tai khoan", "account"})
        if row_email and wanted_email and row_email.lower() != wanted_email:
            continue

        group_url = _pick_row_value(
            row,
            {"url_nhom", "url nhóm", "url_groups", "group_url", "url group", "url"},
        )
        key = _normalize_group_url_key(group_url)
        if not key:
            continue

        group_name = _pick_row_value(
            row,
            {"ten_nhom", "tên nhóm", "name_group", "group_name", "name", "nhom", "group"},
        )
        member_count = _parse_member_count(
            _pick_row_value(
                row,
                {"member", "members", "member_count", "membercount", "so_thanh_vien", "số thành viên", "thanh_vien", "thành viên"},
            ),
        )
        metadata[key] = {
            "group_name": group_name,
            "member_count": member_count,
        }
    return metadata


def _resolve_group_metadata(
    group_item: dict,
    group_url: str,
    metadata_by_url: dict[str, dict[str, object]],
) -> tuple[str, int]:
    sheet_metadata = metadata_by_url.get(_normalize_group_url_key(group_url), {})
    actor_name = str(group_item.get("group_name") or "").strip()
    sheet_name = str(sheet_metadata.get("group_name") or "").strip()
    actor_member_count = _parse_member_count(group_item.get("member_count") or group_item.get("members"))
    sheet_member_count = _parse_member_count(sheet_metadata.get("member_count"))

    group_name = actor_name or sheet_name or "Unknown Group"
    if sheet_name and (not actor_name or (actor_name.startswith("Group ") and actor_name[6:].isdigit())):
        group_name = sheet_name

    member_count = actor_member_count or sheet_member_count
    return group_name, member_count


def _is_placeholder_content(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()
    return (
        not normalized
        or normalized == "no alternative text description for this image"
        or normalized.startswith("view group:")
        or normalized.startswith("view newsletter:")
    )


def _prepare_top_post_for_output(post: dict) -> dict:
    prepared = dict(post)
    content = str(prepared.get("content") or "").strip()
    if _is_placeholder_content(content):
        content = ""
        for key in ("text", "caption", "commentary", "description", "title"):
            candidate = str(prepared.get(key) or "").strip()
            if candidate and not _is_placeholder_content(candidate):
                content = candidate
                break
    if not content:
        for key in ("image_url", "imageUrl", "media_url", "mediaUrl", "thumbnail_url", "thumbnail", "image"):
            media_url = str(prepared.get(key) or "").strip()
            if media_url.startswith(("http://", "https://")):
                content = f"Bài viết dạng hình ảnh/media: {media_url}"
                break
    if not content:
        content = "Bài viết dạng hình ảnh hoặc media, chưa đọc được caption từ LinkedIn."
    prepared["content"] = content
    return prepared


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
    group_metadata_by_url = _load_group_metadata_by_url(request.email)

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
        batches = _chunks(group_urls, settings.apify_batch_size)
        processed_count = 0
        for batch_index, batch_urls in enumerate(batches, start=1):
            logger.info(
                "Start Apify actor run batch %d/%d: groups=%d email=%s",
                batch_index,
                len(batches),
                len(batch_urls),
                request.email,
            )
            crawl_task_service.update_task_status(
                session_id,
                "running",
                f"Dang crawl Apify batch {batch_index}/{len(batches)} ({len(batch_urls)} nhom)...",
            )
            batch_results = await run_apify_crawler_for_groups(
                batch_urls,
                email=request.email,
                session_id=session_id,
                max_items=max_posts,
                target_date=target_date,
                scroll_times=scroll_times,
            )

            for url in batch_urls:
                processed_count += 1
                crawl_task_service.update_task_status(
                    session_id,
                    "running",
                    f"Dang xu ly ket qua {processed_count}/{total} nhom bang Apify batch...",
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
                            email=request.email,
                            session_id=session_id,
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
                    group_name, member_count = _resolve_group_metadata(group_item, url, group_metadata_by_url)
                    filter_time = datetime.now()
                    filtered_posts = _filter_posts_by_target_date(raw_posts, target_date)

                    if not filtered_posts:
                        filtered_posts, _ = ranking_service.enrich_and_filter_posts(raw_posts, target_date, filter_time)

                    if not filtered_posts:
                        logger.warning(
                            "No posts matched target_date=%s for %s. Parsed dates sample=%s",
                            target_date,
                            url,
                            [p.get("_parsed_date") for p in raw_posts[:20]],
                        )
                    else:
                        for post in filtered_posts:
                            post["score"] = ranking_service.compute_score(post)

                    top_post = ranking_service.pick_top_post(filtered_posts)

                    if top_post:
                        top_post = _prepare_top_post_for_output(top_post)
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
                            index=processed_count,
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
                            index=processed_count,
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
                            index=processed_count,
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
                    f"Dang crawl {processed_count}/{total} nhom - OK {success_count}, khong khop {no_match_count}, loi {failed_count}",
                )

            if batch_index < len(batches):
                delay_sec = 300
                logger.info(
                    "Batch %d/%d processed. Waiting %.2fs before next Apify actor run batch for account %s",
                    batch_index,
                    len(batches),
                    delay_sec,
                    request.email,
                )
                crawl_task_service.update_task_status(
                    session_id,
                    "running",
                    f"Da gui ket qua batch {batch_index}/{len(batches)}, nghi 5 phut truoc batch tiep theo...",
                )
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
            group_name, member_count = _resolve_group_metadata(group_item, url, group_metadata_by_url)

            filter_time = datetime.now()
            filtered_posts = _filter_posts_by_target_date(raw_posts, target_date)

            if not filtered_posts:
                filtered_posts, _ = ranking_service.enrich_and_filter_posts(raw_posts, target_date, filter_time)

            if not filtered_posts:
                logger.warning(
                    "No posts matched target_date=%s for %s. Parsed dates sample=%s",
                    target_date,
                    url,
                    [p.get("_parsed_date") for p in raw_posts[:20]],
                )
            else:
                for post in filtered_posts:
                    post["score"] = ranking_service.compute_score(post)

            top_post = ranking_service.pick_top_post(filtered_posts)

            if top_post:
                top_post = _prepare_top_post_for_output(top_post)
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
