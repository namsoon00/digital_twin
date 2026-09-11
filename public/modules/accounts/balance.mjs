import { renderAccountCommandCenter, renderAdminAccountPanel } from "./editor.mjs";
import { renderInfoIconButton, renderWorkDetailButton } from "../navigation/detail.mjs";
import { activePageMode, activeSectionForPageMode, modeSectionsForPage, normalizeAccountSection } from "../navigation/routes.mjs";
import { renderPortfolioLifecyclePanel } from "../portfolio/lifecycle.mjs";
import { exposureDiffText, portfolioCashBasisText, portfolioValuationBasisLabel, portfolioValuationMetricLabel, renderPortfolioBasisRows } from "../portfolio/valuation.mjs";
import { settingValue } from "../settings/fields.mjs";
import { formatClock, formatMoney, numeric } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { accountSections } from "../shell/catalog.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { accountsState } from "../state/accounts.mjs";
import { settingsState } from "../state/settings.mjs";

function accountSnapshotMode(snapshot) {
  var toss = (snapshot || {}).toss || {};
  if ((snapshot || {}).preview) return "preview";
  if ((snapshot || {}).mock || (snapshot || {}).dataMode === "mock") return "mock";
  return toss.mode || (snapshot && snapshot.dataMode) || "unknown";
}

function accountSnapshotModeLabel(snapshot) {
  var mode = accountSnapshotMode(snapshot);
  if (mode === "live") return "실제 데이터";
  if (mode === "mock") return "mock 데이터";
  if (mode === "preview") return "정적 미리보기";
  if (mode === "demo") return "demo 데이터";
  return "데이터 대기";
}

function accountSnapshotTone(snapshot) {
  var mode = accountSnapshotMode(snapshot);
  if (mode === "live") return "live";
  if (mode === "mock" || mode === "preview" || mode === "demo") return "demo";
  return "watch";
}

function timestampAgeMinutes(value) {
  if (!value) return null;
  var date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
}

function accountFreshness(snapshot) {
  var provided = (snapshot || {}).dataFreshness;
  if (provided && typeof provided === "object" && provided.status) {
    var providedAge = provided.ageMinutes;
    var providedMaxAge = provided.maxAgeMinutes;
    var providedDetail = providedAge == null
      ? String(provided.reason || "기준시각 없음")
      : String(providedAge) + "분 전 · 기준 " + String(providedMaxAge || "-") + "분";
    return {
      label: provided.label || (provided.status === "fresh" ? "신선" : (provided.status === "stale" ? "데이터 지연" : "기준시각 없음")),
      detail: providedDetail,
      tone: provided.status === "fresh" ? "ok" : "warn"
    };
  }
  var generatedAt = (snapshot || {}).generatedAt || "";
  var age = timestampAgeMinutes(generatedAt);
  var maxAge = Number(settingValue("marketDataMaxAgeMinutes") || settingValue("dataFreshnessDefaultMaxAgeMinutes") || 30);
  if (age == null) {
    return { label: "기준시각 없음", detail: "스냅샷 generatedAt 없음", tone: "warn" };
  }
  return {
    label: age <= maxAge ? "신선" : "지연",
    detail: age + "분 전 · 기준 " + maxAge + "분",
    tone: age <= maxAge ? "ok" : "warn"
  };
}

function accountSnapshotItems(snapshot) {
  var toss = (snapshot || {}).toss || {};
  var positions = Array.isArray(toss.positions) ? toss.positions : [];
  var watchlist = Array.isArray(toss.watchlist) ? toss.watchlist : [];
  return positions.concat(watchlist).filter(function (item) {
    return item && String(item.symbol || "").toUpperCase() !== "CASH";
  });
}

function accountDataQualityCounts(snapshot) {
  var mode = accountSnapshotMode(snapshot);
  return accountSnapshotItems(snapshot).reduce(function (memo, item) {
    var quality = String(item.dataQuality || "").toLowerCase();
    if (mode === "mock" || mode === "preview" || quality.indexOf("mock") >= 0 || quality.indexOf("demo") >= 0) {
      memo.mock += 1;
    } else if (quality.indexOf("cache") >= 0 || quality.indexOf("cached") >= 0) {
      memo.cached += 1;
    } else if (item.currentPrice || item.marketValue || item.quoteSource) {
      memo.actual += 1;
    } else {
      memo.pending += 1;
    }
    return memo;
  }, { actual: 0, cached: 0, mock: 0, pending: 0 });
}

function currentAccountLabel(snapshot) {
  var accounts = accountsState.serviceAccounts || [];
  if (accounts.length === 1) return accounts[0].label || accounts[0].id || "계정";
  var displayNumber = (((snapshot || {}).toss || {}).account || {}).displayNumber || "";
  if (displayNumber) return "현재 조회 계좌 " + displayNumber;
  return accounts.length ? "다중 계정" : "계정 미등록";
}

function renderAccountControlMetric(label, value, detail, tone, type) {
  return [
    '<span class="account-control-metric ' + escapeHtml(tone || "neutral") + '"' + cardTypeAttrs(type || "metric-cell", tone || "neutral") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value == null ? "-" : value) + '</strong>',
    detail ? '<b>' + escapeHtml(detail) + '</b>' : '',
    '</span>'
  ].join("");
}

function renderAccountApiStatusRow(label, value, detail, tone) {
  return [
    '<div class="account-api-row ' + escapeHtml(tone || "neutral") + '"' + cardTypeAttrs("health-card", tone || "neutral") + '>',
    '<span>' + escapeHtml(label) + '</span>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    '<em>' + escapeHtml(detail || "") + '</em>',
    '</div>'
  ].join("");
}

function renderAccountApiLedger(accounts, snapshot) {
  var configured = settingsState.serverConfigured || {};
  var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
  var accountSeqReady = accounts.filter(function (account) { return account.accountSeq; }).length;
  var items = accountSnapshotItems(snapshot);
  var kisItems = items.filter(function (item) {
    return String(item.quoteSource || item.signalSource || "").toLowerCase().indexOf("kis") >= 0;
  }).length;
  return [
    '<section class="account-api-ledger">',
    renderAccountApiStatusRow("Toss Open API", tossReady + "/" + accounts.length + " 계정", ((snapshot.toss || {}).status || "계정별 key/secret 기준"), tossReady ? "ok" : "warn"),
    renderAccountApiStatusRow("계좌 순번", accountSeqReady + "/" + accounts.length + " 계정", "계좌 조회에 필요한 account seq 저장 상태", accountSeqReady ? "ok" : "warn"),
    renderAccountApiStatusRow("KIS 시세·수급", configured.kisAppKey && configured.kisAppSecret ? "키 저장됨" : "키 필요", kisItems ? kisItems + "개 종목 보강" : "보유 종목 시세 보강 대기", configured.kisAppKey && configured.kisAppSecret ? "ok" : "neutral"),
    renderAccountApiStatusRow("스냅샷 모드", accountSnapshotModeLabel(snapshot), "actual/cache/mock 계정 데이터 구분", accountSnapshotTone(snapshot)),
    '</section>'
  ].join("");
}

function renderAccountQualityLedger(snapshot) {
  var counts = accountDataQualityCounts(snapshot);
  var total = counts.actual + counts.cached + counts.mock + counts.pending;
  return [
    '<section class="account-quality-ledger">',
    renderAccountControlMetric("실제", counts.actual, total ? "화면 데이터 중 " + total + "개" : "데이터 없음", "ok"),
    renderAccountControlMetric("캐시", counts.cached, "레이트리밋/실패 시 사용", counts.cached ? "warn" : "neutral"),
    renderAccountControlMetric("mock", counts.mock, "실제와 구분 표시", counts.mock ? "warn" : "neutral"),
    renderAccountControlMetric("대기", counts.pending, "시세 또는 원장 미수집", counts.pending ? "warn" : "neutral"),
    '</section>'
  ].join("");
}

function renderAccountBalanceAudit(snapshot) {
  var portfolio = (snapshot || {}).portfolio || {};
  var basis = String(portfolio.valuationBasis || portfolio.valuation_basis || "legacy-unknown");
  return [
    '<section class="account-balance-audit">',
    '<div class="account-balance-grid">',
    renderAccountControlMetric("투자 평가액", formatMoney(portfolio.invested || 0), "보유 종목 원화환산", "neutral"),
    renderAccountControlMetric("현금/주문 가능", formatMoney(portfolio.cash || 0), portfolioCashBasisText(snapshot || {}, portfolio), "neutral"),
    renderAccountControlMetric(portfolioValuationMetricLabel(basis), formatMoney(portfolio.total || 0), portfolioValuationBasisLabel(basis), "ok"),
    renderAccountControlMetric("산식 차이", exposureDiffText(portfolio.total || 0, numeric(portfolio.invested) + numeric(portfolio.cash)), "total - (invested + cash)", Math.abs(numeric(portfolio.total) - numeric(portfolio.invested) - numeric(portfolio.cash)) < 1 ? "ok" : "warn"),
    renderAccountControlMetric("토스 비용 전", formatMoney(portfolio.brokerGrossTotal || portfolio.broker_gross_total || 0), "broker gross + cash", "neutral"),
    renderAccountControlMetric("분석 시가", formatMoney(portfolio.markToMarketTotal || portfolio.mark_to_market_total || 0), "최신 시세 × 수량 + 현금", "neutral"),
    '</div>',
    '<div class="account-balance-ledger">',
    '<div class="account-board-title">',
    '<strong>검증 레저</strong>',
    '<span>데이터 원천, 환율, 현금 기준, 원장 합계를 같은 기준으로 대조합니다.</span>',
    '</div>',
    '<div class="source-stack compact">',
    renderPortfolioBasisRows(snapshot || {}, portfolio),
    '</div>',
    '</div>',
    '</section>'
  ].join("");
}

function renderAccountSectionBar() {
  var visibleSections = modeSectionsForPage("accounts", accountSections);
  var activeId = activeSectionForPageMode("accounts", accountSections, accountsState.activeAccountSection);
  if (activePageMode("accounts") !== "settings") {
    return [
      '<div class="account-section-bar account-drilldown-bar" data-section-mode="results">',
      '<div class="account-section-tabs account-drilldown-rail" role="toolbar" aria-label="계정 상세 보기">',
      renderWorkDetailButton("account-connections-board", "", "연결", "text-button compact"),
      renderWorkDetailButton("account-balance-board", "", "자산 검증", "text-button compact"),
      renderWorkDetailButton("account-history-board", "", "데이터 이력", "text-button compact"),
      renderWorkDetailButton("account-identity-board", "", "계정 설정", "text-button compact"),
      renderInfoIconButton("accounts", "계정·연결 탭의 단일 화면 운영 방식"),
      '</div>',
      '<div class="account-section-actions">',
      '<button class="text-button" data-action="refresh">데이터 새로고침</button>',
      '<button class="text-button" data-page-mode-page="accounts" data-page-mode="settings">계정 관리</button>',
      '</div>',
      '</div>'
    ].join("");
  }
  return [
    '<div class="account-section-bar" data-section-mode="' + escapeHtml(activePageMode("accounts")) + '">',
    '<div class="account-section-tabs" role="tablist" aria-label="계정 섹션">',
    visibleSections.map(function (item) {
      var active = activeId === item.id;
      return [
        '<button type="button" role="tab" class="' + (active ? "active" : "") + '" data-account-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
        '<strong>' + escapeHtml(item.label) + '</strong>',
        '<span>' + escapeHtml(item.description) + '</span>',
        '</button>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="account-section-actions">',
    '<button class="text-button" data-action="refresh">데이터 새로고침</button>',
    activePageMode("accounts") === "settings"
      ? '<button class="text-button primary" data-action="new-service-account">새 계정</button>'
      : '<button class="text-button" data-account-section="identity">계정 관리</button>',
    '</div>',
    '</div>'
  ].join("");
}

function renderAccountSectionContent(snapshot) {
  var section = activeSectionForPageMode("accounts", accountSections, normalizeAccountSection(accountsState.activeAccountSection));
  if (activePageMode("accounts") !== "settings") return renderAccountUnifiedConsole(snapshot);
  if (section === "connections") return renderAccountConnectionsPanel(snapshot);
  if (section === "balance") return renderAccountBalancePanel(snapshot);
  if (section === "history") return renderAccountDataHistoryPanel(snapshot);
  if (section === "identity") return renderAdminAccountPanel();
  return renderAccountCommandCenter(snapshot);
}

function renderAccountUnifiedConsole(snapshot) {
  return [
    '<div class="single-tab-console account-unified-console">',
    renderAccountCommandCenter(snapshot),
    renderAccountConnectionsPanel(snapshot),
    renderAccountBalancePanel(snapshot),
    renderPortfolioLifecyclePanel(snapshot),
    '</div>'
  ].join("");
}

function renderAccountConnectionsPanel(snapshot) {
  snapshot = snapshot || {};
  var accounts = accountsState.serviceAccounts || [];
  return [
    '<article class="panel account-connections-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Account Connections</p>',
    '<h2>증권사 연결과 데이터 출처</h2>',
    '<p class="subtle">계정별 증권사 인증 상태, 조회 가능성, 실제·캐시·mock 비중을 분리해서 봅니다.</p>',
    '</div>',
    '<span class="status-pill ' + escapeHtml(accountSnapshotTone(snapshot)) + '">' + escapeHtml(accountSnapshotModeLabel(snapshot)) + '</span>',
    '</div>',
    '<div class="account-command-layout">',
    '<div>',
    '<div class="account-board-title"><strong>API 출처 상태</strong><span>키 원문 없이 연결 가능성과 사용 출처만 표시합니다.</span></div>',
    renderAccountApiLedger(accounts, snapshot),
    '</div>',
    '<div>',
    '<div class="account-board-title"><strong>데이터 품질</strong><span>레이트리밋이나 실패 시 캐시가 섞였는지 확인합니다.</span></div>',
    renderAccountQualityLedger(snapshot),
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

function renderAccountDataHistoryPanel(snapshot) {
  snapshot = snapshot || {};
  var freshness = accountFreshness(snapshot);
  var portfolio = snapshot.portfolio || {};
  var toss = snapshot.toss || {};
  return [
    '<article class="panel account-history-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Account Data History</p>',
    '<h2>계정 데이터 이력</h2>',
    '<p class="subtle">계좌 스냅샷, 보유/현금 기준, 캐시 사용 여부를 시간순으로 확인합니다.</p>',
    '</div>',
    '<span class="status-pill ' + escapeHtml(freshness.tone || "demo") + '">' + escapeHtml(freshness.label) + '</span>',
    '</div>',
    '<div class="account-command-grid">',
    renderAccountControlMetric("스냅샷 생성", formatClock(snapshot.generatedAt), (toss.status || "조회 상태 대기"), accountSnapshotTone(snapshot)),
    renderAccountControlMetric("계좌 기준", currentAccountLabel(snapshot), accountSnapshotModeLabel(snapshot), accountSnapshotTone(snapshot)),
    renderAccountControlMetric("보유 평가", formatMoney(portfolio.invested || 0), "positions 원화환산", "neutral"),
    renderAccountControlMetric("현금 기준", formatMoney(portfolio.cash || 0), portfolioCashBasisText(snapshot, portfolio), "neutral"),
    '</div>',
    '<div class="account-overview-ledger">',
    '<div class="account-board-title"><strong>데이터 품질 이력</strong><span>실제 데이터와 캐시/mock 데이터가 섞였는지 확인합니다.</span></div>',
    renderAccountQualityLedger(snapshot),
    '</div>',
    '</article>'
  ].join("");
}

function renderAccountBalancePanel(snapshot) {
  snapshot = snapshot || {};
  var portfolio = (snapshot || {}).portfolio || {};
  var basis = String(portfolio.valuationBasis || portfolio.valuation_basis || "legacy-unknown");
  var formulaTotal = numeric(portfolio.invested) + numeric(portfolio.cash);
  return [
    '<article class="panel account-balance-panel account-balance-workspace">',
    '<div class="panel-head account-balance-hero">',
    '<div>',
    '<p class="label">Balance Audit</p>',
    '<h2>자산 검증</h2>',
    '<p class="subtle">화면의 평가액이 어떤 현금 기준, 환율, 보유 원장 합계에서 나왔는지 한 화면에서 대조합니다.</p>',
    '</div>',
    '<div class="account-balance-total">',
    '<em>' + escapeHtml(portfolioValuationMetricLabel(basis)) + '</em>',
    '<strong>' + escapeHtml(formatMoney(portfolio.total || 0)) + '</strong>',
    '<span>' + escapeHtml(exposureDiffText(portfolio.total || 0, formulaTotal)) + '</span>',
    '</div>',
    '</div>',
    renderAccountBalanceAudit(snapshot),
    '</article>'
  ].join("");
}

export { accountFreshness, accountSnapshotModeLabel, accountSnapshotTone, currentAccountLabel, renderAccountBalancePanel, renderAccountConnectionsPanel, renderAccountControlMetric, renderAccountDataHistoryPanel, renderAccountQualityLedger, timestampAgeMinutes };
