import { loadServiceAccounts } from "../accounts/commands.mjs";
import { consoleReadModelAccountId } from "../accounts/identity.mjs";
import { loadInvestmentCalendar } from "../calendar/commands.mjs";
import { loadInvestmentFlow } from "../decisions/requests.mjs";
import { loadHypothesisWorkspace } from "../hypotheses/workspace.mjs";
import { loadSymbolUniverse, loadSymbolUniverseRefreshStatus } from "../instruments/universe.mjs";
import { loadMarketReadModel } from "../market/requests.mjs";
import { normalizeMarketWorkspaceMode, normalizePortfolioView, normalizeTabId } from "./routes.mjs";
import { loadNotificationJobs } from "../notifications/requests.mjs";
import { activeOntologyAccountId, loadOntologyInferenceLedger, loadOntologyStrategyDetail, shouldLoadHypothesisWorkspace, shouldLoadOntologyInferenceLedger, shouldLoadOntologyStrategyDetail, shouldLoadStrategyProposals, snapshotHasFullOntologyDetail, strategyProposalsNeedLoad } from "../ontology/requests.mjs";
import { loadOperationsHealth, operationsHealthIsStale } from "../operations/requests.mjs";
import { loadDashboardSummary } from "../overview/requests.mjs";
import { loadPortfolioReadModel, portfolioReadModelBusy } from "../portfolio/requests.mjs";
import { loadStrategyProposals } from "../proposals/requests.mjs";
import { withSilentRequests } from "../requests/activity-scope.mjs";
import { loadResearchEvidence } from "../research/requests.mjs";
import { loadServerSettings } from "../settings/requests.mjs";
import { snapshotRefreshInProgress } from "../snapshot/requests.mjs";
import { accountsState } from "../state/accounts.mjs";
import { calendarState } from "../state/calendar.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";
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

var tabDataPreloadTimer = null;

var tabDataPreloadIdleHandle = null;

var tabDataPreloadPromise = null;

var tabDataPreloadRequested = false;

var tabDataPreloadForcePending = false;

const tabDataPreloadPrerequisitesReadyCell = { value: false };

var tabDataPreloadLastIdentity = "";

var tabDataPreloadLastAt = 0;


var TAB_DATA_PRELOAD_STALE_MS = 120000;

var TAB_DATA_PRELOAD_MAX_CONCURRENCY = 2;

function primeActiveTabData(tab, force) {
  if (!shellState.snapshot) return;
  var active = normalizeTabId(tab || navigationState.activeTab);
  force = Boolean(force);
  if (!universeState.symbolUniverseRefreshStatusLoaded && !universeState.symbolUniverseRefreshStatusLoading) {
    loadSymbolUniverseRefreshStatus(false);
  }
  if ((active === "overview" || active === "calendar") && (force || !calendarState.investmentCalendar) && !calendarState.investmentCalendarLoading) {
    loadInvestmentCalendar(force);
  }
  if (active === "overview" && (force || !shellState.dashboardSummary) && !shellState.dashboardSummaryLoading) loadDashboardSummary(force);
  if (active === "portfolio") loadPortfolioReadModel(portfolioState.activePortfolioView, force);
  if (active === "feed") {
    if ((force || !marketState.marketReadModel) && !marketState.marketReadModelLoading) loadMarketReadModel(force);
    if ((force || !researchState.researchEvidence) && !researchState.researchEvidenceLoading) loadResearchEvidence(force);
    if (!accountsState.serviceAccountsLoaded && !accountsState.serviceAccountsLoading) loadServiceAccounts();
    if (normalizeMarketWorkspaceMode(marketState.marketWorkspaceMode) === "universe" && !universeState.symbolUniverseLoaded && !universeState.symbolUniverseLoading) {
      loadSymbolUniverse();
    }
  }
  if (active === "notifications") {
    var notificationFirstPageRefresh = force
      && Number(notificationsState.notificationJobsOffset || 0) === 0
      && !notificationsState.notificationJobsCursor;
    if ((!notificationsState.notificationJobsLoaded || notificationFirstPageRefresh) && !notificationsState.notificationJobsLoading) loadNotificationJobs();
  }
  if (active === "settings") {
    if (!settingsState.serverSettingsLoaded && !settingsState.serverSettingsLoading) loadServerSettings();
    if (!accountsState.serviceAccountsLoaded && !accountsState.serviceAccountsLoading) loadServiceAccounts();
  }
  if (active === "operations" && !operationsState.operationsHealthLoading && (force || operationsHealthIsStale(120000))) {
    loadOperationsHealth(force || Boolean(operationsState.operationsHealth));
  }
  if (active === "modeling" || active === "experiments") {
    if ((force || !decisionsState.investmentFlowLoaded || decisionsState.investmentFlowAccountId !== activeOntologyAccountId()) && !decisionsState.investmentFlowLoading) loadInvestmentFlow(force);
    if (shouldLoadStrategyProposals() && (force || strategyProposalsNeedLoad()) && !proposalsState.strategyProposalsLoading) loadStrategyProposals(force);
    if (shouldLoadHypothesisWorkspace() && (force || !hypothesesState.hypothesisWorkspaceLoaded) && !hypothesesState.hypothesisWorkspaceLoading) loadHypothesisWorkspace(force);
    if (shouldLoadOntologyInferenceLedger() && (force || !ontologyState.ontologyInferenceLedgerLoaded) && !ontologyState.ontologyInferenceLedgerLoading) loadOntologyInferenceLedger(force);
    if (shouldLoadOntologyStrategyDetail() && (force || !snapshotHasFullOntologyDetail(shellState.snapshot)) && !ontologyState.ontologyStrategyDetailLoading) loadOntologyStrategyDetail(force);
  }
}

function tabDataPreloadIdentity() {
  var snapshot = shellState.snapshot || {};
  return [
    String(snapshot.generatedAt || "snapshot"),
    String(consoleReadModelAccountId() || "default")
  ].join("|");
}

function tabDataPreloadTasks(force) {
  var tasks = [];
  var add = function (id, needed, loader) {
    if (needed) tasks.push({ id: id, load: loader });
  };
  var portfolioView = normalizePortfolioView(portfolioState.activePortfolioView || "summary");
  var canRefreshNotificationPage = !notificationsState.notificationJobsLoading
    && (!notificationsState.notificationJobsLoaded || (
      force
      && Number(notificationsState.notificationJobsOffset || 0) === 0
      && !notificationsState.notificationJobsCursor
    ));

  add("dashboard-summary", !shellState.dashboardSummaryLoading && (force || !shellState.dashboardSummary), function () {
    return loadDashboardSummary(force);
  });
  add("portfolio-summary", !portfolioReadModelBusy("summary") && (force || !portfolioState.portfolioReadModels.summary), function () {
    return loadPortfolioReadModel("summary", force, { prefetch: true });
  });
  if (portfolioView !== "summary") {
    add("portfolio-" + portfolioView, !portfolioReadModelBusy(portfolioView) && (force || !portfolioState.portfolioReadModels[portfolioView]), function () {
      return loadPortfolioReadModel(portfolioView, force, { prefetch: true });
    });
  }
  add("market-instruments", !marketState.marketReadModelLoading && (force || !marketState.marketReadModel), function () {
    return loadMarketReadModel(force);
  });
  add("investment-calendar", !calendarState.investmentCalendarLoading && (force || !calendarState.investmentCalendar), function () {
    return loadInvestmentCalendar(force);
  });
  add("investment-cases", !decisionsState.investmentFlowLoading && (force || !decisionsState.investmentFlowLoaded || decisionsState.investmentFlowAccountId !== activeOntologyAccountId()), function () {
    if (decisionsState.investmentFlowLoading) return Promise.resolve(decisionsState.investmentFlow);
    return loadInvestmentFlow(force);
  });
  add("research-evidence", !researchState.researchEvidenceLoading && (force || !researchState.researchEvidence), function () {
    return loadResearchEvidence(force);
  });
  add("notification-jobs", canRefreshNotificationPage, function () {
    if (notificationsState.notificationJobsLoading) return Promise.resolve();
    if (notificationsState.notificationJobsLoaded && (Number(notificationsState.notificationJobsOffset || 0) > 0 || notificationsState.notificationJobsCursor)) return Promise.resolve();
    return loadNotificationJobs();
  });
  add("strategy-proposals", !proposalsState.strategyProposalsLoading && (force || !proposalsState.strategyProposalsLoaded), function () {
    if (proposalsState.strategyProposalsLoading) return Promise.resolve(proposalsState.strategyProposals);
    return loadStrategyProposals(force);
  });
  add("operations-health", !operationsState.operationsHealthLoading && (force || operationsHealthIsStale(TAB_DATA_PRELOAD_STALE_MS)), function () {
    return loadOperationsHealth(force || Boolean(operationsState.operationsHealth));
  });
  return tasks;
}

function runTabDataPreloadQueue(tasks) {
  var cursor = 0;
  var workerCount = Math.min(TAB_DATA_PRELOAD_MAX_CONCURRENCY, tasks.length);
  var workers = [];
  var runNext = function () {
    var task = tasks[cursor];
    cursor += 1;
    if (!task) return Promise.resolve();
    var request;
    try {
      request = Promise.resolve(withSilentRequests(task.load));
    } catch (error) {
      request = Promise.reject(error);
    }
    return request
      .catch(function () { return null; })
      .then(runNext);
  };
  for (var index = 0; index < workerCount; index += 1) workers.push(runNext());
  return Promise.all(workers);
}

function startTabDataPreload() {
  if (tabDataPreloadPromise || !shellState.snapshot || !tabDataPreloadPrerequisitesReadyCell.value) return tabDataPreloadPromise || Promise.resolve();
  if (typeof navigator !== "undefined" && navigator.onLine === false) return Promise.resolve();
  if (typeof document !== "undefined" && document.visibilityState === "hidden") return Promise.resolve();
  if (snapshotRefreshInProgress(shellState.readModel)) return Promise.resolve();
  var identity = tabDataPreloadIdentity();
  var identityChanged = Boolean(tabDataPreloadLastIdentity && tabDataPreloadLastIdentity !== identity);
  var stale = Boolean(tabDataPreloadLastAt && Date.now() - tabDataPreloadLastAt >= TAB_DATA_PRELOAD_STALE_MS);
  var force = Boolean(tabDataPreloadForcePending || identityChanged || stale);
  tabDataPreloadForcePending = false;
  tabDataPreloadRequested = false;
  var tasks = tabDataPreloadTasks(force);
  if (!tasks.length) {
    tabDataPreloadLastIdentity = identity;
    tabDataPreloadLastAt = Date.now();
    return Promise.resolve();
  }
  tabDataPreloadPromise = runTabDataPreloadQueue(tasks).finally(function () {
    tabDataPreloadPromise = null;
    tabDataPreloadLastIdentity = identity;
    tabDataPreloadLastAt = Date.now();
    if (tabDataPreloadRequested || tabDataPreloadLastIdentity !== tabDataPreloadIdentity()) {
      scheduleTabDataPreload({ force: tabDataPreloadForcePending, reason: "queued" });
    }
  });
  return tabDataPreloadPromise;
}

function scheduleTabDataPreload(options) {
  options = options || {};
  if (!shellState.snapshot) return;
  tabDataPreloadRequested = true;
  tabDataPreloadForcePending = tabDataPreloadForcePending || Boolean(options.force);
  if (tabDataPreloadPromise || tabDataPreloadTimer || tabDataPreloadIdleHandle) return;
  tabDataPreloadTimer = window.setTimeout(function () {
    tabDataPreloadTimer = null;
    var start = function () {
      tabDataPreloadIdleHandle = null;
      startTabDataPreload();
    };
    if (typeof window.requestIdleCallback === "function") {
      tabDataPreloadIdleHandle = window.requestIdleCallback(start, { timeout: 1200 });
    } else {
      start();
    }
  }, options.immediate ? 0 : 300);
}

export { primeActiveTabData, scheduleTabDataPreload, tabDataPreloadPrerequisitesReadyCell };
