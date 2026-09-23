import { bindAccountsControls } from "../accounts/actions.mjs";
import { createNewAccountDraft } from "../accounts/commands.mjs";
import { bindCalendarControls } from "../calendar/actions.mjs";
import { approveInvestmentCalendarCandidate, defaultInvestmentCalendarDraft, discoverInvestmentCalendarEvents, loadInvestmentCalendar, loadInvestmentCalendarCandidates, recommendInvestmentCalendarCandidates, rejectInvestmentCalendarCandidate, runInvestmentCalendarReminders, syncOfficialInvestmentCalendar } from "../calendar/commands.mjs";
import { bindDecisionsControls } from "../decisions/actions.mjs";
import { activateOntologyExperiment, applyOntologyExperiment, applySelectedOntologyExperimentRecommendations, loadOntologyExperiments, pauseOntologyExperiment, runOntologyExperiment, runOntologyExperimentsOnce, suggestOntologyExperiments } from "../experiments/requests.mjs";
import { applicableOntologyExperimentRecommendationIds, setOntologyExperimentRecommendationSelection, toggleOntologyExperimentRecommendation } from "../experiments/workspace.mjs";
import { bindHypothesesControls } from "../hypotheses/actions.mjs";
import { loadHypothesisWorkspace, proposeHypothesisQualityReview, recordHypothesisPolicyBaseline, runHypothesisReplay } from "../hypotheses/workspace.mjs";
import { bindInstrumentsControls } from "../instruments/actions.mjs";
import { addVisibleSymbolsToPreferredWatchlist } from "../instruments/universe-view.mjs";
import { loadSymbolUniverse } from "../instruments/universe.mjs";
import { primeActiveTabData } from "../navigation/preload.mjs";
import { navigateToTab } from "../navigation/router.mjs";
import { activePageMode, normalizeExperimentSection, normalizeMarketWorkspaceMode, normalizeOperationsView, normalizePageMode, normalizePortfolioView, normalizeTabId, setPageViewMode, writeAccountSectionHistory, writeConsoleWorkspaceViewHistory, writeExperimentSectionHistory, writeFeedSectionHistory, writeMarketWorkspaceHistory, writeNotificationSectionHistory, writePageModeHistory, writeStrategySectionHistory } from "../navigation/routes.mjs";
import { rememberRenderedPageScrollPosition } from "../navigation/scroll.mjs";
import { bindTabNavigation } from "../navigation/tab-strip.mjs";
import { bindNotificationsControls } from "../notifications/actions.mjs";
import { resetNotificationRule, saveNotificationRule } from "../notifications/policy.mjs";
import { loadNotificationJobs, resetNotificationJobsPaging } from "../notifications/requests.mjs";
import { alertRuleGroups } from "../notifications/workspace.mjs";
import { bindOntologyControls } from "../ontology/actions.mjs";
import { appendRuleboxCandidate, loadOntologyAudit, loadOntologyCatalogSection, loadOntologyCatalogSummary, loadOntologyDiagnostics, loadOntologyInferenceLedger, loadOntologyReasoningStatus, loadOntologyRulebox, proposeOntologyRuleCandidates, runOntologyRulebox, saveOntologyRulebox, seedOntologyGraph } from "../ontology/requests.mjs";
import { loadOperationsHealth } from "../operations/requests.mjs";
import { loadPortfolioLifecycle, reviewPortfolioActionPlan } from "../portfolio/lifecycle.mjs";
import { loadPortfolioReadModel } from "../portfolio/requests.mjs";
import { bindProposalsControls } from "../proposals/actions.mjs";
import { loadStrategyProposals } from "../proposals/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { bindResearchControls } from "../research/actions.mjs";
import { loadResearchEvidence, revalidateResearchEvidence } from "../research/requests.mjs";
import { bindSettingsControls } from "../settings/actions.mjs";
import { loadInvestmentLanguage, previewInvestmentLanguage } from "../settings/language.mjs";
import { saveSettingsToServer } from "../settings/requests.mjs";
import { requestShareTunnelRotation } from "../settings/share.mjs";
import { copyTextToClipboard, showSnackbar } from "./snackbar.mjs";
import { load } from "../snapshot/requests.mjs";
import { accountsState } from "../state/accounts.mjs";
import { calendarState } from "../state/calendar.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { experimentsState } from "../state/experiments.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { operationsState } from "../state/operations.mjs";
import { portfolioState } from "../state/portfolio.mjs";
import { proposalsState } from "../state/proposals.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";
import { universeState } from "../state/universe.mjs";

function bindActions(app) {
app = app || document.getElementById("app");
if (!app) return;
bindShellControls(app);
bindOntologyControls(app);
bindDecisionsControls(app);
bindResearchControls(app);
bindNotificationsControls(app);
bindSettingsControls(app);
bindCalendarControls(app);
bindAccountsControls(app);
bindHypothesesControls(app);
bindProposalsControls(app);
bindInstrumentsControls(app);
}

function bindShellControls(app) {
var refresh = app.querySelector('[data-action="refresh"]');
if (refresh) {
    refresh.addEventListener("click", function () {
      if (!shellState.refreshing) load({ refresh: true });
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-tab]")).forEach(bindTabNavigation);
Array.prototype.slice.call(app.querySelectorAll("[data-monitor-instrument-detail]")).forEach(function (row) {
    var openInstrumentDetail = function () {
      var symbol = String(row.getAttribute("data-monitor-instrument-detail") || "").toUpperCase();
      if (!symbol) return;
      navigationState.monitoringDetail = { type: "instrument", symbol: symbol };
      render();
    };
    row.addEventListener("click", openInstrumentDetail);
    row.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openInstrumentDetail();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-monitoring-detail-close]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (button.classList && button.classList.contains("monitoring-detail-backdrop") && event.target !== button) return;
      navigationState.monitoringDetail = null;
      render();
    });
  });
var settingsBack = app.querySelector('[data-action="settings-back"]');
if (settingsBack) {
    settingsBack.addEventListener("click", function () {
      navigateToTab(navigationState.previousTab || "overview", { replace: true, skipPrevious: true });
    });
  }
Array.prototype.slice.call(app.querySelectorAll('[data-action="refresh-research-evidence"]')).forEach(function (refreshEvidence) {
    refreshEvidence.addEventListener("click", function () {
      loadResearchEvidence(true);
    });
  });
Array.prototype.slice.call(app.querySelectorAll('[data-action="revalidate-research-evidence"]')).forEach(function (button) {
    button.addEventListener("click", function () {
      revalidateResearchEvidence();
    });
  });
Array.prototype.slice.call(app.querySelectorAll('[data-action="refresh-investment-calendar"]')).forEach(function (button) {
    button.addEventListener("click", function () {
      Promise.all([loadInvestmentCalendar(true), loadInvestmentCalendarCandidates(true)]);
    });
  });
Array.prototype.slice.call(app.querySelectorAll('[data-action="run-investment-calendar-reminders"]')).forEach(function (button) {
    button.addEventListener("click", function () {
      runInvestmentCalendarReminders();
    });
  });
Array.prototype.slice.call(app.querySelectorAll('[data-action="sync-official-investment-calendar"]')).forEach(function (button) {
    button.addEventListener("click", function () {
      syncOfficialInvestmentCalendar();
    });
  });
Array.prototype.slice.call(app.querySelectorAll('[data-action="discover-investment-calendar"]')).forEach(function (button) {
    button.addEventListener("click", function () {
      discoverInvestmentCalendarEvents();
    });
  });
Array.prototype.slice.call(app.querySelectorAll('[data-action="research-investment-calendar"]')).forEach(function (button) {
    button.addEventListener("click", function () {
      recommendInvestmentCalendarCandidates();
    });
  });
var resetCalendarDraft = app.querySelector('[data-action="reset-investment-calendar-draft"]');
if (resetCalendarDraft) {
    resetCalendarDraft.addEventListener("click", function () {
      calendarState.investmentCalendarDraft = defaultInvestmentCalendarDraft();
      render();
    });
  }
var calendarCandidateList = app.querySelector('[data-console-keyed-list="calendar-candidates"]');
if (calendarCandidateList) {
    calendarCandidateList.addEventListener("click", function (event) {
      var button = event.target && event.target.closest
        ? event.target.closest("[data-calendar-candidate-approve], [data-calendar-candidate-reject]")
        : null;
      if (!button || !calendarCandidateList.contains(button)) return;
      if (button.hasAttribute("data-calendar-candidate-approve")) {
        approveInvestmentCalendarCandidate(button.getAttribute("data-calendar-candidate-approve"));
        return;
      }
      rejectInvestmentCalendarCandidate(button.getAttribute("data-calendar-candidate-reject"));
    });
  }
Array.prototype.slice.call(app.querySelectorAll('[data-action="new-service-account"]')).forEach(function (newServiceAccount) {
    newServiceAccount.addEventListener("click", function () {
      setPageViewMode("accounts", "settings");
      accountsState.editingAccountId = "";
      accountsState.accountDraft = createNewAccountDraft();
      accountsState.accountSaved = false;
      accountsState.serviceAccountsError = "";
      accountsState.activeAccountSection = "identity";
      writeAccountSectionHistory("identity");
      render();
    });
  });
var lifecycleRefresh = app.querySelector("[data-portfolio-lifecycle-refresh]");
if (lifecycleRefresh) {
    lifecycleRefresh.addEventListener("click", function () { loadPortfolioLifecycle(true); });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-action-plan-review]")).forEach(function (button) {
    button.addEventListener("click", function () {
      reviewPortfolioActionPlan(
        button.getAttribute("data-action-plan-id"),
        button.getAttribute("data-action-plan-review")
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-language-term]")).forEach(function (button) {
    button.addEventListener("click", function () {
      decisionsState.activeInvestmentLanguageTermId = button.getAttribute("data-language-term") || "";
      settingsState.investmentLanguagePreview = null;
      render();
    });
  });
var refreshInvestmentLanguage = app.querySelector('[data-action="refresh-investment-language"]');
if (refreshInvestmentLanguage) {
    refreshInvestmentLanguage.addEventListener("click", function () {
      settingsState.investmentLanguageLoaded = false;
      loadInvestmentLanguage(true);
    });
  }
var previewInvestmentLanguageButton = app.querySelector('[data-action="preview-investment-language"]');
if (previewInvestmentLanguageButton) {
    previewInvestmentLanguageButton.addEventListener("click", previewInvestmentLanguage);
  }
var refreshRuleboxButton = app.querySelector('[data-action="refresh-rulebox"]');
if (refreshRuleboxButton) {
    refreshRuleboxButton.addEventListener("click", function () {
      loadOntologyRulebox(true).then(function () {
        showSnackbar("TypeDB RuleBox를 다시 읽었습니다.");
      });
    });
  }
var refreshOntologyCatalogButton = app.querySelector('[data-action="refresh-ontology-catalog"]');
if (refreshOntologyCatalogButton) {
    refreshOntologyCatalogButton.addEventListener("click", function () {
      ontologyState.ontologyCatalogSummaryLoaded = false;
      ontologyState.ontologyCatalogPages = {};
      ontologyState.ontologyCatalogSelection = null;
      ontologyState.ontologyCatalogLineage = null;
      Promise.all([
        loadOntologyCatalogSummary(true),
        ontologyState.activeOntologyCatalogTab === "overview" ? Promise.resolve(null) : loadOntologyCatalogSection(ontologyState.activeOntologyCatalogTab, true, "offset:0")
      ]).then(function () {
        if (!ontologyState.ontologyCatalogSummaryError) showSnackbar("온톨로지 카탈로그를 다시 읽었습니다.");
      });
    });
  }
var refreshOntologyDiagnosticsButton = app.querySelector('[data-action="refresh-ontology-diagnostics"]');
if (refreshOntologyDiagnosticsButton) {
    refreshOntologyDiagnosticsButton.addEventListener("click", function () {
      loadOntologyDiagnostics(true, true).then(function (payload) {
        showSnackbar(payload && payload.cache && payload.cache.refreshing
          ? "TypeDB 진단 갱신을 시작했습니다."
          : "TypeDB 진단을 다시 읽었습니다.");
      });
    });
  }
var refreshOntologyReasoningStatusButton = app.querySelector('[data-action="refresh-ontology-reasoning-status"]');
if (refreshOntologyReasoningStatusButton) {
    refreshOntologyReasoningStatusButton.addEventListener("click", function () {
      loadOntologyReasoningStatus(true).then(function () {
        if (!ontologyState.ontologyReasoningStatusError) showSnackbar("추론 대기열 상태를 다시 읽었습니다.");
      });
    });
  }
var seedOntologyGraphButton = app.querySelector('[data-action="seed-ontology-graph"]');
if (seedOntologyGraphButton) {
    seedOntologyGraphButton.addEventListener("click", function () {
      seedOntologyGraph();
    });
  }
var seedRuleboxButton = app.querySelector('[data-action="seed-rulebox"]');
if (seedRuleboxButton) {
    seedRuleboxButton.addEventListener("click", function () {
      saveOntologyRulebox(true);
    });
  }
var saveRuleboxButton = app.querySelector('[data-action="save-rulebox"]');
if (saveRuleboxButton) {
    saveRuleboxButton.addEventListener("click", function () {
      saveOntologyRulebox(false);
    });
  }
var runRuleboxButton = app.querySelector('[data-action="run-rulebox"]');
if (runRuleboxButton) {
    runRuleboxButton.addEventListener("click", function () {
      runOntologyRulebox();
    });
  }
var proposeRuleboxCandidatesButton = app.querySelector('[data-action="propose-rulebox-candidates"]');
if (proposeRuleboxCandidatesButton) {
    proposeRuleboxCandidatesButton.addEventListener("click", function () {
      proposeOntologyRuleCandidates();
    });
  }
var refreshOntologyAuditButton = app.querySelector('[data-action="refresh-ontology-audit"]');
if (refreshOntologyAuditButton) {
    refreshOntologyAuditButton.addEventListener("click", function () {
      ontologyState.ontologyAuditLoaded = false;
      loadOntologyAudit(true).then(function () {
        showSnackbar("온톨로지 감사 데이터를 다시 읽었습니다.");
      });
    });
  }
var refreshInferenceLedgerButton = app.querySelector('[data-action="refresh-inference-ledger"]');
if (refreshInferenceLedgerButton) {
    refreshInferenceLedgerButton.addEventListener("click", function () {
      ontologyState.ontologyInferenceLedgerLoaded = false;
      loadOntologyInferenceLedger(true).then(function () {
        if (!ontologyState.ontologyInferenceLedgerError) showSnackbar("Inference Trace Ledger를 다시 읽었습니다.");
      });
    });
  }
var refreshLabButton = app.querySelector("[data-lab-refresh]");
if (refreshLabButton) {
    refreshLabButton.addEventListener("click", function () {
      loadOntologyExperiments(true).then(function () {
        showSnackbar("온톨로지 실험 상태를 다시 읽었습니다.");
      });
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-lab-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var section = normalizeExperimentSection(button.getAttribute("data-lab-section"));
      if (section === experimentsState.activeExperimentSection) return;
      experimentsState.activeExperimentSection = section;
      writeExperimentSectionHistory(section);
      primeActiveTabData("experiments");
      render({ transition: "section" });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var id = String(button.getAttribute("data-lab-select") || "").trim();
      if (!id || id === experimentsState.activeOntologyExperimentId) return;
      experimentsState.activeOntologyExperimentId = id;
      writeExperimentSectionHistory(experimentsState.activeExperimentSection, id);
      render();
    });
  });
var runActiveLabButton = app.querySelector("[data-lab-run-active]");
if (runActiveLabButton) {
    runActiveLabButton.addEventListener("click", function () {
      runOntologyExperimentsOnce();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-lab-suggest]")).forEach(function (suggestLabButton) {
    suggestLabButton.addEventListener("click", function () {
      suggestOntologyExperiments();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-run]")).forEach(function (button) {
    button.addEventListener("click", function () {
      runOntologyExperiment(button.getAttribute("data-lab-run"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-apply]")).forEach(function (button) {
    button.addEventListener("click", function () {
      applyOntologyExperiment(button.getAttribute("data-lab-apply"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-recommendation-toggle]")).forEach(function (input) {
    input.addEventListener("change", function () {
      toggleOntologyExperimentRecommendation(
        input.getAttribute("data-lab-recommendation-experiment"),
        input.getAttribute("data-lab-recommendation-toggle"),
        input.checked
      );
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-recommendations-select-all]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var id = button.getAttribute("data-lab-recommendations-select-all");
      setOntologyExperimentRecommendationSelection(id, applicableOntologyExperimentRecommendationIds(id));
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-recommendations-clear]")).forEach(function (button) {
    button.addEventListener("click", function () {
      setOntologyExperimentRecommendationSelection(button.getAttribute("data-lab-recommendations-clear"), []);
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-apply-selected]")).forEach(function (button) {
    button.addEventListener("click", function () {
      applySelectedOntologyExperimentRecommendations(button.getAttribute("data-lab-apply-selected"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-activate]")).forEach(function (button) {
    button.addEventListener("click", function () {
      activateOntologyExperiment(button.getAttribute("data-lab-activate"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-lab-pause]")).forEach(function (button) {
    button.addEventListener("click", function () {
      pauseOntologyExperiment(button.getAttribute("data-lab-pause"));
    });
  });
var refreshStrategyProposalsButton = app.querySelector('[data-action="refresh-strategy-proposals"]');
if (refreshStrategyProposalsButton) {
    refreshStrategyProposalsButton.addEventListener("click", function () {
      loadStrategyProposals(true).then(function () {
        if (!proposalsState.strategyProposalsError) showSnackbar("전략 제안 목록을 다시 읽었습니다.");
      });
    });
  }
var refreshHypothesisWorkspaceButton = app.querySelector('[data-action="refresh-hypothesis-workspace"]');
if (refreshHypothesisWorkspaceButton) {
    refreshHypothesisWorkspaceButton.addEventListener("click", function () {
      loadHypothesisWorkspace(true).then(function () {
        if (!hypothesesState.hypothesisWorkspaceError) showSnackbar("가설 검증 정보를 다시 읽었습니다.");
      });
    });
  }
var runHypothesisReplayButton = app.querySelector('[data-action="run-hypothesis-replay"]');
if (runHypothesisReplayButton) {
    runHypothesisReplayButton.addEventListener("click", runHypothesisReplay);
  }
var proposeHypothesisQualityReviewButton = app.querySelector('[data-action="propose-hypothesis-quality-review"]');
if (proposeHypothesisQualityReviewButton) {
    proposeHypothesisQualityReviewButton.addEventListener("click", proposeHypothesisQualityReview);
  }
var recordHypothesisPolicyBaselineButton = app.querySelector('[data-action="record-hypothesis-policy-baseline"]');
if (recordHypothesisPolicyBaselineButton) {
    recordHypothesisPolicyBaselineButton.addEventListener("click", recordHypothesisPolicyBaseline);
  }
Array.prototype.slice.call(app.querySelectorAll('[data-action="append-rulebox-candidate"]')).forEach(function (button) {
    button.addEventListener("click", function () {
      appendRuleboxCandidate(button.getAttribute("data-candidate-id"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("button[data-page-mode-page][data-page-mode]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var page = normalizeTabId(button.getAttribute("data-page-mode-page") || navigationState.activeTab);
      var mode = normalizePageMode(button.getAttribute("data-page-mode"));
      if (activePageMode(page) === mode) return;
      setPageViewMode(page, mode);
      if (page === "accounts") writeAccountSectionHistory(accountsState.activeAccountSection);
      else if (page === "notifications") writeNotificationSectionHistory(notificationsState.activeNotificationSection);
      else if (page === "modeling") writeStrategySectionHistory(decisionsState.activeStrategySection);
      else if (page === "feed") writeFeedSectionHistory(marketState.activeFeedSection);
      else writePageModeHistory(page, mode);
      render({ transition: "section" });
    });
  });
var expandMessageTypes = app.querySelector('[data-action="expand-message-types"]');
if (expandMessageTypes) {
    expandMessageTypes.addEventListener("click", function () {
      notificationsState.notificationExpandedTypes = {};
      notificationsState.notificationExpandedGroups = {};
      alertRuleGroups().forEach(function (group) {
        notificationsState.notificationExpandedGroups[group.name] = true;
      });
      render();
    });
  }
var collapseMessageTypes = app.querySelector('[data-action="collapse-message-types"]');
if (collapseMessageTypes) {
    collapseMessageTypes.addEventListener("click", function () {
      notificationsState.notificationExpandedTypes = {};
      notificationsState.notificationExpandedGroups = {};
      render();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-rule-save]")).forEach(function (button) {
    button.addEventListener("click", function () {
      saveNotificationRule(button.getAttribute("data-rule-save"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-rule-reset]")).forEach(function (button) {
    button.addEventListener("click", function () {
      resetNotificationRule(button.getAttribute("data-rule-reset"));
    });
  });
var refreshNotificationJobsButton = app.querySelector('[data-action="refresh-notification-jobs"]');
if (refreshNotificationJobsButton) {
    refreshNotificationJobsButton.addEventListener("click", function () {
      loadNotificationJobs();
      render();
    });
  }
var refreshOperationsHealthButton = app.querySelector('[data-action="refresh-operations-health"]');
if (refreshOperationsHealthButton) {
    refreshOperationsHealthButton.addEventListener("click", function () {
      loadOperationsHealth(true);
      render();
    });
  }
var resetNotificationJobFilters = app.querySelector('[data-action="reset-notification-job-filters"]');
if (resetNotificationJobFilters) {
    resetNotificationJobFilters.addEventListener("click", function () {
      notificationsState.notificationJobSearch = "";
      notificationsState.notificationJobStatusFilter = "all";
      notificationsState.notificationJobTypeFilter = "all";
      notificationsState.notificationInboxFilter = "all";
      resetNotificationJobsPaging();
      loadNotificationJobs();
      render();
      showSnackbar("알림 검색 조건을 초기화했습니다.");
    });
  }
Array.prototype.slice.call(app.querySelectorAll('[data-action="save-settings"]')).forEach(function (saveSettings) {
    saveSettings.addEventListener("click", function () {
      if (settingsState.settingsSaving) return;
      settingsState.settingsSaving = true;
      settingsState.serverSettingsError = "";
      render();
      saveSettingsToServer()
        .then(function () {
          settingsState.settingsSaved = true;
          showSnackbar("설정을 저장했습니다.");
        })
        .catch(function (error) {
          settingsState.serverSettingsError = error.message || "설정을 저장하지 못했습니다.";
          settingsState.settingsSaved = false;
          showSnackbar(settingsState.serverSettingsError, "danger");
        })
        .finally(function () {
          settingsState.settingsSaving = false;
          render();
        });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-copy-share-url]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var url = button.getAttribute("data-copy-share-url") || "";
      button.disabled = true;
      copyTextToClipboard(url)
        .then(function () { showSnackbar("고정 접속 전체 링크를 복사했습니다.", "success"); })
        .catch(function () { showSnackbar("링크를 복사하지 못했습니다.", "danger"); })
        .finally(function () { button.disabled = false; });
    });
  });
Array.prototype.slice.call(app.querySelectorAll('[data-action="rotate-share-tunnel"]')).forEach(function (button) {
    button.addEventListener("click", requestShareTunnelRotation);
  });
Array.prototype.slice.call(app.querySelectorAll("[data-market-workspace]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var mode = normalizeMarketWorkspaceMode(button.getAttribute("data-market-workspace"));
      if (mode === marketState.marketWorkspaceMode) return;
      rememberRenderedPageScrollPosition();
      marketState.marketWorkspaceMode = mode;
      writeMarketWorkspaceHistory(mode);
      if (mode === "universe" && !universeState.symbolUniverseLoaded && !universeState.symbolUniverseLoading) loadSymbolUniverse();
      render({ transition: "section" });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-portfolio-view]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var view = normalizePortfolioView(button.getAttribute("data-portfolio-view"));
      if (view === portfolioState.activePortfolioView) return;
      rememberRenderedPageScrollPosition();
      portfolioState.activePortfolioView = view;
      writeConsoleWorkspaceViewHistory("portfolio", "portfolioView", view, "summary");
      loadPortfolioReadModel(view, false);
      render({ transition: "section" });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-operations-view]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var view = normalizeOperationsView(button.getAttribute("data-operations-view"));
      if (view === operationsState.activeOperationsView) return;
      rememberRenderedPageScrollPosition();
      operationsState.activeOperationsView = view;
      writeConsoleWorkspaceViewHistory("operations", "operationsView", view, "health");
      if (!operationsState.operationsHealth && !operationsState.operationsHealthLoading) loadOperationsHealth(false);
      render({ transition: "section" });
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-operations-action-view]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var view = normalizeOperationsView(button.getAttribute("data-operations-action-view"));
      if (view === operationsState.activeOperationsView) return;
      rememberRenderedPageScrollPosition();
      operationsState.activeOperationsView = view;
      writeConsoleWorkspaceViewHistory("operations", "operationsView", view, "health");
      render({ transition: "section" });
    });
  });
var addVisibleSymbols = app.querySelector('[data-action="add-visible-symbols"]');
if (addVisibleSymbols) {
    addVisibleSymbols.addEventListener("click", function () {
      addVisibleSymbolsToPreferredWatchlist();
    });
  }
Array.prototype.slice.call(app.querySelectorAll('[data-action="toggle-secrets"]')).forEach(function (toggleSecrets) {
    toggleSecrets.addEventListener("click", function () {
      settingsState.showSecrets = !settingsState.showSecrets;
      render();
    });
  });
}

export { bindActions };
