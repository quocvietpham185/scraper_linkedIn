# Production Environment Variables Template

## For Render (Backend)

Create these environment variables in Render dashboard:

```
# Core Configuration
HEADLESS=true
HOST=0.0.0.0
PORT=8111
STATE_PATH=/app/storage/linkedin_state.json

# Frontend Origin (for CORS) - UPDATE WITH YOUR VERCEL URL
CORS_ORIGINS=https://your-project.vercel.app,https://your-custom-domain.com

# Authentication
API_KEY=<generate-strong-random-key-min-32-chars>

# Browser & Scraping
USE_PERSISTENT_PROFILE=true
DEFAULT_SCROLL_TIMES=8
DEFAULT_SCROLL_DELAY_MS=2000
APIFY_FALLBACK_ENABLED=true

# External Services (from GitHub Secrets)
APIFY_TOKEN=<from-apify-console>
GOOGLE_SERVICE_ACCOUNT_JSON=<entire-json-file>
TELEGRAM_BOT_TOKEN=<from-telegram-botfather>

# n8n Webhooks (optional)
N8N_WEBHOOK_URL=<your-n8n-webhook>
N8N_WEBHOOK_TIMEOUT_SEC=30
N8N_WEBHOOK_ADD_LIST_GROUP_TIMEOUT_SEC=300
```

### How to Generate Strong API Key:

```bash
# On Mac/Linux:
openssl rand -base64 32

# On Windows PowerShell:
[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Random -Minimum 100000000 -Maximum 999999999).ToString() + (Get-Random -Minimum 100000000 -Maximum 999999999).ToString()))

# Or use online tools: https://generate-random.org/
```

---

## For Vercel (Frontend)

Set these in Vercel dashboard → Project Settings → Environment Variables:

```
# Backend API URL (must match Render deployment)
NEXT_PUBLIC_LINKEDIN_CRAWLER_API_URL=https://linkedin-scraper-api.onrender.com/api

# API Key (same as backend API_KEY)
NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY=<api-key-from-render>

# Internal API URL (for server-side calls)
LINKEDIN_CRAWLER_INTERNAL_API_URL=https://linkedin-scraper-api.onrender.com
```

**Note**: Variables starting with `NEXT_PUBLIC_` are exposed to the browser (public).

---

## GitHub Secrets Required for CI/CD

Set these in GitHub repo → Settings → Secrets and variables → Actions:

### For Backend Deployment

- `RENDER_API_KEY` - From Render account settings
- `RENDER_SERVICE_ID` - From Render service details (get after creating service)
- `APIFY_TOKEN`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `TELEGRAM_BOT_TOKEN`
- `N8N_WEBHOOK_URL`

### For Frontend Deployment

- `VERCEL_TOKEN` - From Vercel account settings
- `VERCEL_ORG_ID` - From Vercel team settings
- `VERCEL_PROJECT_ID` - From Vercel project settings (get after creating project)

---

## Checking Your Values Before Deployment

Before deploying, verify:

```bash
# 1. Check API key is strong enough (min 32 chars)
echo $API_KEY | wc -c

# 2. Check CORS origins format (comma-separated, HTTPS only)
echo $CORS_ORIGINS

# 3. Check Apify token exists
curl -H "Authorization: Bearer $APIFY_TOKEN" https://api.apify.com/v2/users/me

# 4. Check Google service account JSON is valid
echo $GOOGLE_SERVICE_ACCOUNT_JSON | jq .

# 5. Check Telegram token
curl https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe
```

---

## After First Deployment

1. ✅ Test backend health endpoint:

   ```bash
   curl https://linkedin-scraper-api.onrender.com/health
   ```

2. ✅ Test frontend loads:

   ```bash
   curl https://your-project.vercel.app/vietpq-scraper
   ```

3. ✅ Monitor logs:
   - Render: Dashboard → Service → Logs
   - Vercel: Dashboard → Deployments → Logs

4. ✅ Update CORS if you add custom domain:
   - Add new domain to `CORS_ORIGINS` in Render
   - Redeploy Render service

---

## Production Checklist

- [ ] GitHub Secrets set up
- [ ] Render service created and deployed
- [ ] Vercel project created and deployed
- [ ] CORS origins updated with Vercel domain
- [ ] Backend health check passing
- [ ] Frontend loads without errors
- [ ] API calls working (DevTools Network tab)
- [ ] Login workflow tested
- [ ] Monitors/alerts configured (optional)
- [ ] Backups configured (optional)

---

**Important**: Never commit `.env` or API keys to GitHub!
