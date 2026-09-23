import { configuredChip } from "../accounts/directory.mjs";
import { newsProviderLabel, settingEnabled } from "../decisions/signals.mjs";
import { defaultSettings } from "./defaults.mjs";
import { formulaSetting } from "./formulas.mjs";
import { appTimezoneLabel } from "./preferences.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { alertThresholdCatalog } from "../shell/catalog.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { app } from "../shell/root.mjs";
import { settingsState } from "../state/settings.mjs";

function renderModelSettingField(name, label, type, placeholder) {
  return [
    '<label class="setting-field">',
    '<span>' + escapeHtml(label) + '</span>',
    '<input data-model-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name) || defaultSettings[name] || "") + '" placeholder="' + escapeHtml(placeholder || "") + '" autocomplete="off" />',
    '</label>'
  ].join("");
}

function renderModelFormulaField(name, label, placeholder) {
  return [
    '<label class="setting-field wide">',
    '<span>' + escapeHtml(label) + '</span>',
    '<textarea data-model-setting="' + escapeHtml(name) + '" rows="3" autocomplete="off" placeholder="' + escapeHtml(placeholder || "") + '">' + escapeHtml(formulaSetting(name)) + '</textarea>',
    '</label>'
  ].join("");
}

function renderNumberSettingGrid(settingName, map, keys) {
  return [
    '<div class="model-number-grid">',
    keys.map(function (key) {
      return renderNumberSettingInput(settingName, key, map[key]);
    }).join(""),
    '</div>'
  ].join("");
}

function numberSettingCatalogItem(key) {
  var catalogs = alertThresholdCatalog;
  return catalogs.filter(function (item) { return item.key === key; })[0] || null;
}

function renderNumberSettingInput(settingName, key, value) {
  var item = numberSettingCatalogItem(key);
  var label = item ? item.label : key;
  var unit = item && item.unit ? " " + item.unit : "";
  var step = item && item.step ? item.step : "0.01";
  return [
    '<label class="lab-control">',
    '<span>' + escapeHtml(label) + escapeHtml(unit) + '</span>',
    '<input type="number" step="' + escapeHtml(step) + '" value="' + escapeHtml(value == null ? 0 : value) + '" data-number-setting="' + escapeHtml(settingName) + '" data-number-key="' + escapeHtml(key) + '" />',
    '</label>'
  ].join("");
}

function modelVariableGuide() {
  return [
    ["currentPrice", "현재가"],
    ["averagePrice", "평균매입가"],
    ["profitLossRate", "보유 수익률"],
    ["positionWeightPct", "계좌 안 종목 비중"],
    ["volumeRatio", "거래량 배율"],
    ["buyShare", "매수 체결 비중"],
    ["sellShare", "매도 체결 비중"],
    ["bidAskImbalance", "호가 불균형"],
    ["priceChangeRate", "가격 변화율"],
    ["flowDirection", "체결·호가·가격 조건 중 어느 방향이 더 많이 성립했는지"],
    ["ma5", "5일 이동평균"],
    ["ma20", "20일 이동평균"],
    ["ma60", "60일 이동평균"],
    ["trendDistance20", "20일선과 현재가 차이"],
    ["trendDistance60", "60일선과 현재가 차이"],
    ["maSpread", "20일선과 60일선의 간격"],
    ["foreignNet", "외국인 순매수"],
    ["institutionNet", "기관 순매수"],
    ["individualNet", "개인 순매수"],
    ["smartMoneyNet", "외국인과 기관을 합친 순매수"],
    ["lossThreshold", "손실 관리 기준 손익률"],
    ["lossRateBufferPct", "손실 기준 근처 흔들림을 흡수하는 완충 구간"],
    ["reviewLevel", "정상·관찰·조건 확인·대응 준비·즉시 확인·판단 보류 단계"],
    ["dataState", "자료 충분·일부 부족·부족·사용 불가 상태"],
    ["changeState", "변화 없음·새 조건·개선·악화·방향 변경·새 근거"],
    ["conflictState", "위험만·버팀만·엇갈림·참고만 있는 근거 상태"],
    ["validationState", "AI 검증 완료·조건부 사용·판단 보류 상태"],
    ["evidenceRole", "위험·버팀·반대·참고·판단 차단 중 근거 역할"]
  ];
}

function renderVariableGuide(items) {
  return [
    '<div class="variable-grid">',
    items.map(function (item) {
      return '<span><strong>' + escapeHtml(item[0]) + '</strong>' + escapeHtml(item[1]) + '</span>';
    }).join(""),
    '</div>'
  ].join("");
}

function settingValue(name) {
  return settingsState.settings && settingsState.settings[name] != null ? settingsState.settings[name] : "";
}

function isConfiguredSetting(name) {
  return Boolean(settingsState.serverConfigured && settingsState.serverConfigured[name]);
}

function renderSettingField(name, label, type, placeholder, options) {
  options = options || {};
  var fieldPlaceholder = placeholder || "";
  if (options.preserveConfigured && isConfiguredSetting(name)) {
    fieldPlaceholder = "설정됨 - 새 값 입력 시 교체";
  }
  var configuredNote = options.preserveConfigured && isConfiguredSetting(name);
  return [
    '<label class="setting-field setting-field-' + escapeHtml(type || "text") + '">',
    '<span class="setting-field-label">' + escapeHtml(label) + '</span>',
    '<div class="form-control-shell">',
    '<input data-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name)) + '" placeholder="' + escapeHtml(fieldPlaceholder) + '" autocomplete="off" />',
    '</div>',
    configuredNote ? '<em class="setting-field-note">저장됨</em>' : '',
    '</label>'
  ].join("");
}

function renderSettingsApiSummary() {
  return [
    '<div class="settings-api-grid">',
    renderSettingsApiCard("토스 API", settingValue("tossApiBaseUrl") || defaultSettings.tossApiBaseUrl, [
      configuredChip("Client ID", isConfiguredSetting("tossClientId")),
      configuredChip("Secret", isConfiguredSetting("tossClientSecret")),
      configuredChip("Account Seq", isConfiguredSetting("tossAccountSeq"), isConfiguredSetting("tossAccountSeq") ? "저장됨" : "선택")
    ]),
    renderSettingsApiCard("텔레그램", settingValue("notifyProvider") || "telegram", [
      configuredChip("Bot token", isConfiguredSetting("telegramBotToken")),
      configuredChip("Chat ID", isConfiguredSetting("telegramChatId"), isConfiguredSetting("telegramChatId") ? "저장됨" : ""),
      configuredChip("알림 링크", Boolean(settingValue("notifyLinkUrl")))
    ]),
    renderSettingsApiCard("운영 텔레그램", "기술 운영 알림", [
      configuredChip("Bot token", isConfiguredSetting("operationsTelegramBotToken")),
      configuredChip("Chat ID", isConfiguredSetting("operationsTelegramChatId"), isConfiguredSetting("operationsTelegramChatId") ? "저장됨" : "계정 채널 사용")
    ]),
    renderSettingsApiCard("외부 데이터", "가격·크립토·거시·공시", [
      configuredChip("Alpha", settingEnabled("externalAlphaEnabled"), isConfiguredSetting("alphaVantageApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("CoinGecko", settingEnabled("externalCoinGeckoEnabled"), isConfiguredSetting("coingeckoApiKey") ? "키 저장됨" : "키 없음"),
      configuredChip("FRED", settingEnabled("externalFredEnabled"), isConfiguredSetting("fredApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("OpenDART", settingEnabled("externalDartEnabled"), isConfiguredSetting("opendartApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("SEC", settingEnabled("externalSecEnabled"), "무키"),
      configuredChip("뉴스", settingEnabled("externalNewsEnabled"), newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)),
      configuredChip("공시 AI", settingValue("dartDisclosureAiAnalysisEnabled") !== "0", settingValue("dartDisclosureAiUseCodex") === "0" ? "로컬" : "AI")
    ]),
    '</div>'
  ].join("");
}

function renderRuntimeSettingsSummary() {
  var externalEnabledCount = [
    "externalAlphaEnabled",
    "externalCoinGeckoEnabled",
    "externalFredEnabled",
    "externalSecEnabled",
    "externalDartEnabled",
    "externalNewsEnabled"
  ].filter(settingEnabled).length;
  return [
    '<div class="settings-api-grid">',
    renderSettingsApiCard("앱 환경", appThemeLabel(settingValue("appTheme") || defaultSettings.appTheme), [
      configuredChip("테마", true, appThemeLabel(settingValue("appTheme") || defaultSettings.appTheme)),
      configuredChip("표시 시각", true, appTimezoneLabel(settingValue("appTimezone") || defaultSettings.appTimezone)),
      configuredChip("종목 카탈로그", Boolean(settingValue("symbolUniverseMaxAgeHours")), (settingValue("symbolUniverseMaxAgeHours") || defaultSettings.symbolUniverseMaxAgeHours) + "시간")
    ]),
    renderSettingsApiCard("알림 전달", settingValue("notifyProvider") || "telegram", [
      configuredChip("Bot token", isConfiguredSetting("telegramBotToken")),
      configuredChip("Chat ID", isConfiguredSetting("telegramChatId"), isConfiguredSetting("telegramChatId") ? "저장됨" : ""),
      configuredChip("알림 링크", Boolean(settingValue("notifyLinkUrl")))
    ]),
    renderSettingsApiCard("운영 알림", "별도 Telegram 봇", [
      configuredChip("Bot token", isConfiguredSetting("operationsTelegramBotToken")),
      configuredChip("Chat ID", isConfiguredSetting("operationsTelegramChatId"), isConfiguredSetting("operationsTelegramChatId") ? "저장됨" : "계정 채널 사용")
    ]),
    renderSettingsApiCard("AI 투자판단", settingEnabled("notificationAiGateEnabled") && String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") !== "0" ? "사용" : "중지", [
      configuredChip("AI 판단", settingEnabled("notificationAiGateEnabled") && String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") !== "0"),
      configuredChip(
        "병렬 워커",
        String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") !== "0",
        String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") + "개"
      ),
      configuredChip("Codex", settingValue("notificationAiUseCodex") !== "0", settingValue("notificationAiReasoningEffort") || defaultSettings.notificationAiReasoningEffort || "max")
    ]),
    renderSettingsApiCard("외부 데이터", externalEnabledCount + "/6개 수집 사용", [
      configuredChip("Alpha", settingEnabled("externalAlphaEnabled"), isConfiguredSetting("alphaVantageApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("CoinGecko", settingEnabled("externalCoinGeckoEnabled"), isConfiguredSetting("coingeckoApiKey") ? "키 저장됨" : "키 없음"),
      configuredChip("FRED", settingEnabled("externalFredEnabled"), isConfiguredSetting("fredApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("OpenDART", settingEnabled("externalDartEnabled"), isConfiguredSetting("opendartApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("SEC", settingEnabled("externalSecEnabled"), "무키"),
      configuredChip("뉴스", settingEnabled("externalNewsEnabled"), newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)),
      configuredChip("공시 AI", settingValue("dartDisclosureAiAnalysisEnabled") !== "0", settingValue("dartDisclosureAiUseCodex") === "0" ? "로컬" : "AI")
    ]),
    '</div>'
  ].join("");
}

function appThemeLabel(value) {
  var key = String(value || "light").toLowerCase();
  if (key === "dark") return "다크";
  if (key === "system") return "시스템 설정";
  return "라이트";
}

function renderSettingsApiCard(title, subtitle, chips) {
  return [
    '<div class="settings-api-row"' + cardTypeAttrs("config-panel") + '>',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<span>' + escapeHtml(subtitle || "-") + '</span>',
    '<div class="chip-row">' + chips.join("") + '</div>',
    '</div>'
  ].join("");
}

function renderSettingSelect(name, label, options) {
  var current = settingValue(name) || defaultSettings[name] || "";
  return [
    '<label class="setting-field setting-field-select">',
    '<span class="setting-field-label">' + escapeHtml(label) + '</span>',
    '<div class="form-control-shell select-shell">',
    '<select data-setting="' + escapeHtml(name) + '">',
    options.map(function (option) {
      return '<option value="' + escapeHtml(option.value) + '"' + (String(current) === String(option.value) ? " selected" : "") + '>' + escapeHtml(option.label) + '</option>';
    }).join(""),
    '</select>',
    '</div>',
    '</label>'
  ].join("");
}

function settingsSaveDisabledAttr() {
  return settingsState.serverSettingsLocked || settingsState.settingsSaving || !settingsHasPendingChanges() ? ' disabled' : '';
}

function settingsHasPendingChanges() {
  return !settingsState.settingsSaved || Boolean(settingsState.serverSettingsError);
}

function settingsSaveButtonLabel() {
  if (settingsState.settingsSaving) return "저장 중";
  if (settingsHasPendingChanges()) return settingsState.serverSettingsError ? "다시 저장" : "변경 저장";
  return "저장됨";
}

function settingsSaveButtonClass() {
  return settingsHasPendingChanges() || settingsState.settingsSaving ? "text-button primary" : "text-button";
}

function settingsStatusLabel() {
  if (settingsState.settingsSaving) return "DB 저장 중";
  if (settingsState.serverSettingsError) return "저장 실패";
  return settingsState.settingsSaved ? "DB 저장됨" : "저장 필요";
}

function settingsStatusTone() {
  if (settingsState.settingsSaving) return "caution";
  if (settingsState.serverSettingsError) return "danger";
  return settingsState.settingsSaved ? "watch" : "hold";
}

function refreshSettingsSaveControls() {
  if (!app || !app.querySelectorAll) return;
  Array.prototype.slice.call(app.querySelectorAll('[data-action="save-settings"]')).forEach(function (button) {
    button.disabled = Boolean(settingsState.serverSettingsLocked || settingsState.settingsSaving || !settingsHasPendingChanges());
    button.className = settingsSaveButtonClass();
    button.textContent = settingsSaveButtonLabel();
  });
  Array.prototype.slice.call(app.querySelectorAll("[data-settings-status]")).forEach(function (item) {
    item.className = "tone-chip " + settingsStatusTone();
    item.textContent = settingsStatusLabel();
  });
  Array.prototype.slice.call(app.querySelectorAll("[data-settings-save-title]")).forEach(function (item) {
    item.textContent = settingsHasPendingChanges() ? "변경사항 저장 필요" : "변경사항 저장됨";
  });
  Array.prototype.slice.call(app.querySelectorAll("[data-settings-save-description]")).forEach(function (item) {
    item.textContent = settingsHasPendingChanges()
      ? "현재 화면의 앱 표시, 알림 전달, 외부 API 설정을 로컬 저장소에 반영합니다."
      : "입력값이 로컬 저장소와 동기화되어 있습니다.";
  });
}

export { appThemeLabel, isConfiguredSetting, modelVariableGuide, refreshSettingsSaveControls, renderModelFormulaField, renderModelSettingField, renderRuntimeSettingsSummary, renderSettingField, renderSettingSelect, renderSettingsApiCard, renderSettingsApiSummary, settingValue, settingsHasPendingChanges, settingsSaveButtonClass, settingsSaveButtonLabel, settingsSaveDisabledAttr, settingsStatusLabel, settingsStatusTone };
