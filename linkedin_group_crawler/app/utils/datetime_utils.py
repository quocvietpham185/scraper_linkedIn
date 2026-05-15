"""Date and time utility helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re


def normalize_relative_time(raw: str, crawl_time: datetime) -> datetime | None:
    """Convert LinkedIn relative time like 5m, 2h, 1d into an absolute datetime."""

    if not raw:
        return None

    text = " ".join(raw.strip().lower().replace("·", "•").split())
    if any(token in text for token in ("now", "just now", "vừa xong")):
        return crawl_time

    pattern = re.compile(
        r"(\d+)\s*(mo|mos|month|months|yr|yrs|year|years|w|wk|wks|week|weeks|d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|phút|gio|giờ|ngày|ngay|tuần|tuan|tháng|thang|năm|nam)\b",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return None

    # LinkedIn subtitle text can include profile copy before the real age, e.g.
    # "I build systems in 24 Hours • 7m •". Prefer the last compact token.
    match = matches[-1]
    value = int(match.group(1))
    unit = match.group(2)

    if unit in {"m", "min", "mins", "minute", "minutes", "phút"}:
        return crawl_time - timedelta(minutes=value)
    if unit in {"h", "hr", "hrs", "hour", "hours", "gio", "giờ"}:
        return crawl_time - timedelta(hours=value)
    if unit in {"d", "day", "days", "ngày", "ngay"}:
        return crawl_time - timedelta(days=value)
    if unit in {"w", "wk", "wks", "week", "weeks", "tuần", "tuan"}:
        return crawl_time - timedelta(weeks=value)
    if unit in {"mo", "mos", "month", "months", "tháng", "thang"}:
        return crawl_time - timedelta(days=value * 30)
    if unit in {"yr", "yrs", "year", "years", "năm", "nam"}:
        return crawl_time - timedelta(days=value * 365)
    return None


def parse_target_date(target_date: str | None, crawl_time: datetime) -> date:
    """Parse target date or fallback to crawl date."""

    if not target_date:
        return crawl_time.date()
    return datetime.strptime(target_date, "%Y-%m-%d").date()


def is_same_day(dt: datetime, target_date: date) -> bool:
    """Check whether datetime belongs to the target date."""

    return dt.date() == target_date
