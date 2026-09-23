import { initialSettingsSection } from "../navigation/routes.mjs";
import { loadSettings } from "../settings/storage.mjs";

const settingsState = {};

function initializeSettingsState(cachedSnapshot) {
  return {
    settings: loadSettings(),
    showSecrets: false,
    settingsSaving: false,
    settingsSaved: false,
    serverSettingsLoaded: false,
    serverSettingsError: "",
    serverSettingsLocked: false,
    shareAccess: { role: "local-owner", writable: true, capabilities: ["read", "write"] },
    shareRuntime: {},
    shareRotationRequesting: false,
    runtimeIdentity: {},
    serverConfigured: {},
    activeSettingsSection: initialSettingsSection(),
    settingsRuntimeExpanded: false,
    investmentLanguage: null,
    investmentLanguageLoading: false,
    investmentLanguageLoaded: false,
    investmentLanguageSaving: false,
    investmentLanguageError: "",
    investmentLanguageSearch: "",
    investmentLanguagePreviewLevel: "absoluteBeginner",
    investmentLanguagePreviewText: "종목 타입: PlatformGrowth. 계좌 안 역할: growth. feature 기여도와 thesis를 확인합니다.",
    investmentLanguagePreview: null
  };
}

export { initializeSettingsState, settingsState };
