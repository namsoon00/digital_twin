import test from 'node:test';
import assert from 'node:assert/strict';
import { renderHypothesisStudyReadiness, renderHypothesisQualityReport } from '../../public/modules/experiments/evolution.mjs';
import { questionAssessmentView } from '../../public/modules/research/question-assessment.mjs';

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
