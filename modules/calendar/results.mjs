import { escapeHtml } from "../shared/text.mjs";
import { informationTime, renderInformationLink, renderInformationReaction } from "../research/information.mjs";

function releaseValue(value, unit) {
  if (value === null || value === undefined) return "미확보";
  if (Array.isArray(value)) return value.map(function (number) { return releaseValue(number, ""); }).join(" ~ ") + unit;
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) + unit : "미확보";
}

function renderCalendarReleaseInformation(event) {
  var info = event.releaseInformation || {};
  var release = info.release || {};
  var metrics = Array.isArray(release.metrics) ? release.metrics : [];
  var collection = info.collection || {};
  var revisions = Array.isArray(release.revisions) ? release.revisions : [];
  var statistics = info.latestStatistics;
  return [
    '<section class="work-detail-section calendar-release-information"><strong>' + escapeHtml(info.statusLabel || "발표 결과 연결 전") + '</strong>',
    release.referencePeriod ? '<p>대상 기간 ' + escapeHtml(release.referencePeriod) + '</p>' : '',
    metrics.length ? '<dl class="calendar-release-values">' + metrics.map(function (metric) {
      return '<div><dt>' + escapeHtml(metric.label) + '</dt><dd>' + escapeHtml(releaseValue(metric.actual, metric.unit || "")) + '</dd>' +
        (metric.previous !== null && metric.previous !== undefined ? '<dd class="subtle">직전 기간 ' + escapeHtml(releaseValue(metric.previous, metric.unit || "")) + '</dd>' : '') + '</div>';
    }).join("") + '</dl>' : '',
    metrics.length ? '<details><summary>수치의 원문 근거</summary>' + metrics.map(function (metric) { return '<p>' + escapeHtml(metric.excerpt) + '</p>'; }).join("") + '</details>' : '',
    release.releasedDate ? '<p>발표 ' + escapeHtml(release.releasedAt ? informationTime(release.releasedAt) : release.releasedDate + " (시각 미확인)") + '<br>최초 수집 ' + escapeHtml(informationTime(release.firstCollectedAt)) + '<br>최근 수집 ' + escapeHtml(informationTime(release.lastCollectedAt)) + '</p>' : '',
    renderInformationLink(release.sourceUrl || event.sourceUrl, release.source || event.source || "공식 출처"),
    statistics ? '<section class="inline-detail-block"><strong>' + escapeHtml(statistics.label) + '</strong><p>대상 기간 ' + escapeHtml(statistics.referencePeriod) + '</p>' +
      (statistics.metrics || []).map(function (metric) { return '<p>' + escapeHtml(metric.label + " " + releaseValue(metric.actual, metric.unit || "")) + '</p>'; }).join("") +
      '<p class="subtle">' + escapeHtml(statistics.note) + '</p><p>조회 ' + escapeHtml(informationTime(statistics.fetchedAt)) +
      (Number.isFinite(statistics.ageHours) ? '<br>수집 후 ' + escapeHtml(statistics.ageHours) + '시간 경과' : '') +
      (statistics.freshnessState === "stale" ? '<br>최근 통계 재확인이 지연되고 있습니다.' : '') + '</p>' + renderInformationLink(statistics.sourceUrl, statistics.source) +
      '<details><summary>통계 원값과 계산식</summary>' + (statistics.metrics || []).map(function (metric) { return '<p>' + escapeHtml(metric.label + ": " + metric.formula) + '<br>' + (metric.inputs || []).map(function (input) { return escapeHtml(input.seriesId + " · " + input.period + " · " + input.value); }).join('<br>') + '</p>'; }).join("") + '</details></section>' : '',
    '<p class="subtle">' + escapeHtml((info.comparison || {}).reason || "예상 대비 평가 미연결") + '</p>',
    renderInformationReaction(info.marketReaction),
    collection.state === "error" ? '<p>결과 수집에 오류가 있습니다. 저장된 발표 결과와 최신 수집 상태는 별개입니다.</p>' : '',
    collection.enabled && collection.nextAttemptAt ? '<p>다음 자동 확인 ' + escapeHtml(informationTime(collection.nextAttemptAt)) + '</p>' : '<p class="subtle">' + escapeHtml(collection.state === "disabled" ? "결과 자동 수집 꺼짐" : "이 일정의 자동 결과 확인 미등록") + '</p>',
    revisions.length > 1 ? '<details><summary>보관된 발표문 변경 이력 · ' + escapeHtml(release.revisionCount) + '개 버전</summary><ol>' + revisions.map(function (revision) {
      return '<li>' + escapeHtml(informationTime(revision.firstCollectedAt)) + '<p>' + (revision.metrics || []).map(function (metric) { return escapeHtml(metric.label + " " + releaseValue(metric.actual, metric.unit || "")); }).join(" · ") + '</p></li>';
    }).join("") + '</ol></details>' : '',
    '</section>'
  ].join("");
}

export { releaseValue, renderCalendarReleaseInformation };
