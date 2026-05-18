import { Actor } from 'apify';
import { PlaywrightCrawler, log } from 'crawlee';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright';

import { extractGroupName, extractMemberCount, isAuthPage, parsePosts, findPostLocator } from './linkedinParser.js';
import { crawlGroupByApi, extractGroupId } from './groupApiCrawler.js';

function toInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function randomDelay(minMs, maxMs) {
  const min = Math.min(minMs, maxMs);
  const max = Math.max(minMs, maxMs);
  return Math.floor(min + Math.random() * (max - min + 1));
}

function normalizeUrlKey(url) {
  return String(url || '').trim().replace(/\/+$/, '');
}

function buildGroupSurfaceUrls(groupUrl) {
  const normalized = normalizeUrlKey(groupUrl);
  const groupId = extractGroupId(normalized);
  const urls = [normalized ? `${normalized}/` : groupUrl];
  if (groupId) {
    urls.push(
      `https://www.linkedin.com/groups/${groupId}/recent-activity/`,
      `https://www.linkedin.com/groups/${groupId}/feed/`,
      `https://www.linkedin.com/groups/${groupId}/?sortBy=RECENT`,
    );
  }
  return [...new Set(urls.filter(Boolean))];
}

function sanitizeKvPart(value) {
  const sanitized = String(value || 'default')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return sanitized || 'default';
}

function kvKey(prefix, ...parts) {
  return [prefix, ...parts.map(sanitizeKvPart)].join('__').slice(0, 240);
}

function sanitizeStoreName(value) {
  const sanitized = String(value || 'linkedin-actor-state')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .replace(/-{2,}/g, '-');
  return sanitized || 'linkedin-actor-state';
}

function boolInput(value, fallback = false) {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'boolean') return value;
  return ['1', 'true', 'yes', 'on'].includes(String(value).trim().toLowerCase());
}

function nowIso() {
  return new Date().toISOString();
}

function isFreshIso(value, ttlHours) {
  if (!value || !Number.isFinite(ttlHours) || ttlHours <= 0) return false;
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return false;
  return Date.now() - ts <= ttlHours * 60 * 60 * 1000;
}

function isFutureIso(value) {
  if (!value) return false;
  const ts = Date.parse(value);
  return Number.isFinite(ts) && ts > Date.now();
}

function storageStateFingerprint(state) {
  if (!state || typeof state !== 'object') return '';
  const cookies = Array.isArray(state.cookies)
    ? state.cookies.map((cookie) => ({
      name: cookie?.name || '',
      domain: cookie?.domain || '',
      path: cookie?.path || '',
      value: cookie?.value || '',
    }))
    : [];
  const origins = Array.isArray(state.origins) ? state.origins : [];
  return createHash('sha256')
    .update(JSON.stringify({ cookies, origins }))
    .digest('hex')
    .slice(0, 24);
}

function withTimeout(promise, timeoutMs, message) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timeoutId));
}

function buildStorageState(input) {
  if (input.storageState && typeof input.storageState === 'object') {
    return input.storageState;
  }
  if (input.storageStateJson && typeof input.storageStateJson === 'object') {
    return input.storageStateJson;
  }
  if (input.storageStateJson && typeof input.storageStateJson === 'string') {
    try {
      const parsed = JSON.parse(input.storageStateJson);
      if (parsed && typeof parsed === 'object') return parsed;
    } catch (error) {
      throw new Error(`storageStateJson is not valid JSON: ${error.message}`);
    }
  }
  if (!input.sessionCookie) return undefined;
  return {
    cookies: [
      {
        name: 'li_at',
        value: input.sessionCookie,
        domain: '.linkedin.com',
        path: '/',
        httpOnly: true,
        secure: true,
        sameSite: 'None',
      },
    ],
    origins: [],
  };
}

function classifyError(error, attemptMode = '') {
  const rawMessage = String(error?.message || error || '');
  const message = rawMessage.toLowerCase();
  const authWallPattern = /auth_challenge|uas\/login|\/login|checkpoint|authwall|auth required|requires login|security verification|welcome back/;

  if (/group_no_access|request to join|join this group|private group|not a member|content isn't available|content is unavailable|doesn.t exist|not found/i.test(rawMessage)) {
    return 'GROUP_NO_ACCESS';
  }
  if (/session_invalid_global/i.test(rawMessage)) {
    return 'SESSION_INVALID';
  }
  if (attemptMode === 'public' && authWallPattern.test(message)) {
    return 'AUTH_REQUIRED';
  }
  if (attemptMode === 'auth' && authWallPattern.test(message)) {
    return 'AUTH_REQUIRED';
  }
  if (/session invalid/i.test(rawMessage)) {
    return 'SESSION_INVALID';
  }
  if (/auth_challenge|uas\/login|final url:\s*\/uas\/login/i.test(rawMessage)) return 'AUTH_REQUIRED';
  if (/group_redirected/i.test(rawMessage)) return 'GROUP_REDIRECTED';
  if (/final url:\s*https:\/\/www\.linkedin\.com\/?\s*$/i.test(rawMessage) || /final url:\s*\/\s*$/i.test(rawMessage)) {
    return 'GROUP_REDIRECTED';
  }
  if (/login|checkpoint|authwall|auth required|requires login|security verification|welcome back/i.test(rawMessage)) {
    return 'AUTH_REQUIRED';
  }
  if (/timeout|timed out/i.test(rawMessage)) return 'TIMEOUT';
  if (/net::err_|target closed|navigation failed|tunnel_connection_failed/i.test(rawMessage)) return 'TRANSIENT_NAVIGATION';
  if (/too many redirects|too_many_redirects/i.test(rawMessage)) return 'AUTH_REQUIRED';
  if (/no post candidates|no posts parsed|empty unverified/i.test(rawMessage)) return 'EMPTY_UNVERIFIED_RESULT';
  if (/navigating and changing the content/i.test(rawMessage)) return 'PAGE_NAVIGATING';
  return 'CRAWL_ERROR';
}

function isTransientError(error, attemptMode = '') {
  const errorType = classifyError(error, attemptMode);
  if (['AUTH_REQUIRED', 'SESSION_INVALID', 'GROUP_REDIRECTED', 'GROUP_NO_ACCESS', 'EMPTY_UNVERIFIED_RESULT'].includes(errorType)) {
    return false;
  }
  const message = String(error?.message || error || '');
  return ['TIMEOUT', 'TRANSIENT_NAVIGATION', 'PAGE_NAVIGATING'].includes(errorType)
    || /Timeout|timed out|net::ERR_|Target closed|Navigation failed/i.test(message);
}

function statusFromErrorType(errorType, message = '') {
  if (errorType === 'AUTH_REQUIRED') return 'auth_required';
  if (errorType === 'SESSION_INVALID') {
    return /SESSION_INVALID_GLOBAL/i.test(message) ? 'session_invalid_global' : 'session_invalid';
  }
  if (errorType === 'GROUP_NO_ACCESS') return 'group_no_access';
  if (errorType === 'EMPTY_UNVERIFIED_RESULT') return 'empty_unverified';
  if (errorType === 'TIMEOUT') return 'timeout';
  if (errorType === 'GROUP_REDIRECTED') return 'group_redirected';
  return 'failed';
}

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&quot;/g, '"')
    .replace(/&#34;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .trim();
}

function stripHtml(value) {
  return decodeHtmlEntities(String(value || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' '));
}

function textAround(html, needle, radius = 2500) {
  const index = html.indexOf(needle);
  if (index < 0) return '';
  return html.slice(Math.max(0, index - radius), Math.min(html.length, index + needle.length + radius));
}

function parsePublicHtmlPosts(html, groupUrl, maxItems) {
  const posts = [];
  const seen = new Set();
  const urlPattern = /https:\/\/www\.linkedin\.com\/(?:feed\/update\/urn:li:activity:\d+|posts\/[^"'\\<\s]+|[^"'\\<\s]*activity-\d+)[^"'\\<\s]*/gi;
  const matches = [...String(html || '').matchAll(urlPattern)];

  for (const match of matches) {
    if (posts.length >= maxItems) break;
    const postUrl = decodeHtmlEntities(match[0]).split('?')[0];
    if (!postUrl || seen.has(postUrl)) continue;
    seen.add(postUrl);

    const nearby = textAround(html, match[0]);
    const author =
      stripHtml((nearby.match(/"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"/i) || [])[1])
      || stripHtml((nearby.match(/"actorName"\s*:\s*"([^"]+)"/i) || [])[1])
      || stripHtml((nearby.match(/"name"\s*:\s*"([^"]+)"/i) || [])[1]);
    const content =
      stripHtml((nearby.match(/"articleBody"\s*:\s*"([^"]+)"/i) || [])[1])
      || stripHtml((nearby.match(/"text"\s*:\s*"([^"]{20,})"/i) || [])[1])
      || stripHtml((nearby.match(/<p[^>]*>([\s\S]{20,800}?)<\/p>/i) || [])[1]);

    if (!content && !author) continue;
    posts.push({
      author,
      content,
      likes: 0,
      comments: 0,
      reposts: 0,
      post_url: postUrl,
      group_url: groupUrl,
      group_name: '',
      member_count: 0,
      posted_at_raw: '',
    });
  }

  return posts;
}

async function crawlPublicFast(groupUrl) {
  const startedAt = Date.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    log.info(`FAST_PUBLIC_START group=${groupUrl}`);
    const response = await fetch(groupUrl, {
      signal: controller.signal,
      headers: {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      },
      redirect: 'follow',
    });
    const finalUrl = response.url || groupUrl;
    const html = await response.text();
    if (
      /\/login|\/uas\/login|\/checkpoint|authwall/i.test(finalUrl)
      || /LinkedIn Login|Sign in \| LinkedIn|session_redirect|checkpoint|authwall/i.test(html)
    ) {
      return {
        groupUrl,
        finalUrl,
        status: 'auth_required',
        errorType: 'AUTH_REQUIRED',
        mode: 'public_fast',
        reachedGroup: false,
        authRequired: true,
        rawPostsCount: 0,
        postCandidateCount: 0,
        maxCandidateCount: 0,
        groupName: '',
        memberCount: 0,
        elapsedMs: Date.now() - startedAt,
        message: 'Fast public lane reached LinkedIn login/authwall.',
      };
    }
    const posts = parsePublicHtmlPosts(html, groupUrl, maxItems);
    if (!posts.length) {
      return {
        groupUrl,
        finalUrl,
        status: 'empty_public_result',
        errorType: 'EMPTY_PUBLIC_RESULT',
        mode: 'public_fast',
        reachedGroup: true,
        authRequired: false,
        rawPostsCount: 0,
        postCandidateCount: 0,
        maxCandidateCount: 0,
        groupName: '',
        memberCount: 0,
        elapsedMs: Date.now() - startedAt,
        message: 'Fast public lane did not find posts in public HTML.',
      };
    }

    const outputPosts = posts.map((post) => toOutputPost(post, {
      sessionId: actorSessionId,
      emailCrawl: actorEmailCrawl,
      groupUrl,
      totalPosts: posts.length,
      mode: 'public_fast',
    }));
    await Actor.pushData(outputPosts);
    await writePostCache(groupUrl, outputPosts, { mode: 'public_fast' });
    await updateGroupState(groupUrl, {
      requires_auth: false,
      last_public_status: 'success',
      last_error_type: null,
      last_success_source: 'public_fast',
      last_checked_at: nowIso(),
      last_success_at: nowIso(),
    });
    log.info(`FAST_PUBLIC_DONE group=${groupUrl} parsed=${posts.length} elapsedMs=${Date.now() - startedAt}`);
    return {
      groupUrl,
      finalUrl,
      status: 'success',
      errorType: null,
      mode: 'public_fast',
      reachedGroup: true,
      authRequired: false,
      rawPostsCount: posts.length,
      postCandidateCount: posts.length,
      maxCandidateCount: posts.length,
      groupName: '',
      memberCount: 0,
      elapsedMs: Date.now() - startedAt,
      message: 'Fast public crawl completed',
    };
  } catch (error) {
    const errorType = /abort/i.test(String(error?.name || error?.message || '')) ? 'TIMEOUT' : 'TRANSIENT_NAVIGATION';
    return {
      groupUrl,
      finalUrl: '',
      status: statusFromErrorType(errorType, String(error?.message || error)),
      errorType,
      mode: 'public_fast',
      reachedGroup: false,
      authRequired: false,
      rawPostsCount: 0,
      postCandidateCount: 0,
      maxCandidateCount: 0,
      groupName: '',
      memberCount: 0,
      elapsedMs: Date.now() - startedAt,
      message: `Fast public lane failed: ${error?.message || error}`,
    };
  } finally {
    clearTimeout(timeout);
  }
}

function calcScore(post) {
  const likes = Number(post.likes || 0);
  const comments = Number(post.comments || 0);
  const reposts = Number(post.reposts || post.repost || 0);
  return likes + comments * 3 + reposts * 5;
}

function toOutputPost(post, meta) {
  const likes = Number(post.likes || 0);
  const comments = Number(post.comments || 0);
  const reposts = Number(post.reposts || post.repost || 0);
  const groupUrl = post.group_url || post.groupUrl || meta.groupUrl || '';
  const postUrl = post.post_url || post.postUrl || post.url_article || '';
  const memberCount = Number(post.member_count || post.memberCount || post.members || 0);
  const postedAt = post.posted_at || post.posted_at_raw || post.day_up || '';

  return {
    id_session_crawl: meta.sessionId || '',
    email_crawl: meta.emailCrawl || '',
    day_returned: new Date().toISOString(),
    url_groups: groupUrl,
    url_article: postUrl,
    author: post.author || '',
    content: post.content || '',
    likes,
    comments,
    repost: reposts,
    score: calcScore({ likes, comments, reposts }),
    day_up: postedAt,
    members: memberCount,
    total_number_of_articles_obtained_each_time: Number(meta.totalPosts || 0),
    group_name: post.group_name || post.groupName || meta.groupName || '',
    crawl_mode: meta.mode || '',
    crawl_status: meta.crawlStatus || 'success',

    // Backward-compatible aliases consumed by the current backend normalizer.
    post_url: postUrl,
    group_url: groupUrl,
    reposts,
    member_count: memberCount,
    posted_at: post.posted_at || '',
    posted_at_raw: post.posted_at_raw || post.day_up || '',
  };
}

async function safeSavePageDebug(page, prefix) {
  const stamp = Date.now();
  try {
    const html = await page.content();
    await Actor.setValue(`${prefix}-${stamp}.html`, html, { contentType: 'text/html' });
  } catch (error) {
    log.warning(`Could not save ${prefix} HTML: ${error?.message || error}`);
  }
  try {
    const screenshot = await page.screenshot({ fullPage: true });
    await Actor.setValue(`${prefix}-${stamp}.png`, screenshot, { contentType: 'image/png' });
  } catch (error) {
    log.warning(`Could not save ${prefix} screenshot: ${error?.message || error}`);
  }
}

async function waitForSurfaceSignal(page, timeoutMs = 12000) {
  const articleWait = page
    .locator('[role="article"], div[data-urn^="urn:li:activity:"], div[data-id^="urn:li:activity:"], article.feed-shared-update-v2, div.feed-shared-update-v2')
    .first()
    .waitFor({ state: 'visible', timeout: timeoutMs });
  const headingWait = page.locator('h1').first().waitFor({ state: 'visible', timeout: timeoutMs });
  return Promise.any([articleWait, headingWait]).then(() => true).catch(() => false);
}

async function getPageTitle(page) {
  return (await page.title().catch(() => '')).trim();
}

function isLinkedInRoot(url) {
  return /^https:\/\/www\.linkedin\.com\/?$/i.test(String(url || '').trim());
}

async function assertNoAuthChallenge(page, { request, session, attemptMode, retireSession = false }) {
  const finalUrl = page.url();
  const title = await getPageTitle(page);
  if (await isAuthPage(page)) {
    if (retireSession) session?.retire?.();
    request.noRetry = true;
    throw new Error(`AUTH_CHALLENGE mode=${attemptMode} finalUrl=${finalUrl} title=${title}`);
  }
  if (isLinkedInRoot(finalUrl)) {
    if (retireSession) session?.retire?.();
    request.noRetry = true;
    throw new Error(`GROUP_REDIRECTED mode=${attemptMode} finalUrl=${finalUrl} title=${title}`);
  }
}

async function ensureGroupSurface(page, {
  groupUrl,
  request,
  session,
  attemptMode,
  navTimeoutMs = 30000,
}) {
  for (const candidateUrl of buildGroupSurfaceUrls(groupUrl)) {
    const currentUrl = page.url();
    const title = await getPageTitle(page);
    if (await isAuthPage(page)) {
      request.noRetry = true;
      throw new Error(`AUTH_CHALLENGE mode=${attemptMode} finalUrl=${currentUrl} title=${title}`);
    }
    const isBadSurface = await isAuthPage(page) || isLinkedInRoot(currentUrl);
    const hasSignal = await waitForSurfaceSignal(page, 4000);
    if (!isBadSurface && hasSignal) {
      return;
    }

    log.warning(
      `GROUP_SURFACE_RETRY mode=${attemptMode} group=${groupUrl} currentUrl=${currentUrl} title=${JSON.stringify(title)} nextUrl=${candidateUrl}`,
    );
    try {
      await page.goto(candidateUrl, {
        waitUntil: 'commit',
        timeout: navTimeoutMs,
      });
    } catch (error) {
      log.warning(`GROUP_SURFACE_GOTO_CONTINUE mode=${attemptMode} url=${candidateUrl} finalUrl=${page.url()} error=${error?.message || error}`);
    }
    await page.waitForTimeout(4000);
  }

  await assertNoAuthChallenge(page, {
    request,
    session,
    attemptMode,
    retireSession: false,
  });
}

async function detectGroupAccessIssue(page) {
  const text = await page
    .locator('body')
    .innerText({ timeout: 3000 })
    .catch(() => '');
  const normalized = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
  if (!normalized) return '';
  const checks = [
    ['request_to_join', /request to join|ask to join|join this group|join group/],
    ['private_group', /private group|this group is private|nh[oó]m ri[eê]ng tư/],
    ['not_member', /not a member|you are not a member|only members can see/],
    ['unavailable', /content isn't available|content is unavailable|this page doesn't exist|page not found|not found/],
  ];
  for (const [code, pattern] of checks) {
    if (pattern.test(normalized)) return code;
  }
  return '';
}

async function gotoLinkedInSurface({ page, session, url, navTimeoutMs = 30000, label = 'NAV' }) {
  log.info(`${label}_START url=${url} waitUntil=commit timeoutMs=${navTimeoutMs}`);
  let response = null;
  try {
    response = await page.goto(url, {
      waitUntil: 'commit',
      timeout: navTimeoutMs,
    });
  } catch (error) {
    log.warning(`${label}_GOTO_CONTINUE url=${url} finalUrl=${page.url()} error=${error?.message || error}`);
  }

  await page.waitForTimeout(3000);
  const finalUrl = page.url();
  const title = await getPageTitle(page);
  log.info(`${label}_DONE url=${url} finalUrl=${finalUrl} title=${JSON.stringify(title)} status=${response?.status?.() || ''}`);

  if (
    /\/checkpoint|\/login|\/uas\/login|\/authwall/i.test(finalUrl)
    || /sign in|welcome back|checkpoint|security verification/i.test(title)
  ) {
    session?.retire?.();
    throw new Error(`AUTH_CHALLENGE finalUrl=${finalUrl} title=${title}`);
  }

  await waitForSurfaceSignal(page, 12000);
  return {
    status: response?.status?.(),
    finalUrl,
    title,
  };
}

await Actor.init();

const input = await Actor.getInput() ?? {};
const groupUrls = Array.isArray(input.groupUrls)
  ? input.groupUrls.map((url) => String(url).trim()).filter(Boolean)
  : [];

if (groupUrls.length === 0) {
  throw new Error('Input groupUrls must contain at least one LinkedIn group URL.');
}

const inputMode = String(input.mode || 'auto').toLowerCase();
const mode = ['public', 'auth', 'auto', 'api'].includes(inputMode) ? inputMode : 'auto';

// ── API pagination mode params ──────────────────────────────────────────────
// apiMode=true  → use lightweight HTTP API pagination (like the log you saw)
// apiMode=false → fall back to Playwright browser scroll (original behavior)
const apiMode = boolInput(input.apiMode ?? input.api_mode, false) || mode === 'api';
const iterations = Math.min(Math.max(toInt(input.iterations, 6), 1), 50);
const pageDelayMinMs = Math.min(Math.max(toInt(input.pageDelayMinMs ?? input.page_delay_min_ms, 500), 0), 30000);
const pageDelayMaxMs = Math.min(Math.max(toInt(input.pageDelayMaxMs ?? input.page_delay_max_ms, 1500), pageDelayMinMs), 30000);
const apiMaxRetries = Math.min(Math.max(toInt(input.maxRetries ?? input.max_retries, 5), 0), 10);
const apiMaxConsecutiveFailedPages = Math.min(Math.max(toInt(input.maxConsecutiveFailedPages ?? input.max_consecutive_failed_pages, 3), 1), 10);
const apiFetchTimeoutMs = Math.min(Math.max(toInt(input.fetchTimeoutMs ?? input.fetch_timeout_ms, 20000), 5000), 60000);
const apiPageSize = Math.min(Math.max(toInt(input.apiPageSize ?? input.pageSize ?? input.count, 40), 1), 100);
const apiStart = Math.min(Math.max(toInt(input.apiStart ?? input.start, 0), 0), 10000);
const liPageInstance = String(input.liPageInstance || input.li_page_instance || '').trim();
const continueWithoutPaginationToken = boolInput(
  input.continueWithoutPaginationToken ?? input.continue_without_pagination_token,
  true,
);

// ── Browser scroll mode params ───────────────────────────────────────────────
const maxItems = apiMode
  ? Math.min(Math.max(toInt(input.maxItems, 500), 250), 5000)
  : Math.min(Math.max(toInt(input.maxItems, 20), 1), 500);
const scrollTimes = Math.min(Math.max(toInt(input.scrollTimes, 3), 1), 3);
const delayMinMs = Math.min(Math.max(toInt(input.delayMinMs, 5000), 5000), 120000);
const delayMaxMs = Math.min(Math.max(toInt(input.delayMaxMs, 12000), delayMinMs), 120000);
const groupDelayMinMs = Math.min(Math.max(toInt(input.groupDelayMinMs, 5000), 0), 1800000);
const groupDelayMaxMs = Math.min(Math.max(toInt(input.groupDelayMaxMs, 15000), groupDelayMinMs), 1800000);
const navigationTimeoutMs = Math.min(Math.max(toInt(input.navigationTimeoutMs, 30000), 15000), 120000);
const maxConcurrency = 1;
const maxRequestRetries = Math.min(Math.max(toInt(input.maxRequestRetries, 1), 0), 2);
const requestHandlerTimeoutSecs = Math.min(
  Math.max(Math.ceil((groupDelayMaxMs + navigationTimeoutMs + scrollTimes * delayMaxMs + 90000) / 1000), 120),
  1800,
);
const storageState = buildStorageState(input);
const proxyConfiguration = await Actor.createProxyConfiguration(input.proxyConfiguration);
const actorSessionId = String(input.sessionId || input.session_id || '').trim();
const actorEmailCrawl = String(input.emailCrawl || input.email_crawl || input.email || '').trim();
const accountId = String(input.accountId || input.account_id || actorEmailCrawl || actorSessionId || 'default').trim();
const targetDate = String(input.targetDate || input.target_date || '').trim() || 'all';
const allowCacheFallback = boolInput(input.allowCacheFallback ?? input.allow_cache_fallback, false);
const cacheTtlHours = Math.min(Math.max(toInt(input.cacheTtlHours ?? input.cache_ttl_hours, 6), 1), 168);
const groupStateTtlHours = Math.min(Math.max(toInt(input.groupStateTtlHours ?? input.group_state_ttl_hours, 24), 1), 720);
const sessionInvalidCooldownHours = Math.min(
  Math.max(toInt(input.sessionInvalidCooldownHours ?? input.session_invalid_cooldown_hours, 6), 1),
  168,
);
const storageFingerprint = storageStateFingerprint(storageState);
const stateStoreName = sanitizeStoreName(input.stateStoreName || input.state_store_name || 'linkedin-actor-state');
const stateStore = await Actor.openKeyValueStore(stateStoreName);

log.info('Actor input strategy', {
  mode,
  apiMode,
  accountId,
  groupsCount: groupUrls.length,
  maxItems,
  // API pagination params
  ...(apiMode ? { iterations, apiStart, apiPageSize, continueWithoutPaginationToken, pageDelayMinMs, pageDelayMaxMs, apiMaxRetries, apiMaxConsecutiveFailedPages } : {}),
  // Browser scroll params
  ...(!apiMode ? { scrollTimes, groupDelayMinMs, groupDelayMaxMs, navigationTimeoutMs, maxRequestRetries } : {}),
  allowCacheFallback,
  cacheTtlHours,
  groupStateTtlHours,
  sessionInvalidCooldownHours,
  stateStoreName,
});
log.info('LinkedIn session input', {
  cookiesCount: storageState?.cookies?.length || 0,
  hasLiAt: Boolean(storageState?.cookies?.some((cookie) => cookie.name === 'li_at')),
  originsCount: storageState?.origins?.length || 0,
});
if (storageState?.cookies?.length === 1 && !storageState?.origins?.length) {
  log.warning('LinkedIn session contains only one cookie. Full storageStateJson is strongly recommended.');
}

const summary = {
  startedAt: new Date().toISOString(),
  mode,
  accountId,
  totalGroups: groupUrls.length,
  succeeded: 0,
  failed: 0,
  proxyConfiguration: input.proxyConfiguration || null,
  stickyProxySession: true,
  allowCacheFallback,
  cacheTtlHours,
  groupStateTtlHours,
  sessionInvalidCooldownHours,
  stateStoreName,
  groups: [],
  attempts: [],
  errors: [],
  state: {
    sessionKey: kvKey('SESSION_STATE', accountId),
    groupKeys: groupUrls.map((url) => kvKey('GROUP_STATE', accountId, normalizeUrlKey(url))),
  },
};

const sessionStateKey = kvKey('SESSION_STATE', accountId);
const groupStateCache = new Map();
let cacheWrites = 0;
let cacheHits = 0;

async function readKvJson(key, fallback) {
  const value = await stateStore.getValue(key).catch((error) => {
    log.warning(`KV_READ_FAILED key=${key} error=${error?.message || error}`);
    return null;
  });
  return value && typeof value === 'object' && !Buffer.isBuffer(value) ? value : fallback;
}

async function writeKvJson(key, value) {
  await stateStore.setValue(key, value).catch((error) => {
    log.warning(`KV_WRITE_FAILED key=${key} error=${error?.message || error}`);
  });
}

function defaultSessionState() {
  return {
    account_id: accountId,
    email: actorEmailCrawl,
    status: 'unknown',
    health_score: 100,
    last_feed_check: null,
    last_success_at: null,
    fail_count: 0,
    last_error_type: null,
    proxy_country: input.proxyConfiguration?.countryCode || '',
    requires_relogin: false,
    cooldown_until: null,
    storage_state_fingerprint: storageFingerprint,
    updated_at: nowIso(),
  };
}

function defaultGroupState(groupUrl) {
  return {
    account_id: accountId,
    group_url: groupUrl,
    requires_auth: false,
    last_public_status: null,
    last_auth_status: null,
    last_error_type: null,
    account_has_access: null,
    last_success_source: null,
    last_checked_at: null,
    last_success_at: null,
    updated_at: nowIso(),
  };
}

let sessionState = await readKvJson(sessionStateKey, defaultSessionState());
if (storageFingerprint && sessionState.storage_state_fingerprint !== storageFingerprint) {
  sessionState = {
    ...defaultSessionState(),
    status: 'unknown',
    health_score: 100,
    fail_count: 0,
    requires_relogin: false,
    cooldown_until: null,
    last_error_type: null,
  };
  await writeKvJson(sessionStateKey, sessionState);
}

async function updateSessionState(patch) {
  sessionState = {
    ...sessionState,
    ...patch,
    account_id: accountId,
    email: actorEmailCrawl,
    storage_state_fingerprint: storageFingerprint || sessionState.storage_state_fingerprint || '',
    updated_at: nowIso(),
  };
  await writeKvJson(sessionStateKey, sessionState);
}

function groupStateKey(groupUrl) {
  return kvKey('GROUP_STATE', accountId, normalizeUrlKey(groupUrl));
}

async function getGroupState(groupUrl) {
  const key = normalizeUrlKey(groupUrl);
  if (groupStateCache.has(key)) return groupStateCache.get(key);
  const value = await readKvJson(groupStateKey(groupUrl), defaultGroupState(groupUrl));
  const state = {
    ...defaultGroupState(groupUrl),
    ...value,
    group_url: groupUrl,
    account_id: accountId,
  };
  groupStateCache.set(key, state);
  return state;
}

async function updateGroupState(groupUrl, patch) {
  const key = normalizeUrlKey(groupUrl);
  const current = await getGroupState(groupUrl);
  const state = {
    ...current,
    ...patch,
    account_id: accountId,
    group_url: groupUrl,
    updated_at: nowIso(),
  };
  groupStateCache.set(key, state);
  await writeKvJson(groupStateKey(groupUrl), state);
  return state;
}

function postCacheKey(groupUrl) {
  return kvKey('POST_CACHE', accountId, normalizeUrlKey(groupUrl), targetDate);
}

async function writePostCache(groupUrl, posts, meta = {}) {
  if (!Array.isArray(posts) || posts.length === 0) return;
  cacheWrites += 1;
  await writeKvJson(postCacheKey(groupUrl), {
    account_id: accountId,
    group_url: groupUrl,
    target_date: targetDate,
    cached_at: nowIso(),
    total_posts: posts.length,
    mode: meta.mode || '',
    posts,
  });
}

async function readPostCache(groupUrl) {
  const cache = await readKvJson(postCacheKey(groupUrl), null);
  if (!cache || !Array.isArray(cache.posts) || !cache.posts.length) return null;
  if (!isFreshIso(cache.cached_at, cacheTtlHours)) return null;
  cacheHits += 1;
  return cache;
}

function isSessionInCooldown() {
  return Boolean(sessionState.requires_relogin && isFutureIso(sessionState.cooldown_until));
}

function sessionCooldownUntil() {
  return new Date(Date.now() + sessionInvalidCooldownHours * 60 * 60 * 1000).toISOString();
}

async function crawlAttempt({ attemptMode, urls, useStorageState }) {
  const results = new Map();
  const startedAt = Date.now();
  let sessionChecked = false;
  let globalAuthRejected = false;
  let crawler;

  log.info(`ATTEMPT_START mode=${attemptMode} groups=${urls.length} useStorageState=${Boolean(useStorageState && storageState)}`);

  if (useStorageState && isSessionInCooldown()) {
    log.warning(`AUTH_ATTEMPT_SKIPPED accountId=${accountId} cooldownUntil=${sessionState.cooldown_until}`);
    for (const url of urls) {
      const result = {
        groupUrl: url,
        finalUrl: '',
        status: 'failed',
        errorType: 'SESSION_INVALID',
        mode: attemptMode,
        reachedGroup: false,
        authRequired: true,
        rawPostsCount: 0,
        postCandidateCount: 0,
        maxCandidateCount: 0,
        groupName: '',
        memberCount: 0,
        elapsedMs: 0,
        message: `Auth attempt skipped because this account is in cooldown until ${sessionState.cooldown_until}. Login manually and export a fresh storage state.`,
      };
      results.set(normalizeUrlKey(url), result);
      summary.attempts.push(result);
    }
    return results;
  }

  crawler = new PlaywrightCrawler({
    proxyConfiguration,
    maxConcurrency,
    maxRequestRetries,
    useSessionPool: true,
    persistCookiesPerSession: true,
    sessionPoolOptions: {
      maxPoolSize: 1,
      sessionOptions: {
        maxUsageCount: Math.max(urls.length + 2, 10),
      },
    },
    navigationTimeoutSecs: Math.ceil(navigationTimeoutMs / 1000),
    requestHandlerTimeoutSecs,
    launchContext: {
      launcher: chromium,
      useIncognitoPages: true,
      launchOptions: {
        headless: true,
        args: ['--disable-dev-shm-usage', '--no-sandbox', '--disable-setuid-sandbox'],
      },
    },
    browserPoolOptions: {
      useFingerprints: true,
      prePageCreateHooks: [
        async (_pageId, _browserController, pageOptions) => {
          if (!pageOptions) return;
          pageOptions.locale = 'en-US';
          pageOptions.timezoneId = 'Asia/Ho_Chi_Minh';
          pageOptions.viewport = { width: 1366, height: 768 };
          pageOptions.extraHTTPHeaders = {
            ...(pageOptions.extraHTTPHeaders ?? {}),
            'accept-language': 'en-US,en;q=0.9',
            'upgrade-insecure-requests': '1',
            dnt: '1',
          };
          if (useStorageState && storageState) {
            pageOptions.storageState = storageState;
          }
        },
      ],
    },
    preNavigationHooks: [
      async ({ page, request, session }, gotoOptions) => {
        if (!page.__blockedHeavyResources) {
          page.__blockedHeavyResources = true;
          await page.route('**/*', async (route) => {
            const type = route.request().resourceType();
            if (['image', 'media', 'font'].includes(type)) {
              await route.abort().catch(() => {});
              return;
            }
            await route.continue().catch(() => {});
          }).catch((error) => {
            log.warning(`RESOURCE_BLOCK_SETUP_FAILED message=${error?.message || error}`);
          });
        }
        gotoOptions.waitUntil = 'commit';
        gotoOptions.timeout = navigationTimeoutMs;
        log.info(`NAV_START mode=${attemptMode} group=${request.url} waitUntil=commit timeoutMs=${navigationTimeoutMs} retry=${request.retryCount || 0} sessionId=${session?.id || ''}`);

        if (!useStorageState || !storageState || sessionChecked) {
          return;
        }
        if (globalAuthRejected) {
          request.noRetry = true;
          throw new Error(`SESSION_INVALID_GLOBAL mode=${attemptMode} already rejected before opening group. finalUrl=${page.url()}`);
        }

        try {
          await gotoLinkedInSurface({
            page,
            session,
            url: 'https://www.linkedin.com/feed/',
            navTimeoutMs: navigationTimeoutMs,
            label: 'AUTH_CHECK',
          });
          await updateSessionState({
            status: 'healthy',
            health_score: 100,
            last_feed_check: nowIso(),
            requires_relogin: false,
            cooldown_until: null,
            last_error_type: null,
          });
        } catch (error) {
          globalAuthRejected = true;
          request.noRetry = true;
          await updateSessionState({
            status: 'invalid',
            health_score: 0,
            last_feed_check: nowIso(),
            fail_count: Number(sessionState.fail_count || 0) + 1,
            last_error_type: 'SESSION_INVALID_GLOBAL',
            requires_relogin: true,
            cooldown_until: sessionCooldownUntil(),
          });
          await safeSavePageDebug(page, 'session-invalid');
          throw new Error(`SESSION_INVALID_GLOBAL mode=${attemptMode} finalUrl=${page.url()} error=${error?.message || error}`);
        }
        sessionChecked = true;
      },
    ],
    errorHandler: async ({ request, session }, error) => {
      const errorType = classifyError(error, attemptMode);
      const transient = isTransientError(error, attemptMode);
      log.warning(`REQUEST_ERROR mode=${attemptMode} group=${request.url} errorType=${errorType} transient=${transient} retry=${request.retryCount || 0} message=${error?.message || error}`);
      if (!transient || ['AUTH_REQUIRED', 'SESSION_INVALID', 'GROUP_REDIRECTED', 'GROUP_NO_ACCESS'].includes(errorType)) {
        request.noRetry = true;
      }
      if (errorType === 'SESSION_INVALID') {
        session?.retire?.();
      }
    },
    requestHandler: async ({ page, request, session }) => {
      const groupUrl = request.url;
      const requestIndex = request.userData?.index || 0;
      const requestStart = Date.now();

      if (requestIndex > 0 && groupDelayMaxMs > 0) {
        const waitMs = randomDelay(groupDelayMinMs, groupDelayMaxMs);
        log.info(`GROUP_WAIT mode=${attemptMode} seconds=${Math.round(waitMs / 1000)} groupIndex=${requestIndex + 1}/${urls.length}`);
        await page.waitForTimeout(waitMs);
      }

      await page.waitForTimeout(3000);
      const navTitle = await getPageTitle(page);
      log.info('NAV_DONE', {
        mode: attemptMode,
        requestedUrl: groupUrl,
        finalUrl: page.url(),
        title: navTitle,
        sessionId: session?.id || '',
        retryCount: request.retryCount || 0,
      });

      await ensureGroupSurface(page, {
        groupUrl,
        request,
        session,
        attemptMode,
        navTimeoutMs: navigationTimeoutMs,
      });

      let maxCandidateCount = 0;
      let lastCandidateCount = 0;
      let stableRounds = 0;
      for (let i = 0; i < scrollTimes; i += 1) {
        await assertNoAuthChallenge(page, {
          request,
          session,
          attemptMode,
          retireSession: false,
        });
        const locator = await findPostLocator(page);
        const count = await locator.count().catch(() => 0);
        maxCandidateCount = Math.max(maxCandidateCount, count);
        log.info(`SCROLL_STATE mode=${attemptMode} group=${groupUrl} scroll=${i + 1}/${scrollTimes} candidates=${count} finalUrl=${page.url()}`);
        if (count >= maxItems) break;
        if (count === lastCandidateCount) stableRounds += 1;
        else stableRounds = 0;
        if (stableRounds >= 2) break;
        lastCandidateCount = count;
        await page.mouse.wheel(0, 2500);
        await page.waitForTimeout(randomDelay(Math.min(delayMinMs, 2500), Math.min(delayMaxMs, 5000)));
      }

      await page.waitForTimeout(1000);
      await assertNoAuthChallenge(page, {
        request,
        session,
        attemptMode,
        retireSession: false,
      });

      const finalLocator = await findPostLocator(page);
      const postCandidateCount = await finalLocator.count().catch(() => 0);
      maxCandidateCount = Math.max(maxCandidateCount, postCandidateCount);
      const groupName = await extractGroupName(page);
      const memberCount = await extractMemberCount(page);

      if (postCandidateCount === 0) {
        const accessIssue = await detectGroupAccessIssue(page);
        if (accessIssue) {
          await safeSavePageDebug(page, `${attemptMode}-group-no-access`);
          request.noRetry = true;
          throw new Error(`GROUP_NO_ACCESS mode=${attemptMode} reason=${accessIssue} finalUrl=${page.url()}`);
        }
        await safeSavePageDebug(page, `${attemptMode}-no-post-candidates`);
        request.noRetry = true;
        throw new Error(`EMPTY_UNVERIFIED_RESULT mode=${attemptMode} No post candidates after navigation. maxCandidateCount=${maxCandidateCount} finalUrl=${page.url()}`);
      }

      const posts = await withTimeout(
        parsePosts(page, { groupUrl, maxItems }),
        45000,
        `Parsing posts timed out after 45 seconds. mode=${attemptMode} finalUrl=${page.url()}`,
      );
      if (posts.length === 0) {
        await safeSavePageDebug(page, `${attemptMode}-no-posts`);
        request.noRetry = true;
        throw new Error(`EMPTY_UNVERIFIED_RESULT mode=${attemptMode} No posts parsed although ${postCandidateCount} candidates were found. finalUrl=${page.url()}`);
      }

      const outputPosts = posts.map((post) => toOutputPost(post, {
        sessionId: actorSessionId,
        emailCrawl: actorEmailCrawl,
        groupUrl,
        groupName: posts[0]?.group_name || posts[0]?.groupName || groupName || '',
        totalPosts: posts.length,
        mode: attemptMode,
      }));

      await withTimeout(
        Actor.pushData(outputPosts),
        30000,
        `Saving posts timed out after 30 seconds. mode=${attemptMode} finalUrl=${page.url()}`,
      );
      await writePostCache(groupUrl, outputPosts, { mode: attemptMode });
      if (attemptMode === 'auth') {
        await updateSessionState({
          status: 'healthy',
          health_score: 100,
          fail_count: 0,
          last_success_at: nowIso(),
          last_error_type: null,
          requires_relogin: false,
          cooldown_until: null,
        });
      }
      await updateGroupState(groupUrl, {
        requires_auth: attemptMode === 'public' ? false : (await getGroupState(groupUrl)).requires_auth,
        last_public_status: attemptMode === 'public' ? 'success' : (await getGroupState(groupUrl)).last_public_status,
        last_auth_status: attemptMode === 'auth' ? 'success' : (await getGroupState(groupUrl)).last_auth_status,
        last_error_type: null,
        account_has_access: attemptMode === 'auth' ? true : (await getGroupState(groupUrl)).account_has_access,
        last_success_source: attemptMode,
        last_checked_at: nowIso(),
        last_success_at: nowIso(),
      });

      const result = {
        groupUrl,
        finalUrl: page.url(),
        status: 'success',
        errorType: null,
        mode: attemptMode,
        reachedGroup: true,
        authRequired: false,
        rawPostsCount: posts.length,
        postCandidateCount,
        maxCandidateCount,
        groupName: posts[0]?.group_name || posts[0]?.groupName || groupName || '',
        memberCount: posts[0]?.member_count || posts[0]?.memberCount || memberCount || 0,
        elapsedMs: Date.now() - requestStart,
        message: 'Crawl completed',
      };
      results.set(normalizeUrlKey(groupUrl), result);
      summary.attempts.push(result);
      log.info(`POSTS_PARSED mode=${attemptMode} group=${groupUrl} candidates=${postCandidateCount} parsed=${posts.length} elapsedMs=${result.elapsedMs}`);
    },
    failedRequestHandler: async ({ request }, error) => {
      const errorType = classifyError(error, attemptMode);
      const message = error?.message || String(error);
      const isGlobalSessionInvalid = useStorageState && errorType === 'SESSION_INVALID' && /SESSION_INVALID_GLOBAL/i.test(message);
      if (useStorageState && errorType === 'SESSION_INVALID' && /SESSION_INVALID_GLOBAL/i.test(message)) {
        globalAuthRejected = true;
        await updateSessionState({
          status: 'invalid',
          health_score: 0,
          fail_count: Number(sessionState.fail_count || 0) + 1,
          last_error_type: 'SESSION_INVALID_GLOBAL',
          requires_relogin: true,
          cooldown_until: sessionCooldownUntil(),
        });
        await crawler.autoscaledPool?.abort();
      }
      const previousGroupState = await getGroupState(request.url);
      if (attemptMode === 'public' && errorType === 'AUTH_REQUIRED') {
        await updateGroupState(request.url, {
          requires_auth: true,
          last_public_status: 'auth_required',
          last_error_type: errorType,
          last_checked_at: nowIso(),
        });
      } else if (attemptMode === 'auth') {
        await updateGroupState(request.url, {
          requires_auth: previousGroupState.requires_auth,
          last_auth_status: statusFromErrorType(errorType, message),
          last_error_type: errorType,
          account_has_access: ['GROUP_NO_ACCESS', 'AUTH_REQUIRED', 'GROUP_REDIRECTED'].includes(errorType) ? false : previousGroupState.account_has_access,
          last_checked_at: nowIso(),
        });
      } else {
        await updateGroupState(request.url, {
          last_public_status: statusFromErrorType(errorType, message),
          last_error_type: errorType,
          last_checked_at: nowIso(),
        });
      }

      let cachedResult = null;
      if (allowCacheFallback && !isGlobalSessionInvalid) {
        const cache = await readPostCache(request.url);
        if (cache) {
          const cachedPosts = cache.posts.map((post) => ({
            ...post,
            crawl_status: 'cache',
            cache_age_seconds: Math.round((Date.now() - Date.parse(cache.cached_at)) / 1000),
          }));
          await Actor.pushData(cachedPosts);
          cachedResult = {
            groupUrl: request.url,
            finalUrl: '',
            status: 'success',
            errorType: null,
            mode: `${attemptMode}_cache`,
            reachedGroup: false,
            authRequired: false,
            rawPostsCount: cachedPosts.length,
            postCandidateCount: 0,
            maxCandidateCount: 0,
            groupName: cachedPosts[0]?.group_name || '',
            memberCount: Number(cachedPosts[0]?.members || cachedPosts[0]?.member_count || 0),
            elapsedMs: 0,
            message: `Returned cache because fresh crawl failed with ${errorType}.`,
          };
        }
      }
      if (cachedResult) {
        results.set(normalizeUrlKey(request.url), cachedResult);
        summary.attempts.push(cachedResult);
        log.warning(`REQUEST_CACHE_FALLBACK mode=${attemptMode} group=${request.url} originalErrorType=${errorType}`);
        return;
      }

      const result = {
        groupUrl: request.url,
        finalUrl: '',
        status: statusFromErrorType(errorType, message),
        errorType,
        mode: attemptMode,
        reachedGroup: !['AUTH_REQUIRED', 'SESSION_INVALID', 'GROUP_REDIRECTED'].includes(errorType),
        authRequired: ['AUTH_REQUIRED', 'SESSION_INVALID'].includes(errorType),
        rawPostsCount: 0,
        postCandidateCount: 0,
        maxCandidateCount: 0,
        groupName: '',
        memberCount: 0,
        elapsedMs: 0,
        message,
      };
      results.set(normalizeUrlKey(request.url), result);
      summary.attempts.push(result);
      log.error(`REQUEST_FAILED mode=${attemptMode} group=${request.url} errorType=${errorType} message=${error?.message || error}`);
    },
  });

  await crawler.run(urls.map((url, index) => ({
    url,
    uniqueKey: `${attemptMode}:${normalizeUrlKey(url)}`,
    userData: { index, attemptMode },
  })));

  for (const url of urls) {
    const key = normalizeUrlKey(url);
    if (!results.has(key)) {
      const result = {
        groupUrl: url,
        finalUrl: '',
        status: 'failed',
        errorType: globalAuthRejected ? 'SESSION_INVALID' : 'CRAWL_ERROR',
        mode: attemptMode,
        reachedGroup: false,
        authRequired: globalAuthRejected,
        rawPostsCount: 0,
        postCandidateCount: 0,
        maxCandidateCount: 0,
        groupName: '',
        memberCount: 0,
        elapsedMs: 0,
        message: globalAuthRejected ? 'Auth attempt stopped because LinkedIn rejected the session.' : 'Attempt ended without a result.',
      };
      results.set(key, result);
      summary.attempts.push(result);
    }
  }

  log.info(`ATTEMPT_DONE mode=${attemptMode} groups=${urls.length} elapsedMs=${Date.now() - startedAt}`);
  return results;
}

// ─── API pagination mode ─────────────────────────────────────────────────────
/**
 * Run the GraphQL API crawler for all group URLs.
 * This replicates the behavior from the run log:
 *   iteration 1/6 → page-done received=36 new=36 …
 *   finished successfulPages=6 failedPages=0 items=203
 *
 * FIX (bug #1): after processing each group, we call finalResults.set() so
 * the summary block at the bottom of main() reads the correct data.
 * We do NOT modify summary.succeeded/failed/groups.push() here — the bottom
 * block does that by iterating finalResults.
 */
async function crawlGroupsByApi(urls) {
  const cookies = storageState?.cookies || [];

  if (cookies.length === 0) {
    log.warning(
      '[api-mode] No cookies found in storageState. '
      + 'LinkedIn API requires authentication. '
      + 'Provide storageStateJson or sessionCookie.',
    );
  }

  for (const groupUrl of urls) {
    const groupId = extractGroupId(groupUrl);
    log.info(`[api-mode] Starting group groupId=${groupId} url=${groupUrl}`);

    const result = await crawlGroupByApi({
      groupUrl,
      cookies,
      maxIterations: iterations,
      maxItems,
      pageSize: apiPageSize,
      apiStart,
      liPageInstance,
      continueWithoutPaginationToken,
      pageDelayMinMs,
      pageDelayMaxMs,
      fetchTimeoutMs: apiFetchTimeoutMs,
      maxRetries: apiMaxRetries,
      maxConsecutiveFailedPages: apiMaxConsecutiveFailedPages,
    });

    const isSuccess = result.posts.length > 0 && result.status === 'success';
    const isPartial = result.posts.length > 0 && result.status !== 'success';
    const finalStatus = isSuccess ? 'success' : isPartial ? 'partial' : result.status;

    if (isSuccess) {
      // Map API posts to the standard output format
      const outputPosts = result.posts.map((post) => ({
        // Standard fields expected by the backend
        id_session_crawl: actorSessionId,
        email_crawl: actorEmailCrawl,
        day_returned: new Date().toISOString(),
        url_groups: post.group_url || groupUrl,
        url_article: post.url,
        author: post.author || '',
        content: post.content || '',
        likes: Number(post.likes || 0),
        comments: Number(post.comments || 0),
        repost: Number(post.share || 0),
        score: Number(post.likes || 0) + Number(post.comments || 0) * 3 + Number(post.share || 0) * 5,
        // FIX bug #3: day_up uses datetime (ISO) first, text_date as fallback
        day_up: post.day_up || post.datetime || post.text_date || '',
        members: 0,
        total_number_of_articles_obtained_each_time: result.posts.length,
        // FIX bug #4: group_name = '' in v1 (acceptable), set from state cache if available
        group_name: '',
        member_count: 0,
        crawl_mode: 'api_pagination',
        crawl_status: result.status,
        // API-specific fields
        type: post.type || 'group',
        url: post.url,
        timestamp: post.timestamp || '',
        text_date: post.text_date || '',
        datetime: post.datetime || null,
        tipo_post: post.tipo_post || 'Other',
        views: post.views || '',
        share: Number(post.share || 0),
        // Backward-compatible aliases
        post_url: post.url,
        group_url: post.group_url || groupUrl,
        reposts: Number(post.share || 0),
        posted_at: post.datetime || '',
        posted_at_raw: post.text_date || '',
      }));

      await Actor.pushData(outputPosts);
      log.info(
        `[api-mode] Pushed ${outputPosts.length} posts for groupId=${groupId} `
        + `successfulPages=${result.successfulPages} failedPages=${result.failedPages}`,
      );
    } else {
      log.error(
        `[api-mode] No posts for groupId=${groupId} status=${result.status} msg=${result.message}`,
      );
    }

    // FIX (bug #1): Set finalResults so the summary block at the bottom
    // of main() correctly computes succeeded/failed/groups/errors.
    finalResults.set(normalizeUrlKey(groupUrl), {
      groupUrl,
      status: finalStatus,
      errorType: isSuccess ? null : result.status.toUpperCase(),
      reachedGroup: result.posts.length > 0,
      authRequired: result.status === 'auth_required',
      mode: 'api_pagination',
      rawPostsCount: result.posts.length,
      postCandidateCount: result.posts.length,
      successfulPages: result.successfulPages,
      failedPages: result.failedPages,
      message: result.message,
      groupName: '',
      memberCount: 0,
    });
  }
}

const finalResults = new Map();

// ─── Route to correct crawl strategy ────────────────────────────────────────
if (apiMode) {
  // ── NEW: API pagination (fast, no browser) ─────────────────────────────
  log.info(`[api-mode] Using API pagination mode: iterations=${iterations} groupsCount=${groupUrls.length}`);
  await crawlGroupsByApi(groupUrls);
} else if (mode === 'public') {
  for (const url of groupUrls) {
    const result = await crawlPublicFast(url);
    finalResults.set(normalizeUrlKey(url), result);
    summary.attempts.push(result);
    if (result.status !== 'success') {
      await updateGroupState(url, {
        requires_auth: result.errorType === 'AUTH_REQUIRED',
        last_public_status: result.status,
        last_error_type: result.errorType,
        last_checked_at: nowIso(),
      });
    }
  }
} else if (mode === 'auth') {
  if (!storageState) {
    for (const url of groupUrls) {
      finalResults.set(normalizeUrlKey(url), {
        groupUrl: url,
        finalUrl: '',
        status: 'failed',
        errorType: 'AUTH_REQUIRED',
        mode: 'auth',
        reachedGroup: false,
        authRequired: true,
        rawPostsCount: 0,
        postCandidateCount: 0,
        maxCandidateCount: 0,
        groupName: '',
        memberCount: 0,
        elapsedMs: 0,
        message: 'Auth mode requires storageStateJson or sessionCookie.',
      });
    }
  } else {
    const authResults = await crawlAttempt({ attemptMode: 'auth', urls: groupUrls, useStorageState: true });
    for (const [key, result] of authResults.entries()) finalResults.set(key, result);
  }
} else {
  const fastPublicUrls = [];
  const authFallbackUrls = [];
  for (const url of groupUrls) {
    const groupState = await getGroupState(url);
    if (groupState.requires_auth && isFreshIso(groupState.last_checked_at, groupStateTtlHours)) {
      const skipped = {
        groupUrl: url,
        finalUrl: '',
        status: 'auth_required',
        errorType: 'AUTH_REQUIRED',
        mode: 'public',
        reachedGroup: false,
        authRequired: true,
        rawPostsCount: 0,
        postCandidateCount: 0,
        maxCandidateCount: 0,
        groupName: '',
        memberCount: 0,
        elapsedMs: 0,
        message: `Skipped public mode because recent group state requires auth. lastCheckedAt=${groupState.last_checked_at}`,
      };
      summary.attempts.push(skipped);
      authFallbackUrls.push(url);
    } else {
      fastPublicUrls.push(url);
    }
  }

  const publicResults = new Map();
  for (const url of fastPublicUrls) {
    const result = await crawlPublicFast(url);
    publicResults.set(normalizeUrlKey(url), result);
    summary.attempts.push(result);
    if (result.status === 'success') {
      finalResults.set(normalizeUrlKey(url), result);
    } else {
      await updateGroupState(url, {
        requires_auth: result.errorType === 'AUTH_REQUIRED',
        last_public_status: result.status,
        last_error_type: result.errorType,
        last_checked_at: nowIso(),
      });
      authFallbackUrls.push(url);
    }
  }

  if (authFallbackUrls.length && storageState) {
    log.info(`AUTO_FALLBACK_TO_AUTH groups=${authFallbackUrls.length}`);
    const authResults = await crawlAttempt({ attemptMode: 'auth', urls: authFallbackUrls, useStorageState: true });
    for (const url of authFallbackUrls) {
      const key = normalizeUrlKey(url);
      const authResult = authResults.get(key);
      finalResults.set(key, authResult || publicResults.get(key));
    }
  } else {
    for (const url of authFallbackUrls) {
      const key = normalizeUrlKey(url);
      const publicResult = publicResults.get(key);
      finalResults.set(key, {
        ...(publicResult || {
          groupUrl: url,
          finalUrl: '',
          status: 'failed',
          errorType: 'AUTH_REQUIRED',
          reachedGroup: false,
          authRequired: true,
          rawPostsCount: 0,
          postCandidateCount: 0,
          maxCandidateCount: 0,
          groupName: '',
          memberCount: 0,
          elapsedMs: 0,
          message: 'Public mode did not return posts and no storageStateJson was provided for auth fallback.',
        }),
        mode: publicResult?.mode || 'public_fast',
      });
    }
  }
} // end of mode routing

summary.groups = groupUrls
  .map((url) => finalResults.get(normalizeUrlKey(url)))
  .filter(Boolean);
summary.succeeded = summary.groups.filter((group) => group.status === 'success').length;
summary.failed = summary.groups.length - summary.succeeded;
summary.errors = summary.groups
  .filter((group) => group.status !== 'success')
  .map((group) => ({
    url: group.groupUrl,
    errorType: group.errorType,
    error: group.message,
    mode: group.mode,
  }));

summary.sessionState = {
  ...sessionState,
  storage_state_fingerprint: sessionState.storage_state_fingerprint ? `${sessionState.storage_state_fingerprint.slice(0, 8)}...` : '',
};
summary.groupStates = await Promise.all(groupUrls.map((url) => getGroupState(url)));
summary.cache = {
  allowCacheFallback,
  cacheTtlHours,
  writes: cacheWrites,
  hits: cacheHits,
};
summary.finishedAt = new Date().toISOString();
await Actor.setValue('SUMMARY', summary);
await Actor.exit();
