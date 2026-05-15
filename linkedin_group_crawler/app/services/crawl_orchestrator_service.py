"""Tiered crawler orchestration for LinkedIn groups."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from app.config import settings
from app.services.apify_crawler_service import run_apify_crawler_for_group
from app.services.crawler_service import open_group_and_collect_posts
from app.utils.logger import get_logger

logger = get_logger(__name__)

CrawlerMode = Literal["auto", "playwright", "apify"]


@dataclass
class CrawlAttempt:
    tier: str
    success: bool
    message: str
    status: str = "failed"
    error_type: str | None = None
    reached_group: bool = False
    auth_required: bool = False
    raw_posts_count: int = 0


@dataclass
class TieredCrawlResult:
    success: bool
    group_item: Optional[dict[str, Any]] = None
    source: str = ""
    attempts: list[CrawlAttempt] = field(default_factory=list)

    @property
    def error_summary(self) -> str:
        return " | ".join(
            f"{a.tier}[{a.error_type or a.status}]: {a.message}"
            for a in self.attempts
            if not a.success
        )


async def _to_thread(func, *args, **kwargs):
    import functools

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


def _classify_local_error(message: str) -> tuple[str, bool]:
    text = (message or "").lower()
    if any(token in text for token in ("login", "checkpoint", "authwall", "session is invalid", "missing auth cookie")):
        return "AUTH_REQUIRED", True
    if "not stay on the requested group page" in text or "redirect" in text:
        return "REDIRECTED", False
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT", False
    return "CRAWL_ERROR", False


async def crawl_group_with_tiers(
    *,
    group_url: str,
    mode: CrawlerMode = "auto",
    session_id: Optional[str] = None,
    email: Optional[str] = None,
    max_items: Optional[int] = None,
    target_date: Optional[str] = None,
    scroll_times: Optional[int] = None,
    scroll_delay_min_ms: Optional[int] = None,
    scroll_delay_max_ms: Optional[int] = None,
) -> TieredCrawlResult:
    """Run the configured crawl tiers and return the first successful result."""

    attempts: list[CrawlAttempt] = []

    should_try_playwright = mode in ("auto", "playwright")
    should_try_own_apify = mode in ("auto", "apify") and settings.apify_own_actor_enabled
    should_try_third_party = (
        mode in ("auto", "apify")
        and settings.apify_3rd_party_fallback_enabled
    )

    if should_try_playwright:
        try:
            item = await _to_thread(
                open_group_and_collect_posts,
                session_id=session_id,
                email=email,
                group_url=group_url,
                max_items=max_items,
                scroll_times_override=scroll_times,
                scroll_delay_min_ms=scroll_delay_min_ms,
                scroll_delay_max_ms=scroll_delay_max_ms,
            )
            raw_posts_count = len(item.get("posts") or [])
            attempts.append(
                CrawlAttempt(
                    "tier1_playwright_local",
                    True,
                    "ok",
                    status="success",
                    reached_group=True,
                    raw_posts_count=raw_posts_count,
                )
            )
            item["source"] = "playwright_local"
            item["status"] = "success"
            item["error_type"] = None
            item["reached_group"] = True
            item["auth_required"] = False
            item["raw_posts_count"] = raw_posts_count
            return TieredCrawlResult(True, item, "playwright_local", attempts)
        except Exception as exc:
            message = str(exc)
            error_type, auth_required = _classify_local_error(message)
            attempts.append(
                CrawlAttempt(
                    "tier1_playwright_local",
                    False,
                    message,
                    status="failed",
                    error_type=error_type,
                    reached_group=False,
                    auth_required=auth_required,
                )
            )
            logger.warning("Tier 1 Playwright failed for %s: %s", group_url, message)
            if mode == "playwright":
                return TieredCrawlResult(False, None, "", attempts)

    if should_try_own_apify:
        own_result = await run_apify_crawler_for_group(
            group_url,
            kind="own",
            email=email,
            session_id=session_id,
            max_items=max_items,
            target_date=target_date,
            scroll_times=scroll_times,
        )
        if own_result.get("success"):
            attempts.append(
                CrawlAttempt(
                    "tier2_apify_own_actor",
                    True,
                    "ok",
                    status=str(own_result.get("status") or "success"),
                    error_type=own_result.get("error_type"),
                    reached_group=bool(own_result.get("reached_group", True)),
                    auth_required=bool(own_result.get("auth_required", False)),
                    raw_posts_count=len(own_result.get("posts") or []),
                )
            )
            return TieredCrawlResult(True, own_result, "apify_own_actor", attempts)
        message = str(own_result.get("error") or "Apify own actor failed")
        attempts.append(
            CrawlAttempt(
                "tier2_apify_own_actor",
                False,
                message,
                status=str(own_result.get("status") or "failed"),
                error_type=own_result.get("error_type"),
                reached_group=bool(own_result.get("reached_group", False)),
                auth_required=bool(own_result.get("auth_required", False)),
            )
        )
        logger.warning("Tier 2 Apify own Actor failed for %s: %s", group_url, message)

    if should_try_third_party:
        third_result = await run_apify_crawler_for_group(
            group_url,
            kind="third_party",
            max_items=max_items,
            target_date=target_date,
            scroll_times=scroll_times,
        )
        if third_result.get("success"):
            attempts.append(
                CrawlAttempt(
                    "tier3_apify_3rd_party",
                    True,
                    "ok",
                    status=str(third_result.get("status") or "success"),
                    error_type=third_result.get("error_type"),
                    reached_group=bool(third_result.get("reached_group", True)),
                    auth_required=bool(third_result.get("auth_required", False)),
                    raw_posts_count=len(third_result.get("posts") or []),
                )
            )
            return TieredCrawlResult(True, third_result, "apify_3rd_party", attempts)
        message = str(third_result.get("error") or "Apify third-party actor failed")
        attempts.append(
            CrawlAttempt(
                "tier3_apify_3rd_party",
                False,
                message,
                status=str(third_result.get("status") or "failed"),
                error_type=third_result.get("error_type"),
                reached_group=bool(third_result.get("reached_group", False)),
                auth_required=bool(third_result.get("auth_required", False)),
            )
        )
        logger.warning("Tier 3 Apify third-party Actor failed for %s: %s", group_url, message)

    if mode == "apify" and not should_try_own_apify and not should_try_third_party:
        attempts.append(
            CrawlAttempt(
                "tier2_apify_own_actor",
                False,
                "APIFY_OWN_ACTOR_ENABLED=false and third-party fallback disabled",
                status="failed",
                error_type="CONFIG_ERROR",
            )
        )

    return TieredCrawlResult(False, None, "", attempts)
