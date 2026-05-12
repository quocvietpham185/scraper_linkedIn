# Hướng Dẫn Cài Đặt Hệ Thống Lên Máy Ảo (VPS Linux/Ubuntu)

Việc chạy công cụ cào dữ liệu Playwright trên máy tính cá nhân (Local) thường tiêu tốn khá nhiều RAM và làm máy bị đơ nếu chạy lâu. Đưa hệ thống lên máy ảo (VPS) là giải pháp tối ưu nhất.

Dưới đây là hướng dẫn từng bước để bạn tự thiết lập toàn bộ backend lên một VPS Ubuntu (khuyến nghị bản 22.04 hoặc 24.04, RAM tối thiểu 2GB).

---

## Bước 1: Cài đặt các công cụ cơ bản trên VPS
Sau khi đăng nhập vào VPS qua SSH, hãy chạy các lệnh sau để cập nhật hệ thống và cài Python:

```bash
# Cập nhật danh sách gói phần mềm
sudo apt update && sudo apt upgrade -y

# Cài đặt Python 3, pip và venv
sudo apt install python3 python3-pip python3-venv git -y
```

## Bước 2: Tải mã nguồn và tạo môi trường ảo
Bạn tải toàn bộ thư mục code của project này (đặc biệt là thư mục `linkedin_group_crawler`) lên VPS.

```bash
# Di chuyển vào thư mục code backend
cd /đường/dẫn/tới/linkedin_group_crawler

# Tạo môi trường ảo (giúp code không bị xung đột với các app khác trên VPS)
python3 -m venv .venv

# Kích hoạt môi trường ảo
source .venv/bin/activate
```
*(Lưu ý: Sau khi kích hoạt, bạn sẽ thấy chữ `(.venv)` hiện ở đầu dòng lệnh).*

## Bước 3: Cài đặt thư viện và Playwright
Vì VPS Linux thường là bản Server không có giao diện (GUI), Playwright cần cài thêm các thư viện đồ họa hệ điều hành ngầm.

```bash
# Cài các thư viện Python
pip install -r requirements.txt

# Cài đặt trình duyệt Chromium và CÁC THƯ VIỆN HỆ ĐIỀU HÀNH BẮT BUỘC (--with-deps)
playwright install --with-deps chromium
```

## Bước 4: Thiết lập file cấu hình (.env)
Tạo file `.env` từ file mẫu:

```bash
cp .env.example .env
nano .env
```
Trong màn hình `nano`, bạn điền đầy đủ các thông tin:
- `GOOGLE_SERVICE_ACCOUNT_JSON=...` (Upload file JSON key lên VPS và trỏ đường dẫn vào đây)
- `GOOGLE_SPREADSHEET_ID=...`
- `TELEGRAM_BOT_TOKEN=...`
- `TELEGRAM_CHAT_ID=...`

Sau khi điền xong, ấn `Ctrl + O` -> `Enter` để lưu, và `Ctrl + X` để thoát.

## Bước 5: Chạy hệ thống ngầm mãi mãi (Dùng PM2)
Nếu bạn chạy bằng lệnh `uvicorn` bình thường, khi bạn tắt cửa sổ SSH thì server cũng tắt theo. Do đó, ta nên dùng `pm2` để quản lý.

```bash
# 1. Cài đặt Node.js và npm (nếu VPS chưa có)
sudo apt install nodejs npm -y

# 2. Cài đặt PM2 toàn cầu
sudo npm install -g pm2

# 3. Khởi chạy Backend bằng PM2
pm2 start "uvicorn app.main:app --host 0.0.0.0 --port 8101" --name linkedin-backend

# 4. Lưu cấu hình PM2 để tự động chạy lại khi VPS bị khởi động lại
pm2 save
pm2 startup
```

---

### Một số lệnh PM2 hữu ích để quản lý
- **Xem log hệ thống (quan trọng để theo dõi tiến trình cào):**
  `pm2 logs linkedin-backend`
- **Khởi động lại server:**
  `pm2 restart linkedin-backend`
- **Dừng server:**
  `pm2 stop linkedin-backend`

### Lưu ý quan trọng cho VPS
1. **Firewall:** Nếu bạn dùng AWS/Google Cloud/DigitalOcean, nhớ mở Port `8101` trên Firewall/Security Group để UI có thể gọi tới được API này.
2. **Headless:** Project đã mặc định chạy Playwright ở chế độ `headless=True` (không mở UI trình duyệt), rất phù hợp cho VPS Linux.
