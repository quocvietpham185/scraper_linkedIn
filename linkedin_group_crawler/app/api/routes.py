"""API routes for LinkedIn group crawler."""
from __future__ import annotations


from datetime import date, datetime, timedelta
import json
import random
import re
import time
from typing import Any, Optional, Dict
from pydantic import BaseModel
from typing_extensions import Annotated

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, status, BackgroundTasks
from fastapi.responses import JSONResponse

from app.config import BASE_DIR, settings
from app.schemas.request_models import (
    AddListGroupRequest,
    CrawlGroupRequest,
    FilterDataRequest,
    GetAllPostsRequest,
    LinkedinAppCrawlBatchRequest,
    LinkedinAppFilterPostsSheetRequest,
    LinkedinAppGetAllPostsSheetRequest,
    LoginRequest,
    AddGroupRequest,
    N8nCredentialWebhookRequest,
    GetAllGroupsRequest,
    GetSheetLinkRequest,
    RemoveGroupRequest,
    UpdateGroupRequest,
    N8nWebhookPassthroughRequest,
    ProfileCommentsRequest,
    StartWorkflowRequest,
    VerifyLoginRequest,
    KpiReportRequest,
)
from app.schemas.response_models import (
    BaseResponse,
    BulkGroupImportData,
    BulkGroupImportResponse,
    BulkGroupImportScrapedItem,
    CrawlDataResponse,
    CrawlResponse,
    FilterDataResponse,
    GetAllPostsResponse,
    LinkedinAppCrawlBatchData,
    LinkedinAppCrawlBatchResponse,
    LinkedinAppCrawlGroupResult,
    LinkedinSheetFilterPostsResponse,
    LinkedinSheetGroupsData,
    LinkedinSheetGroupsResponse,
    LinkedinSheetTopPostsData,
    LinkedinSheetTopPostsResponse,
    LoginResponse,
    StartCrawlData,
    StartCrawlResponse,
    GetSheetLinkData,
    GetSheetLinkResponse,
    StatusDataResponse,
    StatusResponse,
    TopPostResponse,
    VerifyLoginResponse,
    CrawlTaskStatusData,
    CrawlTaskStatusResponse,
)
from app.services import kpi_service
from app.services import comment_verifier
from app.services import crawl_task_service
from app.services import seeding_task_service
from app.services import google_sheet_crud
from app.services import google_sheet_service as gsheet
from app.services import group_status_service
from app.services import live_feed_service
from app.services.background_crawler_service import run_background_crawl
from app.services.auth_service import (
    PendingLoginSessionNotFoundError,
    login_and_save_session,
    verify_pending_login_otp,
)
from app.services.crawler_service import open_group_and_collect_posts
from app.services.apify_crawler_service import run_apify_crawler_for_group
from app.services.group_bulk_import_service import bulk_scrape_groups
from app.services.telegram_service import send_telegram_message
from app.services import google_sheet_service as gsheet
from app.services.post_filter_service import (
    build_crawl_sessions_from_posts,
    filter_posts_by_inclusive_date_range,
    normalize_n8n_posts,
    posts_from_n8n_payload,
)
from app.services.ranking_service import (
    enrich_and_filter_posts,
    pick_top_post,
    select_most_recent_posts,
)
from app.utils.logger import get_logger


router = APIRouter()
logger = get_logger(__name__)


def _state_path_for_response(state_path) -> str:
    """Return a user-friendly state path for API responses."""

    try:
        return state_path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(state_path)


def _compute_filter_date_window(payload: FilterDataRequest) -> tuple[date | None, date | None]:
    """Trả về (start, end) inclusive theo payload; cả hai None = không lọc ngày."""

    today = datetime.now().date()
    if payload.preset == "last_7_days":
        return today - timedelta(days=7), today
    if payload.preset == "last_30_days":
        return today - timedelta(days=30), today

    if payload.date_from or payload.date_to:
        start_d = date.fromisoformat(payload.date_from) if payload.date_from else None
        end_d = date.fromisoformat(payload.date_to) if payload.date_to else None
        if start_d is None and end_d is not None:
            start_d = end_d
        elif end_d is None and start_d is not None:
            end_d = today
        if start_d is not None and end_d is not None and start_d > end_d:
            raise ValueError("date_from phải nhỏ hơn hoặc bằng date_to")
        return start_d, end_d

    if payload.date:
        d = date.fromisoformat(payload.date)
        return d, d

    return None, None


def _crawl_id_session_prefix(email: str | None, session_id: str | None, resolved_linkedin_session_id: str) -> str:
    """Đồng nhất cách tạo prefix với auth_service.py: dùng toàn bộ email làm slug."""

    raw_email = (email or "").strip().lower()
    if raw_email and "@" in raw_email:
        # Thay thế toàn bộ ký tự đặc biệt trong email thành gạch dưới để khớp với auth_service
        slug = re.sub(r"[^a-z0-9]+", "_", raw_email).strip("_")
        return slug
    
    # Fallback cho session_id cũ
    fallback = (raw_email or (session_id or "").strip().lower() or (resolved_linkedin_session_id or "").strip().lower())
    if fallback:
        # Cho phép dấu chấm (.) trong slug để khớp với file storage
        slug = re.sub(r"[^a-z0-9._-]+", "-", fallback).strip("._-")[:80]
        return slug or "session"
    return "session"


def _extract_webhook_message_and_payload(raw_preview: str) -> tuple[str | None, Any]:
    """Parse preview text thành message/payload để frontend hiển thị dễ hơn."""

    text = (raw_preview or "").strip()
    if not text:
        return None, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text, None

    if isinstance(parsed, dict):
        for key in ("message", "msg", "detail", "status", "result"):
            value = parsed.get(key)
            if isinstance(value, str):
                message = value.strip()
                if message:
                    return message, parsed
        return text, parsed
    if isinstance(parsed, str):
        parsed_text = parsed.strip()
        return (parsed_text or text), parsed
    return text, parsed


def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Optionally protect endpoints with an API key."""

    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


@router.get("/health", response_model=BaseResponse)
def health_check() -> BaseResponse:
    """Health check endpoint."""

    return BaseResponse(success=True, message="Service is healthy")


@router.get("/status", response_model=StatusResponse)
def system_status() -> StatusResponse:
    """Return runtime configuration needed by the frontend."""

    return StatusResponse(
        success=True,
        message="Service status loaded",
        data=StatusDataResponse(
            api_key_enabled=bool(settings.api_key),
            headless=settings.headless,
            default_max_items=settings.default_max_items,
            default_scroll_times=settings.default_scroll_times,
            cors_origins=settings.cors_origins or [],
            n8n_webhook_configured=bool((settings.n8n_webhook_url or "").strip()),
            n8n_get_link_webhook_configured=bool(
                (settings.n8n_webhook_get_link_url or "").strip(),
            ),
            n8n_webhook_get_post_crawled_configured=bool(
                (settings.n8n_webhook_get_post_crawled_url or "").strip(),
            ),
            n8n_webhook_get_url_group_crawled_configured=bool(
                (settings.n8n_webhook_get_url_group_crawled_url or "").strip(),
            ),
            n8n_webhook_get_result_crawl_by_id_configured=bool(
                (settings.n8n_webhook_get_result_crawl_by_id_url or "").strip(),
            ),
            n8n_webhook_filter_data_configured=bool(
                (settings.n8n_webhook_get_all_posts_url or "").strip(),
            ),
            n8n_webhook_get_all_posts_configured=bool(
                (settings.n8n_webhook_get_all_posts_url or "").strip(),
            ),
            n8n_webhook_start_configured=bool(
                (settings.n8n_webhook_start_url or "").strip(),
            ),
            n8n_webhook_get_group_configured=bool(
                (settings.n8n_webhook_get_group_url or "").strip(),
            ),
            n8n_webhook_add_group_configured=bool(
                (settings.n8n_webhook_add_group_url or "").strip(),
            ),
            n8n_webhook_remove_group_configured=bool(
                (settings.n8n_webhook_remove_group_url or "").strip(),
            ),
            n8n_webhook_update_group_configured=bool(
                (settings.n8n_webhook_update_group_url or "").strip(),
            ),
            google_sheet_configured=gsheet.spreadsheet_configured(),
        ),
    )


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(verify_api_key)])
def login(payload: LoginRequest) -> LoginResponse:
    """Login to LinkedIn. Returns need_otp when email challenge is required."""

    try:
        result = login_and_save_session(
            email=payload.email,
            password=payload.password,
            session_id=payload.session_id,
            force_relogin=payload.force_relogin,
        )
        if result.status == "need_otp":
            return LoginResponse(
                success=True,
                message="LinkedIn yêu cầu mã xác minh. Gọi POST /verify với mã OTP.",
                session_id=result.session_id,
                state_path=None,
                email=result.email,
                login_step="need_otp",
                need_otp=True,
                checkpoint_url=result.checkpoint_url,
            )
        return LoginResponse(
            success=True,
            message="LinkedIn session saved successfully",
            session_id=result.session_id,
            state_path=_state_path_for_response(result.state_path) if result.state_path else None,
            email=result.email,
            login_step="success",
            need_otp=False,
            checkpoint_url=None,
        )
    except Exception as exc:
        logger.exception("Login endpoint failed")
        return LoginResponse(
            success=False,
            message=str(exc),
            session_id=None,
            state_path=None,
            login_step="error",
            need_otp=False,
            checkpoint_url=None,
        )


@router.post("/verify", response_model=VerifyLoginResponse, dependencies=[Depends(verify_api_key)])
def verify_login(payload: VerifyLoginRequest) -> VerifyLoginResponse:
    """Complete LinkedIn OTP verification using pending session from POST /login."""

    try:
        session_id, state_path, email = verify_pending_login_otp(
            pending_session_id=payload.session_id,
            otp_code=payload.otp,
            checkpoint_url=payload.checkpoint_url,
        )
        return VerifyLoginResponse(
            success=True,
            message="Xác minh OTP thành công. Session LinkedIn đã được lưu.",
            session_id=session_id,
            state_path=_state_path_for_response(state_path),
            email=email,
            login_step="success",
            need_otp=False,
            checkpoint_url=None,
        )
    except PendingLoginSessionNotFoundError as exc:
        return VerifyLoginResponse(
            success=False,
            message=str(exc),
            session_id=None,
            state_path=None,
            email=None,
            login_step="error",
            need_otp=False,
            checkpoint_url=None,
        )
    except Exception as exc:
        logger.exception("Verify endpoint failed")
        return VerifyLoginResponse(
            success=False,
            message=str(exc),
            session_id=None,
            state_path=None,
            email=None,
            login_step="error",
            need_otp=False,
            checkpoint_url=None,
        )


@router.post("/crawl-linkedin-group", response_model=CrawlResponse, dependencies=[Depends(verify_api_key)])
def crawl_linkedin_group(payload: CrawlGroupRequest) -> CrawlResponse:
    """Crawl một nhóm: trả **toàn bộ** bài đúng ngày mục tiêu; không có thì **N** bài gần nhất (cho n8n).

    Option A: Nếu Playwright thất bại và APIFY_FALLBACK_ENABLED=true, tự động thử lại bằng Apify API.
    """
    live_feed_service.add_event("crawl_start", payload.email or "Hệ thống", f"Bắt đầu cào dữ liệu từ nhóm: {payload.group_url}")
    import asyncio

    try:
        if not payload.session_id and not payload.email:
            return CrawlResponse(
                success=False,
                message="Provide either session_id or email so the API can resolve the saved LinkedIn session.",
                data=None,
            )

        playwright_error: Exception | None = None
        crawl_result = None

        # ── Phương án 1: Playwright ──────────────────────────────
        try:
            crawl_result = open_group_and_collect_posts(
                session_id=payload.session_id,
                email=payload.email,
                group_url=payload.group_url,
                max_items=payload.max_items,
            )
        except Exception as exc:
            playwright_error = exc
            logger.warning(
                "Playwright crawler thất bại cho %s: %s",
                payload.group_url,
                exc,
            )

        # ── Option A: Tự động fallback sang Apify nếu Playwright lỗi ───
        if crawl_result is None and playwright_error is not None:
            if not settings.apify_fallback_enabled:
                logger.info(
                    "APIFY_FALLBACK_ENABLED=false, bỏ qua fallback cho %s",
                    payload.group_url,
                )
                return CrawlResponse(
                    success=False,
                    message=(
                        f"Playwright thất bại và Apify fallback chưa bật "
                        f"(APIFY_FALLBACK_ENABLED=false). Lỗi: {playwright_error}"
                    ),
                    data=None,
                )

            logger.info(
                "Kích hoạt Apify fallback cho %s (lồi Playwright: %s)",
                payload.group_url,
                playwright_error,
            )
            try:
                apify_result = asyncio.get_event_loop().run_until_complete(
                    run_apify_crawler_for_group(payload.group_url)
                )
            except RuntimeError:
                # FastAPI sync endpoint: tạo event loop mới nếu cần
                loop = asyncio.new_event_loop()
                try:
                    apify_result = loop.run_until_complete(
                        run_apify_crawler_for_group(payload.group_url)
                    )
                finally:
                    loop.close()

            likes = int(apify_result.get("likes", 0) or 0)
            if likes > 0:
                top_post_data = {
                    "content": apify_result.get("content", ""),
                    "likes": likes,
                    "comments": int(apify_result.get("comments", 0) or 0),
                    "reposts": int(apify_result.get("share", 0) or 0),
                    "post_url": apify_result.get("post_url", ""),
                    "score": likes,
                }
                return CrawlResponse(
                    success=True,
                    message=(
                        f"⚡ Apify fallback OK (Playwright thất bại) — "
                        f"{likes} likes | {payload.group_url}"
                    ),
                    data=CrawlDataResponse(
                        session_id=payload.session_id or "apify_fallback",
                        group_url=payload.group_url,
                        group_name="",
                        target_date="",
                        email=payload.email,
                        total_posts_scraped=1,
                        total_posts_in_target_date=1,
                        top_post=TopPostResponse.from_post_dict(top_post_data),
                        posts=[TopPostResponse.from_post_dict(top_post_data)],
                        selection_mode="apify_fallback",
                    ),
                )
            else:
                return CrawlResponse(
                    success=False,
                    message=(
                        f"⚠️ Apify fallback: Không có bài trong 24h cho {payload.group_url}. "
                        f"(Playwright cũng thất bại: {playwright_error})"
                    ),
                    data=None,
                )

        # ── Xử lý kết quả Playwright thành công ─────────────────────
        filtered_posts, target_day = enrich_and_filter_posts(
            posts=crawl_result["posts"],
            target_date=payload.target_date,
            crawl_time=crawl_result["crawl_time"],
        )

        if crawl_result["total_posts_scraped"] == 0:
            return CrawlResponse(success=False, message="No posts found on the LinkedIn group page", data=None)

        if filtered_posts:
            posts_out = filtered_posts
            selection_mode = "target_day"
            top_post = pick_top_post(filtered_posts)
            msg = f"Crawl OK — {len(posts_out)} bài trong ngày {target_day.isoformat()}"
        else:
            posts_out = select_most_recent_posts(
                list(crawl_result["posts"]),
                limit=payload.fallback_recent_count,
            )
            selection_mode = "fallback_recent"
            top_post = pick_top_post(posts_out) if posts_out else None
            msg = (
                f"Không có bài trong ngày {target_day.isoformat()}, "
                f"trả {len(posts_out)} bài gần nhất"
            )

        response_data = CrawlDataResponse(
            session_id=crawl_result["session_id"],
            group_url=payload.group_url,
            group_name=crawl_result.get("group_name", ""),
            target_date=target_day.isoformat(),
            email=payload.email,
            total_posts_scraped=crawl_result["total_posts_scraped"],
            total_posts_in_target_date=len(filtered_posts),
            top_post=TopPostResponse.from_post_dict(top_post) if top_post else None,
            posts=[TopPostResponse.from_post_dict(p) for p in posts_out],
            selection_mode=selection_mode,
        )

        # Auto-add seeding tasks
        if payload.email and posts_out:
            try:
                seeding_task_service.add_seeding_tasks_bulk(
                    email=payload.email,
                    posts=posts_out,
                    group_name=crawl_result.get("group_name", "Unknown Group"),
                    group_url=payload.group_url
                )
            except Exception as e:
                logger.error(f"Failed to auto-add seeding tasks: {e}")

        return CrawlResponse(success=True, message=msg, data=response_data)
    except Exception as exc:
        logger.exception("Crawl endpoint failed")
        return CrawlResponse(success=False, message=str(exc), data=None)


@router.post("/apify/crawl-group", response_model=CrawlResponse, dependencies=[Depends(verify_api_key)])
async def apify_crawl_group(
    request: Request,
) -> CrawlResponse:
    """Option B: Crawl thủ công 1 nhóm bằng Apify API."""
    try:
        from app.services.ranking_service import enrich_and_filter_posts, pick_top_post
        
        # 1. Parse body thủ công để tránh lỗi 422 validation
        body = await request.json()
        group_url = body.get("group_url")
        if not group_url:
            return CrawlResponse(success=False, message="group_url là bắt buộc", data=None)
            
        target_date = body.get("target_date") or datetime.now().strftime("%Y-%m-%d")
        
        # 2. Lấy email từ body hoặc quét thủ công Cookie
        email = body.get("email")
        if not email:
            from urllib.parse import unquote
            for k, v in request.cookies.items():
                if "email" in k.lower():
                    # Giải mã URL mã hóa (ví dụ %40 -> @)
                    raw_val = v.strip()
                    email = unquote(raw_val)
                    logger.info(f"Đã tìm thấy email từ cookie '{k}': {email}")
                    break
        
        if not email:
            email = "unknown"

        apify_res = await run_apify_crawler_for_group(group_url)
        if not apify_res.get("success"):
             return CrawlResponse(success=False, message=apify_res.get("error", "Apify failed"), data=None)
        
        raw_posts = apify_res.get("posts") or []
        
        filtered_posts, dt = enrich_and_filter_posts(raw_posts, target_date, datetime.now())
        top_post = pick_top_post(filtered_posts)

        if top_post:
            # 1. Update Google Sheet
            try:
                headers = gsheet.read_top_post_header_row()
                row = gsheet.build_top_post_row_values(
                    headers=headers,
                    email_crawl=email,
                    crawl_date=target_date,
                    group_name=apify_res.get("group_name", "Unknown Group"),
                    group_url=group_url,
                    total_posts_in_run=len(raw_posts),
                    member_count=apify_res.get("member_count", 0),
                    post=top_post,
                    session_id="apify_direct"
                )
                gsheet.append_top_post_rows([row])
            except Exception as sheet_exc:
                logger.warning(f"Manual Apify: Lỗi cập nhật sheet: {sheet_exc}")

            # 2. Construct and Send Telegram Message
            group_name = apify_res.get("group_name", "Unknown Group")
            msg = (
                f"✅ <b>Thông báo kết quả cào LinkedIn (Manual)</b>\n\n"
                f"👥 <b>Nhóm:</b> {group_name}\n"
                f"🔗 <b>Link Group:</b> {group_url}\n\n"
                f"📝 <b>Top Post:</b>\n"
                f"🔗 <a href='{top_post.get('post_url')}'>Link to post</a>\n"
                f"📄 <b>Nội dung:</b> {str(top_post.get('content'))[:200]}...\n"
                f"👍 <b>Likes:</b> {top_post.get('likes')}\n"
                f"💬 <b>Comments:</b> {top_post.get('comments')}\n"
                f"📊 <b>Score:</b> {top_post.get('score')}"
            )
            send_telegram_message(msg)

            # Auto-add seeding tasks
            try:
                seeding_task_service.add_seeding_tasks_bulk(
                    email=email,
                    posts=filtered_posts,
                    group_name=group_name,
                    group_url=group_url
                )
            except Exception as e:
                logger.error(f"Failed to auto-add seeding tasks (Apify): {e}")

            return CrawlResponse(
                success=True,
                message=f"🎉 Hoàn thành! Đã cào xong nhóm '{group_name}', lưu dữ liệu vào Google Sheet và gửi báo cáo Telegram.",
                data=CrawlDataResponse(
                    session_id="apify_direct",
                    group_url=group_url,
                    group_name=group_name,
                    target_date=target_date,
                    email=email,
                    total_posts_scraped=len(raw_posts),
                    total_posts_in_target_date=len(filtered_posts),
                    top_post=TopPostResponse.from_post_dict(top_post),
                    posts=[TopPostResponse.from_post_dict(p) for p in filtered_posts],
                    selection_mode="apify_direct",
                ),
            )
        
        # If no posts found, log to sheet but return success=True so UI knows it finished
        group_name = apify_res.get("group_name", "Unknown Group")
        msg_info = f"✅ Hoàn thành: Đã cào nhóm '{group_name}' nhưng không có bài viết nào phù hợp trong ngày {target_date}."
        
        # Gửi Telegram báo cáo kể cả khi không có bài
        tele_msg = (
            f"⚠️ <b>Thông báo kết quả cào LinkedIn (Manual)</b>\n\n"
            f"👥 <b>Nhóm:</b> {group_name}\n"
            f"🔗 <b>Link Group:</b> {group_url}\n\n"
            f"ℹ️ <i>Không tìm thấy bài viết nào phù hợp trong ngày {target_date}.</i>"
        )
        send_telegram_message(tele_msg)

        google_sheet_crud.append_error_log_to_sheet(
            date=target_date,
            group_name=group_name,
            group_url=group_url,
            email=email,
            message=msg_info,
            error="No posts matched target date"
        )
        # Trả về kết quả rỗng hợp lệ để tránh lỗi 500 (schema validation)
        return CrawlResponse(
            success=True,
            message=msg_info,
            data=CrawlDataResponse(
                session_id="apify_direct",
                group_url=group_url,
                group_name=group_name,
                target_date=target_date,
                email=email,
                total_posts_scraped=len(raw_posts),
                total_posts_in_target_date=0,
                top_post=None,
                posts=[],
                selection_mode="apify_direct"
            )
        )
    except Exception as exc:
        logger.exception("Apify crawl endpoint failed")
        return CrawlResponse(success=False, message=str(exc), data=None)


def _truncate_webhook_preview(raw: str, limit: int = 512) -> str:
    text = (raw or "").strip()
    if len(text) > limit:
        return f"{text[:limit]}…"
    return text


def _resolve_crawler_email_for_n8n_groups(
    *,
    body_email: str | None,
    email_crawl: str | None = None,
) -> str:
    """Ưu tiên cookie ``email_crawl`` (frontend), sau đó ``body.email``."""

    merged = ((email_crawl or "").strip() or (body_email or "").strip())
    if not merged:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Thiếu email — cần cookie `email_crawl` hoặc trường `email` trong JSON "
                "để lấy **tất cả nhóm** theo đúng tài khoản crawl."
            ),
        )
    return merged


def _parse_n8n_json_body_optional(full_text: str) -> Any:
    text = (full_text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text[:4096]}


def _pick_n8n_message(parsed: Any) -> str | None:
    """Lấy message từ JSON trả về của node Respond to Webhook (nếu có)."""

    if isinstance(parsed, dict):
        for key in ("message", "msg", "detail"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _resolve_bulk_add_group_email(
    *,
    body_email: str | None,
    email_crawl: str | None = None,
) -> str | None:
    """Ưu tiên cookie ``email_crawl``; fallback ``body.email``; thiếu cả hai vẫn cho phép."""

    merged = ((email_crawl or "").strip() or (body_email or "").strip())
    return merged or None


def _bulk_add_group_webhook_email_payload(email: str) -> dict[str, str]:
    """Giữ alias email nhất quán với các route n8n nhóm khác."""

    e = email.strip()
    return {
        "email": e,
        "Email_crawl": e,
        "userEmail": e,
    }


def _n8n_get_all_groups_webhook_body(email: str) -> dict[str, Any]:
    """Payload gửi n8n: một email thống nhất (alias) để workflow lọc **tất cả nhóm** theo owner."""

    e = email.strip()
    return {
        "email": e,
        "Email_crawl": e,
        "userEmail": e,
    }


def _pick_group_rows(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, dict):
        data = parsed.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    return []


def _pick_group_field(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Find a field in a dictionary by trying several normalized key variations."""
    
    # Pre-calculate normalized keys for the item
    normalized_item = {}
    for k, v in item.items():
        nk = str(k).strip().lower().replace("_", " ").replace(" ", "")
        normalized_item[nk] = v
        
    for key in keys:
        # 1. Try exact match
        if key in item:
            return item.get(key)
            
        # 2. Try normalized match
        nk_target = str(key).strip().lower().replace("_", " ").replace(" ", "")
        if nk_target in normalized_item:
            return normalized_item[nk_target]
            
    return None


def _normalize_n8n_groups(parsed: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in _pick_group_rows(parsed):
        raw_row = _pick_group_field(item, ("row_number", "rowNumber", "stt", "STT"))
        row_number: int | None = None
        try:
            if raw_row is not None and str(raw_row).strip():
                row_number = int(raw_row)
        except (TypeError, ValueError):
            row_number = None

        raw_url = _pick_group_field(
            item,
            ("url_group", "URL_Nhóm", "group_url", "groupUrl", "URL_nhom", "url", "link", "URL nhóm"),
        )
        raw_name = _pick_group_field(
            item,
            ("name_group", "Tên nhóm", "group_name", "groupName", "name", "tên", "Tên nhóm"),
        )
        raw_email = _pick_group_field(item, ("email", "Email", "Email_crawl", "email_crawl", "userEmail", "user_email"))
        raw_member = _pick_group_field(item, ("member", "members", "Thành viên", "thanh_vien", "memberCount", "Số thành viên"))

        url_group = str(raw_url or "").strip()
        if not url_group:
            continue
        name_group = str(raw_name or "").strip()
        email = str(raw_email or "").strip()
        try:
            member = int(raw_member) if raw_member is not None and str(raw_member).strip() else 0
        except (TypeError, ValueError):
            member = 0

        out.append(
            {
                "row_number": row_number,
                "url_group": url_group,
                "name_group": name_group,
                "email": email,
                "member": max(0, member),
            },
        )
    return out


@router.post("/groups/check-status", response_model=BaseResponse, dependencies=[Depends(verify_api_key)])
async def check_group_status(payload: dict[str, str]) -> BaseResponse:
    """Kiểm tra URL nhóm có còn truy cập được không (Live/Dead)."""
    url = payload.get("url")
    if not url:
        return BaseResponse(success=False, message="URL là bắt buộc")
    
    status = await group_status_service.check_single_group_status(url)
    return BaseResponse(success=True, message=status)

@router.get("/groups/all-statuses", dependencies=[Depends(verify_api_key)])
def get_all_group_statuses():
    """Lấy cache trạng thái của toàn bộ nhóm."""
    return {"success": True, "data": group_status_service.get_all_cached_statuses()}


@router.get("/tasks/status/{session_id}", response_model=CrawlTaskStatusResponse, dependencies=[Depends(verify_api_key)])
def get_task_status_api(session_id: str):
    """Lấy trạng thái của một task cào dữ liệu cụ thể."""
    status_data = crawl_task_service.get_task_status(session_id)
    if not status_data:
        return CrawlTaskStatusResponse(success=False, message="Task không tồn tại", data=None)
    return CrawlTaskStatusResponse(success=True, message="Success", data=status_data)


# --- Seeding Tasks Endpoints ---

class SeedingStatusUpdate(BaseModel):
    task_id: str
    email: str
    status: str

@router.get("/seeding/list")
def list_seeding_tasks(email: str = Query(...)):
    """Danh sách nhiệm vụ seeding của nhân viên."""
    try:
        tasks = seeding_task_service.list_employee_tasks(email)
        return {"success": True, "data": tasks}
    except Exception as e:
        return {"success": False, "message": str(e)}

@router.post("/seeding/update-status")
def update_seeding_status(payload: SeedingStatusUpdate):
    """Cập nhật trạng thái nhiệm vụ seeding thủ công."""
    try:
        task = seeding_task_service.update_task_status_manual(
            task_id=payload.task_id,
            email=payload.email,
            new_status=payload.status
        )
        return {"success": True, "data": task}
    except Exception as e:
        return {"success": False, "message": str(e)}

@router.post("/seeding/report")
def report_seeding_comment(payload: dict):
    """Báo cáo đã comment cho một nhiệm vụ."""
    try:
        task_id = payload.get("task_id")
        email = payload.get("email")
        comment_url = payload.get("comment_url")
        if not task_id or not email:
            return {"success": False, "message": "task_id and email are required"}
        
        task = seeding_task_service.report_employee_comment(task_id, email, comment_url)
        return {"success": True, "data": task}
    except Exception as e:
        return {"success": False, "message": str(e)}

@router.post("/seeding/verify")
def verify_seeding_comment(payload: dict):
    """Yêu cầu hệ thống xác minh comment tự động."""
    try:
        task_id = payload.get("task_id")
        email = payload.get("email")
        if not task_id or not email:
            return {"success": False, "message": "task_id and email are required"}
        
        task = seeding_task_service.verify_employee_comment(task_id, email)
        return {"success": True, "data": task}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/seeding/lock-comments")
def lock_seeding_comments(payload: dict):
    """Đánh dấu bài viết đã khóa comment."""
    try:
        task_id = payload.get("task_id")
        email = payload.get("email")
        if not task_id or not email:
            return {"success": False, "message": "task_id and email are required"}
        
        task = seeding_task_service.mark_comments_locked(task_id, email)
        return {"success": True, "data": task}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/start", response_model=StartCrawlResponse, dependencies=[Depends(verify_api_key)])
def start_crawl_workflow(payload: StartWorkflowRequest, background_tasks: BackgroundTasks) -> StartCrawlResponse:
    """Khởi động luồng cào dữ liệu qua BackgroundTasks thay vì n8n."""
    try:
        id_prefix = _crawl_id_session_prefix(payload.email, None, "")
        
        # Kiểm tra xem đã có file session cho email này chưa
        from app.config import settings
        session_file = settings.session_storage_dir / f"{id_prefix}.json"
        if not session_file.exists():
             return StartCrawlResponse(
                success=False, 
                message=f"Chưa tìm thấy phiên đăng nhập cho {payload.email}. Vui lòng thực hiện Đăng nhập (Login) trước khi cào.", 
                data=None
            )
            
        id_session_crawl = f"{id_prefix}_{random.randint(1_000_000_000, 9_999_999_999_999)}"
        
        # Tạo task tracking 
        group_count = len(payload.group_urls) if payload.group_urls else 0
        crawl_task_service.create_task(id_session_crawl, payload.email, group_count)

        # Chạy ngầm tiến trình cào
        background_tasks.add_task(run_background_crawl, id_session_crawl, payload)

        return StartCrawlResponse(
            success=True,
            message="Đang tiến hành cào....",
            data=StartCrawlData(
                http_status=200,
                response_preview="Chạy nền thành công.",
                response_message="Chạy nền thành công.",
                response_payload={"id_session_crawl": id_session_crawl},
                id_session_crawl=id_session_crawl,
            ),
        )
    except RuntimeError as exc:
        logger.warning("Lỗi khởi động crawl: %s", exc)
        return StartCrawlResponse(success=False, message=str(exc), data=None)
    except httpx.HTTPStatusError as exc:
        preview = _truncate_webhook_preview(exc.response.text or "")
        status_code = exc.response.status_code
        response_message, response_payload = _extract_webhook_message_and_payload(preview)
        logger.warning(
            "n8n start webhook trả về HTTP lỗi status=%s (payload không ghi log)",
            status_code,
        )
        return StartCrawlResponse(
            success=False,
            message=f"n8n start webhook trả về HTTP {status_code}",
            data=StartCrawlData(
                http_status=status_code,
                response_preview=preview,
                response_message=response_message,
                response_payload=response_payload,
            ),
        )
    except httpx.ReadTimeout:
        logger.warning("n8n start webhook read timeout (chưa nhận response trong thời gian chờ)")
        return StartCrawlResponse(
            success=False,
            message=(
                "n8n start webhook bị timeout khi chờ response. "
                "Khả năng cao workflow/proxy chưa trả response kịp (Respond to Webhook đến quá muộn)."
            ),
            data=None,
        )
    except httpx.RequestError as exc:
        logger.warning(
            "Không kết nối được tới n8n start webhook: %s",
            type(exc).__name__,
        )
        return StartCrawlResponse(
            success=False,
            message="Không kết nối được tới n8n start webhook. Kiểm tra URL và mạng.",
            data=None,
        )
    except Exception as exc:
        logger.exception("Gửi start tới webhook n8n thất bại")
        return StartCrawlResponse(success=False, message=str(exc), data=None)


@router.post(
    "/n8n/get-sheet-link",
    response_model=GetSheetLinkResponse,
    dependencies=[Depends(verify_api_key)],
)
def get_sheet_link(payload: GetSheetLinkRequest) -> GetSheetLinkResponse:
    """Trả về link Google Sheet trực tiếp, bỏ qua webhook n8n cũ."""
    sheet_id = settings.google_spreadsheet_id
    if not sheet_id:
        return GetSheetLinkResponse(
            success=False,
            message="Chưa cấu hình GOOGLE_SPREADSHEET_ID trong .env",
            data=None,
        )
        
    sheet_link = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    
    return GetSheetLinkResponse(
        success=True,
        message="Đã lấy link sheet thành công",
        data=GetSheetLinkData(
            sheet_link=sheet_link,
            http_status=200,
            response_preview="Direct link generated",
        ),
    )


@router.post(
    "/groups/get-all",
    response_model=BaseResponse,
    dependencies=[Depends(verify_api_key)],
)
def groups_get_all(
    payload: GetAllGroupsRequest,
    email_crawl: Annotated[str | None, Cookie()] = None,
) -> BaseResponse:
    """Lấy **tất cả nhóm** theo email bằng cách đọc trực tiếp Google Sheet."""
    email = _resolve_crawler_email_for_n8n_groups(
        body_email=payload.email,
        email_crawl=email_crawl,
    )
    from app.services.google_sheet_service import read_group_url_rows
    # _normalize_n8n_groups is defined locally in routes.py
    
    try:
        raw_rows = read_group_url_rows()
        # Filter by email
        want_email = (email or "").strip().lower()
        my_groups = []
        for row in raw_rows:
            # Check all possible email keys in a case-insensitive way
            row_email = ""
            for k, v in row.items():
                if k.lower() in ("email", "email_crawl", "useremail", "user_email"):
                    row_email = str(v or "").strip().lower()
                    if row_email:
                        break
            
            if row_email == want_email:
                my_groups.append(row)
                
        groups = _normalize_n8n_groups(my_groups)
        return BaseResponse(
            success=True,
            message="Lấy danh sách nhóm thành công từ Google Sheet",
            data={
                "http_status": 200,
                "total": len(groups),
                "groups": groups,
            }
        )
    except Exception as e:
        logger.exception("Lỗi khi lấy danh sách nhóm")
        return BaseResponse(success=False, message=str(e), data=None)


@router.post(
    "/groups/add",
    response_model=BaseResponse,
    dependencies=[Depends(verify_api_key)],
)
def groups_add(
    payload: AddGroupRequest,
    email_crawl: Annotated[str | None, Cookie()] = None,
) -> BaseResponse:
    """Thêm nhóm vào Google Sheet trực tiếp."""
    email = _resolve_crawler_email_for_n8n_groups(
        body_email=payload.email,
        email_crawl=email_crawl,
    )
    success = google_sheet_crud.add_group_to_sheet(
        url=payload.url_group,
        name=payload.name_group,
        member=str(payload.member),
        email=email
    )
    if success:
        return BaseResponse(success=True, message="Thêm nhóm thành công", data=None)
    return BaseResponse(success=False, message="Thêm nhóm thất bại (kiểm tra logs)", data=None)


@router.post(
    "/groups/remove",
    response_model=BaseResponse,
    dependencies=[Depends(verify_api_key)],
)
def groups_remove(
    payload: RemoveGroupRequest,
    email_crawl: Annotated[str | None, Cookie()] = None,
) -> BaseResponse:
    """Xóa nhóm khỏi Google Sheet trực tiếp."""
    email = _resolve_crawler_email_for_n8n_groups(
        body_email=payload.email,
        email_crawl=email_crawl,
    )
    success = google_sheet_crud.remove_group_from_sheet(url=payload.url_group, email=email)
    if success:
        return BaseResponse(success=True, message="Xóa nhóm thành công", data=None)
    return BaseResponse(success=False, message="Xóa nhóm thất bại (có thể không tìm thấy)", data=None)


@router.post(
    "/groups/update",
    response_model=BaseResponse,
    dependencies=[Depends(verify_api_key)],
)
def groups_update(
    payload: UpdateGroupRequest,
    email_crawl: Annotated[str | None, Cookie()] = None,
) -> BaseResponse:
    """Cập nhật nhóm trên Google Sheet trực tiếp."""
    email = _resolve_crawler_email_for_n8n_groups(
        body_email=payload.email,
        email_crawl=email_crawl,
    )
    success = google_sheet_crud.update_group_in_sheet(
        old_url=payload.url_group_need_update,
        new_url=payload.new_url_group,
        new_name=payload.new_name_group,
        new_member=str(payload.new_member) if payload.new_member is not None else "",
        email=email
    )
    if success:
        return BaseResponse(success=True, message="Cập nhật nhóm thành công", data=None)
    return BaseResponse(success=False, message="Cập nhật nhóm thất bại", data=None)


@router.post(
    "/groups/add-list-group",
    response_model=BulkGroupImportResponse,
    dependencies=[Depends(verify_api_key)],
)
def add_list_group(
    payload: AddListGroupRequest,
    email_crawl: Annotated[str | None, Cookie()] = None,
) -> BulkGroupImportResponse:
    """Cào hàng loạt URL nhóm, sau đó lưu trực tiếp vào Google Sheet."""

    owner_email = _resolve_bulk_add_group_email(
        body_email=payload.email,
        email_crawl=email_crawl,
    )

    try:
        scraped_items = bulk_scrape_groups(
            group_urls=payload.group_urls,
            session_id=payload.session_id,
            email=owner_email,
            delay_min_sec=payload.delay_min_sec,
            delay_max_sec=payload.delay_max_sec,
        )
    except Exception as exc:
        logger.exception("add-list-group scrape failed")
        return BulkGroupImportResponse(
            success=False,
            message=f"Cào nhóm thất bại: {exc}",
            data=None,
        )

    response_items = [BulkGroupImportScrapedItem(**item) for item in scraped_items]

    # NEW: Save directly to Google Sheet instead of n8n
    success_items = [item for item in scraped_items if item.get("success")]
    if success_items and owner_email:
        google_sheet_crud.bulk_add_groups_to_sheet(success_items, owner_email)
        logger.info(f"Đã lưu {len(success_items)} nhóm thành công vào Google Sheet cho {owner_email}")

    return BulkGroupImportResponse(
        success=True,
        message=f"Đã xử lý {len(scraped_items)} nhóm và lưu vào Google Sheet.",
        data=BulkGroupImportData(
            items=response_items,
            webhook_skipped=True,
            webhook_http_status=None,
            webhook_response_preview=None,
            webhook_response=None,
        ),
    )


@router.post("/filter-data", response_model=FilterDataResponse, dependencies=[Depends(verify_api_key)])
def filter_data(payload: FilterDataRequest) -> FilterDataResponse:
    """Đọc bài từ Google Sheet -> lọc theo điều kiện ngày -> trả mảng phiên cào."""

    try:
        if not gsheet.spreadsheet_configured():
            return FilterDataResponse(success=False, message="Google Sheet chưa được cấu hình.", data=None)

        _, all_rows = gsheet.read_top_posts_as_dicts()
        
        # Resolve dates
        try:
            window_start, window_end = _compute_filter_date_window(payload)
        except ValueError as ve:
            return FilterDataResponse(success=False, message=str(ve), data=None)

        # Filter by email and date
        filtered_rows = gsheet.filter_sheet_top_posts_for_owner(
            all_rows,
            owner_email_token=payload.email,
            date_from=window_start,
            date_to=window_end
        )

        posts_raw = normalize_n8n_posts(filtered_rows)
        crawl_sessions = build_crawl_sessions_from_posts(posts_raw)

        return FilterDataResponse(
            success=True,
            message="Đã lọc và gom theo phiên cào",
            data=crawl_sessions,
        )
    except Exception as exc:
        logger.exception("Filter data endpoint failed")
        return FilterDataResponse(success=False, message=str(exc), data=None)


@router.post("/get-all-posts", response_model=GetAllPostsResponse, dependencies=[Depends(verify_api_key)])
def get_all_posts(payload: GetAllPostsRequest) -> GetAllPostsResponse:
    """Lấy toàn bộ bài từ Google Sheet và gom theo phiên cào."""

    try:
        if not gsheet.spreadsheet_configured():
            return GetAllPostsResponse(success=False, message="Google Sheet chưa được cấu hình.", data=None)

        _, all_rows = gsheet.read_top_posts_as_dicts()
        
        # Filter by email only (all dates)
        my_rows = gsheet.filter_sheet_top_posts_for_owner(
            all_rows,
            owner_email_token=payload.email
        )

        posts_raw = normalize_n8n_posts(my_rows)
        crawl_sessions = build_crawl_sessions_from_posts(posts_raw)

        return GetAllPostsResponse(
            success=True,
            message=f"Đã tải {len(crawl_sessions)} phiên cào từ Google Sheet.",
            data=crawl_sessions,
        )
    except Exception as exc:
        logger.exception("Get all posts endpoint failed")
        return GetAllPostsResponse(success=False, message=str(exc), data=None)


linkedin_app_router = APIRouter(
    prefix="/linkedin-app",
    tags=["linkedin-app"],
    dependencies=[Depends(verify_api_key)],
)


@linkedin_app_router.get("/get-all-posts", response_model=LinkedinSheetTopPostsResponse)
def linkedin_app_sheet_get_all_posts_get(
    email: Annotated[str, Query(..., min_length=1)],
) -> LinkedinSheetTopPostsResponse:
    """Đọc mọi bài của user (ô ``Email_crawl`` trùng ``email``), không lộ dữ liệu account khác."""

    return _linkedin_app_sheet_get_posts_for_owner(email)


@linkedin_app_router.post("/get-all-posts", response_model=LinkedinSheetTopPostsResponse)
def linkedin_app_sheet_get_all_posts_post(
    payload: LinkedinAppGetAllPostsSheetRequest,
) -> LinkedinSheetTopPostsResponse:
    """POST body: ``{ \"email\": \"...\" }``."""

    return _linkedin_app_sheet_get_posts_for_owner(payload.email)


def _linkedin_app_sheet_get_posts_for_owner(owner_email: str) -> LinkedinSheetTopPostsResponse:
    try:
        if not gsheet.spreadsheet_configured():
            return LinkedinSheetTopPostsResponse(
                success=False,
                message="Google Sheet chưa cấu hình hoặc thiếu file GOOGLE_SERVICE_ACCOUNT_JSON.",
                data=None,
            )

        headers, rows = gsheet.read_top_posts_as_dicts()
        filtered = gsheet.filter_sheet_top_posts_for_owner(
            rows,
            owner_email_token=owner_email,
            date_from=None,
            date_to=None,
        )
        return LinkedinSheetTopPostsResponse(
            success=True,
            message="Đã đọc dữ liệu của bạn từ Sheet",
            data=LinkedinSheetTopPostsData(headers=headers, rows=filtered, row_count=len(filtered)),
        )
    except Exception as exc:
        logger.exception("linkedin-app get-all-posts failed")
        return LinkedinSheetTopPostsResponse(
            success=False,
            message=gsheet.safe_http_message(exc),
            data=None,
        )


@linkedin_app_router.post("/filter-post", response_model=LinkedinSheetFilterPostsResponse)
def linkedin_app_sheet_filter_posts(payload: LinkedinAppFilterPostsSheetRequest) -> LinkedinSheetFilterPostsResponse:
    """Lọc theo ``email`` (bắt buộc) và khoảng ``date_from`` / ``date_to`` trên cột ``Ngày``."""

    try:
        if not gsheet.spreadsheet_configured():
            return LinkedinSheetFilterPostsResponse(
                success=False,
                message="Google Sheet chưa cấu hình hoặc thiếu file GOOGLE_SERVICE_ACCOUNT_JSON.",
                data=None,
            )
        headers, rows = gsheet.read_top_posts_as_dicts()
        d_from = date.fromisoformat(payload.date_from) if payload.date_from else None
        d_to = date.fromisoformat(payload.date_to) if payload.date_to else None
        filtered = gsheet.filter_sheet_top_posts_for_owner(
            rows,
            owner_email_token=payload.email,
            date_from=d_from,
            date_to=d_to,
        )
        return LinkedinSheetFilterPostsResponse(
            success=True,
            message="Đã lọc posts từ Sheet",
            data=LinkedinSheetTopPostsData(headers=headers, rows=filtered, row_count=len(filtered)),
        )
    except Exception as exc:
        logger.exception("linkedin-app filter-post failed")
        return LinkedinSheetFilterPostsResponse(
            success=False,
            message=gsheet.safe_http_message(exc),
            data=None,
        )


@linkedin_app_router.get("/get-all-groups", response_model=LinkedinSheetGroupsResponse)
def linkedin_app_sheet_get_all_groups() -> LinkedinSheetGroupsResponse:
    """Đọc tab danh sách URL nhóm (URL_Nhóm, email, Trạng thái,...)."""

    try:
        if not gsheet.spreadsheet_configured():
            return LinkedinSheetGroupsResponse(
                success=False,
                message="Google Sheet chưa cấu hình hoặc thiếu file GOOGLE_SERVICE_ACCOUNT_JSON.",
                data=None,
            )
        rows = gsheet.read_group_url_rows()
        return LinkedinSheetGroupsResponse(
            success=True,
            message="Đã đọc danh sách nhóm",
            data=LinkedinSheetGroupsData(rows=rows, row_count=len(rows)),
        )
    except Exception as exc:
        logger.exception("linkedin-app get-all-groups failed")
        return LinkedinSheetGroupsResponse(
            success=False,
            message=gsheet.safe_http_message(exc),
            data=None,
        )


@linkedin_app_router.post("/crawl-linkedin-app", response_model=LinkedinAppCrawlBatchResponse)
def linkedin_app_crawl_batch(payload: LinkedinAppCrawlBatchRequest) -> LinkedinAppCrawlBatchResponse:
    """Lặp crawl nhiều nhóm LinkedIn và append bài vào tab ``top_posts``; có nghỉ ngẫu nhiên giữa các nhóm."""

    if not payload.session_id and not payload.email:
        return LinkedinAppCrawlBatchResponse(
            success=False,
            message="Cần ``email`` hoặc ``session_id`` để dùng session LinkedIn đã lưu (POST /login).",
            data=None,
        )

    if not gsheet.spreadsheet_configured():
        return LinkedinAppCrawlBatchResponse(
            success=False,
            message="Google Sheet chưa cấu hình hoặc thiếu file GOOGLE_SERVICE_ACCOUNT_JSON.",
            data=None,
        )

    try:
        headers = gsheet.read_top_post_header_row()
    except Exception as exc:
        logger.warning("Không đọc được tiêu đề top_posts: %s", exc)
        return LinkedinAppCrawlBatchResponse(
            success=False,
            message=gsheet.safe_http_message(exc),
            data=None,
        )

    gmin = payload.group_delay_min_sec
    gmax = payload.group_delay_max_sec
    if gmin is None:
        gmin = settings.crawl_batch_group_delay_min_sec
    if gmax is None:
        gmax = settings.crawl_batch_group_delay_max_sec
    gmin_f = float(min(gmin, gmax))
    gmax_f = float(max(gmin, gmax))

    scroll_min_ms = (
        int(payload.scroll_delay_min_sec * 1000) if payload.scroll_delay_min_sec is not None else None
    )
    scroll_max_ms = (
        int(payload.scroll_delay_max_sec * 1000) if payload.scroll_delay_max_sec is not None else None
    )

    results: list[LinkedinAppCrawlGroupResult] = []

    for index, url in enumerate(payload.group_urls):
        if index > 0 and gmax_f > 0:
            delay_sec = random.uniform(gmin_f, gmax_f)
            logger.info("linkedin-app crawl: chờ %.2fs trước khi sang nhóm tiếp theo", delay_sec)
            time.sleep(delay_sec)

        try:
            crawl_result = open_group_and_collect_posts(
                session_id=payload.session_id,
                email=payload.email,
                group_url=url,
                max_items=payload.max_items,
                scroll_times_override=payload.scroll_times,
                scroll_delay_min_ms=scroll_min_ms,
                scroll_delay_max_ms=scroll_max_ms,
            )

            filtered_posts, target_day_resolved = enrich_and_filter_posts(
                posts=list(crawl_result["posts"]),
                target_date=payload.target_date,
                crawl_time=crawl_result["crawl_time"],
            )

            crawl_day_label = crawl_result["crawl_time"].strftime("%Y-%m-%d")

            if filtered_posts:
                top_one = pick_top_post(filtered_posts)
                posts_to_write = [] if top_one is None else [top_one]
                detail_msg = (
                    f"Đã ghi top 1 bài của ngày {target_day_resolved.isoformat()}"
                    if posts_to_write
                    else "Không chọn được bài top trong tập lọc theo ngày"
                )
            else:
                posts_to_write = select_most_recent_posts(
                    list(crawl_result["posts"]),
                    limit=payload.fallback_recent_count,
                )
                detail_msg = (
                    f"Không có bài nào thuộc ngày {target_day_resolved.isoformat()}, "
                    f"ghi {len(posts_to_write)} bài gần nhất trong feed đã scrape"
                )

            batch_rows = [
                gsheet.build_top_post_row_values(
                    headers,
                    email_crawl=payload.email_crawl,
                    crawl_date=crawl_day_label,
                    group_name=str(crawl_result.get("group_name") or ""),
                    group_url=str(crawl_result.get("group_url") or url),
                    total_posts_in_run=int(crawl_result.get("total_posts_scraped") or 0),
                    member_count=int(crawl_result.get("member_count") or 0),
                    post=post_item,
                )
                for post_item in posts_to_write
            ]

            if batch_rows:
                gsheet.append_top_post_rows(batch_rows)

            if payload.mark_group_done:
                try:
                    updated = gsheet.update_group_status_by_url(url, "done")
                    if not updated:
                        detail_msg = f"{detail_msg} (chưa ghi Trạng thái=done — kiểm tra tab URL nhóm / GOOGLE_SHEET_GROUP_URLS_TAB)."
                except Exception as status_exc:
                    logger.warning("Cập nhật Trạng thái sheet thất bại: %s", status_exc)
                    detail_msg = f"{detail_msg} (lỗi cập nhật Trạng thái: {gsheet.safe_http_message(status_exc)})"

            results.append(
                LinkedinAppCrawlGroupResult(
                    group_url=url,
                    success=True,
                    message=detail_msg,
                    posts_appended=len(batch_rows),
                ),
            )
        except Exception as exc:
            logger.exception("linkedin-app crawl thất bại cho %s", url)
            results.append(
                LinkedinAppCrawlGroupResult(
                    group_url=url,
                    success=False,
                    message=gsheet.safe_http_message(exc),
                    posts_appended=0,
                ),
            )

    all_ok = all(item.success for item in results)
    return LinkedinAppCrawlBatchResponse(
        success=all_ok,
        message="Hoàn thành batch crawl" if all_ok else "Một hoặc nhiều nhóm crawl lỗi",
        data=LinkedinAppCrawlBatchData(
            results=results,
            spreadsheet_id=settings.google_spreadsheet_id,
        ),
    )


class SeedingTaskReportRequest(BaseModel):
    email: str
    task_id: str
    comment_url: Optional[str] = ""


class LinkStatusRequest(BaseModel):
    url: str


@router.get("/seeding-tasks", dependencies=[Depends(verify_api_key)])
def get_employee_seeding_tasks(email: str) -> dict[str, Any]:
    """Danh sách task seeding của nhân viên từ tab Seeding_tasks."""
    try:
        tasks = seeding_task_service.list_employee_tasks(email)
        return {"success": True, "message": "Seeding tasks fetched", "data": tasks}
    except Exception as exc:
        logger.exception("Failed to fetch seeding tasks")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/seeding-tasks/report", dependencies=[Depends(verify_api_key)])
def report_employee_seeding_task(payload: SeedingTaskReportRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Nhân viên báo đã comment; hệ thống chuyển sang verifying và verify nền."""
    try:
        task = seeding_task_service.report_employee_comment(
            task_id=payload.task_id,
            email=payload.email,
            comment_url=payload.comment_url,
        )
        background_tasks.add_task(
            seeding_task_service.verify_employee_comment,
            payload.task_id,
            payload.email,
        )
        live_feed_service.add_event("seeding_report", payload.email, f"Đã báo cáo comment task {payload.task_id}")
        return {"success": True, "message": "Đã nhận báo cáo. Hệ thống đang xác minh comment.", "data": task}
    except Exception as exc:
        logger.exception("Failed to report seeding task")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/seeding-tasks/verify", dependencies=[Depends(verify_api_key)])
def verify_employee_seeding_task(payload: SeedingTaskReportRequest) -> dict[str, Any]:
    """Retry verify thủ công cho một task."""
    try:
        task = seeding_task_service.verify_employee_comment(payload.task_id, payload.email)
        return {"success": True, "message": "Verification finished", "data": task}
    except Exception as exc:
        logger.exception("Failed to verify seeding task")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/links/check-status", dependencies=[Depends(verify_api_key)])
async def check_link_status(payload: LinkStatusRequest) -> dict[str, Any]:
    """Check whether an arbitrary link is live, dead, blocked, or error."""
    try:
        url = (payload.url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL is required")
        status_value = await group_status_service.check_single_group_status(url, update_cache=False)
        return {"success": True, "message": "Link status fetched", "data": {"url": url, "status": status_value}}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to check link status")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@router.get("/team/live-feed", dependencies=[Depends(verify_api_key)])
async def get_live_feed() -> dict[str, Any]:
    """Lấy danh sách hoạt động đội ngũ thời gian thực."""
    try:
        feed_data = live_feed_service.get_feed()
        # Always return a list, never None for consistency
        if feed_data is None:
            feed_data = []
        return {
            "success": True,
            "message": f"Đã lấy {len(feed_data)} sự kiện gần đây",
            "data": feed_data
        }
    except Exception as exc:
        logger.exception("Failed to get live feed")
        return {
            "success": False,
            "message": f"Lỗi lấy live feed: {str(exc)[:100]}",
            "data": []
        }

@router.get("/crawl-status/{session_id}", dependencies=[Depends(verify_api_key)])
def get_crawl_task_status(session_id: str) -> dict[str, Any]:
    """Poll for the status of an async crawl session."""
    task = crawl_task_service.get_task_status(session_id)
    if not task:
        return {"success": False, "message": "Task not found", "data": None}
    return {"success": True, "message": "Task status fetched", "data": task}

class WebhookUpdateTaskRequest(BaseModel):
    id_session_crawl: str
    status: str
    message: Optional[str] = ""

@router.post("/crawl-webhook/update", dependencies=[Depends(verify_api_key)])
def update_crawl_task_status(payload: WebhookUpdateTaskRequest) -> dict[str, Any]:
    """N8n calls this webhook to update the task status (e.g. status='completed')."""
    crawl_task_service.update_task_status(payload.id_session_crawl, payload.status, payload.message or "")
    return {"success": True, "message": "Task updated"}

# --- KPI & Task Management Endpoints ---

class KpiReportRequest(BaseModel):
    email: str
    post_url: str

class PersonalTaskRequest(BaseModel):
    email: str
    title: str
    priority: str = "medium"
    deadline: str = "Hôm nay"

class PersonalTaskUpdate(BaseModel):
    email: str
    task_id: str
    updates: Dict[str, Any]

@router.post("/kpi/report", dependencies=[Depends(verify_api_key)])
async def report_kpi_task(payload: KpiReportRequest):
    try:
        from app.services import kpi_service, comment_verifier
        task = kpi_service.report_task_seeded(payload.email, payload.post_url)
        # Trigger verification in background
        comment_verifier.queue_verification(payload.email, payload.post_url)
        live_feed_service.add_event("report", payload.email, f"Đã báo cáo bài viết: {payload.post_url}")
        return {"success": True, "message": "Đã báo cáo bài viết. Hệ thống đang xác minh comment ngầm.", "data": task}
    except Exception as e:
        logger.exception("KPI report error")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@router.get("/kpi/status", dependencies=[Depends(verify_api_key)])
async def get_kpi_status_route(email: str):
    try:
        from app.services import kpi_service
        data = kpi_service.get_kpi_status(email)
        return {"success": True, "message": "KPI status fetched", "data": data}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@router.post("/kpi/tasks/add", dependencies=[Depends(verify_api_key)])
async def add_personal_task_route(payload: PersonalTaskRequest):
    try:
        from app.services import kpi_service
        task = kpi_service.add_personal_task(payload.email, payload.title, payload.priority, payload.deadline)
        live_feed_service.add_event("task_add", payload.email, f"Đã tạo nhiệm vụ: {payload.title}")
        return {"success": True, "message": "Task added", "data": task}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@router.patch("/kpi/tasks/update", dependencies=[Depends(verify_api_key)])
async def update_personal_task_route(payload: PersonalTaskUpdate):
    try:
        from app.services import kpi_service
        success = kpi_service.update_personal_task(payload.email, payload.task_id, payload.updates)
        return {"success": success, "message": "Task updated" if success else "Task not found"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@router.delete("/kpi/tasks/delete", dependencies=[Depends(verify_api_key)])
async def delete_personal_task_route(email: str, task_id: str):
    try:
        from app.services import kpi_service
        success = kpi_service.delete_personal_task(email, task_id)
        return {"success": success, "message": "Task deleted" if success else "Task not found"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


# --- Employee Dashboard Stats Endpoint ---

@router.get("/employee/dashboard-stats", dependencies=[Depends(verify_api_key)])
async def get_employee_dashboard_stats(email: str) -> dict[str, Any]:
    """Get personalized dashboard statistics for an individual employee.
    
    Includes: total posts, groups, success rate, today's activity, and recent posts.
    """
    try:
        if not gsheet.spreadsheet_configured():
            return {
                "success": False,
                "message": "Google Sheet chưa được cấu hình.",
                "data": None
            }
        
        # Get all posts for this employee
        _, all_rows = gsheet.read_top_posts_as_dicts()
        employee_rows = gsheet.filter_sheet_top_posts_for_owner(
            all_rows,
            owner_email_token=email,
            date_from=None,
            date_to=None
        )
        
        # Ensure employee_rows is always a list (type safety)
        if employee_rows is None:
            employee_rows = []
        if not isinstance(employee_rows, list):
            employee_rows = []
        
        # Get all groups for this employee
        all_group_rows = gsheet.read_group_url_rows()
        if all_group_rows is None:
            all_group_rows = []
        
        employee_groups = [
            row for row in all_group_rows 
            if str((row.get("Email") or row.get("email") or "")).strip().lower() == email.lower()
        ]
        
        # Calculate stats
        total_posts = len(employee_rows)
        active_groups = len([
            g for g in employee_groups 
            if str((g.get("Trạng thái") or g.get("Status") or "")).strip().lower() in ("", "todo", "pending", "active")
        ])
        completed_groups = len([
            g for g in employee_groups 
            if str((g.get("Trạng thái") or g.get("Status") or "")).strip().lower() == "done"
        ])
        total_groups = len(employee_groups)
        
        # Calculate success rate (posts per group or 0 if no groups)
        success_rate = 100.0
        if total_groups > 0:
            success_rate = min(100.0, (total_posts / total_groups) * 100) if total_posts > 0 else 0.0
        
        # Get today's activity (use naive date comparison consistently)
        from datetime import datetime, timezone, date
        today = date.today()
        today_posts = []
        for row in employee_rows:
            try:
                post_date_str = str(row.get("Ngày") or "").split()[0]
                if post_date_str:
                    post_date = datetime.fromisoformat(post_date_str.strip()).date()
                    if post_date == today:
                        today_posts.append(row)
            except (ValueError, IndexError, TypeError, AttributeError):
                pass
        
        # Get recent posts (last 10)
        recent_posts = employee_rows[:10]
        
        # Build response data focused on employee's individual performance
        stats_data = {
            "email": email,
            "total_posts": total_posts,
            "total_groups": total_groups,
            "active_groups": active_groups,
            "completed_groups": completed_groups,
            "success_rate_percent": round(success_rate, 1),
            "today_posts_count": len(today_posts),
            "recent_posts_count": len(recent_posts),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        
        return {
            "success": True,
            "message": f"Đã lấy thống kê cho {email}",
            "data": stats_data
        }
    except Exception as exc:
        logger.exception("Failed to get employee dashboard stats")
        return {
            "success": False,
            "message": f"Lỗi lấy thống kê: {str(exc)[:100]}",
            "data": None
        }


# --- Group Status Endpoints ---

@router.post("/groups/check-status", dependencies=[Depends(verify_api_key)])
async def check_group_status_manual(payload: Dict[str, str]):
    try:
        from app.services import group_status_service
        url = payload.get("url")
        if not url:
            return JSONResponse(status_code=400, content={"success": False, "message": "URL is required", "data": None})
        status = await group_status_service.check_single_group_status(url, update_cache=True)
        return {"success": True, "message": f"Group status checked", "data": status}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e), "data": None})

@router.get("/groups/all-statuses", dependencies=[Depends(verify_api_key)])
async def get_all_group_statuses_route():
    try:
        from app.services import group_status_service
        statuses = group_status_service.get_all_cached_statuses()
        return {"success": True, "message": f"Trạng thái {len(statuses) if statuses else 0} nhóm", "data": statuses or {}}
    except Exception as e:
        logger.exception("Failed to get group statuses")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e), "data": {}})
