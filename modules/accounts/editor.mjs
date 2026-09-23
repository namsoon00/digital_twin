import { accountFreshness, accountSnapshotModeLabel, accountSnapshotTone, currentAccountLabel, renderAccountControlMetric, renderAccountQualityLedger } from "./balance.mjs";
import { defaultAccountDraft } from "./commands.mjs";
import { accountRowStatusChip, renderAccountCredentialSummary, renderDirectoryStat, renderEmptyAccountCredentialSummary } from "./directory.mjs";
import { accountWatchlistSymbols, watchSymbolListText } from "./watchlist.mjs";
import { messageDeliveryLevelOptions, notificationDetailLevelOptions } from "../notifications/policy.mjs";
import { appTimezoneLabel, appTimezoneOptions, currentAppTimezone } from "../settings/preferences.mjs";
import { formatClock, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs } from "../shell/layout.mjs";
import { app } from "../shell/root.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { accountsState } from "../state/accounts.mjs";
import { settingsState } from "../state/settings.mjs";

function renderAccountCommandCenter(snapshot) {
  snapshot = snapshot || {};
  var accounts = accountsState.serviceAccounts || [];
  var enabled = accounts.filter(function (account) { return account.enabled !== false; }).length;
  var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
  var freshness = accountFreshness(snapshot);
  var modeLabel = accountSnapshotModeLabel(snapshot);
  return [
    '<article class="panel account-command-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Account Control</p>',
    '<h2>계정 관제 보드</h2>',
    '<p class="subtle">API 키 원문은 숨긴 상태로, 계좌 데이터 출처와 금액 산식만 검증합니다.</p>',
    '</div>',
    '<span class="status-pill ' + escapeHtml(accountSnapshotTone(snapshot)) + '">' + escapeHtml(modeLabel) + '</span>',
    '</div>',
    '<div class="account-command-grid">',
    renderAccountControlMetric("현재 계좌", currentAccountLabel(snapshot), enabled + "/" + accounts.length + " 활성", enabled ? "ok" : "warn", "health-card"),
    renderAccountControlMetric("Toss 준비", tossReady + "/" + accounts.length, "계정별 API key/secret", tossReady ? "ok" : "warn", "health-card"),
    renderAccountControlMetric("데이터 신선도", freshness.label, freshness.detail, freshness.tone, "health-card"),
    renderAccountControlMetric("스냅샷", formatClock(snapshot.generatedAt), (snapshot.toss || {}).status || "조회 상태 대기", accountSnapshotTone(snapshot), "health-card"),
    '</div>',
    '<div class="account-overview-ledger">',
    '<div class="account-board-title"><strong>데이터 품질</strong><span>연결/금액 탭에서 세부 검증을 볼 수 있습니다.</span></div>',
    renderAccountQualityLedger(snapshot),
    '</div>',
    '</article>'
  ].join("");
}

function accountQuietHoursSummary(account) {
  account = account || {};
  if (account.quietHoursEnabled === false) return "시간 제한 없음";
  var start = String(account.quietHoursStart || "22:00");
  var end = String(account.quietHoursEnd || "05:00");
  var timezone = String(account.quietHoursTimezone || currentAppTimezone());
  return start + "-" + end + " · " + appTimezoneLabel(timezone) + " 기준";
}

function accountQuietHoursTimezoneOptions() {
  var draft = accountsState.accountDraft || defaultAccountDraft();
  var selected = String(draft.quietHoursTimezone || currentAppTimezone());
  var options = appTimezoneOptions().slice();
  if (!options.some(function (item) { return item.value === selected; })) {
    options.push({ value: selected, label: selected });
  }
  return options;
}

function renderAccountQuietHoursSettings() {
  var draft = accountsState.accountDraft || defaultAccountDraft();
  return [
    '<section class="account-quiet-hours-settings" data-account-quiet-hours-enabled="' + (draft.quietHoursEnabled !== false ? 'true' : 'false') + '">',
    '<div class="account-quiet-hours-heading">',
    '<div><span>NOTIFICATION SCHEDULE</span><strong>방해 금지 시간</strong><p>수면 중이거나 알림을 받고 싶지 않은 시간을 이 계정에만 적용합니다.</p></div>',
    '<em data-account-quiet-hours-summary>' + escapeHtml(accountQuietHoursSummary(draft)) + '</em>',
    '</div>',
    '<label class="admin-check-field account-quiet-hours-toggle">',
    '<input data-account-field="quietHoursEnabled" type="checkbox"' + (draft.quietHoursEnabled !== false ? ' checked' : '') + ' />',
    '<span>이 계정에 방해 금지 시간 적용</span>',
    '</label>',
    '<div class="account-quiet-hours-fields">',
    renderAccountField("quietHoursStart", "시작", "time", "22:00"),
    renderAccountField("quietHoursEnd", "종료", "time", "05:00"),
    renderAccountSelectField("quietHoursTimezone", "기준 시간대", accountQuietHoursTimezoneOptions()),
    '</div>',
    '<p class="account-quiet-hours-note">해당 시간의 투자·뉴스 알림은 앱 알림함에 사유와 함께 기록되지만 외부 채널로 전송하지 않습니다. 시간이 지난 알림을 종료 후 몰아서 보내지 않으며, 운영 완료와 운영자 보고는 예외입니다.</p>',
    '</section>'
  ].join("");
}

function updateAccountQuietHoursDraftSummary() {
  var summary = app.querySelector("[data-account-quiet-hours-summary]");
  var section = app.querySelector(".account-quiet-hours-settings");
  if (summary) summary.textContent = accountQuietHoursSummary(accountsState.accountDraft || defaultAccountDraft());
  if (section) section.setAttribute("data-account-quiet-hours-enabled", (accountsState.accountDraft || {}).quietHoursEnabled !== false ? "true" : "false");
}

function renderAdminAccountPanel() {
  var accounts = accountsState.serviceAccounts || [];
  var draft = accountsState.accountDraft || defaultAccountDraft();
  var locked = settingsState.serverSettingsLocked || isStaticPreviewHost();
  var editingAccount = accounts.filter(function (account) {
    return account.id === accountsState.editingAccountId;
  })[0] || null;
  var active = accounts.filter(function (account) { return account.enabled !== false; }).length;
  var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
  var accountSeqReady = accounts.filter(function (account) { return account.accountSeq; }).length;
  return [
    '<article class="panel admin-account-panel account-manager-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Accounts</p>',
    '<h2>DB 저장 계정</h2>',
    '</div>',
    '<div class="settings-actions">',
    '<button class="text-button" data-action="new-service-account">새 계정</button>',
    '<span class="metric">' + escapeHtml(accounts.length) + '</span>',
    '</div>',
    '</div>',
    '<div class="account-manager-summary">',
    renderDirectoryStat("등록", accounts.length + "개"),
    renderDirectoryStat("활성", active + "개"),
    renderDirectoryStat("토스 API", tossReady + "개"),
    renderDirectoryStat("계좌 seq", accountSeqReady + "개"),
    '</div>',
    '<div class="admin-account-layout">',
    '<div class="admin-account-list">',
    '<div class="account-column-head"><strong>저장된 계정</strong><span>API 원문은 표시하지 않습니다.</span></div>',
    accountsState.serviceAccountsLoading ? '<p class="subtle">계정 정보를 읽는 중입니다.</p>' : '',
    accountsState.serviceAccountsError ? '<p class="form-error">' + escapeHtml(accountsState.serviceAccountsError) + '</p>' : '',
    accountsState.accountSaved ? '<p class="lab-message">계정 설정을 저장했습니다.</p>' : '',
    accounts.length ? accounts.map(renderServiceAccountRow).join("") : '<p class="subtle">아직 등록된 서비스 계정이 없습니다.</p>',
    '</div>',
    '<form class="admin-account-form" data-account-form>',
    '<div class="settings-note">',
    '<strong>' + escapeHtml(accountsState.editingAccountId ? "계정 수정" : "새 계정 등록") + '</strong>',
    '<p>저장된 API 값은 아래 상태 칩으로 확인하세요. 수정하지 않을 secret 칸은 비워두면 기존 값을 유지합니다.</p>',
    '</div>',
    editingAccount ? renderAccountCredentialSummary(editingAccount) : renderEmptyAccountCredentialSummary(),
    '<div class="admin-form-grid">',
    renderAccountField("id", "계정 ID", "text", "main", { required: true, disabled: Boolean(accountsState.editingAccountId) }),
    renderAccountField("label", "표시 이름", "text", "메인 계정", { required: true }),
    renderAccountField("provider", "증권사", "text", "toss"),
    renderAccountField("baseUrl", "Toss API Base URL", "url", "https://openapi.tossinvest.com", { wide: true }),
    renderAccountField("clientId", "Toss API Key", settingsState.showSecrets ? "text" : "password", "새 값 입력 시 교체", { configured: Boolean(editingAccount && editingAccount.clientId) }),
    renderAccountField("clientSecret", "Toss Secret Key", settingsState.showSecrets ? "text" : "password", "새 값 입력 시 교체", { configured: Boolean(editingAccount && editingAccount.clientSecret) }),
    renderAccountField("accountSeq", "계좌 순번", "text", "선택", { configured: Boolean(editingAccount && editingAccount.accountSeq) }),
    renderAccountField("watchlistSymbols", "관심 종목", "text", "NVDA,005930", { wide: true }),
    renderAccountSelectField("messageDeliveryLevel", "알림 표현", messageDeliveryLevelOptions()),
    renderAccountSelectField("notificationDetailLevel", "알림 정보량", notificationDetailLevelOptions()),
    '<label class="admin-check-field">',
    '<input data-account-field="enabled" type="checkbox"' + (draft.enabled !== false ? " checked" : "") + ' />',
    '<span>이 계정을 모니터링에 사용</span>',
    '</label>',
    '</div>',
    renderAccountQuietHoursSettings(),
    '<div class="settings-actions">',
    '<button class="text-button primary" type="submit"' + (locked ? ' disabled' : '') + '>계정 저장</button>',
    '<button class="text-button" type="button" data-action="toggle-secrets">' + (settingsState.showSecrets ? "secret 숨기기" : "secret 보기") + '</button>',
    locked ? '<span class="subtle">로컬 서버에서만 저장할 수 있습니다.</span>' : '',
    '</div>',
    '</form>',
    '</div>',
    '</article>'
  ].join("");
}

function renderAccountField(name, label, type, placeholder, options) {
  options = options || {};
  var draft = accountsState.accountDraft || defaultAccountDraft();
  var value = draft[name] == null ? "" : draft[name];
  var fieldPlaceholder = options.configured && !value ? "저장됨 - 새 값 입력 시 교체" : (placeholder || "");
  return [
    '<label class="setting-field' + (options.wide ? " wide" : "") + '">',
    '<span>' + escapeHtml(label) + '</span>',
    '<input data-account-field="' + escapeHtml(name) + '" name="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(value) + '" placeholder="' + escapeHtml(fieldPlaceholder) + '" autocomplete="off"' + (options.required ? " required" : "") + (options.disabled ? " disabled" : "") + ' />',
    options.configured ? '<em class="setting-field-note">저장됨</em>' : '',
    '</label>'
  ].join("");
}

function renderAccountSelectField(name, label, options) {
  var draft = accountsState.accountDraft || defaultAccountDraft();
  var value = String(draft[name] || "");
  var selected = (options || []).filter(function (item) { return item.value === value; })[0];
  return [
    '<label class="setting-field">',
    '<span>' + escapeHtml(label) + '</span>',
    '<select data-account-field="' + escapeHtml(name) + '" name="' + escapeHtml(name) + '">',
    (options || []).map(function (item) {
      return '<option value="' + escapeHtml(item.value) + '"' + (item.value === value ? ' selected' : '') + '>' + escapeHtml(item.label) + '</option>';
    }).join(""),
    '</select>',
    selected && selected.description ? '<em class="setting-field-note">' + escapeHtml(selected.description) + '</em>' : '',
    '</label>'
  ].join("");
}

function renderServiceAccountRow(account) {
  var watchlist = watchSymbolListText(accountWatchlistSymbols(account));
  return [
    '<div class="service-account-row"' + cardTypeAttrs("ledger-row", account.enabled === false ? "hold" : "watch") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="service-account-main">',
    '<div class="service-account-title">',
    '<strong>' + escapeHtml(account.label || account.id || "-") + '</strong>',
    accountRowStatusChip(account),
    '</div>',
    '<span class="service-account-line">' + escapeHtml(account.id || "-") + ' · ' + escapeHtml(account.provider || "toss") + ' · ' + escapeHtml(account.enabled === false ? "중지" : "사용") + '</span>',
    renderRecordChangedAt(account),
    '<span class="service-account-line">관심 ' + escapeHtml(watchlist || "-") + '</span>',
    renderAccountExposureGrid(account),
    '</div>',
    '<div class="service-account-meta">',
    '<div class="row-actions">',
    '<button class="mini-button" data-account-edit="' + escapeHtml(account.id || "") + '">수정</button>',
    '<button class="mini-button danger" data-account-remove="' + escapeHtml(account.id || "") + '">삭제</button>',
    '</div>',
    '</div>',
    '</div>'
  ].join("");
}

function renderAccountExposureGrid(account) {
  account = account || {};
  var symbols = accountWatchlistSymbols(account);
  return [
    '<div class="account-exposure-grid" aria-label="계정 노출 상태">',
    renderAccountExposureItem("토스 API", account.clientId && account.clientSecret ? "연결" : "확인", account.clientId && account.clientSecret ? "ok" : "warn"),
    renderAccountExposureItem("계좌 seq", account.accountSeq ? String(account.accountSeq) : "선택 안함", account.accountSeq ? "ok" : "warn"),
    renderAccountExposureItem("관심종목", symbols.length + "개", symbols.length ? "ok" : "neutral"),
    renderAccountExposureItem("알림 수신", accountQuietHoursSummary(account), account.quietHoursEnabled === false ? "neutral" : "ok"),
    renderAccountExposureItem("사용 상태", account.enabled === false ? "중지" : "사용", account.enabled === false ? "warn" : "ok"),
    '</div>'
  ].join("");
}

function renderAccountExposureItem(label, value, tone) {
  return [
    '<span class="account-exposure-item ' + escapeHtml(tone || "neutral") + '"' + cardTypeAttrs("health-card", tone || "neutral") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '</span>'
  ].join("");
}

export { accountQuietHoursSummary, renderAccountCommandCenter, renderAdminAccountPanel, updateAccountQuietHoursDraftSummary };
