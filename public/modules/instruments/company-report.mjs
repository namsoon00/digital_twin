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
    return '<article class="company-report-document"><p class="label">' + escapeHtml([document.kind, document.publishedAt].filter(Boolean).join(" · ")) + '</p><h5>' + escapeHtml(document.title) + '</h5><p>' + escapeHtml(document.useLabel) + '</p>' + (document.excerpt && document.bodyVerified ? '<blockquote>' + escapeHtml(document.excerpt) + '</blockquote><p><small>공시·발표 원문 발췌 · 독립적인 검증 결과 아님</small></p>' : '') + (document.bodyVerified ? companyEvidenceArray(document.passages).map(function (passage) { return '<blockquote>' + escapeHtml(passage.quote) + '</blockquote>'; }).join('') : '') + (document.textScope ? '<p>발췌 자료입니다. 문서 전체를 검토한 결과가 아닙니다.</p>' : '') + companyEvidenceLink(document.url, "공시·IR 원문 보기") + '<details class="company-report-provenance"><summary>문서 식별 정보</summary><p>' + escapeHtml([document.documentId, document.sourceRevision].filter(Boolean).join(" · ")) + '</p></details></article>';
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
    var labels = {"fy1-revenue-consensus": "첫해 매출 전망", "fy2-revenue-consensus": "다음해 매출 전망", "risk-free-rate": "무위험 금리", "equity-risk-premium": "주식 위험 프리미엄", wacc: "할인율", "terminal-growth": "영구성장률", "years-3-to-5-growth-fade": "이후 성장률의 시작 기준", "constant-operating-margin": "유지한다고 가정한 영업이익률", "constant-reinvestment-ratios": "매출 대비 투자 비율 유지", "preferred-equity-zero": "우선주 청구권 가정", "non-controlling-interest-zero": "비지배지분 가정"};
    var assumptions = companyEvidenceArray(item.assumptions).map(function (assumption) {
      var value = typeof assumption.value === "boolean" ? (assumption.value ? "적용" : "미적용") : companyEvidenceNumber(assumption.value, assumption.unit === "percent" || assumption.unit === "percent-start" ? "%" : ["KRW", "USD"].includes(assumption.unit) ? assumption.unit : "");
      var review = assumption.reviewState === "pending" ? "검토 대기" : assumption.status === "observed" ? "관측값" : "가정";
      return '<li>' + escapeHtml(labels[assumption.id] || "평가 가정") + ': ' + escapeHtml(value) + ' · ' + escapeHtml(review) + '</li>';
    }).join("");
    var sensitivity = item.sensitivity || {};
    var range = sensitivity.valueRange || {};
    var rangeText = sensitivity.status === "calculated" && Number.isFinite(range.low) && Number.isFinite(range.high) ? '<p>할인율·영구성장률 변화에 따른 참고 범위 ' + escapeHtml(companyEvidenceNumber(range.low, item.currency || currency)) + ' ~ ' + escapeHtml(companyEvidenceNumber(range.high, item.currency || currency)) + ' · 통계적 신뢰구간이 아닙니다.</p>' : '';
    return '<p><strong>' + escapeHtml(item.label || "평가 모델") + '</strong> · ' + escapeHtml(item.stateLabel || "검토 상태 미확인") + '<br>참고 계산값 ' + escapeHtml(companyEvidenceNumber(item.fairValue, item.currency || currency)) + '</p>' + (assumptions ? '<ul>' + assumptions + '</ul>' : '') + rangeText;
  }).join("");
  var assumptions = [
    "계산식: " + (model.formula || "자료 없음"),
    "이익 기준: " + (earnings.period || "미확인") + " · 출처 " + (earnings.providers || []).join(", "),
    "PER 표본 " + (multiple.sampleCount || 0) + "개 · 공급자 " + (multiple.providerCount || 0) + "개 · " + (multiple.evidenceBacked ? "근거 연결됨" : "비교 배수 근거 확인 필요")
  ];
  return '<details class="company-report-calculation"><summary>참고 계산과 가정 펼치기</summary><p>가정 변화에 민감한 계산입니다. 검토 대기·참고 상태의 금액을 투자 판단 기준으로 사용하지 않습니다.</p>' + models + table + '<ul>' + assumptions.map(function (line) { return '<li>' + escapeHtml(line) + '</li>'; }).join("") + '</ul></details>';
}

function companyReadingCards(cards) {
  return companyEvidenceArray(cards).map(function (card) {
    var kind = card.kind === "conditional-model" ? "조건부 계산" : "수치로 확인한 의미";
    var evidence = companyEvidenceArray(card.evidence).map(function (metric) {
      return '<article><p><strong>' + escapeHtml(metric.label || metric.key) + '</strong> · ' + escapeHtml(companyEvidenceNumber(metric.value, metric.currency)) + '</p><p>' + escapeHtml(metric.basisLabel) + '</p>' + companyEvidenceSources(metric) + '</article>';
    }).join("");
    var assumptions = Object.entries(card.fixedAssumptions || {}).filter(function (entry) {
      return entry[1] !== null && typeof entry[1] !== "object";
    }).map(function (entry) {
      var labels = {waccPct: "할인율 (%)", terminalGrowthPct: "영구성장률 (%)", ebitMarginPct: "영업이익률 (%)", revenueGrowthPct: "매출 성장률 (%)", cash: "계산에 사용한 현금", debt: "계산에 사용한 부채", preferredEquity: "우선주 청구권", nonControllingInterest: "비지배지분", nonOperatingAssets: "비영업자산", dilutedShares: "희석주식 수"};
      return labels[entry[0]] ? '<li>' + escapeHtml(labels[entry[0]]) + ': ' + escapeHtml(entry[1]) + '</li>' : '';
    }).join("");
    return '<article class="company-report-reading"><p class="label">' + escapeHtml(kind) + '</p><h5>' + escapeHtml(card.title) + '</h5><p><strong>확인 내용</strong> ' + escapeHtml(card.fact) + '</p><p><strong>의미</strong> ' + escapeHtml(card.meaning) + '</p>'
      + companyEvidenceArray(card.sourceStatements).map(function (statement) { return '<p>' + escapeHtml(statement.label) + '</p><blockquote>' + escapeHtml(statement.quote) + '</blockquote>' + companyEvidenceLink(statement.url, '설명 원문'); }).join('')
      + (card.asOf ? '<p>계산에 사용한 가격 관측: ' + escapeHtml(card.asOf) + '</p>' : '')
      + companyEvidenceArray(card.limitations).map(function (line) { return '<p class="instrument-valuation-explanation">' + escapeHtml(line) + '</p>'; }).join("")
      + (evidence || assumptions ? '<details class="company-report-provenance"><summary>이 설명의 근거와 가정</summary>' + evidence + (assumptions ? '<ul>' + assumptions + '</ul>' : '') + '</details>' : '') + '</article>';
  }).join("");
}

function companyReportBrief(report) {
  if (report.brief && report.brief.presentation === "company-report-brief-v1") return report.brief;
  var source = companyEvidenceArray(report.sections);
  var reading = report.reading || {};
  var financial = companyEvidenceArray(reading.financial || (source.find(function (item) { return item.key === "businessMeaning"; }) || {}).readingCards).slice(0, 2);
  var valuations = companyEvidenceArray(reading.valuation || (source.find(function (item) { return item.key === "valuationMeaning"; }) || {}).readingCards);
  var value = valuations.find(function (item) { return item.key === "price-requirements"; }) || valuations[0];
  var checks = companyEvidenceArray((source.find(function (item) { return item.key === "judgmentConditions"; }) || {}).rows);
  var changes = source.find(function (item) { return item.key === "changes"; });
  return {summary: String(report.summary || "").split(". ")[0], sections: [
    {title: changes ? "이번에 달라진 점" : "핵심 수치", rows: changes ? companyEvidenceArray(changes.rows).slice(0, 2) : financial.map(function (card) { return String(card.briefFact || card.fact).replaceAll("official-filing", "공시 기준") + (card.key === "cash-after-investment" ? " · 차입·상환·배당 제외" : ""); })},
    {title: "가치 판단", rows: value ? [value.fact + " · " + value.meaning + " " + companyEvidenceArray(value.limitations).join(" ")] : ["가치평가 근거 확인 필요"]},
    {title: "다음 확인", rows: checks.slice(0, 1)}
  ]};
}

function renderCompanyEvidenceReport(report) {
  var kindLabel = {baseline: "첫 비교 기준", expanded: "상세 자료 보강", change: "변경 자료 확인", unchanged: "변화 없음"}[report.reportKind] || "기업 보고서";
  var sections = companyEvidenceArray(report.sections).map(function (section, index) {
    var sourceExcerpt = section.sourceExcerpt ? '<details class="company-report-provenance"><summary>' + escapeHtml(section.sourceExcerptLabel || "수집 원문 발췌") + '</summary><p>' + escapeHtml(section.sourceExcerpt) + '</p></details>' : '';
    var insightSources = companyEvidenceArray(section.insightEvidenceIds).length ? '<details class="company-report-provenance"><summary>해석의 근거 식별자</summary><ul>' + section.insightEvidenceIds.map(function (id) { return '<li>' + escapeHtml(id) + '</li>'; }).join("") + '</ul></details>' : '';
    return '<section class="instrument-valuation-band company-report-section"><div class="instrument-valuation-section-head"><h4>' + escapeHtml(String(index + 1).padStart(2, "0") + ' · ' + section.title) + '</h4></div>' + companyEvidenceArray(section.paragraphs).map(function (paragraph) { return '<p class="instrument-valuation-explanation">' + escapeHtml(paragraph) + '</p>'; }).join("") + (companyEvidenceArray(section.rows).length ? '<ul>' + section.rows.map(function (row) { return '<li>' + escapeHtml(row) + '</li>'; }).join("") + '</ul>' : '') + companyReadingCards(section.readingCards) + insightSources + sourceExcerpt + companyEvidenceFinancials(section.financialReports) + companyEvidenceDocuments(section.documents) + companyEvidenceCalculation(section, report.currency) + '</section>';
  }).join("");
  var compact = report.contractVersion === "company-change-report-v3";
  var brief = compact ? companyReportBrief(report) : null;
  var briefHtml = brief ? companyEvidenceArray(brief.sections).map(function (section) {
    return '<section class="company-report-brief"><h4>' + escapeHtml(section.title) + '</h4><ul>' + companyEvidenceArray(section.rows).map(function (row) { return '<li>' + escapeHtml(row) + '</li>'; }).join("") + '</ul></section>';
  }).join("") : '';
  var recordSection = companyEvidenceArray(report.sections).find(function (section) { return section.key === "researchRecord"; });
  var researchHtml = compact && recordSection ? '<section class="company-report-brief"><h4>' + escapeHtml(recordSection.title) + '</h4>' + companyEvidenceArray(recordSection.paragraphs).map(function (line) { return '<p>' + escapeHtml(line) + '</p>'; }).join('') + '</section>' : '';
  var full = sections + '<p class="instrument-valuation-explanation">' + escapeHtml(report.boundary) + '</p>';
  return '<section class="instrument-valuation-workspace company-change-report"><header><div><span class="label">기업 자료 보고서</span><h3>' + escapeHtml(report.headline || "기업 보고서") + '</h3><p>' + escapeHtml(brief ? brief.summary : report.summary) + '</p></div><span class="tone-chip hold">' + escapeHtml(kindLabel) + '</span></header><p class="instrument-valuation-explanation">자료 확인 ' + escapeHtml(report.sourceCutoffDisplay || report.sourceCutoffAt || "시각 미기록") + ' · 종목 ' + escapeHtml(report.symbol) + '</p>' + briefHtml + researchHtml + (compact ? '<details class="company-report-full"><summary>상세 근거와 전체 보고서</summary>' + full + '</details>' : full) + '</section>';
}

export { renderCompanyEvidenceReport };
