import test from "node:test";
import assert from "node:assert/strict";
import { renderDecisionReview } from "../../public/modules/decisions/case-review.mjs";

test("decision review distinguishes missing data from failure and missing returns from zero", () => {
  const html = renderDecisionReview({
    state: "data-gap", previousSummary: "매출 증가 확인", outcomes: [{
      state: "data-gap", explanation: "필수 자료가 부족해 성공·실패 판정을 보류했습니다.",
      priceChangeFromDecisionPct: 0, benchmarkReturnPct: null,
    }],
  });
  assert.match(html, /자료 보충 대기/);
  assert.match(html, /성공·실패 판정을 보류/);
  assert.match(html, /가격 0%/);
  assert.match(html, /비교 지수 자료 없음/);
  assert.doesNotMatch(html, /NaN|undefined/);
});

test("decision review renders only stored changes and escapes external text", () => {
  const html = renderDecisionReview({state: "pending", previousSummary: '<script>alert(1)</script>',
    verifiedChanges: [{label: "20일선 회복", status: "satisfied"}], nextChecks: ["매출 발표"]});
  assert.match(html, /20일선 회복 · 성립/);
  assert.match(html, /매출 발표/);
  assert.match(html, /관측 대기/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(renderDecisionReview(), /이 판단에 연결된 이전 기록이 없습니다/);
});
