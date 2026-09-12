import { patchInvestmentCaseTabRegion } from "../decisions/case-detail.mjs";
import { investmentCaseOperatorAccess } from "../decisions/case-summary.mjs";
import { loadInvestmentCaseHistory, loadInvestmentCaseTrace, loadInvestmentFlow, loadInvestmentFlowDetail, loadInvestmentModel } from "../decisions/requests.mjs";
import { symbolUniverseRefreshCollapseTimerCell } from "../instruments/refresh-runtime.mjs";
import { loadInstrumentTimeline, loadInstrumentValuation, openInstrumentEventGroup } from "../instruments/timeline.mjs";
import { loadSymbolUniverseRefreshStatus, openSymbolUniverseRefreshResult, refreshSymbolUniverse } from "../instruments/universe.mjs";
import { closeWorkDetailLayer, openWorkDetailLayer } from "../navigation/detail.mjs";
import { activateCommandPaletteResult, closeCommandPalette, openCommandPalette } from "../navigation/palette.mjs";
import { navigateToTab } from "../navigation/router.mjs";
import { notificationJobByKey } from "../notifications/history.mjs";
import { loadNotificationJobDetailSection, loadNotificationJobs, markAllNotificationsRead, updateNotificationReceipt } from "../notifications/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { activateConsoleMetricTarget } from "../shared/console.mjs";
import { applyServiceWorkerUpdate, installOrbitAlpha } from "./install.mjs";
import { app } from "./root.mjs";
import { showSnackbar } from "./snackbar.mjs";
import { load } from "../snapshot/requests.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { instrumentsState } from "../state/instruments.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { shellState } from "../state/shell.mjs";
import { writePersistentPayload } from "../state/storage.mjs";
import { universeState } from "../state/universe.mjs";

var delegatedConsoleActionsBound = false;

var marketSearchTimer = null;

function bindDelegatedConsoleActions() {
  if (delegatedConsoleActionsBound) return;
  delegatedConsoleActionsBound = true;
  app.addEventListener("click", function (event) {
    var symbolRefreshAction = event.target.closest && event.target.closest('[data-action="refresh-symbol-universe"]');
    if (symbolRefreshAction && app.contains(symbolRefreshAction)) {
      event.preventDefault();
      if (!universeState.symbolUniverseRefreshing && !universeState.symbolUniverseLoading) refreshSymbolUniverse();
      return;
    }
    var installApp = event.target.closest && event.target.closest('[data-action="install-app"]');
    if (installApp && app.contains(installApp)) {
      event.preventDefault();
      installOrbitAlpha();
      return;
    }
    var retryConnectivity = event.target.closest && event.target.closest('[data-action="retry-connectivity"]');
    if (retryConnectivity && app.contains(retryConnectivity)) {
      event.preventDefault();
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        showSnackbar("아직 네트워크에 연결되지 않았습니다.", "danger");
      } else if (!shellState.refreshing) {
        load({ refresh: true });
      }
      return;
    }
    var applyAppUpdate = event.target.closest && event.target.closest('[data-action="apply-app-update"]');
    if (applyAppUpdate && app.contains(applyAppUpdate)) {
      event.preventDefault();
      applyServiceWorkerUpdate();
      return;
    }
    var openSymbolRefresh = event.target.closest && event.target.closest('[data-action="open-symbol-universe-refresh"], [data-snackbar-action="open-symbol-universe-refresh"]');
    if (openSymbolRefresh && app.contains(openSymbolRefresh)) {
      event.preventDefault();
      shellState.snackbar = null;
      openSymbolUniverseRefreshResult();
      return;
    }
    var toggleSymbolRefresh = event.target.closest && event.target.closest('[data-action="toggle-symbol-refresh-status"]');
    if (toggleSymbolRefresh && app.contains(toggleSymbolRefresh)) {
      event.preventDefault();
      if (symbolUniverseRefreshCollapseTimerCell.value) {
        clearTimeout(symbolUniverseRefreshCollapseTimerCell.value);
        symbolUniverseRefreshCollapseTimerCell.value = null;
      }
      universeState.symbolUniverseRefreshExpanded = !universeState.symbolUniverseRefreshExpanded;
      render();
      return;
    }
    var checkSymbolRefresh = event.target.closest && event.target.closest('[data-action="check-symbol-refresh-status"]');
    if (checkSymbolRefresh && app.contains(checkSymbolRefresh)) {
      event.preventDefault();
      loadSymbolUniverseRefreshStatus(true);
      return;
    }
    var retrySymbolRefresh = event.target.closest && event.target.closest('[data-action="retry-symbol-refresh"]');
    if (retrySymbolRefresh && app.contains(retrySymbolRefresh)) {
      event.preventDefault();
      var retryMarkets = String(retrySymbolRefresh.getAttribute("data-refresh-markets") || "").split(",").filter(Boolean);
      refreshSymbolUniverse(retryMarkets);
      return;
    }
    var showSymbolRefreshResults = event.target.closest && event.target.closest('[data-action="show-symbol-refresh-results"]');
    if (showSymbolRefreshResults && app.contains(showSymbolRefreshResults)) {
      event.preventDefault();
      var resultList = app.querySelector("[data-symbol-result-list]");
      if (resultList && resultList.scrollIntoView) resultList.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    var dismissSymbolRefresh = event.target.closest && event.target.closest('[data-action="dismiss-symbol-refresh-status"]');
    if (dismissSymbolRefresh && app.contains(dismissSymbolRefresh)) {
      event.preventDefault();
      universeState.symbolUniverseRefreshDismissedJobId = String((universeState.symbolUniverseRefresh || {}).jobId || "");
      universeState.symbolUniverseRefreshAcknowledgedJobId = universeState.symbolUniverseRefreshDismissedJobId;
      writePersistentPayload("orbitAlphaSymbolRefreshAcknowledged", universeState.symbolUniverseRefreshAcknowledgedJobId);
      render();
      return;
    }
    var paletteClose = event.target.closest && event.target.closest("[data-command-palette-close]");
    if (paletteClose && app.contains(paletteClose) && (!paletteClose.classList.contains("command-palette-backdrop") || event.target === paletteClose)) {
      event.preventDefault();
      closeCommandPalette();
      return;
    }
    var paletteResult = event.target.closest && event.target.closest("[data-command-palette-result]");
    if (paletteResult && app.contains(paletteResult)) {
      event.preventDefault();
      activateCommandPaletteResult(
        paletteResult.getAttribute("data-command-palette-result"),
        paletteResult.getAttribute("data-command-palette-key"),
        paletteResult.getAttribute("data-command-palette-tab")
      );
      return;
    }
    var instrumentTab = event.target.closest && event.target.closest("[data-instrument-workspace-tab]");
    if (instrumentTab && app.contains(instrumentTab)) {
      event.preventDefault();
      var instrumentSymbol = String(instrumentTab.getAttribute("data-instrument-symbol") || "").toUpperCase();
      instrumentsState.instrumentWorkspaceTabs[instrumentSymbol] = instrumentTab.getAttribute("data-instrument-workspace-tab") || "summary";
      render({ transition: "section" });
      if (instrumentsState.instrumentWorkspaceTabs[instrumentSymbol] === "valuation") loadInstrumentValuation(instrumentSymbol, false);
      if (["chart", "decision", "timeline"].indexOf(instrumentsState.instrumentWorkspaceTabs[instrumentSymbol]) >= 0) loadInstrumentTimeline(instrumentSymbol, false);
      return;
    }
    var instrumentRange = event.target.closest && event.target.closest("[data-instrument-range]");
    if (instrumentRange && app.contains(instrumentRange)) {
      event.preventDefault();
      var rangeSymbol = String(instrumentRange.getAttribute("data-instrument-symbol") || "").toUpperCase();
      instrumentsState.instrumentTimelineRanges[rangeSymbol] = instrumentRange.getAttribute("data-instrument-range") || "3m";
      render();
      loadInstrumentTimeline(rangeSymbol, false);
      return;
    }
    var instrumentEventGroup = event.target.closest && event.target.closest("[data-instrument-event-group]");
    if (instrumentEventGroup && app.contains(instrumentEventGroup)) {
      event.preventDefault();
      openInstrumentEventGroup(
        instrumentEventGroup.getAttribute("data-instrument-event-group"),
        instrumentEventGroup.getAttribute("data-instrument-event-selector")
      );
      return;
    }
    var instrumentRefresh = event.target.closest && event.target.closest("[data-instrument-timeline-refresh]");
    if (instrumentRefresh && app.contains(instrumentRefresh)) {
      event.preventDefault();
      loadInstrumentTimeline(instrumentRefresh.getAttribute("data-instrument-timeline-refresh"), true);
      return;
    }
    var instrumentValuationRefresh = event.target.closest && event.target.closest("[data-instrument-valuation-refresh]");
    if (instrumentValuationRefresh && app.contains(instrumentValuationRefresh)) {
      event.preventDefault();
      loadInstrumentValuation(instrumentValuationRefresh.getAttribute("data-instrument-valuation-refresh"), true);
      return;
    }
    var consoleMetricTarget = event.target.closest && event.target.closest("[data-console-metric-target]");
    if (consoleMetricTarget && app.contains(consoleMetricTarget)) {
      event.preventDefault();
      if (activateConsoleMetricTarget(consoleMetricTarget)) return;
    }
    var validationOpen = event.target.closest && event.target.closest("[data-open-validation]");
    if (validationOpen && app.contains(validationOpen)) {
      event.preventDefault();
      navigateToTab("experiments");
      return;
    }
    var decisionView = event.target.closest && event.target.closest("[data-decision-view]");
    if (decisionView && app.contains(decisionView)) {
      event.preventDefault();
      decisionsState.consoleDecisionView = decisionView.getAttribute("data-decision-view") || "attention";
      shellState.consolePages.decision = 1;
      render({ transition: "section" });
      return;
    }
    var flowRefresh = event.target.closest && event.target.closest('[data-action="refresh-investment-flow"]');
    if (flowRefresh && app.contains(flowRefresh)) {
      event.preventDefault();
      loadInvestmentFlow(true);
      return;
    }
    var investmentModelRefresh = event.target.closest && event.target.closest('[data-action="refresh-investment-model"]');
    if (investmentModelRefresh && app.contains(investmentModelRefresh)) {
      event.preventDefault();
      loadInvestmentModel(true);
      return;
    }
    var investmentModelManagementTab = event.target.closest && event.target.closest("[data-investment-model-management-tab]");
    if (investmentModelManagementTab && app.contains(investmentModelManagementTab)) {
      event.preventDefault();
      decisionsState.investmentModelManagementTab = String(investmentModelManagementTab.getAttribute("data-investment-model-management-tab") || "release");
      render({ transition: "section" });
      return;
    }
    var flowRetry = event.target.closest && event.target.closest("[data-investment-flow-retry]");
    if (flowRetry && app.contains(flowRetry)) {
      event.preventDefault();
      loadInvestmentFlowDetail(flowRetry.getAttribute("data-investment-flow-retry"), true);
      return;
    }
    var caseHistoryRetry = event.target.closest && event.target.closest("[data-investment-case-history-retry]");
    if (caseHistoryRetry && app.contains(caseHistoryRetry)) {
      event.preventDefault();
      var historyRetryKey = caseHistoryRetry.getAttribute("data-investment-case-history-retry");
      loadInvestmentCaseHistory(historyRetryKey, true);
      patchInvestmentCaseTabRegion(historyRetryKey, "history");
      return;
    }
    var caseTraceRetry = event.target.closest && event.target.closest("[data-investment-case-trace-retry]");
    if (caseTraceRetry && app.contains(caseTraceRetry)) {
      event.preventDefault();
      var traceRetryKey = caseTraceRetry.getAttribute("data-investment-case-trace-retry");
      loadInvestmentCaseTrace(traceRetryKey, true);
      patchInvestmentCaseTabRegion(traceRetryKey, "trace");
      return;
    }
    var notificationDetailTab = event.target.closest && event.target.closest("[data-notification-detail-tab]");
    if (notificationDetailTab && app.contains(notificationDetailTab)) {
      event.preventDefault();
      var notificationJobId = String(notificationDetailTab.getAttribute("data-notification-job-id") || "");
      var notificationTabId = String(notificationDetailTab.getAttribute("data-notification-detail-tab") || "summary");
      if (!notificationJobId) return;
      notificationsState.notificationJobDetailTabs[notificationJobId] = notificationTabId;
      render({ transition: "section" });
      loadNotificationJobDetailSection(notificationJobId, notificationTabId, false);
      return;
    }
    var caseTab = event.target.closest && event.target.closest("[data-investment-case-tab]");
    if (caseTab && app.contains(caseTab)) {
      event.preventDefault();
      var caseKey = String(caseTab.getAttribute("data-investment-case-key") || "");
      var caseTabId = String(caseTab.getAttribute("data-investment-case-tab") || "summary");
      if (!caseKey) return;
      if (caseTabId === "trace" && !investmentCaseOperatorAccess()) caseTabId = "summary";
      decisionsState.investmentCaseDetailTabs[caseKey] = caseTabId;
      var sameCase = navigationState.workDetailLayer
        && ["investment-case", "investment-flow"].indexOf(navigationState.workDetailLayer.type) >= 0
        && navigationState.workDetailLayer.key === caseKey;
      if (caseTabId === "history") loadInvestmentCaseHistory(caseKey, false);
      if (caseTabId === "trace") loadInvestmentCaseTrace(caseKey, false);
      if (sameCase) {
        if (!patchInvestmentCaseTabRegion(caseKey, caseTabId)) render();
      } else {
        openWorkDetailLayer("investment-case", caseKey);
      }
      return;
    }
    var detailButton = event.target.closest && event.target.closest("[data-work-detail]");
    if (detailButton && app.contains(detailButton)) {
      event.preventDefault();
      var detailType = detailButton.getAttribute("data-work-detail");
      var detailKey = detailButton.getAttribute("data-work-detail-key") || "";
      var ontologyCatalogSection = String(detailButton.getAttribute("data-ontology-catalog-section") || "");
      if (ontologyCatalogSection) ontologyState.activeOntologyCatalogTab = ontologyCatalogSection;
      if (detailType === "notification-job") {
        var unreadJob = notificationJobByKey(detailKey);
        if (unreadJob && !unreadJob.readAt) updateNotificationReceipt(detailKey, { read: true });
      }
      openWorkDetailLayer(detailType, detailKey);
      return;
    }
    var receiptButton = event.target.closest && event.target.closest("[data-notification-receipt]");
    if (receiptButton && app.contains(receiptButton)) {
      event.preventDefault();
      var receiptField = receiptButton.getAttribute("data-notification-receipt");
      var receiptJobId = receiptButton.getAttribute("data-notification-job-id") || "";
      var receiptValue = receiptButton.getAttribute("data-notification-receipt-value") === "true";
      if (receiptField && receiptJobId) {
        var receiptChange = {};
        receiptChange[receiptField] = receiptValue;
        updateNotificationReceipt(receiptJobId, receiptChange);
      }
      return;
    }
    var feedbackButton = event.target.closest && event.target.closest("[data-notification-feedback]");
    if (feedbackButton && app.contains(feedbackButton)) {
      event.preventDefault();
      var feedbackJobId = feedbackButton.getAttribute("data-notification-job-id") || "";
      if (feedbackJobId) {
        updateNotificationReceipt(feedbackJobId, {
          usefulness: feedbackButton.getAttribute("data-notification-usefulness") || "",
          feedbackReason: feedbackButton.getAttribute("data-notification-feedback-reason") || ""
        });
      }
      return;
    }
    var markAllRead = event.target.closest && event.target.closest('[data-action="mark-all-notifications-read"]');
    if (markAllRead && app.contains(markAllRead)) {
      event.preventDefault();
      markAllNotificationsRead();
      return;
    }
    var closeButton = event.target.closest && event.target.closest("[data-work-detail-close]");
    if (closeButton && app.contains(closeButton)) {
      if (closeButton.classList.contains("work-detail-backdrop") && event.target !== closeButton) return;
      closeWorkDetailLayer();
      return;
    }
    var pageButton = event.target.closest && event.target.closest("[data-console-page]");
    if (pageButton && app.contains(pageButton)) {
      var key = pageButton.getAttribute("data-console-page") || "";
      var value = Math.max(1, Number(pageButton.getAttribute("data-console-page-value") || 1));
      if (!key) return;
      if (!shellState.consolePages) shellState.consolePages = {};
      shellState.consolePages[key] = value;
      render();
      return;
    }
    var notificationPage = event.target.closest && event.target.closest("[data-notification-job-page]");
    if (notificationPage && app.contains(notificationPage)) {
      var nextPage = Math.max(1, Number(notificationPage.getAttribute("data-notification-job-page") || 1));
      notificationsState.notificationJobsOffset = (nextPage - 1) * Math.max(1, Number(notificationsState.notificationJobsPageSize || 20));
      notificationsState.notificationJobsCursor = notificationPage.getAttribute("data-notification-job-cursor") || "";
      loadNotificationJobs();
      return;
    }
    var commandButton = event.target.closest && event.target.closest('[data-action="command-palette"]');
    if (commandButton && app.contains(commandButton)) {
      event.preventDefault();
      openCommandPalette(commandButton.getAttribute("data-command-palette-mode"));
    }
  });
  app.addEventListener("input", function (event) {
    var commandInput = event.target && event.target.closest && event.target.closest("[data-command-palette-input]");
    if (commandInput && app.contains(commandInput)) {
      navigationState.commandPaletteQuery = commandInput.value || "";
      render();
      return;
    }
    var search = event.target && event.target.closest && event.target.closest("[data-console-market-search]");
    if (search && app.contains(search)) {
      window.clearTimeout(marketSearchTimer);
      marketSearchTimer = window.setTimeout(function () {
        marketState.consoleMarketSearch = search.value || "";
        shellState.consolePages.market = 1;
        render();
      }, 180);
      return;
    }
    var decisionSearch = event.target && event.target.closest && event.target.closest("[data-console-decision-search]");
    if (decisionSearch && app.contains(decisionSearch)) {
      window.clearTimeout(marketSearchTimer);
      marketSearchTimer = window.setTimeout(function () {
        decisionsState.consoleDecisionSearch = decisionSearch.value || "";
        shellState.consolePages.decision = 1;
        render();
      }, 180);
    }
  });
  app.addEventListener("change", function (event) {
    var scope = event.target && event.target.closest && event.target.closest("[data-console-market-scope]");
    if (scope && app.contains(scope)) {
      marketState.consoleMarketScope = scope.value || "all";
      shellState.consolePages.market = 1;
      render();
      return;
    }
    var decisionFilter = event.target && event.target.closest && event.target.closest("[data-console-decision-filter]");
    if (decisionFilter && app.contains(decisionFilter)) {
      var decisionFilterName = decisionFilter.getAttribute("data-console-decision-filter");
      if (decisionFilterName === "scope") decisionsState.consoleDecisionScope = decisionFilter.value || "all";
      if (decisionFilterName === "action") decisionsState.consoleDecisionAction = decisionFilter.value || "all";
      if (decisionFilterName === "quality") decisionsState.consoleDecisionQuality = decisionFilter.value || "all";
      if (decisionFilterName === "status") decisionsState.consoleDecisionStatus = decisionFilter.value || "all";
      shellState.consolePages.decision = 1;
      render();
    }
  });
  app.addEventListener("submit", function (event) {
    var form = event.target.closest && event.target.closest("[data-console-market-form]");
    if (form && app.contains(form)) {
      event.preventDefault();
      var search = form.querySelector("[data-console-market-search]");
      var scope = form.querySelector("[data-console-market-scope]");
      marketState.consoleMarketSearch = search ? search.value : "";
      marketState.consoleMarketScope = scope ? scope.value : "all";
      shellState.consolePages.market = 1;
      render();
      return;
    }
    var decisionForm = event.target.closest && event.target.closest("[data-console-decision-form]");
    if (decisionForm && app.contains(decisionForm)) {
      event.preventDefault();
      var decisionInput = decisionForm.querySelector("[data-console-decision-search]");
      decisionsState.consoleDecisionSearch = decisionInput ? decisionInput.value : "";
      shellState.consolePages.decision = 1;
      render();
    }
  });
}

export { bindDelegatedConsoleActions };
