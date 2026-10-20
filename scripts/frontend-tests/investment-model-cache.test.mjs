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

test("model refresh retries after a cold dependency timeout and stops once current", async () => {
  const { modelRefreshDelay, modelRefreshPending } = await import("../../public/modules/decisions/model-refresh.mjs");
  const source = readFileSync(new URL("../../public/modules/decisions/requests.mjs", import.meta.url), "utf8")
    .replace(/^import .*;$/gm, "").replace(/^export .*;$/gm, "");
  const pending = { status: "unavailable", cache: {stale: true, retryAfterSeconds: 20} };
  const ready = { status: "review", cache: {stale: false, retryAfterSeconds: 0} };
  const timers = [];
  const requests = [];
  const state = { investmentModel: pending, investmentModelLoaded: true, investmentModelPollCount: 0 };
  let response = pending;
  const context = vm.createContext({decisionsState: state, shellState: {}, window: {setTimeout: (fn, delay) => timers.push({fn, delay})},
    createLatestRequestLane: () => ({}), isStaticPreviewHost: () => false, render: () => {},
    modelRefreshDelay, modelRefreshPending,
    requestJson: async (path, options) => { requests.push({path, options}); return response; },
  });
  vm.runInContext(source, context);
  await context.loadInvestmentModel(false);
  assert.equal(timers[0].delay, 20000);
  assert.equal(requests[0].path, "/api/investment-model");
  assert.equal(requests[0].options.force, true);
  response = ready;
  await timers[0].fn();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 2);
  assert.equal(timers.length, 1);
  assert.equal(state.investmentModelPollCount, 0);
  assert.equal(state.investmentModel.cache.stale, false);
  state.investmentModelPollCount = 12;
  response = pending;
  await context.loadInvestmentModel(true);
  assert.equal(state.investmentModelPollCount, 1);
  assert.equal(timers.length, 2);
  assert.equal(modelRefreshPending({status: "unavailable", cache: {stale: true, retryAfterSeconds: 0}}), false);
  assert.equal(modelRefreshDelay({status: "warming", cache: {refreshing: true}}), 5000);
  assert.equal(modelRefreshDelay({cache: {retryAfterSeconds: 10}}), 10000);
});

test("capital flow labels a cached portfolio calculation with its actual snapshot clock", () => {
  const source = readFileSync(new URL("../../public/modules/decisions/evidence.mjs", import.meta.url), "utf8")
    .replace(/^import .*;$/gm, "").replace(/^export .*;$/gm, "");
  const context = vm.createContext({ escapeHtml, capitalFlowModel: snapshot => snapshot.capitalFlow });
  vm.runInContext(source, context);
  const html = context.renderInvestmentMoneyFlowPanel({capitalFlow: {status: "ready", readCache: {stale: true},
    sourceSnapshotAt: "2026-10-21T00:00:00Z"}});
  assert.match(html, /이전 집계 · 갱신 대기/);
  assert.match(html, /보유 자료 기준: 2026-10-21T00:00:00Z/);
  assert.doesNotMatch(html, /tone-chip watch/);
  const warming = context.renderInvestmentMoneyFlowPanel({capitalFlow: {status: "warming", readCache: {refreshing: true}}});
  assert.match(warming, /저장된 수급 자료를 불러오고/);
});
