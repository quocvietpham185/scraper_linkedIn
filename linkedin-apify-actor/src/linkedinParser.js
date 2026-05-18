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

const AUTHOR_SELECTORS = [
  '.update-components-actor__name span[aria-hidden="true"]',
  '.feed-shared-actor__name',
  'a[href*="/in/"] span[aria-hidden="true"]',
];

const CONTENT_SELECTORS = [
  '.update-components-text',
  '.feed-shared-text',
  '[data-test-id="main-feed-activity-card__commentary"]',
];

const TIME_SELECTORS = [
  ".update-components-actor__sub-description span[aria-hidden='true']",
  '.feed-shared-actor__sub-description',
  'span[aria-label*="ago"]',
];

const REACTION_SELECTORS = [
  '.social-details-social-counts__reactions-count',
  'button[aria-label*="reaction"]',
  'span[aria-label*="reaction"]',
];

const COMMENT_SELECTORS = [
  'button[aria-label*="comment"]',
  '.social-details-social-counts__comments button',
  'span[aria-label*="comment"]',
];

const REPOST_SELECTORS = [
  'button[aria-label*="repost"]',
  'button[aria-label*="share"]',
  'span[aria-label*="repost"]',
  'span[aria-label*="share"]',
];

const LINK_SELECTORS = [
  'a[href*="/feed/update/"]',
  'a[href*="/posts/"]',
  'a[href*="/activity-"]',
  'a[aria-label="Original Post"]',
];

const RELATIVE_TIME_PATTERN =
  /\b(?:just\s+now|now|\d+\s*(?:mo|mos|month|months|yr|yrs|year|years|w|wk|wks|week|weeks|d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes))\b/i;

export function isAuthWall(url) {
  const parsed = new URL(url);
  return ['/login', '/uas/login', '/checkpoint', '/authwall'].some((prefix) => parsed.pathname.toLowerCase().startsWith(prefix));
}

export async function isAuthPage(page) {
  if (isAuthWall(page.url())) return true;
  const title = (await page.title().catch(() => '')).trim().toLowerCase();
  if (
    title.includes('sign in')
    || title.includes('login')
    || title.includes('welcome back')
    || title.includes('checkpoint')
    || title.includes('security verification')
  ) return true;
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
  const match = bodyText.match(/([\d,.]+)\s*(members|thành viên|thanh vien)/i);
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

async function safeText(item, selectors, timeout = 2000) {
  for (const selector of selectors) {
    const locator = item.locator(selector).first();
    const count = await locator.count().catch(() => 0);
    if (!count) continue;
    const text = (await locator.innerText({ timeout }).catch(() => '')).trim();
    if (text) return text;
  }
  return '';
}

async function safeAttribute(item, selectors, attribute, timeout = 2000) {
  for (const selector of selectors) {
    const locator = item.locator(selector).first();
    const count = await locator.count().catch(() => 0);
    if (!count) continue;
    const value = (await locator.getAttribute(attribute, { timeout }).catch(() => '') || '').trim();
    if (value) return value;
  }
  return '';
}

function normalizeLinkedInUrl(url) {
  const cleaned = String(url || '').trim();
  if (!cleaned) return '';
  if (/^https?:\/\//i.test(cleaned)) return cleaned;
  return new URL(cleaned, 'https://www.linkedin.com').toString();
}

function activityUrnToUrl(value) {
  const match = String(value || '').match(/urn:li:activity:\d+/);
  return match ? `https://www.linkedin.com/feed/update/${match[0]}/` : '';
}

async function extractPostUrl(item) {
  const ownDataUrn = await item.getAttribute('data-urn').catch(() => '');
  const ownDataId = await item.getAttribute('data-id').catch(() => '');
  for (const value of [ownDataUrn, ownDataId]) {
    const url = activityUrnToUrl(value);
    if (url) return url;
  }

  const childDataUrn = await item
    .locator('[role="article"][data-urn], [data-urn^="urn:li:activity:"]')
    .first()
    .getAttribute('data-urn')
    .catch(() => '');
  const childUrl = activityUrnToUrl(childDataUrn);
  if (childUrl) return childUrl;

  const directUrl = await safeAttribute(item, LINK_SELECTORS, 'href');
  if (directUrl) return normalizeLinkedInUrl(directUrl);

  const hrefs = await item.locator('a[href]').evaluateAll((nodes) =>
    nodes
      .map((node) => node.getAttribute('href') || node.href || '')
      .filter(Boolean),
  ).catch(() => []);

  for (const href of hrefs) {
    if (/\/feed\/update\/urn:li:activity:/i.test(href)) return normalizeLinkedInUrl(href);
  }
  for (const href of hrefs) {
    if (/\/feed\/update\/|\/posts\/|\/activity-/i.test(href)) return normalizeLinkedInUrl(href);
  }

  const html = await item.evaluate((node) => node.outerHTML).catch(() => '');
  const htmlHref = html.match(/href="([^"]*\/feed\/update\/urn:li:activity:\d+\/?[^"]*)"/i);
  if (htmlHref) return normalizeLinkedInUrl(htmlHref[1]);
  return activityUrnToUrl(html);
}

function metricFromText(text, labels) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
  for (const label of labels) {
    const re = new RegExp(`([\\d,.]+\\s*[km]?)\\s+${label}\\b`, 'i');
    const match = normalized.match(re);
    if (match) return match[1];
  }
  return '';
}

function extractNumber(text) {
  return parseCount(String(text || '').replace(/^[^\d]*/, ''));
}

async function metricBySelectors(item, selectors) {
  const text = await safeText(item, selectors);
  const fromText = extractNumber(text);
  if (fromText) return fromText;
  for (const selector of selectors) {
    const label = await item.locator(selector).first().getAttribute('aria-label').catch(() => '');
    const value = extractNumber(label);
    if (value) return value;
  }
  return 0;
}

async function metricByAria(item, keyword) {
  const candidates = item.locator(`button[aria-label*="${keyword}"], span[aria-label*="${keyword}"]`);
  const count = await candidates.count().catch(() => 0);
  for (let index = 0; index < Math.min(count, 12); index += 1) {
    const label = await candidates.nth(index).getAttribute('aria-label').catch(() => '');
    const value = extractNumber(label);
    if (value) return value;
  }
  return 0;
}

async function extractMetrics(item, text) {
  const likes =
    await metricBySelectors(item, REACTION_SELECTORS)
    || await metricByAria(item, 'reaction')
    || extractNumber(metricFromText(text, ['reaction', 'reactions', 'like', 'likes']));
  const comments =
    await metricBySelectors(item, COMMENT_SELECTORS)
    || await metricByAria(item, 'comment')
    || extractNumber(metricFromText(text, ['comment', 'comments']));
  const reposts =
    await metricBySelectors(item, REPOST_SELECTORS)
    || await metricByAria(item, 'repost')
    || extractNumber(metricFromText(text, ['repost', 'reposts', 'share', 'shares']));
  return { likes, comments, reposts };
}

function extractRelativeTimeFromText(text) {
  const cleaned = String(text || '').replace(/Â·/g, '•').replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';

  const segments = cleaned.split(/[•\n\r]+/).map((segment) => segment.trim()).filter(Boolean);
  const candidates = [];
  for (const segment of segments) {
    const normalized = segment.toLowerCase().replace('edited', '').trim();
    const match = normalized.match(RELATIVE_TIME_PATTERN);
    if (match) candidates.push(match[0]);
  }
  if (candidates.length) return candidates.sort((a, b) => a.length - b.length)[0];

  const matches = [...cleaned.matchAll(new RegExp(RELATIVE_TIME_PATTERN, 'gi'))].map((match) => match[0].trim());
  return matches.length ? matches[matches.length - 1] : '';
}

async function extractPostedAtRaw(item, fallbackText) {
  for (const selector of TIME_SELECTORS) {
    const elements = item.locator(selector);
    const count = Math.min(await elements.count().catch(() => 0), 8);
    for (let index = 0; index < count; index += 1) {
      const text = (await elements.nth(index).innerText({ timeout: 2000 }).catch(() => '')).trim();
      const relative = extractRelativeTimeFromText(text);
      if (relative) return relative;
    }
  }
  return extractRelativeTimeFromText(fallbackText);
}

function cleanFallbackContent(text) {
  const skipExact = new Set(['like', 'comment', 'repost', 'send', 'follow', 'activate to view larger image']);
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !skipExact.has(line.toLowerCase()))
    .join('\n');
}

export async function parsePosts(page, { groupUrl, maxItems }) {
  const groupName = await extractGroupName(page);
  const memberCount = await extractMemberCount(page);
  const locator = await findPostLocator(page);
  const rawPosts = await locator.evaluateAll(
    (nodes, options) => {
      const {
        maxItems: limit,
        authorSelectors,
        contentSelectors,
        timeSelectors,
        reactionSelectors,
        commentSelectors,
        repostSelectors,
        linkSelectors,
      } = options;

      const relativeTimePattern =
        /\b(?:just\s+now|now|\d+\s*(?:mo|mos|month|months|yr|yrs|year|years|w|wk|wks|week|weeks|d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes))\b/i;

      const textOf = (node) => (node?.innerText || node?.textContent || '').trim();
      const cleanLines = (text) => String(text || '')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      const firstText = (root, selectors) => {
        for (const selector of selectors) {
          const found = root.querySelector(selector);
          const text = textOf(found);
          if (text) return text;
        }
        return '';
      };
      const normalizeUrl = (href) => {
        if (!href) return '';
        try {
          return new URL(href, 'https://www.linkedin.com').toString();
        } catch {
          return '';
        }
      };
      const activityUrl = (value) => {
        const match = String(value || '').match(/urn:li:activity:\d+/);
        return match ? `https://www.linkedin.com/feed/update/${match[0]}/` : '';
      };
      const postUrlOf = (root) => {
        for (const attr of ['data-urn', 'data-id']) {
          const url = activityUrl(root.getAttribute(attr));
          if (url) return url;
        }
        const childUrn = root.querySelector('[role="article"][data-urn], [data-urn^="urn:li:activity:"]');
        const childUrl = activityUrl(childUrn?.getAttribute('data-urn'));
        if (childUrl) return childUrl;
        for (const selector of linkSelectors) {
          const link = root.querySelector(selector);
          const url = normalizeUrl(link?.getAttribute('href') || link?.href || '');
          if (url) return url;
        }
        for (const link of Array.from(root.querySelectorAll('a[href]')).slice(0, 80)) {
          const href = link.getAttribute('href') || link.href || '';
          if (/\/feed\/update\/urn:li:activity:|\/posts\/|\/activity-/i.test(href)) {
            const url = normalizeUrl(href);
            if (url) return url;
          }
        }
        return activityUrl(root.outerHTML || '');
      };
      const metricFromText = (text, labels) => {
        const normalized = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
        for (const label of labels) {
          const match = normalized.match(new RegExp(`([\\d,.]+\\s*[km]?)\\s+${label}\\b`, 'i'));
          if (match) return match[1];
        }
        return '';
      };
      const metricBySelectors = (root, selectors) => {
        for (const selector of selectors) {
          const found = root.querySelector(selector);
          const value = textOf(found) || found?.getAttribute?.('aria-label') || '';
          const match = String(value).match(/[\d,.]+\s*[km]?/i);
          if (match) return match[0];
        }
        return '';
      };
      const postedAtOf = (root, fallbackText) => {
        for (const selector of timeSelectors) {
          const candidates = Array.from(root.querySelectorAll(selector)).slice(0, 8);
          for (const candidate of candidates) {
            const text = textOf(candidate) || candidate.getAttribute('aria-label') || '';
            const match = text.match(relativeTimePattern);
            if (match) return match[0];
          }
        }
        const fallback = String(fallbackText || '').replace(/\s+/g, ' ');
        const match = fallback.match(relativeTimePattern);
        return match ? match[0] : '';
      };
      const cleanContent = (text) => {
        const skip = new Set(['like', 'comment', 'repost', 'send', 'follow', 'activate to view larger image']);
        return cleanLines(text)
          .filter((line) => !skip.has(line.toLowerCase()))
          .slice(0, 40)
          .join('\n');
      };

      const output = [];
      const seen = new Set();
      for (const node of nodes) {
        if (output.length >= limit) break;
        const text = textOf(node);
        if (!text) continue;

        const lines = cleanLines(text);
        const author = firstText(node, authorSelectors) || lines[0] || '';
        const content = firstText(node, contentSelectors) || cleanContent(text);
        if (!content) continue;

        const postUrl = postUrlOf(node);
        const dedupeKey = postUrl || `${author}\n${content.slice(0, 160)}`;
        if (seen.has(dedupeKey)) continue;
        seen.add(dedupeKey);

        output.push({
          author,
          content,
          likes:
            metricBySelectors(node, reactionSelectors)
            || metricFromText(text, ['reaction', 'reactions', 'like', 'likes']),
          comments:
            metricBySelectors(node, commentSelectors)
            || metricFromText(text, ['comment', 'comments']),
          reposts:
            metricBySelectors(node, repostSelectors)
            || metricFromText(text, ['repost', 'reposts', 'share', 'shares']),
          postUrl,
          postedAtRaw: postedAtOf(node, text),
        });
      }
      return output;
    },
    {
      maxItems,
      authorSelectors: AUTHOR_SELECTORS,
      contentSelectors: CONTENT_SELECTORS,
      timeSelectors: TIME_SELECTORS,
      reactionSelectors: REACTION_SELECTORS,
      commentSelectors: COMMENT_SELECTORS,
      repostSelectors: REPOST_SELECTORS,
      linkSelectors: LINK_SELECTORS,
    },
  ).catch(() => []);

  return rawPosts.map((raw) => normalizePost(raw, groupUrl, groupName, memberCount));
}
