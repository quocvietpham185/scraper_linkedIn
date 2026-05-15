# LinkedIn Apify Actor

Actor nay la Tier 2 trong workflow:

1. Playwright local tren VM
2. Apify Actor tu viet
3. Apify Actor 3rd party neu `APIFY_3RD_PARTY_FALLBACK_ENABLED=true`

## Chay local

```bash
cd linkedin-apify-actor
npm install
apify run
```

Input mau:

```json
{
  "groupUrls": ["https://www.linkedin.com/groups/1234567/"],
  "maxItems": 20,
  "scrollTimes": 3,
  "delayMinMs": 5000,
  "delayMaxMs": 12000,
  "maxConcurrency": 1,
  "maxRequestRetries": 0,
  "proxyConfiguration": {
    "useApifyProxy": true,
    "groups": ["RESIDENTIAL"],
    "countryCode": "VN"
  }
}
```

Neu group can login, truyen full `storageStateJson` Playwright tu backend. `sessionCookie` chi nen dung de test nhanh vi chi co cookie `li_at` thuong khong on dinh bang full storage state.

Neu LinkedIn tra ve login/checkpoint hoac redirect ve `https://www.linkedin.com/` / `/uas/login`, Actor ghi `SUMMARY.groups[].errorType` la `AUTH_REQUIRED`, `SESSION_INVALID`, hoac `GROUP_REDIRECTED` va luu debug HTML/screenshot trong Key-value store. Backend se bao ro LinkedIn khong chap nhan session tren Apify thay vi xem nhu khong co bai.

## Deploy len Apify

```bash
cd linkedin-apify-actor
npm install
apify login
apify push
```

Sau khi deploy, cap nhat backend `.env`:

```env
APIFY_TOKEN=...
APIFY_ACTOR_ID=yourUsername~linkedin-group-crawler
APIFY_OWN_ACTOR_ENABLED=true
APIFY_3RD_PARTY_FALLBACK_ENABLED=false
APIFY_PROXY_GROUPS=RESIDENTIAL
APIFY_PROXY_COUNTRY_CODE=VN
SCHEDULED_CRAWLER_TYPE=auto
```

## Output dataset

Moi item duoc push theo schema backend:

```json
{
  "author": "",
  "content": "",
  "likes": 0,
  "comments": 0,
  "reposts": 0,
  "post_url": "",
  "group_url": "",
  "group_name": "",
  "member_count": 0,
  "posted_at_raw": "",
  "posted_at": null
}
```

## Output SUMMARY

Actor luu record `SUMMARY` trong Key-value store de backend phan biet crawl loi voi khong co bai:

```json
{
  "succeeded": 1,
  "failed": 0,
  "groups": [
    {
      "groupUrl": "https://www.linkedin.com/groups/1234567/",
      "finalUrl": "https://www.linkedin.com/groups/1234567/",
      "status": "success",
      "errorType": null,
      "reachedGroup": true,
      "authRequired": false,
      "rawPostsCount": 10,
      "groupName": "Example group",
      "memberCount": 1000
    }
  ]
}
```
