import { loadSymbolUniverse } from "../instruments/universe.mjs";
import { openWorkDetailLayer } from "../navigation/detail.mjs";
import { mobileInfiniteScrollEnabled, renderMobileInfiniteScrollFooter } from "../navigation/infinite-list.mjs";
import { navigateToTab } from "../navigation/router.mjs";
import { normalizeMarketWorkspaceMode, writeMarketWorkspaceHistory } from "../navigation/routes.mjs";
import { focusElementWithoutScroll } from "../navigation/scroll.mjs";
import { loadNotificationJobs, resetNotificationJobsPaging } from "../notifications/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { escapeHtml } from "./text.mjs";
import { renderSecondaryDisclosure } from "./disclosure.mjs";
import { pageStructureMeta } from "../shell/catalog.mjs";
import { app } from "../shell/root.mjs";
import { calendarState } from "../state/calendar.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { shellState } from "../state/shell.mjs";
import { universeState } from "../state/universe.mjs";

function consolePageNumber(key) {
  var page = Number(((shellState.consolePages || {})[key]) || 1);
  return Math.max(1, isFinite(page) ? Math.floor(page) : 1);
}

function consolePageSlice(items, key, pageSize) {
  items = Array.isArray(items) ? items : [];
  pageSize = Math.max(1, Number(pageSize || 8));
  var totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  var page = Math.min(consolePageNumber(key), totalPages);
  if (!shellState.consolePages) shellState.consolePages = {};
  shellState.consolePages[key] = page;
  var start = mobileInfiniteScrollEnabled() ? 0 : (page - 1) * pageSize;
  return {
    items: items.slice(start, page * pageSize),
    page: page,
    total: items.length,
    totalPages: totalPages
  };
}

function renderConsolePager(key, pageInfo) {
  if (mobileInfiniteScrollEnabled()) {
    return '<div class="oa-pager-slot" data-console-live-region="pager-' + escapeHtml(key) + '">' + renderMobileInfiniteScrollFooter({
      loaded: pageInfo ? pageInfo.items.length : 0,
      total: pageInfo ? pageInfo.total : 0,
      hasNext: Boolean(pageInfo && pageInfo.page < pageInfo.totalPages),
      nextAttributes: 'data-console-page="' + escapeHtml(key) + '" data-console-page-value="' + escapeHtml((pageInfo ? pageInfo.page : 0) + 1) + '"'
    }) + '</div>';
  }
  var pager = !pageInfo || pageInfo.totalPages <= 1 ? "" : [
    '<nav class="oa-pager" aria-label="목록 페이지">',
    '<span>전체 ' + escapeHtml(pageInfo.total) + '건</span>',
    '<div>',
    '<button class="icon-button" type="button" data-console-page="' + escapeHtml(key) + '" data-console-page-value="' + escapeHtml(pageInfo.page - 1) + '"' + (pageInfo.page <= 1 ? ' disabled' : '') + ' title="이전 페이지" aria-label="이전 페이지">&larr;</button>',
    '<strong>' + escapeHtml(pageInfo.page) + ' / ' + escapeHtml(pageInfo.totalPages) + '</strong>',
    '<button class="icon-button" type="button" data-console-page="' + escapeHtml(key) + '" data-console-page-value="' + escapeHtml(pageInfo.page + 1) + '"' + (pageInfo.page >= pageInfo.totalPages ? ' disabled' : '') + ' title="다음 페이지" aria-label="다음 페이지">&rarr;</button>',
    '</div>',
    '</nav>'
  ].join("");
  return '<div class="oa-pager-slot" data-console-live-region="pager-' + escapeHtml(key) + '">' + pager + '</div>';
}

function renderConsoleLiveRegion(key, content) {
  return '<div class="oa-live-region" data-console-live-region="' + escapeHtml(key) + '">' + (content || "") + '</div>';
}

function consoleToneRank(tone) {
  return { danger: 5, caution: 4, watch: 3, hold: 2, muted: 1 }[String(tone || "hold")] || 0;
}

function consoleQualityMeta(value) {
  var quality = String(value || "").toLowerCase();
  if (["actual", "live", "fresh", "ok"].indexOf(quality) >= 0) return { label: "실데이터", tone: "watch" };
  if (["cache", "cached", "stale"].indexOf(quality) >= 0) return { label: quality === "stale" ? "지연" : "캐시", tone: "caution" };
  if (["mock", "demo"].indexOf(quality) >= 0) return { label: "데모", tone: "hold" };
  if (["missing", "gap", "error", "failed"].indexOf(quality) >= 0) return { label: "부족", tone: "danger" };
  return { label: value || "확인", tone: "hold" };
}

function consoleMetricTargetAttributes(target) {
  target = target && typeof target === "object" ? target : {};
  var type = String(target.type || "").trim();
  if (!type) return "";
  var attributes = ' data-console-metric-target="' + escapeHtml(type) + '"';
  if (target.value != null) attributes += ' data-console-metric-value="' + escapeHtml(String(target.value)) + '"';
  if (target.key != null) attributes += ' data-console-metric-key="' + escapeHtml(String(target.key)) + '"';
  if (target.scope != null) attributes += ' data-console-metric-scope="' + escapeHtml(String(target.scope)) + '"';
  if (target.quality != null) attributes += ' data-console-metric-quality="' + escapeHtml(String(target.quality)) + '"';
  if (target.status != null) attributes += ' data-console-metric-status="' + escapeHtml(String(target.status)) + '"';
  if (type === "detail") {
    attributes += ' data-work-detail="' + escapeHtml(String(target.value || "")) + '"';
    attributes += ' data-work-detail-key="' + escapeHtml(String(target.key || "")) + '"';
  }
  return attributes;
}

function renderConsoleMetricStrip(metrics) {
  var options = arguments.length > 1 && arguments[1] ? arguments[1] : {};
  var loading = Boolean(options.loading);
  return [
    '<section class="oa-metric-strip" aria-label="핵심 지표"' + (loading ? ' aria-busy="true"' : '') + '>',
    (metrics || []).slice(0, 6).map(function (metric) {
      if (loading) {
        return [
          '<div class="oa-metric is-loading">',
          '<span>' + escapeHtml(metric.label || "-") + '</span>',
          '<i class="oa-skeleton-line oa-skeleton-value" aria-hidden="true"></i>',
          '<i class="oa-skeleton-line oa-skeleton-detail" aria-hidden="true"></i>',
          '</div>'
        ].join("");
      }
      var targetAttributes = consoleMetricTargetAttributes(metric.target);
      var interactive = Boolean(targetAttributes);
      var tag = interactive ? "button" : "div";
      return [
        '<' + tag + (interactive ? ' type="button"' : '') + ' class="oa-metric ' + escapeHtml(metric.tone || "neutral") + (interactive ? ' is-link' : '') + '"' + targetAttributes + (interactive ? ' aria-label="' + escapeHtml([metric.label || "지표", metric.value == null ? "" : metric.value, "상세 보기"].filter(Boolean).join(" ")) + '"' : '') + '>',
        '<span>' + escapeHtml(metric.label || "-") + '</span>',
        '<strong>' + escapeHtml(metric.value == null ? "-" : metric.value) + '</strong>',
        '<em>' + escapeHtml(metric.detail || "") + '</em>',
        interactive ? '<i class="oa-metric-arrow" aria-hidden="true">&rarr;</i>' : '',
        '</' + tag + '>'
      ].join("");
    }).join(""),
    '</section>'
  ].join("");
}

function focusConsoleMetricDestination(name) {
  var destination = String(name || "");
  if (!destination) return;
  window.setTimeout(function () {
    var target = Array.prototype.slice.call(app.querySelectorAll("[data-console-monitor-destination]")).filter(function (node) {
      return node.getAttribute("data-console-monitor-destination") === destination;
    })[0];
    if (!target) return;
    if (target.scrollIntoView) target.scrollIntoView({ behavior: "smooth", block: "start" });
    if (target.focus) focusElementWithoutScroll(target);
  }, 90);
}

function activateConsoleMetricTarget(button) {
  if (!button) return false;
  var type = String(button.getAttribute("data-console-metric-target") || "");
  var value = String(button.getAttribute("data-console-metric-value") || "");
  var key = String(button.getAttribute("data-console-metric-key") || "");
  var scope = String(button.getAttribute("data-console-metric-scope") || "");
  var quality = String(button.getAttribute("data-console-metric-quality") || "");
  var status = String(button.getAttribute("data-console-metric-status") || "");
  if (type === "detail") {
    openWorkDetailLayer(value, key);
    return true;
  }
  if (type === "tab") {
    navigateToTab(value);
    return true;
  }
  if (type === "market") {
    marketState.marketWorkspaceMode = normalizeMarketWorkspaceMode(value);
    marketState.consoleMarketScope = scope || "all";
    marketState.consoleMarketSearch = "";
    shellState.consolePages.market = 1;
    if (navigationState.activeTab !== "feed") navigateToTab("feed");
    else render({ transition: "section" });
    writeMarketWorkspaceHistory(marketState.marketWorkspaceMode);
    if (marketState.marketWorkspaceMode === "universe" && !universeState.symbolUniverseLoaded && !universeState.symbolUniverseLoading) loadSymbolUniverse();
    focusConsoleMetricDestination("market");
    return true;
  }
  if (type === "decision") {
    decisionsState.consoleDecisionSearch = "";
    decisionsState.consoleDecisionScope = "all";
    decisionsState.consoleDecisionAction = value || "all";
    decisionsState.consoleDecisionQuality = quality || "all";
    decisionsState.consoleDecisionStatus = status || "all";
    decisionsState.consoleDecisionView = key || "all";
    shellState.consolePages.decision = 1;
    render({ transition: "section" });
    focusConsoleMetricDestination("decisions");
    return true;
  }
  if (type === "notification") {
    notificationsState.notificationJobSearch = "";
    notificationsState.notificationInboxFilter = value || "all";
    notificationsState.notificationJobStatusFilter = scope || "all";
    notificationsState.notificationJobTypeFilter = "all";
    resetNotificationJobsPaging();
    loadNotificationJobs();
    render({ transition: "section" });
    focusConsoleMetricDestination("alerts");
    return true;
  }
  if (type === "anchor") {
    focusConsoleMetricDestination(value);
    return true;
  }
  if (type === "calendar-entry") {
    calendarState.calendarEntryModalOpen = true;
    render({ transition: "detail-open" });
    return true;
  }
  return false;
}

function renderConsoleSurface(options) {
  options = options || {};
  return [
    '<section class="oa-surface ' + escapeHtml(options.className || "") + '">',
    '<header class="oa-surface-head">',
    '<div>',
    options.kicker ? '<span>' + escapeHtml(options.kicker) + '</span>' : '',
    '<h2>' + escapeHtml(options.title || "") + '</h2>',
    options.description ? '<p>' + escapeHtml(options.description) + '</p>' : '',
    '</div>',
    options.meta ? '<strong>' + escapeHtml(options.meta) + '</strong>' : '',
    options.actions ? '<div class="oa-surface-actions">' + options.actions + '</div>' : '',
    '</header>',
    options.body || '',
    options.footer || '',
    '</section>'
  ].join("");
}

function renderConsoleEmpty(title, description, action) {
  return [
    '<div class="oa-empty">',
    '<strong>' + escapeHtml(title || "표시할 데이터가 없습니다") + '</strong>',
    '<span>' + escapeHtml(description || "데이터가 수집되면 이 목록에 표시합니다.") + '</span>',
    action || '',
    '</div>'
  ].join("");
}

function renderConsoleListSkeleton(rowClass, labels, rowCount) {
  var columns = Array.isArray(labels) ? labels : [];
  var count = Math.max(2, Number(rowCount || 4));
  return [
    '<div class="oa-data-table oa-list-skeleton" aria-busy="true">',
    '<div class="oa-table-head ' + escapeHtml(rowClass || "") + '">',
    columns.map(function (label) { return '<span>' + escapeHtml(label) + '</span>'; }).join(""),
    '</div>',
    Array(count).fill(0).map(function () {
      return '<div class="oa-data-row ' + escapeHtml(rowClass || "") + '">' + columns.map(function () {
        return '<span><i class="oa-skeleton-line" aria-hidden="true"></i><i class="oa-skeleton-line short" aria-hidden="true"></i></span>';
      }).join("") + '</div>';
    }).join(""),
    '</div>'
  ].join("");
}

function renderConsoleManagedPage(pageId, metrics, content, options) {
  var structure = pageStructureMeta(pageId);
  options = options || {};
  var secondaryMetrics = options.secondaryMetrics === true;
  var metricMarkup = metrics && metrics.length ? renderConsoleMetricStrip(metrics, options) : "";
  return [
    '<div class="managed-page oa-console-page oa-console-page-' + escapeHtml(pageId) + '" data-console-workspace="' + escapeHtml(pageId) + '" data-structure-layer="' + escapeHtml(structure.layer) + '">',
    options.leading || '',
    secondaryMetrics ? "" : metricMarkup,
    content,
    secondaryMetrics && metricMarkup ? renderSecondaryDisclosure(pageId + "-metrics", "전체 현황", metricMarkup) : "",
    '</div>'
  ].join("");
}

export { activateConsoleMetricTarget, consoleMetricTargetAttributes, consolePageSlice, consoleQualityMeta, renderConsoleEmpty, renderConsoleListSkeleton, renderConsoleLiveRegion, renderConsoleManagedPage, renderConsolePager, renderConsoleSurface };
