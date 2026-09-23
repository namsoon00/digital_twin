import { accountIdOf, accountWatchlistSymbols, activeWatchAccount, watchlistAccountLabel } from "../accounts/watchlist.mjs";
import { renderInvestmentMoneyFlowPanel } from "../decisions/evidence.mjs";
import { stockDisplayName } from "../instruments/catalog.mjs";
import { renderWatchSuggestList } from "../instruments/suggestions.mjs";
import { renderSymbolUniversePanel, renderWatchAccountSelectOptions } from "../instruments/universe-view.mjs";
import { consoleResearchItems, filteredConsoleInstrumentRows } from "./selectors.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { normalizeMarketWorkspaceMode } from "../navigation/routes.mjs";
import { selectConsolePortfolio } from "../portfolio/selectors.mjs";
import { researchEvidenceImpactMeta, researchEvidenceKoreanSummary, researchEvidenceTranslationMeta } from "../research/quality.mjs";
import { formatFeedTime } from "../research/requests.mjs";
import { compareResearchEvidenceForDisplay, feedEvidenceKey } from "../research/workspace.mjs";
import { consolePageSlice, renderConsoleEmpty, renderConsoleListSkeleton, renderConsoleLiveRegion, renderConsoleManagedPage, renderConsolePager, renderConsoleSurface } from "../shared/console.mjs";
import { optionalPrice, optionalSignedPct, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { marketWorkspaceModes } from "../shell/catalog.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { accountsState } from "../state/accounts.mjs";
import { marketState } from "../state/market.mjs";
import { researchState } from "../state/research.mjs";
import { settingsState } from "../state/settings.mjs";

function renderMarketInstrumentRow(row) {
  var flowTone = !row.partialFlowAvailable ? "hold" : (row.foreignInstitutionNet > 0 ? "watch" : (row.foreignInstitutionNet < 0 ? "danger" : "hold"));
  var changeTone = !row.changeAvailable ? "hold" : (row.changeRate > 0 ? "watch" : (row.changeRate < 0 ? "danger" : "hold"));
  var decisionLabel = row.decision ? (row.decision.decision || row.decision.action || "판단 있음") : "판단 대기";
  return [
    '<button class="oa-data-row oa-market-row" type="button" data-console-row-key="' + escapeHtml(row.key) + '" data-work-detail="market-instrument" data-work-detail-key="' + escapeHtml(row.symbol) + '">',
    '<span class="oa-symbol-cell"><strong>' + escapeHtml(row.name || row.symbol) + '</strong><em>' + escapeHtml([row.symbol, row.source === "watchlist" ? "관심" : "보유"].join(" · ")) + '</em>' + renderRecordChangedAt(row) + '</span>',
    '<span><strong>' + escapeHtml(optionalPrice(row.currentPrice, row.currency, row.quoteAvailable)) + '</strong><em class="' + escapeHtml(changeTone) + '">' + escapeHtml(optionalSignedPct(row.changeRate, row.changeAvailable)) + '</em></span>',
    '<span><strong class="' + escapeHtml(flowTone) + '">' + escapeHtml(row.flowDisplay) + '</strong><em>' + escapeHtml(row.flowLabel) + '</em></span>',
    '<span><strong class="' + escapeHtml(row.impact.tone || "hold") + '">' + escapeHtml(row.impact.label || "근거 대기") + '</strong><em>' + escapeHtml(decisionLabel) + ' · 근거 ' + escapeHtml(row.evidenceCount) + '건</em></span>',
    '<span class="oa-open-cell">&rarr;</span>',
    '</button>'
  ].join("");
}

function renderMarketNewsRow(item, index) {
  var key = feedEvidenceKey(item, index);
  var impact = researchEvidenceImpactMeta(item);
  var symbol = String(item.symbol || "").toUpperCase();
  var translation = researchEvidenceTranslationMeta(item);
  return [
    '<button class="oa-news-row" type="button" data-console-row-key="' + escapeHtml(key) + '" data-work-detail="research-evidence" data-work-detail-key="' + escapeHtml(key) + '">',
    '<span class="tone-chip ' + escapeHtml(impact.tone || "hold") + '">' + escapeHtml(impact.label || "중립") + '</span>',
    '<span><strong>' + escapeHtml(translation.displayTitle || "제목 없음") + '</strong><em>' + escapeHtml(researchEvidenceKoreanSummary(item)) + '</em>' + renderRecordChangedAt(item) + '</span>',
    '<small>' + escapeHtml([stockDisplayName(symbol, item), item.source, formatFeedTime(item.publishedAt || item.observedAt)].filter(Boolean).join(" · ")) + '</small>',
    '</button>'
  ].join("");
}

function renderMarketWorkspaceNavigation() {
  return [
    '<nav class="market-workspace-tabs" aria-label="시장 화면 보기">',
    marketWorkspaceModes.map(function (mode) {
      var active = normalizeMarketWorkspaceMode(marketState.marketWorkspaceMode) === mode.id;
      return '<button type="button" data-market-workspace="' + escapeHtml(mode.id) + '" class="market-workspace-tab' + (active ? " active" : "") + '"' + (active ? ' aria-current="page"' : '') + '><strong>' + escapeHtml(mode.label) + '</strong><span>' + escapeHtml(mode.description) + '</span></button>';
    }).join(""),
    '</nav>'
  ].join("");
}

function renderMarketWatchlistCommand(snapshot) {
  var account = activeWatchAccount();
  var accountId = accountIdOf(account);
  var symbols = accountWatchlistSymbols(account);
  var locked = isStaticPreviewHost() || settingsState.serverSettingsLocked || !accountId;
  return [
    '<section class="market-watch-command" aria-label="관심 종목 빠른 관리">',
    '<div class="market-watch-command-copy"><span>WATCHLIST</span><strong>' + escapeHtml(account ? watchlistAccountLabel(account) : "계정을 선택하세요") + '</strong><em>' + escapeHtml(symbols.length + "개 관심 종목") + '</em></div>',
    '<label class="market-watch-account"><span>저장 계정</span><select data-market-watch-account' + (accountsState.serviceAccountsLoading ? " disabled" : "") + '>' + renderWatchAccountSelectOptions() + '</select></label>',
    '<form class="market-watch-add" data-watch-add-form data-watch-account-id="' + escapeHtml(accountId) + '">',
    '<label><span class="sr-only">관심 종목 검색</span><input name="symbol" data-watch-symbol-input role="combobox" aria-autocomplete="list" aria-controls="market-watch-symbol-suggestions" aria-expanded="' + (accountsState.watchSuggestQuery ? "true" : "false") + '" placeholder="회사명 또는 코드" autocomplete="off"' + (locked ? " disabled" : "") + ' /></label>',
    '<button class="text-button primary"' + (locked ? " disabled" : "") + '>추가</button>',
    '</form>',
    '<button class="text-button" type="button" data-market-workspace="universe">전체 종목에서 찾기</button>',
    '<div class="watch-suggest-box market-watch-suggest" id="market-watch-symbol-suggestions" role="listbox" data-watch-suggest-list data-watch-account-id="' + escapeHtml(accountId) + '">' + renderWatchSuggestList() + '</div>',
    '<p class="market-watch-api">저장 API /api/service-accounts/{accountId}/watchlist · 시세 Toss Open API · 종목 KRX KIND / Nasdaq Trader</p>',
    locked ? '<p class="market-watch-readonly">' + escapeHtml(isStaticPreviewHost() || settingsState.serverSettingsLocked ? "GitHub Pages는 읽기 전용입니다. 로컬 앱에서 추가할 수 있습니다." : "설정에서 계정을 먼저 등록하세요.") + '</p>' : '',
    accountsState.watchlistError ? '<p class="form-error">' + escapeHtml(accountsState.watchlistError) + '</p>' : '',
    '</section>'
  ].join("");
}

function renderMarketMineWorkspace(snapshot, rows) {
  var page = consolePageSlice(rows, "market", 8);
  var toolbar = [
    '<form class="oa-filter-bar" data-console-market-form>',
    '<label><span>종목 검색</span><input type="search" data-console-market-search value="' + escapeHtml(marketState.consoleMarketSearch || "") + '" placeholder="회사명 또는 코드" /></label>',
    '<label><span>범위</span><select data-console-market-scope><option value="all"' + (marketState.consoleMarketScope === "all" ? " selected" : "") + '>전체</option><option value="holding"' + (marketState.consoleMarketScope === "holding" ? " selected" : "") + '>보유</option><option value="watchlist"' + (marketState.consoleMarketScope === "watchlist" ? " selected" : "") + '>관심</option></select></label>',
    '</form>'
  ].join("");
  var table = page.items.length ? '<div class="oa-data-table" data-console-keyed-list="market-instruments"><div class="oa-table-head oa-market-row"><span>종목</span><span>현재가</span><span>수급</span><span>영향·판단</span><span></span></div>' + page.items.map(renderMarketInstrumentRow).join("") + '</div>' : renderConsoleEmpty("조건에 맞는 내 종목이 없습니다", "전체 종목에서 회사를 검색해 관심 종목으로 추가하세요.", '<button class="text-button primary" type="button" data-market-workspace="universe">전체 종목 찾기</button>');
  return [
    renderMarketWatchlistCommand(snapshot),
    toolbar,
    renderConsoleSurface({ kicker: "MY INSTRUMENTS", title: "보유·관심 종목", description: "선택 계정의 보유 종목과 관심 종목을 빠짐없이 확인합니다.", meta: rows.length + "개", body: renderConsoleLiveRegion("market-instrument-body", table), footer: renderConsolePager("market", page) })
  ].join("");
}

function renderMarketNewsWorkspace(evidence) {
  var page = consolePageSlice(evidence, "marketNews", 10);
  var news = page.items.length ? '<div class="oa-news-list" data-console-keyed-list="market-news">' + page.items.map(renderMarketNewsRow).join("") + '</div>' : (researchState.researchEvidenceLoading ? renderConsoleListSkeleton("oa-news-row", ["영향", "기사", "출처"], 6) : renderConsoleEmpty("저장된 시장 근거가 없습니다", "뉴스·공시 수집과 영향 분석이 완료되면 최신순으로 표시합니다.", renderWorkDetailButton("feed-source-board", "", "수집 상태", "text-button compact")));
  return renderConsoleSurface({ kicker: "NEWS & FLOW", title: "뉴스·수급 영향", description: "최신 뉴스와 공시를 투자 영향 기준으로 확인합니다.", meta: evidence.length + "건", body: renderConsoleLiveRegion("market-news-body", news), footer: renderConsolePager("marketNews", page) });
}

function renderMarketFlowWorkspace(snapshot) {
  return [
    '<div class="market-flow-workspace">',
    renderInvestmentMoneyFlowPanel(snapshot),
    renderConsoleSurface({
      kicker: "FLOW METHOD",
      title: "새 자금 흐름을 읽는 기준",
      description: "자산·섹터 간 이동과 아직 확정되지 않은 구조 변화를 분리합니다.",
      body: '<div class="oa-health-list"><div><span>관측</span><strong>가격·거래량·수급</strong></div><div><span>연결</span><strong>뉴스·공시·거시 사건</strong></div><div><span>검증</span><strong>가설·규칙·관계 변화</strong></div></div>'
    }),
    '</div>'
  ].join("");
}

function renderMarketConsole(snapshot) {
  var rows = filteredConsoleInstrumentRows(snapshot);
  var evidence = consoleResearchItems();
  var negative = evidence.filter(function (item) { return researchEvidenceImpactMeta(item).tone === "danger"; }).length;
  var portfolio = selectConsolePortfolio(snapshot);
  var quoteReady = rows.filter(function (row) { return row.quoteAvailable; }).length;
  var quoteState = rows.length && quoteReady === rows.length ? portfolio.freshness : {
    label: quoteReady ? "부분 수집" : "시세 대기",
    detail: quoteReady + "/" + rows.length + " 종목 수집",
    tone: quoteReady ? "warn" : "caution"
  };
  var metrics = [
    { label: "보유 종목", value: portfolio.holdingCount + "개", detail: "계정 기준", target: { type: "market", value: "mine", scope: "holding" } },
    { label: "관심 전용 종목", value: portfolio.watchCount + "개", detail: "보유와 겹치는 종목 제외", target: { type: "market", value: "mine", scope: "watchlist" } },
    { label: "저장 근거", value: evidence.length + "건", detail: "뉴스·공시", target: { type: "market", value: "news" } },
    { label: "확인된 부정적 근거", value: negative + "건", detail: "미분석 자료의 위험은 미확인", tone: negative ? "danger" : "neutral", target: { type: "market", value: "news" } },
    { label: "시세 상태", value: quoteState.label, detail: quoteState.detail, tone: quoteState.tone, target: { type: "detail", value: "feed-source-board" } }
  ];
  var mode = normalizeMarketWorkspaceMode(marketState.marketWorkspaceMode);
  var workspace = mode === "universe"
    ? renderSymbolUniversePanel({ full: true })
    : (mode === "flow"
      ? renderMarketFlowWorkspace(snapshot)
      : (mode === "news" ? renderMarketNewsWorkspace(evidence) : renderMarketMineWorkspace(snapshot, rows)));
  return renderConsoleManagedPage("feed", metrics, [
    renderMarketWorkspaceNavigation(),
    '<div class="market-workspace market-workspace-' + escapeHtml(mode) + '" data-console-monitor-destination="market" tabindex="-1">',
    workspace,
    '</div>'
  ].join(""), { secondaryMetrics: true });
}

export { renderMarketConsole };
