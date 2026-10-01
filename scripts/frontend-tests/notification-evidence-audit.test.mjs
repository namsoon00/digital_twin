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
