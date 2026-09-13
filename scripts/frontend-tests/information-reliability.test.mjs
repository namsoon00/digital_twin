import test from "node:test";
import assert from "node:assert/strict";
import { informationSourceUrl, renderResearchInformation } from "../../public/modules/research/information.mjs";
import { releaseValue, renderCalendarReleaseInformation } from "../../public/modules/calendar/results.mjs";

test("facts, opinions and unregistered follow-ups have distinct labels", () => {
  const html = renderResearchInformation({source: "Publisher", informationBrief: {summary: "Summary", facts: [{text: "Source sentence", label: "기사에 기재", sourceUrl: "https://example.org"}], interpretation: "Opinion", independentSourceCount: 0, followUps: [{text: "Check", statusLabel: "자동 관찰 미등록"}]}});
  assert.match(html, /요약 · 분석/);
  assert.match(html, /원문과 대조한 내용/);
  assert.match(html, /의미 · 분석 의견/);
  assert.match(html, /독립 원출처 0곳/);
  assert.match(html, /자동 관찰 미등록/);
  assert.doesNotMatch(html, /검증 완료/);
});

test("unsafe source URLs and markup cannot execute", () => {
  assert.equal(informationSourceUrl("javascript:alert(1)"), "");
  assert.equal(informationSourceUrl("https://u:p@example.org"), "");
  const html = renderResearchInformation({informationBrief: {summary: '<script>alert(1)</script>', sourceUrl: "javascript:alert(1)"}});
  assert.doesNotMatch(html, /<script>|href="javascript/);
  assert.match(html, /&lt;script&gt;/);
});

test("actual zero is preserved and does not become unavailable or consensus", () => {
  assert.equal(releaseValue(0, "%"), "0%");
  assert.equal(releaseValue(null, "%"), "미확보");
  const html = renderCalendarReleaseInformation({releaseInformation: {statusLabel: "공식 발표 결과 확보", release: {metrics: [{label: "CPI", actual: 0, previous: 0.1, unit: "%"}]}, comparison: {reason: "예상치 미확보"}, collection: {state: "disabled"}}});
  assert.match(html, /0%/);
  assert.match(html, /직전 기간 0.1%/);
  assert.match(html, /예상치 미확보/);
  assert.match(html, /자동 수집 꺼짐/);
  assert.doesNotMatch(html, /예상 상회/);
});

test("release failure and absence never render an invented actual", () => {
  const html = renderCalendarReleaseInformation({releaseInformation: {statusLabel: "결과 수집 오류", collection: {state: "error"}}});
  assert.match(html, /결과 수집 오류/);
  assert.doesNotMatch(html, /calendar-release-values/);
});
