# 🚀 Deployment Guide: LinkedIn Scraper to Vercel + Render

## Overview

This guide walks you through deploying the LinkedIn Scraper to production using:

- **Frontend**: Next.js → **Vercel**
- **Backend**: Python FastAPI → **Render**

**Status**: Code is ready ✅ | Configuration complete ✅ | Ready for deployment

---

## 🔑 Prerequisites

Before starting, you need:

1. GitHub account (already have) ✅
2. Vercel account (free: https://vercel.com)
3. Render account (free tier available: https://render.com)
4. API keys from services used in your app (Apify, Google, Telegram, etc.)

---

## 📋 Step 1: Set Up GitHub Secrets

GitHub Secrets keep your API keys safe and are used by CI/CD pipelines.

### For Backend (Render)

Go to **GitHub repo** → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name                   | Value                                           | Where to get                                                                             |
| ----------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `RENDER_API_KEY`              | Your Render API key                             | [render.com/account/api-tokens](https://dashboard.render.com/api-tokens)                 |
| `RENDER_SERVICE_ID`           | Your Render service ID (after creating service) | Render dashboard → Service details                                                       |
| `APIFY_TOKEN`                 | Your Apify token                                | [console.apify.com/account/integrations](https://console.apify.com/account/integrations) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON from Google Service Account           | JSON file from Google Cloud Console                                                      |
| `TELEGRAM_BOT_TOKEN`          | Your Telegram bot token                         | Telegram BotFather                                                                       |
| `N8N_WEBHOOK_URL`             | Your n8n webhook URL                            | Your n8n instance                                                                        |

### For Frontend (Vercel)

Add these secrets:

| Secret Name                            | Value                  | Example                                                                    |
| -------------------------------------- | ---------------------- | -------------------------------------------------------------------------- |
| `VERCEL_TOKEN`                         | Your Vercel API token  | Generate at [vercel.com/account/tokens](https://vercel.com/account/tokens) |
| `VERCEL_ORG_ID`                        | Your Vercel org ID     | Found in Vercel dashboard → Settings                                       |
| `VERCEL_PROJECT_ID`                    | Your Vercel project ID | Created after step 3                                                       |
| `NEXT_PUBLIC_LINKEDIN_CRAWLER_API_URL` | Backend URL            | `https://linkedin-scraper-api.onrender.com/api`                            |
| `NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY` | API key                | Same as `API_KEY` in Render                                                |
| `LINKEDIN_CRAWLER_INTERNAL_API_URL`    | Backend internal URL   | `https://linkedin-scraper-api.onrender.com`                                |

---

## 🔧 Step 2: Deploy Backend to Render

### 2.1 Create Render Web Service

1. Go to **[render.com](https://render.com)** → **Dashboard**
2. Click **New +** → **Web Service**
3. Click **Connect** next to your GitHub repo
4. Select the repository: `linkedin-scraper-vietpq`
5. Fill in the form:

   | Field           | Value                     |
   | --------------- | ------------------------- |
   | **Name**        | `linkedin-scraper-api`    |
   | **Environment** | `Docker`                  |
   | **Region**      | `Oregon` (closest to you) |
   | **Branch**      | `main`                    |

6. Click **Create Web Service**

### 2.2 Configure Environment Variables

In Render dashboard for `linkedin-scraper-api` service:

1. Go to **Environment** tab
2. Add these variables:

   ```
   HEADLESS=true
   HOST=0.0.0.0
   CORS_ORIGINS=https://your-vercel-domain.vercel.app,https://your-custom-domain.com
   STATE_PATH=/app/storage/linkedin_state.json
   USE_PERSISTENT_PROFILE=true
   APIFY_FALLBACK_ENABLED=true
   API_KEY=<generate-strong-random-key>
   ```

3. Add secrets (from GitHub Secrets):
   - `APIFY_TOKEN` (from Apify console)
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (entire JSON file contents)
   - `TELEGRAM_BOT_TOKEN` (optional)
   - `N8N_WEBHOOK_URL` (optional)

### 2.3 Test Backend Deployment

Once deployed:

1. Go to **render.com** → Your service → **Logs**
2. Wait for build to complete (5-10 minutes)
3. Test health endpoint:
   ```bash
   curl https://linkedin-scraper-api.onrender.com/health
   ```
   Expected response: `{"status":"ok"}` or similar

---

## 🌐 Step 3: Deploy Frontend to Vercel

### 3.1 Create Vercel Project

1. Go to **[vercel.com](https://vercel.com)** → **Dashboard**
2. Click **Add New** → **Project**
3. Click **Import** next to your GitHub repo
4. Select the repository: `linkedin-scraper-vietpq`
5. Fill in the form:

   | Field                | Value                                  |
   | -------------------- | -------------------------------------- |
   | **Project Name**     | `linkedin-crawler-ui` (or your choice) |
   | **Root Directory**   | `linkedin-crawler-ui`                  |
   | **Framework**        | `Next.js`                              |
   | **Build Command**    | `npm run build`                        |
   | **Output Directory** | `.next`                                |
   | **Install Command**  | `npm ci`                               |

### 3.2 Configure Environment Variables

On the **Environment Variables** page:

1. Add these variables for **Production**:

   ```
   NEXT_PUBLIC_LINKEDIN_CRAWLER_API_URL=https://linkedin-scraper-api.onrender.com/api
   NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY=<API_KEY_FROM_RENDER>
   LINKEDIN_CRAWLER_INTERNAL_API_URL=https://linkedin-scraper-api.onrender.com
   ```

2. Click **Deploy**

### 3.3 Test Frontend Deployment

Once deployed:

1. Vercel will show deployment URL (e.g., `https://linkedin-crawler-ui.vercel.app`)
2. Wait for build to complete (2-5 minutes)
3. Open the URL and test:
   - ✅ Dashboard loads
   - ✅ API calls work (check DevTools → Network tab)
   - ✅ Login works (if testing login flow)

---

## 🔗 Step 4: Update CORS Configuration

After you have both URLs, update CORS on Render:

1. Go to **Render dashboard** → `linkedin-scraper-api` service
2. Go to **Environment** tab
3. Update `CORS_ORIGINS`:
   ```
   CORS_ORIGINS=https://your-project.vercel.app,https://your-custom-domain.com
   ```
4. Click **Save** (will redeploy)

---

## ✅ Step 5: Full System Testing

### Test 1: Health Check

```bash
# Backend is running
curl https://linkedin-scraper-api.onrender.com/health

# Frontend loads
curl https://your-project.vercel.app
```

### Test 2: API Integration

1. Open frontend in browser
2. Open DevTools → **Network** tab
3. Try an action (login, search groups, etc.)
4. Check that API requests go to `linkedin-scraper-api.onrender.com`
5. Verify response status is 200-201 (not 403 CORS error)

### Test 3: End-to-End Workflow

1. Login to frontend
2. Create/search group
3. Verify data persists in backend storage

---

## 🚨 Common Issues & Troubleshooting

### Frontend shows "API not found" or CORS error

**Problem**: `https://your-vercel-app.vercel.app` not in backend's `CORS_ORIGINS`

**Solution**:

1. Check Render dashboard → Service environment variables
2. Update `CORS_ORIGINS` to include your Vercel domain
3. Redeploy Render service

### Backend deployment fails

**Problem**: Dockerfile build error or missing dependencies

**Solution**:

1. Check **Render Logs** for error message
2. Verify `linkedin_group_crawler/requirements.txt` is complete
3. Ensure `.env` variables are set correctly

### API calls timeout

**Problem**: Playwright headless mode slow on Render free tier

**Solution**:

1. Upgrade to Render **Paid tier** ($7+/month)
2. Or increase timeouts in backend code
3. Monitor Render resource usage

### Data not persisting

**Problem**: File-based storage (`/app/storage`) is ephemeral on free tier

**Solution**:

1. Upgrade to Render **Paid tier** with persistent disks
2. Or migrate to PostgreSQL + Redis (more reliable for production)

---

## 📊 Monitoring & Logs

### Backend Logs

- **Render Dashboard** → Service → **Logs** tab
- Check for errors, warnings, API requests

### Frontend Logs

- **Vercel Dashboard** → Deployments → **Logs** tab
- Check build and runtime logs

### Application Monitoring (Optional)

Add error tracking:

- [Sentry.io](https://sentry.io) - Error tracking
- [DataDog](https://www.datadoghq.com/) - APM & monitoring
- [LogRocket](https://logrocket.com/) - Frontend monitoring

---

## 🔄 CI/CD Automatic Deployment

Configured GitHub Actions workflows will automatically deploy on push to `main`:

### Backend (`deploy-backend.yml`)

- Runs tests
- Builds Docker image
- Deploys to Render
- Health check

### Frontend (`deploy-frontend.yml`)

- Lints code
- Builds Next.js
- Deploys to Vercel

**Trigger**: Push to `main` branch

---

## 📝 Environment Variables Reference

### Frontend (`.env.production` - Vercel)

```
NEXT_PUBLIC_LINKEDIN_CRAWLER_API_URL=https://linkedin-scraper-api.onrender.com/api
NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY=your_api_key
LINKEDIN_CRAWLER_INTERNAL_API_URL=https://linkedin-scraper-api.onrender.com
```

### Backend (Render Environment Variables)

```
HEADLESS=true
HOST=0.0.0.0
PORT=8111
CORS_ORIGINS=https://your-vercel-app.vercel.app,https://your-custom-domain.com
API_KEY=your_strong_api_key
STATE_PATH=/app/storage/linkedin_state.json
APIFY_TOKEN=your_apify_token
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
TELEGRAM_BOT_TOKEN=your_bot_token
N8N_WEBHOOK_URL=your_n8n_webhook_url
```

---

## 🎯 Next Steps

1. ✅ Set up GitHub Secrets (Step 1)
2. ✅ Deploy backend to Render (Step 2)
3. ✅ Deploy frontend to Vercel (Step 3)
4. ✅ Update CORS (Step 4)
5. ✅ Run tests (Step 5)
6. ⏭️ Add custom domain (optional)
7. ⏭️ Set up monitoring (optional)

---

## 💡 Tips & Best Practices

1. **Use environment variables** - Never commit secrets to GitHub
2. **Test locally first** - Verify it works with `npm run dev` and `python app/main.py`
3. **Monitor deployment logs** - Check Render/Vercel logs after each push
4. **Set up alerts** - Enable Render/Vercel notifications for deployment failures
5. **Backup data** - Set up automated backups for persistent data
6. **Version your API** - Add `/v1` prefix to API routes for future compatibility

---

## 📞 Support

- **Render Docs**: https://render.com/docs
- **Vercel Docs**: https://vercel.com/docs
- **Next.js Docs**: https://nextjs.org/docs
- **FastAPI Docs**: https://fastapi.tiangolo.com/

---

**Last Updated**: May 2026
**Status**: Production Ready ✅
