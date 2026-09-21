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
