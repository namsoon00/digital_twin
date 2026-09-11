import { syncAccountDraftFromLoadedAccounts } from "../accounts/commands.mjs";
import { syncActiveWatchAccountId } from "../accounts/watchlist.mjs";
import { requestJson } from "../requests/json.mjs";
import { syncedModelAlertSettings } from "../settings/formulas.mjs";
import { persistSettings, settingsWithExplicitDataGaps } from "../settings/storage.mjs";
import { accountsState } from "../state/accounts.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

var staticBuildConfigPromise = null;

function isStaticPreviewHost() {
  return window.location.protocol === "file:" || /\.github\.io$/i.test(window.location.hostname);
}

function staticLocalData(payload) {
  return payload && payload.localData && typeof payload.localData === "object" ? payload.localData : {};
}

function loadStaticBuildConfig() {
  if (!isStaticPreviewHost()) return Promise.resolve(null);
  if (shellState.staticBuildConfig) return Promise.resolve(shellState.staticBuildConfig);
  if (staticBuildConfigPromise) return staticBuildConfigPromise;
  staticBuildConfigPromise = requestJson("admin/config.json")
    .then(function (payload) {
      shellState.staticBuildConfig = payload;
      shellState.staticBuildConfigError = "";
      return payload;
    })
    .catch(function (error) {
      shellState.staticBuildConfigError = error.message || "정적 빌드 설정을 읽지 못했습니다.";
      return null;
    });
  return staticBuildConfigPromise;
}

function applyStaticBuildSettings(payload) {
  var localData = staticLocalData(payload);
  var settings = localData.settings && typeof localData.settings === "object" ? localData.settings : null;
  if (!settings) return;
  settings = settingsWithExplicitDataGaps(settings);
  settingsState.settings = syncedModelAlertSettings(Object.assign({}, settingsState.settings, settings));
  settingsState.serverConfigured = localData.configured || {};
  settingsState.serverSettingsLoaded = true;
  settingsState.serverSettingsLocked = true;
  settingsState.serverSettingsError = "";
  settingsState.settingsSaved = true;
  persistSettings();
}

function applyStaticBuildAccounts(payload, forceDraft) {
  var localData = staticLocalData(payload);
  accountsState.serviceAccounts = Array.isArray(localData.accounts) ? localData.accounts : [];
  accountsState.serviceAccountsLoaded = true;
  syncActiveWatchAccountId();
  syncAccountDraftFromLoadedAccounts(Boolean(forceDraft));
}

function staticPreviewSnapshot() {
  var localData = staticLocalData(shellState.staticBuildConfig);
  var stamped = localData.generatedAt || new Date().toISOString();
  var accountCount = Number(localData.accountCount || 0);
  return {
    generatedAt: stamped,
    preview: true,
    headline: accountCount ? "빌드 시점 로컬 DB 설정을 표시합니다." : "로컬 서버에서 계정과 알림 설정을 관리합니다.",
    regime: "정적 미리보기",
    summary: [],
    toss: {
      mode: "preview",
      configured: false,
      status: "정적 미리보기",
      account: {},
      positions: [],
      watchlist: []
    },
    portfolio: {
      total: 0,
      invested: 0,
      cash: 0,
      concentration: 0,
      markets: [],
      sectors: []
    },
    tossDecision: {
      headline: "로컬 서버에서 실제 계정 데이터를 조회합니다.",
      urgentCount: 0,
      holdingCount: 0,
      watchCount: 0,
      items: [],
      rules: []
    },
    checklist: []
  };
}

export { applyStaticBuildAccounts, applyStaticBuildSettings, isStaticPreviewHost, loadStaticBuildConfig, staticPreviewSnapshot };
