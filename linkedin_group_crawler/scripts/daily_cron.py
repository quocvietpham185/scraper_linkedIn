import asyncio
import os
import httpx
from datetime import datetime

# Import các service có sẵn trong hệ thống để tận dụng logic
import sys
from pathlib import Path

# Thêm thư mục gốc vào PYTHONPATH để có thể import từ app
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from app.config import settings
from app.services.google_sheet_service import read_group_url_rows

async def run_scheduled_crawl():
    print(f"[{datetime.now()}] Bắt đầu tiến trình tự động crawl theo lịch...")
    
    # 1. Đọc danh sách Group từ Google Sheet
    try:
        raw_rows = read_group_url_rows()
    except Exception as e:
        print(f"Lỗi khi đọc Google Sheet: {e}")
        return

    # Gom nhóm URL theo từng Email nhân viên
    # Cấu trúc: { "nguyenvana@gmail.com": ["url1", "url2"], "tranvanb@gmail.com": ["url3"] }
    tasks_by_email = {}

    for row in raw_rows:
        url = row.get("URL_Nhóm") or row.get("url_group") or row.get("URL nhóm") or row.get("Link")
        email = row.get("Email") or row.get("Email_crawl") or row.get("email")
        
        # Chỉ lấy những group có URL hợp lệ và có gắn email nhân viên
        if url and email and "linkedin.com/groups/" in str(url):
            url_str = str(url).strip()
            email_str = str(email).strip().lower()
            
            if email_str not in tasks_by_email:
                tasks_by_email[email_str] = set()
            tasks_by_email[email_str].add(url_str)

    if not tasks_by_email:
        print("Không tìm thấy URL nhóm nào hợp lệ hoặc chưa được gán Email trong Google Sheet để cào.")
        return

    print(f"Đã phân loại được danh sách cào cho {len(tasks_by_email)} nhân viên.")

    # Lấy Port từ biến môi trường (mặc định 8111 như trong Dockerfile)
    backend_port = os.getenv("PORT", "8111")
    API_URL = f"http://localhost:{backend_port}/start" 
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
         headers["x-api-key"] = settings.api_key

    # 2. Gửi API request tới endpoint /start cho TỪNG nhân viên
    async with httpx.AsyncClient(timeout=30.0) as client:
        for employee_email, group_urls in tasks_by_email.items():
            print(f"-> Đang kích hoạt cào cho {employee_email} ({len(group_urls)} nhóm)...")
            
            payload = {
                "email": employee_email,
                # Dùng password giả lập vì force_relogin=False, backend sẽ tự lấy session đã lưu của nhân viên này
                "password": "dummy_password_not_used", 
                "force_relogin": False, 
                "max_posts": int(os.getenv("DEFAULT_MAX_ITEMS", 50)),
                "crawler_type": os.getenv("SCHEDULED_CRAWLER_TYPE", "auto"),
                "group_urls": list(group_urls) 
            }

            try:
                response = await client.post(API_URL, json=payload, headers=headers)
                response_data = response.json()
                if response_data.get("success"):
                     print(f"   [OK] {employee_email}: {response_data.get('message')}")
                else:
                     print(f"   [LỖI] {employee_email}: {response_data.get('message')}")
            except Exception as e:
                print(f"   [THẤT BẠI] Lỗi kết nối API cho {employee_email}: {e}")
                
            # Nghỉ 5 giây trước khi kích hoạt cho nhân viên tiếp theo để tránh quá tải Server (Nhiều Chromium mở cùng lúc)
            await asyncio.sleep(5)

    print(f"[{datetime.now()}] Hoàn thành việc gửi yêu cầu crawl hàng loạt!")

if __name__ == "__main__":
    asyncio.run(run_scheduled_crawl())
