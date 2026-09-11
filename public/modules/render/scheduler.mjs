import { loadServiceAccounts } from "../accounts/commands.mjs";
import { loadInvestmentCalendar, loadInvestmentCalendarCandidates } from "../calendar/commands.mjs";
import { loadInvestmentFlow, loadInvestmentFlowDetail, loadInvestmentModel } from "../decisions/requests.mjs";
import { loadHypothesisDevelopment, loadOntologyExperiments } from "../experiments/requests.mjs";
import { loadHypothesisWorkspace } from "../hypotheses/workspace.mjs";
import { destroyInstrumentTimelineChart, ensureInstrumentEventGroupTimeline, initInstrumentTimelineChart, instrumentTimelineCacheKey, instrumentTimelineRange, instrumentValuationCacheKey, instrumentWorkspaceTab, loadInstrumentTimeline, loadInstrumentValuation } from "../instruments/timeline.mjs";
import { loadSymbolUniverse, loadSymbolUniverseRefreshStatus } from "../instruments/universe.mjs";
import { loadMarketReadModel } from "../market/requests.mjs";
import { syncAppNavScrollState, syncTopbarScrollState } from "../navigation/chrome.mjs";
import { activeOverlayDialog, focusWorkDetailLayer } from "../navigation/detail.mjs";
import { bindMobileInfiniteScroll, disconnectMobileInfiniteScroll } from "../navigation/infinite-list.mjs";
import { layoutLifetime } from "../navigation/lifecycle.mjs";
import { overlayScrollPositionCell } from "../navigation/overlay-runtime.mjs";
import { normalizeMarketWorkspaceMode } from "../navigation/routes.mjs";
import { bindPageScrollMemory, rememberRenderedInteractiveState, rememberRenderedPageScrollPosition, restoreRenderedDisclosureState, restoreRenderedInteractiveStateAfterLayout, restoreRenderedPageScrollPositionAfterLayout } from "../navigation/scroll.mjs";
import { restoreTabBarPosition } from "../navigation/tab-strip.mjs";
import { pendingTabTransitionCell } from "../navigation/transition-runtime.mjs";
import { loadNotificationJobs, loadNotificationRules, loadNotificationTemplates } from "../notifications/requests.mjs";
import { destroyOntologyCytoscapeGraphs, initOntologyCytoscapeGraphs } from "../ontology/graphs.mjs";
import { activeOntologyAccountId, loadOntologyCatalogSection, loadOntologyCatalogSummary, loadOntologyDiagnostics, loadOntologyInferenceLedger, loadOntologyRulebox, loadOntologyStrategyDetail, shouldLoadHypothesisWorkspace, shouldLoadOntologyInferenceLedger, shouldLoadOntologyStrategyDetail, shouldLoadStrategyProposals, snapshotHasFullOntologyDetail, strategyProposalsNeedLoad } from "../ontology/requests.mjs";
import { loadOperationsHealth, operationsHealthIsStale } from "../operations/requests.mjs";
import { loadDashboardSummary } from "../overview/requests.mjs";
import { loadPortfolioInterpretation, loadPortfolioReadModel, portfolioReadModelBusy } from "../portfolio/requests.mjs";
import { loadStrategyProposals } from "../proposals/requests.mjs";
import { patchStableDashboardMarkup } from "./reconcile.mjs";
import { dashboardRegionReplacementOccurredCell, renderQueuedDuringSuppressionCell, renderSuppressionDepthCell } from "./runtime.mjs";
import { loadResearchEvidence } from "../research/requests.mjs";
import { loadInvestmentLanguage } from "../settings/language.mjs";
import { applyAppTheme } from "../settings/preferences.mjs";
import { loadServerSettings } from "../settings/requests.mjs";
import { bindActions } from "../shell/actions.mjs";
import { bindAutoGrowingTextareas } from "../shell/forms.mjs";
import { syncAppDocumentMetadata } from "../shell/install.mjs";
import { renderDashboard, renderError, renderLoading } from "../shell/layout.mjs";
import { decorateRenderedBusyControls, syncNetworkActivityDom } from "../shell/network-activity.mjs";
import { app } from "../shell/root.mjs";
import { accountsState } from "../state/accounts.mjs";
import { calendarState } from "../state/calendar.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { experimentsState } from "../state/experiments.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";
import { instrumentsState } from "../state/instruments.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { operationsState } from "../state/operations.mjs";
import { portfolioState } from "../state/portfolio.mjs";
import { proposalsState } from "../state/proposals.mjs";
import { researchState } from "../state/research.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";
import { universeState } from "../state/universe.mjs";

var scheduledRenderFrame = 0;

const pendingRenderTransitionCell = { value: "" };

function overlayPageStateOpen() {
  return Boolean(
    navigationState.commandPaletteOpen
    || accountsState.watchlistAccountPickerSymbol
    || navigationState.workDetailLayer
    || calendarState.calendarEntryModalOpen
    || calendarState.investmentCalendarCandidateConfirmation
    || notificationsState.notificationTemplateEditorOpen
    || notificationsState.notificationPolicyEditorOpen
    || ontologyState.expandedOntologyGraphId
    || navigationState.monitoringDetail
  );
}

function syncOverlayPageState(preferredState) {
  var overlayOpen = typeof preferredState === "boolean" ? preferredState : overlayPageStateOpen();
  document.documentElement.classList.toggle("oa-overlay-open", overlayOpen);
  document.body.classList.toggle("oa-overlay-open", overlayOpen);
}

function syncRenderedOverlayPageState() {
  var overlayOpen = Boolean(activeOverlayDialog());
  syncOverlayPageState(overlayOpen);
  return overlayOpen;
}

function reducedMotionPreferred() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

var activeViewTransition = null;
var renderRequestGeneration = 0;

function render(options) {
  options = options || {};
  renderRequestGeneration++;
  if (options.transition) pendingRenderTransitionCell.value = String(options.transition);
  if (renderSuppressionDepthCell.value > 0) {
    renderQueuedDuringSuppressionCell.value = true;
    return;
  }
  if (scheduledRenderFrame) return;
  var run = function () {
    scheduledRenderFrame = 0;
    var generation = renderRequestGeneration;
    if (activeViewTransition && activeViewTransition.skipTransition) activeViewTransition.skipTransition();
    activeViewTransition = null;
    var transitionKind = pendingRenderTransitionCell.value;
    pendingRenderTransitionCell.value = "";
    var perform = function () { if (generation === renderRequestGeneration) renderNow(); };
    if (!transitionKind || transitionKind === "section" || reducedMotionPreferred()) {
      perform();
      return;
    }
    document.documentElement.setAttribute("data-app-transition", transitionKind);
    var clearTransitionState = function () {
      if (generation === renderRequestGeneration && document.documentElement.getAttribute("data-app-transition") === transitionKind) {
        document.documentElement.removeAttribute("data-app-transition");
      }
    };
    if (typeof document.startViewTransition !== "function") {
      perform();
      window.setTimeout(clearTransitionState, 240);
      return;
    }
    try {
      var transition = document.startViewTransition(perform);
      activeViewTransition = transition;
      Promise.resolve(transition.finished).catch(function () { return null; }).finally(clearTransitionState);
    } catch (error) {
      perform();
      window.setTimeout(clearTransitionState, 240);
    }
  };
  scheduledRenderFrame = window.requestAnimationFrame ? window.requestAnimationFrame(run) : setTimeout(run, 0);
}

function renderNow() {
  layoutLifetime.invalidate();
  var runtimePerformance = window.OrbitWebRuntime;
  var renderMetric = runtimePerformance ? runtimePerformance.begin("render", {
    tab: navigationState.activeTab || "overview",
    detail: String((navigationState.workDetailLayer || {}).type || "")
  }) : "";
  if (renderSuppressionDepthCell.value > 0) {
    renderQueuedDuringSuppressionCell.value = true;
    if (renderMetric) runtimePerformance.end(renderMetric, { suppressed: true });
    return;
  }
  applyAppTheme();
  syncAppDocumentMetadata();
  var overlayWillBeOpen = overlayPageStateOpen();
  var overlayWasOpen = document.documentElement.classList.contains("oa-overlay-open");
  var renderedInteractiveState = rememberRenderedInteractiveState();
  var renderedScrollPosition = overlayWasOpen && overlayScrollPositionCell.value
    ? overlayScrollPositionCell.value
    : rememberRenderedPageScrollPosition();
  if (!overlayWasOpen && overlayWillBeOpen && renderedScrollPosition) {
    overlayScrollPositionCell.value = renderedScrollPosition;
  } else if (overlayWasOpen && !overlayWillBeOpen && overlayScrollPositionCell.value) {
    renderedScrollPosition = overlayScrollPositionCell.value;
  }
  if (shellState.loading && !shellState.snapshot) {
    syncOverlayPageState(false);
    disconnectMobileInfiniteScroll();
    destroyInstrumentTimelineChart();
    destroyOntologyCytoscapeGraphs();
    app.innerHTML = renderLoading();
    syncNetworkActivityDom();
    if (renderMetric) runtimePerformance.end(renderMetric, { mode: "loading" });
    return;
  }
  if (!shellState.snapshot) {
    syncOverlayPageState(false);
    disconnectMobileInfiniteScroll();
    destroyInstrumentTimelineChart();
    destroyOntologyCytoscapeGraphs();
    app.innerHTML = renderError();
    bindAutoGrowingTextareas(app);
    bindActions();
    syncNetworkActivityDom();
    decorateRenderedBusyControls();
    if (renderMetric) runtimePerformance.end(renderMetric, { mode: "error" });
    return;
  }
  var markupMetric = runtimePerformance ? runtimePerformance.begin("render-markup", { tab: navigationState.activeTab || "overview" }) : "";
  var dashboardMarkup = renderDashboard(shellState.snapshot);
  if (markupMetric) runtimePerformance.end(markupMetric, { bytes: dashboardMarkup.length });
  var patchMetric = runtimePerformance ? runtimePerformance.begin("render-dom-patch", { tab: navigationState.activeTab || "overview" }) : "";
  var patchedDashboard = patchStableDashboardMarkup(dashboardMarkup);
  if (patchMetric) runtimePerformance.end(patchMetric, { patched: patchedDashboard });
  if (!patchedDashboard) {
    destroyOntologyCytoscapeGraphs();
    app.innerHTML = dashboardMarkup;
    bindAutoGrowingTextareas(app);
    bindActions();
    initOntologyCytoscapeGraphs();
    restoreTabBarPosition();
    bindPageScrollMemory();
  } else if (dashboardRegionReplacementOccurredCell.value) {
    initOntologyCytoscapeGraphs();
    bindPageScrollMemory();
  }
  syncNetworkActivityDom();
  decorateRenderedBusyControls();
  restoreRenderedDisclosureState(renderedInteractiveState);
  if (!patchedDashboard || dashboardRegionReplacementOccurredCell.value) {
    restoreRenderedPageScrollPositionAfterLayout(renderedScrollPosition);
    restoreRenderedInteractiveStateAfterLayout(renderedInteractiveState);
  }
  var overlayRenderedOpen = syncRenderedOverlayPageState();
  if (!overlayRenderedOpen) overlayScrollPositionCell.value = null;
  syncAppNavScrollState();
  syncTopbarScrollState();
  bindMobileInfiniteScroll();
  focusWorkDetailLayer();
  initInstrumentTimelineChart();
  if (pendingTabTransitionCell.value) {
    pendingTabTransitionCell.value = false;
    window.setTimeout(function () {
      var shell = app.querySelector(".console-shell.is-tab-transitioning");
      if (shell) shell.classList.remove("is-tab-transitioning");
    }, 220);
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "market-instrument" && navigationState.workDetailLayer.key) {
    var activeInstrumentTab = instrumentWorkspaceTab(navigationState.workDetailLayer.key);
    if (activeInstrumentTab === "valuation") {
      var valuationKey = instrumentValuationCacheKey(navigationState.workDetailLayer.key);
      if (!instrumentsState.instrumentValuations[valuationKey]
        && !instrumentsState.instrumentValuationLoading[valuationKey]
        && !instrumentsState.instrumentValuationErrors[valuationKey]) {
        loadInstrumentValuation(navigationState.workDetailLayer.key, false);
      }
    } else if (["chart", "decision", "timeline"].indexOf(activeInstrumentTab) >= 0) {
      var timelineKey = instrumentTimelineCacheKey(navigationState.workDetailLayer.key, instrumentTimelineRange(navigationState.workDetailLayer.key));
      if (!instrumentsState.instrumentTimelines[timelineKey] && !instrumentsState.instrumentTimelineLoading[timelineKey]) {
        loadInstrumentTimeline(navigationState.workDetailLayer.key, false);
      }
    }
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "instrument-event-group" && navigationState.workDetailLayer.key) {
    ensureInstrumentEventGroupTimeline(navigationState.workDetailLayer.key);
  }
  var notificationDetailNeedsEvidence = navigationState.workDetailLayer && navigationState.workDetailLayer.type === "notification-job";
  if ((navigationState.activeTab === "feed" || notificationDetailNeedsEvidence) && !researchState.researchEvidence && !researchState.researchEvidenceLoading) {
    loadResearchEvidence(false);
  }
  if (navigationState.activeTab === "overview" && !shellState.dashboardSummary && !shellState.dashboardSummaryLoading) {
    loadDashboardSummary(false);
  }
  if (navigationState.activeTab === "portfolio" && !portfolioState.portfolioReadModels[portfolioState.activePortfolioView] && !portfolioReadModelBusy(portfolioState.activePortfolioView)) {
    loadPortfolioReadModel(portfolioState.activePortfolioView, false);
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "portfolio-interpretation" && !portfolioState.portfolioInterpretation && !portfolioState.portfolioInterpretationLoading) {
    loadPortfolioInterpretation(false);
  }
  if (navigationState.activeTab === "feed" && !marketState.marketReadModel && !marketState.marketReadModelLoading) {
    loadMarketReadModel(false);
  }
  if (navigationState.activeTab === "operations" && !operationsState.operationsHealthLoading && operationsHealthIsStale(120000)) {
    loadOperationsHealth(Boolean(operationsState.operationsHealth));
  }
  if (navigationState.activeTab === "feed" && !accountsState.serviceAccountsLoaded && !accountsState.serviceAccountsLoading) {
    loadServiceAccounts();
  }
  if (navigationState.activeTab === "feed" && normalizeMarketWorkspaceMode(marketState.marketWorkspaceMode) === "universe" && !universeState.symbolUniverseLoaded && !universeState.symbolUniverseLoading) {
    loadSymbolUniverse();
  }
  if (!universeState.symbolUniverseRefreshStatusLoaded && !universeState.symbolUniverseRefreshStatusLoading) {
    loadSymbolUniverseRefreshStatus(false);
  }
  if ((navigationState.activeTab === "overview" || navigationState.activeTab === "calendar") && !calendarState.investmentCalendar && !calendarState.investmentCalendarLoading) {
    loadInvestmentCalendar(false);
  }
  if (navigationState.activeTab === "notifications" && !notificationsState.notificationJobsLoaded && !notificationsState.notificationJobsLoading) {
    loadNotificationJobs();
  }
  if (navigationState.activeTab === "settings" && !settingsState.serverSettingsLoaded && !settingsState.serverSettingsLoading) {
    loadServerSettings();
  }
  if (navigationState.activeTab === "settings" && !accountsState.serviceAccountsLoaded && !accountsState.serviceAccountsLoading) {
    loadServiceAccounts();
  }
  if (shouldLoadStrategyProposals() && strategyProposalsNeedLoad() && !proposalsState.strategyProposalsLoading) {
    loadStrategyProposals(false);
  }
  if ((navigationState.activeTab === "modeling" || navigationState.activeTab === "experiments") && (!decisionsState.investmentFlowLoaded || decisionsState.investmentFlowAccountId !== activeOntologyAccountId()) && !decisionsState.investmentFlowLoading) {
    loadInvestmentFlow(false);
  }
  if (navigationState.activeTab === "modeling" && !settingsState.investmentLanguageLoaded && !settingsState.investmentLanguageLoading) {
    loadInvestmentLanguage(false);
  }
  if (shouldLoadHypothesisWorkspace() && !hypothesesState.hypothesisWorkspaceLoaded && !hypothesesState.hypothesisWorkspaceLoading) {
    loadHypothesisWorkspace(false);
  }
  if (shouldLoadOntologyInferenceLedger() && !ontologyState.ontologyInferenceLedgerLoaded && !ontologyState.ontologyInferenceLedgerLoading) {
    loadOntologyInferenceLedger(false);
  }
  if (shouldLoadOntologyStrategyDetail() && !snapshotHasFullOntologyDetail(shellState.snapshot) && !ontologyState.ontologyStrategyDetailLoading) {
    loadOntologyStrategyDetail(false);
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "strategy-rulebox-editor") {
    if (!ontologyState.ontologyRuleboxLoaded && !ontologyState.ontologyRuleboxLoading) loadOntologyRulebox(false);
    if (!ontologyState.ontologyDiagnostics && !ontologyState.ontologyDiagnosticsLoading) loadOntologyDiagnostics(false);
  }
  if (navigationState.workDetailLayer && ["investment-model-overview", "investment-model-management", "strategy-model-policy-editor"].indexOf(navigationState.workDetailLayer.type) >= 0) {
    if (!decisionsState.investmentModelLoaded && !decisionsState.investmentModelLoading) loadInvestmentModel(false);
    if (navigationState.workDetailLayer.type === "investment-model-management") {
      if (!experimentsState.ontologyExperimentsLoaded && !experimentsState.ontologyExperimentsLoading) loadOntologyExperiments(false);
      if (!hypothesesState.hypothesisDevelopmentLoaded && !hypothesesState.hypothesisDevelopmentLoading) loadHypothesisDevelopment(false);
    }
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "strategy-graphs-board") {
    if (!ontologyState.ontologyCatalogSummaryLoaded && !ontologyState.ontologyCatalogSummaryLoading) loadOntologyCatalogSummary(false);
    if (ontologyState.activeOntologyCatalogTab !== "overview"
        && !ontologyState.ontologyCatalogPages[ontologyState.activeOntologyCatalogTab]
        && !ontologyState.ontologyCatalogLoading[ontologyState.activeOntologyCatalogTab]) {
      loadOntologyCatalogSection(ontologyState.activeOntologyCatalogTab, false);
    }
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "settings-investment-language") {
    if (!settingsState.investmentLanguageLoaded && !settingsState.investmentLanguageLoading) loadInvestmentLanguage(false);
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "calendar-candidate-board") {
    if (!calendarState.investmentCalendarCandidates && !calendarState.investmentCalendarCandidatesLoading) loadInvestmentCalendarCandidates(false);
  }
  if (navigationState.workDetailLayer && ["notification-policy-board", "notification-diagnostics-board"].indexOf(navigationState.workDetailLayer.type) >= 0) {
    if (!notificationsState.notificationRulesLoaded && !notificationsState.notificationRulesLoading) loadNotificationRules();
  }
  if (navigationState.workDetailLayer && navigationState.workDetailLayer.type === "notification-templates-board") {
    if (!notificationsState.notificationTemplatesLoaded && !notificationsState.notificationTemplatesLoading) loadNotificationTemplates();
  }
  if (navigationState.workDetailLayer && ["settings-data-sources", "settings-runtime", "settings-ai-runtime", "settings-operations-notifications", "settings-diagnostics"].indexOf(navigationState.workDetailLayer.type) >= 0) {
    if (!settingsState.serverSettingsLoaded && !settingsState.serverSettingsLoading) loadServerSettings();
  }
  if (navigationState.workDetailLayer && ["investment-case", "investment-flow"].indexOf(navigationState.workDetailLayer.type) >= 0 && navigationState.workDetailLayer.key) {
    if (!decisionsState.investmentFlowDetails[navigationState.workDetailLayer.key] && !decisionsState.investmentFlowDetailLoading[navigationState.workDetailLayer.key]) {
      loadInvestmentFlowDetail(navigationState.workDetailLayer.key, false);
    }
  }
  if (renderMetric) runtimePerformance.end(renderMetric, {
    mode: patchedDashboard ? "stable-patch" : "full",
    regionReplacement: Boolean(dashboardRegionReplacementOccurredCell.value),
    nodeCount: app.querySelectorAll("*").length
  });
}

export { pendingRenderTransitionCell, reducedMotionPreferred, render, syncRenderedOverlayPageState };
