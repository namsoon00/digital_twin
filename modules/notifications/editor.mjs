import { textWithDisplaySymbol } from "../instruments/catalog.mjs";
import { renderAlertThresholdInput } from "./alerts-view.mjs";
import { alertThresholds } from "./alerts.mjs";
import { canSendNotificationTemplateTest, defaultMarketHoursSessions, defaultNotificationRuleMarketHoursMarkets, defaultNotificationRuleSimilarityFields, defaultNotificationTemplates, isAlertTemplateType, messageScheduleByType, notificationRuleForEdit, notificationTemplateVariables, renderNotificationTemplatePreviewText } from "./policy.mjs";
import { activeNotificationRule, notificationTemplateItems } from "./workspace.mjs";
import { settingsSaveButtonClass, settingsSaveButtonLabel, settingsSaveDisabledAttr } from "../settings/fields.mjs";
import { formatClock, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { alertRuleCatalog, alertThresholdCatalog, labelWithNotificationIcon } from "../shell/catalog.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { settingsState } from "../state/settings.mjs";

function notificationTemplateLabel(messageType) {
  var found = alertRuleCatalog.filter(function (rule) { return rule.key === messageType; })[0];
  if (found) return found.label;
  var labels = {
    default: "기본 템플릿",
    modelReview: "모델 리뷰",
    workHandoff: "작업 완료",
    notification: "일반 알림"
  };
  return labels[messageType] || messageType;
}

function scheduleStatusLabel(schedule) {
  if (!schedule) return "이력 없음";
  if (schedule.status === "event") return "이벤트 발생 시";
  if (schedule.status === "disabled") return "꺼짐";
  if (schedule.status === "waiting") return "대기 중";
  if (schedule.status === "ready") return "발송 가능";
  return schedule.status || "확인 필요";
}

function scheduleStatusClass(schedule) {
  if (!schedule) return "muted";
  if (schedule.status === "event") return "watch";
  if (schedule.status === "disabled") return "muted";
  if (schedule.status === "waiting") return "hold";
  if (schedule.status === "ready") return "watch";
  return "muted";
}

function scheduleTimeText(value) {
  return value ? formatClock(value) : "-";
}

function scheduleTargetText(targets) {
  if (!Array.isArray(targets) || !targets.length) return "최근 실제 발송 대상 없음";
  return targets.slice(0, 3).map(function (item) {
    var target = item.target ? textWithDisplaySymbol(item.target, item.target, item) : "전체";
    return target + " · " + scheduleTimeText(item.sentAt);
  }).join(" / ");
}

function renderMessageScheduleSummary(schedule, compact) {
  if (!schedule) {
    return [
      '<div class="message-schedule-summary muted">',
      '<span>로컬 서버에서 실제 발송 이력을 읽으면 표시됩니다.</span>',
      '</div>'
    ].join("");
  }
  return [
    '<div class="message-schedule-summary">',
    '<span class="tone-chip ' + escapeHtml(scheduleStatusClass(schedule)) + '">' + escapeHtml(scheduleStatusLabel(schedule)) + '</span>',
    '<span>' + escapeHtml(schedule.cadenceText || "조건 충족 시 발송") + '</span>',
    '<span>마지막 ' + escapeHtml(scheduleTimeText(schedule.lastSentAt)) + '</span>',
    '<span>다음 가능 ' + escapeHtml(scheduleTimeText(schedule.nextEligibleAt)) + '</span>',
    '</div>',
    compact ? '' :
    '<div class="message-schedule-detail">',
    '<strong>언제 오나</strong>',
    '<p>' + escapeHtml(schedule.triggerSummary || "조건이 실제 데이터에서 충족될 때 보냅니다.") + '</p>',
    '<em>' + escapeHtml(scheduleTargetText(schedule.recentTargets)) + '</em>',
    '</div>'
  ].join("");
}

function notificationRuleConditionTypeLabel(type) {
  var found = (notificationsState.notificationRuleConditionTypes || []).filter(function (item) {
    return item.type === type;
  })[0];
  return found ? found.label : type;
}

function notificationRuleConditionValue(condition) {
  if (condition.type === "text_contains_any" || condition.type === "context_contains_any") {
    return Array.isArray(condition.terms) ? condition.terms.join(", ") : "";
  }
  return String(condition.value || "");
}

function renderNotificationRuleCondition(messageType, condition, disabled) {
  var conditionId = String(condition.id || "");
  var fieldNeeded = /^context_/.test(condition.type || "");
  var valueNeeded = ["context_equals", "context_number_gte", "context_number_lte"].indexOf(condition.type) >= 0;
  var termsNeeded = condition.type === "text_contains_any" || condition.type === "context_contains_any";
  return [
    '<div class="notification-rule-condition">',
    '<label class="notification-rule-condition-main">',
    '<input type="checkbox" data-notification-rule-condition-enabled="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '"' + (condition.enabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' />',
    '<span><strong>' + escapeHtml(condition.label || conditionId) + '</strong><em>' + escapeHtml(notificationRuleConditionTypeLabel(condition.type)) + '</em></span>',
    '</label>',
    fieldNeeded ? '<label><span>필드</span><input type="text" data-notification-rule-condition-field="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(condition.field || "") + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
    termsNeeded ? '<label class="notification-rule-condition-value"><span>단어</span><textarea rows="2" data-notification-rule-condition-value="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '"' + (disabled ? " disabled" : "") + '>' + escapeHtml(notificationRuleConditionValue(condition)) + '</textarea></label>' : '',
    valueNeeded ? '<label class="notification-rule-condition-value"><span>값</span><input type="text" data-notification-rule-condition-value="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(notificationRuleConditionValue(condition)) + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
    '</div>'
  ].join("");
}

function notificationRuleSimilarityFieldsText(rule) {
  return (Array.isArray(rule.similarityFields) ? rule.similarityFields : defaultNotificationRuleSimilarityFields()).join(", ");
}

function notificationRuleBypassTypeLabel(type) {
  var labels = {
    severity_upgrade: "등급 상승",
    review_level_upgrade: "확인 단계 상승",
    list_new_items_gte: "새 근거 추가",
    profit_loss_worsened_lte: "손익률 악화",
    profit_loss_improved_gte: "손익률 개선",
    ma60_crossed_below: "60일 평균 아래로 전환",
    ma60_crossed_above: "60일 평균 위로 회복",
    field_changed: "상태 변경",
    field_changed_any: "권장 대응 변경",
    inference_state_changed: "추론 상태 변경",
    abs_number_delta_gte: "절대값 차이 이상",
    number_delta_gte: "숫자 증가 이상",
    number_delta_lte: "숫자 감소 이상",
    number_multiplier_gte: "배수 증가 이상",
    baseline_age_gte: "초기 관계 유지 시간"
  };
  return labels[type] || type || "반복 예외";
}

function notificationRuleBypassNeedsField(type) {
  return ["review_level_upgrade", "list_new_items_gte", "field_changed", "field_changed_any", "abs_number_delta_gte", "number_delta_gte", "number_delta_lte", "number_multiplier_gte"].indexOf(type) >= 0;
}

function notificationRuleBypassNeedsValue(type) {
  return ["list_new_items_gte", "profit_loss_worsened_lte", "profit_loss_improved_gte", "ma60_crossed_below", "ma60_crossed_above", "abs_number_delta_gte", "number_delta_gte", "number_delta_lte", "number_multiplier_gte", "baseline_age_gte"].indexOf(type) >= 0;
}

function renderNotificationBypassCondition(messageType, condition, disabled) {
  var conditionId = String(condition.id || "");
  var type = String(condition.type || "");
  return [
    '<div class="notification-rule-bypass-condition">',
    '<label class="notification-rule-condition-main">',
    '<input type="checkbox" data-notification-rule-bypass-enabled="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '"' + (condition.enabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' />',
    '<span><strong>' + escapeHtml(condition.label || conditionId) + '</strong><em>' + escapeHtml(notificationRuleBypassTypeLabel(type)) + '</em>' + (condition.description ? '<small>' + escapeHtml(condition.description) + '</small>' : '') + '</span>',
    '</label>',
    notificationRuleBypassNeedsField(type) ? '<label><span>필드</span><input type="text" data-notification-rule-bypass-field="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(condition.field || "") + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
    notificationRuleBypassNeedsValue(type) ? '<label><span>기준값</span><input type="number" step="0.1" data-notification-rule-bypass-value="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(condition.value) + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
    '</div>'
  ].join("");
}

function renderNotificationBypassConditionsEditor(messageType, rule, disabled) {
  var conditions = Array.isArray(rule.similarityBypassConditions) ? rule.similarityBypassConditions : [];
  if (!conditions.length) return "";
  return [
    '<div class="notification-rule-bypass-list">',
    '<div class="notification-rule-head notification-rule-subhead">',
    '<div><strong>반복 예외 조건</strong><span>실제 값이나 근거가 의미 있게 바뀌면 반복 보류를 해제합니다.</span></div>',
    '</div>',
    conditions.map(function (condition) {
      return renderNotificationBypassCondition(messageType, condition, disabled);
    }).join(""),
    '</div>'
  ].join("");
}

function renderNotificationSimilarityEditor(messageType, rule, disabled) {
  var summary = rule.similarityEnabled === false
    ? "유사 억제 꺼짐"
    : String(rule.similarityWindowMinutes || 0) + "분 안에 같은 내용이면 보류";
  return [
    '<div class="notification-rule-similarity">',
    '<div class="notification-rule-head notification-rule-subhead">',
    '<div><strong>유사 메시지</strong><span>' + escapeHtml(summary) + '</span></div>',
    '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-similarity-enabled="' + escapeHtml(messageType) + '"' + (rule.similarityEnabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 억제</label>',
    '</div>',
    '<div class="notification-rule-state-grid">',
    '<label><span>억제 시간</span><input type="number" min="0" max="10080" step="10" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="similarityWindowMinutes" value="' + escapeHtml(rule.similarityWindowMinutes) + '"' + (disabled ? " disabled" : "") + ' /></label>',
    '</div>',
    '<label class="notification-rule-fields"><span>fingerprint 필드</span><textarea rows="2" data-notification-rule-fields="' + escapeHtml(messageType) + '"' + (disabled ? " disabled" : "") + '>' + escapeHtml(notificationRuleSimilarityFieldsText(rule)) + '</textarea></label>',
    renderNotificationBypassConditionsEditor(messageType, rule, disabled),
    '</div>'
  ].join("");
}

function renderNotificationStateCooldownEditor(messageType, rule, disabled) {
  var summary = rule.stateCooldownEnabled === false
    ? "상태 지속 억제 꺼짐"
    : "같은 임계값 상태는 " + String(rule.stateCooldownMinutes || 0) + "분 뒤 요약만 발송";
  return [
    '<div class="notification-rule-state">',
    '<div class="notification-rule-head notification-rule-subhead">',
    '<div><strong>상태 지속 억제</strong><span>' + escapeHtml(summary) + '</span></div>',
    '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-state-enabled="' + escapeHtml(messageType) + '"' + (rule.stateCooldownEnabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 적용</label>',
    '</div>',
    '<div class="notification-rule-state-grid">',
    '<label><span>즉시 변화 재알림</span><input type="number" min="0" max="10080" step="5" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="immediateCooldownMinutes" value="' + escapeHtml(rule.immediateCooldownMinutes) + '"' + (disabled ? " disabled" : "") + ' /></label>',
    '<label><span>중요 근거 재알림</span><input type="number" min="0" max="10080" step="10" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="materialCooldownMinutes" value="' + escapeHtml(rule.materialCooldownMinutes) + '"' + (disabled ? " disabled" : "") + ' /></label>',
    '<label><span>같은 상태 요약</span><input type="number" min="0" max="10080" step="10" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="stateCooldownMinutes" value="' + escapeHtml(rule.stateCooldownMinutes) + '"' + (disabled ? " disabled" : "") + ' /></label>',
    '</div>',
    '<p class="subtle">즉시 변화는 손익·행동·주요 기준선 전환, 중요 근거는 새 뉴스·공시와 관계 변화입니다. 같은 상태는 요약 시간이 지난 뒤 한 번만 다시 알리고, 참고 정보는 웹 이력에 저장합니다.</p>',
    '</div>'
  ].join("");
}

function marketHoursSessionSummary(session) {
  if (Array.isArray(session.sessions) && session.sessions.length) {
    return session.sessions.map(function (item) {
      return String(item.label || "") + " " + String(item.openTime || "") + "-" + String(item.closeTime || "");
    }).join(" · ") + " " + String(session.timezone || "");
  }
  return String(session.openTime || "") + "-" + String(session.closeTime || "") + " " + String(session.timezone || "");
}

function renderNotificationMarketHoursEditor(messageType, rule, disabled) {
  var sessions = notificationsState.notificationMarketHoursSessions.length ? notificationsState.notificationMarketHoursSessions : defaultMarketHoursSessions();
  var selected = Array.isArray(rule.marketHoursMarkets) ? rule.marketHoursMarkets : defaultNotificationRuleMarketHoursMarkets(messageType);
  selected = selected.map(function (market) { return String(market || "").trim().toUpperCase(); });
  var summary = rule.marketHoursEnabled === false
    ? "장 상태 기록 꺼짐"
    : (selected.length ? selected.join(", ") : "시장 미선택") + " · 장 마감에도 발송";
  return [
    '<div class="notification-rule-market-hours">',
    '<div class="notification-rule-head notification-rule-subhead">',
    '<div><strong>장 상태 참고</strong><span>' + escapeHtml(summary) + '</span></div>',
    '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-market-hours-enabled="' + escapeHtml(messageType) + '"' + (rule.marketHoursEnabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 기록</label>',
    '</div>',
    '<p class="subtle">장 운영 상태와 표시 가격의 기준만 기록하며 알림 발송을 보류하지 않습니다.</p>',
    '<div class="notification-rule-market-list">',
    sessions.map(function (session) {
      var market = String(session.market || "").toUpperCase();
      return [
        '<label class="notification-rule-market-option">',
        '<input type="checkbox" data-notification-rule-market-hours-market="' + escapeHtml(messageType) + '" data-market="' + escapeHtml(market) + '"' + (selected.indexOf(market) >= 0 ? " checked" : "") + (disabled ? " disabled" : "") + ' />',
        '<span><strong>' + escapeHtml(session.label || market) + '</strong><em>' + escapeHtml(marketHoursSessionSummary(session)) + '</em></span>',
        '</label>'
      ].join("");
    }).join(""),
    '</div>',
    '</div>'
  ].join("");
}

function renderNotificationRuleEditor(messageType, options) {
  options = options || {};
  var rule = notificationRuleForEdit(messageType);
  var disabled = settingsState.serverSettingsLocked || isStaticPreviewHost();
  var compact = Boolean(options.compact);
  var summary = rule.enabled === false
    ? "상태 확인을 건너뛰고 이벤트를 그대로 전달합니다."
    : "확인 단계, 자료 상태, 새 변화, AI 검증 상태를 함께 확인합니다.";
  return [
    '<div class="notification-rule-editor' + (options.inline ? " admin-message-rule" : "") + '">',
    '<div class="notification-rule-head">',
    '<div><strong>상태 기반 발송 규칙</strong><span>' + escapeHtml(summary) + '</span></div>',
    '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-enabled="' + escapeHtml(messageType) + '"' + (rule.enabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 적용</label>',
    '</div>',
    '<div class="notification-rule-state-grid">',
    '<label><span>관계 판단</span><input type="text" value="TypeDB 추론 상태" disabled /></label>',
    '<label><span>자료 확인</span><input type="text" value="충분 · 일부 · 부족 · 사용 불가" disabled /></label>',
    '<label><span>AI 검증</span><input type="text" value="검증 완료 · 조건부 · 판단 보류" disabled /></label>',
    '</div>',
    compact ? '<p class="subtle">유사 메시지, 상태 지속 억제, 장 상태 참고, 세부 조건은 진단 탭에서 조정합니다.</p>' : renderNotificationSimilarityEditor(messageType, rule, disabled),
    compact ? '' : renderNotificationStateCooldownEditor(messageType, rule, disabled),
    compact ? '' : renderNotificationMarketHoursEditor(messageType, rule, disabled),
    compact ? '' : '<div class="notification-rule-condition-list">',
    compact ? '' : (rule.conditions || []).map(function (condition) {
      return renderNotificationRuleCondition(messageType, condition, disabled);
    }).join(""),
    compact ? '' : '</div>',
    '<div class="settings-actions">',
    '<button class="text-button primary" data-rule-save="' + escapeHtml(messageType) + '"' + (disabled || notificationsState.notificationRulesLoading ? ' disabled' : '') + '>룰 저장</button>',
    '<button class="text-button" data-rule-reset="' + escapeHtml(messageType) + '"' + (disabled || notificationsState.notificationRulesLoading ? ' disabled' : '') + '>기본값</button>',
    '</div>',
    '</div>'
  ].join("");
}

function renderNotificationTemplateManagerPanel() {
  var templates = notificationTemplateItems();
  var editorOpen = Boolean(notificationsState.notificationTemplateEditorOpen);
  return [
    '<article class="panel notification-template-manager-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Templates</p>',
    '<h2>알림 템플릿</h2>',
    '<p class="subtle">템플릿 목록을 유지한 채 본문, 변수, 미리보기와 테스트 발송은 레이어에서 수정합니다.</p>',
    '</div>',
    '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
    '</div>',
    '<div class="settings-body">',
    notificationsState.notificationTemplatesError ? '<p class="form-error">' + escapeHtml(notificationsState.notificationTemplatesError) + '</p>' : '',
    notificationsState.notificationTemplatesSaved ? '<p class="lab-message">알림 템플릿을 저장했습니다.</p>' : '',
    '<div class="template-variable-row admin-template-variable-row">',
    notificationTemplateVariables().map(function (name) {
      return '<span class="chip">{' + escapeHtml(name) + '}</span>';
    }).join(""),
    '</div>',
    '<div class="notification-template-workbench notification-template-list-workbench">',
    '<div class="notification-template-index">',
    '<div class="flow-title"><div><strong>템플릿 목록</strong><span>수정을 누르면 목록 위로 템플릿 편집 레이어가 열립니다.</span></div></div>',
    '<div class="notification-template-select-list">',
    templates.map(renderNotificationTemplateSelector).join(""),
    '</div>',
    '</div>',
    '</div>',
    '</div>',
    editorOpen ? renderNotificationTemplateEditorLayer() : '',
    '</article>'
  ].join("");
}

function activeNotificationTemplate() {
  var templates = notificationTemplateItems();
  var selected = templates.filter(function (item) {
    return item.messageType === notificationsState.activeNotificationTemplateType;
  })[0];
  if (selected) return selected;
  return templates.filter(function (item) { return item.messageType === "investmentInsight"; })[0] || templates[0] || defaultNotificationTemplates()[0];
}

function renderNotificationTemplateSelector(item) {
  var active = activeNotificationTemplate().messageType === item.messageType;
  var editing = active && notificationsState.notificationTemplateEditorOpen;
  var kind = isAlertTemplateType(item.messageType) ? "알림" : "시스템";
  return [
    '<button type="button" class="notification-template-select-row' + (editing ? " active" : "") + '" data-template-select="' + escapeHtml(item.messageType || "") + '" aria-pressed="' + escapeHtml(editing ? "true" : "false") + '">',
    '<span><strong>' + escapeHtml(labelWithNotificationIcon(item.messageType, notificationTemplateLabel(item.messageType))) + '</strong><em>' + escapeHtml(kind + " · " + (item.messageType || "-")) + '</em></span>',
    '<b>' + escapeHtml(editing ? "편집 중" : "수정") + '</b>',
    '</button>'
  ].join("");
}

function renderNotificationTemplateEditorLayer() {
  var selected = activeNotificationTemplate();
  return [
    '<div class="notification-template-modal-backdrop">',
    '<section class="notification-template-editor-layer" role="dialog" aria-modal="true" aria-label="템플릿 상세 편집">',
    '<div class="notification-template-modal-head">',
    '<div>',
    '<p class="label">' + escapeHtml(isAlertTemplateType(selected.messageType) ? "Alert Template" : "System Template") + '</p>',
    '<h2>' + escapeHtml(labelWithNotificationIcon(selected.messageType, notificationTemplateLabel(selected.messageType))) + '</h2>',
    '<span>' + escapeHtml(selected.messageType || "-") + (selected.description ? " · " + selected.description : "") + '</span>',
    '</div>',
    '<button class="icon-button" type="button" data-notification-template-editor-close aria-label="템플릿 편집 닫기">&times;</button>',
    '</div>',
    renderNotificationTemplateRow(selected, { templateDetail: true }),
    '</section>',
    '</div>'
  ].join("");
}

function renderNotificationTemplateRow(item, options) {
  options = options || {};
  var detailMode = Boolean(options.inline || options.policyDetail || options.templateDetail);
  var disabled = settingsState.serverSettingsLocked || isStaticPreviewHost();
  var preview = renderNotificationTemplatePreviewText(item.template || "", item.messageType);
  var canTest = canSendNotificationTemplateTest(item.messageType);
  var sending = notificationsState.notificationTemplateSending === item.messageType;
  var schedule = messageScheduleByType(item.messageType);
  return [
    '<div class="notification-template-row' + (options.inline ? " admin-message-template" : "") + (options.policyDetail ? " notification-policy-template" : "") + (options.templateDetail ? " notification-template-detail-row" : "") + '">',
    '<div class="notification-template-meta">',
    '<strong>' + escapeHtml(detailMode ? "템플릿" : notificationTemplateLabel(item.messageType)) + '</strong>',
    '<span>' + escapeHtml(item.messageType || "-") + (item.description && !detailMode ? " · " + escapeHtml(item.description) : "") + '</span>',
    renderRecordChangedAt(item),
    detailMode ? '' : '<div class="template-schedule-compact">' + renderMessageScheduleSummary(schedule) + '</div>',
    '</div>',
    '<textarea data-notification-template="' + escapeHtml(item.messageType || "") + '" rows="3"' + (disabled ? " disabled" : "") + '>' + escapeHtml(item.template || "") + '</textarea>',
    '<div class="settings-actions">',
    '<button class="text-button primary" data-template-save="' + escapeHtml(item.messageType || "") + '"' + (disabled || notificationsState.notificationTemplatesLoading ? ' disabled' : '') + '>템플릿 저장</button>',
    '<button class="text-button" data-template-reset="' + escapeHtml(item.messageType || "") + '"' + (disabled || notificationsState.notificationTemplatesLoading ? ' disabled' : '') + '>기본값</button>',
    canTest ? '<button class="text-button" data-template-test-send="' + escapeHtml(item.messageType || "") + '"' + (disabled || notificationsState.notificationTemplatesLoading || notificationsState.notificationTemplateSending ? ' disabled' : '') + '>' + escapeHtml(sending ? "발송 중" : "실제 데이터 발송") + '</button>' : '',
    '</div>',
    '<div class="notification-template-preview">',
    '<strong>미리보기</strong>',
    '<pre data-template-preview="' + escapeHtml(item.messageType || "") + '">' + escapeHtml(preview) + '</pre>',
    '</div>',
    (detailMode || options.templateOnly) ? '' : renderNotificationRuleEditor(item.messageType || "", { inline: true }),
    '</div>'
  ].join("");
}

function renderNotificationAdvancedRulePanel() {
  var rule = activeNotificationRule();
  return [
    '<article class="panel notification-advanced-rule-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Diagnostics Rule</p>',
    '<h2>반복·장 상태</h2>',
    '<p class="subtle">정책 탭에서 선택한 타입의 유사 메시지, 장 상태 참고, 조건 변화를 진단합니다.</p>',
    '</div>',
    '<span class="tone-chip hold">' + escapeHtml(rule.label) + '</span>',
    '</div>',
    '<div class="settings-body">',
    renderNotificationRuleEditor(rule.key, { inline: true }),
    '</div>',
    '</article>'
  ].join("");
}

function renderNotificationThresholdPanel() {
  var thresholds = alertThresholds();
  return [
    '<article class="panel notification-threshold-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Advanced</p>',
    '<h2>알림 임계값</h2>',
    '<p class="subtle">모델, 실시간, 외부 데이터 알림이 발생하는 기준입니다. 자주 바꾸지 않는 값만 이곳에 모읍니다.</p>',
    '</div>',
    '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
    '</div>',
    '<div class="alert-threshold-section">',
    '<div class="alert-threshold-grid">',
    alertThresholdCatalog.map(function (item) {
      return renderAlertThresholdInput(item, thresholds[item.key]);
    }).join(""),
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

export { notificationTemplateLabel, renderMessageScheduleSummary, renderNotificationAdvancedRulePanel, renderNotificationRuleEditor, renderNotificationTemplateManagerPanel, renderNotificationTemplateRow, renderNotificationThresholdPanel, scheduleStatusClass, scheduleStatusLabel };
