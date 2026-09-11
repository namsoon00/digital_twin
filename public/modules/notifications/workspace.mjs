import { renderStrategyOverviewActionCard } from "../decisions/strategy.mjs";
import { renderInfoIconButton, renderWorkDetailButton } from "../navigation/detail.mjs";
import { activeNotificationSectionMeta, activePageMode, activeSectionForPageMode, modeSectionsForPage, normalizeNotificationSection } from "../navigation/routes.mjs";
import { renderAlertCenterPanel } from "./alerts-view.mjs";
import { alertCadenceMinutes, alertRules, enabledAlertRule } from "./alerts.mjs";
import { renderMessageScheduleSummary, renderNotificationRuleEditor, renderNotificationTemplateManagerPanel, renderNotificationTemplateRow, scheduleStatusClass, scheduleStatusLabel } from "./editor.mjs";
import { renderNotificationDecisionPanel } from "./history.mjs";
import { defaultNotificationTemplates, messageScheduleByType, notificationTemplateForEdit } from "./policy.mjs";
import { renderNotificationDetailMetric } from "./reasoning.mjs";
import { renderAdminMonitoringPanel, renderMonitoringDetailOverlay, renderMonitoringInstrumentPanel } from "../operations/monitoring.mjs";
import { renderPortfolioPanel } from "../portfolio/valuation.mjs";
import { settingsSaveButtonClass, settingsSaveButtonLabel, settingsSaveDisabledAttr } from "../settings/fields.mjs";
import { latestChangedFirst, recordChangedAt, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { alertThresholdCatalog, labelWithNotificationIcon, notificationPolicyCatalog, notificationSections, visibleNotificationTemplateType } from "../shell/catalog.mjs";
import { configuredCount } from "../shell/commands.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { renderManagedPage } from "../shell/pages.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { shellState } from "../state/shell.mjs";

function renderNotificationsPage() {
  var section = activeSectionForPageMode("notifications", notificationSections, normalizeNotificationSection(notificationsState.activeNotificationSection));
  var content = renderNotificationSectionContent();
  return renderManagedPage("notifications", shellState.snapshot || {}, [
    '<section class="admin-grid notifications-view">',
    renderNotificationCommandCenter(section),
    content,
    '</section>',
    section === "candidates" ? renderMonitoringDetailOverlay(shellState.snapshot || {}) : ''
  ].join(""));
}

function notificationEnabledRuleCount() {
  var rules = alertRules();
  return notificationPolicyCatalog().filter(function (rule) {
    return enabledAlertRule(rules, rule.key);
  }).length;
}

function notificationTemplateItems() {
  var templates = notificationsState.notificationTemplates.length ? notificationsState.notificationTemplates : defaultNotificationTemplates();
  return templates.filter(function (item) {
    return visibleNotificationTemplateType(item.messageType);
  });
}

function renderNotificationSectionBar() {
  var visibleSections = modeSectionsForPage("notifications", notificationSections);
  var activeId = activeSectionForPageMode("notifications", notificationSections, notificationsState.activeNotificationSection);
  if (activePageMode("notifications") !== "settings") {
    return [
      '<div class="notification-section-bar notification-drilldown-bar" data-section-mode="results">',
      '<div class="notification-section-tabs notification-drilldown-rail" role="toolbar" aria-label="알림 상세 보기">',
      renderWorkDetailButton("notification-candidates-board", "", "후보 신호", "text-button compact"),
      renderWorkDetailButton("notification-diagnostics-board", "", "진단", "text-button compact"),
      renderWorkDetailButton("notification-policy-board", "", "정책", "text-button compact"),
      renderWorkDetailButton("notification-templates-board", "", "템플릿", "text-button compact"),
      renderInfoIconButton("notifications", "알림 운영 탭의 단일 화면 운영 방식"),
      '</div>',
      '<div class="notification-section-actions">',
      '<button class="text-button" data-action="refresh-notification-jobs"' + (notificationsState.notificationJobsLoading ? ' disabled' : '') + '>판단 새로고침</button>',
      '<button class="text-button" data-page-mode-page="notifications" data-page-mode="settings">알림 설정</button>',
      '</div>',
      '</div>'
    ].join("");
  }
  return [
    '<div class="notification-section-bar" data-section-mode="' + escapeHtml(activePageMode("notifications")) + '">',
    '<div class="notification-section-tabs" role="tablist" aria-label="알림 설정 섹션">',
    visibleSections.map(function (item) {
      var active = activeId === item.id;
      return [
        '<button type="button" role="tab" class="' + (active ? "active" : "") + '" data-notification-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
        '<strong>' + escapeHtml(item.label) + '</strong>',
        '<span>' + escapeHtml(item.description) + '</span>',
        '</button>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="notification-section-actions">',
    '<button class="text-button" data-action="refresh-notification-jobs"' + (notificationsState.notificationJobsLoading ? ' disabled' : '') + '>판단 새로고침</button>',
    activePageMode("notifications") === "settings"
      ? '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>'
      : '<button class="text-button" data-page-mode-page="notifications" data-page-mode="settings">알림 설정</button>',
    '</div>',
    '</div>'
  ].join("");
}

function renderNotificationCommandCenter(sectionId) {
  var section = notificationSections.filter(function (item) {
    return item.id === normalizeNotificationSection(sectionId);
  })[0] || activeNotificationSectionMeta();
  var summary = notificationsState.notificationJobsSummary || shellState.realtime.notificationJobs || {};
  var failedCount = Number(summary.failed || 0);
  var pendingCount = Number(summary.pending || 0);
  var statusTone = failedCount ? "danger" : (pendingCount ? "watch" : "muted");
  var statusText = failedCount ? failedCount + "건 실패 확인" : (pendingCount ? pendingCount + "건 대기" : "대기 없음");
  return [
    '<section class="notification-command-center" aria-label="알림 운영 요약">',
    '<div class="notification-command-head">',
    '<div>',
    '<p class="label">Notifications</p>',
    '<h2>알림 운영</h2>',
    '<span>' + escapeHtml(section.label + " · " + section.description) + '</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(statusTone) + '">' + escapeHtml(statusText) + '</span>',
    '</div>',
    renderNotificationSectionBar(),
    renderNotificationOpsRail(),
    '</section>'
  ].join("");
}

function renderNotificationOpsRail() {
  var summary = notificationsState.notificationJobsSummary || shellState.realtime.notificationJobs || {};
  var templateCount = notificationTemplateItems().length;
  var scheduleCount = Array.isArray(notificationsState.messageSchedules) ? notificationsState.messageSchedules.length : 0;
  var items = [
    ["대기", Number(summary.pending || 0), "watch"],
    ["발송", Number(summary.done || 0), "watch"],
    ["보류", Number(summary.suppressed || 0), "muted"],
    ["실패", Number(summary.failed || 0), Number(summary.failed || 0) ? "danger" : "muted"],
    ["관리 룰", notificationEnabledRuleCount() + "/" + notificationPolicyCatalog().length, "policy"],
    ["템플릿", templateCount + "개", "muted"],
    ["스케줄", scheduleCount || "-", "muted"]
  ];
  return [
    '<section class="notification-ops-rail" aria-label="알림 상태 요약">',
    items.map(renderNotificationOpsCell).join(""),
    '</section>'
  ].join("");
}

function renderNotificationOpsCell(item) {
  return [
    '<span class="notification-ops-cell ' + escapeHtml(item[2] || "muted") + '"' + cardTypeAttrs("metric-cell", item[2] || "muted") + '>',
    '<em>' + escapeHtml(item[0]) + '</em>',
    '<strong>' + escapeHtml(String(item[1])) + '</strong>',
    '</span>'
  ].join("");
}

function renderNotificationSectionContent() {
  var section = activeSectionForPageMode("notifications", notificationSections, normalizeNotificationSection(notificationsState.activeNotificationSection));
  if (activePageMode("notifications") !== "settings") return renderNotificationUnifiedConsole();
  if (section === "candidates") return renderNotificationCandidatePanel(shellState.snapshot || {});
  if (section === "policy") return renderAdminMessagePanel();
  if (section === "templates") return renderNotificationTemplateManagerPanel();
  if (section === "diagnostics") return renderNotificationDiagnosticsPanel();
  return renderNotificationDecisionPanel();
}

function renderNotificationUnifiedConsole() {
  return [
    '<div class="single-tab-console notification-unified-console">',
    renderNotificationDecisionPanel(),
    renderNotificationDiagnosticsSummaryPanel(),
    '</div>'
  ].join("");
}

function decisionStateLabel(value) {
  return ({
    sufficient: "자료 충분",
    partial: "일부 자료만 있음",
    insufficient: "자료 부족",
    unavailable: "자료 없음",
    "risk-only": "위험 근거만 있음",
    "support-only": "우호 근거만 있음",
    mixed: "우호·위험 근거 혼재",
    "context-only": "방향 없는 참고 근거",
    normal: "평소 상태",
    observe: "관찰",
    check: "확인 필요",
    act: "대응 검토",
    immediate: "즉시 확인",
    blocked: "판단 보류"
  })[String(value || "")] || String(value || "-");
}

function renderNotificationCandidatePanel(snapshot) {
  return [
    renderAdminMonitoringPanel(snapshot),
    renderAlertCenterPanel(snapshot),
    renderMonitoringInstrumentPanel(snapshot),
    renderPortfolioPanel(snapshot)
  ].join("");
}

function renderNotificationDiagnosticsPanel() {
  return renderNotificationDiagnosticsSummaryPanel();
}

function renderNotificationDiagnosticsSummaryPanel() {
  var summary = notificationsState.notificationJobsSummary || shellState.realtime.notificationJobs || {};
  var diagnostics = notificationsState.notificationJobDiagnostics || {};
  var rule = activeNotificationRule();
  var pending = Number(summary.pending || 0);
  var failed = Number(summary.failed || 0);
  var suppressed = Number(summary.suppressed || 0);
  var staleCount = Number(diagnostics.staleProcessingCount || 0);
  var externalReady = configuredCount(["telegramBotToken", "telegramChatId"]);
  var cards = [
    {
      tone: externalReady >= 2 ? "watch" : "caution",
      value: externalReady + "/2",
      title: "전달 채널",
      description: "Telegram 토큰, Chat ID, 알림 링크 같은 전달 설정은 상세에서 수정합니다.",
      type: "notification-delivery-settings",
      button: "전달 설정"
    },
    {
      tone: staleCount || suppressed ? "caution" : "watch",
      value: suppressed + "건",
      title: "반복·장 상태",
      description: rule.label + " 기준으로 유사 메시지와 장 상태 참고 정보를 진단합니다.",
      type: "notification-rule-diagnostics",
      button: "조건 진단"
    },
    {
      tone: "hold",
      value: alertThresholdCatalog.length + "개",
      title: "임계값",
      description: "실시간·모델·외부 데이터 알림 기준은 필요할 때만 상세에서 조정합니다.",
      type: "notification-threshold-settings",
      button: "임계값 편집"
    }
  ];
  return [
    '<article class="panel notification-diagnostics-summary-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Diagnostics</p>',
    '<h2>알림 진단 요약</h2>',
    '<p class="subtle">발송 결과와 보류 원인을 먼저 보고, 긴 설정 화면은 상세 레이어에서만 엽니다.</p>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(failed ? "danger" : (pending ? "watch" : "hold")) + '">' + escapeHtml(failed ? failed + "건 실패" : (pending ? pending + "건 대기" : "대기 없음")) + '</span>',
    '</div>',
    '<div class="work-detail-metric-row">',
    renderNotificationDetailMetric("대기", pending + "건", pending ? "watch" : "muted"),
    renderNotificationDetailMetric("보류", suppressed + "건", suppressed ? "caution" : "muted"),
    renderNotificationDetailMetric("실패", failed + "건", failed ? "danger" : "muted"),
    renderNotificationDetailMetric("재시도", staleCount + "건", staleCount ? "caution" : "muted"),
    '</div>',
    '<div class="work-detail-grid notification-diagnostics-grid">',
    cards.map(renderStrategyOverviewActionCard).join(""),
    '</div>',
    '<div class="rule-strip"><span>알림 진단 화면은 원인을 찾는 곳입니다. 설정값 전체를 훑기보다 보류·실패·전달 채널 순서로 확인합니다.</span><span>변경 저장은 상세 레이어 안의 저장 버튼에서 처리합니다.</span></div>',
    '</article>'
  ].join("");
}

function renderAdminMessagePanel() {
  var rules = alertRules();
  var cadences = alertCadenceMinutes();
  var groups = alertRuleGroups();
  var editorOpen = Boolean(notificationsState.notificationPolicyEditorOpen);
  return [
    '<article class="panel admin-message-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Messages</p>',
    '<h2>메시지 타입별 알림</h2>',
    '<p class="subtle">메시지 타입을 그룹별로 확인하고 상세 편집은 레이어로 띄워 수정합니다.</p>',
    '</div>',
    '<div class="settings-actions">',
    '<button class="text-button compact" data-action="expand-message-types">그룹 펼치기</button>',
    '<button class="text-button compact" data-action="collapse-message-types">전체 접기</button>',
    '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
    '</div>',
    '</div>',
    '<div class="settings-body">',
    notificationsState.messageSchedulesError ? '<p class="form-error">' + escapeHtml(notificationsState.messageSchedulesError) + '</p>' : '',
    notificationsState.notificationRulesError ? '<p class="form-error">' + escapeHtml(notificationsState.notificationRulesError) + '</p>' : '',
    notificationsState.notificationRulesSaved ? '<p class="lab-message">알림 룰을 저장했습니다.</p>' : '',
    renderNotificationPolicyListScreen(groups, rules, cadences),
    '</div>',
    editorOpen ? renderNotificationPolicyEditorLayer() : '',
    '</article>'
  ].join("");
}

function renderNotificationPolicyListScreen(groups, rules, cadences) {
  return [
    '<div class="notification-policy-list-screen">',
    '<div class="flow-title"><div><strong>사용 중인 알림 타입</strong><span>투자 근거 신호와 레거시 타입은 인사이트 합성 입력으로만 유지하고, 여기서는 직접 관리하는 타입만 편집합니다.</span></div></div>',
    '<div class="admin-message-group-list">',
    groups.map(function (group) {
      return renderAdminMessageGroup(group, rules, cadences);
    }).join(""),
    '</div>',
    '</div>'
  ].join("");
}

function alertRuleGroups() {
  var order = [];
  var byGroup = {};
  notificationPolicyCatalog().forEach(function (rule) {
    var group = rule.group || "기타";
    if (!byGroup[group]) {
      byGroup[group] = [];
      order.push(group);
    }
    byGroup[group].push(rule);
  });
  return order.map(function (group) {
    return { name: group, rules: byGroup[group] };
  });
}

function notificationGroupExpanded(group) {
  return Boolean(notificationsState.notificationExpandedGroups && notificationsState.notificationExpandedGroups[group]);
}

function renderAdminMessageGroup(group, rules, cadences) {
  var expanded = notificationGroupExpanded(group.name);
  var enabledCount = group.rules.filter(function (rule) {
    return enabledAlertRule(rules, rule.key);
  }).length;
  var selectedInGroup = group.rules.some(function (rule) {
    return rule.key === activeNotificationRule().key;
  });
  return [
    '<section class="admin-message-group">',
    '<button class="admin-message-group-head" type="button" data-message-group-toggle="' + escapeHtml(group.name) + '" aria-expanded="' + escapeHtml(expanded ? "true" : "false") + '">',
    '<span><strong>' + escapeHtml(group.name) + '</strong><em>' + escapeHtml(enabledCount + "/" + group.rules.length + "개 사용" + (selectedInGroup ? " · 선택됨" : "")) + '</em></span>',
    '<b>' + escapeHtml(expanded ? "접기" : "보기") + '</b>',
    '</button>',
    expanded ? '<div class="admin-message-list">' + latestChangedFirst(group.rules, function (rule) {
      return recordChangedAt(rule, recordChangedAt(notificationTemplateForEdit(rule.key)));
    }).map(function (rule) {
      return renderAdminMessageRow(
        rule,
        enabledAlertRule(rules, rule.key),
        cadences[rule.key],
        messageScheduleByType(rule.key),
        notificationTemplateForEdit(rule.key)
      );
    }).join("") + '</div>' : '',
    '</section>'
  ].join("");
}

function notificationTypeExpanded(messageType) {
  return Boolean(notificationsState.notificationExpandedTypes && notificationsState.notificationExpandedTypes[messageType]);
}

function renderAdminMessageRow(rule, checked, cadence, schedule, template) {
  var ruleId = "alert-rule-" + String(rule.key || "").replace(/[^A-Za-z0-9_-]/g, "-");
  var active = activeNotificationRule().key === rule.key;
  var editing = active && notificationsState.notificationPolicyEditorOpen;
  return [
    '<div class="admin-message-row ' + (active ? "active" : "collapsed") + '">',
    '<input id="' + escapeHtml(ruleId) + '" type="checkbox" data-alert-rule="' + escapeHtml(rule.key) + '"' + (checked ? " checked" : "") + ' />',
    '<label class="admin-message-main" for="' + escapeHtml(ruleId) + '">',
    '<strong>' + escapeHtml(labelWithNotificationIcon(rule.key, rule.label)) + '</strong>',
    '<em>' + escapeHtml(rule.group + " · " + rule.description) + '</em>',
    renderRecordChangedAt(rule, recordChangedAt(template)),
    '</label>',
    '<span class="admin-cadence-field">',
    '<input data-alert-cadence="' + escapeHtml(rule.key) + '" type="number" min="10" step="10" value="' + escapeHtml(cadence) + '" />',
    '<b>분</b>',
    '</span>',
    '<button class="admin-message-toggle" type="button" data-message-select="' + escapeHtml(rule.key) + '" aria-pressed="' + escapeHtml(editing ? "true" : "false") + '">',
    '<span>' + escapeHtml(editing ? "편집 중" : "상세 편집") + '</span>',
    '</button>',
    '<div class="admin-message-schedule">',
    renderMessageScheduleSummary(schedule, true),
    '</div>',
    '</div>'
  ].join("");
}

function notificationRuleByKey(key) {
  return notificationPolicyCatalog().filter(function (rule) {
    return rule.key === key;
  })[0] || null;
}

function activeNotificationRule() {
  var selected = notificationRuleByKey(notificationsState.activeNotificationMessageType);
  return selected || notificationRuleByKey("investmentInsight") || notificationPolicyCatalog()[0];
}

function renderNotificationPolicyDetailPanel() {
  var rule = activeNotificationRule();
  var template = notificationTemplateForEdit(rule.key);
  var schedule = messageScheduleByType(rule.key);
  return [
    '<aside class="notification-policy-detail" aria-label="선택한 알림 상세">',
    '<div class="notification-policy-detail-head">',
    '<div>',
    '<p class="label">Selected Policy</p>',
    '<h3>' + escapeHtml(labelWithNotificationIcon(rule.key, rule.label)) + '</h3>',
    '<span>' + escapeHtml(rule.group + " · " + rule.description) + '</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(scheduleStatusClass(schedule)) + '">' + escapeHtml(scheduleStatusLabel(schedule)) + '</span>',
    '</div>',
    '<div class="notification-policy-schedule">',
    renderMessageScheduleSummary(schedule),
    '</div>',
    '<div class="notification-policy-editor">',
    renderNotificationTemplateRow(template, { policyDetail: true }),
    renderNotificationRuleEditor(rule.key, { inline: true }),
    '</div>',
    '</aside>'
  ].join("");
}

function renderNotificationPolicyEditorLayer() {
  return [
    '<div class="notification-policy-modal-backdrop">',
    '<section class="notification-policy-editor-layer" role="dialog" aria-modal="true" aria-label="알림 상세 편집">',
    '<div class="notification-policy-modal-head">',
    '<div>',
    '<p class="label">Edit Policy</p>',
    '<h2>알림 상세 편집</h2>',
    '<span>템플릿, 발송 기준, 반복 억제 조건을 수정합니다.</span>',
    '</div>',
    '<button class="icon-button" type="button" data-notification-editor-close aria-label="상세 편집 닫기">&times;</button>',
    '</div>',
    renderNotificationPolicyDetailPanel(),
    '</section>',
    '</div>'
  ].join("");
}

export { activeNotificationRule, alertRuleGroups, notificationEnabledRuleCount, notificationGroupExpanded, notificationRuleByKey, notificationTemplateItems, notificationTypeExpanded, renderAdminMessagePanel, renderNotificationCandidatePanel, renderNotificationDiagnosticsPanel };
