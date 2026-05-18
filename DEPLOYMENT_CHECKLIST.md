# 📋 Deployment Checklist

## ✅ Code Preparation (COMPLETED)

- [x] Removed API rewrite rule from `next.config.ts`
- [x] Created `.env.production` for frontend with backend URL
- [x] Updated CORS example in `.env.example` with production note
- [x] Updated Dockerfile to use PORT environment variable
- [x] Created `render.yaml` configuration
- [x] Created `vercel.json` configuration
- [x] Created GitHub Actions workflows
  - [x] Backend deployment (`deploy-backend.yml`)
  - [x] Frontend deployment (`deploy-frontend.yml`)
- [x] Documentation created
  - [x] `DEPLOYMENT.md` - Full deployment guide
  - [x] `PRODUCTION_ENV_TEMPLATE.md` - Environment variables reference

---

## 🔧 Setup Phase (IN PROGRESS)

### Step 1: GitHub Secrets

- [ ] Generate GitHub Secrets
  - [ ] `RENDER_API_KEY` - Get from https://dashboard.render.com/api-tokens
  - [ ] `APIFY_TOKEN` - Get from https://console.apify.com/account/integrations
  - [ ] `GOOGLE_SERVICE_ACCOUNT_JSON` - Download from Google Cloud Console
  - [ ] `TELEGRAM_BOT_TOKEN` - Get from Telegram BotFather
  - [ ] `N8N_WEBHOOK_URL` - Your n8n webhook URL
  - [ ] `VERCEL_TOKEN` - Get from https://vercel.com/account/tokens
  - [ ] `VERCEL_ORG_ID` - From Vercel team settings
  - [ ] `VERCEL_PROJECT_ID` - Create after step 2

### Step 2: Deploy Backend to Render

- [ ] Create Render account at https://render.com
- [ ] Click **New Web Service**
- [ ] Connect GitHub repository
- [ ] Select `linkedin-scraper-vietpq` repo
- [ ] Set name: `linkedin-scraper-api`
- [ ] Select environment: `Docker`
- [ ] Set region: `Oregon` (or closest)
- [ ] Set branch: `main`
- [ ] Add environment variables from `PRODUCTION_ENV_TEMPLATE.md`
- [ ] Create service (wait for initial build)
- [ ] Copy `RENDER_SERVICE_ID` from URL to GitHub Secrets
- [ ] Test health endpoint: `curl https://linkedin-scraper-api.onrender.com/health`

### Step 3: Deploy Frontend to Vercel

- [ ] Create Vercel account at https://vercel.com
- [ ] Click **Add New** → **Project**
- [ ] Import GitHub repository
- [ ] Set root directory: `linkedin-crawler-ui`
- [ ] Set build command: `npm run build`
- [ ] Add environment variables:
  - [ ] `NEXT_PUBLIC_LINKEDIN_CRAWLER_API_URL=https://linkedin-scraper-api.onrender.com/api`
  - [ ] `NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY=<API_KEY_FROM_RENDER>`
  - [ ] `LINKEDIN_CRAWLER_INTERNAL_API_URL=https://linkedin-scraper-api.onrender.com`
- [ ] Deploy
- [ ] Copy `VERCEL_PROJECT_ID` from URL to GitHub Secrets
- [ ] Test frontend loads: Visit your Vercel URL

### Step 4: Update CORS

- [ ] Update `CORS_ORIGINS` in Render environment:
  - [ ] Include your Vercel URL: `https://your-project.vercel.app`
  - [ ] Include any custom domains
- [ ] Render will auto-redeploy

### Step 5: Full System Tests

- [ ] Backend health check passes
- [ ] Frontend loads without CORS errors
- [ ] API calls succeed (DevTools Network tab)
- [ ] Login workflow works (if applicable)
- [ ] Data persists in backend

---

## 🚀 Production Phase

### Before Going Live

- [ ] Monitor performance with real data
- [ ] Test all main workflows
- [ ] Set up error tracking (Sentry, DataDog, etc.)
- [ ] Set up monitoring/alerts
- [ ] Document any known issues
- [ ] Prepare rollback plan

### Going Live

- [ ] Update DNS if using custom domain
- [ ] Point custom domain to Vercel
- [ ] Update `CORS_ORIGINS` if using custom domain
- [ ] Announce deployment

### Post-Deployment

- [ ] Monitor logs for errors
- [ ] Test critical workflows daily
- [ ] Set up automated backups
- [ ] Plan database migration (if not using file-based storage)

---

## 📚 Documentation

All documentation is in the repository root:

- **`DEPLOYMENT.md`** - Step-by-step deployment guide
- **`PRODUCTION_ENV_TEMPLATE.md`** - Environment variables reference
- **`.github/workflows/`** - CI/CD automation workflows
- **`render.yaml`** - Render service configuration
- **`vercel.json`** - Vercel project configuration

---

## 🆘 Troubleshooting

If you encounter issues:

1. **Check logs first**:
   - Render: Dashboard → Service → Logs
   - Vercel: Dashboard → Deployments → Logs

2. **Verify environment variables**:
   - Are CORS_ORIGINS correct?
   - Is API_KEY set?
   - Are all secrets in place?

3. **Test manually**:

   ```bash
   # Backend health
   curl https://linkedin-scraper-api.onrender.com/health

   # Frontend
   curl https://your-project.vercel.app/vietpq-scraper
   ```

4. **Common issues**: See **Troubleshooting** section in `DEPLOYMENT.md`

---

## ✨ Next Steps After Deployment

1. **Set up monitoring** - Add error tracking and performance monitoring
2. **Optimize performance** - Monitor Render/Vercel resource usage
3. **Plan for scaling** - If successful, plan for paid tiers
4. **Database migration** - Consider migrating from file-based storage
5. **Custom domain** - Add custom domain if desired
6. **SSL/TLS** - Already handled by Vercel/Render

---

## 📞 Support & Resources

- **Render Docs**: https://render.com/docs
- **Vercel Docs**: https://vercel.com/docs
- **Next.js Docs**: https://nextjs.org/docs
- **FastAPI Docs**: https://fastapi.tiangolo.com
- **Docker Docs**: https://docs.docker.com

---

**Status**: Ready for Step 1 ✅
**Last Updated**: May 2026
**Next Action**: Follow `DEPLOYMENT.md` Step 1 to add GitHub Secrets
