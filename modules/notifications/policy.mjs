import { notificationTemplateLabel } from "./editor.mjs";
import { loadNotificationJobs, loadNotificationSchedules } from "./requests.mjs";
import { notificationTemplatePreviewContext } from "./template-preview.mjs";
import { render } from "../render/scheduler.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { alertRuleCatalog, notificationPolicyCatalog, visibleNotificationTemplateType } from "../shell/catalog.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { settingsState } from "../state/settings.mjs";

function defaultNotificationTemplates() {
  var richTemplate = "{telegramMessage}";
  var items = [
    {
      messageType: "default",
      template: richTemplate,
      description: "기본 알림 템플릿"
    }
  ];
  notificationPolicyCatalog().forEach(function (rule) {
    items.push({
      messageType: rule.key,
      template: richTemplate,
      description: rule.label + " · " + rule.description
    });
  });
  [
    { messageType: "modelReview", template: "{body}", description: "비동기 모델 리뷰 결과" },
    { messageType: "workHandoff", template: "{body}", description: "작업 완료 핸드오프" },
    { messageType: "notification", template: "{body}", description: "일반 텍스트 알림" }
  ].forEach(function (item) {
    items.push(item);
  });
  var seen = {};
  return items.filter(function (item) {
    if (!visibleNotificationTemplateType(item.messageType)) return false;
    if (seen[item.messageType]) return false;
    seen[item.messageType] = true;
    return true;
  }).map(function (item) {
    return Object.assign({ enabled: true, updatedAt: "" }, item);
  });
}

function defaultNotificationRuleConditionTypes() {
  return [
    { type: "text_contains_any", label: "메시지에 단어 포함" },
    { type: "context_contains_any", label: "정보 필드에 단어 포함" },
    { type: "context_equals", label: "정보 값 일치" },
    { type: "context_present", label: "정보 값 존재" },
    { type: "context_number_gte", label: "정보 숫자 이상" },
    { type: "context_number_lte", label: "정보 숫자 이하" },
    { type: "always", label: "항상 적용" }
  ];
}

function defaultNotificationRuleConditions() {
  return [
    { id: "severity_alert", label: "주의 단계", type: "context_equals", field: "severity", value: "ALERT", terms: [], enabled: true },
    { id: "severity_watch", label: "관찰 단계", type: "context_equals", field: "severity", value: "WATCH", terms: [], enabled: true },
    { id: "has_symbol", label: "종목 지정", type: "context_present", field: "symbol", value: "", terms: [], enabled: true },
    { id: "important_terms", label: "중요 투자 내용", type: "context_contains_any", field: "notificationSignals", value: "", terms: ["important"], enabled: true },
    { id: "confirming_data", label: "확인 자료 포함", type: "context_contains_any", field: "notificationSignals", value: "", terms: ["confirmingData"], enabled: true },
    { id: "actionable_terms", label: "대응 확인 필요", type: "context_contains_any", field: "notificationSignals", value: "", terms: ["actionable"], enabled: true },
    { id: "body_present", label: "본문 있음", type: "context_present", field: "body", value: "", terms: [], enabled: true },
    { id: "status_noise", label: "상태성 반복 내용", type: "context_contains_any", field: "notificationSignals", value: "", terms: ["statusNoise"], enabled: true }
  ];
}

function defaultNotificationRuleSimilarityEnabled(messageType) {
  return ["default", "modelReview", "workHandoff", "notification"].indexOf(String(messageType || "")) < 0;
}

function defaultNotificationRuleSimilarityWindow(messageType) {
  var type = String(messageType || "");
  if (type === "investmentInsight") return 180;
  if (["holdingTiming", "monitorHeartbeat", "externalEquityMove", "externalCryptoMove"].indexOf(type) >= 0) return 360;
  if (["watchlistQuotePending", "externalDataConnection"].indexOf(type) >= 0) return 180;
  if (["monitorPnlChange", "monitorValueChange", "monitorTrendChange", "monitorCashChange"].indexOf(type) >= 0) return 60;
  return 120;
}

function defaultNotificationRuleSimilarityBypassConditions(messageType) {
  var type = String(messageType || "");
  if (type === "externalEquityMove") {
    return [
      { id: "severity_upgrade", label: "등급 상승", type: "severity_upgrade", field: "", value: "", enabled: true, description: "관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다." },
      { id: "change_abs_delta", label: "변동률 추가 확대", type: "abs_number_delta_gte", field: "changePercent", value: 2, enabled: true, description: "이전 유사 알림보다 변동률 절대값이 기준 %p 이상 커지면 보냅니다." },
      { id: "volume_multiplier", label: "거래량 급증", type: "number_multiplier_gte", field: "volume", value: 1.5, enabled: true, description: "이전 유사 알림보다 거래량이 기준 배수 이상 커지면 보냅니다." }
    ];
  }
  if (type === "externalCryptoMove") {
    return [
      { id: "severity_upgrade", label: "등급 상승", type: "severity_upgrade", field: "", value: "", enabled: true, description: "관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다." },
      { id: "change_24h_abs_delta", label: "24시간 변동 확대", type: "abs_number_delta_gte", field: "change24h", value: 2, enabled: true, description: "이전 유사 알림보다 24시간 변동률 절대값이 기준 %p 이상 커지면 보냅니다." },
      { id: "change_7d_abs_delta", label: "7일 변동 확대", type: "abs_number_delta_gte", field: "change7d", value: 3, enabled: true, description: "이전 유사 알림보다 7일 변동률 절대값이 기준 %p 이상 커지면 보냅니다." },
      { id: "volume_multiplier", label: "거래액 급증", type: "number_multiplier_gte", field: "volume24h", value: 1.5, enabled: true, description: "이전 유사 알림보다 거래액이 기준 배수 이상 커지면 보냅니다." }
    ];
  }
  if (type === "holdingTiming") {
    return [
      { id: "severity_upgrade", label: "알림 단계 상승", type: "severity_upgrade", field: "", value: "", enabled: true, description: "관찰에서 주의처럼 단계가 올라가면 다시 보냅니다." },
      { id: "loss_rate_worsened", label: "손익률 추가 악화", type: "profit_loss_worsened_lte", field: "", value: 1, enabled: true, description: "이전 알림보다 손익률이 1%p 이상 나빠지면 다시 보냅니다." },
      { id: "loss_rate_improved", label: "손익률 개선", type: "profit_loss_improved_gte", field: "", value: 1, enabled: true, description: "이전 알림보다 손익률이 1%p 이상 좋아지면 다시 보냅니다." },
      { id: "holding_ma60_crossed_below", label: "60일 평균 아래로 전환", type: "ma60_crossed_below", field: "", value: 0, enabled: true, description: "가격이 60일 평균 아래로 내려가면 다시 보냅니다." },
      { id: "holding_ma60_crossed_above", label: "60일 평균 위로 회복", type: "ma60_crossed_above", field: "", value: 0, enabled: true, description: "가격이 60일 평균 위로 회복하면 다시 보냅니다." },
      { id: "holding_action_changed", label: "권장 대응 변경", type: "field_changed_any", field: "holdingDecision,holdingAction,actionLabel,activeInvestmentOpinion.actionLabel,activeInvestmentOpinion.action", value: "", enabled: true, description: "최종 대응이 바뀌면 다시 보냅니다." }
    ];
  }
  if (type === "investmentInsight") {
    return [
      { id: "insight_severity_upgrade", label: "알림 단계 상승", type: "severity_upgrade", field: "", value: "", enabled: true, description: "알림 단계가 올라가면 다시 보냅니다." },
      { id: "review_level_upgrade", label: "확인 단계 상승", type: "review_level_upgrade", field: "ontologyInsight.reviewLevel", value: "", enabled: true, description: "관찰에서 대응 준비처럼 확인 단계가 올라가면 다시 보냅니다." },
      { id: "new_source_signal", label: "새 근거 종류 추가", type: "list_new_items_gte", field: "sourceSignalTypes", value: 1, enabled: true, description: "이전에 없던 근거 종류가 추가되면 다시 보냅니다." },
      { id: "new_relation_event", label: "새 뉴스·공시 추가", type: "list_new_items_gte", field: "ontologyInsight.sourceEventKeys", value: 1, enabled: true, description: "새 뉴스나 공시 원문이 추가되면 다시 보냅니다." },
      { id: "insight_profit_loss_worsened", label: "손익률 추가 악화", type: "profit_loss_worsened_lte", field: "", value: 1, enabled: true, description: "손익률이 1%p 이상 나빠지면 다시 보냅니다." },
      { id: "insight_profit_loss_improved", label: "손익률 개선", type: "profit_loss_improved_gte", field: "", value: 1, enabled: true, description: "손익률이 1%p 이상 좋아지면 다시 보냅니다." },
      { id: "insight_ma60_crossed_below", label: "60일 평균 아래로 전환", type: "ma60_crossed_below", field: "", value: 0, enabled: true, description: "가격이 60일 평균 아래로 내려가면 다시 보냅니다." },
      { id: "insight_ma60_crossed_above", label: "60일 평균 위로 회복", type: "ma60_crossed_above", field: "", value: 0, enabled: true, description: "가격이 60일 평균 위로 회복하면 다시 보냅니다." },
      { id: "insight_action_changed", label: "권장 대응 변경", type: "field_changed_any", field: "notificationAiValidatedResponse.actionLabel,notificationAiValidatedResponse.action,aiOpinion.actionLabel,aiOpinion.action", value: "", enabled: true, description: "검증된 최종 대응이 바뀌면 다시 보냅니다." },
      { id: "insight_inference_state_changed", label: "추론 상태 변경", type: "inference_state_changed", field: "", value: "", enabled: true, description: "최종 행동, 확인 단계, 자료 상태 또는 AI 검증 상태가 바뀌면 다시 보냅니다." }
    ];
  }
  return [];
}

function defaultNotificationRuleSimilarityFields() {
  return ["messageType", "accountId", "symbol", "severity", "title"];
}

function defaultNotificationRuleStateCooldownEnabled(messageType) {
  return ["investmentInsight", "holdingTiming", "externalEquityMove", "externalCryptoMove"].indexOf(String(messageType || "")) >= 0;
}

function defaultNotificationRuleStateCooldownMinutes(messageType) {
  return defaultNotificationRuleStateCooldownEnabled(messageType) ? 360 : 0;
}

function defaultNotificationRuleImmediateCooldownMinutes(messageType) {
  return defaultNotificationRuleStateCooldownEnabled(messageType) ? 10 : 0;
}

function defaultNotificationRuleMaterialCooldownMinutes(messageType) {
  return defaultNotificationRuleStateCooldownEnabled(messageType) ? 60 : 0;
}

function defaultMarketHoursSessions() {
  return [
    {
      market: "KR",
      label: "국장",
      timezone: "Asia/Seoul",
      openTime: "08:00",
      closeTime: "20:00",
      weekdays: [0, 1, 2, 3, 4],
      sessions: [
        { key: "pre", label: "프리마켓", openTime: "08:00", closeTime: "08:50" },
        { key: "regular", label: "정규장", openTime: "09:00", closeTime: "15:30" },
        { key: "after", label: "애프터마켓", openTime: "15:30", closeTime: "20:00" }
      ]
    },
    {
      market: "US",
      label: "미장",
      timezone: "America/New_York",
      openTime: "04:00",
      closeTime: "20:00",
      weekdays: [0, 1, 2, 3, 4],
      sessions: [
        { key: "pre", label: "프리마켓", openTime: "04:00", closeTime: "09:30" },
        { key: "regular", label: "정규장", openTime: "09:30", closeTime: "16:00" },
        { key: "after", label: "애프터마켓", openTime: "16:00", closeTime: "20:00" }
      ]
    }
  ];
}

function defaultNotificationRuleMarketHoursEnabled(messageType) {
  return [
    "modelBuy",
    "investmentInsight",
    "modelSell",
    "watchlistBuyCandidate",
    "watchlistQuote",
    "watchlistQuotePending",
    "holdingTiming",
    "monitorPositionChange",
    "monitorPnlChange",
    "monitorValueChange",
    "monitorTrendChange",
    "monitorDecisionChange",
    "externalEquityMove",
    "externalDartDisclosure"
  ].indexOf(String(messageType || "")) >= 0;
}

function defaultNotificationRuleMarketHoursMarkets(messageType) {
  var type = String(messageType || "");
  if (type === "externalEquityMove") return ["US"];
  if (type === "externalDartDisclosure") return ["KR"];
  return defaultNotificationRuleMarketHoursEnabled(type) ? ["KR", "US"] : [];
}

function defaultNotificationRuleOffHoursMode(messageType) {
  var type = String(messageType || "");
  return ["investmentInsight", "externalDartDisclosure"].indexOf(type) >= 0
    ? "important_only"
    : "defer_until_open";
}

function defaultNotificationRule(messageType) {
  var type = String(messageType || "notification").trim() || "notification";
  return {
    messageType: type,
    enabled: true,
    conditions: defaultNotificationRuleConditions().map(function (condition) {
      return Object.assign({}, condition, { terms: (condition.terms || []).slice() });
    }),
    similarityEnabled: defaultNotificationRuleSimilarityEnabled(type),
    similarityWindowMinutes: defaultNotificationRuleSimilarityWindow(type),
    similarityBypassConditions: defaultNotificationRuleSimilarityBypassConditions(type),
    similarityFields: defaultNotificationRuleSimilarityFields(),
    stateCooldownEnabled: defaultNotificationRuleStateCooldownEnabled(type),
    immediateCooldownMinutes: defaultNotificationRuleImmediateCooldownMinutes(type),
    materialCooldownMinutes: defaultNotificationRuleMaterialCooldownMinutes(type),
    stateCooldownMinutes: defaultNotificationRuleStateCooldownMinutes(type),
    marketHoursEnabled: defaultNotificationRuleMarketHoursEnabled(type),
    marketHoursMarkets: defaultNotificationRuleMarketHoursMarkets(type),
    offHoursDeliveryMode: defaultNotificationRuleOffHoursMode(type),
    updatedAt: ""
  };
}

function defaultNotificationRules() {
  var seen = {};
  var keys = [];
  defaultNotificationTemplates().forEach(function (item) {
    keys.push(item.messageType);
  });
  notificationPolicyCatalog().forEach(function (rule) {
    keys.push(rule.key);
  });
  return keys.filter(function (key) {
    if (!key || seen[key]) return false;
    seen[key] = true;
    return true;
  }).map(defaultNotificationRule);
}

function messageScheduleByType(messageType) {
  return (notificationsState.messageSchedules || []).filter(function (item) {
    return item.messageType === messageType;
  })[0] || null;
}

function notificationTemplateByType(messageType) {
  return (notificationsState.notificationTemplates || []).filter(function (item) {
    return item.messageType === messageType;
  })[0] || null;
}

function isAlertTemplateType(messageType) {
  return alertRuleCatalog.some(function (rule) { return rule.key === messageType; });
}

function notificationTemplateForEdit(messageType) {
  var found = notificationTemplateByType(messageType);
  if (found) return found;
  return defaultNotificationTemplates().filter(function (item) {
    return item.messageType === messageType;
  })[0] || {
    messageType: messageType,
    template: "{telegramMessage}",
    description: "",
    enabled: true,
    updatedAt: ""
  };
}

function notificationTemplateVariables() {
  return notificationsState.notificationTemplateVariables.length
    ? notificationsState.notificationTemplateVariables
    : ["title", "statusHeadline", "titleHeadline", "telegramMessage", "readableMessage", "dataLines", "telegramDataLines", "triggerSummary", "triggerBlock", "criterionBlock", "criterionLines", "lines", "rawLines", "referenceDate", "eventGeneratedAt", "sentAt", "sentTime", "sentLine", "body", "messageType", "symbol", "rawSymbol", "symbolDisplayName", "severity", "metadata", "market", "changePercent", "change24h", "change7d", "price", "volume", "volume24h", "provider"];
}

function clampInteger(value, min, max, fallback) {
  var parsed = parseInt(String(value === 0 ? "0" : value || "").replace(/,/g, ""), 10);
  if (!Number.isFinite(parsed)) parsed = fallback;
  return Math.max(min, Math.min(max, parsed));
}

function notificationRuleByType(messageType) {
  return (notificationsState.notificationRules || []).filter(function (item) {
    return item.messageType === messageType;
  })[0] || null;
}

function normalizeNotificationRule(rule) {
  var normalized = Object.assign(defaultNotificationRule(rule && rule.messageType), rule || {});
  normalized.enabled = normalized.enabled !== false;
  normalized.similarityEnabled = normalized.similarityEnabled !== false;
  normalized.similarityWindowMinutes = clampInteger(normalized.similarityWindowMinutes, 0, 10080, defaultNotificationRuleSimilarityWindow(normalized.messageType));
  normalized.similarityBypassConditions = Array.isArray(normalized.similarityBypassConditions) && normalized.similarityBypassConditions.length
    ? normalized.similarityBypassConditions.map(function (condition) {
      return Object.assign({
        id: "",
        label: "",
        type: "field_changed",
        field: "",
        value: "",
        enabled: true,
        description: ""
      }, condition, {
        enabled: condition.enabled !== false
      });
    })
    : defaultNotificationRuleSimilarityBypassConditions(normalized.messageType);
  normalized.similarityFields = Array.isArray(normalized.similarityFields)
    ? normalized.similarityFields.map(function (field) { return String(field || "").trim(); }).filter(Boolean)
    : String(normalized.similarityFields || "").split(",").map(function (field) { return field.trim(); }).filter(Boolean);
  if (!normalized.similarityFields.length) normalized.similarityFields = defaultNotificationRuleSimilarityFields();
  normalized.stateCooldownEnabled = normalized.stateCooldownEnabled !== false;
  normalized.immediateCooldownMinutes = clampInteger(normalized.immediateCooldownMinutes, 0, 10080, defaultNotificationRuleImmediateCooldownMinutes(normalized.messageType));
  normalized.materialCooldownMinutes = clampInteger(normalized.materialCooldownMinutes, 0, 10080, defaultNotificationRuleMaterialCooldownMinutes(normalized.messageType));
  normalized.stateCooldownMinutes = clampInteger(normalized.stateCooldownMinutes, 0, 10080, defaultNotificationRuleStateCooldownMinutes(normalized.messageType));
  normalized.marketHoursEnabled = normalized.marketHoursEnabled !== false;
  normalized.marketHoursMarkets = Array.isArray(normalized.marketHoursMarkets)
    ? normalized.marketHoursMarkets.map(function (market) { return String(market || "").trim().toUpperCase(); }).filter(Boolean)
    : String(normalized.marketHoursMarkets || "").split(",").map(function (market) { return market.trim().toUpperCase(); }).filter(Boolean);
  if (!normalized.marketHoursMarkets.length && defaultNotificationRuleMarketHoursEnabled(normalized.messageType)) {
    normalized.marketHoursMarkets = defaultNotificationRuleMarketHoursMarkets(normalized.messageType);
  }
  normalized.offHoursDeliveryMode = ["important_only", "send_all", "defer_until_open"].indexOf(String(normalized.offHoursDeliveryMode || "")) >= 0
    ? String(normalized.offHoursDeliveryMode)
    : defaultNotificationRuleOffHoursMode(normalized.messageType);
  normalized.conditions = Array.isArray(normalized.conditions) && normalized.conditions.length
    ? normalized.conditions.map(function (condition) {
      return Object.assign({
        id: "",
        label: "",
        type: "text_contains_any",
        field: "",
        value: "",
        terms: [],
        enabled: true
      }, condition, {
        terms: Array.isArray(condition.terms) ? condition.terms.slice() : String(condition.terms || "").split(",").map(function (term) { return term.trim(); }).filter(Boolean),
        enabled: condition.enabled !== false
      });
    })
    : defaultNotificationRuleConditions();
  return normalized;
}

function notificationRuleForEdit(messageType) {
  return normalizeNotificationRule(notificationRuleByType(messageType) || defaultNotificationRule(messageType));
}

function ensureNotificationRule(messageType) {
  var existing = notificationRuleByType(messageType);
  if (existing) return existing;
  existing = defaultNotificationRule(messageType);
  notificationsState.notificationRules = (notificationsState.notificationRules || []).concat(existing);
  return existing;
}

function notificationRuleCondition(rule, conditionId) {
  return (rule.conditions || []).filter(function (condition) {
    return condition.id === conditionId;
  })[0] || null;
}

function notificationRuleBypassCondition(rule, conditionId) {
  return (rule.similarityBypassConditions || []).filter(function (condition) {
    return condition.id === conditionId;
  })[0] || null;
}

function updateNotificationRuleField(messageType, field, value) {
  var rule = ensureNotificationRule(messageType);
  if (field === "enabled") {
    rule.enabled = Boolean(value);
  } else if (field === "similarityEnabled") {
    rule.similarityEnabled = Boolean(value);
  } else if (field === "similarityWindowMinutes") {
    rule.similarityWindowMinutes = clampInteger(value, 0, 10080, defaultNotificationRuleSimilarityWindow(messageType));
  } else if (field === "similarityFields") {
    rule.similarityFields = String(value || "").split(",").map(function (item) { return item.trim(); }).filter(Boolean);
  } else if (field === "stateCooldownEnabled") {
    rule.stateCooldownEnabled = Boolean(value);
  } else if (field === "immediateCooldownMinutes") {
    rule.immediateCooldownMinutes = clampInteger(value, 0, 10080, defaultNotificationRuleImmediateCooldownMinutes(messageType));
  } else if (field === "materialCooldownMinutes") {
    rule.materialCooldownMinutes = clampInteger(value, 0, 10080, defaultNotificationRuleMaterialCooldownMinutes(messageType));
  } else if (field === "stateCooldownMinutes") {
    rule.stateCooldownMinutes = clampInteger(value, 0, 10080, defaultNotificationRuleStateCooldownMinutes(messageType));
  } else if (field === "marketHoursEnabled") {
    rule.marketHoursEnabled = Boolean(value);
  } else if (field === "marketHoursMarkets") {
    rule.marketHoursMarkets = Array.isArray(value)
      ? value.map(function (item) { return String(item || "").trim().toUpperCase(); }).filter(Boolean)
      : String(value || "").split(",").map(function (item) { return item.trim().toUpperCase(); }).filter(Boolean);
  } else if (field === "offHoursDeliveryMode") {
    rule.offHoursDeliveryMode = ["important_only", "send_all", "defer_until_open"].indexOf(String(value || "")) >= 0
      ? String(value)
      : defaultNotificationRuleOffHoursMode(messageType);
  }
  notificationsState.notificationRulesSaved = false;
  notificationsState.notificationRulesError = "";
}

function updateNotificationRuleMarket(messageType, market, enabled) {
  var rule = ensureNotificationRule(messageType);
  var key = String(market || "").trim().toUpperCase();
  if (!key) return;
  var markets = Array.isArray(rule.marketHoursMarkets) ? rule.marketHoursMarkets.slice() : defaultNotificationRuleMarketHoursMarkets(messageType);
  markets = markets.map(function (item) { return String(item || "").trim().toUpperCase(); }).filter(Boolean);
  if (enabled && markets.indexOf(key) < 0) markets.push(key);
  if (!enabled) markets = markets.filter(function (item) { return item !== key; });
  rule.marketHoursMarkets = markets;
  notificationsState.notificationRulesSaved = false;
  notificationsState.notificationRulesError = "";
}

function updateNotificationRuleCondition(messageType, conditionId, field, value) {
  var rule = ensureNotificationRule(messageType);
  var condition = notificationRuleCondition(rule, conditionId);
  if (!condition) return;
  if (field === "enabled") {
    condition.enabled = Boolean(value);
  } else if (field === "field") {
    condition.field = String(value || "").trim();
  } else if (field === "value") {
    if (condition.type === "text_contains_any" || condition.type === "context_contains_any") {
      condition.terms = String(value || "").split(",").map(function (term) { return term.trim(); }).filter(Boolean);
    } else {
      condition.value = String(value || "");
    }
  }
  notificationsState.notificationRulesSaved = false;
  notificationsState.notificationRulesError = "";
}

function updateNotificationRuleBypassCondition(messageType, conditionId, field, value) {
  var rule = ensureNotificationRule(messageType);
  if (!Array.isArray(rule.similarityBypassConditions)) {
    rule.similarityBypassConditions = defaultNotificationRuleSimilarityBypassConditions(messageType);
  }
  var condition = notificationRuleBypassCondition(rule, conditionId);
  if (!condition) return;
  if (field === "enabled") {
    condition.enabled = Boolean(value);
  } else if (field === "field") {
    condition.field = String(value || "").trim();
  } else if (field === "value") {
    condition.value = String(value || "").trim();
  }
  notificationsState.notificationRulesSaved = false;
  notificationsState.notificationRulesError = "";
}

function updateNotificationTemplate(messageType, value) {
  var existing = notificationTemplateByType(messageType);
  if (!existing) {
    existing = notificationTemplateForEdit(messageType);
    notificationsState.notificationTemplates = (notificationsState.notificationTemplates || []).concat(existing);
  }
  existing.template = value;
  notificationsState.notificationTemplatesSaved = false;
  notificationsState.notificationTemplatesError = "";
}

function renderNotificationTemplatePreviewText(template, messageType) {
  var context = notificationTemplatePreviewContext(messageType);
  var rendered = String(template || "").replace(/\{([A-Za-z0-9_]+)\}/g, function (match, key) {
    return Object.prototype.hasOwnProperty.call(context, key) ? String(context[key]) : match;
  }).trim();
  var compacted = [];
  var previousBlank = false;
  rendered.split(/\r?\n/).forEach(function (line) {
    var cleaned = line.replace(/\s+$/, "");
    if (cleaned.trim()) {
      compacted.push(cleaned);
      previousBlank = false;
    } else if (compacted.length && !previousBlank) {
      compacted.push("");
      previousBlank = true;
    }
  });
  while (compacted.length && !compacted[compacted.length - 1].trim()) compacted.pop();
  return compacted.join("\n") || "(빈 메시지)";
}

function saveNotificationTemplate(messageType) {
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    notificationsState.notificationTemplatesError = "공유 모드에서는 알림 템플릿을 변경할 수 없습니다.";
    render();
    return Promise.resolve();
  }
  var item = notificationTemplateByType(messageType);
  if (!item) return Promise.resolve();
  notificationsState.notificationTemplatesLoading = true;
  notificationsState.notificationTemplatesError = "";
  render();
  return sendJson("/api/notification-templates", "PUT", item)
    .then(function (payload) {
      var saved = payload.template || item;
      notificationsState.notificationTemplates = (notificationsState.notificationTemplates || []).map(function (current) {
        return current.messageType === saved.messageType ? saved : current;
      });
      notificationsState.notificationTemplatesSaved = true;
      showSnackbar("알림 템플릿을 저장했습니다.");
    })
    .catch(function (error) {
      notificationsState.notificationTemplatesError = error.message || "알림 템플릿을 저장하지 못했습니다.";
      showSnackbar(notificationsState.notificationTemplatesError, "danger");
    })
    .finally(function () {
      notificationsState.notificationTemplatesLoading = false;
      render();
    });
}

function resetNotificationTemplate(messageType) {
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    notificationsState.notificationTemplatesError = "공유 모드에서는 알림 템플릿을 변경할 수 없습니다.";
    render();
    return Promise.resolve();
  }
  notificationsState.notificationTemplatesLoading = true;
  notificationsState.notificationTemplatesError = "";
  render();
  return sendJson("/api/notification-templates/" + encodeURIComponent(messageType), "DELETE", {})
    .then(function (payload) {
      var saved = payload.template;
      if (saved) {
        notificationsState.notificationTemplates = (notificationsState.notificationTemplates || []).map(function (current) {
          return current.messageType === saved.messageType ? saved : current;
        });
      }
      notificationsState.notificationTemplatesSaved = true;
      showSnackbar("알림 템플릿을 기본값으로 되돌렸습니다.");
    })
    .catch(function (error) {
      notificationsState.notificationTemplatesError = error.message || "알림 템플릿을 초기화하지 못했습니다.";
      showSnackbar(notificationsState.notificationTemplatesError, "danger");
    })
    .finally(function () {
      notificationsState.notificationTemplatesLoading = false;
      render();
    });
}

function saveNotificationRule(messageType) {
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    notificationsState.notificationRulesError = "공유 모드에서는 알림 룰을 변경할 수 없습니다.";
    render();
    return Promise.resolve();
  }
  var item = normalizeNotificationRule(ensureNotificationRule(messageType));
  notificationsState.notificationRulesLoading = true;
  notificationsState.notificationRulesError = "";
  render();
  return sendJson("/api/notification-rules", "PUT", item)
    .then(function (payload) {
      var saved = payload.rule || item;
      notificationsState.notificationRules = (notificationsState.notificationRules || []).map(function (current) {
        return current.messageType === saved.messageType ? saved : current;
      });
      if (!notificationRuleByType(saved.messageType)) {
        notificationsState.notificationRules = (notificationsState.notificationRules || []).concat(saved);
      }
      notificationsState.notificationRulesSaved = true;
      showSnackbar("알림 룰을 저장했습니다.");
    })
    .catch(function (error) {
      notificationsState.notificationRulesError = error.message || "알림 룰을 저장하지 못했습니다.";
      showSnackbar(notificationsState.notificationRulesError, "danger");
    })
    .finally(function () {
      notificationsState.notificationRulesLoading = false;
      render();
    });
}

function resetNotificationRule(messageType) {
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    notificationsState.notificationRulesError = "공유 모드에서는 알림 룰을 변경할 수 없습니다.";
    render();
    return Promise.resolve();
  }
  notificationsState.notificationRulesLoading = true;
  notificationsState.notificationRulesError = "";
  render();
  return sendJson("/api/notification-rules/" + encodeURIComponent(messageType), "DELETE", {})
    .then(function (payload) {
      var saved = payload.rule;
      if (saved) {
        var replaced = false;
        notificationsState.notificationRules = (notificationsState.notificationRules || []).map(function (current) {
          if (current.messageType === saved.messageType) {
            replaced = true;
            return saved;
          }
          return current;
        });
        if (!replaced) notificationsState.notificationRules = (notificationsState.notificationRules || []).concat(saved);
      }
      notificationsState.notificationRulesSaved = true;
      showSnackbar("알림 룰을 기본값으로 되돌렸습니다.");
    })
    .catch(function (error) {
      notificationsState.notificationRulesError = error.message || "알림 룰을 초기화하지 못했습니다.";
      showSnackbar(notificationsState.notificationRulesError, "danger");
    })
    .finally(function () {
      notificationsState.notificationRulesLoading = false;
      render();
    });
}

function canSendNotificationTemplateTest(messageType) {
  return alertRuleCatalog.some(function (rule) { return rule.key === messageType; });
}

function sendNotificationTemplateTest(messageType) {
  if (!canSendNotificationTemplateTest(messageType)) {
    showSnackbar("실제 데이터 발송은 알림 이벤트 타입에서만 가능합니다.", "danger");
    return Promise.resolve();
  }
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    notificationsState.notificationTemplatesError = "공유 모드에서는 실제 알림을 발송할 수 없습니다.";
    showSnackbar(notificationsState.notificationTemplatesError, "danger");
    render();
    return Promise.resolve();
  }
  var directInvestmentTest = messageType === "investmentInsight";
  var confirmation = directInvestmentTest
    ? "실제 투자 인사이트 알림을 정책 우회로 발송합니다. 투자 판단과 별개인 테스트 메시지가 외부 채널에 전달됩니다. 계속할까요?"
    : "테스트 알림이 실제 외부 채널에 전달됩니다. 계속할까요?";
  if (!window.confirm(confirmation)) return Promise.resolve();
  notificationsState.notificationTemplateSending = messageType;
  notificationsState.notificationTemplatesError = "";
  render();
  return sendJson("/api/notification-templates/test-send", "POST", { messageType: messageType, bypassPolicy: directInvestmentTest })
    .then(function (payload) {
      var event = payload.event || {};
      if (payload.suppressed) {
        showSnackbar(payload.error || payload.deliveryGateReason || "자료 또는 검증 상태가 발송 조건을 충족하지 않아 보류했습니다.", "danger");
      } else if (payload.delivered) {
        showSnackbar("테스트 알림을 실제 발송했습니다: " + (event.title || notificationTemplateLabel(messageType)));
      } else {
        showSnackbar("알림 발송 요청을 큐에 적재했습니다: " + (event.title || notificationTemplateLabel(messageType)));
      }
      return Promise.all([loadNotificationSchedules(), loadNotificationJobs()]);
    })
    .catch(function (error) {
      notificationsState.notificationTemplatesError = error.message || "실제 데이터 알림을 보내지 못했습니다.";
      showSnackbar(notificationsState.notificationTemplatesError, "danger");
    })
    .finally(function () {
      notificationsState.notificationTemplateSending = "";
      render();
    });
}

function textValueUnlessBoolean(value) {
  return typeof value === "boolean" ? "" : String(value || "");
}

function messageDeliveryLevelOptions() {
  return [
    { value: "absoluteBeginner", label: "왕초보", description: "전문 용어를 쉬운 말로" },
    { value: "beginner", label: "초보", description: "쉬운 말과 기본 용어 함께" },
    { value: "intermediate", label: "중수", description: "표준 투자 용어로" },
    { value: "advanced", label: "고수", description: "원래 용어와 검증 표현 유지" }
  ];
}

function notificationDetailLevelOptions() {
  return [
    { value: "concise", label: "간결", description: "행동·변화·핵심 근거·변경 조건" },
    { value: "standard", label: "표준", description: "현재 흐름·핵심 추론·회사 가치 포함" },
    { value: "full", label: "전체", description: "웹 상세 수준의 모든 분석 표시" }
  ];
}

function normalizeNotificationDetailLevel(value) {
  var text = String(value || "").trim();
  var aliases = { "간결": "concise", "표준": "standard", "전체": "full", compact: "concise", normal: "standard", detailed: "full" };
  var normalized = aliases[text] || text;
  return notificationDetailLevelOptions().some(function (item) { return item.value === normalized; }) ? normalized : "concise";
}

function normalizeMessageDeliveryLevel(value) {
  var text = String(value || "").trim();
  var aliases = {
    "왕초보": "absoluteBeginner",
    "absolute_beginner": "absoluteBeginner",
    "absolute-beginner": "absoluteBeginner",
    "초보": "beginner",
    "중수": "intermediate",
    "고수": "advanced"
  };
  var normalized = aliases[text] || text;
  return messageDeliveryLevelOptions().some(function (item) { return item.value === normalized; }) ? normalized : "absoluteBeginner";
}

function messageDeliveryLevelLabel(value) {
  var level = normalizeMessageDeliveryLevel(value);
  var matched = messageDeliveryLevelOptions().filter(function (item) { return item.value === level; })[0];
  return matched ? matched.label : "왕초보";
}

export { canSendNotificationTemplateTest, defaultMarketHoursSessions, defaultNotificationRuleConditionTypes, defaultNotificationRuleMarketHoursMarkets, defaultNotificationRuleSimilarityFields, defaultNotificationRules, defaultNotificationTemplates, isAlertTemplateType, messageDeliveryLevelOptions, messageScheduleByType, normalizeMessageDeliveryLevel, normalizeNotificationDetailLevel, notificationDetailLevelOptions, notificationRuleForEdit, notificationTemplateForEdit, notificationTemplateVariables, renderNotificationTemplatePreviewText, resetNotificationRule, resetNotificationTemplate, saveNotificationRule, saveNotificationTemplate, sendNotificationTemplateTest, textValueUnlessBoolean, updateNotificationRuleBypassCondition, updateNotificationRuleCondition, updateNotificationRuleField, updateNotificationRuleMarket, updateNotificationTemplate };
