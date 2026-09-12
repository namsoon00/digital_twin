import test from "node:test";
import assert from "node:assert/strict";
import { investmentBrief, renderInvestmentBrief } from "../../public/modules/decisions/brief.mjs";
import { opinionRecency, decisionInView } from "../../public/modules/decisions/recency.mjs";
import { evidenceSummary, evidenceResolutionLabel } from "../../public/modules/decisions/evidence-summary.mjs";
import { notificationEventSummary } from "../../public/modules/notifications/summary.mjs";
import { renderSecondaryDisclosure } from "../../public/modules/shared/disclosure.mjs";

test("old opinions remain in history and recheck, never current tasks or recent changes forever", () => {
  const item = {updatedAt: "2026-08-18T10:00:00Z", changeState: "changed", userReviewable: true};
  const now = Date.parse("2026-09-12T00:00:00Z");
  const row = {...item, recency: opinionRecency(item, now)};
  assert.equal(row.recency.state, "review");
  assert.equal(decisionInView(row, "attention", now), false);
  assert.equal(decisionInView(row, "recent", now), false);
  assert.equal(decisionInView(row, "review", now), true);
  assert.equal(decisionInView(row, "all", now), true);
  assert.equal(item.updatedAt, "2026-08-18T10:00:00Z");
  assert.equal(opinionRecency({}, now).state, "unknown");
  assert.equal(opinionRecency({...item, lastVerifiedAt: "2026-09-12T00:00:00Z"}, now).state, "current");
});

test("brief preserves important limitations and does not create an investment action", () => {
  const detail = {headline: "자료 확인 중", decision: {state: "blocked"},
    explanation: {primaryCause: {summary: "자료 확인 중"}, constraints: [{summary: "매출 자료 부족"}]},
    statusDimensions: [{id: "data", state: "warning", reason: "매출 자료 부족"}]};
  const brief = investmentBrief(detail);
  assert.deepEqual(brief.limitations, ["매출 자료 부족"]);
  assert.deepEqual(brief.reasons, []);
  assert.deepEqual(brief.next, []);
  assert.equal(brief.limited, true);
  assert.equal(brief.action, undefined);
});

test("current review and recheck views are disjoint and blocked opinions are never actionable", () => {
  const current = {recency: {state: "current"}, userReviewable: true};
  assert.equal(decisionInView(current, "attention", Date.now()), true);
  assert.equal(decisionInView(current, "review", Date.now()), false);
  const blocked = {...current, blocked: true, userActionable: true};
  assert.equal(decisionInView(blocked, "attention", Date.now()), false);
  assert.equal(decisionInView(blocked, "action", Date.now()), false);
  assert.equal(decisionInView(blocked, "review", Date.now()), true);
});

test("unvalidated AI assessment is excluded from the brief", () => {
  const brief = investmentBrief({reasoningLineage: {ai: {status: "contract-failed", insightAssessment: {
    publishable: true, causalMechanism: "must not publish", invalidationCondition: "must not publish"
  }}}});
  assert.doesNotMatch(JSON.stringify(brief), /must not publish/);
});

test("brief translates internal storage terms without rewriting the stored reasoning", () => {
  const detail = {headline: "TypeDB 추론 완료", explanation: {dataGaps: [{detail: "ABox 스냅샷 확인 필요"}]}};
  const brief = investmentBrief(detail);
  assert.equal(brief.headline, "종목 관계 분석 완료");
  assert.deepEqual(brief.limitations, ["판단 당시 자료 확인 필요"]);
  assert.equal(detail.headline, "TypeDB 추론 완료");
});

test("brief escapes external text and preserves dates and source warnings", () => {
  const html = renderInvestmentBrief({headline: '<script>alert(1)</script>', updatedAt: "2026-09-12T00:00:00Z"}, 'x" onclick="bad', "의견 없음", value => value);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>| onclick="bad/);
  assert.match(html, /2026-09-12T00:00:00Z/);
  assert.match(html, /자료 기준.*확인 필요/);
  assert.match(html, /data-investment-case-tab="evidence"/);
});

test("unknown evidence counts are not zero and lineage is not original source resolution", () => {
  assert.deepEqual(evidenceSummary({}), {support: null, counter: null, missing: null, originals: 0, lineage: 0, records: 0});
  const summary = evidenceSummary({evidence: {resolvedCount: 99, records: [{resolutionState: "lineage-linked"}, {resolutionState: "resolved"}]}, explanation: {supportingCauses: [{summary: "basis"}], counterCauses: []}});
  assert.equal(summary.support, 1);
  assert.equal(summary.counter, 0);
  assert.equal(summary.originals, 1);
  assert.equal(summary.lineage, 1);
  assert.equal(evidenceResolutionLabel("lineage-linked"), "추론 계보 연결");
  assert.equal(evidenceSummary({evidence: {missingCount: null}}).missing, null);
  assert.equal(evidenceSummary({evidence: {missingCount: 0}}).missing, 0);
});

test("notification explanation never uses quiet hours or transport failure as investment reasoning", () => {
  const job = {suppressionSummary: "22:00 quiet hours", deliveryReasons: ["transport failed"],
    customerInvestmentDocument: {headline: "검증용 변화", sections: [
      {key: "change", rows: ["수요 전망 변경"]}, {key: "importance", rows: ["매출 가정 재검토"]}
    ]}};
  assert.deepEqual(notificationEventSummary(job), {title: "검증용 변화", change: "수요 전망 변경", reason: "매출 가정 재검토", reasonLabel: "이유"});
  delete job.customerInvestmentDocument;
  assert.doesNotMatch(notificationEventSummary(job).reason, /quiet hours|transport failed/);
});

test("lightweight alert summaries and legacy previews remain visible and distinct", () => {
  const projected = notificationEventSummary({investmentSummary: {headline: "수요 변경", reason: "매출 확인 필요"}});
  assert.equal(projected.reason, "매출 확인 필요");
  assert.equal(projected.reasonLabel, "이유");
  const legacy = notificationEventSummary({textPreview: "저장된 과거 알림", suppressionSummary: "quiet hours"});
  assert.equal(legacy.reason, "저장된 과거 알림");
  assert.equal(legacy.reasonLabel, "본문 미리보기");
});

test("secondary disclosure has a stable identity and defaults closed", () => {
  const html = renderSecondaryDisclosure("test-metrics", "현황", "<p>detail</p>");
  assert.match(html, /id="disclosure-test-metrics"/);
  assert.doesNotMatch(html, / open[ >]/);
  assert.match(html, /<summary>/);
});
