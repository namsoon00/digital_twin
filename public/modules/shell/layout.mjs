import { accountFreshness } from "../accounts/balance.mjs";
import { renderWatchlistAccountPicker } from "../accounts/watchlist.mjs";
import { renderCalendarEntryModal, renderInvestmentCalendarCandidateConfirmation } from "../calendar/workspace.mjs";
import { topbarCollapsed } from "../navigation/chrome.mjs";
import { renderWorkDetailLayer } from "../navigation/detail.mjs";
import { renderCommandPalette } from "../navigation/palette.mjs";
import { activeScrollKey } from "../navigation/scroll.mjs";
import { pendingTabTransitionCell } from "../navigation/transition-runtime.mjs";
import { renderOntologyGraphExpandedOverlay } from "../ontology/graphs.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { activeTabMeta, appBrandName, pageStructureMeta, webStyleContract } from "./catalog.mjs";
import { renderAppNavigation, renderAppRuntimeBanner, renderDeskbar, renderTabs, renderTopbarSyncState } from "./navigation.mjs";
import { renderActiveTab } from "./pages.mjs";
import { renderSnackbar } from "./snackbar.mjs";
import { navigationState } from "../state/navigation.mjs";
import { shellState } from "../state/shell.mjs";

function renderLoading() {
  return [
    '<main class="shell loading-shell" aria-busy="true">',
    '<section class="loading-stage" aria-label="앱 초기화">',
    '<div class="loading-brand">',
    '<span class="app-brand-mark" aria-hidden="true"><span></span></span>',
    '<div>',
    '<p class="label">' + escapeHtml(appBrandName) + '</p>',
    '<h1>데이터를 불러오는 중</h1>',
    '<p class="subtle">잠시만 기다려주세요.</p>',
    '</div>',
    '</div>',
    '<div class="loading-progress" role="progressbar" aria-label="초기 데이터 동기화" aria-valuemin="0" aria-valuemax="100">',
    '<span></span>',
    '</div>',
    '</section>',
    '<section class="loading-skeleton-board" aria-hidden="true">',
    '<div class="loading-skeleton-head">',
    '<span></span>',
    '<span></span>',
    '</div>',
    '<div class="loading-skeleton-grid">',
    '<span></span><span></span><span></span><span></span>',
    '</div>',
    '<div class="loading-skeleton-list">',
    '<span></span><span></span><span></span>',
    '</div>',
    '</section>',
    '</main>'
  ].join("");
}

function renderEmptyState(options) {
  options = options || {};
  var tone = options.tone || "muted";
  var label = options.label || "State";
  var title = options.title || "표시할 데이터가 없습니다";
  var description = options.description || "데이터가 들어오면 같은 위치에 표시합니다.";
  var meta = Array.isArray(options.meta) ? options.meta : [];
  return [
    '<div class="empty-state ' + escapeHtml(tone) + '"' + cardTypeAttrs("empty-state", tone) + '>',
    '<div class="empty-state-copy">',
    '<p class="label">' + escapeHtml(label) + '</p>',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<span>' + escapeHtml(description) + '</span>',
    '</div>',
    meta.length ? '<div class="empty-state-meta">' + meta.map(function (item) {
      return '<span>' + escapeHtml(item) + '</span>';
    }).join("") + '</div>' : '',
    options.action || '',
    '</div>'
  ].join("");
}

function cardTypeAttrs(type, tone) {
  return ' data-card-type="' + escapeHtml(type || "container") + '"' + (tone ? ' data-card-tone="' + escapeHtml(tone) + '"' : '');
}

function cardFormatAttrs(format, density) {
  return ' data-card-format="' + escapeHtml(format || "surface") + '"' + (density ? ' data-card-density="' + escapeHtml(density) + '"' : '');
}

function renderError() {
  return [
    '<main class="shell">',
    '<section class="topbar">',
    '<div>',
    '<p class="eyebrow">' + escapeHtml(appBrandName) + '</p>',
    '<h1>' + escapeHtml(appBrandName) + '를 불러오지 못했습니다</h1>',
    '<p class="subtle">' + escapeHtml(shellState.error || "알 수 없는 오류") + "</p>",
    '</div>',
    '<button class="icon-button" type="button" data-action="refresh" title="새로고침" aria-label="새로고침">↻</button>',
    '</section>',
    '</main>'
  ].join("");
}

function renderDashboard(snapshot) {
  var toss = snapshot.toss || { mode: "demo" };
  var freshness = accountFreshness(snapshot);
  var modeLabel = snapshot.preview ? "Pages preview" : (toss.mode === "live" ? "Toss 연결" : "Local server");
  var modeClass = toss.mode === "live" ? "live" : "demo";
  var tab = activeTabMeta();
  var structure = pageStructureMeta(tab.id);
  var showHomeDeskbar = navigationState.activeTab === "overview";
  var subtitle = (structure.objective || tab.description || "운영") + " · 마지막 데이터 " + formatClock(snapshot.generatedAt) + " · " + freshness.label + " (" + freshness.detail + ")";
  return [
    '<main class="shell console-shell ' + escapeHtml(webStyleContract.shellClass) + (showHomeDeskbar ? " shell-home" : " shell-page") + (topbarCollapsed ? " topbar-collapsed" : "") + (pendingTabTransitionCell.value ? " is-tab-transitioning" : "") + '" data-web-style="' + escapeHtml(webStyleContract.id) + '" data-web-style-version="' + escapeHtml(webStyleContract.version) + '" data-active-group="' + escapeHtml(structure.groupId) + '">',
    renderAppNavigation(tab, modeLabel, modeClass, snapshot),
    renderAppRuntimeBanner(),
    '<section class="topbar web-style-topbar" data-style-region="topbar">',
    '<div class="topbar-copy">',
    '<p class="eyebrow">' + escapeHtml(structure.groupLabel + " / " + structure.layer) + '</p>',
    '<h1>' + escapeHtml(tab.label || "홈") + '</h1>',
    '<p class="subtle">' + escapeHtml(subtitle) + '</p>',
    '</div>',
    renderTopbarSyncState(snapshot),
    '</section>',
    showHomeDeskbar ? renderDeskbar(snapshot, modeLabel, modeClass) : '',
    '<section class="workspace-layout web-style-workspace" data-style-region="workspace">',
    renderTabs(),
    '<div class="workspace-main web-style-main" data-style-region="main" data-scroll-key="' + escapeHtml(activeScrollKey()) + '">',
    renderActiveTab(snapshot),
    '</div>',
    '</section>',
    renderOntologyGraphExpandedOverlay(),
    renderWatchlistAccountPicker(),
    renderCommandPalette(snapshot),
    renderWorkDetailLayer(),
    renderCalendarEntryModal(),
    renderInvestmentCalendarCandidateConfirmation(),
    renderSnackbar(),
    '</main>'
  ].join("");
}

export { cardFormatAttrs, cardTypeAttrs, renderDashboard, renderEmptyState, renderError, renderLoading };
