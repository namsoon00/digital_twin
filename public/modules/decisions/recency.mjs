function opinionRecency(item, now, windowHours) {
  item = item || {};
  var verifiedAt = item.lastVerifiedAt || (item.freshness || {}).lastVerifiedAt;
  var at = verifiedAt || item.updatedAt || item.decidedAt || "";
  var timestamp = Date.parse(at);
  var hours = Math.max(1, Number(windowHours || 96));
  if (!Number.isFinite(timestamp)) return { state: "unknown", label: "기준 시각 확인 필요", at: "" };
  // An old opinion is not invalidated or rewritten; it leaves the current-review queue.
  if (timestamp < Number(now) - hours * 3600000) return { state: "review", label: "이전 의견 · 재확인 필요", at: at };
  return { state: "current", label: verifiedAt ? "재확인됨" : "최근 기록", at: at };
}

function decisionInView(row, view, now) {
  var current = !row.recency || row.recency.state === "current";
  var attention = Boolean(row.userActionable || row.userReviewable || row.attentionState === "review");
  if (view === "attention") return current && !row.blocked && attention;
  if (view === "action") return current && !row.blocked && Boolean(row.userActionable);
  if (view === "review") return !current || Boolean(row.blocked);
  if (view === "recent") {
    var at = Date.parse(row.updatedAt || "");
    return Number.isFinite(at) && at >= Number(now) - 7 * 86400000;
  }
  return true;
}

export { decisionInView, opinionRecency };
