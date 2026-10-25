import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";
import { questionAssessmentView, questionPerformanceView } from "../../public/modules/research/question-assessment.mjs";

const source = fs.readFileSync(new URL("../../public/ai-control.mjs", import.meta.url), "utf8")
  .replace(/^import \{ questionAssessmentView, questionPerformanceView \} from "\.\/modules\/research\/question-assessment\.mjs(?:\?v=[^"]+)?";$/m, "");
function render(input) {
  // Load the rendering functions without starting browser polling or requests.
  return vm.runInNewContext(source.slice(0, source.indexOf("async function request(")) + "\nresearchAnswerView(input)", {input, URL, questionAssessmentView, questionPerformanceView});
}
test("question answers expose original sources, remaining requirements and exploration counts", () => {
  const html = render({documentaryAnswer:{status:"partial",text:"분기 매출은 확인했습니다.",
    missingRequirements:["전년 동기 단위"],blockers:["일부 원문 접근 실패"],
    sources:[{title:"공식 IR",sourceUrl:"https://example.com/results"}]},
    sourceExploration:{searchCount:2,readCount:3,stopReason:"step-budget-exhausted"}});
  for (const text of ["일부 항목 확인", "분기 매출", "전년 동기 단위", "https://example.com/results", "검색 2회", "문서 읽기 3회", "탐색 횟수 소진"]) assert.ok(html.includes(text), text);
  assert.equal(render({}), "");
});
test("external source text is escaped and unsafe citation links are omitted", () => {
  const html = render({documentaryAnswer:{status:"partial",text:"<script>untrusted</script>",
    sources:[{title:"malicious",sourceUrl:"javascript:alert(1)"},
      {title:"secret",sourceUrl:"https://user:password@example.com/"},
      {title:'<img src=x>',sourceUrl:"https://example.com/"}]}});
  assert.doesNotMatch(html, /<script>|<img|javascript:|user:password/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img/);
});
test("the final cited answer replaces earlier exploration gaps, including an empty gap list", () => {
  const old = {missingRequirements:["이미 확인한 매출"],searchCount:1,readCount:1};
  const html = render({documentaryAnswer:{status:"answered",text:"공식 매출을 확인했습니다.",missingRequirements:[]},sourceExploration:old});
  assert.match(html, /자료 답변 확인/);
  assert.doesNotMatch(html, /아직 확인하지 못한 항목|이미 확인한 매출/);
  assert.match(render({sourceExploration:old}), /이미 확인한 매출/);
});
test("recovery history shows causes and measured extraction without declaring an answer complete", () => {
  const html = render({sourceRecovery:{version:"source-recovery-v1",issues:[{label:"접근 거부",host:"example.com",
    explanation:"다른 공식 원문 확인 <script>"}],progress:{measured:true,extractionCount:1,newPassageCount:2}}});
  for (const text of ["접근 거부", "example.com", "다른 공식 원문", "집중 추출 1회", "본문 2개", "답변 완료를 뜻하지 않습니다"]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /<script>|자료 답변 확인/);
  const legacy = render({sourceRecovery:{version:"source-recovery-v1",issues:[],progress:{measured:false,newPassageCount:0}}});
  assert.doesNotMatch(legacy, /본문 0개/);
});
test("a pending collection interval shows its explanation and retry time without a failure label", () => {
  const input = {status:"pending",capability:"research",symbol:"TEST",lastError:"research-cooldown",
    nextCheckAt:"2026-10-11T13:00:00Z",result:{status:"research-wait",reason:"research-cooldown",
      explanation:"자료 수집 간격을 기다린 뒤 같은 질문의 조사를 이어갑니다."}};
  const html = vm.runInNewContext(source.slice(0, source.indexOf("async function load(")) + "\ncard(input)", {input,URL,questionAssessmentView,questionPerformanceView});
  assert.match(html, /같은 질문의 조사를 이어갑니다/);
  assert.match(html, /다음 확인/);
  assert.doesNotMatch(html, /research-cooldown|작업에 실패|답변 근거 부족/);
});
test("source answer completion and further research stay visible when a market draft is rejected", () => {
  const input = {answerResolution:{status:"answered"},questionContract:{target:"현금흐름",period:"2026년 2분기",answerCriterion:"공식 공시"},
    researchPlan:{reason:"남은 자료 확인",resumeWhen:"공식 공시 발행"},reviewBlocker:{errors:["<script>문장 문제</script>"]}};
  const html = vm.runInNewContext(source.slice(0, source.indexOf("async function request(")) + "\nresearchWorkView(input)", {input,URL,questionAssessmentView,questionPerformanceView});
  for (const text of ["원문 조사 평가", "답변 자료 확보", "최종 AI 답변", "2026년 2분기", "공식 공시 발행", "투자 관찰 문장 검토 문제", "별도 상태"]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /<script>/);
});
test("market judgments, question work and receipt-backed delivery remain separate", () => {
  const lane = {capability:"market-observe",authored:0,checksWithoutJudgment:3,notificationProposals:0,
    latestTask:{status:"completed",resultStatus:"unchanged-wait",updatedAt:"2026-10-19T01:00:00Z"},
    latestAlert:{publication:{status:"queued",deliveryStatus:"pending",reason:"<script>waiting</script>"}}};
  const input = {since:"2026-10-18T01:00:00Z",lanes:[lane,
    {capability:"observe",authored:12,internalResearch:12,checksWithoutJudgment:2,latestTask:null}]};
  const renderLanes = () => vm.runInNewContext(source.slice(0, source.indexOf("async function load(")) + "\nlanesView(input)", {input,URL,questionAssessmentView,questionPerformanceView});
  const html = renderLanes();
  for (const text of ["시장 판단 0건", "연구 판단 12건", "반복 호출을 막고", "실제 발송 영수증 미확인", "&lt;script&gt;"]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /실제 발송 확인|<script>/);
  lane.latestAlert.publication.receipt = {deliveredAt:"2026-10-19T02:00:00Z"};
  assert.match(renderLanes(), /실제 발송 확인/);
  assert.doesNotMatch(renderLanes(), /실제 발송 영수증 미확인/);
  const queue = vm.runInNewContext(source.slice(0, source.indexOf("async function load(")) + '\nqueueView({cycle:["market-observe","observe","research"],queues:[]})', {URL,questionAssessmentView,questionPerformanceView});
  assert.match(queue, /시장 관찰 → 질문 연구 → 자료 수집/);
});
