import test from 'node:test';
import assert from 'node:assert/strict';
import { renderHypothesisStudyReadiness, renderHypothesisQualityReport } from '../../public/modules/experiments/evolution.mjs';

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
