import { saveAccountWatchlistSymbols, saveWatchlistSymbols } from "../accounts/commands.mjs";
import { accountIdOf, activeWatchAccount, preferredWatchlistSymbols, watchlistAccountLabel } from "../accounts/watchlist.mjs";
import { stockDisplayMeta, stockDisplayName } from "./catalog.mjs";
import { DEFAULT_SYMBOL_UNIVERSE_LIMIT } from "./constants.mjs";
import { failedSymbolUniverseRefreshMarkets, friendlySymbolUniverseRefreshError, symbolUniverseRefreshActive, symbolUniverseRefreshStageMeta } from "./universe.mjs";
import { mobileInfiniteScrollEnabled, renderMobileInfiniteScrollFooter } from "../navigation/infinite-list.mjs";
import { alertRules, enabledAlertRule } from "../notifications/alerts.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { formatClock, formatCurrency, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { accountsState } from "../state/accounts.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";
import { universeState } from "../state/universe.mjs";

function marketLabel(market) {
  var key = String(market || "").toUpperCase();
  if (key === "KOSPI") return "코스피";
  if (key === "KOSDAQ") return "코스닥";
  if (key === "NASDAQ") return "나스닥";
  if (key === "US") return "미국";
  if (key === "KR") return "한국";
  return key || "-";
}

function freshnessLabel(item) {
  if (!item || !item.lastSeenAt) return "초기 데이터";
  return (item.stale ? "갱신 필요" : "신선") + " · " + formatClock(item.lastSeenAt);
}

function symbolUniverseRefreshButtonLabel() {
  var refresh = universeState.symbolUniverseRefresh || {};
  if (symbolUniverseRefreshActive(refresh)) {
    var completed = Number(refresh.completedCount || 0);
    var total = Number(refresh.totalCount || 0);
    return total ? "원천 갱신 " + completed + "/" + total : "갱신 요청 중";
  }
  return "원천 데이터 갱신";
}

function symbolUniverseRefreshElapsedText(payload) {
  var started = Date.parse(String((payload || {}).startedAt || (payload || {}).requestedAt || ""));
  var finished = Date.parse(String((payload || {}).finishedAt || ""));
  if (!Number.isFinite(started)) return "";
  var seconds = Math.max(0, Math.round(((Number.isFinite(finished) ? finished : Date.now()) - started) / 1000));
  if (seconds < 60) return seconds + "초 경과";
  return Math.floor(seconds / 60) + "분 " + (seconds % 60) + "초 경과";
}

function renderSymbolUniverseRefreshStageRail(refresh) {
  var stage = String((refresh || {}).stage || "queued");
  var order = ["queued", "fetching", "saving", "verifying"];
  var activeIndex = stage === "connecting" ? 1 : order.indexOf(stage);
  if (stage === "submitting") activeIndex = 0;
  if (stage === "summarizing" || stage === "market_completed") activeIndex = 3;
  if (["completed", "partial"].indexOf(stage) >= 0) activeIndex = 4;
  if (stage === "failed") activeIndex = Number((refresh || {}).completedCount || 0) ? 3 : 1;
  var labels = ["요청 접수", "원천 수집", "운영 DB 저장", "결과 확인"];
  return '<ol class="symbol-refresh-stage-rail" aria-label="갱신 처리 단계">' + labels.map(function (label, index) {
    var className = index < activeIndex ? "done" : (index === activeIndex ? "active" : "");
    return '<li class="' + className + '"><span aria-hidden="true">' + (index < activeIndex ? "✓" : index + 1) + '</span><em>' + escapeHtml(label) + '</em></li>';
  }).join("") + '</ol>';
}

function renderSymbolUniverseRefreshHistory() {
  var history = universeState.symbolUniverseRefreshHistory || [];
  if (!history.length) return "";
  return [
    '<details class="symbol-refresh-history">',
    '<summary><span>최근 원천 갱신</span><em>' + escapeHtml(history.length + "건") + '</em></summary>',
    '<div class="symbol-refresh-history-list">',
    history.map(function (item) {
      var status = String(item.status || "idle");
      var resultCount = (item.results || []).reduce(function (sum, result) {
        return sum + (String(result.status || "") === "ok" ? Number(result.count || 0) : 0);
      }, 0);
      var label = status === "completed" ? "완료" : (status === "partial" ? "일부 완료" : (status === "failed" ? "실패" : "진행 중"));
      return [
        '<div class="symbol-refresh-history-row ' + escapeHtml(status) + '">',
        '<span><strong>' + escapeHtml((item.markets || []).map(marketLabel).join(" · ") || "전체 시장") + '</strong><em>' + escapeHtml((item.finishedAt || item.requestedAt) ? formatClock(item.finishedAt || item.requestedAt) : "시간 확인 중") + '</em></span>',
        '<span><strong>' + escapeHtml(label) + '</strong><em>' + escapeHtml(resultCount ? resultCount.toLocaleString("ko-KR") + "개" : Number(item.completedCount || 0) + "/" + Number(item.totalCount || 0) + " 시장") + '</em></span>',
        '</div>'
      ].join("");
    }).join(""),
    '</div>',
    '</details>'
  ].join("");
}

function renderSymbolUniverseRefreshStatus() {
  var refresh = universeState.symbolUniverseRefresh || {};
  var status = String(refresh.status || "idle");
  var active = symbolUniverseRefreshActive(refresh);
  if (!active && (!refresh.jobId || ["completed", "partial", "failed", "unknown"].indexOf(status) < 0)) return "";
  if (!active && universeState.symbolUniverseRefreshDismissedJobId === refresh.jobId) return "";
  var completed = Number(refresh.completedCount || 0);
  var total = Number(refresh.totalCount || (refresh.markets || []).length || 0);
  var percent = Math.max(0, Math.min(100, Number(refresh.progressPercent || 0)));
  var stageMeta = symbolUniverseRefreshStageMeta(refresh);
  var expanded = active || Boolean(universeState.symbolUniverseRefreshExpanded);
  var title = active
    ? stageMeta.label
    : (status === "completed" ? "전체 종목 목록 갱신 완료" : status === "partial" ? "일부 시장 갱신 완료" : "종목 목록 갱신 실패");
  var detail = active
    ? "서버에서 계속 처리 중입니다. 화면을 이동하거나 다시 열어도 작업 상태를 이어서 확인합니다."
    : (status === "completed" ? "저장된 최신 목록을 불러왔습니다." : "갱신하지 못한 시장은 마지막 성공 목록을 계속 사용합니다.");
  var resultText = (refresh.results || []).map(function (item) {
    var label = marketLabel(item.market);
    if (item.status === "ok") return label + " " + Number(item.count || 0) + "개";
    return label + " 실패" + (item.error ? " · " + item.error : "");
  });
  var failedMarkets = failedSymbolUniverseRefreshMarkets(refresh);
  var connectionLabel = shellState.realtime.connected ? "실시간 상태 연결" : "자동 상태 확인";
  var elapsed = symbolUniverseRefreshElapsedText(refresh);
  return [
    '<section class="symbol-refresh-status ' + escapeHtml(active ? "active" : status) + (expanded ? " expanded" : " compact") + '" data-symbol-refresh-anchor tabindex="-1" aria-live="polite" aria-busy="' + (active ? "true" : "false") + '">',
    '<div class="symbol-refresh-status-head"><span><strong>' + escapeHtml(title) + '</strong><em>' + escapeHtml([connectionLabel, elapsed].filter(Boolean).join(" · ")) + '</em></span><span>' + escapeHtml(total ? completed + "/" + total + " 시장 · " + percent + "%" : "준비 중") + '</span><button class="icon-button compact" type="button" data-action="toggle-symbol-refresh-status" title="' + escapeHtml(expanded ? "진행 상태 접기" : "진행 상태 펼치기") + '" aria-label="' + escapeHtml(expanded ? "진행 상태 접기" : "진행 상태 펼치기") + '"><span aria-hidden="true">' + (expanded ? "−" : "+") + '</span></button></div>',
    '<div class="symbol-refresh-track' + (active ? " is-active" : "") + '" role="progressbar" aria-label="전체 종목 목록 갱신 진행률" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + escapeHtml(percent) + '" aria-valuetext="' + escapeHtml(stageMeta.label + " " + percent + "%") + '"><span style="width:' + escapeHtml(percent) + '%"></span></div>',
    expanded ? '<div class="symbol-refresh-status-body">' : '',
    expanded ? renderSymbolUniverseRefreshStageRail(refresh) : '',
    expanded ? '<p>' + escapeHtml(detail) + '</p>' : '',
    expanded && resultText.length ? '<div class="symbol-refresh-results">' + resultText.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
    expanded && refresh.pollError && active ? '<em>' + escapeHtml(refresh.pollError) + ' 자동으로 다시 확인합니다.</em>' : '',
    expanded && refresh.lastError && !active ? '<p class="symbol-refresh-friendly-error">' + escapeHtml(friendlySymbolUniverseRefreshError(refresh.lastError)) + '</p><details class="symbol-refresh-error-detail"><summary>오류 상세</summary><code>' + escapeHtml(refresh.lastError) + '</code></details>' : '',
    expanded && refresh.finishedAt && !active ? '<small>완료 ' + escapeHtml(formatClock(refresh.finishedAt)) + '</small>' : '',
    expanded ? '<div class="symbol-refresh-actions">' : '',
    expanded && active ? '<button class="text-button compact" type="button" data-action="check-symbol-refresh-status"' + (universeState.symbolUniverseRefreshStatusLoading ? ' disabled aria-busy="true"' : '') + '>' + escapeHtml(universeState.symbolUniverseRefreshStatusLoading ? "확인 중" : "상태 다시 확인") + '</button>' : '',
    expanded && !active && failedMarkets.length ? '<button class="text-button primary compact" type="button" data-action="retry-symbol-refresh" data-refresh-markets="' + escapeHtml(failedMarkets.join(",")) + '">실패 시장 다시 시도</button>' : '',
    expanded && !active ? '<button class="text-button compact" type="button" data-action="show-symbol-refresh-results">저장 목록 보기</button><button class="text-button compact" type="button" data-action="dismiss-symbol-refresh-status">완료 상태 닫기</button>' : '',
    expanded ? '</div></div>' : '',
    '</section>'
  ].join("");
}

function renderSymbolUniversePanel(options) {
  var full = Boolean(options && options.full);
  var universe = universeState.symbolUniverse || {};
  var summary = universe.summary || {};
  var items = universe.items || [];
  var markets = summary.markets || [];
  var sources = summary.sources || [];
  var marketData = summary.marketData || {};
  var limit = Number(universe.limit || universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT);
  var offset = Number(universe.offset || universeState.symbolUniverseOffset || 0);
  var cumulativeMobile = mobileInfiniteScrollEnabled() && full;
  var resultTotal = Number(universe.resultTotal || 0);
  if (!resultTotal) resultTotal = universeState.symbolUniverseQuery || universeState.symbolUniverseMarket ? items.length : Number(summary.total || items.length || 0);
  var visibleFrom = resultTotal && items.length ? (cumulativeMobile ? 1 : offset + 1) : 0;
  var visibleTo = resultTotal ? Math.min((cumulativeMobile ? 0 : offset) + items.length, resultTotal) : items.length;
  var hasPrev = offset > 0;
  var hasNext = Boolean(universe.hasMore || (!cumulativeMobile && resultTotal && offset + items.length < resultTotal));
  var renderedItems = full ? items : items.slice(0, 12);
  var emptyCatalog = !renderedItems.length;
  return [
    '<article class="panel symbol-universe-panel' + (full ? " symbol-universe-full" : "") + '"' + cardTypeAttrs("source-card", items.length ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Symbol Universe</p>',
    '<h2>' + escapeHtml(full ? "전체 종목 정보" : "전체 종목 카탈로그") + '</h2>',
    '<p class="subtle">시장 카탈로그에서 추적 종목 후보를 찾고 계정별 관심 목록으로 넘깁니다.</p>',
    '</div>',
    '<span class="metric">' + escapeHtml(summary.total || items.length || 0) + '</span>',
    '</div>',
    '<div class="symbol-summary-grid">',
    (markets.length ? markets.map(renderSymbolMarketSummary).join("") : '<p class="subtle">아직 저장된 전체 종목 목록이 없습니다.</p>') + renderSymbolMarketDataSummary(marketData),
    '</div>',
    full && sources.length ? '<div class="symbol-source-grid">' + sources.map(renderSymbolSourceSummary).join("") + '</div>' : '',
    '<form class="symbol-filter-form ' + (full ? "full" : "compact") + '" data-symbol-search-form>',
    '<label>',
    '<span>시장</span>',
    '<select name="market" data-symbol-market>',
    '<option value="">전체 시장</option>',
    ["KOSPI", "KOSDAQ", "NASDAQ"].map(function (market) {
      return '<option value="' + escapeHtml(market) + '"' + (universeState.symbolUniverseMarket === market ? " selected" : "") + '>' + escapeHtml(marketLabel(market)) + '</option>';
    }).join(""),
    '</select>',
    '</label>',
    '<label>',
    '<span>검색어</span>',
    '<input name="query" data-symbol-query placeholder="회사명 검색" value="' + escapeHtml(universeState.symbolUniverseQuery || "") + '" autocomplete="off" />',
    '</label>',
    full ? '<label><span>표시 수</span><select name="limit" data-symbol-limit>' + [8, 16, 40, 80].map(function (value) {
      return '<option value="' + value + '"' + (Number(universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT) === value ? " selected" : "") + '>' + value + '개</option>';
    }).join("") + '</select></label>' : '',
    full ? '<label><span>추가 대상</span><select name="watchAccount" data-symbol-add-account>' + renderWatchAccountSelectOptions() + '</select></label>' : '',
    '<button class="text-button primary' + (universeState.symbolUniverseLoading ? " is-loading" : "") + '" data-symbol-search-submit' + (universeState.symbolUniverseLoading ? ' disabled aria-busy="true"' : '') + '>' + escapeHtml(universeState.symbolUniverseLoading ? "저장 목록 조회 중" : "저장 목록 검색") + '</button>',
    '<button class="text-button" type="button" data-action="refresh-symbol-universe"' + (universeState.symbolUniverseLoading || universeState.symbolUniverseRefreshing ? ' disabled aria-busy="true"' : '') + '>' + escapeHtml(symbolUniverseRefreshButtonLabel()) + '</button>',
    '</form>',
    renderSymbolUniverseRefreshStatus(),
    renderSymbolUniverseRefreshHistory(),
    universeState.symbolUniverseError ? '<p class="form-error">' + escapeHtml(universeState.symbolUniverseError) + '</p>' : '',
    (universeState.symbolUniverseLoading && renderedItems.length) ? '<p class="data-refresh-status">최근 성공 목록을 먼저 보여주는 중입니다. 최신 카탈로그는 백그라운드에서 갱신합니다.</p>' : '',
    '<p class="symbol-universe-note subtle">코스피·코스닥은 KRX KIND, 나스닥은 Nasdaq Trader 심볼 디렉터리를 운영 DB에 저장합니다. 원천 호출이 실패해도 마지막 성공 목록을 계속 사용합니다.</p>',
    emptyCatalog ? renderSymbolUniverseStarterConsole(summary, sources, marketData) : '',
    full && !cumulativeMobile ? '<div class="symbol-pager"><span>' + escapeHtml(resultTotal ? visibleFrom + "-" + visibleTo + " / " + resultTotal + "개 표시" : "표시할 종목 없음") + '</span><div><button class="mini-button" data-symbol-page="prev"' + (hasPrev ? "" : " disabled") + '>이전</button><button class="mini-button" data-symbol-page="next"' + (hasNext ? "" : " disabled") + '>다음</button></div></div>' : '',
    full ? renderSymbolBulkActionBar(renderedItems) : '',
    full ? '<div class="symbol-result-workbench">' : '',
    '<div class="symbol-result-list" data-symbol-result-list>',
    (universeState.symbolUniverseLoading && !renderedItems.length) ? renderEmptyState({
      tone: "watch",
      label: "Catalog",
      title: "종목 카탈로그를 갱신하고 있습니다",
      description: "마지막 성공 목록은 유지하고, 검색 조건에 맞는 결과만 백그라운드로 다시 읽습니다.",
      meta: [marketLabel(universeState.symbolUniverseMarket || "전체"), String(limit) + "개 단위"]
    }) : (renderedItems.length ? renderedItems.map(renderSymbolUniverseRow).join("") : renderEmptyState({
      tone: "muted",
      label: "Catalog",
      title: "검색 조건에 맞는 종목이 없습니다",
      description: "시장 필터와 검색어를 줄이거나 목록 갱신을 실행해 최신 카탈로그를 다시 불러오세요.",
      meta: [marketLabel(universeState.symbolUniverseMarket || "전체"), universeState.symbolUniverseQuery || "검색어 없음"],
      action: '<button class="text-button primary" type="button" data-action="refresh-symbol-universe"' + (universeState.symbolUniverseLoading || universeState.symbolUniverseRefreshing ? ' disabled aria-busy="true"' : '') + '>' + escapeHtml(symbolUniverseRefreshButtonLabel()) + '</button>'
    })),
    '</div>',
    full ? renderSymbolUniverseDetailPanel(renderedItems) : '',
    full ? '</div>' : '',
    cumulativeMobile ? renderMobileInfiniteScrollFooter({
      loaded: items.length,
      total: resultTotal,
      loading: universeState.symbolUniverseLoading,
      hasNext: hasNext,
      nextAttributes: 'data-symbol-page="next"'
    }) : '',
    '</article>'
  ].join("");
}

function renderSymbolUniverseStarterConsole(summary, sources, marketData) {
  var sourceCount = Array.isArray(sources) ? sources.length : 0;
  var marketCount = Array.isArray((summary || {}).markets) ? summary.markets.length : 0;
  var quoteCount = Number((marketData || {}).count || 0);
  return [
    '<div class="symbol-empty-console">',
    '<section' + cardTypeAttrs("source-card", sourceCount ? "watch" : "hold") + '>',
    '<span>01 원천</span>',
    '<strong>' + escapeHtml(sourceCount ? sourceCount + "개 소스 확인" : "KRX/NASDAQ 연결 대기") + '</strong>',
    '<p>시장별 카탈로그 원천 상태를 확인하고 마지막 성공 목록을 유지합니다.</p>',
    '</section>',
    '<section' + cardTypeAttrs("diagnostic-card", marketCount ? "watch" : "hold") + '>',
    '<span>02 카탈로그</span>',
    '<strong>' + escapeHtml(marketCount ? marketCount + "개 시장" : "저장 종목 없음") + '</strong>',
    '<p>필터가 너무 좁거나 초기 수집 전이면 결과가 비어 보일 수 있습니다.</p>',
    '</section>',
    '<section' + cardTypeAttrs("action-queue-card", "hold") + '>',
    '<span>03 다음 행동</span>',
    '<strong>' + escapeHtml(quoteCount ? "관심 목록 편입" : "목록 갱신") + '</strong>',
    '<p>카탈로그를 갱신한 뒤 계정별 관심 종목 후보로 넘깁니다.</p>',
    '<button class="text-button primary" type="button" data-action="refresh-symbol-universe"' + (universeState.symbolUniverseLoading || universeState.symbolUniverseRefreshing ? ' disabled aria-busy="true"' : '') + '>' + escapeHtml(symbolUniverseRefreshButtonLabel()) + '</button>',
    '</section>',
    '</div>'
  ].join("");
}

function renderSymbolMarketSummary(market) {
  return [
    '<div class="symbol-summary-metric"' + cardTypeAttrs("metric-cell") + '>',
    '<span>' + escapeHtml(marketLabel(market.market)) + '</span>',
    '<strong>' + escapeHtml(market.count || 0) + '</strong>',
    '<em>' + escapeHtml(freshnessLabel(market)) + '</em>',
    '</div>'
  ].join("");
}

function renderSymbolMarketDataSummary(summary) {
  if (!summary || !summary.count) return "";
  return [
    '<div class="symbol-summary-metric"' + cardTypeAttrs("metric-cell") + '>',
    '<span>수집 시세</span>',
    '<strong>' + escapeHtml(summary.count || 0) + '</strong>',
    '<em>' + escapeHtml(summary.latestUpdatedAt ? formatClock(summary.latestUpdatedAt) : "수집 대기") + '</em>',
    '</div>'
  ].join("");
}

function renderSymbolSourceSummary(source) {
  var ok = String(source.status || "").toLowerCase() === "ok";
  return [
    '<div class="symbol-source-status ' + (ok ? "ok" : "warn") + '"' + cardTypeAttrs("health-card", ok ? "watch" : "hold") + '>',
    '<span>' + escapeHtml(marketLabel(source.market)) + ' API</span>',
    '<strong>' + escapeHtml(source.status || "-") + '</strong>',
    '<em>' + escapeHtml(source.lastSuccessAt ? formatClock(source.lastSuccessAt) : "성공 기록 없음") + '</em>',
    '</div>'
  ].join("");
}

function renderWatchAccountSelectOptions() {
  var accounts = accountsState.serviceAccounts || [];
  return accounts.length ? accounts.map(function (account) {
    var id = accountIdOf(account);
    return '<option value="' + escapeHtml(id) + '"' + (id === accountsState.activeWatchAccountId ? " selected" : "") + '>' + escapeHtml(account.label || id) + '</option>';
  }).join("") : '<option value="">기본 관심목록</option>';
}

function visibleSymbolUniverseSymbols(items) {
  var seen = {};
  var symbols = [];
  (items || []).forEach(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    if (!symbol || seen[symbol]) return;
    seen[symbol] = true;
    symbols.push(symbol);
  });
  return symbols;
}

function renderSymbolBulkActionBar(items) {
  var symbols = visibleSymbolUniverseSymbols(items);
  var registered = preferredWatchlistSymbols();
  var missing = symbols.filter(function (symbol) {
    return registered.indexOf(symbol) < 0;
  });
  var account = activeWatchAccount();
  return [
    '<div class="symbol-bulk-bar">',
    '<div>',
    '<strong>' + escapeHtml(account ? watchlistAccountLabel(account) : "기본 관심목록") + '</strong>',
    '<span>' + escapeHtml("현재 페이지 " + symbols.length + "개 중 " + missing.length + "개 추가 가능") + '</span>',
    '</div>',
    '<button class="text-button primary" type="button" data-action="add-visible-symbols"' + (missing.length ? "" : " disabled") + '>페이지 종목 일괄 추가</button>',
    '</div>'
  ].join("");
}

function symbolUniverseKey(item) {
  item = item || {};
  return String([item.symbol || "", item.market || item.exchange || "", item.source || ""].join(":")).toUpperCase();
}

function symbolUniverseItemByKey(key, items) {
  key = String(key || "");
  return (items || universeState.symbolUniverse.items || []).filter(function (item) {
    return symbolUniverseKey(item) === key;
  })[0] || null;
}

function renderSymbolUniverseDetailPanel(items) {
  var item = symbolUniverseItemByKey(universeState.activeSymbolUniverseKey, items);
  if (!item) {
    return [
      '<aside class="symbol-detail-panel" aria-label="선택 종목 상세">',
      renderEmptyState({
        tone: "muted",
        label: "Detail",
        title: "종목을 선택하세요",
        description: "기본 목록은 최소 정보만 보여주고, 출처·시세·관심 편입 상태는 선택한 종목 상세에서 확인합니다.",
        meta: ["목록 클릭", "관심 추가"]
      }),
      '</aside>'
    ].join("");
  }
  var symbol = String(item.symbol || "").toUpperCase();
  var account = activeWatchAccount();
  var already = preferredWatchlistSymbols().indexOf(symbol) >= 0;
  var targetText = account ? watchlistAccountLabel(account) : "기본 관심목록";
  var hasPrice = Boolean(item.currentPrice);
  var priceText = hasPrice ? formatCurrency(item.currentPrice, item.currency) : "시세 수집 대기";
  var quality = String(item.dataQuality || "").toLowerCase();
  var qualityLabel = quality === "actual" ? "실제 데이터" : (quality === "cached" ? "저장 데이터" : (quality || "대기"));
  return [
    '<aside class="symbol-detail-panel" aria-label="선택 종목 상세">',
    '<div class="symbol-detail-head">',
    '<div>',
    '<p class="label">Symbol Detail</p>',
    '<h3>' + escapeHtml(stockDisplayName(symbol, item)) + '</h3>',
    '<span>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || item.exchange), item.sector || item.assetType || "STOCK"])) + '</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(already ? "watch" : "hold") + '">' + escapeHtml(already ? "관심 등록" : "후보") + '</span>',
    '</div>',
    '<div class="inline-detail-metrics">',
    renderNotificationDetailMetric("시세", priceText, hasPrice ? "watch" : "muted"),
    renderNotificationDetailMetric("데이터", qualityLabel, hasPrice ? "watch" : "hold"),
    renderNotificationDetailMetric("시장", marketLabel(item.market || item.exchange), "muted"),
    renderNotificationDetailMetric("통화", item.currency || "-", "muted"),
    '</div>',
    '<section class="inline-detail-block primary">',
    '<strong>관심 편입</strong>',
    '<p>' + escapeHtml(targetText + "에 편입해 알림과 투자 판단 입력으로 연결합니다.") + '</p>',
    '<button class="text-button ' + escapeHtml(already ? "danger" : "primary") + '" type="button" data-symbol-watch-toggle="' + escapeHtml(symbol) + '"' + ((isStaticPreviewHost() || settingsState.serverSettingsLocked) ? " disabled" : "") + '>' + escapeHtml(already ? "선택 계정에서 삭제" : "관심목록 추가") + '</button>',
    '</section>',
    '<section class="inline-detail-block">',
    '<strong>출처와 신선도</strong>',
    '<div class="inline-detail-tags">',
    '<span>출처 ' + escapeHtml(item.source || "-") + '</span>',
    '<span>최근 ' + escapeHtml(item.lastSeenAt ? formatClock(item.lastSeenAt) : "초기 데이터") + '</span>',
    '<span>시세 ' + escapeHtml(item.marketDataUpdatedAt ? formatClock(item.marketDataUpdatedAt) : "대기") + '</span>',
    '<span>상태 ' + escapeHtml(item.stale ? "갱신 필요" : "신선") + '</span>',
    '</div>',
    '</section>',
    '</aside>'
  ].join("");
}

function addVisibleSymbolsToPreferredWatchlist() {
  var symbols = visibleSymbolUniverseSymbols(universeState.symbolUniverse.items || []);
  var registered = preferredWatchlistSymbols();
  var missing = symbols.filter(function (symbol) {
    return registered.indexOf(symbol) < 0;
  });
  if (!missing.length) {
    showSnackbar("현재 페이지 종목은 이미 관심목록에 있습니다.");
    return Promise.resolve();
  }
  var account = activeWatchAccount();
  return account
    ? saveAccountWatchlistSymbols(accountIdOf(account), registered.concat(missing))
    : saveWatchlistSymbols(registered.concat(missing));
}

function renderSymbolUniverseRow(item) {
  var symbol = String(item.symbol || "").toUpperCase();
  var key = symbolUniverseKey(item);
  var account = activeWatchAccount();
  var already = preferredWatchlistSymbols().indexOf(symbol) >= 0;
  var targetText = account ? watchlistAccountLabel(account) : "기본 관심목록";
  var hasPrice = Boolean(item.currentPrice);
  var priceText = hasPrice ? formatCurrency(item.currentPrice, item.currency) : "시세 수집 대기";
  var quality = String(item.dataQuality || "").toLowerCase();
  var qualityLabel = quality === "actual" ? "실제 데이터" : (quality === "cached" ? "저장 데이터" : "");
  var dataLine = hasPrice
    ? [qualityLabel, item.quoteSource || "", item.marketDataUpdatedAt ? formatClock(item.marketDataUpdatedAt) : ""].filter(Boolean).join(" · ")
    : (item.quoteStatus || "추천용 시세 수집 순서를 기다리는 중");
  var active = universeState.activeSymbolUniverseKey === key;
  var changed = Boolean((universeState.symbolUniverseChangedKeys || {})[key]);
  return [
    '<div class="symbol-result-row has-watch-action ' + escapeHtml((active ? "active " : "") + (changed ? "is-refreshed" : "")) + '"' + cardTypeAttrs("ledger-row", already ? "watch" : "hold") + cardFormatAttrs("market-ledger-row", "compact") + '>',
    '<button class="symbol-result-select" type="button" data-symbol-select="' + escapeHtml(key) + '" aria-label="' + escapeHtml(stockDisplayName(symbol, item) + " 상세 보기") + '">',
    '<div class="symbol-result-main">',
    '<div class="symbol-result-title">',
    '<strong>' + escapeHtml(stockDisplayName(symbol, item)) + '</strong>',
    changed ? '<em class="symbol-refresh-change">이번 갱신 변경</em>' : '',
    '<span>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || item.exchange), item.sector || item.assetType || "STOCK"])) + '</span>',
    renderRecordChangedAt(item),
    '</div>',
    '<div class="symbol-result-meta">',
    '<span>' + escapeHtml(marketLabel(item.market || item.exchange)) + '</span>',
    '<span>' + escapeHtml(item.assetType || "STOCK") + '</span>',
    '<span>' + escapeHtml(item.currency || "-") + '</span>',
    '<span>' + escapeHtml(item.stale ? "갱신 필요" : "신선") + '</span>',
    '</div>',
    '<p>' + escapeHtml([item.source || "-", dataLine].filter(Boolean).join(" · ")) + '</p>',
    '</div>',
    '<div class="symbol-result-side">',
    '<strong>' + escapeHtml(priceText) + '</strong>',
    '<span>' + escapeHtml((item.sector || "섹터 미분류") + " · " + targetText) + '</span>',
    '<em>' + escapeHtml(active ? "상세 표시 중" : "행 선택") + '</em>',
    '</div>',
    '</button>',
    '<button class="symbol-watch-toggle' + (already ? " active" : "") + '" type="button" data-symbol-watch-toggle="' + escapeHtml(symbol) + '" data-network-busy-icon-only="true" title="' + escapeHtml(already ? targetText + "에서 관심 종목 삭제" : "관심 종목 추가") + '" aria-label="' + escapeHtml(already ? stockDisplayName(symbol, item) + " 관심 종목 삭제" : stockDisplayName(symbol, item) + " 관심 종목 추가") + '"' + ((isStaticPreviewHost() || settingsState.serverSettingsLocked) ? " disabled" : "") + '><span aria-hidden="true">' + (already ? "★" : "☆") + '</span></button>',
    '</div>'
  ].join("");
}

function renderWatchAlertMeta(item) {
  var rules = alertRules();
  var hasPrice = Boolean(item.currentPrice);
  var quoteRule = enabledAlertRule(rules, "watchlistQuote");
  var pendingRule = enabledAlertRule(rules, "watchlistQuotePending");
  var quality = String(item.dataQuality || "").toLowerCase();
  var qualityLabel = quality === "actual" ? "실제 데이터" : (quality === "cached" ? "저장 데이터" : (quality === "mock" ? "mock 데이터" : ""));
  var chips = [
    '<span class="chip ' + (hasPrice ? "ok" : "missing") + '">' + escapeHtml(item.quoteStatus || (hasPrice ? "시세 수집" : "시세 대기")) + '</span>',
    '<span class="chip ' + (quoteRule ? "ok" : "missing") + '">시세 알림 ' + escapeHtml(quoteRule ? "ON" : "OFF") + '</span>'
  ];
  if (qualityLabel) {
    chips.push('<span class="chip ' + (quality === "actual" ? "ok" : "") + '">' + escapeHtml(qualityLabel) + '</span>');
  }
  if (item.quoteSource) {
    chips.push('<span class="chip">' + escapeHtml(item.quoteSource) + '</span>');
  }
  if (!hasPrice) {
    chips.push('<span class="chip ' + (pendingRule ? "ok" : "missing") + '">대기 알림 ' + escapeHtml(pendingRule ? "ON" : "OFF") + '</span>');
  }
  return [
    '<div class="watch-row-meta">',
    '<div class="chip-row">' + chips.join("") + '</div>',
    item.quoteMessage ? '<p>' + escapeHtml(item.quoteMessage) + '</p>' : '',
    '</div>'
  ].join("");
}

export { addVisibleSymbolsToPreferredWatchlist, marketLabel, renderSymbolUniversePanel, renderWatchAccountSelectOptions, renderWatchAlertMeta, symbolUniverseKey };
