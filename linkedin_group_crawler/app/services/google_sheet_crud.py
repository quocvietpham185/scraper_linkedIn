from __future__ import annotations
from typing import Any
from app.services.google_sheet_service import get_sheets_service, settings, _sheet_a1, resolve_top_posts_tab_title, resolve_group_urls_tab_title, _read_values, _normalize_header_cell, _normalize_group_url
from app.utils.logger import get_logger

logger = get_logger(__name__)

def get_error_logs_tab_title(sid: str) -> str:
    # Cố gắng tìm tab 'error_logs'
    from app.services.google_sheet_service import get_spreadsheet_sheet_titles
    titles = get_spreadsheet_sheet_titles(sid)
    for t in titles:
        if t.strip().lower() == "error_logs":
            return t
    # Fallback 2: Check for 'Log' tab which often exists
    for t in titles:
        if t.strip().lower() == "log":
            return t
    # Final fallback
    return "error_logs"

def append_error_log_to_sheet(date: str, group_name: str, group_url: str, email: str, message: str, error: str) -> None:
    sid = settings.google_spreadsheet_id
    tab = get_error_logs_tab_title(sid)
    service = get_sheets_service()
    
    row = [date, group_name, email, "FAILED", group_url, message, error]
    
    try:
        service.spreadsheets().values().append(
            spreadsheetId=sid,
            range=_sheet_a1(sid, tab, "A:A"),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        logger.info(f"Đã lưu error log cho {group_url} vào tab {tab}")
    except Exception as e:
        logger.error(f"Lỗi khi lưu error log: {e}")

def add_group_to_sheet(url: str, name: str, member: str, email: str) -> bool:
    sid = settings.google_spreadsheet_id
    top_tab = resolve_top_posts_tab_title(sid)
    tab = resolve_group_urls_tab_title(sid, top_tab)
    if not tab:
        return False

    service = get_sheets_service()
    row = [url, email, name, member] # Mapping: URL_Nhóm, email, Tên nhóm, Thành viên
    try:
        service.spreadsheets().values().append(
            spreadsheetId=sid,
            range=_sheet_a1(sid, tab, "A:A"),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Lỗi khi thêm nhóm {url}: {e}")
        return False

def _find_row_by_url_and_email(sid: str, tab: str, url: str, email: str) -> tuple[int, list[str]]:
    raw = _read_values(spreadsheet_id=sid, range_a1=_sheet_a1(sid, tab, "A:Z"))
    if len(raw) < 2:
        return -1, []
    
    headers = [str(c or "").strip() for c in raw[0]]
    try:
        url_idx = next(i for i, h in enumerate(headers) if "url" in h.lower() and "nh" in h.lower())
    except StopIteration:
        url_idx = 0
    try:
        email_idx = next(i for i, h in enumerate(headers) if "email" in h.lower())
    except StopIteration:
        email_idx = 1

    want_url = _normalize_group_url(url)
    want_email = (email or "").strip().lower()

    for offset, line in enumerate(raw[1:], start=2):
        cells = list(line) + [""] * (max(url_idx, email_idx) + 1 - len(line))
        cell_url = cells[url_idx] if url_idx < len(cells) else ""
        cell_email = cells[email_idx] if email_idx < len(cells) else ""
        
        if _normalize_group_url(str(cell_url)) == want_url and str(cell_email).strip().lower() == want_email:
            return offset, headers

    return -1, headers

def remove_group_from_sheet(url: str, email: str) -> bool:
    sid = settings.google_spreadsheet_id
    top_tab = resolve_top_posts_tab_title(sid)
    tab = resolve_group_urls_tab_title(sid, top_tab)
    if not tab:
        return False

    row_num, _ = _find_row_by_url_and_email(sid, tab, url, email)
    if row_num == -1:
        return False
        
    service = get_sheets_service()
    
    # Để xóa 1 dòng bằng Google Sheets API: phải dùng batchUpdate với deleteDimension
    try:
        # Lấy sheet_id của tab
        sheet_metadata = service.spreadsheets().get(spreadsheetId=sid).execute()
        sheets = sheet_metadata.get('sheets', '')
        sheet_id = None
        for s in sheets:
            if s.get("properties", {}).get("title") == tab:
                sheet_id = s.get("properties", {}).get("sheetId")
                break
                
        if sheet_id is None:
            return False

        requests = [{
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row_num - 1, # zero-indexed, inclusive
                    "endIndex": row_num # zero-indexed, exclusive
                }
            }
        }]
        service.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": requests}
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Lỗi khi xóa nhóm {url}: {e}")
        return False

def update_group_in_sheet(old_url: str, new_url: str, new_name: str, new_member: str, email: str) -> bool:
    sid = settings.google_spreadsheet_id
    top_tab = resolve_top_posts_tab_title(sid)
    tab = resolve_group_urls_tab_title(sid, top_tab)
    if not tab:
        return False

    row_num, headers = _find_row_by_url_and_email(sid, tab, old_url, email)
    if row_num == -1:
        return False

    # Giả sử headers: URL_Nhóm, email, Tên nhóm, Thành viên
    try:
        url_idx = next(i for i, h in enumerate(headers) if "url" in h.lower() and "nh" in h.lower())
    except StopIteration:
        url_idx = 0
    try:
        name_idx = next(i for i, h in enumerate(headers) if "tên" in h.lower() and "nhóm" in h.lower())
    except StopIteration:
        name_idx = 2
    try:
        member_idx = next(i for i, h in enumerate(headers) if "thành" in h.lower() and "viên" in h.lower())
    except StopIteration:
        member_idx = 3

    # Cập nhật từng ô 
    service = get_sheets_service()
    data = []
    if new_url:
        data.append({"range": f"{tab}!{chr(ord('A')+url_idx)}{row_num}", "values": [[new_url]]})
    if new_name:
        data.append({"range": f"{tab}!{chr(ord('A')+name_idx)}{row_num}", "values": [[new_name]]})
    if new_member:
        data.append({"range": f"{tab}!{chr(ord('A')+member_idx)}{row_num}", "values": [[new_member]]})

    if not data:
        return True

    try:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": data
            }
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Lỗi khi cập nhật nhóm {old_url}: {e}")
        return False
def bulk_add_groups_to_sheet(items: list[dict[str, Any]], email: str) -> bool:
    """Add multiple groups to the sheet in a single call."""
    if not items:
        return True
        
    sid = settings.google_spreadsheet_id
    top_tab = resolve_top_posts_tab_title(sid)
    tab = resolve_group_urls_tab_title(sid, top_tab)
    if not tab:
        return False

    service = get_sheets_service()
    rows = []
    for item in items:
        url = item.get("url_group", "")
        name = item.get("name_group", "")
        member = str(item.get("member", "0"))
        rows.append([url, email, name, member])

    try:
        service.spreadsheets().values().append(
            spreadsheetId=sid,
            range=_sheet_a1(sid, tab, "A:A"),
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
        return True
    except Exception as e:
        logger.error(f"Lỗi khi thêm hàng loạt nhóm: {e}")
        return False
