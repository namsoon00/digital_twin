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
    ,"forecast-growth-exceeds-100pct": "전망 매출 성장률이 100%를 초과함"
    ,"forecast-growth-below-minus-80pct": "전망 매출 감소율이 80%를 초과함"
    ,"fy1-revenue-consensus-growth-outlier": "다음 회계연도 매출 컨센서스의 단위·증가율 재검증"
    ,"fy2-revenue-consensus-growth-outlier": "차차기 회계연도 매출 컨센서스의 단위·증가율 재검증"
    ,"consensus-currency-missing": "매출 컨센서스 통화 단위"
    ,"consensus-currency-mismatch": "공시 재무와 매출 컨센서스의 통화 불일치"
    ,"market-capitalization-missing": "검증 가능한 시가총액"
    ,"beta-missing": "시장 수익률과 비교한 베타"
    ,"beta-return-samples-insufficient": "베타 계산에 필요한 60개 이상 일간 수익률"
    ,"market-input-currency-mismatch": "시세와 공시 재무의 통화 일치"
    ,"non-positive-equity-value": "현재 사업 수익성 가정에서 양(+)의 주주가치가 나오지 않음"
    ,"non-positive-equity-value-under-current-economics": "현재 영업이익률·재투자율을 유지하면 양(+)의 주주가치가 나오지 않음"
    ,"fy1-analyst-count-missing": "다음 회계연도 컨센서스 참여 분석가 수"
    ,"fy2-analyst-count-missing": "차차기 회계연도 컨센서스 참여 분석가 수"
    ,"valuation-models-materially-disagree": "평가 모델 간 적정가 차이가 30% 이상"
    ,"valuation-model-currency-conflict": "평가 모델 간 통화 단위 불일치"
    ,"terminal-value-share-exceeds-80pct": "기업가치의 80% 이상이 terminal value에 의존함"
    ,"unapproved-assumptions-present": "검토가 끝나지 않은 장기 DCF 가정"
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
  var dataReadiness = analysis.dataReadiness || {};
  var modelAgreement = analysis.modelAgreement || dataReadiness.modelAgreement || {};
  var consensusEvidence = readiness.consensusEvidence || (readiness.observedInputs || {}).consensusEvidence || dataReadiness.consensus || {};
  var consensusRows = Array.isArray(consensusEvidence.rows) ? consensusEvidence.rows : [];
  var impliedGrowthSolved = implied.status === "solved" && valuationHasNumericValue(implied.impliedRevenueGrowthPct);
  var impliedMarginSolved = implied.status === "solved" && valuationHasNumericValue(implied.impliedEbitMarginPct);
  var impliedSolved = impliedGrowthSolved || impliedMarginSolved;
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
  var modelRelease = readiness.modelRelease || dcfModel.modelRelease || {};
  var referenceReleased = modelRelease.status === "released" && modelRelease.releaseMode === "reference";
  var activeRelease = modelRelease.status === "active" && modelRelease.releaseMode === "active";
  var releaseAudit = readiness.releaseAudit || dcfModel.releaseAudit || modelRelease.audit || {};
  var releaseLimitations = Array.isArray(releaseAudit.limitations) ? releaseAudit.limitations : [];
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
      var releasedReference = model.modelRelease && model.modelRelease.status === "released" && model.modelRelease.releaseMode === "reference";
      var releasedActive = model.modelRelease && model.modelRelease.status === "active" && model.modelRelease.releaseMode === "active";
      var diagnosticRelease = (releasedReference || releasedActive) && model.releaseAudit && model.releaseAudit.diagnosticOnly;
      return '<p><span>' + escapeHtml(instrumentValuationModelLabel({ id: model.modelId })) + '</span><b>' + escapeHtml(valuationHasNumericValue(model.fairValue) ? valuationPrice(model.fairValue, model.currency || currency) : diagnosticRelease ? "현재 수익성으로 양(+) 가치 미산출" : "계산 보류") + '</b><em>' + escapeHtml(model.decisionEligible ? "판단 입력 가능" : releasedActive ? "운영 승격 · 진단용" : releasedReference ? "참고용 릴리즈" : reviewPending ? "모델 검토 대기" : assumptionPending ? "가정 검토 필요" : "참고용") + '</em></p>';
    }).join("") + '</div>' : '<p>비교할 평가 모델이 없습니다.</p>') + '</section>',
    '</div>',
    dataReadiness.status ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>데이터 준비도</strong><p>가격·공식 재무·컨센서스·모델 일치도를 각각 확인합니다.</p></div><span class="tone-chip ' + (dataReadiness.status === "ready" ? "watch" : "caution") + '">' + escapeHtml(dataReadiness.status === "ready" ? "검증 통과" : "제한 있음") + '</span></div><div class="instrument-investment-facts"><p><strong>현재가</strong><span>' + escapeHtml((dataReadiness.quote || {}).status === "available" ? "확인" : "누락") + '</span><em>' + escapeHtml((dataReadiness.quote || {}).asOf || "기준일 없음") + '</em></p><p><strong>공식 재무</strong><span>' + escapeHtml(String((dataReadiness.officialFinancials || {}).officialMetricCount || 0) + "/" + String((dataReadiness.officialFinancials || {}).requiredMetricCount || 0) + "개") + '</span><em>' + escapeHtml((dataReadiness.officialFinancials || {}).period || "보고기간 없음") + '</em></p><p><strong>매출 컨센서스</strong><span>' + escapeHtml(consensusEvidence.status === "validated" ? "통화·기간·범위 검증" : "재검증 필요") + '</span><em>' + escapeHtml(consensusEvidence.currency || "통화 없음") + '</em></p><p><strong>모델 일치도</strong><span>' + escapeHtml(modelAgreement.status === "conflict" ? "충돌" : modelAgreement.status === "comparable" ? "비교 가능" : "비교 모델 부족") + '</span><em>' + escapeHtml(valuationHasNumericValue(modelAgreement.spreadPct) ? "최대 차이 " + valuationDecimal(modelAgreement.spreadPct, "%", 1) : "산출값 부족") + '</em></p></div></section>' : '',
    modelAgreement.status === "conflict" ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>적정가 모델 충돌</strong><p>모델별 값을 평균내지 않고 차이의 원인을 별도로 검토합니다.</p></div><span class="tone-chip caution">판단 보류</span></div><p class="instrument-valuation-explanation">' + escapeHtml((modelAgreement.blockingReasons || []).map(instrumentValuationMissingLabel).join(" · ")) + '</p></section>' : '',
    (readiness.status || dcfModel.modelId) ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>DCF 신뢰도 점검</strong><p>계산 가능 여부와 투자 판단에 쓸 수 있는 근거를 구분합니다.</p></div><span class="tone-chip ' + (financialEvidence.officialDecisionReady ? 'hold' : 'caution') + '">' + escapeHtml(financialEvidence.officialDecisionReady ? "공식 재무 확인" : "참고 계산") + '</span></div><div class="instrument-investment-facts">' + (activeRelease ? '<p><strong>릴리즈</strong><span>운영 분석 승격</span><em>' + escapeHtml(modelRelease.releaseId || "release ID 확인 필요") + '</em></p>' : referenceReleased ? '<p><strong>릴리즈</strong><span>참고용 릴리즈</span><em>' + escapeHtml(modelRelease.releaseId || "release ID 확인 필요") + '</em></p>' : '') + '<p><strong>재무 입력</strong><span>' + escapeHtml(financialEvidence.sourceClass === "official-filing" ? "공식 공시" : financialEvidence.sourceClass === "mixed" ? "공식·보조 혼합" : "집계 재무") + '</span><em>' + escapeHtml(String(financialEvidence.officialMetricCount == null ? 0 : financialEvidence.officialMetricCount) + "/" + String(financialEvidence.requiredMetricCount == null ? 0 : financialEvidence.requiredMetricCount) + "개 필수 항목 공식 확인") + '</em></p><p><strong>환율 노출</strong><span>' + escapeHtml(instrumentExposureStateLabel(currencyExposure.status)) + '</span><em>기업 매출·비용·부채 기준</em></p><p><strong>금리 노출</strong><span>' + escapeHtml(instrumentExposureStateLabel(debtRateExposure.status)) + '</span><em>고정·변동금리 부채 기준</em></p><p><strong>가정 검토</strong><span>' + escapeHtml(String(assumptionReview.pendingCount == null ? pendingDcfAssumptions.length : assumptionReview.pendingCount) + "개 대기") + '</span><em>현재 입력 bundle에만 유효</em></p></div>' + (releaseLimitations.length ? '<p class="instrument-valuation-explanation"><strong>릴리즈 점검 경고</strong> · ' + releaseLimitations.map(instrumentValuationMissingLabel).map(escapeHtml).join(" · ") + '</p>' : '') + '<p class="instrument-valuation-explanation">' + escapeHtml(activeRelease ? "승격된 입력 번들은 운영 분석에서 계속 추적합니다. 양(+) 가치가 없는 진단 결과는 매수·매도 판단이나 자동 주문에 사용하지 않습니다." : "참고용 릴리즈는 적정가·민감도 조회에 사용하며 매수·매도 판단과 자동 주문에는 사용하지 않습니다.") + '</p></section>' : '',
    consensusRows.length ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>컨센서스 입력 검증</strong><p>회계연도, 통화, 분석가 수와 전년 대비 변화를 함께 표시합니다.</p></div><span class="tone-chip ' + (consensusEvidence.status === "validated" ? "hold" : "caution") + '">' + escapeHtml(consensusEvidence.status === "validated" ? "검증됨" : "차단됨") + '</span></div><div class="instrument-investment-facts">' + consensusRows.map(function (item) { return '<p><strong>' + escapeHtml(item.horizon || item.periodToken || "전망") + '</strong><span>' + escapeHtml(valuationPrice(item.value, item.currency || currency)) + '</span><em>' + escapeHtml("분석가 " + String(item.analystCount || 0) + "명 · 성장률 " + valuationDecimal(item.growthPct, "%", 1)) + '</em></p>'; }).join("") + '</div></section>' : '',
    (impliedSolved || readiness.status) ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>현재 가격의 내재 기대</strong><p>다른 가정을 고정했을 때 현재 가격과 일치하는 조건을 역산합니다.</p></div><span class="tone-chip caution">' + escapeHtml(implied.assumptionReviewState === "required" ? "가정 검토 필요" : "조건부 계산") + '</span></div>' + (impliedSolved
      ? '<div class="instrument-investment-facts">' + (impliedMarginSolved ? '<p><strong>현재가 내재 영업이익률</strong><span>' + escapeHtml(valuationDecimal(implied.impliedEbitMarginPct, "%", 2)) + '</span><em>현재 관측치 ' + escapeHtml(valuationDecimal(implied.observedEbitMarginPct, "%", 2)) + '</em></p><p><strong>필요 개선폭</strong><span>' + escapeHtml(valuationDecimal(implied.marginExpansionRequiredPctPoints, "%p", 2)) + '</span><em>전망기간 일정 이익률 가정</em></p>' : '<p><strong>5년 일정 매출 성장률</strong><span>' + escapeHtml(valuationDecimal(implied.impliedRevenueGrowthPct, "%", 2)) + '</span><em>시장 기대를 관측한 값이 아님</em></p>') + '<p><strong>고정 WACC</strong><span>' + escapeHtml(valuationDecimal(impliedAssumptions.waccPct, "%", 2)) + '</span><em>장기성장률 ' + escapeHtml(valuationDecimal(impliedAssumptions.terminalGrowthPct, "%", 2)) + '</em></p></div><p class="instrument-valuation-explanation">' + escapeHtml(implied.interpretation || "고정 가정 아래의 조건부 역산값입니다.") + '</p>'
      : '<p class="instrument-valuation-explanation">필수 입력이 충족되지 않아 현재가의 내재 조건을 계산하지 않았습니다.</p>') + '</section>' : '',
    sensitivity.status === "calculated" ? '<section class="instrument-investment-next"><div class="instrument-valuation-section-head"><div><strong>DCF 가정 민감도</strong><p>WACC와 영구성장률을 각각 ±1%p 바꿔 적정가 변화를 확인합니다.</p></div><span class="tone-chip caution">조건부 범위</span></div><div class="instrument-investment-facts"><p><strong>조건별 적정가 범위</strong><span>' + escapeHtml(valuationPrice(sensitivityRange.low, sensitivityRange.currency || currency)) + ' ~ ' + escapeHtml(valuationPrice(sensitivityRange.high, sensitivityRange.currency || currency)) + '</span><em>독립적인 적정가 근거가 아님</em></p><p><strong>기준 terminal 비중</strong><span>' + escapeHtml(valuationDecimal(baseSensitivity.terminalValueSharePct, "%", 1)) + '</span><em>장기 가정 의존도</em></p></div>' + (pendingDcfAssumptions.length ? '<p class="instrument-valuation-explanation"><strong>검토 대기 가정</strong> · ' + pendingDcfAssumptions.slice(0, 8).map(function (item) { return escapeHtml(instrumentDcfAssumptionLabel(item.id)); }).join(" · ") + '</p>' : '') + '</section>' : '',
    '<div class="instrument-investment-next"><strong>다음 확인</strong>' + (nextChecks.length ? '<ul>' + nextChecks.slice(0, 6).map(function (item) { return '<li>' + escapeHtml(instrumentValuationMissingLabel(item)) + '</li>'; }).join("") + '</ul>' : '<p>현재 등록된 추가 확인 항목이 없습니다.</p>') + '</div>',
    '</section>'
  ].join("");
}

function companyReportFactValue(fact, currency) {
  if (!valuationHasNumericValue((fact || {}).value)) return "값 확인 필요";
  if (fact.unit === "percent") return valuationDecimal(fact.value, "%", 2);
  return valuationPrice(fact.value, fact.unit && fact.unit !== "reported-currency" ? fact.unit : currency);
}

function companyReportSourceScopeLabel(value) {
  var labels = {
    overview: "기업 지표",
    "overview-secondary": "보조 기업 지표",
    "statements-governance": "재무·경영 정보",
    "official-filing": "공식 공시",
    "official-filing-company": "국내 공식 공시",
    "valuation-model-input": "적정가 계산 입력",
    "company-metrics": "기업 평가 지표"
  };
  return labels[String(value || "").toLowerCase()] || String(value || "기업 자료");
}

function companyReportChangeRow(change, currency) {
  var previous = change.previous || {};
  var current = change.current || {};
  var label = change.label || change.modelId || current.label || current.modelId || "변경 항목";
  var isModel = Boolean(current.modelId || change.modelId);
  var before = change.kind === "new"
    ? "이전 자료 없음"
    : isModel ? valuationPrice(previous.fairValue, previous.currency || currency) : companyReportFactValue(previous, currency);
  var after = isModel ? valuationPrice(current.fairValue, current.currency || currency) : companyReportFactValue(current, currency);
  return '<p><strong>' + escapeHtml(label) + '</strong><span>' + escapeHtml(before + " → " + after) + '</span><em>' + escapeHtml(change.kind === "new" ? "새로 확인" : "이전 전달 보고서와 비교") + '</em></p>';
}

function renderCompanyChangeReport(report) {
  report = report || {};
  var state = report.currentState || {};
  var changes = report.changes || {};
  var facts = Array.isArray(state.facts) ? state.facts : [];
  var models = Array.isArray(state.valuationModels) ? state.valuationModels : [];
  var factChanges = Array.isArray(changes.factChanges) ? changes.factChanges : [];
  var valuationChanges = Array.isArray(changes.valuationChanges) ? changes.valuationChanges : [];
  var changeRows = factChanges.concat(valuationChanges);
  var uncertainties = Array.isArray(report.uncertainties) ? report.uncertainties : [];
  var nextChecks = Array.isArray(report.nextChecks) ? report.nextChecks : [];
  var sources = Array.isArray(report.sources) ? report.sources : [];
  var currency = report.currency || "";
  var kindLabel = report.reportKind === "baseline" ? "기준 보고서" : report.reportKind === "change" ? "변화 확인" : "변화 없음";
  var kindTone = report.reportKind === "change" ? "watch" : report.reportKind === "baseline" ? "caution" : "hold";
  return [
    '<section class="instrument-valuation-workspace company-change-report">',
    '<header><div><span class="label">COMPANY CHANGE REPORT</span><h3>' + escapeHtml(report.headline || "기업 변화 보고서") + '</h3><p>' + escapeHtml(report.summary || "현재 확인 가능한 기업 상태를 정리했습니다.") + '</p></div><span class="tone-chip ' + kindTone + '">' + escapeHtml(kindLabel) + '</span></header>',
    '<section class="instrument-valuation-band"><div class="instrument-valuation-section-head"><div><span class="label">CHANGE</span><h4>이번에 달라진 점</h4></div><span>' + escapeHtml(String(changes.count || 0) + "건") + '</span></div>',
    changeRows.length ? '<div class="instrument-investment-facts">' + changeRows.slice(0, 8).map(function (change) { return companyReportChangeRow(change, currency); }).join("") + '</div>' : '<p class="instrument-valuation-explanation">' + escapeHtml(report.reportKind === "baseline" ? "첫 보고서이므로 현재 상태를 비교 기준으로 저장했습니다." : "마지막으로 전달된 보고서와 비교해 핵심 기업 상태 변화가 없습니다.") + '</p>',
    '</section>',
    '<section class="instrument-investment-analysis"><div class="instrument-valuation-section-head"><div><span class="label">VERIFIED STATE</span><h4>확인된 기업 상태</h4></div><span class="tone-chip hold">사실 기반</span></div>',
    facts.length ? '<div class="instrument-investment-facts">' + facts.map(function (fact) { return '<p><strong>' + escapeHtml(fact.label || "기업 지표") + '</strong><span>' + escapeHtml(companyReportFactValue(fact, currency)) + '</span><em>' + escapeHtml(fact.period || "기간 확인 필요") + '</em></p>'; }).join("") + '</div>' : '<p class="instrument-valuation-explanation">원문 revision과 연결된 핵심 사업 지표가 아직 부족합니다.</p>',
    models.length ? '<div class="instrument-investment-next"><strong>가치평가 상태</strong><div class="instrument-investment-facts">' + models.map(function (model) { return '<p><strong>' + escapeHtml(instrumentValuationModelLabel({ id: model.modelId })) + '</strong><span>' + escapeHtml(valuationPrice(model.fairValue, model.currency || currency)) + '</span><em>' + escapeHtml(model.decisionEligible ? "판단 입력 가능" : "참고용") + '</em></p>'; }).join("") + '</div></div>' : '',
    '</section>',
    '<div class="instrument-valuation-detail-grid">',
    '<section class="instrument-valuation-band"><div class="instrument-valuation-section-head"><div><span class="label">LIMITS</span><h4>확인 한계</h4></div><span>' + escapeHtml(String(uncertainties.length) + "건") + '</span></div>' + (uncertainties.length ? '<ul>' + uncertainties.slice(0, 8).map(function (item) { return '<li>' + escapeHtml(instrumentValuationMissingLabel(item)) + '</li>'; }).join("") + '</ul>' : '<p>현재 보고서에 기록된 주요 누락 항목이 없습니다.</p>') + '</section>',
    '<section class="instrument-valuation-band"><div class="instrument-valuation-section-head"><div><span class="label">NEXT</span><h4>다음 확인</h4></div><span>' + escapeHtml(String(nextChecks.length) + "건") + '</span></div>' + (nextChecks.length ? '<ul>' + nextChecks.slice(0, 8).map(function (item) { return '<li>' + escapeHtml(instrumentValuationMissingLabel(item)) + '</li>'; }).join("") + '</ul>' : '<p>현재 등록된 추가 확인 항목이 없습니다.</p>') + '</section>',
    '</div>',
    '<section class="instrument-valuation-sources"><div class="instrument-valuation-section-head"><div><span class="label">PROVENANCE</span><h4>출처와 보고 기준</h4></div><span>' + escapeHtml(report.sourceCutoffAt || report.generatedAt || "기준 시각 없음") + '</span></div>',
    sources.length ? '<div>' + sources.map(function (source) { return '<p><strong>' + escapeHtml(source.provider || "출처 미기록") + '</strong><span>' + escapeHtml((source.scopes || [source.scope]).filter(Boolean).map(companyReportSourceScopeLabel).join(" · ") || "기업 자료") + '</span><time>' + escapeHtml(source.asOf || "기준일 없음") + '</time></p>'; }).join("") + '</div>' : '<p class="instrument-valuation-explanation">출처 기록이 없습니다.</p>',
    '</section>',
    '<p class="instrument-valuation-explanation"><strong>분석 경계</strong> · ' + escapeHtml(report.boundary || "확인된 사실을 요약하며 투자 행동을 만들지 않습니다.") + '</p>',
    '</section>'
  ].join("");
}

export { instrumentDcfAssumptionLabel, instrumentExposureStateLabel, instrumentValuationMissingLabel, instrumentValuationModelLabel, renderCompanyChangeReport, renderInstrumentInvestmentAnalysis };
