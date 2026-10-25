import test from 'node:test';
import assert from 'node:assert/strict';
import { renderHypothesisStudyReadiness, renderHypothesisQualityReport } from '../../public/modules/experiments/evolution.mjs';
import { questionAssessmentView } from '../../public/modules/research/question-assessment.mjs';

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
