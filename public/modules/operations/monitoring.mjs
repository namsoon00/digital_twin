import { buildTradeSignalItems, currentPriceOf, decisionStateMeta, instrumentItems } from "../decisions/signals.mjs";
import { clientKnownStockInfo, stockDisplayMeta, stockDisplayName, textWithDisplaySymbol } from "../instruments/catalog.mjs";
import { marketLabel } from "../instruments/universe-view.mjs";
import { alertRuleLabel, alertSeverityLabel, buildAlertItems } from "../notifications/alerts.mjs";
import { normalizeRealtimeEvent, notificationJobSummaryText } from "../realtime/events.mjs";
import { realtimeEventLabel } from "../realtime/labels.mjs";
import { formatClock, formatCurrency, formatMoney, formatSignalNumber, formatSignalRatio, formatSignalVolume, latestChangedFirst, pct, recordChangedAt, renderRecordChangedAt, signedMoney, signedPct, sourceLabel } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { accountsState } from "../state/accounts.mjs";
import { navigationState } from "../state/navigation.mjs";
import { shellState } from "../state/shell.mjs";

function renderMonitorLedgerCell(label, value, tone) {
  return [
    '<div class="monitor-ledger-cell ' + escapeHtml(tone || "") + '"' + cardTypeAttrs("health-card", tone || "neutral") + '>',
    '<span>' + escapeHtml(label || "-") + '</span>',
    '<strong>' + escapeHtml(value == null ? "-" : value) + '</strong>',
    '</div>'
  ].join("");
}

function renderMonitorRuntimeRow(label, value, detail, tone) {
  return [
    '<div class="monitor-runtime-row ' + escapeHtml(tone || "") + '"' + cardTypeAttrs("health-card", tone || "neutral") + '>',
    '<span>' + escapeHtml(label || "-") + '</span>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    detail ? '<em>' + escapeHtml(detail) + '</em>' : '',
    '</div>'
  ].join("");
}

function renderMonitorAlertSummary() {
  var event = normalizeRealtimeEvent(shellState.realtime.monitoring && shellState.realtime.monitoring.alerts);
  var payload = event && event.payload || {};
  var count = Number(payload.count || 0);
  var symbols = Array.isArray(payload.symbols) ? payload.symbols.slice(0, 4) : [];
  var symbolText = symbols.length ? symbols.map(function (symbol) {
    return stockDisplayName(symbol, clientKnownStockInfo(symbol));
  }).join(", ") : "최근 감지된 모니터링 알림 없음";
  return [
    '<section class="monitor-alert-summary ' + escapeHtml(count ? "active" : "idle") + '"' + cardTypeAttrs("signal-card", count ? "watch" : "hold") + '>',
    '<span>최근 모니터링 알림</span>',
    '<strong>' + escapeHtml(count ? count + "건 감지" : "대기 중") + '</strong>',
    '<p>' + escapeHtml(symbolText) + '</p>',
    '<em>' + escapeHtml(event && event.occurredAt ? formatClock(event.occurredAt) : "알림 이벤트 대기") + '</em>',
    '</section>'
  ].join("");
}

function renderMonitorRuntimeBoard() {
  var cycleEvent = normalizeRealtimeEvent(shellState.realtime.monitoring && shellState.realtime.monitoring.cycle);
  var cyclePayload = cycleEvent && cycleEvent.payload || {};
  var cycleValue = cycleEvent
    ? "스냅샷 " + Number(cyclePayload.snapshotCount || 0) + " · 알림 " + Number(cyclePayload.alertCount || 0)
    : "사이클 대기";
  var cycleDetail = cycleEvent && cycleEvent.occurredAt ? formatClock(cycleEvent.occurredAt) : "모니터링 사이클 이벤트 대기";
  return [
    '<section class="monitor-board-section monitor-runtime-strip monitor-runtime-board" aria-label="모니터링 런타임 상태">',
    '<div class="monitor-section-head">',
    '<strong>런타임 신호</strong>',
    '<span>실시간 연결, 사이클, 큐 상태</span>',
    '</div>',
    '<div class="monitor-runtime-timeline">',
    renderMonitorRuntimeRow("웹소켓 최근 이벤트", shellState.realtime.lastEvent ? realtimeEventLabel(shellState.realtime.lastEvent) : "이벤트 대기", shellState.realtime.lastEventAt ? formatClock(shellState.realtime.lastEventAt) : "연결 이벤트 대기", shellState.realtime.connected ? "live" : ""),
    renderMonitorRuntimeRow("최근 모니터링 사이클", cycleValue, cycleDetail, ""),
    renderMonitorRuntimeRow("알림 큐", notificationJobSummaryText(shellState.realtime.notificationJobs), "notification worker queue", "live"),
    '</div>',
    renderMonitorAlertSummary(),
    '</section>'
  ].join("");
}

function renderAdminMonitoringPanel(snapshot) {
  var toss = snapshot.toss || {};
  var portfolio = snapshot.portfolio || {};
  var accounts = accountsState.serviceAccounts || [];
  var enabledCount = accounts.filter(function (account) { return account.enabled !== false; }).length;
  var positions = ((toss.positions || []) || []).filter(function (item) {
    return item.source !== "cash" && item.sector !== "현금";
  });
  var healthRows = [
    ["활성 계정", enabledCount + "/" + accounts.length, "live"],
    ["보유 종목", positions.length + "개", ""],
    ["평가 금액", formatMoney(portfolio.total || 0), ""],
    ["토스 연결", toss.status || "-", ""],
    ["마지막 갱신", formatClock(snapshot.generatedAt), ""]
  ];
  var marketRows = (portfolio.markets || []).map(function (market) {
    return renderMonitorLedgerCell(market.label || market.key || "-", "현금 " + pct(market.cashRatio || 0), "");
  }).join("");
  var liveLabel = snapshot.preview ? "정적 미리보기" : "실데이터 실행";
  return [
    '<article class="panel admin-monitoring-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Monitoring</p>',
    '<h2>모니터링 실행 상태</h2>',
    '</div>',
    '<span class="status-pill ' + (snapshot.preview ? "demo" : "live") + '">' + escapeHtml(snapshot.preview ? "Preview" : "Live") + '</span>',
    '</div>',
    '<div class="monitor-board">',
    '<section class="monitor-board-section monitor-status-board">',
    '<div class="monitor-section-head">',
    '<strong>현재 실행 상태</strong>',
    '<span>연결 상태와 핵심 지표</span>',
    '</div>',
    '<div class="monitor-primary-state">',
    '<div class="monitor-primary-head">',
    '<span class="tone-chip ' + (snapshot.preview ? "hold" : "watch") + '">' + escapeHtml(liveLabel) + '</span>',
    '</div>',
    '<div class="monitor-primary-copy">',
    '<strong>' + escapeHtml(toss.status || "연결 상태 확인") + '</strong>',
    '<em>최근 데이터 ' + escapeHtml(formatClock(snapshot.generatedAt)) + ' · ' + escapeHtml(realtimeEventLabel(shellState.realtime.lastEvent)) + '</em>',
    '</div>',
    '</div>',
    '<div class="monitor-health-ledger">',
    healthRows.map(function (row) {
      return renderMonitorLedgerCell(row[0], row[1], row[2]);
    }).join(""),
    '</div>',
    '</section>',
    renderMonitorRuntimeBoard(),
    marketRows ? '<section class="monitor-board-section monitor-market-section"><div class="monitor-section-head"><strong>시장별 현금</strong><span>매수 여력 기준</span></div><div class="monitor-market-ledger">' + marketRows + '</div></section>' : '',
    '</div>',
    '<div class="rule-strip"><span>실제 백그라운드 워커 실행/중지는 로컬 명령으로 관리하고, 웹은 저장된 계정과 알림 설정을 같은 로컬 DB/설정 파일에 기록합니다.</span></div>',
    '</article>'
  ].join("");
}

function renderMonitoringInstrumentPanel(snapshot) {
  var items = latestChangedFirst(instrumentItems(snapshot), function (item) {
    return recordChangedAt(item, snapshot.generatedAt);
  });
  var signalMap = {};
  buildTradeSignalItems(snapshot).forEach(function (item) {
    signalMap[item.symbol] = item;
  });
  var holdings = items.filter(function (item) { return item.source !== "watchlist"; }).length;
  var watch = items.length - holdings;
  var priced = items.filter(function (item) { return Boolean(currentPriceOf(item)); }).length;
  return [
    '<article class="panel monitoring-instrument-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Monitoring Universe</p>',
    '<h2>보유·관심 종목 통합</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(items.length) + '</span>',
    '</div>',
    '<div class="monitoring-instrument-summary">',
    '<div><span>보유</span><strong>' + escapeHtml(holdings) + '</strong></div>',
    '<div><span>관심</span><strong>' + escapeHtml(watch) + '</strong></div>',
    '<div><span>시세</span><strong>' + escapeHtml(priced) + '</strong></div>',
    '</div>',
    '<div class="monitoring-instrument-list">',
    items.length ? items.map(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      return renderMonitoringInstrumentRow(item, signalMap[symbol]);
    }).join("") : renderEmptyState({
      tone: "muted",
      label: "Universe",
      title: "보유·관심 종목이 아직 없습니다",
      description: "계정·연결에서 연결을 확인하거나 관심 관리에서 추적 대상을 추가하면 모니터링 원장이 채워집니다.",
      meta: ["계정 연결", "관심종목"]
    }),
    '</div>',
    '</article>'
  ].join("");
}

function renderMonitoringInstrumentRow(item, signal) {
  var symbol = String(item.symbol || "").toUpperCase();
  var holding = item.source !== "watchlist";
  var price = currentPriceOf(item);
  var sourceLabel = holding ? "보유" : "관심";
  var sourceClass = holding ? "holding" : "watchlist";
  var valueText = holding
    ? formatCurrency(item.marketValue || 0, item.currency)
    : (price ? formatCurrency(price, item.currency) : "시세 대기");
  var detailText = holding
    ? "수량 " + (item.quantity || "-") + " · 평단 " + formatCurrency(item.averagePrice || 0, item.currency)
    : (item.quoteStatus || "관심 기준 관찰");
  var performanceText = holding
    ? signedMoney(item.profitLoss, item.currency) + " · " + signedPct(item.profitLossRate)
    : (item.changeRate == null ? "등락률 대기" : signedPct(item.changeRate));
  var signalText = signal && signal.hasData
    ? decisionStateMeta("review", signal.reviewLevel, "observe").label + " · " + decisionStateMeta("data", signal.dataState, "partial").label
    : "수급 입력 필요";
  var displayName = stockDisplayName(symbol, item);
  return [
    '<div class="monitoring-instrument-row"' + cardTypeAttrs("signal-card", signal && signal.tone ? signal.tone : "hold") + cardFormatAttrs("summary-list-card", "compact") + ' role="button" tabindex="0" data-monitor-instrument-detail="' + escapeHtml(symbol) + '" aria-label="' + escapeHtml(displayName + " 상세 보기") + '">',
    '<div class="monitoring-instrument-main">',
    '<div class="monitoring-instrument-title">',
    '<strong>' + escapeHtml(displayName) + '</strong>',
    '<span class="source-chip ' + escapeHtml(sourceClass) + '">' + escapeHtml(sourceLabel) + '</span>',
    '</div>',
    '<span>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || "-"), item.sector || "-"])) + '</span>',
    '<span>' + escapeHtml(detailText) + '</span>',
    renderRecordChangedAt(item),
    '</div>',
    '<div class="monitoring-instrument-side">',
    '<strong>' + escapeHtml(valueText) + '</strong>',
    '<span>' + escapeHtml(performanceText) + '</span>',
    '<span class="tone-chip ' + escapeHtml(signal && signal.tone ? signal.tone : "hold") + '">' + escapeHtml(signal && signal.action ? signal.action : "관찰") + '</span>',
    '<em>' + escapeHtml(signalText) + '</em>',
    '</div>',
    '</div>'
  ].join("");
}

function monitoringSignalItemBySymbol(snapshot, symbol) {
  var target = String(symbol || "").toUpperCase();
  if (!target) return null;
  var items = buildTradeSignalItems(snapshot || shellState.snapshot || {});
  return items.filter(function (item) {
    return String(item.symbol || "").toUpperCase() === target;
  })[0] || null;
}

function monitoringAlertByIndex(snapshot, index) {
  var alerts = buildAlertItems(snapshot || shellState.snapshot || {});
  var selectedIndex = Number(index);
  if (!Number.isFinite(selectedIndex) || selectedIndex < 0 || selectedIndex >= alerts.length) return null;
  return alerts[selectedIndex];
}

function monitoringDetailCurrency(value, currency) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "-";
  return formatCurrency(number, currency);
}

function monitoringDetailQuantity(value) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "-";
  return number.toLocaleString("ko-KR", {
    maximumFractionDigits: Number.isInteger(number) ? 0 : 4
  });
}

function renderMonitoringDetailMetric(label, value, tone) {
  var displayValue = value == null || value === "" ? "-" : value;
  return [
    '<span class="monitoring-detail-metric ' + escapeHtml(tone || "") + '">',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(displayValue) + '</strong>',
    '</span>'
  ].join("");
}

function renderMonitoringDetailSignalGrid(item) {
  var signal = item.signal || {};
  return [
    '<div class="monitoring-detail-block">',
    '<div class="flow-title">',
    '<div>',
    '<strong>신호 입력값</strong>',
    '<span>성립 조건과 근거 역할을 확인하는 수급, 추세, 투자자별 실제 값입니다.</span>',
    '</div>',
    '</div>',
    '<div class="monitoring-detail-signal-grid">',
    renderMonitoringDetailMetric("거래량 배율", formatSignalRatio(signal.volumeRatio)),
    renderMonitoringDetailMetric("매수량", formatSignalVolume(signal.buyVolume)),
    renderMonitoringDetailMetric("매도량", formatSignalVolume(signal.sellVolume)),
    renderMonitoringDetailMetric("호가 불균형", formatSignalNumber(signal.bidAskImbalance, "%")),
    renderMonitoringDetailMetric("가격 변화", formatSignalNumber(signal.priceChangeRate, "%")),
    renderMonitoringDetailMetric("20일선", formatSignalNumber(signal.ma20, "")),
    renderMonitoringDetailMetric("60일선", formatSignalNumber(signal.ma60, "")),
    renderMonitoringDetailMetric("외국인 순매수", formatSignalVolume(signal.foreignNet)),
    renderMonitoringDetailMetric("기관 순매수", formatSignalVolume(signal.institutionNet)),
    renderMonitoringDetailMetric("개인 순매수", formatSignalVolume(signal.individualNet)),
    '</div>',
    '</div>'
  ].join("");
}

function renderMonitoringDetailReasons(item) {
  var reasons = Array.isArray(item.reasons) ? item.reasons : [];
  return [
    '<div class="monitoring-detail-block">',
    '<div class="flow-title">',
    '<div>',
    '<strong>판단 근거</strong>',
    '<span>현재 라벨을 만든 데이터 해석입니다.</span>',
    '</div>',
    '</div>',
    '<div class="monitoring-detail-reasons">',
    reasons.length ? reasons.map(function (reason) {
      return '<p>' + escapeHtml(reason) + '</p>';
    }).join("") : '<p>표시할 판단 근거가 없습니다.</p>',
    '</div>',
    '</div>'
  ].join("");
}

function renderMonitoringDetailTriggers(item) {
  var triggers = Array.isArray(item.triggers) ? item.triggers : [];
  if (!triggers.length) return "";
  return [
    '<div class="trigger-list monitoring-detail-triggers">',
    triggers.map(function (trigger) {
      return '<span>' + escapeHtml(trigger) + '</span>';
    }).join(""),
    '</div>'
  ].join("");
}

function renderMonitoringInstrumentDetail(item) {
  var sourceClass = item.source === "holding" ? "holding" : "watchlist";
  var valuationText = item.valuation && item.valuation.status ? item.valuation.status : "가정 대기";
  var pnlText = item.source === "holding"
    ? signedMoney(item.profitLoss, item.currency) + " · " + signedPct(item.profitLossRate)
    : "-";
  var displayName = stockDisplayName(item.symbol, item);
  return [
    '<div class="monitoring-detail-content">',
    '<div class="monitoring-detail-head">',
    '<div>',
    '<p class="label">Instrument Detail</p>',
    '<h2>' + escapeHtml(displayName) + '</h2>',
    '<span>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || "-"), item.sector || "-"])) + '</span>',
    '</div>',
    '<div class="monitoring-detail-badges">',
    '<span class="source-chip ' + escapeHtml(sourceClass) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
    '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.action || "관망") + '</span>',
    '</div>',
    '</div>',
    '<div class="monitoring-detail-metric-grid">',
    renderMonitoringDetailMetric("현재가", monitoringDetailCurrency(item.currentPrice, item.currency)),
    renderMonitoringDetailMetric("평가액", monitoringDetailCurrency(item.marketValue, item.currency)),
    renderMonitoringDetailMetric("평단", monitoringDetailCurrency(item.averagePrice, item.currency)),
    renderMonitoringDetailMetric("수량", monitoringDetailQuantity(item.quantity)),
    renderMonitoringDetailMetric("손익", pnlText, Number(item.profitLoss || 0) < 0 ? "sell" : "buy"),
    renderMonitoringDetailMetric("확인 단계", decisionStateMeta("review", item.reviewLevel, "observe").label, decisionStateMeta("review", item.reviewLevel, "observe").tone),
    renderMonitoringDetailMetric("자료 상태", decisionStateMeta("data", item.dataState, "partial").label, decisionStateMeta("data", item.dataState, "partial").tone),
    renderMonitoringDetailMetric("근거 관계", decisionStateMeta("conflict", item.conflictState, "context-only").label),
    renderMonitoringDetailMetric("AI 검증", decisionStateMeta("validation", item.validationState, "conditional").label),
    renderMonitoringDetailMetric("매수 체결비중", item.hasData ? pct(item.buyShare) : "-"),
    renderMonitoringDetailMetric("가치 판단", valuationText),
    '</div>',
    renderMonitoringDetailSignalGrid(item),
    renderMonitoringDetailReasons(item),
    renderMonitoringDetailTriggers(item),
    '</div>'
  ].join("");
}

function renderMonitoringAlertRelatedInstrument(item) {
  if (!item) return "";
  return [
    '<div class="monitoring-detail-block">',
    '<div class="flow-title">',
    '<div>',
    '<strong>관련 종목 신호</strong>',
    '<span>알림이 가리키는 종목의 현재 행동과 확인 상태입니다.</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.action || "관망") + '</span>',
    '</div>',
    '<div class="monitoring-detail-metric-grid compact">',
    renderMonitoringDetailMetric("현재가", monitoringDetailCurrency(item.currentPrice, item.currency)),
    renderMonitoringDetailMetric("확인 단계", decisionStateMeta("review", item.reviewLevel, "observe").label),
    renderMonitoringDetailMetric("자료 상태", decisionStateMeta("data", item.dataState, "partial").label),
    renderMonitoringDetailMetric("변화", decisionStateMeta("change", item.changeState, "unchanged").label),
    renderMonitoringDetailMetric("매수 체결비중", item.hasData ? pct(item.buyShare) : "-"),
    '</div>',
    '<div class="monitoring-detail-reasons compact">',
    (item.reasons || []).map(function (reason) {
      return '<p>' + escapeHtml(reason) + '</p>';
    }).join(""),
    '</div>',
    '</div>'
  ].join("");
}

function renderMonitoringAlertDetail(alert, relatedItem) {
  var severity = alert.severity || "info";
  var displaySymbol = alert.symbol ? stockDisplayName(alert.symbol, relatedItem || alert) : "";
  var title = textWithDisplaySymbol(alert.title || "알림 상세", alert.symbol, relatedItem || alert);
  var message = textWithDisplaySymbol(alert.message || "세부 메시지가 없습니다.", alert.symbol, relatedItem || alert);
  return [
    '<div class="monitoring-detail-content">',
    '<div class="monitoring-detail-head">',
    '<div>',
    '<p class="label">Alert Detail</p>',
    '<h2>' + escapeHtml(title) + '</h2>',
    '<span>' + escapeHtml([displaySymbol || "", alert.source || "", alertRuleLabel(alert.rule)].filter(Boolean).join(" · ")) + '</span>',
    '</div>',
    '<div class="monitoring-detail-badges">',
    '<span class="alert-severity ' + escapeHtml(severity) + '">' + escapeHtml(alertSeverityLabel(severity)) + '</span>',
    '<span class="tone-chip hold">' + escapeHtml(alertRuleLabel(alert.rule)) + '</span>',
    '</div>',
    '</div>',
    '<div class="monitoring-detail-message">',
    '<strong>알림 메시지</strong>',
    '<p>' + escapeHtml(message) + '</p>',
    '</div>',
    '<div class="monitoring-detail-metric-grid">',
    renderMonitoringDetailMetric("종목", displaySymbol || "-"),
    renderMonitoringDetailMetric("출처", alert.source || "-"),
    renderMonitoringDetailMetric("현재", alert.value || "-"),
    renderMonitoringDetailMetric("기준", alert.threshold || "-"),
    renderMonitoringDetailMetric("심각도", alertSeverityLabel(severity)),
    renderMonitoringDetailMetric("규칙", alertRuleLabel(alert.rule)),
    '</div>',
    renderMonitoringAlertRelatedInstrument(relatedItem),
    '</div>'
  ].join("");
}

function renderMonitoringDetailEmpty() {
  return [
    '<div class="monitoring-detail-content">',
    '<div class="monitoring-detail-head">',
    '<div>',
    '<p class="label">Detail</p>',
    '<h2>상세 데이터를 찾지 못했습니다</h2>',
    '<span>스냅샷이 갱신되었거나 선택 항목이 사라졌습니다.</span>',
    '</div>',
    '</div>',
    '</div>'
  ].join("");
}

function renderMonitoringDetailOverlay(snapshot) {
  var selection = navigationState.monitoringDetail || {};
  if (!selection.type) return "";
  var content = "";
  if (selection.type === "instrument") {
    var item = monitoringSignalItemBySymbol(snapshot, selection.symbol);
    content = item ? renderMonitoringInstrumentDetail(item) : renderMonitoringDetailEmpty();
  } else if (selection.type === "alert") {
    var alert = monitoringAlertByIndex(snapshot, selection.index);
    var relatedItem = alert && alert.symbol ? monitoringSignalItemBySymbol(snapshot, alert.symbol) : null;
    content = alert ? renderMonitoringAlertDetail(alert, relatedItem) : renderMonitoringDetailEmpty();
  } else {
    content = renderMonitoringDetailEmpty();
  }
  return [
    '<div class="monitoring-detail-backdrop" data-monitoring-detail-close>',
    '<aside class="monitoring-detail-drawer" role="dialog" aria-modal="true" aria-label="모니터링 상세">',
    '<div class="monitoring-detail-toolbar">',
    '<span>상세 보기</span>',
    '<button class="icon-button" type="button" data-monitoring-detail-close aria-label="상세 닫기">&times;</button>',
    '</div>',
    content,
    '</aside>',
    '</div>'
  ].join("");
}

export { renderAdminMonitoringPanel, renderMonitoringDetailOverlay, renderMonitoringInstrumentPanel };
