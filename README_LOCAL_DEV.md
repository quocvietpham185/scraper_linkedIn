# Hướng Dẫn Chạy Môi Trường Local (Local Development Workflow)

Dự án này là một hệ thống gồm 3 thành phần chính (Monorepo), được thiết kế để chạy cùng nhau thông qua một cổng Proxy.

## Kiến Trúc Hệ Thống (Môi trường Local)

1. **Backend** (Python FastAPI): Chạy trên port `8101`
2. **Frontend** (Next.js): Chạy trên port `3101`
3. **Proxy** (Node.js Express): Chạy trên port `18080`, làm nhiệm vụ điều hướng request.
   - Gọi API: `http://localhost:18080/vietpq-scraper/api` -> Forward tới Backend (8101)
   - Truy cập Giao diện: `http://localhost:18080/vietpq-scraper` -> Forward tới Frontend (3101)

---

## Các Bước Khởi Chạy Local

Bạn cần mở **3 tab Terminal riêng biệt**, mỗi tab chạy một thành phần.

### 1. Khởi chạy Backend (Terminal 1)

**Chức năng:** Xử lý logic nghiệp vụ và Crawler.

```bash
cd linkedin_group_crawler

# 1. Tạo môi trường ảo (nếu chưa có)
python -m venv .venv

# 2. Kích hoạt môi trường ảo
# Trên Windows:
.venv\Scripts\activate
# Trên Mac/Linux:
source .venv/bin/activate

# 3. Cài đặt các thư viện cần thiết
pip install -r requirements.txt

# 4. Cài đặt trình duyệt Playwright (Bắt buộc để cào dữ liệu)
playwright install

# 5. Thiết lập biến môi trường
copy .env.example .env
# Lưu ý mở file .env lên để điền các cấu hình quan trọng:
# - GOOGLE_SERVICE_ACCOUNT_JSON=...
# - GOOGLE_SPREADSHEET_ID=...
# - TELEGRAM_BOT_TOKEN=...
# - TELEGRAM_CHAT_ID=...

# 6. Khởi chạy Backend trên port 8111
uvicorn app.main:app --host 127.0.0.1 --port 8111 --reload
```
*Backend đã sẵn sàng nếu terminal không báo lỗi và báo đang lắng nghe trên port 8111.*

### 2. Khởi chạy Frontend (Terminal 2)

**Chức năng:** Giao diện người dùng Next.js.

```bash
cd linkedin-crawler-ui

# 1. Cài đặt các gói thư viện Node.js
npm install

# 2. Cấu hình biến môi trường
# Tạo file .env.local và đảm bảo nội dung như sau:
# NEXT_PUBLIC_LINKEDIN_CRAWLER_API_URL=http://localhost:18080/vietpq-scraper/api
# NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY=secret_api_key (nếu có yêu cầu key)

# 3. Khởi chạy Frontend trên port 3101
npm run dev -- -p 3111
```
*(Lưu ý bắt buộc dùng port 3101 để đồng bộ với Proxy)*

### 3. Khởi chạy Proxy Điều Hướng (Terminal 3)

**Chức năng:** Đóng vai trò làm cổng vào duy nhất, gom chung Backend và Frontend vào 1 domain và giúp Frontend tránh bị lỗi CORS/Prefix.

```bash
cd vietpq-private-proxy

# 1. Cài đặt các gói thư viện
npm install

# 2. Khởi chạy server Proxy
node server.js
```
*Bạn sẽ thấy log báo: `vietpq-private-proxy listening on :18080`*

---

## 🚀 Trải Nghiệm Ứng Dụng

Sau khi cả 3 Terminal đều báo chạy thành công, hãy mở trình duyệt lên và truy cập:

- **Giao diện chính:** [http://localhost:18080/vietpq-scraper](http://localhost:18080/vietpq-scraper) hoặc [http://localhost:18080/vietpq-scraper/quan-ly-nhom](http://localhost:18080/vietpq-scraper/quan-ly-nhom)
- **Kiểm tra API Backend (Swagger Docs):** [http://localhost:18080/vietpq-scraper/api/docs](http://localhost:18080/vietpq-scraper/api/docs)
- **Kiểm tra Health Check API:** [http://localhost:18080/vietpq-scraper/api/health](http://localhost:18080/vietpq-scraper/api/health)

### ⚠️ Một Vài Lưu Ý Khi Phát Triển Local
- Luôn luôn truy cập qua cổng `18080` của proxy. Nếu bạn truy cập trực tiếp port `3101` của Next.js, có thể giao diện sẽ bị lỗi đường dẫn (prefix `/vietpq-scraper`).
- Đảm bảo trong `linkedin-crawler-ui/next.config.ts` có cấu hình `basePath` và `assetPrefix` trỏ tới `"/vietpq-scraper"` (hiện tại trong code của người trước đã được cấu hình như vậy).
- Để tắt hệ thống, hãy vào từng Terminal và ấn `Ctrl + C`.
