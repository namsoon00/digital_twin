import { consoleReadModelAccountId } from "../accounts/identity.mjs";
import { render } from "../render/scheduler.mjs";
import { activeJsonRequest } from "../requests/active.mjs";
import { requestJson } from "../requests/json.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { shellState } from "../state/shell.mjs";

function loadDashboardSummary(force) {
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (shellState.dashboardSummaryLoading) return activeJsonRequest("dashboard-summary") || Promise.resolve(shellState.dashboardSummary);
  if (shellState.dashboardSummary && !force) return Promise.resolve(shellState.dashboardSummary);
  shellState.dashboardSummaryLoading = true;
  shellState.dashboardSummaryError = "";
  var params = new URLSearchParams();
  params.set("accountId", consoleReadModelAccountId());
  if (force) params.set("refresh", "1");
  return requestJson("/api/dashboard/summary?" + params.toString(), {
    key: "dashboard-summary",
    force: Boolean(force),
    cacheTtlMs: 10000,
    timeoutMs: 15000
  }).then(function (payload) {
    shellState.dashboardSummary = payload || {};
    return shellState.dashboardSummary;
  }).catch(function (error) {
    shellState.dashboardSummaryError = error.message || "오늘 요약을 읽지 못했습니다.";
    return null;
  }).finally(function () {
    shellState.dashboardSummaryLoading = false;
    if (shellState.snapshot) render();
  });
}

export { loadDashboardSummary };
