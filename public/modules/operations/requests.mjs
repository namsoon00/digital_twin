import { render } from "../render/scheduler.mjs";
import { activeJsonRequest } from "../requests/active.mjs";
import { requestJson } from "../requests/json.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { operationsState } from "../state/operations.mjs";
import { shellState } from "../state/shell.mjs";

function loadOperationsHealth(force) {
  if (isStaticPreviewHost()) {
    operationsState.operationsHealth = {
      version: "console-read-model-v1",
      state: "unknown",
      generatedAt: (shellState.snapshot || {}).generatedAt || "",
      summary: { unknown: 1 },
      components: [{ id: "preview", label: "정적 미리보기", state: "unknown", detail: "운영 상태는 로컬 앱에서 확인합니다." }],
      providers: [],
      queues: {}
    };
    return Promise.resolve(operationsState.operationsHealth);
  }
  if (operationsState.operationsHealthLoading) return activeJsonRequest("operations-health") || Promise.resolve(operationsState.operationsHealth);
  if (operationsState.operationsHealth && !force) return Promise.resolve(operationsState.operationsHealth);
  operationsState.operationsHealthLoading = true;
  operationsState.operationsHealthError = "";
  return requestJson("/api/operations/health" + (force ? "?refresh=1" : ""), {
    key: "operations-health",
    force: Boolean(force),
    cacheTtlMs: 15000,
    timeoutMs: 15000
  }).then(function (payload) {
    operationsState.operationsHealth = payload || {};
    return operationsState.operationsHealth;
  }).catch(function (error) {
    operationsState.operationsHealthError = error.message || "운영 상태를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    operationsState.operationsHealthLoading = false;
    if (shellState.snapshot) render();
  });
}

function operationsHealthIsStale(maxAgeMs) {
  if (!operationsState.operationsHealth || !operationsState.operationsHealth.generatedAt) return true;
  var generated = new Date(operationsState.operationsHealth.generatedAt).getTime();
  if (!Number.isFinite(generated)) return true;
  return Date.now() - generated > Math.max(30000, Number(maxAgeMs || 120000));
}

export { loadOperationsHealth, operationsHealthIsStale };
