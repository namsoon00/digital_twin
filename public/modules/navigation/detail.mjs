import { accountBalanceWorkDetailPayload, accountConnectionsWorkDetailPayload, accountHistoryWorkDetailPayload, accountIdentityWorkDetailPayload } from "../accounts/detail.mjs";
import { loadInvestmentCalendar } from "../calendar/commands.mjs";
import { calendarCandidateBoardWorkDetailPayload, investmentCalendarEventWorkDetailPayload } from "../calendar/workspace.mjs";
import { investmentActionWorkDetailPayload } from "../decisions/actions.mjs";
import { investmentFlowWorkDetailPayload } from "../decisions/case-detail.mjs";
import { strategyChartsWorkDetailPayload, strategyEvidenceWorkDetailPayload } from "../decisions/detail-links.mjs";
import { investmentReasoningCardWorkDetailPayload } from "../decisions/evidence.mjs";
import { investmentModelManagementWorkDetailPayload, investmentModelOverviewWorkDetailPayload } from "../decisions/model.mjs";
import { loadInvestmentFlowDetail } from "../decisions/requests.mjs";
import { decisionOutcomeBoardWorkDetailPayload, decisionQueueWorkDetailPayload, subjectDecisionCaseWorkDetailPayload } from "../decisions/workspace.mjs";
import { experimentAuditWorkDetailPayload, experimentPromotionWorkDetailPayload, experimentProposalsWorkDetailPayload, experimentValidationWorkDetailPayload } from "../experiments/detail.mjs";
import { ontologyExperimentWorkDetailPayload } from "../experiments/work-detail.mjs";
import { hypothesisGovernanceWorkDetailPayload, hypothesisReviewWorkDetailPayload, loadHypothesisPolicyVersions, loadHypothesisWorkspaceDetail } from "../hypotheses/workspace.mjs";
import { ensureInstrumentEventGroupTimeline, instrumentWorkspaceTab, loadInstrumentTimeline, loadInstrumentValuation } from "../instruments/timeline.mjs";
import { instrumentEventGroupWorkDetailPayload, instrumentTimelineEventWorkDetailPayload, marketInstrumentWorkDetailPayload } from "../instruments/workspace.mjs";
import { feedSettingsWorkDetailPayload } from "../market/detail.mjs";
import { feedImpactBoardWorkDetailPayload, feedPortfolioBoardWorkDetailPayload, feedSourceBoardWorkDetailPayload, feedThemeBoardWorkDetailPayload } from "../market/feed.mjs";
import { viewLifetime } from "./lifecycle.mjs";
import { navigateToTab } from "./router.mjs";
import { investmentCaseDetailRequiresKey, workDetailUrl, writeWorkDetailHistory } from "./routes.mjs";
import { focusElementWithoutScroll } from "./scroll.mjs";
import { notificationCandidatesWorkDetailPayload, notificationDeliveryWorkDetailPayload, notificationDiagnosticsWorkDetailPayload, notificationPolicyWorkDetailPayload, notificationRuleDiagnosticsWorkDetailPayload, notificationTemplatesWorkDetailPayload, notificationThresholdWorkDetailPayload } from "../notifications/detail-links.mjs";
import { notificationWorkDetailPayload } from "../notifications/history.mjs";
import { loadNotificationJobDetail } from "../notifications/requests.mjs";
import { ontologyAuditRowWorkDetailPayload, ontologyAuditSectionWorkDetailPayload, strategyGraphsWorkDetailPayload, strategyModelPolicyWorkDetailPayload, strategyPromptWorkDetailPayload, strategyRuleboxWorkDetailPayload, strategyTraceBoardWorkDetailPayload } from "../ontology/detail.mjs";
import { strategyTraceWorkDetailPayload } from "../ontology/inference.mjs";
import { loadOntologyAuditSection } from "../ontology/requests.mjs";
import { todayQueueWorkDetailPayload } from "../overview/workspace.mjs";
import { portfolioInterpretationWorkDetailPayload } from "../portfolio/detail.mjs";
import { loadPortfolioInterpretation } from "../portfolio/requests.mjs";
import { strategyProposalsWorkDetailPayload } from "../proposals/detail.mjs";
import { pendingRenderTransitionCell, render } from "../render/scheduler.mjs";
import { loadResearchEvidenceDetail } from "../research/requests.mjs";
import { loadInvestmentCalendarDetail } from "../calendar/commands.mjs";
import { feedPipelineWorkDetailPayload, feedQualityWorkDetailPayload, feedSourcesWorkDetailPayload, researchEvidenceWorkDetailPayload } from "../research/workspace.mjs";
import { investmentLanguageWorkDetailPayload, settingsAiRuntimeWorkDetailPayload, settingsDataSourcesWorkDetailPayload, settingsDiagnosticsWorkDetailPayload, settingsOperationsNotificationsWorkDetailPayload, settingsPreferencesWorkDetailPayload, settingsRuntimeWorkDetailPayload, settingsUserNotificationsWorkDetailPayload } from "../settings/legacy.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { screenInfoWorkDetailPayload } from "../shell/commands.mjs";
import { app } from "../shell/root.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { accountsState } from "../state/accounts.mjs";
import { calendarState } from "../state/calendar.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";

var workDetailReturnFocus = null;

function openWorkDetailLayer(type, key) {
  if (!type) return;
  viewLifetime.invalidate();
  if (type === "investment-action" && String(key || "").indexOf("decision:") === 0) {
    type = "investment-case";
  }
  if (investmentCaseDetailRequiresKey(type) && !String(key || "").trim()) {
    if (navigationState.activeTab !== "modeling") navigateToTab("modeling");
    showSnackbar("투자 케이스를 먼저 선택해 주세요.", "caution");
    return;
  }
  var activeElement = document.activeElement;
  workDetailReturnFocus = activeElement && activeElement.getAttribute ? {
    type: activeElement.getAttribute("data-work-detail") || String(type || ""),
    key: activeElement.getAttribute("data-work-detail-key") || String(key || "")
  } : { type: String(type || ""), key: String(key || "") };
  navigationState.workDetailLayer = {
    type: String(type || ""),
    key: String(key || "")
  };
  if (navigationState.workDetailLayer.type === "notification-job") {
    loadNotificationJobDetail(navigationState.workDetailLayer.key);
  }
  if (navigationState.workDetailLayer.type === "research-evidence") {
    loadResearchEvidenceDetail(navigationState.workDetailLayer.key);
  }
  if (navigationState.workDetailLayer.type === "investment-calendar-event") {
    loadInvestmentCalendarDetail(navigationState.workDetailLayer.key);
  }
  if (navigationState.workDetailLayer.type === "portfolio-interpretation") {
    loadPortfolioInterpretation(false);
  }
  if (navigationState.workDetailLayer.type === "hypothesis-review") {
    hypothesesState.activeHypothesisLifecycleKey = navigationState.workDetailLayer.key;
    loadHypothesisWorkspaceDetail(navigationState.workDetailLayer.key);
  }
  if (navigationState.workDetailLayer.type === "investment-calendar-event" && !calendarState.investmentCalendar && !calendarState.investmentCalendarLoading) {
    loadInvestmentCalendar(false);
  }
  if (navigationState.workDetailLayer.type === "hypothesis-governance") {
    loadHypothesisPolicyVersions(false);
  }
  if (["investment-case", "investment-flow"].indexOf(navigationState.workDetailLayer.type) >= 0) {
    if (!decisionsState.investmentCaseDetailTabs[navigationState.workDetailLayer.key]) decisionsState.investmentCaseDetailTabs[navigationState.workDetailLayer.key] = "summary";
    loadInvestmentFlowDetail(navigationState.workDetailLayer.key, false);
  }
  if (navigationState.workDetailLayer.type === "ontology-audit-section") {
    loadOntologyAuditSection(navigationState.workDetailLayer.key);
  }
  writeWorkDetailHistory(type, key);
  render({ transition: "detail-open" });
  if (navigationState.workDetailLayer.type === "market-instrument") {
    var activeInstrumentTab = instrumentWorkspaceTab(navigationState.workDetailLayer.key);
    if (activeInstrumentTab === "valuation") loadInstrumentValuation(navigationState.workDetailLayer.key, false);
    if (["chart", "decision", "timeline"].indexOf(activeInstrumentTab) >= 0) loadInstrumentTimeline(navigationState.workDetailLayer.key, false);
  }
  if (navigationState.workDetailLayer.type === "instrument-event-group") {
    ensureInstrumentEventGroupTimeline(navigationState.workDetailLayer.key);
  }
}

function closeWorkDetailLayer() {
  viewLifetime.invalidate();
  if (!navigationState.workDetailLayer) return;
  var params = new URLSearchParams(window.location.search);
  if (params.get("detail") && window.history && window.history.state && window.history.state.workDetail && window.history.back) {
    pendingRenderTransitionCell.value = "detail-close";
    window.history.back();
    return;
  }
  navigationState.workDetailLayer = null;
  if (window.history && window.history.replaceState) {
    var nextState = Object.assign({}, window.history.state || {});
    delete nextState.workDetail;
    delete nextState.detail;
    delete nextState.detailKey;
    window.history.replaceState(nextState, "", workDetailUrl("", ""));
  }
  render({ transition: "detail-close" });
  restoreWorkDetailFocus();
}

function activeOverlayDialog() {
  if (accountsState.watchlistAccountPickerSymbol) return app.querySelector("[data-watchlist-picker-dialog]");
  if (navigationState.commandPaletteOpen) return app.querySelector("[data-command-palette-dialog]");
  if (calendarState.investmentCalendarCandidateConfirmation) return app.querySelector("[data-calendar-candidate-confirm-dialog]");
  if (calendarState.calendarEntryModalOpen) return app.querySelector(".calendar-entry-modal");
  if (notificationsState.notificationTemplateEditorOpen) return app.querySelector(".notification-template-editor-layer");
  if (notificationsState.notificationPolicyEditorOpen) return app.querySelector(".notification-policy-editor-layer");
  if (ontologyState.expandedOntologyGraphId) return app.querySelector(".ontology-graph-expanded-dialog");
  if (navigationState.workDetailLayer) return app.querySelector("[data-work-detail-dialog]");
  if (navigationState.monitoringDetail) return app.querySelector(".monitoring-detail-drawer");
  return null;
}

function focusWorkDetailLayer() {
  var dialog = activeOverlayDialog();
  if (!dialog || dialog.contains(document.activeElement)) return;
  var closeButton = dialog.querySelector('[data-watchlist-picker-close], [data-command-palette-input], [data-work-detail-close], [data-calendar-candidate-confirm-close], [data-calendar-entry-close], [data-notification-editor-close], [data-notification-template-editor-close], [data-ontology-graph-close], [data-monitoring-detail-close]');
  focusElementWithoutScroll(closeButton || dialog);
}

function restoreWorkDetailFocus() {
  var target = workDetailReturnFocus;
  workDetailReturnFocus = null;
  if (!target) return;
  var buttons = Array.prototype.slice.call(app.querySelectorAll("[data-work-detail]"));
  var match = buttons.filter(function (button) {
    return button.getAttribute("data-work-detail") === target.type
      && String(button.getAttribute("data-work-detail-key") || "") === String(target.key || "");
  })[0];
  if (match) focusElementWithoutScroll(match);
}

function trapWorkDetailFocus(event) {
  if (event.key !== "Tab") return false;
  var dialog = activeOverlayDialog();
  if (!dialog) return false;
  var focusable = Array.prototype.slice.call(dialog.querySelectorAll(
    'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )).filter(function (node) {
    return node.offsetParent !== null;
  });
  if (!focusable.length) {
    event.preventDefault();
    dialog.focus();
    return true;
  }
  var first = focusable[0];
  var last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
    return true;
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}

function renderWorkDetailButton(type, key, label, className, extraAttrs) {
  return [
    '<button class="' + escapeHtml(className || "mini-button") + '" type="button" data-work-detail="' + escapeHtml(type || "") + '" data-work-detail-key="' + escapeHtml(key || "") + '"' + (extraAttrs || "") + '>',
    escapeHtml(label || "상세 보기"),
    '</button>'
  ].join("");
}

function renderInfoIconButton(key, label) {
  var text = label || "이 화면의 흐름과 상세 설명";
  return renderWorkDetailButton(
    "screen-info",
    key || navigationState.activeTab || "overview",
    "i",
    "icon-button screen-info-button",
    ' title="' + escapeHtml(text) + '" aria-label="' + escapeHtml(text) + '"'
  );
}

function workDetailPresentation(type) {
  var detailType = String(type || "");
  var protectedEditors = [
    "feed-settings-editor",
    "strategy-rulebox-editor",
    "strategy-prompt-editor",
    "strategy-model-policy-editor",
    "settings-investment-language",
    "notification-delivery-settings",
    "notification-threshold-settings",
    "settings-user-notifications",
    "settings-preferences",
    "settings-data-sources",
    "settings-ai-runtime",
    "settings-operations-notifications",
    "settings-diagnostics",
    "settings-runtime"
  ];
  var wideDetails = [
    "strategy-charts-board",
    "strategy-graphs-board",
    "strategy-evidence-board",
    "strategy-proposals-board",
    "strategy-trace-board",
    "experiment-validation-board",
    "experiment-promotion-board",
    "experiment-audit-board",
    "experiment-proposals-board",
    "ontology-audit-section",
    "hypothesis-review",
    "hypothesis-governance",
    "subject-decision-case",
    "investment-case",
    "investment-flow",
    "investment-model-overview",
    "investment-model-management",
    "decision-outcome-board",
    "market-instrument"
  ];
  var largeDetails = [
    "today-work-queue",
    "decision-action-queue",
    "feed-impact-board",
    "feed-theme-board",
    "feed-portfolio-board",
    "feed-source-board",
    "account-connections-board",
    "account-balance-board",
    "account-history-board",
    "account-identity-board",
    "instrument-event-group",
    "notification-candidates-board",
    "notification-policy-board",
    "notification-templates-board",
    "notification-diagnostics-board",
    "feed-pipeline",
    "feed-sources",
    "feed-quality"
  ];
  var editor = protectedEditors.indexOf(detailType) >= 0;
  var size = wideDetails.indexOf(detailType) >= 0
    ? "wide"
    : (largeDetails.indexOf(detailType) >= 0 || editor ? "large" : "medium");
  return {
    size: size,
    kind: editor ? "editor" : "reader",
    dismissOnBackdrop: !editor
  };
}

function renderWorkDetailLayer() {
  var detail = navigationState.workDetailLayer || {};
  if (!detail.type) return "";
  var payload = workDetailPayload(detail.type, detail.key);
  if (!payload) return "";
  var headingId = "work-detail-title";
  var descriptionId = "work-detail-description";
  var presentation = workDetailPresentation(detail.type);
  var backdropCloseAttr = presentation.dismissOnBackdrop ? " data-work-detail-close" : "";
  return [
    '<div class="work-detail-backdrop" data-work-detail-backdrop data-work-detail-kind="' + escapeHtml(presentation.kind) + '" data-work-detail-type="' + escapeHtml(detail.type) + '" data-work-detail-key="' + escapeHtml(detail.key || "") + '"' + backdropCloseAttr + '>',
    '<aside class="work-detail-layer work-detail-layer-' + escapeHtml(presentation.size) + '" role="dialog" aria-modal="true" aria-labelledby="' + headingId + '"' + (payload.meta ? ' aria-describedby="' + descriptionId + '"' : '') + ' tabindex="-1" data-work-detail-dialog data-work-detail-size="' + escapeHtml(presentation.size) + '" data-work-detail-type="' + escapeHtml(detail.type) + '" data-work-detail-key="' + escapeHtml(detail.key || "") + '">',
    '<header class="work-detail-head">',
    '<div>',
    '<p class="label">' + escapeHtml(payload.kicker || "Detail") + '</p>',
    '<h2 id="' + headingId + '">' + escapeHtml(payload.title || "상세 정보") + '</h2>',
    payload.meta ? '<span id="' + descriptionId + '">' + escapeHtml(payload.meta) + '</span>' : '',
    '</div>',
    '<button class="icon-button danger" type="button" data-work-detail-close title="상세 닫기" aria-label="상세 닫기">&times;</button>',
    '</header>',
    '<div class="work-detail-body" data-work-detail-region="body">',
    payload.body || '',
    '</div>',
    payload.footer ? '<footer class="work-detail-footer">' + payload.footer + '</footer>' : '',
    '</aside>',
    '</div>'
  ].join("");
}

function workDetailPayload(type, key) {
  if (type === "screen-info") return screenInfoWorkDetailPayload(key);
  if (type === "today-work-queue") return todayQueueWorkDetailPayload();
  if (type === "calendar-candidate-board") return calendarCandidateBoardWorkDetailPayload();
  if (type === "decision-action-queue") return decisionQueueWorkDetailPayload();
  if (type === "market-instrument") return marketInstrumentWorkDetailPayload(key);
  if (type === "portfolio-interpretation") return portfolioInterpretationWorkDetailPayload();
  if (type === "instrument-event-group") return instrumentEventGroupWorkDetailPayload(key);
  if (type === "feed-impact-board") return feedImpactBoardWorkDetailPayload();
  if (type === "feed-theme-board") return feedThemeBoardWorkDetailPayload();
  if (type === "feed-portfolio-board") return feedPortfolioBoardWorkDetailPayload();
  if (type === "feed-source-board") return feedSourceBoardWorkDetailPayload();
  if (type === "account-connections-board") return accountConnectionsWorkDetailPayload();
  if (type === "account-balance-board") return accountBalanceWorkDetailPayload();
  if (type === "account-history-board") return accountHistoryWorkDetailPayload();
  if (type === "account-identity-board") return accountIdentityWorkDetailPayload();
  if (type === "notification-candidates-board") return notificationCandidatesWorkDetailPayload();
  if (type === "notification-policy-board") return notificationPolicyWorkDetailPayload();
  if (type === "notification-templates-board") return notificationTemplatesWorkDetailPayload();
  if (type === "notification-diagnostics-board") return notificationDiagnosticsWorkDetailPayload();
  if (type === "strategy-evidence-board") return strategyEvidenceWorkDetailPayload();
  if (type === "strategy-charts-board") return strategyChartsWorkDetailPayload();
  if (type === "strategy-graphs-board") return strategyGraphsWorkDetailPayload();
  if (type === "strategy-proposals-board") return strategyProposalsWorkDetailPayload();
  if (type === "strategy-trace-board") return strategyTraceBoardWorkDetailPayload();
  if (type === "experiment-validation-board") return experimentValidationWorkDetailPayload();
  if (type === "experiment-promotion-board") return experimentPromotionWorkDetailPayload();
  if (type === "experiment-audit-board") return experimentAuditWorkDetailPayload();
  if (type === "experiment-proposals-board") return experimentProposalsWorkDetailPayload();
  if (type === "notification-job") return notificationWorkDetailPayload(key) || instrumentTimelineEventWorkDetailPayload(type, key);
  if (type === "feed-impact" || type === "research-evidence") return researchEvidenceWorkDetailPayload(key) || instrumentTimelineEventWorkDetailPayload(type, key);
  if (type === "feed-pipeline") return feedPipelineWorkDetailPayload();
  if (type === "feed-sources") return feedSourcesWorkDetailPayload();
  if (type === "feed-quality") return feedQualityWorkDetailPayload();
  if (type === "investment-action") return investmentActionWorkDetailPayload(key) || instrumentTimelineEventWorkDetailPayload(type, key);
  if (type === "subject-decision-case") return subjectDecisionCaseWorkDetailPayload(key);
  if (type === "investment-case" || type === "investment-flow") return investmentFlowWorkDetailPayload(key);
  if (type === "investment-calendar-event") return investmentCalendarEventWorkDetailPayload(key) || instrumentTimelineEventWorkDetailPayload(type, key);
  if (type === "investment-reasoning-card") return investmentReasoningCardWorkDetailPayload(key);
  if (type === "ontology-experiment") return ontologyExperimentWorkDetailPayload(key);
  if (type === "feed-settings-editor") return feedSettingsWorkDetailPayload(key);
  if (type === "strategy-rulebox-editor") return strategyRuleboxWorkDetailPayload();
  if (type === "strategy-prompt-editor") return strategyPromptWorkDetailPayload();
  if (type === "strategy-model-policy-editor") return strategyModelPolicyWorkDetailPayload();
  if (type === "investment-model-overview") return investmentModelOverviewWorkDetailPayload();
  if (type === "investment-model-management") return investmentModelManagementWorkDetailPayload();
  if (type === "decision-outcome-board") return decisionOutcomeBoardWorkDetailPayload();
  if (type === "settings-investment-language") return investmentLanguageWorkDetailPayload();
  if (type === "strategy-trace-detail") return strategyTraceWorkDetailPayload(key);
  if (type === "hypothesis-review") return hypothesisReviewWorkDetailPayload(key) || instrumentTimelineEventWorkDetailPayload(type, key);
  if (type === "hypothesis-governance") return hypothesisGovernanceWorkDetailPayload();
  if (type === "ontology-audit-section") return ontologyAuditSectionWorkDetailPayload(key);
  if (type === "ontology-audit-row") return ontologyAuditRowWorkDetailPayload(key);
  if (type === "notification-delivery-settings") return notificationDeliveryWorkDetailPayload();
  if (type === "notification-rule-diagnostics") return notificationRuleDiagnosticsWorkDetailPayload();
  if (type === "notification-threshold-settings") return notificationThresholdWorkDetailPayload();
  if (type === "settings-user-notifications") return settingsUserNotificationsWorkDetailPayload();
  if (type === "settings-preferences") return settingsPreferencesWorkDetailPayload();
  if (type === "settings-data-sources") return settingsDataSourcesWorkDetailPayload();
  if (type === "settings-ai-runtime") return settingsAiRuntimeWorkDetailPayload();
  if (type === "settings-operations-notifications") return settingsOperationsNotificationsWorkDetailPayload();
  if (type === "settings-diagnostics") return settingsDiagnosticsWorkDetailPayload();
  if (type === "settings-runtime") return settingsRuntimeWorkDetailPayload();
  return null;
}

function editorWorkDetailPayload(kicker, title, meta, body) {
  return {
    kicker: kicker || "Detail",
    title: title || "상세 편집",
    meta: meta || "",
    body: body || ""
  };
}

export { activeOverlayDialog, closeWorkDetailLayer, editorWorkDetailPayload, focusWorkDetailLayer, openWorkDetailLayer, renderInfoIconButton, renderWorkDetailButton, renderWorkDetailLayer, restoreWorkDetailFocus, trapWorkDetailFocus };
