import test from "node:test";
import assert from "node:assert/strict";
import { renderHypothesisProgress, renderOntologyEvolution } from "../../public/modules/experiments/evolution.mjs";

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

test("hypothesis progress separates contract repair from experiment observations", () => {
  const html = renderHypothesisProgress({progress: {label: "가설 명세 수정 필요", nextAction: "<b>수정</b>",
    authoringAttempts: 4, experimentStarted: false}, retry: {contractRepair: {attemptsUsed: 1, attemptLimit: 1, previousAttempts: 3}}});
  assert.match(html, /아직 시작하지 않음/);
  assert.match(html, /자동 예약 없음/);
  assert.match(html, /기존 3회 기록 유지/);
  assert.doesNotMatch(html, /<b>수정|undefined|NaN/);
  const retired = renderHypothesisProgress({progress: {label: '비교 종료', authoringAttempts: 4,
    experimentStarted: true, experimentStateLabel: '비교 종료 · 미반영'}});
  assert.match(retired, /비교 종료 · 미반영/);
  assert.doesNotMatch(retired, /관측 중/);
  assert.equal(renderHypothesisProgress(), "");
});

test("experiment condition absence is distinct from a broken capture path", () => {
  const fixture = {dataSummary: {inferenceCoverage: {sampledCases: 72, candidateMatches: 0, sampleTruncated: true}},
    details: {observationDeadline: "2026-10-14"}, plan: {observationRequirements: {inputs: []}}};
  const waiting = renderOntologyEvolution({...fixture, reason: "experiment-condition-not-observed-in-sample"});
  assert.match(waiting, /조건이 성립하지 않았습니다/);
  assert.match(waiting, /72 \/ 0건 · 최근 표본만 확인/);
  assert.match(waiting, /관측 종료 예정.*2026-10-14/s);
  assert.match(renderOntologyEvolution({...fixture, reason: "experiment-input-capture-missing"}), /수집 연결을 점검/);
  assert.match(renderOntologyEvolution({...fixture, reason: "experiment-outcome-not-due"}), /예약된 결과 관측 시점/);
  assert.match(renderOntologyEvolution({...fixture, reason: "experiment-outcome-overdue"}), /허용 지연을 지났지만/);
});
