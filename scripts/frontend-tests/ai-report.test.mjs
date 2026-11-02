import assert from "node:assert/strict";
import test from "node:test";
import { renderAIReport, deliveryLabel } from "../../public/modules/reports/ai-report.mjs";

test("report retains dated partial answers, missing requirements and safe links", () => {
  const report = {title: "보고서", createdAt: "2026-11-02T05:00:00Z", snapshotAt: "2026-11-01T05:00:00Z", boundary: "과거 의견",
    reportId: "frozen", contentHash: "hash", coverage: {subjects: 1, withCompanySources: 0, withOpinion: 0, reviewedAnswers: 1},
    subjects: [{name: "기업<script>alert(1)</script>", symbol: "EXAMPLE", scope: "holding", opinion: null, companyReport: {},
      coverage: {}, openQuestions: [], answers: [{question: "질문", answer: "부분적으로 확인", answerStatus: "partial",
        at: "2026-10-01T05:00:00Z", missingRequirements: ["비용 원문 없음"], judgmentImpact: {reason: "수익성은 아직 미확인"},
        sources: [{title: "원문", sourceUrl: "javascript:alert(1)"}]}]}]};
  const html = renderAIReport(report);
  assert.match(html, /부분 답변/); assert.match(html, /비용 원문 없음/); assert.match(html, /수익성은 아직 미확인/);
  assert.match(html, /2026\. 10\. 01/); assert.match(html, /기업&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|href="javascript:/);
  assert.match(html, /이번 조회 범위/);
});

test("delivery completion is shown only with verified receipt", () => {
  assert.doesNotMatch(deliveryLabel({status: "done"}), /전송 확인/);
  assert.doesNotMatch(deliveryLabel({status: "delivered", receiptVerified: false, deliveryRoute: "account"}), /전송 확인/);
  assert.doesNotMatch(deliveryLabel({status: "delivered", receiptVerified: true, deliveryRoute: "ai-research"}), /전송 확인/);
  assert.match(deliveryLabel({status: "other-channel"}), /연구 봇으로만/);
  assert.match(deliveryLabel({status: "delivered", receiptVerified: true, deliveryRoute: "account", deliveredAt: "2026-11-02T05:00:00Z"}), /고객 채널 전송 확인.*서버 접수/);
});

test("whole portfolio report renders every subject, scoped gaps and escaped citations", () => {
  const dims = ["business", "value", "price", "external", "portfolio", "research", "risks"];
  const claim = {text: "아직 확인할 자료가 부족합니다.", status: "unknown", missing: ["실적 원문"], refs: []};
  const report = {version: "ai-portfolio-report-v2", title: "전체 보고서", createdAt: "2026-11-02T05:00:00Z", snapshotAt: "2026-11-02T01:00:00Z", analysisStartedAt: "2026-11-02T04:00:00Z",
    boundary: "확보 자료의 범위", reportId: "frozen", contentHash: "hash", coverage: {subjects: 13, withReviewedAnalysis: 0, withGaps: 13, sourcesRead: 20, sourcesAvailable: 50},
    overall: Object.fromEntries(["overview", "market", "portfolio", "priorities"].map(k => [k, claim])), overallSources: {},
    subjects: Array.from({length: 13}, (_, i) => ({name: "기업<script>" + i, symbol: "S" + i, scope: "watchlist", analyzedAt: "2026-11-02T04:00:00Z",
      claims: Object.fromEntries(dims.map(k => [k, claim])), sources: {}, sourceCoverage: {available: 5, read: 2, graphLimitation: "missing"}, openQuestions: []}))};
  const html = renderAIReport(report);
  for (let i = 0; i < 13; i++) assert.match(html, new RegExp(`id="subject-${i}"`));
  assert.match(html, /전체 종합/); assert.match(html, /실적 원문/); assert.match(html, /20 \/ 50/);
  assert.doesNotMatch(html, /<script>/);
});
