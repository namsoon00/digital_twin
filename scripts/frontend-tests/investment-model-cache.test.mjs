import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { escapeHtml } from "../../public/modules/shared/text.mjs";

function render(payload) {
  const source = readFileSync(new URL("../../public/modules/decisions/model.mjs", import.meta.url), "utf8")
    .replace(/^import .*;$/gm, "").replace(/^export .*;$/gm, "");
  const context = vm.createContext({
    decisionsState: { investmentModel: payload, investmentModelLoaded: true },
    escapeHtml, renderWorkDetailButton: () => "", formatClock: value => value,
  });
  vm.runInContext(source, context);
  return context.renderInvestmentModelOverview(true);
}

test("cached model remains readable with an explicit pending verification notice", () => {
  const html = render({ status: "warming", activeRelease: { deploymentId: "current-release" },
    cache: { stale: true, refreshing: true, lastSuccessAt: "2026-10-21T00:00:00Z" },
    validation: { promotionReady: false, blockers: ["read-model-refresh-pending"] },
    productReadiness: { releaseRecommended: false, stageLabel: "검증 자료 확인 중" },
  });
  assert.match(html, /current-release/);
  assert.match(html, /최신 검증 자료를 갱신/);
  assert.match(html, /마지막 성공 조회: 2026-10-21/);
  assert.doesNotMatch(html, /aria-busy="true"|현재 릴리스는 운영 구조 검증을 통과/);
});

test("first model load is warming and later dependency failures are escaped", () => {
  assert.match(render({ status: "warming", cache: {} }), /aria-busy="true"/);
  const html = render({status: "unavailable", cache: {stale: true, lastError: "<script>offline</script>"},
    validation: {blockers: ["read-model-unavailable"]}});
  assert.match(html, /최신 검증 자료 확인이 필요/);
  assert.match(html, /&lt;script&gt;offline/);
  assert.doesNotMatch(html, /<script>/);
});
