from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse

from app.config import settings
from app.services import google_sheet_service as gsheet
from app.services.profile_comments_service import LinkedinLoginRequiredError, crawl_profile_comments
from app.utils.logger import get_logger

logger = get_logger(__name__)

TASK_TAB_CANDIDATES = ("Seeding_tasks", "seeding_tasks", "Seeding Tasks")
TASK_STATUSES = ("assigned", "reported", "verifying", "verified", "failed", "expired", "cancelled")
TASK_STATUS_SET = set(TASK_STATUSES)

_GROUP_POST_RE = re.compile(r"urn:li:groupPost:([^/?&]+)", re.IGNORECASE)
_ACTIVITY_RE = re.compile(r"urn:li:activity:(\d+)", re.IGNORECASE)
_HYPERLINK_RE = re.compile(r'^=\s*HYPERLINK\(\s*"([^"]+)"', re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_header(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


def _col_letter(index_zero_based: int) -> str:
    index = index_zero_based + 1
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _resolve_task_tab() -> str:
    sid = settings.google_spreadsheet_id
    titles = gsheet.get_spreadsheet_sheet_titles(sid)
    for candidate in TASK_TAB_CANDIDATES:
        for title in titles:
            if _norm_header(title) == _norm_header(candidate):
                return title
    raise ValueError("Khong tim thay tab Seeding_tasks trong Google Sheet.")


def _read_task_sheet() -> tuple[str, list[str], list[dict[str, Any]]]:
    sid = settings.google_spreadsheet_id
    tab = _resolve_task_tab()
    raw = gsheet._read_values(spreadsheet_id=sid, range_a1=gsheet._sheet_a1(sid, tab, "A:ZZ"))
    if not raw:
        return tab, [], []
    headers = [str(c or "").strip() for c in raw[0]]
    rows: list[dict[str, Any]] = []
    for row_number, line in enumerate(raw[1:], start=2):
        padded = list(line) + [""] * (len(headers) - len(line))
        item = {headers[i]: padded[i] if i < len(padded) else "" for i in range(len(headers))}
        item["_row_number"] = row_number
        rows.append(item)
    return tab, headers, rows


def _field(row: dict[str, Any], name: str) -> str:
    want = _norm_header(name)
    for key, value in row.items():
        if key.startswith("_"):
            continue
        if _norm_header(key) == want:
            return str(value or "").strip()
    return ""


def _headers_index(headers: list[str]) -> dict[str, int]:
    return {_norm_header(h): i for i, h in enumerate(headers)}


def _sanitize_status(status: str) -> str:
    value = (status or "").strip().lower()
    return value if value in TASK_STATUS_SET else "assigned"


def _status_from_row(row: dict[str, Any]) -> str:
    raw_status = _field(row, "status").strip()
    if raw_status:
        return _sanitize_status(raw_status)
    for status in reversed(TASK_STATUSES):
        raw = _field(row, status).strip().lower()
        if raw in {"1", "true", "yes", "x", "checked", status}:
            return status
    return "assigned"


def _sheet_url_value(value: str) -> str:
    text = str(value or "").strip()
    match = _HYPERLINK_RE.match(text)
    if match:
        return match.group(1).replace('""', '"').strip()
    return text


def _normalize_post_id(value: str) -> str:
    text = str(value or "").strip()
    if "-" in text:
        tail = text.rsplit("-", 1)[1].strip()
        if tail:
            return tail
    digits = re.findall(r"\d{8,}", text)
    return digits[-1] if digits else text


def _normalize_public_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.netloc and "linkedin.com" in parsed.netloc.lower():
        parts = [p for p in parsed.path.split("/") if p]
        if "in" in parts:
            idx = parts.index("in")
            if idx + 1 < len(parts):
                return parts[idx + 1].strip()
    return text.strip("/")


def _extract_post_id_from_url(post_url: str) -> str:
    text = unquote(_sheet_url_value(str(post_url or "")))
    gm = _GROUP_POST_RE.search(text)
    if gm:
        return _normalize_post_id(gm.group(1).strip())
    am = _ACTIVITY_RE.search(text)
    if am:
        return am.group(1)
    parsed = urlparse(text)
    parts = [p for p in parsed.path.split("/") if p]
    for part in reversed(parts):
        digits = re.findall(r"\d{8,}", part)
        if digits:
            return digits[-1]
    return ""


def _comment_url_matches_task(comment_url: str, post_id: str) -> bool:
    if not comment_url or not post_id:
        return False
    return _extract_post_id_from_url(comment_url) == _normalize_post_id(post_id)


def _public_task(row: dict[str, Any]) -> dict[str, Any]:
    post_url = _sheet_url_value(_field(row, "post_url"))
    post_id = _normalize_post_id(_field(row, "post_id")) or _extract_post_id_from_url(post_url)
    return {
        "task_id": _field(row, "task_id"),
        "post_id": post_id,
        "post_url": post_url,
        "group_id": _field(row, "group_id"),
        "group_name": _field(row, "group_name"),
        "post_content": _field(row, "post_content"),
        "assignee_email": _field(row, "assignee_email"),
        "linkedin_public_id": _normalize_public_id(_field(row, "linkedin_public_id")),
        "assigned_at": _field(row, "assigned_at"),
        "reported_at": _field(row, "reported_at"),
        "verified_at": _field(row, "verified_at"),
        "status": _status_from_row(row),
        "comment_id": _field(row, "comment_id"),
        "comment_text": _field(row, "comment_text"),
        "comment_url": _sheet_url_value(_field(row, "comment_url")),
        "verify_method": _field(row, "verify_method"),
        "verify_error": _field(row, "verify_error"),
        "comments_locked": _field(row, "comments_locked"),
    }


def list_employee_tasks(email: str) -> list[dict[str, Any]]:
    owner = str(email or "").strip().lower()
    if not owner:
        return []
    _, _, rows = _read_task_sheet()
    tasks = [_public_task(row) for row in rows if _field(row, "assignee_email").strip().lower() == owner]
    return sorted(tasks, key=lambda t: t.get("assigned_at") or "", reverse=True)


def _find_task_row(task_id: str, email: str | None = None) -> tuple[str, list[str], dict[str, Any]]:
    tab, headers, rows = _read_task_sheet()
    wanted_id = str(task_id or "").strip()
    wanted_email = str(email or "").strip().lower()
    for row in rows:
        if _field(row, "task_id") != wanted_id:
            continue
        if wanted_email and _field(row, "assignee_email").strip().lower() != wanted_email:
            continue
        return tab, headers, row
    raise ValueError("Khong tim thay task phu hop trong Seeding_tasks.")


def _update_task_cells(tab: str, headers: list[str], row_number: int, updates: dict[str, Any]) -> None:
    index = _headers_index(headers)
    data = []
    for field, value in updates.items():
        col_index = index.get(_norm_header(field))
        if col_index is None:
            logger.warning("Seeding_tasks thieu cot %s, bo qua cap nhat", field)
            continue
        cell = f"{_col_letter(col_index)}{row_number}"
        data.append({"range": gsheet._sheet_a1(settings.google_spreadsheet_id, tab, cell), "values": [[value]]})
    if not data:
        return
    service = gsheet.get_sheets_service()
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=settings.google_spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


def _with_status_columns(updates: dict[str, Any], status: str) -> dict[str, Any]:
    out = dict(updates)
    if status in TASK_STATUS_SET:
        out["status"] = status
        for item in TASK_STATUSES:
            out[item] = "TRUE" if item == status else "FALSE"
    return out


def report_employee_comment(task_id: str, email: str, comment_url: str | None = None) -> dict[str, Any]:
    tab, headers, row = _find_task_row(task_id, email)
    status = _status_from_row(row)
    if status in {"verified", "cancelled", "expired"}:
        return _public_task(row)
    updates: dict[str, Any] = {
        "reported_at": _field(row, "reported_at") or _now_iso(),
        "verify_method": "profile_comments",
        "verify_error": "",
    }
    cleaned_comment_url = str(comment_url or "").strip()
    if cleaned_comment_url:
        updates["comment_url"] = cleaned_comment_url
    _update_task_cells(tab, headers, int(row["_row_number"]), _with_status_columns(updates, "verifying"))
    _, _, refreshed = _find_task_row(task_id, email)
    return _public_task(refreshed)


def verify_employee_comment(task_id: str, email: str) -> dict[str, Any]:
    tab, headers, row = _find_task_row(task_id, email)
    task = _public_task(row)
    if task["status"] in {"verified", "cancelled", "expired"}:
        return task

    public_id = task["linkedin_public_id"]
    post_id = task["post_id"]
    if not post_id:
        updates = _with_status_columns({"verify_error": "Thieu post_id va khong parse duoc tu post_url."}, "failed")
        _update_task_cells(tab, headers, int(row["_row_number"]), updates)
        return {**task, **updates}
    if _comment_url_matches_task(task.get("comment_url") or "", post_id):
        updates = _with_status_columns(
            {
                "verified_at": _now_iso(),
                "comment_id": "",
                "comment_text": "",
                "verify_method": "comment_url_post_id_fallback",
                "verify_error": "",
            },
            "verified",
        )
        _update_task_cells(tab, headers, int(row["_row_number"]), updates)
        _, _, refreshed = _find_task_row(task_id, email)
        return _public_task(refreshed)
    if not public_id:
        updates = _with_status_columns(
            {
                "verify_error": (
                    "Thieu linkedin_public_id cua nhan vien. "
                    "Neu da comment, hay dan link comment dung bai roi bam Verify lai."
                ),
            },
            "failed",
        )
        _update_task_cells(tab, headers, int(row["_row_number"]), updates)
        return {**task, **updates}

    try:
        result = crawl_profile_comments(
            public_id=public_id,
            max_items=80,
            target_post_id=post_id,
            session_id=None,
            email=email,
        )
    except LinkedinLoginRequiredError:
        updates = _with_status_columns(
            {
                "verify_error": (
                    "Session LinkedIn het han hoac can xac minh lai. "
                    "Vui long dang nhap lai tai khoan LinkedIn roi bam Verify lai."
                ),
                "verify_method": "profile_comments",
            },
            "failed",
        )
        _update_task_cells(tab, headers, int(row["_row_number"]), updates)
        return {**task, **updates}
    except Exception as exc:
        logger.exception("Verify seeding task failed")
        updates = _with_status_columns(
            {"verify_error": str(exc)[:500], "verify_method": "profile_comments"},
            "failed",
        )
        _update_task_cells(tab, headers, int(row["_row_number"]), updates)
        return {**task, **updates}

    comments = result.get("comments") or []
    if result.get("has_commented_target_post") and comments:
        comment = comments[0]
        updates = _with_status_columns(
            {
                "verified_at": _now_iso(),
                "comment_id": str(comment.get("comment_id") or ""),
                "comment_text": str(comment.get("comment_text") or "")[:3000],
                "comment_url": str(comment.get("activity_url") or task.get("comment_url") or ""),
                "verify_method": "profile_comments",
                "verify_error": "",
            },
            "verified",
        )
    else:
        updates = _with_status_columns(
            {
                "verify_method": "profile_comments",
                "verify_error": (
                    f"Chua tim thay comment cua {public_id} tren post_id {post_id}. "
                    "Neu da comment, hay dan link comment dung bai roi bam Verify lai."
                ),
            },
            "failed",
        )

    _update_task_cells(tab, headers, int(row["_row_number"]), updates)
    _, _, refreshed = _find_task_row(task_id, email)
    return _public_task(refreshed)
def add_seeding_tasks_bulk(
    email: str,
    posts: list[dict[str, Any]],
    group_name: str,
    group_url: str = "",
) -> None:
    """Auto-add crawled posts as tasks for an employee."""
    if not posts:
        return

    sid = settings.google_spreadsheet_id
    tab = _resolve_task_tab()
    headers = gsheet.read_top_post_header_row() # We'll try to read headers from the tab directly if possible, or use gsheet's common logic
    
    # Actually, seeding_task_service has its own _read_task_sheet
    try:
        tab, headers, _ = _read_task_sheet()
    except Exception:
        # Fallback headers if tab is empty or not found
        headers = [
            "task_id", "post_id", "post_url", "group_id", "group_name", "post_content", 
            "assignee_email", "linkedin_public_id", "assigned_at", "reported_at", 
            "verified_at", "status", "comment_id", "comment_text", "comment_url", 
            "verify_method", "verify_error", "assigned", "reported", "verifying", 
            "verified", "failed", "expired", "cancelled"
        ]

    h_idx = _headers_index(headers)
    rows_to_append = []
    now = _now_iso()
    
    # Extract group_id from URL if possible
    group_id = ""
    if group_url:
        gm = _GROUP_POST_RE.search(group_url)
        if gm:
            group_id = gm.group(1)
        else:
            # Fallback parse URL path
            parts = [p for p in urlparse(group_url).path.split("/") if p]
            if parts:
                group_id = parts[-1]

    for post in posts:
        post_url = post.get("post_url", "")
        if not post_url:
            continue
            
        post_id = _extract_post_id_from_url(post_url)
        task_id = f"TASK_{int(datetime.now().timestamp())}_{post_id[:8]}"
        
        row = [""] * len(headers)
        
        def set_val(name, val):
            idx = h_idx.get(_norm_header(name))
            if idx is not None:
                row[idx] = str(val)

        set_val("task_id", task_id)
        set_val("post_id", post_id)
        set_val("post_url", post_url)
        set_val("group_id", group_id)
        set_val("group_name", group_name)
        set_val("post_content", post.get("content", "")[:5000])
        set_val("assignee_email", email.lower())
        set_val("assigned_at", now)
        set_val("status", "assigned")
        
        # Checkbox columns
        for item in TASK_STATUSES:
            set_val(item, "TRUE" if item == "assigned" else "FALSE")
        
        # New column for locked comments
        set_val("comments_locked", "FALSE")
        
        rows_to_append.append(row)

    if rows_to_append:
        service = gsheet.get_sheets_service()
        service.spreadsheets().values().append(
            spreadsheetId=sid,
            range=gsheet._sheet_a1(sid, tab, "A:A"),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows_to_append},
        ).execute()
        logger.info(f"Added {len(rows_to_append)} seeding tasks to {tab} for {email}")


def update_task_status_manual(task_id: str, email: str, new_status: str) -> dict[str, Any]:
    """Manually update task status (Completed, Failed, Waiting, etc.)"""
    tab, headers, row = _find_task_row(task_id, email)
    
    # Map friendly names if needed
    status_map = {
        "hoàn thành": "verified",
        "chưa hoàn thành": "failed",
        "đang đợi check": "verifying",
        "đã xong": "verified",
        "lỗi": "failed",
        "chờ xử lý": "assigned",
        "pending": "assigned",
        "completed": "verified",
        "waiting": "verifying",
    }
    
    status = status_map.get(new_status.lower(), new_status.lower())
    if status not in TASK_STATUS_SET:
        # If not in standard set, just use it as is if it's one of the status flags
        pass

    updates = {}
    if status == "verified":
        updates["verified_at"] = _now_iso()
    elif status == "reported":
        updates["reported_at"] = _now_iso()
        
    updates = _with_status_columns(updates, status)
    
    _update_task_cells(tab, headers, int(row["_row_number"]), updates)
    _, _, refreshed = _find_task_row(task_id, email)
    return _public_task(refreshed)


def mark_comments_locked(task_id: str, email: str) -> dict[str, Any]:
    """Mark a post as having locked comments."""
    tab, headers, row = _find_task_row(task_id, email)
    
    updates = {
        "comments_locked": "TRUE",
        "status": "cancelled"
    }
    # Reset other status checkboxes to FALSE
    for item in TASK_STATUSES:
        updates[item] = "FALSE"
    # Optionally set cancelled to TRUE since we set status to cancelled
    updates["cancelled"] = "TRUE"
    
    _update_task_cells(tab, headers, int(row["_row_number"]), updates)
    _, _, refreshed = _find_task_row(task_id, email)
    return _public_task(refreshed)
