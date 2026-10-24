import test from "node:test";
import assert from "node:assert/strict";
import { renderWorldStatus } from "../../public/modules/ontology/world-status.mjs";
import { renderEventHistory } from "../../public/modules/research/information.mjs";

test("stale diagnostics and unknown world counts stay explicit", () => {
  const html = renderWorldStatus({observation: {label: "과거 진단 · 현재 상태 미확인", refreshing: true, refreshFailed: true}, worldInventory: {sharedPremiseRole: "offline-reconciliation", worlds: [{worldId: "market:shared:global", worldType: "market", status: "unknown"}]}});
  assert.match(html, /과거 진단/);
  assert.match(html, /저장 범위 미확인/);
  assert.match(html, /최근 갱신 실패/);
  assert.match(html, /실시간 추론의 필수 단계가 아닙니다/);
  assert.doesNotMatch(html, /저장 범위 0개|저장 상태 확인/);
});

test("event history labels correction, retraction and separate clocks safely", () => {
  const html = renderEventHistory({version: "retained-event-history-v1", note: "전체 이력 미확인", entries: [{title: "<script>bad</script>", state: "retracted", publishedAt: "2026-10-24", effectiveAt: "2026-11-01", supersededByEvidenceId: "replacement", conflictingEvidenceIds: ["counter"], url: "javascript:alert(1)"}]});
  assert.match(html, /철회됨/);
  assert.match(html, /수집 미확인/);
  assert.match(html, /효력 발생 2026-11-01/);
  assert.match(html, /대체 근거 replacement/);
  assert.match(html, /상충 근거 counter/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|href="javascript/);
});
