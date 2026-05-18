/**
 * groupApiCrawler.js
 *
 * Crawl LinkedIn group posts using LinkedIn's REAL internal GraphQL endpoint
 * (reverse-engineered from browser network capture on /groups/{id}/).
 *
 * Real endpoint (confirmed 2025-05):
 *   GET /voyager/api/graphql
 *     ?variables=(start:0,count:40,groupId:86204)
 *     &queryId=voyagerFeedDashGroupsUpdates.39a663616bc8b24e2984d974c1e74d91
 *
 * Pagination: subsequent pages add paginationToken to variables:
 *   ?variables=(start:40,count:40,groupId:86204,paginationToken:XXX-YYY-ZZZ)
 *
 * Response format: LinkedIn normalized JSON (application/vnd.linkedin.normalized+json+2.1)
 *   - data.data.feedUpdates[]  → array of update URN refs
 *   - data.included[]          → flat entity map, real post objects live here
 *   - data.data.metadata.paginationToken → next page token
 *
 * Flow:
 *   Iteration 1: start=0,  count=40, no paginationToken → ~35-39 new posts
 *   Iteration 2: start=40, count=40, paginationToken=XXX → ~35-39 new posts
 *   ...
 *   Iteration N: stop after maxIterations or no more pages or maxItems reached
 *
 * Deduplication: by post URL across all iterations.
 */

import { log } from 'crawlee';

// ─── Constants ────────────────────────────────────────────────────────────────

const LI_BASE = 'https://www.linkedin.com';

/**
 * The confirmed GraphQL queryId for group feed (from browser capture May 2025).
 * If LinkedIn rotates this hash, the fallback endpoints below will activate.
 */
const GQL_QUERY_ID = 'voyagerFeedDashGroupsUpdates.39a663616bc8b24e2984d974c1e74d91';

const DEFAULT_POSTS_PER_PAGE = 40;

/**
 * Build the endpoint URL for a given page.
 * LinkedIn uses a Restli-style variable serialization (parentheses, not JSON).
 *
 * @param {string} groupId
 * @param {number} start - 0-based offset
 * @param {string|null} paginationToken
 * @param {string} queryId
 */
function buildGqlUrl(groupId, start, count, paginationToken, queryId = GQL_QUERY_ID) {
  // Restli variable format: (key:value,key2:value2)
  let vars = `(start:${start},count:${count},groupId:${groupId}`;
  if (paginationToken) {
    vars += `,paginationToken:${paginationToken}`;
  }
  vars += ')';
  return `${LI_BASE}/voyager/api/graphql?variables=${vars}&queryId=${queryId}`;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function toInt(value, fallback = 0) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function randomDelay(minMs, maxMs) {
  const min = Math.min(minMs, maxMs);
  const max = Math.max(minMs, maxMs);
  return Math.floor(min + Math.random() * (max - min + 1));
}

/**
 * Build exact HTTP headers matching what Chrome sends to LinkedIn GraphQL API.
 * Source: captured from browser DevTools on /groups/86204/.
 *
 * Critical rules:
 *  - csrf-token = JSESSIONID value WITHOUT surrounding quotes
 *  - x-li-pem-metadata = "Voyager - Groups - Group Feed=default-feed" (required for GraphQL)
 *  - x-restli-protocol-version = "2.0.0"
 */
function buildApiHeaders(cookies = [], groupId = '', groupUrl = '', liPageInstance = '') {
  const jsessionid = cookies.find(
    (c) => c.name === 'JSESSIONID' && (c.domain || '').includes('linkedin'),
  );
  // JSESSIONID stored as: "ajax:0023967985635556308" — strip surrounding quotes
  const rawCsrf = jsessionid?.value || '';
  const csrf = rawCsrf.replace(/^"|"$/g, '');

  const cookieHeader = cookies
    .filter((c) => (c.domain || '').includes('linkedin'))
    .map((c) => `${c.name}=${c.value}`)
    .join('; ');

  // x-li-page-instance for group page
  const pageInstance = liPageInstance || (groupId
    ? `urn:li:page:d_flagship3_groups_entity;${Buffer.from(groupId).toString('base64')}`
    : 'urn:li:page:d_flagship3_groups');

  return {
    'accept': 'application/vnd.linkedin.normalized+json+2.1',
    'accept-language': 'en-US,en;q=0.9',
    'cache-control': 'no-cache',
    'csrf-token': csrf,
    'pragma': 'no-cache',
    // Required for GraphQL group feed endpoint
    'x-li-pem-metadata': 'Voyager - Groups - Group Feed=default-feed',
    'x-li-lang': 'en_US',
    'x-li-page-instance': pageInstance,
    // Match Chrome 148 from captured request
    'x-li-track': JSON.stringify({
      clientVersion: '1.13.44203',
      mpVersion: '1.13.44203',
      osName: 'web',
      timezoneOffset: 7,
      timezone: 'Asia/Ho_Chi_Minh',
      deviceFormFactor: 'DESKTOP',
      mpName: 'voyager-web',
      displayDensity: 1.25,
      displayWidth: 1920,
      displayHeight: 1080,
    }),
    'x-restli-protocol-version': '2.0.0',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    ...(groupUrl ? { referer: groupUrl.endsWith('/') ? groupUrl : `${groupUrl}/` } : {}),
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    ...(cookieHeader ? { cookie: cookieHeader } : {}),
  };
}

// ─── Group ID extraction ───────────────────────────────────────────────────────

/**
 * Extract numeric group ID from a LinkedIn group URL.
 * e.g. https://www.linkedin.com/groups/86204/ → "86204"
 */
export function extractGroupId(url) {
  const match = String(url || '').match(/\/groups\/(\d+)/i);
  return match ? match[1] : '';
}

// ─── Response parsing ──────────────────────────────────────────────────────────

/**
 * Extract plain text from LinkedIn's voyager text objects.
 * Handles: string, {text:""}, {values:[{value:""}]}
 */
function extractText(obj) {
  if (!obj) return null;
  if (typeof obj === 'string') return obj.trim() || null;
  if (typeof obj.text === 'string') return obj.text.trim() || null;
  if (typeof obj.accessibilityText === 'string') return obj.accessibilityText.trim() || null;
  if (Array.isArray(obj.values)) {
    return obj.values.map((v) => v?.value || '').join('').trim() || null;
  }
  return null;
}

function findDeepValue(obj, predicate, depth = 0) {
  if (!obj || depth > 8) return null;
  if (typeof obj !== 'object') return null;
  if (Array.isArray(obj)) {
    for (const item of obj) {
      const found = findDeepValue(item, predicate, depth + 1);
      if (found != null) return found;
    }
    return null;
  }
  for (const [key, value] of Object.entries(obj)) {
    if (predicate(key, value)) return value;
    const found = findDeepValue(value, predicate, depth + 1);
    if (found != null) return found;
  }
  return null;
}

function collectDeepValues(obj, predicate, depth = 0, out = []) {
  if (!obj || depth > 10) return out;

  if (Array.isArray(obj)) {
    for (const item of obj) collectDeepValues(item, predicate, depth + 1, out);
    return out;
  }

  if (typeof obj !== 'object') return out;

  for (const [key, value] of Object.entries(obj)) {
    if (predicate(key, value)) out.push(value);
    if (value && typeof value === 'object') {
      collectDeepValues(value, predicate, depth + 1, out);
    }
  }

  return out;
}

function findTextByKey(obj, keyPattern) {
  const value = findDeepValue(obj, (key, candidate) => {
    if (!keyPattern.test(key)) return false;
    return typeof candidate === 'string'
      || candidate?.text
      || Array.isArray(candidate?.values);
  });
  return extractText(value);
}

function findDeepNumberByKey(obj, keyPattern) {
  const value = findDeepValue(obj, (key, candidate) => {
    if (!keyPattern.test(key)) return false;
    if (typeof candidate === 'number') return true;
    if (typeof candidate === 'string') return /^[\d,.]+$/.test(candidate.trim());
    if (candidate && typeof candidate === 'object') {
      return typeof candidate.count === 'number'
        || typeof candidate.value === 'number'
        || typeof candidate.total === 'number';
    }
    return false;
  });
  return extractCount(value);
}

function pickMaxNumberFromCluster(cluster, keyPattern) {
  const values = [];

  for (const entity of cluster) {
    collectDeepValues(entity, (key, value) => {
      if (!keyPattern.test(key)) return false;
      return typeof value === 'number'
        || typeof value === 'string'
        || value?.count != null
        || value?.value != null
        || value?.total != null;
    }, 0, values);
  }

  const nums = values
    .map((value) => extractCount(value))
    .filter((value) => Number.isFinite(value) && value >= 0);

  return nums.length ? Math.max(...nums) : 0;
}

function pickFirstTextFromCluster(cluster, keyPattern, minLength = 1) {
  for (const entity of cluster) {
    const values = collectDeepValues(entity, (key, value) => {
      if (!keyPattern.test(key)) return false;
      return typeof value === 'string'
        || typeof value?.text === 'string'
        || Array.isArray(value?.values);
    });

    for (const value of values) {
      const text = extractText(value);
      if (text && text.length >= minLength) return text;
    }
  }

  return '';
}

function pickTimestampFromCluster(cluster) {
  const values = [];

  for (const entity of cluster) {
    collectDeepValues(entity, (key, value) => {
      if (!/updateTimestamp|createdAt|createdTime|publishedAt|postedAt|postedTime|timestamp/i.test(key)) {
        return false;
      }
      return typeof value === 'number' || /^\d{10,}$/.test(String(value || ''));
    }, 0, values);
  }

  const nums = values
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 1000000000);

  if (!nums.length) return { timestamp: '', datetime: null };

  return parseTimestamp(Math.max(...nums));
}

function pickAuthorFromCluster(cluster) {
  for (const entity of cluster) {
    const author = extractActorName(entity);
    if (author) return author;
  }

  return pickFirstTextFromCluster(cluster, /actorName|authorName|name|title/i, 2);
}

function findDeepStringByKey(obj, keyPattern) {
  const value = findDeepValue(obj, (key, candidate) =>
    keyPattern.test(key) && typeof candidate === 'string' && candidate.trim().length > 0);
  return typeof value === 'string' ? value : '';
}

function firstRegexMatch(value, pattern) {
  const match = String(value || '').match(pattern);
  return match ? match[1] || match[0] : '';
}

function postUrnFromValue(value) {
  return firstRegexMatch(value, /urn:li:(?:activity|groupPost):[\w:-]+/i);
}

function extractActivityIdFromUrnOrUrl(value) {
  const text = String(value || '');

  // urn:li:groupPost:961087-7461057380286488577
  let match = text.match(/urn:li:groupPost:\d+-(\d+)/i);
  if (match) return match[1];

  // urn:li:activity:7460975383337811968
  match = text.match(/urn:li:activity:(\d+)/i);
  if (match) return match[1];

  // fallback: any groupPost-like pattern
  match = text.match(/groupPost:\d+-(\d+)/i);
  if (match) return match[1];

  return '';
}

function parseTimestampFromLinkedInId(id) {
  try {
    if (!id) return { timestamp: '', datetime: null };

    const raw = BigInt(String(id));
    const ms = raw >> 22n;

    // sanity check: LinkedIn activity IDs should decode to a realistic Unix ms time
    const msNumber = Number(ms);
    if (!Number.isFinite(msNumber) || msNumber < 946684800000) {
      return { timestamp: '', datetime: null };
    }

    return {
      timestamp: String(Math.floor(msNumber / 1000)),
      datetime: new Date(msNumber).toISOString(),
    };
  } catch {
    return { timestamp: '', datetime: null };
  }
}

function parseTimestampFromUrnOrUrl(value) {
  const activityId = extractActivityIdFromUrnOrUrl(value);
  return parseTimestampFromLinkedInId(activityId);
}

function postKeyFromUrn(urn) {
  if (!urn) return '';
  const match = String(urn).match(/(?:activity:|groupPost:[^-]+-)(\d+)/i);
  return match ? match[1] : String(urn);
}

/**
 * Extract author/actor name from a voyager update element.
 * LinkedIn puts the actor in different locations depending on post type.
 */
function extractActorName(element) {
  // Direct actor object
  if (element.actor) {
    const name = extractText(element.actor.name)
      || extractText(element.actor.title)
      || extractText(element.actor.subDescription);
    if (name) return name;
  }
  // updateV2 actor
  if (element.updateV2?.actor) {
    const name = extractText(element.updateV2.actor.name)
      || extractText(element.updateV2.actor.title);
    if (name) return name;
  }
  // Direct actorName field
  if (element.actorName) return extractText(element.actorName) || '';
  // Reshared updates
  if (element.resharedUpdate?.actor) {
    const name = extractText(element.resharedUpdate.actor.name);
    if (name) return name;
  }
  return '';
}

/**
 * Detect post type from a voyager update element.
 */
function detectPostType(element) {
  const content = element.content || element.updateV2?.content || element.specificContent || {};
  const keys = Object.keys(content);
  if (keys.some((k) => /image|photo|carousel/i.test(k))) return 'Image';
  if (keys.some((k) => /video/i.test(k))) return 'Video';
  if (keys.some((k) => /article|document/i.test(k))) return 'Article';
  if (element.resharedUpdate) return 'Repost';
  return 'Other';
}

/**
 * Parse unix timestamp (milliseconds) to seconds string and ISO string.
 */
function parseTimestamp(ts) {
  if (!ts) return { timestamp: '', datetime: null };
  const tsNum = Number(ts);
  if (!Number.isFinite(tsNum) || tsNum <= 0) return { timestamp: '', datetime: null };
  // LinkedIn stores timestamps in milliseconds
  const tsSeconds = tsNum > 1e11 ? Math.floor(tsNum / 1000) : tsNum;
  return {
    timestamp: String(tsSeconds),
    datetime: new Date(tsSeconds * 1000).toISOString(),
  };
}

/**
 * Extract a social count metric from various LinkedIn response shapes.
 */
function extractCount(obj) {
  if (obj == null) return 0;
  if (typeof obj === 'number') return obj;
  if (typeof obj === 'object') {
    return toInt(obj.count ?? obj.value ?? obj.total ?? 0, 0);
  }
  return toInt(obj, 0);
}

/**
 * Build a canonical LinkedIn post URL from a voyager URN.
 * e.g. "urn:li:groupPost:86204-7461360634874744832"
 *   → https://www.linkedin.com/feed/update/urn:li:groupPost:86204-7461360634874744832
 */
function urnToUrl(urn) {
  if (!urn) return '';
  const cleaned = String(urn).split('?')[0];
  const directUrn = postUrnFromValue(cleaned);
  if (directUrn) {
    return `${LI_BASE}/feed/update/${directUrn}`;
  }
  return '';
}

/**
 * Map a single entity from included[] to a normalized post object.
 * Returns null if the entity is not a post or lacks a URL.
 *
 * FIX (bug #2): Added `author` field via extractActorName().
 * FIX (bug #3): day_up uses datetime first, text_date as fallback.
 */
function mapUpdateElement(element, groupUrl) {
  if (!element) return null;
  if (typeof element === 'string') {
    const url = urnToUrl(element);
    return url ? {
      type: 'group',
      url,
      author: '',
      timestamp: '',
      text_date: '',
      datetime: null,
      day_up: '',
      tipo_post: 'Other',
      content: null,
      likes: 0,
      comments: 0,
      views: '',
      share: 0,
      group_url: groupUrl,
      source: 'api',
    } : null;
  }
  if (typeof element !== 'object') return null;

  const cluster = [
    element,
    ...(Array.isArray(element.__related) ? element.__related : []),
  ];

  const serialized = JSON.stringify(cluster);

  // URN / URL
  const urn = element.__canonicalUrn
    || element.entityUrn
    || element.dashEntityUrn
    || element.updateMetadata?.urn
    || element.updateKey
    || postUrnFromValue(serialized)
    || '';
  const url = urnToUrl(urn) || element.url || element.shareUrl || '';
  if (!url) return null;

  let { timestamp, datetime } = pickTimestampFromCluster(cluster);

  if (!datetime) {
    const fromUrn = parseTimestampFromUrnOrUrl(urn || url || serialized);
    timestamp = fromUrn.timestamp;
    datetime = fromUrn.datetime;
  }

  const textDate =
    pickFirstTextFromCluster(cluster, /timeText|updateTimeText|textDate/i)
    || '';

  const content = extractText(element.commentary)
    || extractText(element.updateV2?.commentary)
    || pickFirstTextFromCluster(
      cluster,
      /commentary|commentaryText|articleBody|shareCommentary|updateContent|primaryText|text/i,
      20,
    )
    || null;

  const author = pickAuthorFromCluster(cluster);

  const likes = pickMaxNumberFromCluster(
    cluster,
    /^(numLikes|likeCount|likesCount|reactionCount|totalReactionCount)$/i,
  );
  const comments = pickMaxNumberFromCluster(
    cluster,
    /^(numComments|commentCount|commentsCount|totalCommentCount)$/i,
  );
  const shares = pickMaxNumberFromCluster(
    cluster,
    /^(numShares|shareCount|sharesCount|repostCount|repostsCount)$/i,
  );

  // Post type
  const tipo_post = detectPostType(element);

  // FIX (bug #3): day_up = datetime (ISO) first, text_date as fallback
  const day_up = datetime || textDate || '';

  return {
    type: 'group',
    url,
    author,                       // FIX bug #2
    timestamp,
    text_date: textDate,
    datetime,
    day_up,                       // FIX bug #3
    tipo_post,
    content,
    likes,
    comments,
    views: '',
    share: shares,
    group_url: groupUrl,
    source: 'api',
  };
}

/**
 * Parse LinkedIn's normalized JSON response for group feed.
 *
 * LinkedIn GraphQL response shape:
 * {
 *   "data": {
 *     "feedUpdates": { "elements": ["urn:li:...", ...] },
 *     "metadata": { "paginationToken": "XXX-YYY-ZZZ" }
 *   },
 *   "included": [
 *     { "$type": "com.linkedin.voyager.dash.feed.DashFeedUpdate", entityUrn: "...", ... },
 *     { "$type": "com.linkedin.voyager.dash.identity.profile.Profile", ... },
 *     ...
 *   ]
 * }
 *
 * The actual post objects live in included[] filtered by $type.
 */
function extractUpdatesFromResponse(data) {
  if (!data || typeof data !== 'object') {
    return { rawElements: [], nextPaginationToken: null };
  }

  const rawElements = [];
  const seen = new Set();

  // PRIMARY PATH: included[] contains all real entity objects
  const included = Array.isArray(data.included) ? data.included : [];
  const entityInfos = included
    .filter((entity) => entity && typeof entity === 'object')
    .map((entity) => {
      const serialized = JSON.stringify(entity);
      const urn = entity.entityUrn
        || entity.dashEntityUrn
        || entity.updateMetadata?.urn
        || entity.updateKey
        || postUrnFromValue(serialized)
        || '';
      return {
        entity,
        serialized,
        urn,
        postKey: postKeyFromUrn(urn),
      };
    });

  for (const { entity, serialized, urn, postKey } of entityInfos) {
    if (!entity || typeof entity !== 'object') continue;
    const type = String(entity.$type || entity.entityType || '');

    // Match feed update types
    const isFeedUpdate = (
      /DashFeedUpdate|UpdateV2|GroupUpdate|GroupPost/i.test(type)
      || entity.updateMetadata != null   // has updateMetadata = definitely an update
      || (entity.commentary != null && entity.entityUrn)  // has content + URN
      || /urn:li:(activity|groupPost):/i.test(serialized)
    );
    if (!isFeedUpdate) continue;

    const dedupKey = urn || entity.entityUrn || entity.dashEntityUrn || entity.updateKey || '';
    if (dedupKey && seen.has(dedupKey)) continue;
    if (dedupKey) seen.add(dedupKey);

    const related = postKey
      ? entityInfos
        .filter((info) => {
          if (info.entity === entity) return false;
          return info.postKey === postKey
            || info.serialized.includes(postKey)
            || (urn && info.serialized.includes(urn));
        })
        .slice(0, 50)
        .map((info) => info.entity)
      : [];
    rawElements.push({
      ...entity,
      __canonicalUrn: urn,
      __related: related,
    });
  }

  // FALLBACK: elements[] may contain full objects (older API format)
  if (rawElements.length === 0) {
    const elements = Array.isArray(data.elements) ? data.elements
      : Array.isArray(data.data?.feedUpdates?.elements) ? data.data.feedUpdates.elements
      : Array.isArray(data.data?.elements) ? data.data.elements
      : [];
    for (const el of elements) {
      if (el && typeof el === 'object' && !Array.isArray(el)) {
        rawElements.push(el);
      }
    }
  }

  // Pagination token — LinkedIn puts it in data.data.metadata
  const nextPaginationToken =
    data.data?.metadata?.paginationToken
    ?? data.data?.feedUpdates?.metadata?.paginationToken
    ?? data.metadata?.paginationToken
    ?? data.paging?.paginationToken
    ?? data.paginationToken
    ?? findDeepStringByKey(data, /paginationToken|pageToken|nextPageToken|cursor/i)
    ?? null;

  return { rawElements, nextPaginationToken };
}

// ─── Core fetch function ───────────────────────────────────────────────────────

/**
 * Fetch one page of group feed via the real LinkedIn GraphQL API (no browser).
 */
async function fetchGroupFeedPage({
  groupId,
  groupUrl,
  start,
  paginationToken,
  cookies,
  pageSize = DEFAULT_POSTS_PER_PAGE,
  liPageInstance = '',
  iterationIndex,
  maxIterations,
  fetchTimeoutMs = 20000,
}) {
  const iterLabel = `${iterationIndex + 1}/${maxIterations}`;
  const headers = buildApiHeaders(cookies, groupId, groupUrl, liPageInstance);
  const url = buildGqlUrl(groupId, start, pageSize, paginationToken);

  log.info(
    `[api-request] groupId=${groupId} type=group iteration=${iterLabel} `
    + `start=${start} paginationToken=${paginationToken ? 'yes' : 'no'}`,
  );

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), fetchTimeoutMs);

  let response;
  try {
    response = await fetch(url, {
      method: 'GET',
      headers,
      signal: controller.signal,
      redirect: 'follow',
    });
  } catch (error) {
    clearTimeout(timeout);
    const msg = error?.name === 'AbortError'
      ? `API_TIMEOUT: request aborted after ${fetchTimeoutMs}ms`
      : `API_NETWORK_ERROR: ${error?.message || error}`;
    throw new Error(msg);
  }
  clearTimeout(timeout);

  // Auth / redirect checks
  if (/\/login|\/uas\/login|\/checkpoint|\/authwall/i.test(response.url)) {
    throw new Error(`API_AUTH_WALL: redirected to ${response.url}`);
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error(`API_AUTH_REQUIRED: HTTP ${response.status} from ${url}`);
  }
  if (!response.ok) {
    throw new Error(`API_HTTP_ERROR: HTTP ${response.status} from ${url}`);
  }

  let data;
  try {
    data = await response.json();
  } catch {
    const text = await response.text().catch(() => '');
    throw new Error(`API_JSON_PARSE_ERROR: preview=${text.slice(0, 200)}`);
  }

  const { rawElements, nextPaginationToken } = extractUpdatesFromResponse(data);

  log.info(
    `[api-page-raw] groupId=${groupId} iteration=${iterLabel} `
    + `rawElements=${rawElements.length} nextToken=${nextPaginationToken ? 'yes' : 'no'}`,
  );

  return {
    rawElements,
    nextPaginationToken,
    hasMore: Boolean(nextPaginationToken),
    received: rawElements.length,
  };
}

// ─── Main exported crawl function ─────────────────────────────────────────────

/**
 * Crawl a single LinkedIn group using API pagination (no browser).
 *
 * Mirrors the behavior from the run log:
 *   [start]     groupId=86204 type=group iterations=6
 *   [request]   iteration=1/6 paginationToken=no
 *   [page-done] received=36 new=36 duplicates=0 total=36 hasMore=True
 *   ...
 *   [finished]  successfulPages=6 failedPages=0 items=203
 */
export async function crawlGroupByApi({
  groupUrl,
  cookies = [],
  maxIterations = 6,
  maxItems = 250,
  pageSize = DEFAULT_POSTS_PER_PAGE,
  apiStart = 0,
  liPageInstance = '',
  continueWithoutPaginationToken = true,
  pageDelayMinMs = 500,
  pageDelayMaxMs = 1500,
  fetchTimeoutMs = 20000,
  maxRetries = 5,
  maxConsecutiveFailedPages = 3,
}) {
  const groupId = extractGroupId(groupUrl);
  if (!groupId) {
    return {
      posts: [],
      successfulPages: 0,
      failedPages: 0,
      status: 'failed',
      message: `Cannot extract group ID from URL: ${groupUrl}`,
    };
  }

  log.info(
    `[api-start] groupId=${groupId} type=group start=1 `
    + `iterations=${maxIterations} maxRetries=${maxRetries} `
    + `maxConsecutiveFailedPages=${maxConsecutiveFailedPages}`,
  );

  const seen = new Set();
  const allPosts = [];
  let paginationToken = null;
  let start = apiStart;
  let successfulPages = 0;
  let failedPages = 0;
  let consecutiveFailed = 0;

  for (let i = 0; i < maxIterations; i++) {
    if (allPosts.length >= maxItems) {
      log.info(`[api-cap] groupId=${groupId} reached maxItems=${maxItems}`);
      break;
    }

    let rawElements = [];
    let nextToken = null;
    let hasMore = false;
    let received = 0;
    let pageOk = false;

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const result = await fetchGroupFeedPage({
          groupId,
          groupUrl,
          start,
          paginationToken,
          cookies,
          pageSize,
          liPageInstance,
          iterationIndex: i,
          maxIterations,
          fetchTimeoutMs,
        });
        rawElements = result.rawElements;
        nextToken = result.nextPaginationToken;
        hasMore = result.hasMore;
        received = result.received;
        pageOk = true;
        break;
      } catch (error) {
        const msg = error?.message || String(error);
        if (/API_AUTH/i.test(msg)) {
          log.error(`[api-auth-error] groupId=${groupId} error=${msg}`);
          return {
            posts: allPosts,
            successfulPages,
            failedPages,
            status: 'auth_required',
            message: msg,
          };
        }
        log.warning(
          `[api-retry] groupId=${groupId} iteration=${i + 1}/${maxIterations} `
          + `attempt=${attempt + 1}/${maxRetries + 1} error=${msg}`,
        );
        if (attempt < maxRetries) {
          await new Promise((resolve) =>
            setTimeout(resolve, randomDelay(pageDelayMinMs * 2, pageDelayMaxMs * 2)),
          );
        }
      }
    }

    if (!pageOk) {
      failedPages += 1;
      consecutiveFailed += 1;
      log.error(
        `[api-page-fail] groupId=${groupId} iteration=${i + 1}/${maxIterations} `
        + `consecutiveFailed=${consecutiveFailed}`,
      );
      if (consecutiveFailed >= maxConsecutiveFailedPages) {
        log.error(`[api-abort] groupId=${groupId} consecutiveFailed=${consecutiveFailed} → stopping`);
        break;
      }
      continue;
    }

    consecutiveFailed = 0;

    // Deduplicate and map posts
    let newCount = 0;
    let dupCount = 0;
    for (const element of rawElements) {
      if (allPosts.length >= maxItems) break;
      const post = mapUpdateElement(element, groupUrl);
      if (!post) continue;
      const key = post.url;
      if (!key || seen.has(key)) {
        dupCount += 1;
        continue;
      }
      seen.add(key);
      allPosts.push(post);
      newCount += 1;
    }

    successfulPages += 1;
    paginationToken = nextToken;
    start += pageSize;

    log.info(
      `[api-page-done] groupId=${groupId} type=group start=${start - pageSize} `
      + `received=${received} new=${newCount} duplicates=${dupCount} `
      + `total=${allPosts.length} hasMore=${hasMore} `
      + `count=${received} nextPaginationToken=${nextToken ? 'yes' : 'no'}`,
    );

    if (!hasMore) {
      if (!continueWithoutPaginationToken || received === 0 || newCount === 0) {
        log.info(`[api-no-more] groupId=${groupId} no more pages after iteration ${i + 1}`);
        break;
      }
      log.info(
        `[api-offset-continue] groupId=${groupId} no paginationToken, `
        + `continuing with start=${start} because received=${received} new=${newCount}`,
      );
    }

    if (newCount === 0 && successfulPages > 1) {
      log.info(`[api-no-new] groupId=${groupId} stopping after duplicate/empty page at iteration ${i + 1}`);
      break;
    }

    if (i < maxIterations - 1) {
      await new Promise((resolve) =>
        setTimeout(resolve, randomDelay(pageDelayMinMs, pageDelayMaxMs)),
      );
    }
  }

  const status = allPosts.length > 0
    ? (failedPages > 0 ? 'partial' : 'success')
    : 'failed';

  log.info(
    `[api-finished] groupId=${groupId} type=group `
    + `successfulPages=${successfulPages} failedPages=${failedPages} items=${allPosts.length}`,
  );

  return {
    posts: allPosts,
    successfulPages,
    failedPages,
    status,
    message: `API crawl: ${allPosts.length} posts from ${successfulPages} pages`,
  };
}
