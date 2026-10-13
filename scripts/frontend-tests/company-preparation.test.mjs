import test from 'node:test';
import assert from 'node:assert/strict';
import { renderCompanyEvidenceReport } from '../../public/modules/instruments/company-report.mjs';

test('holding and watchlist share escaped preparation details without hiding incomplete stages', () => {
  const html = renderCompanyEvidenceReport({contractVersion: 'company-change-report-v3', sections: [],
    preparation: {status: 'partial', label: '부분 확보', roles: ['holding', 'watchlist'], checkedAt: '2026-10-14',
      stages: [{label: '중앙 AI 최초 분석', status: 'blocked', detail: '<script>검토 미통과</script>'}],
      gaps: ['차입금 <자료> 확인 필요'], researchRequest: {status: 'completed'}, nextCheckAt: '2026-10-15'}});
  assert.match(html, /기업 조사 진행 · 부분 확보/);
  assert.match(html, /보유 · 즐겨찾기/);
  assert.match(html, /중앙 AI 최초 분석<\/strong> · 확인 필요/);
  assert.match(html, /&lt;script&gt;검토 미통과/);
  assert.match(html, /차입금 &lt;자료&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /다음 상태 확인 2026-10-15/);
  assert.match(html, /추가 조사 계획 · 계획 처리 완료/);
  assert.match(html, /계획 처리가 끝나도 자료 확보와 분석은 계속 확인/);
  const failed = renderCompanyEvidenceReport({sections: [], preparation: {status: 'failed'}});
  assert.match(failed, /아래 단계는 마지막 확인 기록/);
});
