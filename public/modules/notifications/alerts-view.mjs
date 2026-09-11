import { stockDisplayName, textWithDisplaySymbol } from "../instruments/catalog.mjs";
import { alertRuleLabel, alertSeverityLabel, alertStats, buildAlertItems } from "./alerts.mjs";
import { renderSettingField, renderSettingSelect, renderSettingsApiSummary, settingsSaveButtonClass, settingsSaveButtonLabel, settingsSaveDisabledAttr, settingsStatusLabel, settingsStatusTone } from "../settings/fields.mjs";
import { latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs } from "../shell/layout.mjs";
import { settingsState } from "../state/settings.mjs";

function renderAlertCenterPanel(snapshot) {
  var alerts = latestChangedFirst(buildAlertItems(snapshot));
  var stats = alertStats(alerts);
  var visibleAlerts = alerts.slice(0, 6);
  return [
    '<article class="panel alert-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Alert Center</p>',
    '<h2>매수·매도 타이밍 알림</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(stats.total) + '</span>',
    '</div>',
    '<div class="alert-stat-grid">',
    renderAlertStat("긴급", stats.danger, "danger"),
    renderAlertStat("주의", stats.caution, "caution"),
    renderAlertStat("관찰", stats.watch, "watch"),
    renderAlertStat("정보", stats.info, "info"),
    '</div>',
    '<div class="alert-list">',
    visibleAlerts.length ? visibleAlerts.map(function (alert, index) {
      return renderAlertRow(alert, index);
    }).join("") + (alerts.length > visibleAlerts.length ? '<p class="data-refresh-status">위험도 높은 6개 알림만 먼저 표시합니다. 나머지 ' + escapeHtml(alerts.length - visibleAlerts.length) + '개는 선택 상세와 알림 운영 흐름에서 확인하세요.</p>' : '') : '<p class="subtle">현재 켜진 규칙에서 발생한 알림이 없습니다.</p>',
    '</div>',
    '<div class="rule-strip"><span>알림은 주문 지시가 아니라 가격선, 수급, 성립 조건, 보유 위험을 다시 확인하라는 신호입니다.</span></div>',
    '</article>'
  ].join("");
}

function renderAlertStat(label, value, severity) {
  return [
    '<span class="alert-stat ' + escapeHtml(severity) + '"' + cardTypeAttrs("metric-cell", severity || "hold") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '</span>'
  ].join("");
}

function renderAlertRow(alert, index) {
  var displaySymbol = alert.symbol ? stockDisplayName(alert.symbol, alert) : "";
  var title = textWithDisplaySymbol(alert.title || "-", alert.symbol, alert);
  var meta = [
    displaySymbol || "",
    alert.source || "",
    alert.value ? "현재 " + alert.value : "",
    alert.threshold ? "기준 " + alert.threshold : ""
  ].filter(Boolean);
  return [
    '<div class="alert-row ' + escapeHtml(alert.severity || "info") + '"' + cardTypeAttrs("signal-card", alert.severity || "info") + cardFormatAttrs("summary-list-card", "compact") + ' role="button" tabindex="0" data-monitor-alert-detail="' + escapeHtml(index) + '" aria-label="' + escapeHtml((title || "알림") + " 상세 보기") + '">',
    '<span class="alert-severity ' + escapeHtml(alert.severity || "info") + '">' + escapeHtml(alertSeverityLabel(alert.severity)) + '</span>',
    '<div class="alert-main">',
    '<div class="flow-title">',
    '<div>',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<span>' + escapeHtml(meta.join(" · ")) + '</span>',
    renderRecordChangedAt(alert),
    '</div>',
    '<span class="tone-chip hold">' + escapeHtml(alertRuleLabel(alert.rule)) + '</span>',
    '</div>',
    '<p>' + escapeHtml(alert.message || "") + '</p>',
    '</div>',
    '</div>'
  ].join("");
}

function renderAlertThresholdInput(item, value) {
  return [
    '<label class="lab-control alert-threshold">',
    '<span>' + escapeHtml(item.label) + (item.unit ? " (" + escapeHtml(item.unit) + ")" : "") + '</span>',
    '<input data-alert-threshold="' + escapeHtml(item.key) + '" type="number" step="' + escapeHtml(item.step || "1") + '" value="' + escapeHtml(value) + '" />',
    '</label>'
  ].join("");
}

function renderAlertDeliveryPanel() {
  var secretType = settingsState.showSecrets ? "text" : "password";
  return [
    '<article class="panel alert-delivery-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Delivery</p>',
    '<h2>웹·푸시 알림 설정</h2>',
    '</div>',
    '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
    '</div>',
    '<div class="settings-body">',
    '<div class="settings-note">',
    '<strong>저장 위치</strong>',
    '<p>웹 알림 규칙, 모델 설정, 푸시 채널 설정은 같은 설정 저장소를 사용합니다. 저장하면 로컬 서버의 알림 워커도 같은 값을 읽습니다.</p>',
    settingsState.serverSettingsError ? '<p class="form-error">' + escapeHtml(settingsState.serverSettingsError) + '</p>' : '',
    settingsState.serverSettingsLocked ? '<p class="form-error">공유 모드에서는 서버 설정 저장이 잠겨 있습니다.</p>' : '',
    '</div>',
    renderSettingsApiSummary(),
    '<div class="settings-grid">',
    renderSettingField("notifyProvider", "알림 제공자", "text", "telegram"),
    renderSettingField("notifyLinkUrl", "알림 링크 URL", "url", "http://127.0.0.1:3000?tab=notifications"),
    renderSettingSelect("operatorReasoningReportEnabled", "운영자 추론 보고서", [
      { value: "0", label: "끄기" },
      { value: "1", label: "사용" }
    ]),
    renderSettingField("telegramBotToken", "Telegram Bot Token", secretType, "bot token", { preserveConfigured: true }),
    renderSettingField("telegramChatId", "Telegram Chat ID", "text", "chat id", { preserveConfigured: true }),
    renderSettingField("operationsTelegramBotToken", "운영 알림 Bot Token", secretType, "operations bot token", { preserveConfigured: true }),
    renderSettingField("operationsTelegramChatId", "운영 알림 Chat ID", "text", "기존 Chat ID 사용 가능", { preserveConfigured: true }),
    '</div>',
    '<div class="settings-actions">',
    '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
    '<button class="text-button" data-action="toggle-secrets">' + (settingsState.showSecrets ? "secret 숨기기" : "secret 보기") + '</button>',
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

export { renderAlertCenterPanel, renderAlertDeliveryPanel, renderAlertThresholdInput };
