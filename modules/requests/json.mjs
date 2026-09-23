import { requestsAreSilent } from "./activity-scope.mjs";
import { createJsonClient } from "./client.mjs";
import { beginNetworkActivity, endNetworkActivity } from "../shell/network-activity.mjs";

const client = createJsonClient({
  fetch: (path, options) => fetch(path, options),
  AbortController: typeof window.AbortController === "function" ? window.AbortController : null,
  begin(path, key, options) {
    const runtime = window.OrbitWebRuntime;
    return {
      activity: options.silent || requestsAreSilent() ? "" : beginNetworkActivity(path, "GET"),
      metric: runtime ? runtime.begin("api-request", { path, key }) : "",
      runtime
    };
  },
  end(token, result) {
    if (token.activity) endNetworkActivity(token.activity);
    if (token.metric) token.runtime.end(token.metric, result);
  },
  record(event, fields) { window.OrbitWebRuntime?.record(event, 0, fields); }
});

export function requestJson(path, options) { return client.request(path, options); }
export function activeJsonRequest(key) { return client.active(key); }
export function invalidateJsonResponseCache(prefix) { client.invalidate(String(prefix || "")); }

const readModelPollTimers = new Map();
const readModelPollAttempts = new Map();

export function readModelIsWarming(payload) {
  var cache = payload && payload.readCache && typeof payload.readCache === "object" ? payload.readCache : {};
  return String((payload || {}).status || "").toLowerCase() === "warming"
    || (Boolean(cache.refreshing) && !String(cache.lastSuccessAt || ""));
}

export function clearReadModelPoll(key) {
  const id = String(key || "");
  if (readModelPollTimers.has(id)) window.clearTimeout(readModelPollTimers.get(id));
  readModelPollTimers.delete(id);
  readModelPollAttempts.delete(id);
}

export function scheduleReadModelPoll(key, callback) {
  const id = String(key || "");
  if (!id || typeof callback !== "function" || readModelPollTimers.has(id)) return;
  const attempts = readModelPollAttempts.get(id) || 0;
  if (attempts >= 20) return;
  readModelPollAttempts.set(id, attempts + 1);
  readModelPollTimers.set(id, window.setTimeout(() => {
    readModelPollTimers.delete(id);
    callback();
  }, Math.min(4000, 1000 + attempts * 250)));
}

export function disposeJsonRequests() {
  client.dispose();
  for (const key of readModelPollTimers.keys()) clearReadModelPoll(key);
}
