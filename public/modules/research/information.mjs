import { escapeHtml } from "../shared/text.mjs";

function informationSourceUrl(value) {
  try {
    var url = new URL(String(value || ""));
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : "";
  } catch (_) { return ""; }
}

function informationTime(value) {
  var date = new Date(value || "");
  return Number.isFinite(date.getTime()) ? date.toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hour12: false }) + " KST" : "미확인";
}

function renderInformationLink(url, label) {
  var safe = informationSourceUrl(url);
  return safe ? '<a class="information-source-link" href="' + escapeHtml(safe) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(label || "원문") + '</a>' : '';
}

function renderResearchInformation(item, options) {
  options = options || {};
  var brief = item.informationBrief || {};
  var facts = Array.isArray(brief.facts) ? brief.facts : [];
  var followUps = Array.isArray(brief.followUps) ? brief.followUps : [];
  var timeline = Array.isArray(item.storyTimeline) ? item.storyTimeline : [];
  var warnings = Array.isArray(brief.warnings) ? brief.warnings : [];
  var freshness = (item.promptEvidenceAdmission || {}).freshnessState;
  var freshnessLabel = { fresh: "활용 기한 이내", stale: "활용 기한 경과", future: "미래 시각 오류", unknown: "시점 미확인", "missing-timestamp": "시점 미확인" }[freshness] || "시점 기준 확인 필요";
  return [
    '<div class="information-brief">',
    '<section class="inline-detail-block">',
    options.showTitle !== false ? '<strong>' + escapeHtml(item.translatedTitleKo || item.title || "제목 미확인") + '</strong>' : '',
    item.translatedTitleKo && item.translatedTitleKo !== item.title ? '<p class="subtle">원제 ' + escapeHtml(item.originalTitle || item.title || "") + '</p>' : '',
    '</section>',
    '<section class="inline-detail-block primary"><strong>' + (brief.summaryRole === "metadata" ? "접수 정보 · 본문 미확보" : "요약 · 분석") + '</strong><p>' + escapeHtml(brief.summary || "현재 원문에 대응하는 요약이 없습니다.") + '</p></section>',
    '<section class="inline-detail-block"><strong>원문과 대조한 내용</strong>',
    facts.length ? '<ul>' + facts.map(function (fact) {
      return '<li><span class="subtle">' + escapeHtml(fact.label) + '</span><p>' + escapeHtml(fact.text) + '</p>' + renderInformationLink(fact.sourceUrl, "근거 원문") + '</li>';
    }).join("") + '</ul>' : '<p>본문에서 대조한 문장이 없습니다.</p>',
    '</section>',
    brief.interpretation ? '<section class="inline-detail-block"><strong>의미 · 분석 의견</strong><p>' + escapeHtml(brief.interpretation) + '</p></section>' : '',
    followUps.length ? '<section class="inline-detail-block"><strong>후속 확인 과제</strong><ul>' + followUps.map(function (entry) {
      return '<li>' + escapeHtml(entry.text) + '<p class="subtle">' + escapeHtml(entry.statusLabel) + '</p></li>';
    }).join("") + '</ul></section>' : '',
    warnings.length ? '<section class="inline-detail-block"><strong>자료 확인 사항</strong><ul>' + warnings.map(function (warning) { return '<li>' + escapeHtml(warning) + '</li>'; }).join("") + '</ul></section>' : '',
    '<section class="inline-detail-block"><strong>출처와 시점</strong>',
    '<p>' + escapeHtml(item.source || "출처 미확인") + ' · 독립 원출처 ' + escapeHtml(brief.independentSourceCount || 0) + '곳</p>',
    '<p>신선도 ' + escapeHtml(freshnessLabel) + (brief.sourceAgeHours != null ? ' · 발행 후 ' + escapeHtml(brief.sourceAgeHours) + '시간' : '') + '</p>',
    '<p>발행 ' + escapeHtml(informationTime(brief.publishedAt)) + '<br>수집 ' + escapeHtml(informationTime(brief.collectedAt)) + '</p>',
    renderInformationLink(brief.sourceUrl, "원문 열기"), '</section>',
    timeline.length > 1 ? '<section class="inline-detail-block"><strong>같은 사건의 보도 이력</strong><ol>' + timeline.map(function (entry) {
      return '<li>' + escapeHtml(entry.title || "제목 없음") + '<p class="subtle">' + escapeHtml(entry.source) + ' · ' + escapeHtml(informationTime(entry.publishedAt)) + ' · ' + escapeHtml(entry.state === "active" ? "현재 자료" : "과거 자료") + '</p>' + renderInformationLink(entry.url, "해당 원문") + '</li>';
    }).join("") + '</ol></section>' : '',
    '<details class="inline-detail-block"><summary>근거 추적</summary><p>' + escapeHtml(brief.sourceRevision || brief.sourceHash || "원문 버전 미확인") + '</p><p>' + escapeHtml((brief.marketReaction || {}).label || "시장 반응 미연결") + '</p></details>',
    '</div>'
  ].join("");
}

export { informationSourceUrl, informationTime, renderInformationLink, renderResearchInformation };
