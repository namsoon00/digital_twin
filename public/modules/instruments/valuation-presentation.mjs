import { escapeHtml } from "../shared/text.mjs";

function valuationHasNumericValue(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return false;
  return Number.isFinite(Number(value));
}

function valuationDecimal(value, suffix, digits) {
  if (!valuationHasNumericValue(value)) return "-";
  var number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString("ko-KR", { minimumFractionDigits: 0, maximumFractionDigits: digits == null ? 2 : digits }) + (suffix || "");
}

function valuationPrice(value, currency) {
  if (!valuationHasNumericValue(value)) return "-";
  var number = Number(value);
  var code = String(currency || "").toUpperCase();
  var formatted = number.toLocaleString("ko-KR", { minimumFractionDigits: 0, maximumFractionDigits: code === "KRW" ? 0 : 2 });
  if (code === "KRW") return formatted + "원";
  if (code === "USD") return "$" + formatted;
  return formatted + (currency ? " " + currency : "");
}

function instrumentValuationModelLabel(model) {
  var id = String((model || {}).id || "");
  var labels = {
    "semiconductor-cycle-earnings": "반도체 이익·업황 방식",
    "growth-quality-earnings": "성장주 이익 방식",
    "bitcoin-treasury-nav": "비트코인 보유가치 방식",
    "preferred-income-yield": "배당수익률 방식",
    "generic-fundamental-earnings": "기업 이익 방식",
    "driver-fcff-dcf": "사업 변수 현금흐름 방식",
    "current-price-reference": "현재가 참고 방식"
  };
  return labels[id] || "적정가 계산 자료 없음";
}

function instrumentValuationMissingLabel(value) {
  var labels = {
    "financial-statements": "재무제표 기간 자료",
    "executive-governance": "경영진·지배구조 자료",
    "valuation-metrics": "PER·PBR 등 시장 평가 지표",
    "capital-structure": "발행주식수·부채 등 자본구조 자료",
    "company-currency-exposure-missing": "기업의 매출·비용 통화 노출",
    "company-debt-rate-exposure-missing": "기업의 고정·변동금리 부채 구조",
    "verified-event-missing": "가격 변화와 연결할 공식 사건",
    "verified-event-source-missing": "사건을 확인할 원문 출처",
    "precise-event-clock-missing": "사건이 공개된 정확한 시각",
    "adjusted-session-price-window-missing": "기업행동을 조정한 장중 가격 구간",
    "market-sector-benchmarks-missing": "같은 시각의 시장·업종 비교",
    "abnormal-return-unavailable": "시장 영향을 제외한 종목 움직임",
    "alternative-explanations-not-checked": "시장·업종 등 다른 원인 점검",
    "independent-source-family-missing": "독립적으로 확인할 원문 출처",
    "event-after-price-reaction": "가격 움직임보다 앞선 사건"
  };
  return labels[String(value || "")] || String(value || "");
}

function instrumentValuationChangeLabel(change) {
  var labels = {
    "no-prior-assessment": "비교할 이전 평가 없음",
    initial: "첫 평가 snapshot",
    "material-change": "평가 의미가 달라짐",
    "lineage-only-change": "출처 기록만 갱신",
    unchanged: "평가 의미 변화 없음"
  };
  return labels[String((change || {}).state || "")] || "이전 평가 확인 필요";
}

function instrumentPriceExplanationLabel(explanation) {
  var labels = {
    "causal-hypothesis": "가격 원인 가설을 뒷받침하는 자료 있음",
    "supported-mechanism": "기업가치 영향 경로만 확인",
    "observed-event": "사건 발생만 확인",
    unresolved: "가격 원인 확인 불가"
  };
  return labels[String((explanation || {}).claimStrength || "unresolved")] || labels.unresolved;
}

function renderInstrumentInvestmentAnalysis(analysis, currency) {
  analysis = analysis || {};
  var newFacts = Array.isArray(analysis.newlyConfirmedFacts) ? analysis.newlyConfirmedFacts : [];
  var facts = Array.isArray(analysis.currentVerifiedFacts) ? analysis.currentVerifiedFacts : newFacts;
  var change = analysis.changeFromPrevious || {};
  var explanation = analysis.priceExplanation || {};
  var nextChecks = Array.isArray(analysis.nextChecks) ? analysis.nextChecks : [];
  var models = Array.isArray(analysis.valuationModels) ? analysis.valuationModels : [];
  var causeLimitations = Array.isArray(explanation.blockingReasons) ? explanation.blockingReasons : [];
  return [
    '<section class="instrument-investment-analysis">',
    '<div class="instrument-valuation-section-head"><div><span class="label">COMPANY STATE</span><h4>회사 상태와 이전 판단</h4></div><span class="tone-chip ' + (change.materialChange ? 'watch' : 'hold') + '">' + escapeHtml(instrumentValuationChangeLabel(change)) + '</span></div>',
    '<p class="instrument-valuation-explanation">' + escapeHtml(newFacts.length ? "이전 기록 이후 새로 확인된 핵심 사업 지표입니다." : "현재 검증 계약과 source revision으로 확인되는 핵심 사업 지표입니다.") + '</p>',
    facts.length ? '<div class="instrument-investment-facts">' + facts.map(function (fact) {
      var value = valuationHasNumericValue(fact.value) ? (fact.unit === "percent" ? valuationDecimal(fact.value, "%", 2) : valuationPrice(fact.value, fact.unit && fact.unit !== "reported-currency" ? fact.unit : currency)) : "값 확인";
      return '<p><strong>' + escapeHtml(fact.label || "기업 지표") + '</strong><span>' + escapeHtml(value) + '</span><em>' + escapeHtml(fact.period || "기간 확인 필요") + '</em></p>';
    }).join("") + '</div>' : '<p class="instrument-valuation-explanation">정확한 원문 revision과 연결된 새 사업 지표가 없습니다.</p>',
    '<div class="instrument-investment-analysis-grid">',
    '<section><strong>가격이 움직인 이유</strong><p>' + escapeHtml(instrumentPriceExplanationLabel(explanation)) + '</p>' + (causeLimitations.length ? '<ul>' + causeLimitations.slice(0, 4).map(function (item) { return '<li>' + escapeHtml(instrumentValuationMissingLabel(item)) + '</li>'; }).join("") + '</ul>' : '') + '</section>',
    '<section><strong>평가 모델</strong>' + (models.length ? '<div>' + models.map(function (model) {
      return '<p><span>' + escapeHtml(instrumentValuationModelLabel({ id: model.modelId })) + '</span><b>' + escapeHtml(valuationHasNumericValue(model.fairValue) ? valuationPrice(model.fairValue, model.currency || currency) : "계산 보류") + '</b><em>' + escapeHtml(model.decisionEligible ? "판단 입력 가능" : "참고용") + '</em></p>';
    }).join("") + '</div>' : '<p>비교할 평가 모델이 없습니다.</p>') + '</section>',
    '</div>',
    '<div class="instrument-investment-next"><strong>다음 확인</strong>' + (nextChecks.length ? '<ul>' + nextChecks.slice(0, 6).map(function (item) { return '<li>' + escapeHtml(instrumentValuationMissingLabel(item)) + '</li>'; }).join("") + '</ul>' : '<p>현재 등록된 추가 확인 항목이 없습니다.</p>') + '</div>',
    '</section>'
  ].join("");
}

export { instrumentValuationMissingLabel, instrumentValuationModelLabel, renderInstrumentInvestmentAnalysis };
