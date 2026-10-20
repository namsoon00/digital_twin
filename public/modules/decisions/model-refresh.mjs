// A failed background refresh has a bounded retry window, not a failed HTTP request.
export function modelRefreshPending(payload) {
  const cache = payload?.cache || {};
  return payload?.status === "warming" || Boolean(cache.refreshing) ||
    Boolean(cache.stale && Number(cache.retryAfterSeconds) > 0);
}

export function modelRefreshDelay(payload) {
  const seconds = Number(payload?.cache?.retryAfterSeconds || 0);
  return Number.isFinite(seconds) ? Math.max(5000, Math.min(30000, seconds * 1000)) : 5000;
}
