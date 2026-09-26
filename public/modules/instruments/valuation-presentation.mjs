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
    ,"dcf-assumption-review-required": "DCF 장기 가정 검토"
    ,"depreciation-amortization-missing": "감가상각비"
    ,"working-capital-change-missing": "운전자본 투자 변화"
    ,"stock-based-compensation-missing": "주식보상비용"
    ,"pretax-income-missing": "세전이익"
    ,"tax-provision-missing": "법인세 비용"
    ,"interest-expense-missing": "이자비용"
    ,"fy1-revenue-consensus-missing": "다음 회계연도 매출 컨센서스"
    ,"fy2-revenue-consensus-missing": "차차기 회계연도 매출 컨센서스"
    ,"matching-risk-free-rate-missing": "평가 통화와 일치하는 무위험금리"
    ,"official-financial-evidence-incomplete": "공식 공시 재무 입력 확인"
    ,"official-financial-metric-coverage-incomplete": "공식 공시의 DCF 필수 재무 항목"
    ,"official-financial-source-revision-missing": "공식 재무 원문의 정확한 revision"
    ,"financial-input-metrics-missing": "DCF 필수 재무 항목"
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

function instrumentDcfAssumptionLabel(value) {
  var labels = {
    "equity-risk-premium": "주식 위험 프리미엄",
    wacc: "가중평균자본비용",
    "terminal-growth": "영구성장률",
    "years-3-to-5-growth-fade": "3~5년 성장률 둔화 경로",
    "constant-operating-margin": "영업이익률 유지",
    "constant-reinvestment-ratios": "재투자율 유지",
    "preferred-equity-zero": "우선주 청구권 0 가정",
    "non-controlling-interest-zero": "비지배지분 0 가정"
  };
  return labels[String(value || "")] || String(value || "");
}

function instrumentExposureStateLabel(value) {
  var labels = {
    "verified-linked": "원문과 시장 지표 연결 완료",
    "verified-exposure-market-link-missing": "기업 노출 확인 · 시장 지표 연결 대기",
    "assumption-only": "가정만 있음 · 원문 확인 필요",
    unresolved: "기업별 노출 자료 없음"
  };
  return labels[String(value || "")] || "확인 상태 없음";
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
  var implied = analysis.impliedExpectations || {};
  var readiness = analysis.dcfReadiness || {};
  var impliedSolved = implied.status === "solved" && valuationHasNumericValue(implied.impliedRevenueGrowthPct);
  var impliedAssumptions = implied.fixedAssumptions || {};
  var dcfModel = models.find(function (model) { return model.modelId === "driver-fcff-dcf"; }) || {};
  var sensitivity = dcfModel.sensitivity || {};
  var sensitivityRange = sensitivity.valueRange || {};
  var sensitivityRows = Array.isArray(sensitivity.rows) ? sensitivity.rows : [];
  var baseSensitivity = sensitivityRows.find(function (row) { return row && row.isBase; }) || {};
  var dcfAssumptions = Array.isArray(dcfModel.assumptions) ? dcfModel.assumptions : [];
  var financialEvidence = readiness.financialEvidence || dcfModel.financialEvidence || {};
  var exposureReadiness = readiness.exposureReadiness || dcfModel.exposureReadiness || {};
  var currencyExposure = exposureReadiness.currency || {};
  var debtRateExposure = exposureReadiness.debtRate || {};
  var assumptionReview = readiness.assumptionReview || dcfModel.assumptionReview || {};
  var pendingDcfAssumptions = dcfAssumptions.filter(function (item) {
    return ["observed", "verified", "approved", "user-approved"].indexOf(String((item || {}).status || "").toLowerCase()) < 0;
  });
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
      var reviewPending = model.evidenceBacked && model.inputState === "sufficient" && model.reliabilityState === "sufficient" && ["ai_applied_pending_review", "pending_review", "pending-review"].indexOf(model.reviewStatus) >= 0;
      var assumptionPending = model.sourceBacked && model.assumptionReviewState === "required";
      return '<p><span>' + escapeHtml(instrumentValuationModelLabel({ id: model.modelId })) + '</span><b>' + escapeHtml(valuationHasNumericValue(model.fairValue) ? valuationPrice(model.fairValue, model.currency || currency) : "계산 보류") + '</b><em>' + escapeHtml(model.decisionEligible ? "판단 입력 가능" : reviewPending ? "모델 검토 대기" : assumptionPending ? "가정 검토 필요" : "참고용") + '</em></p>';
    }).join("") + '</div>' : '<p>비교할 평가 모델이 없습니다.</p>') + '</section>',
    '</div>',
    (readiness.status || dcfModel.modelId) ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>DCF 신뢰도 점검</strong><p>계산 가능 여부와 투자 판단에 쓸 수 있는 근거를 구분합니다.</p></div><span class="tone-chip ' + (financialEvidence.officialDecisionReady ? 'hold' : 'caution') + '">' + escapeHtml(financialEvidence.officialDecisionReady ? "공식 재무 확인" : "참고 계산") + '</span></div><div class="instrument-investment-facts"><p><strong>재무 입력</strong><span>' + escapeHtml(financialEvidence.sourceClass === "official-filing" ? "공식 공시" : financialEvidence.sourceClass === "mixed" ? "공식·보조 혼합" : "집계 재무") + '</span><em>' + escapeHtml(String(financialEvidence.officialMetricCount == null ? 0 : financialEvidence.officialMetricCount) + "/" + String(financialEvidence.requiredMetricCount == null ? 0 : financialEvidence.requiredMetricCount) + "개 필수 항목 공식 확인") + '</em></p><p><strong>환율 노출</strong><span>' + escapeHtml(instrumentExposureStateLabel(currencyExposure.status)) + '</span><em>기업 매출·비용·부채 기준</em></p><p><strong>금리 노출</strong><span>' + escapeHtml(instrumentExposureStateLabel(debtRateExposure.status)) + '</span><em>고정·변동금리 부채 기준</em></p><p><strong>가정 검토</strong><span>' + escapeHtml(String(assumptionReview.pendingCount == null ? pendingDcfAssumptions.length : assumptionReview.pendingCount) + "개 대기") + '</span><em>현재 입력 bundle에만 유효</em></p></div><p class="instrument-valuation-explanation">공식 재무와 기업별 노출이 확인되지 않은 값은 계산에 표시되더라도 매수·매도 판단에는 사용하지 않습니다.</p></section>' : '',
    (impliedSolved || readiness.status) ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>현재 가격의 내재 기대</strong><p>다른 가정을 고정했을 때 현재 가격과 일치하는 조건을 역산합니다.</p></div><span class="tone-chip caution">' + escapeHtml(implied.assumptionReviewState === "required" ? "가정 검토 필요" : "조건부 계산") + '</span></div>' + (impliedSolved
      ? '<div class="instrument-investment-facts"><p><strong>5년 일정 매출 성장률</strong><span>' + escapeHtml(valuationDecimal(implied.impliedRevenueGrowthPct, "%", 2)) + '</span><em>시장 기대를 관측한 값이 아님</em></p><p><strong>고정 WACC</strong><span>' + escapeHtml(valuationDecimal(impliedAssumptions.waccPct, "%", 2)) + '</span><em>장기성장률 ' + escapeHtml(valuationDecimal(impliedAssumptions.terminalGrowthPct, "%", 2)) + '</em></p></div><p class="instrument-valuation-explanation">' + escapeHtml(implied.interpretation || "고정 가정 아래의 조건부 역산값입니다.") + '</p>'
      : '<p class="instrument-valuation-explanation">필수 입력이 충족되지 않아 내재 성장률을 계산하지 않았습니다.</p>') + '</section>' : '',
    sensitivity.status === "calculated" ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>DCF 가정 민감도</strong><p>WACC와 영구성장률을 각각 ±1%p 바꿔 적정가 변화를 확인합니다.</p></div><span class="tone-chip caution">조건부 범위</span></div><div class="instrument-investment-facts"><p><strong>조건별 적정가 범위</strong><span>' + escapeHtml(valuationPrice(sensitivityRange.low, sensitivityRange.currency || currency)) + ' ~ ' + escapeHtml(valuationPrice(sensitivityRange.high, sensitivityRange.currency || currency)) + '</span><em>독립적인 적정가 근거가 아님</em></p><p><strong>기준 terminal 비중</strong><span>' + escapeHtml(valuationDecimal(baseSensitivity.terminalValueSharePct, "%", 1)) + '</span><em>장기 가정 의존도</em></p></div>' + (pendingDcfAssumptions.length ? '<p class="instrument-valuation-explanation"><strong>검토 대기 가정</strong> · ' + pendingDcfAssumptions.slice(0, 8).map(function (item) { return escapeHtml(instrumentDcfAssumptionLabel(item.id)); }).join(" · ") + '</p>' : '') + '</section>' : '',
    '<div class="instrument-investment-next"><strong>다음 확인</strong>' + (nextChecks.length ? '<ul>' + nextChecks.slice(0, 6).map(function (item) { return '<li>' + escapeHtml(instrumentValuationMissingLabel(item)) + '</li>'; }).join("") + '</ul>' : '<p>현재 등록된 추가 확인 항목이 없습니다.</p>') + '</div>',
    '</section>'
  ].join("");
}

export { instrumentDcfAssumptionLabel, instrumentExposureStateLabel, instrumentValuationMissingLabel, instrumentValuationModelLabel, renderInstrumentInvestmentAnalysis };
