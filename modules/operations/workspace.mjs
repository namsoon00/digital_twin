import { normalizeOperationsView } from "../navigation/routes.mjs";
import { renderConsoleEmpty } from "../shared/console.mjs";
import { operationsState } from "../state/operations.mjs";

function renderOperationsHealthConsole() {
  var workspace = window.OrbitAlphaConsoleWorkspaces;
  var view = normalizeOperationsView(operationsState.activeOperationsView);
  var webPerformance = window.OrbitWebRuntime && typeof window.OrbitWebRuntime.snapshot === "function"
    ? window.OrbitWebRuntime.snapshot()
    : {};
  var body = workspace && typeof workspace.renderOperations === "function"
    ? workspace.renderOperations(operationsState.operationsHealth || {}, view, {
        loading: operationsState.operationsHealthLoading,
        error: operationsState.operationsHealthError,
        webPerformance: webPerformance
      })
    : renderConsoleEmpty("운영 화면을 준비하지 못했습니다", "웹 자산을 새로고침하세요.");
  return '<div class="managed-page oa-console-page oa-console-page-operations" data-console-workspace="operations">' + body + '</div>';
}

export { renderOperationsHealthConsole };
