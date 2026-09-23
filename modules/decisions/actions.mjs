import { selectConsoleDecisionRows } from "./selectors.mjs";
import { investmentActionInvalidation, investmentActionLinkedAlert, investmentActionNextWindow, investmentActionPlaybook, investmentActionUserPresentation, investmentActionValidation, investmentAnalysisModel, normalizeInvestmentChartPeriod, renderInvestmentActionDecisionDetail } from "./strategy.mjs";
import { renderInvestmentDecisionCell } from "./today.mjs";
import { stockDisplayName } from "../instruments/catalog.mjs";
import { renderInstrumentWorkspaceLink } from "../instruments/workspace.mjs";
import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { mobileInfiniteScrollEnabled, renderMobileInfiniteScrollFooter } from "../navigation/infinite-list.mjs";
import { primeActiveTabData } from "../navigation/preload.mjs";
import { normalizeInvestmentGraphLayer, normalizeStrategySection, sectionModeForPage, writeStrategySectionHistory } from "../navigation/routes.mjs";
import { notificationJobResolvedSymbol } from "../notifications/detail.mjs";
import { render } from "../render/scheduler.mjs";
import { saveInvestmentLanguageTerm } from "../settings/language.mjs";
import { latestChangedFirst, renderRecordChangedAt, sourceLabel } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

function investmentActionSearchText(row) {
  row = row || {};
  var graph = row.graph || {};
  var validation = investmentActionValidation(row);
  var playbook = investmentActionPlaybook(row);
  return [
    row.symbol,
    row.name,
    row.displayName,
    row.market,
    row.sector,
    row.decision,
    row.dataQuality,
    row.apiSource,
    graph.reason,
    graph.basis,
    validation.label,
    validation.detail,
    playbook.label,
    investmentActionInvalidation(row),
    investmentActionNextWindow(row),
    investmentActionLinkedAlert(row),
    Array.isArray(row.reasons) ? row.reasons.join(" ") : "",
    Array.isArray(graph.nextChecks) ? graph.nextChecks.join(" ") : ""
  ].filter(Boolean).join(" ").toLowerCase();
}

function investmentActionFilteredRows(rows) {
  var query = String(decisionsState.investmentActionQuery || "").trim().toLowerCase();
  rows = Array.isArray(rows) ? rows : [];
  var filtered = !query ? rows.slice() : rows.filter(function (row) {
    return investmentActionSearchText(row).indexOf(query) >= 0;
  });
  return latestChangedFirst(filtered);
}

function investmentActionPageInfo(rows) {
  rows = Array.isArray(rows) ? rows : [];
  var pageSize = Math.max(1, Number(decisionsState.investmentActionPageSize || 6));
  var totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  var page = Math.min(Math.max(1, Number(decisionsState.investmentActionPage || 1)), totalPages);
  var start = mobileInfiniteScrollEnabled() ? 0 : (rows.length ? (page - 1) * pageSize : 0);
  var end = Math.min(rows.length, start + pageSize);
  if (mobileInfiniteScrollEnabled()) end = Math.min(rows.length, page * pageSize);
  return {
    page: page,
    pageSize: pageSize,
    totalPages: totalPages,
    totalRows: rows.length,
    start: start,
    end: end,
    visibleRows: rows.slice(start, end)
  };
}

function renderInvestmentActionToolbar(rows, filteredRows, pageInfo) {
  var query = String(decisionsState.investmentActionQuery || "");
  var from = pageInfo.totalRows ? pageInfo.start + 1 : 0;
  return [
    '<form class="investment-action-toolbar"' + cardFormatAttrs("control-strip", "compact") + ' data-investment-action-search-form>',
    '<label class="investment-action-search">',
    '<span>후보 검색</span>',
    '<input data-investment-action-query type="search" value="' + escapeHtml(query) + '" placeholder="종목, 판단, 근거 검색" autocomplete="off" />',
    '</label>',
    '<div class="investment-action-count" aria-label="투자 후보 표시 범위">',
    '<strong>' + escapeHtml(from) + '-' + escapeHtml(pageInfo.end) + '</strong>',
    '<span>/ ' + escapeHtml(filteredRows.length) + '개 후보 · 전체 ' + escapeHtml(rows.length) + '</span>',
    '</div>',
    '<button class="mini-button primary" type="submit">검색</button>',
    '</form>'
  ].join("");
}

function renderInvestmentActionPager(pageInfo) {
  if (!pageInfo || pageInfo.totalPages <= 1) return "";
  if (mobileInfiniteScrollEnabled()) {
    return renderMobileInfiniteScrollFooter({
      loaded: pageInfo.visibleRows.length,
      total: pageInfo.totalRows,
      hasNext: pageInfo.page < pageInfo.totalPages,
      nextAttributes: 'data-investment-action-page="' + escapeHtml(pageInfo.page + 1) + '"'
    });
  }
  return [
    '<div class="investment-action-pager"' + cardFormatAttrs("pagination-strip", "compact") + '>',
    '<button class="mini-button" type="button" data-investment-action-page="' + escapeHtml(Math.max(1, pageInfo.page - 1)) + '"' + (pageInfo.page <= 1 ? " disabled" : "") + '>이전</button>',
    '<span>' + escapeHtml(pageInfo.page) + ' / ' + escapeHtml(pageInfo.totalPages) + '</span>',
    '<button class="mini-button" type="button" data-investment-action-page="' + escapeHtml(Math.min(pageInfo.totalPages, pageInfo.page + 1)) + '"' + (pageInfo.page >= pageInfo.totalPages ? " disabled" : "") + '>다음</button>',
    '</div>'
  ].join("");
}

function investmentActionKey(row, index) {
  row = row || {};
  return String(row.decisionKey || row.symbol || row.id || [row.name, row.source, row.decision, row.dataQuality, (row.graph || {}).reason, index].filter(Boolean).join(":"));
}

function investmentActionByKey(key) {
  var rows = Array.isArray(investmentAnalysisModel(shellState.snapshot || {}).actionQueue) ? investmentAnalysisModel(shellState.snapshot || {}).actionQueue : [];
  return rows.filter(function (row, index) {
    return investmentActionKey(row, index) === key;
  })[0] || null;
}

function notificationDecisionLinkValue(job, key) {
  var context = job && job.context && typeof job.context === "object" ? job.context : {};
  if (key === "decisionEpisodeId") {
    return String((job && job.decisionEpisodeId) || context.investmentDecisionEpisodeId || context.decisionEpisodeId || "");
  }
  if (key === "decisionKey") return String((job && job.decisionKey) || context.decisionKey || "");
  return "";
}

function relatedNotificationForInvestmentAction(row) {
  row = row || {};
  var episodeId = String(row.decisionEpisodeId || "");
  var decisionKey = String(row.decisionKey || "");
  var accountId = String(row.accountId || "default");
  var symbol = String(row.symbol || "").toUpperCase();
  return latestChangedFirst(notificationsState.notificationJobItems || []).filter(function (job) {
    if (decisionKey && notificationDecisionLinkValue(job, "decisionKey") === decisionKey) return true;
    if (episodeId && notificationDecisionLinkValue(job, "decisionEpisodeId") === episodeId) return true;
    return Boolean(symbol) && String(job.accountId || "default") === accountId && notificationJobResolvedSymbol(job) === symbol;
  })[0] || null;
}

function relatedDecisionForNotification(job) {
  var episodeId = notificationDecisionLinkValue(job, "decisionEpisodeId");
  var decisionKey = notificationDecisionLinkValue(job, "decisionKey");
  var accountId = String((job && job.accountId) || "default");
  var symbol = notificationJobResolvedSymbol(job);
  return selectConsoleDecisionRows(shellState.snapshot || {}).filter(function (row) {
    if (decisionKey && row.decisionKey === decisionKey) return true;
    if (episodeId && row.decisionEpisodeId === episodeId) return true;
    return Boolean(symbol) && row.accountId === accountId && row.symbol === symbol;
  })[0] || null;
}

function investmentActionWorkDetailPayload(key) {
  var row = investmentActionByKey(key);
  if (!row) return null;
  var name = row.name || stockDisplayName(row.symbol, row);
  var relatedNotification = relatedNotificationForInvestmentAction(row);
  return {
    kicker: "종목 판단",
    title: name || row.symbol || "투자 판단 후보",
    meta: [row.symbol, sourceLabel(row.source), row.market, row.sector].filter(Boolean).join(" · "),
    body: renderInstrumentWorkspaceLink(row.symbol, "종목 전체 흐름") + renderInvestmentActionDecisionDetail(row, relatedNotification, false)
  };
}

function renderInvestmentActionRow(row, index) {
  row = row || {};
  var name = row.name || stockDisplayName(row.symbol, row);
  var key = investmentActionKey(row, index);
  var expanded = decisionsState.expandedInvestmentActionKey === key;
  var display = investmentActionUserPresentation(row);
  return [
    '<div class="investment-action-row compact ' + escapeHtml(expanded ? "active" : "") + '"' + cardTypeAttrs("action-queue-card", display.tone) + cardFormatAttrs("decision-ticket", "compact") + '>',
    '<div class="investment-action-main">',
    '<strong>' + escapeHtml(name) + '</strong>',
    '<span>' + escapeHtml([row.symbol, sourceLabel(row.source), row.market, row.sector].filter(Boolean).join(" · ")) + '</span>',
    renderRecordChangedAt(row),
    '</div>',
    '<div class="investment-action-stage">',
    '<span class="tone-chip ' + escapeHtml(display.tone) + '">' + escapeHtml(display.actionLabel) + '</span>',
    '<em>' + escapeHtml(display.statusLabel) + '</em>',
    '</div>',
    '<div class="investment-action-meta">',
    '<span>데이터 <strong>' + escapeHtml(display.quality.label) + '</strong></span>',
    '<span>손익률 <strong>' + escapeHtml(display.profitLoss) + '</strong></span>',
    '<span>분석 <strong>' + escapeHtml(display.statusLabel) + '</strong></span>',
    '<span>출처 <strong>' + escapeHtml(row.apiSource || "미기록") + '</strong></span>',
    '</div>',
    '<p>' + escapeHtml(display.explanation) + '</p>',
    '<div class="investment-decision-rail">',
    renderInvestmentDecisionCell(display.blocked ? "보류 해제 조건" : "판단 변경 조건", display.invalidation, "", display.tone),
    renderInvestmentDecisionCell("다시 볼 시점", display.nextWindow, "", display.quality.tone),
    renderInvestmentDecisionCell("알림", display.linkedAlert, "알림 정책과 연결", display.tone),
    '</div>',
    '<div class="investment-action-checks">',
    '<span>' + escapeHtml(display.checks.length ? display.checks[0] : display.nextAction) + '</span>',
    '<button class="mini-button" type="button" data-investment-action-toggle="' + escapeHtml(key) + '">' + escapeHtml(expanded ? "상세 표시 중" : "상세") + '</button>',
    renderWorkDetailButton("investment-action", key, "팝업", "mini-button ghost"),
    '</div>',
    '</div>'
  ].join("");
}

function renderInvestmentActionDetailPanel(rows) {
  var row = investmentActionByKey(decisionsState.expandedInvestmentActionKey);
  if (!row && Array.isArray(rows)) {
    row = rows.filter(function (item, index) {
      return investmentActionKey(item, index) === decisionsState.expandedInvestmentActionKey;
    })[0] || null;
  }
  return [
    '<aside class="investment-action-detail-panel" aria-label="선택 투자 후보 상세">',
    row ? renderInvestmentActionInlineDetail(row) : renderEmptyState({
      tone: "muted",
      label: "Detail",
      title: "투자 후보를 선택하세요",
      description: "후보 목록은 핵심 결론만 보여주고, 판단에 사용한 정보와 다시 확인할 조건은 상세에서 확인합니다.",
      meta: ["결론", "확인한 정보", "다음 행동"]
    }),
    '</aside>'
  ].join("");
}

function renderInvestmentActionInlineDetail(row) {
  return renderInvestmentActionDecisionDetail(row || {}, relatedNotificationForInvestmentAction(row || {}), true);
}

function bindDecisionsControls(app) {
Array.prototype.slice.call(app.querySelectorAll("[data-investment-graph-layer]")).forEach(function (button) {
    button.addEventListener("click", function () {
      ontologyState.activeInvestmentGraphLayer = normalizeInvestmentGraphLayer(button.getAttribute("data-investment-graph-layer"));
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-investment-action-toggle]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.getAttribute("data-investment-action-toggle") || "";
      decisionsState.expandedInvestmentActionKey = key;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-investment-evidence-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      decisionsState.activeInvestmentEvidenceKey = button.getAttribute("data-investment-evidence-select") || "";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-investment-action-query]")).forEach(function (field) {
    field.addEventListener("input", function () {
      decisionsState.investmentActionQuery = field.value;
      decisionsState.investmentActionPage = 1;
    });
    field.addEventListener("change", function () {
      decisionsState.investmentActionQuery = field.value;
      decisionsState.investmentActionPage = 1;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-investment-action-search-form]")).forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var field = form.querySelector("[data-investment-action-query]");
      decisionsState.investmentActionQuery = field ? field.value : decisionsState.investmentActionQuery;
      decisionsState.investmentActionPage = 1;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-investment-action-page]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var page = Number(button.getAttribute("data-investment-action-page"));
      if (!Number.isFinite(page)) return;
      decisionsState.investmentActionPage = Math.max(1, page);
      render();
    });
  });
var languageSearch = app.querySelector("[data-investment-language-search]");
if (languageSearch) {
    languageSearch.addEventListener("change", function () {
      settingsState.investmentLanguageSearch = languageSearch.value || "";
      render();
    });
  }
var languageForm = app.querySelector("[data-investment-language-form]");
if (languageForm) {
    languageForm.addEventListener("submit", function (event) {
      event.preventDefault();
      saveInvestmentLanguageTerm(languageForm);
    });
  }
var previewLanguageText = app.querySelector("[data-investment-language-preview-text]");
if (previewLanguageText) {
    previewLanguageText.addEventListener("input", function () {
      settingsState.investmentLanguagePreviewText = previewLanguageText.value || "";
    });
  }
var previewLanguageLevel = app.querySelector("[data-investment-language-preview-level]");
if (previewLanguageLevel) {
    previewLanguageLevel.addEventListener("change", function () {
      settingsState.investmentLanguagePreviewLevel = previewLanguageLevel.value || "absoluteBeginner";
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-strategy-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var section = normalizeStrategySection(button.getAttribute("data-strategy-section"));
      if (section === decisionsState.activeStrategySection) return;
      decisionsState.activeStrategySection = section;
      navigationState.pageViewModes.modeling = sectionModeForPage("modeling", section);
      writeStrategySectionHistory(section);
      primeActiveTabData("modeling");
      render({ transition: "section" });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-investment-chart-period]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var period = normalizeInvestmentChartPeriod(button.getAttribute("data-investment-chart-period"));
      if (period === decisionsState.activeInvestmentChartPeriod) return;
      decisionsState.activeInvestmentChartPeriod = period;
      render();
    });
  });
}

export { bindDecisionsControls, investmentActionByKey, investmentActionFilteredRows, investmentActionKey, investmentActionPageInfo, investmentActionWorkDetailPayload, relatedDecisionForNotification, renderInvestmentActionDetailPanel, renderInvestmentActionPager, renderInvestmentActionRow, renderInvestmentActionToolbar };
