import { Actor } from 'apify';
import { PlaywrightCrawler, log } from 'crawlee';
import { chromium } from 'playwright';

import { extractGroupName, extractMemberCount, isAuthPage, parsePosts, findPostLocator } from './linkedinParser.js';

function toInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function randomDelay(minMs, maxMs) {
  const min = Math.min(minMs, maxMs);
  const max = Math.max(minMs, maxMs);
  return Math.floor(min + Math.random() * (max - min + 1));
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

function classifyError(error) {
  const message = String(error?.message || error || '');
  if (/session invalid|uas\/login|final url:\s*\/uas\/login/i.test(message)) {
    return 'SESSION_INVALID';
  }
  if (/final url:\s*https:\/\/www\.linkedin\.com\/?\s*$/i.test(message) || /final url:\s*\/\s*$/i.test(message)) {
    return 'GROUP_REDIRECTED';
  }
  if (/login|checkpoint|authwall|auth required|requires login/i.test(message)) {
    return 'AUTH_REQUIRED';
  }
  if (/timeout|timed out/i.test(message)) return 'TIMEOUT';
  if (/too many redirects|too_many_redirects/i.test(message)) return 'AUTH_REQUIRED';
  if (/no post candidates|no posts parsed|empty unverified/i.test(message)) return 'EMPTY_UNVERIFIED_RESULT';
  if (/navigating and changing the content/i.test(message)) return 'PAGE_NAVIGATING';
  return 'CRAWL_ERROR';
}

async function safeSavePageDebug(page, prefix) {
  const stamp = Date.now();
  try {
    await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => {});
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

await Actor.init();

const input = await Actor.getInput() ?? {};
const groupUrls = Array.isArray(input.groupUrls)
  ? input.groupUrls.map((url) => String(url).trim()).filter(Boolean)
  : [];

if (groupUrls.length === 0) {
  throw new Error('Input groupUrls must contain at least one LinkedIn group URL.');
}

const maxItems = Math.min(Math.max(toInt(input.maxItems, 20), 1), 20);
const scrollTimes = Math.min(Math.max(toInt(input.scrollTimes, 3), 1), 3);
const delayMinMs = Math.min(Math.max(toInt(input.delayMinMs, 5000), 5000), 120000);
const delayMaxMs = Math.min(Math.max(toInt(input.delayMaxMs, 12000), delayMinMs), 120000);
const groupDelayMinMs = Math.min(Math.max(toInt(input.groupDelayMinMs, 300000), 0), 1800000);
const groupDelayMaxMs = Math.min(Math.max(toInt(input.groupDelayMaxMs, 600000), groupDelayMinMs), 1800000);
const maxConcurrency = 1;
const maxRequestRetries = 0;
const storageState = buildStorageState(input);
const proxyConfiguration = await Actor.createProxyConfiguration(input.proxyConfiguration);

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
  totalGroups: groupUrls.length,
  succeeded: 0,
  failed: 0,
  proxyConfiguration: input.proxyConfiguration || null,
  stickyProxySession: true,
  groups: [],
  errors: [],
};

let sessionChecked = false;
let sessionRejected = false;

const crawler = new PlaywrightCrawler({
  proxyConfiguration,
  maxConcurrency,
  maxRequestRetries,
  useSessionPool: true,
  persistCookiesPerSession: true,
  sessionPoolOptions: {
    maxPoolSize: 1,
    sessionOptions: {
      maxUsageCount: Math.max(groupUrls.length + 2, 10),
    },
  },
  navigationTimeoutSecs: 120,
  requestHandlerTimeoutSecs: 120,
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
        if (storageState && pageOptions) {
          pageOptions.storageState = storageState;
        }
      },
    ],
  },
  preNavigationHooks: [
    async ({ page, request }, gotoOptions) => {
      gotoOptions.waitUntil = 'domcontentloaded';
      gotoOptions.timeout = 120000;

      if (!storageState || sessionChecked) {
        return;
      }
      if (sessionRejected) {
        throw new Error(`LinkedIn session already rejected on this Actor run before opening group. Final URL: ${page.url()}`);
      }

      log.info(`Checking LinkedIn session before group ${request.url}`);
      try {
        await page.goto('https://www.linkedin.com/feed/', {
          waitUntil: 'domcontentloaded',
          timeout: 120000,
        });
      } catch (error) {
        const message = error?.message || String(error);
        if (/too many redirects|too_many_redirects|login|checkpoint|authwall/i.test(message)) {
          throw new Error(
            `LinkedIn session invalid before opening group. Final URL: ${page.url()}. Navigation error: ${message}`,
          );
        }
        throw error;
      }
      await page.waitForTimeout(5000);

      if (await isAuthPage(page)) {
        sessionRejected = true;
        await safeSavePageDebug(page, 'session-invalid');
        throw new Error(`LinkedIn session invalid before opening group. Final URL: ${page.url()}`);
      }
      sessionChecked = true;
    },
  ],
  requestHandler: async ({ page, request }) => {
    const groupUrl = request.url;
    const requestIndex = request.userData?.index || 0;
    if (requestIndex > 0 && groupDelayMaxMs > 0) {
      const waitMs = randomDelay(groupDelayMinMs, groupDelayMaxMs);
      log.info(`Waiting ${Math.round(waitMs / 1000)}s before group ${requestIndex + 1}/${groupUrls.length}`);
      await page.waitForTimeout(waitMs);
    }

    log.info(`Opening group ${groupUrl}`);
    await page.waitForTimeout(5000);

    if (await isAuthPage(page)) {
      sessionRejected = true;
      await safeSavePageDebug(page, 'auth-wall');
      throw new Error(`LinkedIn requires login/checkpoint. Final URL: ${page.url()}`);
    }

    for (let i = 0; i < scrollTimes; i += 1) {
      await page.mouse.wheel(0, 3000);
      await page.waitForTimeout(randomDelay(delayMinMs, delayMaxMs));
      const locator = await findPostLocator(page);
      const count = await locator.count().catch(() => 0);
      log.info(`Scroll ${i + 1}/${scrollTimes}: ${count} post candidates`);
      if (count >= maxItems) break;
    }

    await page.waitForTimeout(1500);
    const finalLocator = await findPostLocator(page);
    const postCandidateCount = await finalLocator.count().catch(() => 0);
    const groupName = await extractGroupName(page);
    const memberCount = await extractMemberCount(page);
    if (postCandidateCount === 0) {
      if (/^https:\/\/www\.linkedin\.com\/?$/.test(page.url()) || /\/login|\/uas\/login|\/checkpoint|\/authwall/i.test(page.url())) {
        sessionRejected = true;
      }
      await safeSavePageDebug(page, 'no-post-candidates');
      throw new Error(`No post candidates found after reaching group. Final URL: ${page.url()}`);
    }

    const posts = await parsePosts(page, { groupUrl, maxItems });
    if (posts.length === 0) {
      await safeSavePageDebug(page, 'no-posts');
      throw new Error(`No posts parsed although ${postCandidateCount} candidates were found. Final URL: ${page.url()}`);
    }
    await Actor.pushData(posts);
    summary.groups.push({
      groupUrl,
      finalUrl: page.url(),
      status: 'success',
      errorType: null,
      reachedGroup: true,
      authRequired: false,
      rawPostsCount: posts.length,
      postCandidateCount,
      groupName: posts[0]?.group_name || posts[0]?.groupName || groupName || '',
      memberCount: posts[0]?.member_count || posts[0]?.memberCount || memberCount || 0,
      message: posts.length > 0 ? 'Crawl completed' : 'Crawl completed but no posts were found',
    });
    summary.succeeded += 1;
  },
  failedRequestHandler: async ({ request }, error) => {
    const errorType = classifyError(error);
    if (['AUTH_REQUIRED', 'SESSION_INVALID', 'GROUP_REDIRECTED'].includes(errorType)) {
      sessionRejected = true;
      await crawler.autoscaledPool?.abort();
    }
    summary.failed += 1;
    summary.groups.push({
      groupUrl: request.url,
      finalUrl: '',
      status: 'failed',
      errorType,
      reachedGroup: false,
      authRequired: ['AUTH_REQUIRED', 'SESSION_INVALID', 'GROUP_REDIRECTED'].includes(errorType),
      rawPostsCount: 0,
      postCandidateCount: 0,
      groupName: '',
      memberCount: 0,
      message: error?.message || String(error),
    });
    summary.errors.push({ url: request.url, errorType, error: error?.message || String(error) });
    log.error(`Failed group ${request.url}: ${error?.message || error}`);
  },
});

await crawler.run(groupUrls.map((url, index) => ({ url, userData: { index } })));

summary.finishedAt = new Date().toISOString();
await Actor.setValue('SUMMARY', summary);
await Actor.exit();
