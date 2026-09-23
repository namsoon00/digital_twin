import { defaultSettings } from "./defaults.mjs";
import { syncedModelAlertSettings } from "./formulas.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

var settingsMemoryStore = "";

function loadSettings() {
  try {
    var raw = readStoredSettings();
    return syncedModelAlertSettings(Object.assign({}, defaultSettings, raw ? JSON.parse(raw) : {}));
  } catch (error) {
    return syncedModelAlertSettings(Object.assign({}, defaultSettings));
  }
}

function settingsWithExplicitDataGaps(settings) {
  var next = Object.assign({}, settings || {});
  ["valuationAssumptions", "marketSignalInputs"].forEach(function (key) {
    if (!Object.prototype.hasOwnProperty.call(next, key)) next[key] = "";
  });
  return next;
}

function readStoredSettings() {
  try {
    var storage = window.localStorage;
    return storage ? storage.getItem("exitLensSettings") : settingsMemoryStore;
  } catch (error) {
    return settingsMemoryStore;
  }
}

function writeStoredSettings(payload) {
  settingsMemoryStore = payload;
  try {
    var storage = window.localStorage;
    if (storage) storage.setItem("exitLensSettings", payload);
    return true;
  } catch (error) {
    return true;
  }
}

function persistSettings() {
  settingsState.settingsSaved = writeStoredSettings(JSON.stringify(settingsState.settings));
  if (!settingsState.settingsSaved) {
    shellState.error = "브라우저 저장소에 설정을 저장하지 못했습니다.";
  }
}

function applyServerSettings(payload) {
  var nextSettings = settingsWithExplicitDataGaps(payload.settings || {});
  settingsState.settings = syncedModelAlertSettings(Object.assign({}, settingsState.settings, nextSettings));
  notificationsState.notificationAiPromptRelease = payload.notificationAiPromptRelease && typeof payload.notificationAiPromptRelease === "object"
    ? payload.notificationAiPromptRelease
    : {};
  settingsState.serverConfigured = payload.configured || {};
  settingsState.serverSettingsLocked = Boolean(payload.locked);
  settingsState.shareAccess = payload.shareAccess && typeof payload.shareAccess === "object"
    ? payload.shareAccess
    : { role: settingsState.serverSettingsLocked ? "viewer" : "local-owner", writable: !settingsState.serverSettingsLocked, capabilities: settingsState.serverSettingsLocked ? ["read"] : ["read", "write"] };
  settingsState.shareRuntime = payload.shareRuntime && typeof payload.shareRuntime === "object" ? payload.shareRuntime : {};
  settingsState.runtimeIdentity = payload.runtimeIdentity && typeof payload.runtimeIdentity === "object" ? payload.runtimeIdentity : {};
  settingsState.serverSettingsLoaded = true;
  settingsState.serverSettingsError = "";
  settingsState.settingsSaved = true;
  persistSettings();
}

export { applyServerSettings, loadSettings, persistSettings, settingsWithExplicitDataGaps };
