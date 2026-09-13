import { setAppNavHidden } from "./chrome.mjs";
import { loadInformationWorkDetail, restoreWorkDetailFocus } from "./detail.mjs";
import { viewLifetime } from "./lifecycle.mjs";
import { primeActiveTabData, scheduleTabDataPreload } from "./preload.mjs";
import { activePageMode, initialAccountSection, initialExperimentSection, initialFeedSection, initialMarketWorkspaceMode, initialNotificationSection, initialOntologyExperimentId, initialOntologySection, initialOperationsView, initialPageModeForTab, initialPortfolioView, initialSettingsSection, initialStrategySection, initialTab, initialWorkDetailLayer, normalizeTabId, writeTabHistory } from "./routes.mjs";
import { rememberRenderedPageScrollPosition } from "./scroll.mjs";
import { rememberTabBarPosition } from "./tab-strip.mjs";
import { pendingTabTransitionCell } from "./transition-runtime.mjs";
import { loadNotificationJobDetail } from "../notifications/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { accountsState } from "../state/accounts.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { experimentsState } from "../state/experiments.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { operationsState } from "../state/operations.mjs";
import { portfolioState } from "../state/portfolio.mjs";
import { settingsState } from "../state/settings.mjs";

function navigateToTab(tab, options) {
  options = options || {};
  var nextTab = normalizeTabId(tab);
  if (nextTab === navigationState.activeTab) return;
  rememberRenderedPageScrollPosition();
  viewLifetime.invalidate();
  rememberTabBarPosition();
  var priorTab = navigationState.activeTab;
  pendingTabTransitionCell.value = true;
  navigationState.activeTab = nextTab;
  if (nextTab !== "notifications") navigationState.monitoringDetail = null;
  if (nextTab !== "notifications") notificationsState.notificationPolicyEditorOpen = false;
  if (nextTab !== "notifications") notificationsState.notificationTemplateEditorOpen = false;
  navigationState.workDetailLayer = null;
  if (!options.skipPrevious) navigationState.previousTab = priorTab;
  if (!options.skipHistory) writeTabHistory(nextTab, Boolean(options.replace));
  setAppNavHidden(false);
  primeActiveTabData(nextTab);
  scheduleTabDataPreload({ reason: "tab-change" });
  render({ transition: "tab" });
}

function syncTabFromLocation() {
  rememberRenderedPageScrollPosition();
  viewLifetime.invalidate();
  var nextTab = initialTab();
  var nextAccountSection = initialAccountSection();
  var nextSettingsSection = initialSettingsSection();
  var nextNotificationSection = initialNotificationSection();
  var nextStrategySection = initialStrategySection();
  var nextOntologySection = initialOntologySection();
  var nextFeedSection = initialFeedSection();
  var nextMarketWorkspaceMode = initialMarketWorkspaceMode();
  var nextExperimentSection = initialExperimentSection();
  var nextOntologyExperimentId = initialOntologyExperimentId();
  var nextPortfolioView = initialPortfolioView();
  var nextOperationsView = initialOperationsView();
  var nextPageMode = initialPageModeForTab(nextTab);
  var nextWorkDetailLayer = initialWorkDetailLayer();
  var currentDetailType = String((navigationState.workDetailLayer || {}).type || "");
  var currentDetailKey = String((navigationState.workDetailLayer || {}).key || "");
  var nextDetailType = String((nextWorkDetailLayer || {}).type || "");
  var nextDetailKey = String((nextWorkDetailLayer || {}).key || "");
  var detailChanged = currentDetailType !== nextDetailType || currentDetailKey !== nextDetailKey;
  var accountSectionChanged = nextAccountSection !== accountsState.activeAccountSection;
  var settingsSectionChanged = nextSettingsSection !== settingsState.activeSettingsSection;
  var sectionChanged = nextNotificationSection !== notificationsState.activeNotificationSection;
  var strategySectionChanged = nextStrategySection !== decisionsState.activeStrategySection;
  var ontologySectionChanged = nextOntologySection !== ontologyState.activeOntologySection;
  var feedSectionChanged = nextFeedSection !== marketState.activeFeedSection;
  var marketWorkspaceChanged = nextMarketWorkspaceMode !== marketState.marketWorkspaceMode;
  var experimentSectionChanged = nextExperimentSection !== experimentsState.activeExperimentSection;
  var ontologyExperimentChanged = nextOntologyExperimentId !== experimentsState.activeOntologyExperimentId;
  var portfolioViewChanged = nextPortfolioView !== portfolioState.activePortfolioView;
  var operationsViewChanged = nextOperationsView !== operationsState.activeOperationsView;
  var pageModeChanged = activePageMode(nextTab) !== nextPageMode;
  if (!navigationState.pageViewModes) navigationState.pageViewModes = {};
  navigationState.pageViewModes[nextTab] = nextPageMode;
  accountsState.activeAccountSection = nextAccountSection;
  settingsState.activeSettingsSection = nextSettingsSection;
  notificationsState.activeNotificationSection = nextNotificationSection;
  decisionsState.activeStrategySection = nextStrategySection;
  ontologyState.activeOntologySection = nextOntologySection;
  marketState.activeFeedSection = nextFeedSection;
  marketState.marketWorkspaceMode = nextMarketWorkspaceMode;
  experimentsState.activeExperimentSection = nextExperimentSection;
  experimentsState.activeOntologyExperimentId = nextOntologyExperimentId;
  portfolioState.activePortfolioView = nextPortfolioView;
  operationsState.activeOperationsView = nextOperationsView;
  navigationState.workDetailLayer = nextWorkDetailLayer;
  if (detailChanged) loadInformationWorkDetail(nextWorkDetailLayer);
  if (detailChanged && nextDetailType === "notification-job" && nextDetailKey) {
    loadNotificationJobDetail(nextDetailKey);
  }
  if (nextTab !== "notifications" || sectionChanged) notificationsState.notificationPolicyEditorOpen = false;
  if (nextTab !== "notifications" || sectionChanged) notificationsState.notificationTemplateEditorOpen = false;
  if (nextTab === navigationState.activeTab) {
    var shouldRender = detailChanged
      || (accountSectionChanged && nextTab === "accounts")
      || (settingsSectionChanged && nextTab === "settings")
      || (sectionChanged && nextTab === "notifications")
      || (strategySectionChanged && nextTab === "modeling")
      || (ontologySectionChanged && nextTab === "ontology")
      || (feedSectionChanged && nextTab === "feed")
      || (marketWorkspaceChanged && nextTab === "feed")
      || ((experimentSectionChanged || ontologyExperimentChanged) && nextTab === "experiments")
      || (portfolioViewChanged && nextTab === "portfolio")
      || (operationsViewChanged && nextTab === "operations")
      || pageModeChanged;
    if (shouldRender) render({ transition: detailChanged ? (nextWorkDetailLayer ? "detail-open" : "detail-close") : "section" });
    if (detailChanged && !nextWorkDetailLayer) restoreWorkDetailFocus();
    return;
  }
  rememberTabBarPosition();
  navigationState.previousTab = navigationState.activeTab;
  pendingTabTransitionCell.value = true;
  navigationState.activeTab = nextTab;
  if (nextTab !== "notifications") navigationState.monitoringDetail = null;
  primeActiveTabData(nextTab);
  scheduleTabDataPreload({ reason: "history-navigation" });
  render({ transition: "tab" });
  if (detailChanged && !nextWorkDetailLayer) restoreWorkDetailFocus();
}

export { navigateToTab, syncTabFromLocation };
