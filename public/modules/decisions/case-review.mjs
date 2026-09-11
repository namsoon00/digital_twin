import { escapeHtml } from "../shared/text.mjs";

function percent(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? (value > 0 ? "+" : "") + value.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "%"
    : "자료 없음";
}

export function renderDecisionReview(review, formatClock = value => String(value || "")) {
  var value = review || {};
  var labels = { evaluated: "결과 확인", "data-gap": "자료 보충 대기", partial: "일부 확인", excluded: "평가 제외", pending: "관측 대기", "not-recorded": "이전 기록 없음" };
  var changes = Array.isArray(value.verifiedChanges) ? value.verifiedChanges : [];
  var outcomes = Array.isArray(value.outcomes) ? value.outcomes : [];
  var checks = Array.isArray(value.nextChecks) ? value.nextChecks : [];
  return '<section class="oa-decision-review" data-review-state="' + escapeHtml(value.state || "not-recorded") + '">' +
    '<header><strong>이전 판단과 검증</strong><span>' + escapeHtml(labels[value.state] || "검증 기록 없음") + '</span></header>' +
    '<dl><div><dt>이전 판단</dt><dd>' + escapeHtml(value.previousSummary || value.claim || "이 판단에 연결된 이전 기록이 없습니다.") +
    (value.previousDecidedAt ? '<time>' + escapeHtml(formatClock(value.previousDecidedAt)) + '</time>' : '') + '</dd></div>' +
    '<div><dt>확인된 변화</dt><dd>' + (changes.length ? '<ul>' + changes.slice(0, 4).map(function (item) {
      return '<li>' + escapeHtml(item.label) + ' · ' + escapeHtml({ satisfied: "성립", invalidated: "무효화", expired: "기한 종료" }[item.status] || "변화 확인") + '</li>';
    }).join("") + '</ul>' : '새로 확인된 후속 조건이 없습니다.') + '</dd></div>' +
    '<div><dt>사후 검증</dt><dd>' + (outcomes.length ? '<ul>' + outcomes.slice(-3).map(function (item) {
      var metrics = ['판단 후 가격 ' + percent(item.priceChangeFromDecisionPct), '비교 지수 ' + percent(item.benchmarkReturnPct)];
      return '<li><p>' + escapeHtml(item.explanation) + '</p><small>' + escapeHtml([item.horizonMinutes ? item.horizonMinutes + '분 관측' : '', formatClock(item.observedAt)].filter(Boolean).join(' · ')) + '</small><small>' + escapeHtml(metrics.join(' · ')) + '</small></li>';
    }).join("") + '</ul>' : (value.state === "pending" ? '정해진 관측 시점의 결과를 기다립니다.' : '저장된 검증 결과가 없습니다.')) + '</dd></div>' +
    '<div><dt>다음 확인</dt><dd>' + (checks.length ? '<ul>' + checks.slice(0, 4).map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') + '</ul>' : '이전 판단에 등록된 대기 조건이 없습니다.') + '</dd></div></dl>' +
    (value.packetId ? '<footer><time>' + escapeHtml(formatClock(value.capturedAt)) + '</time><span>' + escapeHtml(value.interpretation || '') + '</span></footer>' : '') + '</section>';
}
