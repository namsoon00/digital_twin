import test from "node:test";
import assert from "node:assert/strict";
import { renderEvidenceValidation, renderDeliveredMessage } from "../../public/modules/notifications/evidence-audit.mjs";

test("rejected claims expose their evidence gap without asserting predictive validation", () => {
  const html = renderEvidenceValidation({evidenceLedger: [{evidenceId: "m", kind: "model-signal", value: "<script>bad</script>", modelEvidenceIds: ["HAS_TEMPORAL_WINDOW:fixture"], sourceFeatureSnapshotId: "f", featureSummary: {measurementBasis: "condition-coverage"}}], validations: [{claimId: "c", status: "rejected", text: "충격 흡수", evidenceIds: ["m"], reasons: ["mechanism-needs-observed-state"]}]});
  assert.match(html, /제외/); assert.match(html, /예측 성과 검증 아님/);
  assert.match(html, /기간별 원본 연결 미확인/); assert.doesNotMatch(html, /<script>/);
});

test("delivery shows receipt-backed text and distinguishes expiration from no history", () => {
  assert.equal(renderDeliveredMessage([{status: "failed", metadata: {renderedMessage: "not delivered"}}]), "");
  assert.match(renderDeliveredMessage([{status: "delivered", metadata: {renderedMessage: "<b>actual</b>"}}]), /&lt;b&gt;actual/);
  assert.match(renderDeliveredMessage([{status: "delivered", metadata: {renderedMessageStatus: "expired"}}]), /보관 기간 종료/);
});

test("relation evidence keeps frozen before/after, missing baseline and escaped proof links", async () => {
  const { renderRelationChangeEvidence } = await import("../../public/modules/notifications/relation-change.mjs");
  assert.equal(renderRelationChangeEvidence({}), "");
  const packet = {version: "relation-change-evidence-v1", baselineAvailable: true, baselineDeliveredAt: "2026-10-01", current: {observedAt: "snapshot-time", sourceAboxSnapshotId: "abox:frozen"}, changes: {
    hypotheses: [{id: "h", change: "changed", previous: {claim: "before"}, current: {claim: "after", ruleIds: ["r1"], counterEvidenceIds: ["counter1"], invalidationConditions: ["price < 10"]}}],
    rules: [{id: "r1", change: "added", current: {label: "<script>rule</script>", matched: true, conditions: [{field: "price", observedValue: 15, expectedValue: 10, operator: ">", evidenceIds: ["fact1"]}]}}],
    facts: [{id: "price", change: "changed", previous: {value: 9}, current: {value: 15}}]
  }};
  const html = renderRelationChangeEvidence(packet);
  for (const value of ["before", "after", "counter1", "fact1", "abox:frozen", "snapshot-time", "마지막 성공 발송"]) assert.ok(html.includes(value));
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(renderRelationChangeEvidence({...packet, baselineAvailable: false}), /이전 발송의 상세 근거가 보존되지/);
});

test("relation detail explains model proof and source coverage without losing observed zero", async () => {
  const { renderRelationChangeEvidence } = await import("../../public/modules/notifications/relation-change.mjs");
  const packet = {version: "relation-change-evidence-v1", current: {marketSignalCoverage: {
    investor: {sourceAsOf: "2026-10-02T10:00:00+09:00", measurementType: "intraday-estimate", observedFields: ["institutionNetVolume", "<script>x</script>"]}
  }}, changes: {
    hypotheses: [{id: "h", current: {claim: "verbose old claim", expectedOutcome: "반등이 이어지지 않을 가능성", falsificationContract: "회복 유지", qualification: {reason: "표본 부족"}, claimContract: {outcomeContract: {outcomeHorizonMinutes: [60, 1440], criteria: [{role: "cause", metric: "ma20DistanceChangePp", operator: "<", threshold: 0, horizonMinutes: 0}]}}}}],
    rules: [{id: "r", current: {conditions: [{modelSignalMatched: true, observedValue: {contractMatched: true}, measuredFactIds: ["ma20Distance"]}]}}],
    facts: [{id: "institutionNetVolume", current: {value: 0}}]
  }};
  const html = renderRelationChangeEvidence(packet);
  for (const value of ["반등이 이어지지 않을 가능성", "회복 유지", "표본 부족", "분석 신호 조건 확인", "자료별 집계 시각", "2026-10-02T10:00:00+09:00", "institutionNetVolume", "<td>0</td>"]) assert.ok(html.includes(value), value);
  assert.doesNotMatch(html, /\[object Object\]|verbose old claim|<script>/);
  assert.match(html, /<details><summary>전체 추론 근거/);
  assert.match(html, /원인 확인.*20일 평균 가격과의 거리 변화/);
  assert.doesNotMatch(html, /0분 뒤/);
  const { renderNotificationCustomerDocument } = await import("../../public/modules/notifications/customer-document.mjs");
  const compact = renderNotificationCustomerDocument({customerInvestmentDocument: {role: "typedb-observation", sections: [
    {key: "current-price", title: "시세", rows: ["11000원"]},
    {key: "hypotheses", title: "회복 가설", rows: ["설명", "규칙", "다음 확인: 회복 유지", "아직 검증 중"]},
    {key: "hypotheses-2", title: "외부 영향 가설", rows: ["설명", "규칙", "다음 확인: 충격 완화", "아직 검증 중"]}
  ]}}, true);
  for (const value of ["11000원", "회복 유지", "충격 완화"]) assert.ok(compact.includes(value), value);
  assert.equal((compact.match(/아직 검증 중/g) || []).length, 2);
});
