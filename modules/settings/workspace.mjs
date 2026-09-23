import { accountQuietHoursSummary } from "../accounts/editor.mjs";
import { enabledServiceAccounts, serviceAccounts } from "../accounts/identity.mjs";
import { accountIdOf, accountWatchlistSymbols, activeWatchAccount } from "../accounts/watchlist.mjs";
import { activeSettingsSectionMeta, normalizeSettingsSection } from "../navigation/routes.mjs";
import { defaultSettings } from "./defaults.mjs";
import { appThemeLabel, settingValue, settingsStatusLabel, settingsStatusTone } from "./fields.mjs";
import { currentAppTimezone } from "./preferences.mjs";
import { renderConsoleManagedPage, renderConsoleSurface } from "../shared/console.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { settingsSections } from "../shell/catalog.mjs";
import { settingsState } from "../state/settings.mjs";

function renderSettingsConsole(snapshot) {
  var accounts = serviceAccounts();
  var activeAccounts = enabledServiceAccounts();
  var section = normalizeSettingsSection(settingsState.activeSettingsSection);
  var activeAccount = activeWatchAccount();
  var metrics = section === "account" ? [
    { label: "등록 계정", value: accounts.length + "개", detail: "계정별 원장", target: { type: "detail", value: "account-identity-board" } },
    { label: "활성 계정", value: activeAccounts.length + "개", detail: "모니터링 대상", tone: activeAccounts.length ? "watch" : "caution", target: { type: "detail", value: "account-connections-board" } },
    { label: "현재 계정", value: activeAccount ? (activeAccount.label || accountIdOf(activeAccount)) : "미선택", detail: activeAccount ? accountIdOf(activeAccount) : "계정 등록 필요", target: { type: "detail", value: "account-identity-board" } },
    { label: "관심 종목", value: activeAccount ? accountWatchlistSymbols(activeAccount).length + "개" : "0개", detail: "현재 계정", target: { type: "market", value: "mine", scope: "watchlist" } },
    { label: "수정 권한", value: settingsState.serverSettingsLocked ? "읽기전용" : "수정 가능", detail: settingsState.serverSettingsLocked ? "공유 화면" : "로컬 소유자", tone: settingsState.serverSettingsLocked ? "caution" : "watch", target: { type: "detail", value: "settings-runtime" } }
  ] : [
    { label: "테마", value: appThemeLabel(settingValue("appTheme") || defaultSettings.appTheme), detail: "앱 표시", target: { type: "detail", value: "settings-preferences" } },
    { label: "시간대", value: currentAppTimezone(), detail: "날짜·캘린더", target: { type: "detail", value: "settings-preferences" } },
    { label: "기본 시각", value: settingValue("investmentCalendarCandidateDefaultTime") || defaultSettings.investmentCalendarCandidateDefaultTime || "09:00", detail: "캘린더 후보", target: { type: "detail", value: "settings-preferences" } },
    { label: "투자 알림", value: settingValue("notifyProvider") || "telegram", detail: "사용자 채널", target: { type: "detail", value: "settings-user-notifications" } },
    { label: "저장 상태", value: settingsStatusLabel(), detail: "앱 설정 DB", tone: settingsStatusTone(), target: { type: "detail", value: "settings-preferences" } }
  ];
  var content = section === "account"
    ? renderAccountSettingsScope(snapshot, accounts, activeAccount)
    : renderPreferenceSettingsScope();
  return renderConsoleManagedPage("settings", metrics, content, {
    leading: renderSettingsScopeNavigation()
  });
}

function renderSettingsScopeNavigation() {
  var active = normalizeSettingsSection(settingsState.activeSettingsSection);
  var meta = activeSettingsSectionMeta();
  return [
    '<section class="settings-scope-shell">',
    '<div class="settings-scope-heading"><div><span>SETTINGS SCOPE</span><strong>설정 범위</strong><p>계정별 · 사용자 환경 · 시스템 전체</p></div><em>' + escapeHtml(meta.scope) + '</em></div>',
    '<nav class="settings-scope-tabs" role="tablist" aria-label="설정 범위">',
    settingsSections.map(function (item) {
      var selected = item.id === active;
      return [
        '<button type="button" role="tab" data-settings-section="' + escapeHtml(item.id) + '" class="' + (selected ? "active" : "") + '" aria-selected="' + (selected ? "true" : "false") + '">',
        '<span><strong>' + escapeHtml(item.label) + '</strong><em>' + escapeHtml(item.description) + '</em></span>',
        '<small>' + escapeHtml(item.scope) + '</small>',
        '</button>'
      ].join("");
    }).join(""),
    '</nav>',
    '</section>'
  ].join("");
}

function renderScopedSettingsList(key, items) {
  return '<div class="oa-settings-list scoped-settings-list" data-console-keyed-list="' + escapeHtml(key) + '">' + (items || []).map(function (item) {
    return [
      '<button type="button" data-console-row-key="' + escapeHtml(item.detailType) + '" data-work-detail="' + escapeHtml(item.detailType) + '" data-work-detail-key="' + escapeHtml(item.detailKey || "") + '">',
      '<span><small class="settings-scope-chip ' + escapeHtml(item.tone || "neutral") + '">' + escapeHtml(item.scope) + '</small><strong>' + escapeHtml(item.title) + '</strong><em>' + escapeHtml(item.description) + '</em></span>',
      '<b>' + escapeHtml(item.action || "열기") + ' &rarr;</b>',
      '</button>'
    ].join("");
  }).join("") + '</div>';
}

function renderAccountSettingsScope(snapshot, accounts, activeAccount) {
  var accountOptions = accounts.length ? accounts.map(function (account) {
    var id = accountIdOf(account);
    return '<option value="' + escapeHtml(id) + '"' + (activeAccount && id === accountIdOf(activeAccount) ? ' selected' : '') + '>' + escapeHtml(account.label || id) + '</option>';
  }).join("") : '<option value="">등록된 계정 없음</option>';
  var context = [
    '<div class="settings-account-context">',
    '<label><span>현재 관리 계정</span><select data-settings-account-select' + (accounts.length ? '' : ' disabled') + '>' + accountOptions + '</select></label>',
    '<div><span class="settings-scope-chip account">계정별</span><strong>' + escapeHtml(activeAccount ? (activeAccount.label || accountIdOf(activeAccount)) : "계정을 먼저 등록하세요") + '</strong><em>' + escapeHtml(activeAccount ? accountIdOf(activeAccount) + " · 관심 " + accountWatchlistSymbols(activeAccount).length + "개 · " + accountQuietHoursSummary(activeAccount) : "계정마다 인증, 관심 종목, 알림 표현을 따로 관리합니다.") + '</em></div>',
    '</div>'
  ].join("");
  var items = [
    { title: "계정 목록과 수신 설정", description: "계정 이름, 증권사, API 자격 정보, 관심 종목, 방해 금지 시간", scope: "계정별", tone: "account", detailType: "account-identity-board", action: "관리" },
    { title: "증권사 연결과 데이터 출처", description: "Toss 연결 가능성, 실제·캐시·mock 데이터 품질", scope: "계정별", tone: "account", detailType: "account-connections-board", action: "점검" },
    { title: "자산 원장 검증", description: "현금, 환율, 평가액과 보유 수량 산식", scope: "계정별", tone: "account", detailType: "account-balance-board", action: "검증" },
    { title: "계정 데이터 이력", description: "스냅샷 생성 시각, 캐시와 데이터 신선도", scope: "계정별", tone: "account", detailType: "account-history-board", action: "확인" }
  ];
  return [
    '<div class="oa-console-grid settings-scope-content settings-account-scope">',
    renderConsoleSurface({ kicker: "ACCOUNT CONTEXT", title: "투자 계정", description: "선택한 계정의 연결과 원장만 관리합니다.", body: context }),
    renderConsoleSurface({ kicker: "ACCOUNT SETTINGS", title: "계정 설정", description: "시스템 운영값과 분리된 계정별 설정입니다.", body: renderScopedSettingsList("account-settings", items) }),
    '</div>'
  ].join("");
}

function renderPreferenceSettingsScope() {
  var items = [
    { title: "화면과 시간 표시", description: "테마, 시간대, 캘린더 기본 시각과 종목 신선도 표시", scope: "앱 환경", tone: "preferences", detailType: "settings-preferences", action: "편집" },
    { title: "투자 알림 수신", description: "투자 인사이트와 뉴스를 받을 Telegram 채널과 링크", scope: "사용자 채널", tone: "preferences", detailType: "settings-user-notifications", action: "편집" }
  ];
  var boundary = [
    '<div class="settings-boundary-list">',
    '<div><span>이 화면에서 변경</span><strong>테마 · 시간대 · 캘린더 기본 시각 · 투자 알림 수신</strong></div>',
    '<div><span>계정에서 변경</span><strong>증권 인증 · 관심 종목 · 계정 알림 표현 · 방해 금지 시간</strong></div>',
    '<div><span>운영 관리에서 변경</span><strong>API 키 · 워커 · 추론 · 데이터 신선도 정책</strong></div>',
    '</div>'
  ].join("");
  return [
    '<div class="oa-console-grid settings-scope-content settings-preference-scope">',
    renderConsoleSurface({ kicker: "APP PREFERENCES", title: "내 환경", description: "투자 데이터나 워커 동작을 바꾸지 않는 표시 설정입니다.", body: renderScopedSettingsList("preference-settings", items) }),
    renderConsoleSurface({ kicker: "SCOPE GUIDE", title: "설정 경계", description: "값이 적용되는 범위를 확인하고 변경합니다.", body: boundary }),
    '</div>'
  ].join("");
}

export { renderSettingsConsole };
