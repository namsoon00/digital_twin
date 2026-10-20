// Same-origin diagnostics contain no query, message, account or response data.
export function createRequestReporter({ fetch: send, now = Date.now } = {}) {
  let windowAt = -60000;
  let count = 0;
  return function report(result) {
    if (!result || result.outcome === "cancelled" || (result.outcome === "ok" && result.durationMs < 2000)) return;
    const path = String(result.path || "").split("?")[0];
    if (!path.startsWith("/api/") || path === "/api/operations/client-performance") return;
    const time = now();
    if (time - windowAt >= 60000) { windowAt = time; count = 0; }
    if (count >= 12) return;
    count += 1;
    try {
      Promise.resolve(send("/api/operations/client-performance", {
        method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
        body: JSON.stringify({ path, outcome: result.outcome, durationMs: result.durationMs, requestId: result.requestId })
      })).catch(() => {});
    } catch (_) { /* Diagnostics must never delay or fail the original request. */ }
  };
}
