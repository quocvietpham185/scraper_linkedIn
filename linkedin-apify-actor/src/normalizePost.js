export function parseCount(value) {
  if (value == null) return 0;
  const text = String(value).trim().toLowerCase();
  if (!text) return 0;
  const compact = text.replace(/,/g, "");
  const match = compact.match(/([\d.]+)\s*([km])?/i);
  if (!match) return 0;
  const base = Number.parseFloat(match[1]);
  if (!Number.isFinite(base)) return 0;
  const suffix = match[2];
  const multiplier = suffix === "k" ? 1000 : suffix === "m" ? 1000000 : 1;
  return Math.round(base * multiplier);
}

function normalizeRelativeTime(raw, now = new Date()) {
  const text = String(raw || '').trim().toLowerCase();
  if (!text) return null;
  if (/\b(now|just now)\b/.test(text)) return now.toISOString();

  const match = text.match(
    /\b(\d+)\s*(mo|mos|month|months|yr|yrs|year|years|w|wk|wks|week|weeks|d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b/i,
  );
  if (!match) return null;

  const value = Number.parseInt(match[1], 10);
  if (!Number.isFinite(value)) return null;
  const unit = match[2].toLowerCase();
  const result = new Date(now.getTime());

  if (['m', 'min', 'mins', 'minute', 'minutes'].includes(unit)) {
    result.setMinutes(result.getMinutes() - value);
  } else if (['h', 'hr', 'hrs', 'hour', 'hours'].includes(unit)) {
    result.setHours(result.getHours() - value);
  } else if (['d', 'day', 'days'].includes(unit)) {
    result.setDate(result.getDate() - value);
  } else if (['w', 'wk', 'wks', 'week', 'weeks'].includes(unit)) {
    result.setDate(result.getDate() - value * 7);
  } else if (['mo', 'mos', 'month', 'months'].includes(unit)) {
    result.setDate(result.getDate() - value * 30);
  } else if (['yr', 'yrs', 'year', 'years'].includes(unit)) {
    result.setDate(result.getDate() - value * 365);
  } else {
    return null;
  }

  return result.toISOString();
}

export function normalizePost(raw, groupUrl, groupName, memberCount) {
  const postedAtRaw = raw.postedAtRaw || '';
  return {
    author: raw.author || "",
    content: raw.content || "",
    likes: parseCount(raw.likes),
    comments: parseCount(raw.comments),
    reposts: parseCount(raw.reposts),
    post_url: raw.postUrl || "",
    group_url: groupUrl,
    group_name: groupName || "",
    member_count: memberCount || 0,
    posted_at_raw: postedAtRaw,
    posted_at: raw.postedAt || normalizeRelativeTime(postedAtRaw),
  };
}
