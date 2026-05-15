import { normalizePost, parseCount } from './normalizePost.js';

const POST_SELECTORS = [
  'div[data-id^="urn:li:activity"]',
  'article[data-urn*="urn:li:activity"]',
  'article.feed-shared-update-v2',
  'div.feed-shared-update-v2',
  'div.occludable-update',
];

const GROUP_NAME_SELECTORS = [
  'h1',
  '[data-test-id*="group-name"]',
  '[data-test-id*="groups-name"]',
  '.groups-hero__main-title',
];

export function isAuthWall(url) {
  const parsed = new URL(url);
  return ['/login', '/checkpoint', '/authwall'].some((prefix) => parsed.pathname.toLowerCase().startsWith(prefix));
}

export async function isAuthPage(page) {
  if (isAuthWall(page.url())) return true;
  const title = (await page.title().catch(() => '')).trim().toLowerCase();
  if (title.includes('sign in') || title.includes('login') || title.includes('welcome back')) return true;
  const authInputs = await page
    .locator('input[name="session_key"], input#username, input[name="session_password"], input#password')
    .count()
    .catch(() => 0);
  return authInputs > 0;
}

export async function extractGroupName(page) {
  for (const selector of GROUP_NAME_SELECTORS) {
    const locator = page.locator(selector).first();
    if (await locator.count().catch(() => 0)) {
      const text = (await locator.innerText({ timeout: 2000 }).catch(() => '')).trim();
      if (text && !text.toLowerCase().includes('sign in')) return text;
    }
  }
  const title = (await page.title().catch(() => '')).trim();
  return title ? title.split('|')[0].trim() : '';
}

export async function extractMemberCount(page) {
  const bodyText = await page.evaluate(() => document.body.innerText).catch(() => '');
  const match = bodyText.match(/([\d,.]+)\s*(members|thành viên)/i);
  return match ? parseCount(match[1]) : 0;
}

export async function findPostLocator(page) {
  for (const selector of POST_SELECTORS) {
    const locator = page.locator(selector);
    const count = await locator.count().catch(() => 0);
    if (count > 0) return locator;
  }
  return page.locator(POST_SELECTORS[0]);
}

function metricFromText(text, labels) {
  const normalized = text.replace(/\s+/g, ' ').trim().toLowerCase();
  for (const label of labels) {
    const re = new RegExp(`([\\d,.]+\\s*[km]?)\\s+${label}\\b`, 'i');
    const match = normalized.match(re);
    if (match) return match[1];
  }
  return '';
}

function metricFromAccessibleText(text, labels) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
  for (const label of labels) {
    const before = normalized.match(new RegExp(`([\\d,.]+\\s*[km]?)\\s+${label}\\b`, 'i'));
    if (before) return before[1];
    const after = normalized.match(new RegExp(`${label}\\D+([\\d,.]+\\s*[km]?)`, 'i'));
    if (after) return after[1];
  }
  return '';
}

async function extractPostUrl(item) {
  const hrefs = await item.locator('a[href]').evaluateAll((nodes) =>
    nodes
      .map((node) => node.href || node.getAttribute('href') || '')
      .filter(Boolean),
  ).catch(() => []);

  const preferred = hrefs.find((href) =>
    /\/feed\/update\/|urn:li:activity|activity-\d+|\/posts\//i.test(href),
  );
  if (preferred) return preferred;

  const dataId = await item.getAttribute('data-id').catch(() => '');
  const urnMatch = String(dataId || '').match(/urn:li:activity:\d+/);
  if (urnMatch) {
    return `https://www.linkedin.com/feed/update/${urnMatch[0]}/`;
  }
  return '';
}

async function extractMetrics(item, text) {
  const accessible = await item
    .locator('[aria-label]')
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('aria-label') || '').join('\n'))
    .catch(() => '');
  const combined = `${accessible}\n${text}`;
  return {
    likes:
      metricFromAccessibleText(combined, ['reaction', 'reactions'])
      || metricFromAccessibleText(combined, ['like', 'likes']),
    comments: metricFromAccessibleText(combined, ['comment', 'comments']),
    reposts: metricFromAccessibleText(combined, ['repost', 'reposts', 'share', 'shares']),
  };
}

function extractPostedAtRaw(text) {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);

  const patterns = [
    /\b(now|just now)\b/i,
    /\b(\d+\s*(?:m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks|mo|mos|month|months|yr|yrs|year|years))\s+ago\b/i,
    /\b(\d+\s*(?:m|min|mins|h|hr|hrs|d|w|wk|wks|mo|mos|yr|yrs))\b/i,
  ];

  for (const line of lines) {
    if (/[#$€£]\s*\d/i.test(line)) continue;
    for (const pattern of patterns) {
      const match = line.match(pattern);
      if (match) return match[1];
    }
  }

  return '';
}

export async function parsePosts(page, { groupUrl, maxItems }) {
  const groupName = await extractGroupName(page);
  const memberCount = await extractMemberCount(page);
  const locator = await findPostLocator(page);
  const total = Math.min(await locator.count().catch(() => 0), maxItems);
  const posts = [];

  for (let index = 0; index < total; index += 1) {
    const item = locator.nth(index);
    const text = (await item.innerText({ timeout: 5000 }).catch(() => '')).trim();
    if (!text) continue;

    const author = text.split('\n').map((line) => line.trim()).find(Boolean) || '';
    const postUrl = await extractPostUrl(item);
    const metrics = await extractMetrics(item, text);
    const raw = {
      author,
      content: text,
      likes: metrics.likes || metricFromText(text, ['reaction', 'reactions', 'like', 'likes']),
      comments: metrics.comments || metricFromText(text, ['comment', 'comments']),
      reposts: metrics.reposts || metricFromText(text, ['repost', 'reposts', 'share', 'shares']),
      postUrl,
      postedAtRaw: extractPostedAtRaw(text),
    };
    posts.push(normalizePost(raw, groupUrl, groupName, memberCount));
  }

  return posts;
}
