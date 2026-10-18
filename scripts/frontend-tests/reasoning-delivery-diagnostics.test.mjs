import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

test("operations shows inference delay and candidate rollout separately from actual delivery", () => {
  const context = vm.createContext({ window: {} });
  vm.runInContext(readFileSync(new URL("../../public/console-workspaces.js", import.meta.url), "utf8"), context);
  const payload = { version: "test", generatedAt: new Date().toISOString(), queues: { engine: {
    deliveryDiagnostics: { checks: [
      { label: "추론 처리 지연", detail: "운영 대기 4건", status: "warning" },
      { label: "새 규칙 검증 중 · 고객 발송 미적용", detail: "<script>unsafe</script>", status: "info" },
    ] },
  } } };
  const html = context.window.OrbitAlphaConsoleWorkspaces.renderOperations(payload, "reasoning");
  assert.match(html, /추론 처리 지연/);
  assert.match(html, /고객 발송 미적용/);
  assert.match(html, /cws-delivery-diagnostics/);
  assert.match(html, /<span class="cws-health-dot" aria-hidden="true"><\/span><div><strong>추론 처리 지연/);
  assert.match(html, /data-operations-view="delivery"/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>unsafe/);
  delete payload.queues.engine.deliveryDiagnostics;
  assert.match(context.window.OrbitAlphaConsoleWorkspaces.renderOperations(payload, "reasoning"), /추론 진단 정보 미확인/);
});
