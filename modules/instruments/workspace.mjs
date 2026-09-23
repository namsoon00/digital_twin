import { marketSignalForItem, parseMarketSignals } from "../decisions/signals.mjs";
import { clientKnownStockInfo, stockDisplayName } from "./catalog.mjs";
import { instrumentChartEventProjection, instrumentEventDetailTarget, instrumentEventGroupRows, instrumentTimelineCacheKey, instrumentTimelineRange, instrumentValuationCacheKey, instrumentValuationViewState, instrumentWorkspaceTab, parseInstrumentEventGroupKey } from "./timeline.mjs";
import { selectConsoleInstrumentRows } from "../market/selectors.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { formatClock, formatSignalRatio, hasNumericValue, optionalPrice, optionalSignedPct } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { instrumentsState } from "../state/instruments.mjs";
import { shellState } from "../state/shell.mjs";

function renderInstrumentWorkspaceLink(symbol, label) {
  var normalized = String(symbol || "").toUpperCase().trim();
  var available = selectConsoleInstrumentRows(shellState.snapshot || {}).some(function (row) {
    return row.symbol === normalized;
  });
  if (!normalized || !available) return "";
  return [
    '<section class="instrument-context-link">',
    '<span><strong>' + escapeHtml(stockDisplayName(normalized, clientKnownStockInfo(normalized))) + '</strong><em>차트·판단·가설·사건을 같은 종목 화면에서 확인</em></span>',
    renderWorkDetailButton("market-instrument", normalized, label || "종목 작업공간", "text-button compact"),
    '</section>'
  ].join("");
}

function instrumentTimelineViewState(symbol) {
  var range = instrumentTimelineRange(symbol);
  var key = instrumentTimelineCacheKey(symbol, range);
  var normalized = String(symbol || "").toUpperCase();
  var currentPayload = (instrumentsState.instrumentTimelines || {})[key] || null;
  var fallbackKey = String((instrumentsState.instrumentTimelineLastKeys || {})[instrumentValuationCacheKey(normalized)] || "");
  var fallbackPayload = fallbackKey ? (instrumentsState.instrumentTimelines || {})[fallbackKey] || null : null;
  return {
    range: range,
    key: key,
    payloadKey: currentPayload ? key : (fallbackPayload ? fallbackKey : key),
    payload: currentPayload || fallbackPayload,
    stale: Boolean(!currentPayload && fallbackPayload),
    loading: Boolean((instrumentsState.instrumentTimelineLoading || {})[key]),
    error: String((instrumentsState.instrumentTimelineErrors || {})[key] || ""),
    staticPreview: isStaticPreviewHost()
  };
}

function renderInstrumentWorkspaceNavigation(symbol) {
  var active = instrumentWorkspaceTab(symbol);
  var tabs = [
    ["summary", "요약"],
    ["valuation", "기업가치"],
    ["chart", "차트"],
    ["decision", "판단"],
    ["timeline", "타임라인"]
  ];
  return '<nav class="instrument-workspace-tabs" role="tablist" aria-label="종목 상세 보기">' + tabs.map(function (item) {
    return '<button type="button" role="tab" aria-selected="' + (active === item[0] ? "true" : "false") + '" data-instrument-workspace-tab="' + item[0] + '" data-instrument-symbol="' + escapeHtml(symbol) + '" class="' + (active === item[0] ? "active" : "") + '">' + escapeHtml(item[1]) + '</button>';
  }).join("") + '</nav>';
}

function renderInstrumentSummary(row) {
  var signal = marketSignalForItem(row.raw || {}, parseMarketSignals());
  var evidence = row.evidence || [];
  return [
    '<section class="work-detail-section instrument-summary-metrics"><div class="work-detail-metric-row work-detail-metric-row--five">',
    renderNotificationDetailMetric("현재가", optionalPrice(row.currentPrice, row.currency, row.quoteAvailable), row.quoteAvailable && row.changeRate < 0 ? "danger" : "watch"),
    renderNotificationDetailMetric("등락", optionalSignedPct(row.changeRate, row.changeAvailable), row.changeAvailable && row.changeRate < 0 ? "danger" : "watch"),
    renderNotificationDetailMetric("보유 손익", row.source === "watchlist" ? "-" : optionalSignedPct(row.profitLossRate, row.profitLossAvailable), row.profitLossAvailable && row.profitLossRate < 0 ? "danger" : "watch"),
    renderNotificationDetailMetric(row.flowLabel, row.flowDisplay, row.partialFlowAvailable && row.foreignInstitutionNet < 0 ? "danger" : "watch"),
    renderNotificationDetailMetric("뉴스 근거", row.evidenceCount + "건", row.impact.tone),
    '</div></section>',
    '<section class="work-detail-section primary instrument-current-state"><div><span class="label">CURRENT STATE</span><strong>현재 상황</strong></div><p>' + escapeHtml([row.decision && (row.decision.decision || row.decision.action), row.impact.label, row.source === "watchlist" ? "관심 종목" : "보유 종목", row.quality.label].filter(Boolean).join(" · ") || "시장 데이터 확인") + '</p></section>',
    '<div class="instrument-summary-grid">',
    '<section class="work-detail-section"><strong>가격·수급</strong><div class="work-detail-list">',
    '<div class="work-detail-row"><b>가격</b><div><strong>' + escapeHtml(optionalPrice(row.currentPrice, row.currency, row.quoteAvailable)) + '</strong><span>등락 ' + escapeHtml(optionalSignedPct(row.changeRate, row.changeAvailable)) + '</span></div><em>' + escapeHtml(formatClock(row.updatedAt)) + '</em></div>',
    '<div class="work-detail-row"><b>수급</b><div><strong>' + escapeHtml(row.flowLabel + " " + row.flowDisplay) + '</strong><span>거래량 비율 ' + escapeHtml(formatSignalRatio(signal.volumeRatio)) + '</span></div><em>' + escapeHtml(row.quality.label) + '</em></div>',
    '</div></section>',
    '<section class="work-detail-section"><strong>연결 상태</strong><div class="instrument-link-summary">',
    '<span><b>' + escapeHtml(row.decision ? "판단 연결" : "판단 대기") + '</b><em>' + escapeHtml(row.decision ? ((row.decision.reasons || [])[0] || row.decision.action || "판단 근거") : "추론 결과가 생성되면 연결됩니다.") + '</em>' + (row.decision && row.decision.consoleKey ? renderWorkDetailButton(row.decision.consoleDetailType || "investment-action", row.decision.consoleKey, "판단 상세", "text-button compact") : '') + '</span>',
    '<span><b>근거 ' + escapeHtml(evidence.length) + '건</b><em>' + escapeHtml(evidence.length ? "뉴스·공시 상세는 타임라인에서 확인" : "연결된 뉴스 근거 없음") + '</em></span>',
    '</div></section>',
    '</div>',
    '<section class="instrument-data-contract"><span class="tone-chip ' + escapeHtml(row.isMock ? "caution" : "watch") + '">' + escapeHtml(row.isMock ? "MOCK" : "ACTUAL") + '</span><p><strong>' + escapeHtml(row.apiSource || "시세 출처 미기록") + '</strong><em>최종 변경 ' + escapeHtml(formatClock(row.updatedAt)) + '</em></p></section>'
  ].join("");
}

function instrumentValuationDecimal(value, suffix, digits) {
  if (!hasNumericValue(value)) return "-";
  var number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits == null ? 2 : digits
  }) + (suffix || "");
}

function instrumentValuationPrice(value, currency) {
  if (!hasNumericValue(value)) return "-";
  var number = Number(value);
  var formatted = number.toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: String(currency || "").toUpperCase() === "KRW" ? 0 : 2
  });
  if (String(currency || "").toUpperCase() === "KRW") return formatted + "원";
  if (String(currency || "").toUpperCase() === "USD") return "$" + formatted;
  return formatted + (currency ? " " + currency : "");
}

function instrumentValuationSignedPct(value) {
  if (!hasNumericValue(value)) return "-";
  var number = Number(value);
  return (number > 0 ? "+" : "") + number.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) + "%";
}

function instrumentValuationPerText(metrics) {
  var status = String((metrics || {}).perStatus || "missing");
  if (status === "not-meaningful-loss") return "적자 · 산출 불가";
  if (status === "not-meaningful-zero-earnings") return "이익 0 · 산출 불가";
  if (status === "available" && hasNumericValue((metrics || {}).currentPER) && Number(metrics.currentPER) > 0) {
    return instrumentValuationDecimal(metrics.currentPER, "배", 2);
  }
  return "자료 없음";
}

function instrumentValuationQualityMeta(quality, valuationStatus) {
  var status = String((quality || {}).status || valuationStatus || "unavailable").toLowerCase();
  if (status === "ready") return { label: "근거 충족", tone: "watch" };
  if (status === "blocked" || String(valuationStatus || "").indexOf("blocked") === 0) return { label: "계산 보류", tone: "danger" };
  if (status === "partial" || valuationStatus === "calculated") return { label: "참고값", tone: "caution" };
  return { label: "자료 부족", tone: "hold" };
}

function instrumentValuationModelLabel(model) {
  var id = String((model || {}).id || "");
  var labels = {
    "semiconductor-cycle-earnings": "반도체 이익·업황 방식",
    "growth-quality-earnings": "성장주 이익 방식",
    "bitcoin-treasury-nav": "비트코인 보유가치 방식",
    "preferred-income-yield": "배당수익률 방식",
    "generic-fundamental-earnings": "기업 이익 방식",
    "current-price-reference": "현재가 참고 방식"
  };
  return labels[id] || "적정가 계산 자료 없음";
}

function instrumentValuationPeriodLabel(value) {
  var labels = {
    "ttm": "최근 12개월",
    "trailing-12m": "최근 12개월",
    "forward-12m": "향후 12개월",
    "fy1": "다음 회계연도",
    "fy2": "그다음 회계연도",
    "annual": "연간",
    "annualized": "연환산",
    "interim": "누적 분기",
    "quarterly": "분기"
  };
  return labels[String(value || "").toLowerCase()] || String(value || "기준 미확인");
}

function instrumentValuationMultipleBasisLabel(value) {
  var labels = {
    "historical": "과거 PER 표본",
    "peer": "비교기업 PER 표본",
    "historical+peer": "과거·비교기업 PER 표본",
    "bootstrap-prior": "종목 유형별 초기 참고 범위",
    "current-market": "현재 시장 PER"
  };
  return labels[String(value || "").toLowerCase()] || "근거 범위 미확인";
}

function instrumentValuationStateLabel(value) {
  var labels = {
    sufficient: "충분",
    ready: "충분",
    partial: "일부만 있음",
    unavailable: "사용 불가",
    fresh: "최신",
    aging: "오래됨",
    stale: "만료",
    unknown: "확인 필요",
    ai_applied_pending_review: "사용자 검토 전",
    user_approved: "사용자 승인",
    user_modified: "사용자 수정",
    user_rejected: "사용 제외"
  };
  return labels[String(value || "").toLowerCase()] || String(value || "확인 필요");
}

function instrumentValuationSourceScopeLabel(value) {
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

function instrumentValuationMissingLabel(value) {
  var labels = {
    "financial-statements": "재무제표 기간 자료",
    "executive-governance": "경영진·지배구조 자료",
    "valuation-metrics": "PER·PBR 등 시장 평가 지표",
    "capital-structure": "발행주식수·부채 등 자본구조 자료"
  };
  return labels[String(value || "")] || String(value || "");
}

function renderInstrumentValuationMetric(label, value, detail, tone) {
  return '<div class="instrument-valuation-metric ' + escapeHtml(tone || "hold") + '"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value || "-") + '</strong><em>' + escapeHtml(detail || "") + '</em></div>';
}

function renderInstrumentValuationScenario(label, value, margin, currency, tone) {
  return '<div class="instrument-valuation-scenario ' + escapeHtml(tone || "hold") + '"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(instrumentValuationPrice(value, currency)) + '</strong><em>현재가 대비 ' + escapeHtml(instrumentValuationSignedPct(margin)) + '</em></div>';
}

function renderInstrumentValuation(row, view) {
  if (view.staticPreview) {
    return '<div class="instrument-chart-state"><strong>정적 화면에서는 기업가치 자료를 조회하지 않습니다.</strong><p>로컬 또는 공유 앱에서 최신 기업 지표와 적정가 계산 근거를 확인하세요.</p></div>';
  }
  var payload = view.payload || {};
  if (view.loading && !view.payload) {
    return '<div class="instrument-chart-state is-loading"><span></span><strong>PER와 적정가 근거를 조회하고 있습니다.</strong></div>';
  }
  if (view.error && !view.payload) {
    return '<div class="instrument-chart-state is-error"><strong>' + escapeHtml(view.error) + '</strong><button type="button" class="text-button" data-instrument-valuation-refresh="' + escapeHtml(row.symbol) + '">다시 조회</button></div>';
  }
  if (!view.payload) {
    return '<div class="instrument-empty"><strong>기업가치 자료를 아직 불러오지 않았습니다.</strong><button type="button" class="text-button" data-instrument-valuation-refresh="' + escapeHtml(row.symbol) + '">조회</button></div>';
  }

  var instrument = payload.instrument || {};
  var metrics = payload.marketMetrics || {};
  var valuation = payload.valuation || {};
  var quality = valuation.quality || {};
  var fairValue = valuation.fairValue || {};
  var safety = valuation.safetyMargin || {};
  var earnings = valuation.earningsScenario || {};
  var multiple = valuation.multipleBand || {};
  var company = payload.companyData || {};
  var qualityMeta = instrumentValuationQualityMeta(quality, valuation.status);
  var currency = fairValue.currency || instrument.currency || row.currency;
  var currentPrice = instrument.currentPrice || row.currentPrice;
  var currentPerDetail = metrics.perStatus === "available" ? "현재 주가 ÷ 최근 이익" : "이익이 없거나 값이 수집되지 않음";
  var modelHasFairValue = hasNumericValue(fairValue.base) && Number(fairValue.base) > 0;
  var evidenceBacked = Boolean(multiple.evidenceBacked);
  var sourceRows = Array.isArray(payload.sources) ? payload.sources : [];
  var missing = Array.isArray(payload.missingData) ? payload.missingData : [];
  var decisionLabel = quality.decisionEligible ? "투자 판단에 사용 가능" : "투자 판단에는 참고만";
  var decisionTone = quality.decisionEligible ? "watch" : "caution";

  return [
    '<section class="instrument-valuation-workspace">',
    '<header><div><span class="label">COMPANY VALUE</span><h3>기업가치</h3><p>시장 평가 지표와 우리 계산의 가정·출처·한계를 분리해 보여줍니다.</p></div><div class="instrument-valuation-head-actions"><span class="tone-chip ' + escapeHtml(qualityMeta.tone) + '">' + escapeHtml(qualityMeta.label) + '</span><button type="button" class="text-button compact" data-instrument-valuation-refresh="' + escapeHtml(row.symbol) + '">새로고침</button></div></header>',
    '<div class="instrument-valuation-metrics">',
    renderInstrumentValuationMetric("현재 PER", instrumentValuationPerText(metrics), currentPerDetail, metrics.perStatus === "available" ? "watch" : "hold"),
    renderInstrumentValuationMetric("선행 PER", hasNumericValue(metrics.forwardPER) ? instrumentValuationDecimal(metrics.forwardPER, "배", 2) : "자료 없음", "향후 이익 예상치 기준", "hold"),
    renderInstrumentValuationMetric("최근 EPS", hasNumericValue(metrics.trailingEPS) ? instrumentValuationPrice(metrics.trailingEPS, currency) : "자료 없음", instrumentValuationPeriodLabel(metrics.trailingEPSPeriod || "ttm"), "hold"),
    renderInstrumentValuationMetric("PBR", hasNumericValue(metrics.pbr) ? instrumentValuationDecimal(metrics.pbr, "배", 2) : "자료 없음", "주가 ÷ 주당순자산", "hold"),
    renderInstrumentValuationMetric("PEG", hasNumericValue(metrics.pegRatio) ? instrumentValuationDecimal(metrics.pegRatio, "배", 2) : "자료 없음", "PER와 이익 성장의 비교", "hold"),
    '</div>',
    '<section class="instrument-valuation-band">',
    '<div class="instrument-valuation-section-head"><div><span class="label">VALUATION SCENARIOS</span><h4>' + (evidenceBacked ? '근거 기반 가격 범위' : '초기 가정별 가격 시나리오') + '</h4></div><span class="tone-chip ' + escapeHtml(decisionTone) + '">' + escapeHtml(decisionLabel) + '</span></div>',
    '<p class="instrument-valuation-model"><strong>' + escapeHtml(instrumentValuationModelLabel(valuation.model)) + '</strong><span>현재가 ' + escapeHtml(instrumentValuationPrice(currentPrice, currency)) + '</span></p>',
    modelHasFairValue ? '<div class="instrument-valuation-scenarios">' + [
      renderInstrumentValuationScenario("보수적", fairValue.low, safety.conservativePct, currency, "hold"),
      renderInstrumentValuationScenario("기준", fairValue.base, safety.basePct, currency, "watch"),
      renderInstrumentValuationScenario("낙관적", fairValue.high, safety.optimisticPct, currency, "hold")
    ].join("") + '</div>' : '<div class="instrument-valuation-unavailable"><strong>적정가 계산 보류</strong><p>' + escapeHtml(valuation.sourceReason || "필수 입력값이 부족해 현재가를 적정가로 대신하지 않았습니다.") + '</p></div>',
    modelHasFairValue ? '<p class="instrument-valuation-explanation">' + escapeHtml(valuation.sourceReason || "확인된 EPS와 PER 범위를 조합해 계산했습니다.") + '</p>' : '',
    '</section>',
    '<div class="instrument-valuation-detail-grid">',
    '<section class="instrument-valuation-band"><div class="instrument-valuation-section-head"><div><span class="label">EARNINGS</span><h4>계산에 쓴 이익</h4></div><span>' + escapeHtml(instrumentValuationPeriodLabel(earnings.period)) + '</span></div>',
    '<div class="instrument-valuation-inline-values"><span><b>낮음</b><strong>' + escapeHtml(instrumentValuationPrice(earnings.low, currency)) + '</strong></span><span><b>기준</b><strong>' + escapeHtml(instrumentValuationPrice(earnings.base, currency)) + '</strong></span><span><b>높음</b><strong>' + escapeHtml(instrumentValuationPrice(earnings.high, currency)) + '</strong></span></div>',
    '<p>' + escapeHtml([earnings.sourceCount ? "출처 " + earnings.sourceCount + "곳" : "출처 수 미확인", earnings.analystCount ? "분석가 " + earnings.analystCount + "명" : "분석가 수 미확인", earnings.providers && earnings.providers.length ? earnings.providers.join(", ") : "제공자 미확인"].join(" · ")) + '</p></section>',
    '<section class="instrument-valuation-band"><div class="instrument-valuation-section-head"><div><span class="label">P/E EVIDENCE</span><h4>비교에 쓴 PER 범위</h4></div><span class="tone-chip ' + escapeHtml(evidenceBacked ? "watch" : "caution") + '">' + escapeHtml(evidenceBacked ? "실제 표본" : "초기 참고값") + '</span></div>',
    '<div class="instrument-valuation-inline-values"><span><b>낮음</b><strong>' + escapeHtml(instrumentValuationDecimal(multiple.low, "배", 1)) + '</strong></span><span><b>기준</b><strong>' + escapeHtml(instrumentValuationDecimal(multiple.base, "배", 1)) + '</strong></span><span><b>높음</b><strong>' + escapeHtml(instrumentValuationDecimal(multiple.high, "배", 1)) + '</strong></span></div>',
    '<p><strong>' + escapeHtml(instrumentValuationMultipleBasisLabel(multiple.basis)) + '</strong> · 표본 ' + escapeHtml(String(multiple.sampleCount || 0)) + '개' + (evidenceBacked ? '' : ' · 실제 과거·비교기업 표본이 부족해 투자 판단에는 사용하지 않습니다.') + '</p></section>',
    '</div>',
    '<section class="instrument-valuation-quality">',
    '<div><span class="tone-chip ' + escapeHtml(decisionTone) + '">' + escapeHtml(decisionLabel) + '</span><p><strong>자료 상태 ' + escapeHtml(qualityMeta.label) + '</strong><em>' + escapeHtml(["기업 자료 " + instrumentValuationStateLabel(company.state), "신선도 " + instrumentValuationStateLabel(quality.freshnessStatus), instrumentValuationStateLabel(valuation.reviewStatus)].join(" · ")) + '</em></p></div>',
    missing.length ? '<div class="instrument-valuation-missing"><strong>더 필요한 자료</strong><ul>' + missing.map(function (item) { return '<li>' + escapeHtml(instrumentValuationMissingLabel(item)) + '</li>'; }).join("") + '</ul></div>' : '<div class="instrument-valuation-missing is-complete"><strong>필수 누락 자료 없음</strong><p>그래도 적정가는 범위로 보고 실제 실적 변화와 함께 재검토해야 합니다.</p></div>',
    '</section>',
    '<section class="instrument-valuation-sources"><div class="instrument-valuation-section-head"><div><span class="label">PROVENANCE</span><h4>출처와 기준일</h4></div><span>' + escapeHtml(formatClock(valuation.asOf || company.sourceAsOf || instrument.priceAsOf)) + '</span></div>',
    sourceRows.length ? '<div>' + sourceRows.map(function (source) { var scopes = Array.isArray(source.scopes) && source.scopes.length ? source.scopes : [source.scope]; return '<p><strong>' + escapeHtml(source.provider || "출처 미기록") + '</strong><span>' + escapeHtml(scopes.filter(Boolean).map(instrumentValuationSourceScopeLabel).join(" · ") || "기업 자료") + '</span><time>' + escapeHtml(formatClock(source.asOf)) + '</time></p>'; }).join("") + '</div>' : '<p class="instrument-valuation-explanation">출처 기록이 없습니다.</p>',
    '</section>',
    '</section>'
  ].join("");
}

function renderInstrumentTimelineSources(payload) {
  var sources = payload && Array.isArray(payload.sources) ? payload.sources : [];
  if (!sources.length) return "";
  return '<section class="instrument-source-strip" aria-label="사용 데이터 출처">' + sources.map(function (source) {
    return '<div><span class="tone-chip watch">ACTUAL</span><p><strong>' + escapeHtml(source.dataset || "데이터") + '</strong><em>' + escapeHtml([source.store, (source.providers || []).join(", "), source.count + "건"].filter(Boolean).join(" · ")) + '</em></p></div>';
  }).join("") + '</section>';
}

function renderInstrumentChart(row, view) {
  var payload = view.payload || {};
  var series = payload.series || {};
  var hasCandles = Array.isArray(series.candles) && series.candles.length > 0;
  var rangeOptions = [["1d", "1일"], ["1w", "1주"], ["1m", "1개월"], ["3m", "3개월"], ["6m", "6개월"], ["1y", "1년"], ["3y", "3년"], ["all", "전체"]];
  var ranges = '<div class="instrument-range-control" role="group" aria-label="차트 기간">' + rangeOptions.map(function (option) {
    return '<button type="button" data-instrument-range="' + option[0] + '" data-instrument-symbol="' + escapeHtml(row.symbol) + '" class="' + (view.range === option[0] ? "active" : "") + '">' + option[1] + '</button>';
  }).join("") + '</div>';
  var status = view.staticPreview
    ? '<div class="instrument-chart-state"><strong>GitHub Pages는 정적 화면입니다.</strong><p>실시간 타임라인 API와 운영 DB는 로컬 또는 공유 앱에서 조회할 수 있습니다. 정적 화면에는 임의의 캔들을 표시하지 않습니다.</p></div>'
    : view.loading && !hasCandles
    ? '<div class="instrument-chart-state is-loading"><span></span><strong>가격·사건 데이터를 조회하고 있습니다.</strong></div>'
    : view.error && !hasCandles
      ? '<div class="instrument-chart-state is-error"><strong>' + escapeHtml(view.error) + '</strong><button type="button" class="text-button" data-instrument-timeline-refresh="' + escapeHtml(row.symbol) + '">다시 조회</button></div>'
      : series.availability === "no-data"
        ? '<div class="instrument-chart-state"><strong>저장된 실제 시계열이 없습니다.</strong><p>모의 캔들은 표시하지 않습니다. 다음 시세 수집 후 이 위치에 실제 데이터가 나타납니다.</p><button type="button" class="text-button" data-instrument-timeline-refresh="' + escapeHtml(row.symbol) + '">새로고침</button></div>'
        : '<div class="instrument-candle-chart" data-instrument-candle-chart="' + escapeHtml(view.payloadKey || view.key) + '" aria-label="' + escapeHtml((row.name || row.symbol) + " 실제 캔들 차트. 사건 표식을 선택하면 연결된 상세를 엽니다.") + '"></div>';
  var continuity = hasCandles && view.loading
    ? '<div class="instrument-chart-refreshing" role="status"><span aria-hidden="true"></span><strong>' + escapeHtml(view.range) + ' 구간을 준비하는 동안 이전 차트를 유지합니다.</strong></div>'
    : (hasCandles && view.stale && view.error
      ? '<div class="instrument-chart-refreshing is-error" role="status"><strong>이전 차트를 유지 중입니다.</strong><button type="button" class="text-button compact" data-instrument-timeline-refresh="' + escapeHtml(row.symbol) + '">다시 조회</button></div>'
      : "");
  var eventCounts = (payload.events || []).reduce(function (counts, event) {
    counts[event.type] = (counts[event.type] || 0) + 1;
    return counts;
  }, {});
  var markerProjection = instrumentChartEventProjection(payload.events || [], series.candles || [], {
    chartWidth: Math.max(280, Math.min(980, Number(window.innerWidth || 720) - 48))
  });
  return [
    '<section class="instrument-chart-workspace">',
    '<header><div><span class="label">PRICE + EVENTS</span><h3>가격과 사건을 한 흐름으로 보기</h3><p>같은 캔들의 사건은 하나의 표식으로 묶고 대표 유형과 건수를 표시합니다.</p></div><span class="tone-chip ' + escapeHtml(view.staticPreview ? "caution" : "watch") + '">' + escapeHtml(view.staticPreview ? "STATIC" : "ACTUAL") + '</span></header>',
    ranges,
    continuity,
    '<div class="instrument-chart-meta"><span>간격 <strong>' + escapeHtml((payload.query || {}).interval || "-") + '</strong></span><span>캔들 <strong>' + escapeHtml(series.pointCount || 0) + '개</strong></span><span>사건 <strong>' + escapeHtml((payload.events || []).length) + '건</strong><small>차트 표식 ' + escapeHtml(markerProjection.markerCount) + '개</small></span><span>최신 <strong>' + escapeHtml(formatClock(series.latestAt)) + '</strong></span></div>',
    status,
    '<div class="instrument-event-legend" aria-label="사건 유형별 상세">' + [
      ["evidence", "뉴스"],
      ["calendar", "일정"],
      ["decision", "판단"],
      ["hypothesis", "가설"],
      ["notification", "알림"]
    ].map(function (item) {
      var count = Number(eventCounts[item[0]] || 0);
      return '<button type="button" class="' + item[0] + '" data-instrument-event-group="' + escapeHtml(view.payloadKey || view.key) + '" data-instrument-event-selector="type:' + item[0] + '"' + (count ? '' : ' disabled') + '>' + escapeHtml(item[1] + " " + count) + '</button>';
    }).join("") + '</div>',
    renderInstrumentTimelineSources(payload),
    '</section>'
  ].join("");
}

function instrumentEventTypeLabel(type) {
  var labels = { evidence: "뉴스·공시", calendar: "일정", decision: "투자 판단", hypothesis: "가설 전환", notification: "알림" };
  return labels[String(type || "")] || String(type || "사건");
}

function renderInstrumentEventRow(event) {
  var target = instrumentEventDetailTarget(event);
  var rowKey = event.id || [event.type, event.occurredAt].join(":");
  var opening = target
    ? '<button type="button" class="instrument-timeline-event ' + escapeHtml(event.tone || "neutral") + '" data-console-row-key="' + escapeHtml(rowKey) + '" data-work-detail="' + escapeHtml(target.type) + '" data-work-detail-key="' + escapeHtml(target.key) + '" aria-label="' + escapeHtml((event.title || "상태 변경") + " 상세 보기") + '">'
    : '<article class="instrument-timeline-event ' + escapeHtml(event.tone || "neutral") + '" data-console-row-key="' + escapeHtml(rowKey) + '">';
  return [
    opening,
    '<time datetime="' + escapeHtml(event.occurredAt || "") + '">' + escapeHtml(formatClock(event.occurredAt)) + '</time>',
    '<span class="instrument-event-type">' + escapeHtml(instrumentEventTypeLabel(event.type)) + '</span>',
    '<div><strong>' + escapeHtml(event.title || "상태 변경") + '</strong><p>' + escapeHtml(event.summary || "상세 설명이 기록되지 않았습니다.") + '</p><em>' + escapeHtml(event.source || "출처 미기록") + '</em></div>',
    target ? '<span class="instrument-timeline-event-action">상세 <b aria-hidden="true">&rarr;</b></span>' : '',
    target ? '</button>' : '</article>'
  ].join("");
}

function instrumentTimelineEventReference(detailType, detailKey) {
  var type = String(detailType || "");
  var key = String(detailKey || "");
  var payloadKeys = Object.keys(instrumentsState.instrumentTimelines || {}).reverse();
  for (var index = 0; index < payloadKeys.length; index += 1) {
    var payload = instrumentsState.instrumentTimelines[payloadKeys[index]] || {};
    var event = (payload.events || []).filter(function (item) {
      return String(item.detailType || "") === type && String(item.detailKey || "") === key;
    })[0];
    if (event) return { event: event, payload: payload, payloadKey: payloadKeys[index] };
  }
  return null;
}

function instrumentEventMetadataValue(value) {
  if (value == null || value === "") return "-";
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  }
  return String(value);
}

function instrumentEventMetadataLabel(key) {
  var labels = {
    evidenceRole: "근거 역할",
    dataState: "데이터 상태",
    eventType: "일정 유형",
    importance: "중요도",
    inferenceGenerationId: "추론 세대",
    decisionReadiness: "판단 준비 상태",
    previousState: "이전 상태",
    currentState: "현재 상태",
    materialChange: "중요 변화",
    status: "발송 상태",
    messageType: "알림 유형"
  };
  return labels[key] || key;
}

function instrumentTimelineEventWorkDetailPayload(detailType, detailKey) {
  var reference = instrumentTimelineEventReference(detailType, detailKey);
  if (!reference) return null;
  var event = reference.event;
  var payload = reference.payload || {};
  var instrument = payload.instrument || {};
  var metadata = event.metadata && typeof event.metadata === "object" ? event.metadata : {};
  var metadataRows = Object.keys(metadata).map(function (key) {
    return '<div class="work-detail-row"><b>' + escapeHtml(instrumentEventMetadataLabel(key)) + '</b><div><strong>' + escapeHtml(instrumentEventMetadataValue(metadata[key])) + '</strong></div></div>';
  }).join("");
  return {
    kicker: "Instrument Event",
    title: event.title || instrumentEventTypeLabel(event.type),
    meta: [instrument.name || instrument.symbol, instrument.symbol, instrumentEventTypeLabel(event.type), formatClock(event.occurredAt)].filter(Boolean).join(" · "),
    body: [
      renderInstrumentWorkspaceLink(instrument.symbol, "종목 전체 흐름"),
      '<section class="work-detail-section primary"><strong>사건 요약</strong><p>' + escapeHtml(event.summary || "상세 설명이 기록되지 않았습니다.") + '</p></section>',
      '<section class="work-detail-section"><strong>원본 기록</strong><div class="work-detail-list">',
      '<div class="work-detail-row"><b>발생 시각</b><div><strong>' + escapeHtml(formatClock(event.occurredAt)) + '</strong></div></div>',
      '<div class="work-detail-row"><b>출처</b><div><strong>' + escapeHtml(event.source || "출처 미기록") + '</strong></div></div>',
      '<div class="work-detail-row"><b>기록 ID</b><div><strong>' + escapeHtml(event.id || detailKey || "-") + '</strong></div></div>',
      metadataRows,
      '</div></section>'
    ].join("")
  };
}

function instrumentEventGroupWorkDetailPayload(key) {
  var parsed = parseInstrumentEventGroupKey(key);
  if (!parsed) return null;
  var payload = (instrumentsState.instrumentTimelines || {})[parsed.payloadKey];
  var error = String((instrumentsState.instrumentTimelineErrors || {})[parsed.payloadKey] || "");
  if (!payload) {
    return {
      kicker: "Chart Events",
      title: error ? "차트 사건을 불러오지 못했습니다" : "차트 사건을 불러오는 중",
      meta: [parsed.symbol, parsed.range].filter(Boolean).join(" · "),
      body: error
        ? '<div class="instrument-chart-state is-error"><strong>' + escapeHtml(error) + '</strong><button type="button" class="text-button" data-instrument-timeline-refresh="' + escapeHtml(parsed.symbol) + '">다시 조회</button></div>'
        : '<div class="work-detail-loading"><span class="spinner"></span><p>선택한 기간의 실제 사건 기록을 읽고 있습니다.</p></div>'
    };
  }
  var events = instrumentEventGroupRows(parsed.payloadKey, parsed.selector);
  var instrument = payload.instrument || {};
  var type = parsed.selector.indexOf("type:") === 0 ? parsed.selector.slice(5) : "";
  var epoch = parsed.selector.indexOf("time:") === 0 ? Number(parsed.selector.slice(5)) : NaN;
  var selectedAt = Number.isFinite(epoch) ? new Date(epoch * 1000).toISOString() : "";
  var title = type
    ? instrumentEventTypeLabel(type) + " " + events.length + "건"
    : formatClock(selectedAt) + " 사건 " + events.length + "건";
  return {
    kicker: type ? "Event Category" : "Chart Event Cluster",
    title: title,
    meta: [instrument.name || instrument.symbol || parsed.symbol, parsed.range, "최신 변경순"].filter(Boolean).join(" · "),
    body: events.length
      ? '<section class="instrument-event-group-detail"><div class="instrument-timeline-list" data-console-keyed-list="instrument-event-group">' + events.map(renderInstrumentEventRow).join("") + '</div>' + renderInstrumentTimelineSources(payload) + '</section>'
      : '<div class="instrument-empty"><strong>연결된 사건이 없습니다.</strong><p>현재 기간의 실제 사건 기록에서 선택한 항목을 찾지 못했습니다.</p></div>'
  };
}

function renderInstrumentDecision(row, view) {
  var events = ((view.payload || {}).events || []).filter(function (event) {
    return event.type === "decision" || event.type === "hypothesis";
  });
  var current = row.decision
    ? '<section class="work-detail-section primary"><span class="label">CURRENT DECISION</span><strong>' + escapeHtml(row.decision.action || row.decision.decision || "판단 확인") + '</strong><p>' + escapeHtml((row.decision.reasons || [])[0] || "판단 근거 상세를 확인하세요.") + '</p>' + (row.decision.recency && row.decision.recency.state !== "current" ? '<p class="caution">' + escapeHtml(row.decision.recency.label) + ' · 저장 당시의 의견입니다.</p>' : '') + renderWorkDetailButton(row.decision.consoleDetailType || "investment-action", row.decision.consoleKey, "판단 근거 전체", "text-button primary compact") + '</section>'
    : '<div class="instrument-empty"><strong>현재 연결된 투자 판단이 없습니다.</strong><p>추론과 검증을 통과한 판단이 생성되면 가설 세대와 함께 표시합니다.</p></div>';
  return [
    '<section class="instrument-decision-workspace">',
    current,
    '<header><div><span class="label">REASONING HISTORY</span><h3>판단과 가설 변화</h3><p>최종 행동만 보지 않고 어떤 가설 세대에서 상태가 바뀌었는지 추적합니다.</p></div>',
    renderWorkDetailButton("strategy-graphs-board", "", "관계·규칙 보기", "text-button compact"),
    '</header>',
    events.length ? '<div class="instrument-timeline-list" data-console-keyed-list="instrument-decision-events">' + events.map(renderInstrumentEventRow).join("") + '</div>' : '<div class="instrument-empty"><strong>저장된 판단·가설 변경 이력이 없습니다.</strong><p>실제 추론 이력만 표시하며 빈 구간을 임의로 채우지 않습니다.</p></div>',
    '</section>'
  ].join("");
}

function renderInstrumentTimeline(row, view) {
  var events = (view.payload || {}).events || [];
  if (view.staticPreview) return '<div class="instrument-chart-state"><strong>정적 화면에서는 운영 타임라인을 조회하지 않습니다.</strong><p>로컬 또는 공유 앱에서 실제 운영 DB의 뉴스·일정·판단·가설·알림 이력을 확인하세요.</p></div>';
  if (view.loading && !events.length) return '<div class="instrument-chart-state is-loading"><span></span><strong>전체 사건을 불러오고 있습니다.</strong></div>';
  if (view.error && !events.length) return '<div class="instrument-chart-state is-error"><strong>' + escapeHtml(view.error) + '</strong><button type="button" class="text-button" data-instrument-timeline-refresh="' + escapeHtml(row.symbol) + '">다시 조회</button></div>';
  return [
    '<section class="instrument-timeline-workspace">',
    '<header><div><span class="label">AUDIT TIMELINE</span><h3>종목 사건 타임라인</h3><p>뉴스·공시·일정·판단·가설 전환·알림을 변경 시각 최신순으로 추적합니다.</p></div><strong>' + escapeHtml(events.length) + '건</strong></header>',
    events.length ? '<div class="instrument-timeline-list" data-console-keyed-list="instrument-all-events">' + events.map(renderInstrumentEventRow).join("") + '</div>' : '<div class="instrument-empty"><strong>연결된 사건이 없습니다.</strong><p>실제 운영 DB에 저장된 사건이 생기면 이 위치에 표시합니다.</p></div>',
    renderInstrumentTimelineSources(view.payload),
    '</section>'
  ].join("");
}

function marketInstrumentWorkDetailPayload(key) {
  var symbol = String(key || "").toUpperCase();
  var row = selectConsoleInstrumentRows(shellState.snapshot || {}).filter(function (item) { return item.symbol === symbol; })[0];
  if (!row) return null;
  var active = instrumentWorkspaceTab(symbol);
  var view = instrumentTimelineViewState(symbol);
  var valuationView = instrumentValuationViewState(symbol);
  var content = active === "chart"
    ? renderInstrumentChart(row, view)
    : active === "valuation"
      ? renderInstrumentValuation(row, valuationView)
      : active === "decision"
        ? renderInstrumentDecision(row, view)
        : active === "timeline"
          ? renderInstrumentTimeline(row, view)
          : renderInstrumentSummary(row);
  return {
    kicker: "Instrument Workspace",
    title: row.name || row.symbol,
    meta: [row.symbol, row.market, row.source === "watchlist" ? "관심" : "보유", "최종 변경 " + formatClock(row.updatedAt)].filter(Boolean).join(" · "),
    body: renderInstrumentWorkspaceNavigation(symbol) + '<div class="instrument-workspace-body" data-instrument-workspace-active="' + escapeHtml(active) + '">' + content + '</div>'
  };
}

export { instrumentEventGroupWorkDetailPayload, instrumentTimelineEventWorkDetailPayload, marketInstrumentWorkDetailPayload, renderInstrumentWorkspaceLink };
