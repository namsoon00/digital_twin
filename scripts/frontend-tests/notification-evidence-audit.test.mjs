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
  const availability = renderRelationChangeEvidence({...packet, transitions: [{changeCategory: "data-availability",
    currentStateLabel: "근거 유지", dataAvailabilityChange: {summary: "자료 복구", reasons: ["<script>source</script>"]}}]});
  assert.match(availability, /자료 상태 · 자료 복구/);
  assert.doesNotMatch(availability, /→ 근거 유지|<script>/);
});

test("relation detail explains model proof and source coverage without losing observed zero", async () => {
  const { renderRelationChangeEvidence } = await import("../../public/modules/notifications/relation-change.mjs");
  const packet = {version: "relation-change-evidence-v1", current: {marketSignalCoverage: {
    investor: {sourceAsOf: "2026-10-02T10:00:00+09:00", measurementType: "intraday-estimate", observedFields: ["institutionNetVolume", "<script>x</script>"]}
  }}, changes: {
    hypotheses: [{id: "h", current: {claim: "verbose old claim", expectedOutcome: "반등이 이어지지 않을 가능성", falsificationContract: "회복 유지", qualification: {reason: "표본 부족"}, claimContract: {outcomeContract: {outcomeHorizonMinutes: [60, 1440], criteria: [{role: "cause", metric: "ma20DistanceChangePp", operator: "<", threshold: 0, horizonMinutes: 0}]}}}}],
    rules: [{id: "r", current: {conditions: [{modelSignalMatched: true, observedValue: {contractMatched: true}, measuredFactIds: ["ma20Distance"],
      sourceFeatureSnapshotId: "source:frozen", knowledgeCutoffAt: "2026-10-02T01:10:00Z", modelEvidenceIds: ["stock:MSTR|HAS_TEMPORAL_WINDOW|window"],
      sourceTemporalWindows: [{windowKey: "3D", startPrice: 153.09, currentPrice: 156.72, priceChangePct: 2.37, priceVelocityChangePct: -7.2,
        hasSufficientHistory: false, knowledgeCutoffAt: "2026-10-02T01:10:00Z", sourceFeatureSnapshotId: "source:frozen", provider: "<script>untrusted</script>"}]
    }]}}],
    facts: [{id: "institutionNetVolume", current: {value: 0}}]
  }};
  const html = renderRelationChangeEvidence(packet);
  for (const value of ["반등이 이어지지 않을 가능성", "회복 유지", "표본 부족", "분석 신호 조건 확인", "자료별 집계 시각", "2026-10-02 10:00:00 KST", "institutionNetVolume", "<td>0</td>"]) assert.ok(html.includes(value), value);
  assert.doesNotMatch(html, /\[object Object\]|verbose old claim|<script>/);
  assert.match(html, /<details><summary>전체 추론 근거/);
  assert.match(html, /원인 확인.*20일 평균 가격과의 거리 변화/);
  assert.doesNotMatch(html, /0분 뒤/);
  for (const value of ["가설에 연결된 기간별 측정값", "source:frozen", "153.09", "156.72", "등락률 변화(%p): -7.2", "기간 자료 충분 여부: false"]) assert.ok(html.includes(value), value);
  const { renderNotificationCustomerDocument } = await import("../../public/modules/notifications/customer-document.mjs");
  const compact = renderNotificationCustomerDocument({customerInvestmentDocument: {role: "typedb-observation", lead: "현재가와 보유 손익을 함께 설명합니다.", sections: [
    {key: "delivery-cause", title: "이번 알림이 온 이유", rows: ["조건 변화", "자료 기준 시각", "비교 불가: <script>source</script>"]},
    {key: "current-price", title: "시세", rows: ["11000원"]},
    {key: "holding", title: "내 보유 상황", rows: ["평균 매입가 10000원 · 평가 수익률 +10%"]},
    {key: "trading", title: "체결", rows: ["체결강도 120", "이전 90 → 이번 120", "체결 표본 5,500주"]},
    {key: "volume", title: "거래량", rows: ["원본 0.55배", "기대 누적 비중 55%", "고정 분포 추정"]},
    {key: "tracking", title: "추적", rows: ["시작 -0.1% → 최근 0.25%", "연속 확인 1/2회", "세 번째 조건 <script>위험</script>"]},
    {key: "hypotheses", title: "회복 가설", rows: ["설명", "규칙", "다음 확인: 회복 유지", "아직 검증 중"]},
    {key: "hypotheses-2", title: "외부 영향 가설", rows: ["설명", "규칙", "다음 확인: 충격 완화", "아직 검증 중"]}
  ]}}, true);
  for (const value of ["11000원", "회복 유지", "충격 완화", "평균 매입가 10000원", "종합해서 보면", "체결강도 120", "체결 표본 5,500주", "고정 분포 추정", "연속 확인 1/2회", "세 번째 조건 &lt;script&gt;"]) assert.ok(compact.includes(value), value);
  assert.equal((compact.match(/아직 검증 중/g) || []).length, 2);
  assert.match(compact, /이번 알림이 온 이유/);
  assert.match(compact, /비교 불가: &lt;script&gt;source/);
});

test("notification clocks remain in Korea across local timezone choices", async () => {
  const { notificationClock } = await import("../../public/modules/notifications/clock.mjs");
  const previous = process.env.TZ;
  try {
    process.env.TZ = "America/New_York";
    for (const stamp of ["2025-12-31T23:30:00Z", "2025-12-31T23:30:00", "2025-12-31T18:30:00-05:00"]) {
      assert.equal(notificationClock(stamp), "2026-01-01 08:30:00 KST");
    }
    assert.equal(notificationClock("2025-12-31"), "2025-12-31 (시각 미기록)");
    assert.equal(notificationClock("unknown"), "unknown");
  } finally { if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous; }
});
