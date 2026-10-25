import test from 'node:test';
import assert from 'node:assert/strict';
import { renderHypothesisStudyReadiness, renderHypothesisQualityReport } from '../../public/modules/experiments/evolution.mjs';
import { questionAssessmentView, questionPerformanceView } from '../../public/modules/research/question-assessment.mjs';

test('question progress distinguishes provider quota and internal budget without claiming delivery', () => {
  const workflow = {stages:[{label:'자료 확보',detail:'질문에 연결된 근거 1건'},
    {label:'질문 답변',detail:'부분 답변 보존 · 남은 항목 확인 필요'}],
    wait:{label:'AI 제공처 사용 한도 대기',scope:'model-provider',explanation:'제공처 한도 초과 <script>',nextCheckAt:'2026-10-25T10:00:00Z'},
    publication:{status:'done',jobId:'job'}};
  const html = questionAssessmentView({workflow});
  for (const text of ['부분 답변 보존', 'AI 제공처 사용 한도', '한도 초기화 시각은 미확인', '발송 영수증 미확인', 'KST']) assert.ok(html.includes(text), text);
  assert.match(html, /19(?:시|:)/);
  assert.doesNotMatch(html, /실제 발송 확인|<script>/);
  workflow.publication.receipt = {source:'transport-receipt',deliveredAt:'2026-10-25T10:00:00Z'};
  assert.match(questionAssessmentView({workflow}), /실제 발송 확인/);
  workflow.wait = {label:'서비스 내부 토큰 예산 대기',scope:'project-budget',explanation:'내부 배정 대기'};
  const internal = questionAssessmentView({workflow});
  assert.match(internal, /서비스 내부 토큰 예산/);
  assert.doesNotMatch(internal, /AI 제공처 사용 한도|한도 초기화 시각은 미확인/);
});

test('research readiness distinguishes qualification, deployment, study window and missing data', () => {
  const html = renderHypothesisStudyReadiness({category:'validation-required', study:{minimumAcquisitionDays:1800, minimumIndependentObservations:20, minimumRetentionDays:3608, gaps:[{reason:'<missing evidence>'}]}});
  assert.match(html, /성과상 사용 자격/);
  assert.match(html, /운영 배포/);
  assert.match(html, /1800일/);
  assert.match(html, /&lt;missing evidence&gt;/);
  assert.match(html, /AI 설명/);
});

test('unlabelled narrative and delivery quality remain unmeasured', () => {
  const html = renderHypothesisQualityReport({episodeCount:2, assistantQuality:{metrics:{sourceTraceRate:1}, labelledEpisodeCount:1, unlabelledEpisodeCount:1}, investmentInsightPerformance:{outcomeCount:3}});
  assert.match(html, /미측정/);
  assert.match(html, /최대 500건/);
  assert.match(html, /실제 체결 수익/);
  assert.match(html, /100%/);
});

test('question progress shows the original answer, missing evidence and authored impact without unsafe source links', () => {
  const html = questionAssessmentView({answerResolution:{status:'answered'}, questionAssessment:{status:'partial', answer:'확인한 부분 답변', taskId:'linked-judgment',
    missingRequirements:['<원래 기간의 현금흐름>'], judgmentImpact:{effect:'insufficient', reason:'현금 회수를 판단할 자료가 부족합니다.'},
    judgment:{summary:'근거를 확인한 설명', hypothesis:'가능한 설명',counterEvidence:'반대 근거'},
    sources:[{sourceUrl:'javascript:alert(1)', title:'unsafe'}, {sourceUrl:'https://www.sec.gov/filing', title:'공식 원문'},
      {sourceUrl:'', title:'TSLA <연간 구조화 보고>', periodEnd:'2025-12-31'}]}});
  assert.match(html, /원래 질문의 부분 답변/);
  assert.match(html, /최종 검토/);
  assert.match(html, /설명을 바꿀 근거 부족/);
  assert.match(html, /&lt;원래 기간의 현금흐름&gt;/);
  assert.match(html, /data-judgment-task="linked-judgment"/);
  assert.match(html, /https:\/\/www.sec.gov\/filing/);
  assert.match(html, /TSLA &lt;연간 구조화 보고&gt; · 보고 기간 말 2025-12-31 · 원문 링크 미제공/);
  assert.doesNotMatch(html, /javascript:|unsafe/);
});

test('question closure and sample outcomes explain remaining work without claiming accuracy', () => {
  const html = questionAssessmentView({workflow:{closure:{nextStep:'기존 작업에서 이어서 처리',
    resumeWhen:'공식 보고서 갱신', missingRequirements:['현금흐름 <script>']}}});
  assert.match(html, /재개 조건/); assert.match(html, /현금흐름 &lt;script&gt;/);
  const metrics = questionPerformanceView({sampleCount:11,finalAnswers:0,reviewedJudgmentLinks:0,
    partialAnswersRetained:8,judgmentSupported:0,judgmentChanged:0,judgmentUnchanged:0,
    judgmentInsufficient:0,providerWaiting:11,receiptVerifiedNotifications:0});
  assert.match(metrics, /부분 답변 보존 8건/); assert.match(metrics, /정확도·수익률은 미측정/);
});

test('confirmed answer and missing original remain visible while repeat research is stopped', () => {
  const html = questionAssessmentView({workflow:{judgmentTaskId:'reviewed',stages:[],closure:{
    answerState:'confirmed-parts',execution:{code:'limit-reached',label:'자동 조사 한도 도달'},
    confirmedParts:[{text:'해당 기간의 매출을 원문에서 확인했습니다. <script>'}],
    gapDetails:[{requirement:'현금흐름표',role:'essential',causeLabel:'필요한 원문 미확보',nextAction:'같은 보고기간의 원문 표를 읽습니다.'},
      {requirement:'추가 산업 비교',role:'supplementary',causeLabel:'공개 자료에서 확인하지 못함',nextAction:'업종 보고서가 갱신되면 확인합니다.'}],
    nextStep:'자동 조사 한도 도달 · 조사 범위 검토 필요'}},questionAssessment:{taskId:'reviewed',status:'partial',
      answer:'기존 AI 검토 기록',sources:[],missingRequirements:['현금흐름표']}});
  for (const phrase of ['확인한 답변','자동 조사 한도 도달','답변 완료에 필요한 항목','필요한 원문 미확보','보강 조사 항목','다음 확인:']) assert.ok(html.includes(phrase),phrase);
  assert.match(html,/&lt;script&gt;/); assert.doesNotMatch(html,/<script>/);
  assert.equal(html.split('현금흐름표').length - 1,1);
  assert.doesNotMatch(html,/원래 질문에 대한 AI 답변/);
});

test('unverified final assessment cannot replace retained source answer in the workflow', () => {
  const html = questionAssessmentView({workflow:{closure:{recordedAnswer:'앞서 확보한 답변입니다.',answerAuthority:'source-answer',
    execution:{code:'blocked',label:'조사 진행 불가 · 원인 확인 필요'}}},
    questionAssessment:{status:'answered',answer:'연결이 검증되지 않은 완료 주장'}});
  assert.match(html,/앞서 확보한 답변입니다/);
  assert.match(html,/최종 검토 전/);
  assert.match(html,/조사 진행 불가/);
  assert.doesNotMatch(html,/연결이 검증되지 않은 완료 주장/);
});
