# LinkedIn Group Crawler - Runbook Van Hanh VM

## 1) Chay local nhanh

```bash
py -3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 2) Kien truc deploy on dinh tren VM

- Monorepo: `/opt/apps/vietpq-linkedin-scraper`
- Backend code: `/opt/apps/vietpq-linkedin-scraper/linkedin_group_crawler`
- Frontend code: `/opt/apps/vietpq-linkedin-scraper/linkedin-crawler-ui`
- Private proxy code: `/opt/apps/vietpq-linkedin-scraper/vietpq-private-proxy`

Port runtime:
- Backend: `127.0.0.1:8101`
- Frontend: `127.0.0.1:3101`
- Proxy public: `0.0.0.0:18080`

URL su dung:
- UI: `http://10.30.50.29:18080/vietpq-scraper/quan-ly-nhom`
- API docs: `http://10.30.50.29:18080/vietpq-scraper/api/docs`
- API health (GET): `http://10.30.50.29:18080/vietpq-scraper/api/health`

Luu y:
- Khong dung Nginx shared lam duong vao chinh.
- Namespace rieng cua app la `/vietpq-scraper`.

## 3) Cau hinh frontend bat buoc (de khong loi prefix)

Trong `linkedin-crawler-ui/next.config.ts`:

```ts
basePath: "/vietpq-scraper",
assetPrefix: "/vietpq-scraper",
```

Trong `linkedin-crawler-ui/.env.local`:

```env
NEXT_PUBLIC_LINKEDIN_CRAWLER_API_URL=http://10.30.50.29:18080/vietpq-scraper/api
NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY=secret_api_key
```

Luu y:
- FE mo qua `:18080` thi API URL phai cung origin `:18080`.

## 4) PM2 process chuan

Ten process:
- `vietpq-backend`
- `vietpq-frontend`
- `vietpq-proxy`

Lenh quan ly:

```bash
pm2 status
pm2 logs vietpq-backend --lines 120
pm2 logs vietpq-frontend --lines 120
pm2 logs vietpq-proxy --lines 120
pm2 restart vietpq-backend --update-env
pm2 restart vietpq-frontend --update-env
pm2 restart vietpq-proxy --update-env
pm2 save
```

## 5) Lenh start lai full stack (chuan da verify)

```bash
cd /opt/apps/vietpq-linkedin-scraper

# Backend (.venv, KHONG phai venv)
pm2 delete vietpq-backend || true
pm2 start "bash -lc 'cd /opt/apps/vietpq-linkedin-scraper/linkedin_group_crawler && source .venv/bin/activate && python -m uvicorn app.main:app --host 127.0.0.1 --port 8101'" --name vietpq-backend --update-env

# Frontend standalone
pm2 delete vietpq-frontend || true
pm2 start "bash -lc 'cd /opt/apps/vietpq-linkedin-scraper/linkedin-crawler-ui && HOSTNAME=127.0.0.1 PORT=3101 node .next/standalone/server.js'" --name vietpq-frontend --update-env

# Proxy
pm2 delete vietpq-proxy || true
pm2 start server.js --name vietpq-proxy --cwd /opt/apps/vietpq-linkedin-scraper/vietpq-private-proxy --update-env

pm2 save
```

## 6) Proxy config chuan (anti-prefix bug)

File: `/opt/apps/vietpq-linkedin-scraper/vietpq-private-proxy/server.js`

```js
const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");

const app = express();
const PORT = process.env.PORT || 18080;

app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Credentials", "true");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key, X-Requested-With");
  if (req.method === "OPTIONS") return res.sendStatus(204);
  next();
});

// API: rewrite bo /vietpq-scraper/api
app.use(createProxyMiddleware({
  target: "http://127.0.0.1:8101",
  changeOrigin: true,
  pathFilter: (path) => path.startsWith("/vietpq-scraper/api"),
  pathRewrite: { "^/vietpq-scraper/api": "" },
}));

// Frontend: GIU NGUYEN duong dan /vietpq-scraper/*
app.use(createProxyMiddleware({
  target: "http://127.0.0.1:3101",
  changeOrigin: true,
  pathFilter: (path) => path.startsWith("/vietpq-scraper") && !path.startsWith("/vietpq-scraper/api"),
}));

app.listen(PORT, "0.0.0.0", () => {
  console.log(`vietpq-private-proxy listening on :${PORT}`);
});
```

Quan trong:
- Khong dung `app.use("/vietpq-scraper", proxy)` neu muon giu nguyen path, vi Express se cat prefix truoc khi forward.
- Bat buoc listen `0.0.0.0` neu can truy cap tu may khac trong LAN.
- Khong them redirect tay `/vietpq-scraper -> /vietpq-scraper/` trong proxy neu da co redirect tu Next, de tranh `ERR_TOO_MANY_REDIRECTS`.

## 7) Build frontend dung cach (tranh mat CSS/chunk)

```bash
cd /opt/apps/vietpq-linkedin-scraper/linkedin-crawler-ui
rm -rf .next
npm run build
mkdir -p .next/standalone/.next
rm -rf .next/standalone/.next/static
cp -r .next/static .next/standalone/.next/static
[ -d public ] && { rm -rf .next/standalone/public; cp -r public .next/standalone/public; } || true
pm2 restart vietpq-frontend --update-env
pm2 save
```

## 8) Healthcheck nhanh sau deploy (pass/fail)

```bash
curl -s -o /dev/null -w "backend=%{http_code}\n" http://127.0.0.1:8101/health
curl -s -o /dev/null -w "direct=%{http_code}\n" http://127.0.0.1:3101/vietpq-scraper/quan-ly-nhom
curl -s -o /dev/null -w "root=%{http_code}\n" http://127.0.0.1:18080/vietpq-scraper
curl -s -o /dev/null -w "home=%{http_code}\n" http://127.0.0.1:18080/vietpq-scraper/
curl -s -o /dev/null -w "page=%{http_code}\n" http://127.0.0.1:18080/vietpq-scraper/quan-ly-nhom
curl -s -o /dev/null -w "api=%{http_code}\n" http://127.0.0.1:18080/vietpq-scraper/api/health
```

Gia tri pass:
- `backend=200`
- `direct=200`
- `root=200`
- `home=308` (redirect hop le)
- `page=200`
- `api=200`

## 9) Xu ly su co thuong gap

1) `Connection refused` tu may local vao `:18080`
- Kiem tra proxy listen `0.0.0.0:18080`:
  ```bash
  ss -ltnp | rg 18080
  ```
- Mo firewall:
  ```bash
  sudo ufw allow 18080/tcp
  ```

2) `api=200` nhung `page=404`
- Loi mapping prefix cua proxy.
- Dung lai file `server.js` theo muc 6 va restart `vietpq-proxy`.

3) `backend errored` voi `venv/bin/activate: No such file`
- VM dang dung `.venv`, khong phai `venv`.
- Sua PM2 command backend theo muc 5.

4) Sau pull/build thay doi nhung UI khong cap nhat
- Hard refresh trinh duyet: `Ctrl + F5`.
- Rebuild frontend theo muc 7.

## 10) Vi tri file quan trong

- Backend env: `/opt/apps/vietpq-linkedin-scraper/linkedin_group_crawler/.env`
- Frontend env: `/opt/apps/vietpq-linkedin-scraper/linkedin-crawler-ui/.env.local`
- Frontend config: `/opt/apps/vietpq-linkedin-scraper/linkedin-crawler-ui/next.config.ts`
- Proxy config: `/opt/apps/vietpq-linkedin-scraper/vietpq-private-proxy/server.js`
- PM2 dump: `/home/vmadmin/.pm2/dump.pm2`

## 11) Ghi chu Python 3.8

VM dang chay Python 3.8, da fix:
- `from typing import Annotated` -> `from typing_extensions import Annotated`
- `@dataclass(slots=True)` -> `@dataclass`

Khuyen nghi dai han: nang cap Python 3.10+.

## 12) Crawl tu dong hang ngay

Script co san: `scripts/daily_cron.py`. Script doc danh sach nhom tu Google Sheet va goi `/start` theo tung email.

Backend `.env` nen co:

```env
SCHEDULED_CRAWLER_TYPE=auto
APIFY_TOKEN=...
APIFY_ACTOR_ID=yourUsername~linkedin-group-crawler
APIFY_OWN_ACTOR_ENABLED=true
APIFY_3RD_PARTY_FALLBACK_ENABLED=false
```

Test thu cong tren VM:

```bash
cd /opt/apps/vietpq-linkedin-scraper/linkedin_group_crawler
source .venv/bin/activate
python scripts/daily_cron.py
```

Cron moi ngay 08:00:

```cron
0 8 * * * cd /opt/apps/vietpq-linkedin-scraper/linkedin_group_crawler && . .venv/bin/activate && python scripts/daily_cron.py >> storage/daily_cron.log 2>&1
```

Luon chay theo tier:

```text
Playwright local -> Apify Actor tu viet -> optional Actor 3rd party -> error_logs + Telegram
```
