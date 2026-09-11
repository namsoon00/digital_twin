// Transport owns caching and in-flight work, never feature state or navigation.
export function createJsonClient({ fetch: fetchJson, AbortController: Abort, now = Date.now,
  setTimer = setTimeout, clearTimer = clearTimeout, begin = () => null, end = () => {},
  record = () => {}, cacheLimit = 128 } = {}) {
  const active = new Map();
  const cache = new Map();
  const pending = new Set();
  let disposed = false;

  function request(path, options = {}) {
    if (disposed) return Promise.reject(new Error("Request client is disposed"));
    const key = String(options.key || path);
    const existing = active.get(key);
    if (!options.force && existing && existing.path === path) {
      record("api-deduplicated", { path, key });
      return existing.promise;
    }
    const ttl = Math.max(0, Number(options.cacheTtlMs == null ? 4000 : options.cacheTtlMs));
    const cached = cache.get(key);
    if (!options.force && ttl > 0 && cached && cached.path === path && now() - cached.storedAt <= ttl) {
      record("api-cache-hit", { path, key });
      return Promise.resolve(cached.payload);
    }
    const controller = Abort ? new Abort() : null;
    const entry = { path, controller, cacheable: true, promise: null };
    const token = begin(path, key, options);
    let status = 0;
    let outcome = "ok";
    const timer = controller ? setTimer(() => controller.abort(), Math.max(1000, Number(options.timeoutMs || 12000))) : null;
    entry.promise = Promise.resolve().then(() => fetchJson(path, {
      headers: { Accept: "application/json" }, cache: "no-store", signal: controller?.signal
    })).then(async response => {
      status = Number(response.status || 0);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "요청 실패");
      // Forced or invalidated requests must not repopulate the cache out of order.
      if (ttl > 0 && !disposed && entry.cacheable && active.get(key) === entry) {
        cache.delete(key);
        cache.set(key, { path, payload, storedAt: now() });
        while (cache.size > cacheLimit) cache.delete(cache.keys().next().value);
      }
      return payload;
    }).catch(error => {
      outcome = error?.name === "AbortError" ? "timeout" : "error";
      if (error?.name === "AbortError") throw new Error("응답 시간이 초과되었습니다. 마지막 데이터를 유지합니다.");
      throw error;
    }).finally(() => {
      if (timer !== null) clearTimer(timer);
      if (active.get(key) === entry) active.delete(key);
      pending.delete(entry);
      end(token, { status, outcome });
    });
    active.set(key, entry);
    pending.add(entry);
    return entry.promise;
  }

  return {
    request,
    active: key => active.get(String(key))?.promise,
    invalidate(prefix = "") {
      for (const key of cache.keys()) if (key.startsWith(prefix)) cache.delete(key);
      for (const [key, entry] of active) if (key.startsWith(prefix)) {
        entry.cacheable = false;
        active.delete(key);
      }
    },
    dispose() {
      disposed = true;
      cache.clear();
      for (const entry of pending) entry.controller?.abort();
      active.clear();
    }
  };
}
