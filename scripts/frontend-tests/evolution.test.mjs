import test from "node:test";
import assert from "node:assert/strict";
import { renderOntologyEvolution } from "../../public/modules/experiments/evolution.mjs";

test("evolution shows pending evidence rather than invented performance", () => {
  const html = renderOntologyEvolution({reason: "independent-outcomes-required", plan: {
    policy: {mode: "automatic", minimumIndependentPairs: 20, minimumDistinctDays: 5},
    createdAt: "2026-09-13T00:00:00Z", baseline: {deploymentId: "baseline"},
  }});
  assert.match(html, /독립된 결과가 더 필요/);
  assert.match(html, /0 \/ 20건/);
  assert.match(html, /반영 전/);
  assert.doesNotMatch(html, /NaN|undefined|성공률/);
  assert.equal(renderOntologyEvolution(), "");
});

test("evolution renders stored comparison and escapes model supplied data", () => {
  const html = renderOntologyEvolution({reason: "not-better-than-baseline", assessment: {
    independentPairCount: 20, pairedGainCount: 2, pairedLossCount: 5,
  }, details: {error: "<script>invalid</script>"}, plan: {
    policy: {mode: "shadow"}, baseline: {deploymentId: "<img src=x>"},
    validationRequirements: [{check: "review"}],
  }});
  assert.match(html, /기존 가설보다 나아지지 않아/);
  assert.match(html, /2 \/ 5/);
  assert.match(html, /별도 연구 조건/);
  assert.doesNotMatch(html, /<script>|<img/);
});

test("observation needs distinguish future collection from unsupported sources", () => {
  const html = renderOntologyEvolution({reason: "observation-unsupported", dataSummary: {capturedInputs: 2, unavailableInputs: 1},
    dataReadiness: {requirements: [{metric: "new-source", lookbackMinutes: 60, state: "unsupported"}]},
    plan: {policy: {mode: "automatic", experimentEvidenceRetentionDays: 7}, observationRequirements: {
      inputs: [{metric: "new-source", label: "<unsafe>새 관측", lookbackMinutes: 60, minimumSamples: 12, cadenceSeconds: 300}],
    }}});
  assert.match(html, /수집 기능을 먼저 보완/);
  assert.match(html, /직전 60분 · 최소 12건 · 관측 간격 300초/);
  assert.match(html, /실험 종료 후 자료 보관.*7일/s);
  assert.match(html, /2건/);
  assert.doesNotMatch(html, /<unsafe>|undefined|NaN/);
});
