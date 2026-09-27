import { escapeHtml } from "../shared/text.mjs";

function companyEvidenceArray(value) {
  return Array.isArray(value) ? value : [];
}

function companyEvidenceNumber(value, currency) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean" || !Number.isFinite(Number(value))) return "자료 없음";
  var number = Number(value);
  var unit = currency || "통화 미확인";
  if (currency === "KRW" && Math.abs(number) >= 1e12) { number /= 1e12; unit = "조원"; }
  else if (currency === "KRW" && Math.abs(number) >= 1e8) { number /= 1e8; unit = "억원"; }
  return number.toLocaleString("ko-KR", {maximumFractionDigits: 2}) + (unit ? " " + unit : "");
}

function companyEvidenceLink(url, label) {
  try {
    var parsed = new URL(String(url || ""));
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || /[\s<>]/.test(String(url))) return "";
    return '<a href="' + escapeHtml(parsed.href) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(label) + '</a>';
  } catch (_) { return ""; }
}

function companyEvidenceSources(metric) {
  var rows = companyEvidenceArray(metric.sourceReferences).map(function (source) {
    return '<li>' + escapeHtml([source.datasetId, source.revisionId, source.sourceAsOf].filter(Boolean).join(" · ")) + '</li>';
  }).join("");
  var identity = [metric.provider, metric.sourceMetric, metric.sourceDocumentId].filter(Boolean).join(" · ");
  return '<details class="company-report-provenance"><summary>항목 출처</summary><p>' + escapeHtml(identity) + '</p>' + (rows ? '<ul>' + rows + '</ul>' : '<p>원문 버전 식별자 없음</p>') + companyEvidenceLink(metric.sourceUrl, "재무 공시 원문") + '</details>';
}

function companyEvidenceFinancials(reports) {
  return companyEvidenceArray(reports).map(function (report) {
    var rows = companyEvidenceArray(report.metrics).map(function (metric) {
      var growth = metric.comparisonLabel ? '<small>' + escapeHtml(metric.comparisonLabel) + '</small>' : '';
      return '<tr><th scope="row">' + escapeHtml(metric.label || metric.key) + '</th><td>' + escapeHtml(companyEvidenceNumber(metric.value, metric.currency)) + growth + '</td><td>' + escapeHtml(metric.basisLabel || "기간 기준 미확인") + '</td><td>' + escapeHtml([metric.provider, metric.sourceLabel, metric.derived ? "계산값" : ""].filter(Boolean).join(" · ")) + companyEvidenceSources(metric) + '</td></tr>';
    }).join("");
    var periodLabel = report.period + ' · ' + ({annual: "연간 자료", interim: "중간보고 자료", quarterly: "분기 자료"}[report.frequency] || "보고기간 자료");
    return '<div class="company-report-table-wrap" tabindex="0" role="region" aria-label="' + escapeHtml(periodLabel) + '"><table class="company-report-table"><caption>' + escapeHtml(periodLabel) + '</caption><thead><tr><th scope="col">항목</th><th scope="col">금액</th><th scope="col">기간·회계 기준</th><th scope="col">출처</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  }).join("");
}

function companyEvidenceDocuments(documents) {
  return companyEvidenceArray(documents).map(function (document) {
    return '<article class="company-report-document"><p class="label">' + escapeHtml([document.kind, document.publishedAt].filter(Boolean).join(" · ")) + '</p><h5>' + escapeHtml(document.title) + '</h5><p>' + escapeHtml(document.useLabel) + '</p>' + (document.excerpt && document.bodyVerified ? '<blockquote>' + escapeHtml(document.excerpt) + '</blockquote><p><small>공시·발표 원문 발췌 · 독립적인 검증 결과 아님</small></p>' : '') + companyEvidenceLink(document.url, "공시·IR 원문 보기") + '<details class="company-report-provenance"><summary>문서 식별 정보</summary><p>' + escapeHtml([document.documentId, document.sourceRevision].filter(Boolean).join(" · ")) + '</p></details></article>';
  }).join("");
}

function companyEvidenceCalculation(section, currency) {
  if (!section.calculation && !section.models) return "";
  var calculation = section.calculation || {};
  var model = calculation.model || {};
  var earnings = calculation.earningsScenario || {};
  var multiple = calculation.multipleBand || {};
  var fairValue = calculation.fairValue || {};
  var cells = [
    ["주당이익 가정", earnings, currency], ["적용 PER", multiple, "배"], ["계산 결과 · 목표주가 아님", fairValue, currency]
  ];
  var table = '<div class="company-report-table-wrap"><table class="company-report-table"><caption>저위·기준·고위 계산 조건</caption><thead><tr><th scope="col">항목</th><th scope="col">저위</th><th scope="col">기준</th><th scope="col">고위</th></tr></thead><tbody>' + cells.map(function (cell) {
    return '<tr><th scope="row">' + escapeHtml(cell[0]) + '</th>' + ["low", "base", "high"].map(function (key) { return '<td>' + escapeHtml(companyEvidenceNumber(cell[1][key], cell[2])) + '</td>'; }).join("") + '</tr>';
  }).join("") + '</tbody></table></div>';
  var models = companyEvidenceArray(section.models).map(function (item) {
    return '<p><strong>' + escapeHtml(item.label || "평가 모델") + '</strong> · ' + escapeHtml(item.stateLabel || "검토 상태 미확인") + '<br>참고 계산값 ' + escapeHtml(companyEvidenceNumber(item.fairValue, item.currency || currency)) + '</p>';
  }).join("");
  var assumptions = [
    "계산식: " + (model.formula || "자료 없음"),
    "이익 기준: " + (earnings.period || "미확인") + " · 출처 " + (earnings.providers || []).join(", "),
    "PER 표본 " + (multiple.sampleCount || 0) + "개 · 공급자 " + (multiple.providerCount || 0) + "개 · " + (multiple.evidenceBacked ? "근거 연결됨" : "비교 배수 근거 확인 필요")
  ];
  return '<details class="company-report-calculation"><summary>참고 계산과 가정 펼치기</summary><p>가정 변화에 민감한 계산입니다. 검토 대기·참고 상태의 금액을 투자 판단 기준으로 사용하지 않습니다.</p>' + models + table + '<ul>' + assumptions.map(function (line) { return '<li>' + escapeHtml(line) + '</li>'; }).join("") + '</ul></details>';
}

function renderCompanyEvidenceReport(report) {
  var kindLabel = {baseline: "첫 비교 기준", expanded: "상세 자료 보강", change: "변경 자료 확인", unchanged: "변화 없음"}[report.reportKind] || "기업 보고서";
  var sections = companyEvidenceArray(report.sections).map(function (section, index) {
    var sourceExcerpt = section.sourceExcerpt ? '<details class="company-report-provenance"><summary>' + escapeHtml(section.sourceExcerptLabel || "수집 원문 발췌") + '</summary><p>' + escapeHtml(section.sourceExcerpt) + '</p></details>' : '';
    return '<section class="instrument-valuation-band company-report-section"><div class="instrument-valuation-section-head"><h4>' + escapeHtml(String(index + 1).padStart(2, "0") + ' · ' + section.title) + '</h4></div>' + companyEvidenceArray(section.paragraphs).map(function (paragraph) { return '<p class="instrument-valuation-explanation">' + escapeHtml(paragraph) + '</p>'; }).join("") + (companyEvidenceArray(section.rows).length ? '<ul>' + section.rows.map(function (row) { return '<li>' + escapeHtml(row) + '</li>'; }).join("") + '</ul>' : '') + sourceExcerpt + companyEvidenceFinancials(section.financialReports) + companyEvidenceDocuments(section.documents) + companyEvidenceCalculation(section, report.currency) + '</section>';
  }).join("");
  return '<section class="instrument-valuation-workspace company-change-report"><header><div><span class="label">기업 자료 보고서</span><h3>' + escapeHtml(report.headline || "기업 보고서") + '</h3><p>' + escapeHtml(report.summary) + '</p></div><span class="tone-chip hold">' + escapeHtml(kindLabel) + '</span></header><p class="instrument-valuation-explanation">자료 확인 ' + escapeHtml(report.sourceCutoffDisplay || report.sourceCutoffAt || "시각 미기록") + ' · 종목 ' + escapeHtml(report.symbol) + '</p>' + sections + '<p class="instrument-valuation-explanation">' + escapeHtml(report.boundary) + '</p></section>';
}

export { renderCompanyEvidenceReport };
