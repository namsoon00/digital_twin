import { accountById, accountIdOf, accountWatchlistSymbols, activeWatchAccount, syncActiveWatchAccountId } from "./watchlist.mjs";
import { normalizeSymbols, watchlistSymbols } from "../instruments/catalog.mjs";
import { normalizeMessageDeliveryLevel, normalizeNotificationDetailLevel, textValueUnlessBoolean } from "../notifications/policy.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { currentAppTimezone } from "../settings/preferences.mjs";
import { saveSettingsToServer } from "../settings/requests.mjs";
import { persistSettings } from "../settings/storage.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { applyStaticBuildAccounts, applyStaticBuildSettings, isStaticPreviewHost, loadStaticBuildConfig } from "../shell/static-preview.mjs";
import { load } from "../snapshot/requests.mjs";
import { accountsState } from "../state/accounts.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

function syncAccountDraftFromLoadedAccounts(force, preferredAccountId) {
  var accounts = accountsState.serviceAccounts || [];
  if (!accounts.length) {
    if (force) accountsState.accountDraft = defaultAccountDraft();
    return;
  }
  var preferred = String(preferredAccountId || "").trim();
  if (preferred) {
    var matched = accounts.filter(function (account) {
      return accountIdOf(account) === preferred;
    })[0];
    if (matched) {
      accountsState.editingAccountId = accountIdOf(matched);
      accountsState.accountDraft = accountDraftFromAccount(matched);
      return;
    }
  }
  if (!force && accountsState.editingAccountId) return;
  if (!force && accountsState.accountDraft && accountsState.accountDraft.id && accountsState.accountDraft.id !== "main") return;
  var selected = accounts[0];
  accountsState.editingAccountId = accountIdOf(selected);
  accountsState.accountDraft = accountDraftFromAccount(selected);
}

function defaultAccountDraft() {
  var currentSettings = settingsState && settingsState.settings ? settingsState.settings : defaultSettings;
  return {
    id: "main",
    label: "메인 계정",
    provider: "toss",
    baseUrl: "https://openapi.tossinvest.com",
    clientId: "",
    clientSecret: "",
    accountSeq: "",
    watchlistSymbols: currentSettings.watchlistSymbols || defaultSettings.watchlistSymbols,
    messageDeliveryLevel: "absoluteBeginner",
    notificationDetailLevel: "concise",
    quietHoursEnabled: true,
    quietHoursStart: "22:00",
    quietHoursEnd: "05:00",
    quietHoursTimezone: currentSettings.appTimezone || defaultSettings.appTimezone || "Asia/Seoul",
    enabled: true
  };
}

function createNewAccountDraft() {
  var draft = defaultAccountDraft();
  var usedIds = {};
  (accountsState.serviceAccounts || []).forEach(function (account) {
    var id = accountIdOf(account);
    if (id) usedIds[id] = true;
  });
  if (!usedIds[draft.id]) return draft;
  for (var index = 2; index < 1000; index += 1) {
    var candidate = "account-" + index;
    if (!usedIds[candidate]) {
      draft.id = candidate;
      draft.label = "추가 계정 " + index;
      return draft;
    }
  }
  draft.id = "account-" + Date.now();
  draft.label = "추가 계정";
  return draft;
}

function loadServiceAccounts(options) {
  options = options || {};
  accountsState.serviceAccountsLoading = true;
  accountsState.serviceAccountsError = "";
  if (isStaticPreviewHost()) {
    return loadStaticBuildConfig()
      .then(function (payload) {
        applyStaticBuildSettings(payload);
        applyStaticBuildAccounts(payload, Boolean(options.forceDraft));
      })
      .catch(function (error) {
        accountsState.serviceAccountsError = error.message || "정적 계정 DB 스냅샷을 읽지 못했습니다.";
      })
      .finally(function () {
        accountsState.serviceAccountsLoading = false;
        if (shellState.snapshot) render();
      });
  }
  return requestJson("/api/service-accounts")
    .then(function (payload) {
      accountsState.serviceAccounts = Array.isArray(payload.accounts) ? payload.accounts : [];
      accountsState.serviceAccountsLoaded = true;
      syncActiveWatchAccountId();
      syncAccountDraftFromLoadedAccounts(Boolean(options.forceDraft), options.draftAccountId);
    })
    .catch(function (error) {
      accountsState.serviceAccountsError = error.message || "계정 DB를 읽지 못했습니다.";
    })
    .finally(function () {
      accountsState.serviceAccountsLoading = false;
      if (shellState.snapshot) render();
    });
}

function accountDraftFromAccount(account) {
  var draft = {
    id: account.id || "",
    label: account.label || account.id || "",
    provider: account.provider || "toss",
    baseUrl: account.baseUrl || "https://openapi.tossinvest.com",
    clientId: "",
    clientSecret: "",
    accountSeq: textValueUnlessBoolean(account.accountSeq),
    watchlistSymbols: Array.isArray(account.watchlistSymbols) ? account.watchlistSymbols.join(",") : String(account.watchlistSymbols || ""),
    messageDeliveryLevel: normalizeMessageDeliveryLevel(account.messageDeliveryLevel),
    notificationDetailLevel: normalizeNotificationDetailLevel(account.notificationDetailLevel),
    quietHoursEnabled: account.quietHoursEnabled !== false,
    quietHoursStart: String(account.quietHoursStart || "22:00"),
    quietHoursEnd: String(account.quietHoursEnd || "05:00"),
    quietHoursTimezone: String(account.quietHoursTimezone || currentAppTimezone()),
    enabled: account.enabled !== false
  };
  draft._baseline = Object.assign({}, draft);
  return draft;
}

function serviceAccountPayloadFromDraft(source) {
  var draft = source || accountsState.accountDraft || defaultAccountDraft();
  var payload = {
    id: String(draft.id || "").trim(),
    label: String(draft.label || "").trim(),
    provider: String(draft.provider || "toss").trim(),
    baseUrl: String(draft.baseUrl || "https://openapi.tossinvest.com").trim(),
    accountSeq: String(draft.accountSeq || "").trim(),
    watchlistSymbols: normalizeSymbols(draft.watchlistSymbols || "").join(","),
    messageDeliveryLevel: normalizeMessageDeliveryLevel(draft.messageDeliveryLevel),
    notificationDetailLevel: normalizeNotificationDetailLevel(draft.notificationDetailLevel),
    quietHoursEnabled: draft.quietHoursEnabled !== false,
    quietHoursStart: String(draft.quietHoursStart || "22:00"),
    quietHoursEnd: String(draft.quietHoursEnd || "05:00"),
    quietHoursTimezone: String(draft.quietHoursTimezone || currentAppTimezone()),
    enabled: draft.enabled !== false
  };
  if (String(draft.clientId || "").trim()) payload.clientId = String(draft.clientId || "").trim();
  if (String(draft.clientSecret || "").trim()) payload.clientSecret = String(draft.clientSecret || "").trim();
  return payload;
}

function accountPayloadChanges(payload, baseline) {
  if (!baseline || baseline.id !== payload.id) return payload;
  var changes = { id: payload.id };
  Object.keys(payload).forEach(function (key) {
    if (key !== "id" && payload[key] !== baseline[key]) changes[key] = payload[key];
  });
  return changes;
}

function saveServiceAccount() {
  if (isStaticPreviewHost()) {
    accountsState.serviceAccountsError = "GitHub Pages에서는 실제 계정 DB를 저장할 수 없습니다. 로컬 서버에서 사용하세요.";
    render();
    return Promise.resolve();
  }
  var account = serviceAccountPayloadFromDraft();
  if (!account.id || !account.label) {
    accountsState.serviceAccountsError = "계정 ID와 표시 이름은 필요합니다.";
    render();
    return Promise.resolve();
  }
  accountsState.serviceAccountsLoading = true;
  accountsState.serviceAccountsError = "";
  accountsState.accountSaved = false;
  render();
  var baseline = accountsState.accountDraft && accountsState.accountDraft._baseline;
  var changes = accountPayloadChanges(account, baseline ? serviceAccountPayloadFromDraft(baseline) : null);
  return sendJson("/api/service-accounts", "POST", { account: changes })
    .then(function () {
      accountsState.accountSaved = true;
      accountsState.editingAccountId = account.id;
      return loadServiceAccounts({ forceDraft: true, draftAccountId: account.id });
    })
    .then(function () {
      showSnackbar("계정을 저장했습니다.");
    })
    .catch(function (error) {
      accountsState.serviceAccountsError = error.message || "계정을 저장하지 못했습니다.";
      showSnackbar(accountsState.serviceAccountsError, "danger");
    })
    .finally(function () {
      accountsState.serviceAccountsLoading = false;
      render();
    });
}

function removeServiceAccount(id) {
  if (isStaticPreviewHost()) {
    accountsState.serviceAccountsError = "GitHub Pages에서는 실제 계정 DB를 변경할 수 없습니다. 로컬 서버에서 사용하세요.";
    render();
    return Promise.resolve();
  }
  if (!id) return Promise.resolve();
  if (!window.confirm("계정을 삭제하면 연결 정보와 계정별 관심 종목이 제거됩니다. 계속할까요?")) return Promise.resolve();
  accountsState.serviceAccountsLoading = true;
  accountsState.serviceAccountsError = "";
  render();
  return sendJson("/api/service-accounts/" + encodeURIComponent(id), "DELETE")
    .then(function () {
      if (accountsState.editingAccountId === id) {
        accountsState.editingAccountId = "";
        accountsState.accountDraft = defaultAccountDraft();
      }
      return loadServiceAccounts({ forceDraft: true });
    })
    .then(function () {
      showSnackbar("계정을 삭제했습니다.");
    })
    .catch(function (error) {
      accountsState.serviceAccountsError = error.message || "계정을 삭제하지 못했습니다.";
      showSnackbar(accountsState.serviceAccountsError, "danger");
    })
    .finally(function () {
      accountsState.serviceAccountsLoading = false;
      render();
    });
}

function accountWatchlistPayload(account, symbols) {
  return {
    id: accountIdOf(account),
    label: String(account.label || account.id || "").trim(),
    provider: String(account.provider || "toss").trim(),
    baseUrl: String(account.baseUrl || "https://openapi.tossinvest.com").trim(),
    accountSeq: textValueUnlessBoolean(account.accountSeq),
    watchlistSymbols: normalizeSymbols((symbols || []).join(",")).join(","),
    enabled: account.enabled !== false
  };
}

function saveAccountWatchlistSymbols(accountId, symbols) {
  var account = accountById(accountId);
  if (!account) {
    accountsState.watchlistError = "관심 종목을 저장할 계정을 선택하세요.";
    render();
    return Promise.resolve();
  }
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    accountsState.watchlistError = "GitHub Pages에서는 계정별 관심 종목을 저장할 수 없습니다. 로컬 서버에서 사용하세요.";
    showSnackbar(accountsState.watchlistError, "danger");
    render();
    return Promise.resolve();
  }
  accountsState.watchlistSavingAccountId = accountIdOf(account);
  accountsState.watchlistError = "";
  accountsState.watchSuggestQuery = "";
  accountsState.watchSuggestItems = [];
  accountsState.watchSuggestLoading = false;
  accountsState.watchSuggestError = "";
  render();
  return sendJson("/api/service-accounts/" + encodeURIComponent(accountIdOf(account)) + "/watchlist", "PUT", { symbols: normalizeSymbols((symbols || []).join(",")) })
    .then(function (payload) {
      accountsState.activeWatchAccountId = accountIdOf(account);
      accountsState.editingWatchAccountId = "";
      accountsState.editingWatchSymbol = "";
      return loadServiceAccounts().then(function () { return payload; });
    })
    .then(function (payload) {
      showSnackbar(payload && payload.refresh && payload.refresh.running ? "관심 종목을 저장했고 최신 시세 수집을 시작했습니다." : "계정별 관심 종목을 저장했습니다.");
      return load({ refresh: true });
    })
    .catch(function (error) {
      accountsState.watchlistError = error.message || "계정별 관심 종목을 저장하지 못했습니다.";
      showSnackbar(accountsState.watchlistError, "danger");
    })
    .finally(function () {
      accountsState.watchlistSavingAccountId = "";
      render();
    });
}

function addAccountWatchSymbol(accountId, symbol) {
  var next = normalizeSymbols(symbol || "");
  if (!next.length) {
    accountsState.watchlistError = "추가할 종목을 입력하세요.";
    render();
    return Promise.resolve();
  }
  var account = accountById(accountId);
  var symbols = accountWatchlistSymbols(account);
  if (symbols.indexOf(next[0]) >= 0) {
    accountsState.watchlistError = "선택한 계정에 이미 추가된 관심 종목입니다.";
    render();
    return Promise.resolve();
  }
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    accountsState.watchlistError = "GitHub Pages는 읽기 전용입니다. 로컬 앱에서 관심 종목을 추가하세요.";
    showSnackbar(accountsState.watchlistError, "danger");
    render();
    return Promise.resolve();
  }
  accountsState.watchlistSavingAccountId = accountId;
  accountsState.watchlistError = "";
  render();
  return sendJson("/api/service-accounts/" + encodeURIComponent(accountId) + "/watchlist", "POST", { symbol: next[0] })
    .then(function (payload) {
      accountsState.activeWatchAccountId = accountId;
      accountsState.watchlistAccountPickerSymbol = "";
      return loadServiceAccounts().then(function () { return payload; });
    })
    .then(function (payload) {
      showSnackbar(payload && payload.refresh && payload.refresh.running ? "관심 종목에 추가했습니다. 시세·판단·알림 갱신을 시작합니다." : "관심 종목에 추가했습니다.");
      return load({ refresh: true });
    })
    .catch(function (error) {
      accountsState.watchlistError = error.message || "관심 종목을 추가하지 못했습니다.";
      showSnackbar(accountsState.watchlistError, "danger");
    })
    .finally(function () {
      accountsState.watchlistSavingAccountId = "";
      render();
    });
}

function removeAccountWatchSymbol(accountId, symbol) {
  var removeSymbol = String(symbol || "").toUpperCase();
  if (!accountById(accountId) || !removeSymbol) return Promise.resolve();
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    accountsState.watchlistError = "GitHub Pages는 읽기 전용입니다. 로컬 앱에서 관심 종목을 삭제하세요.";
    showSnackbar(accountsState.watchlistError, "danger");
    render();
    return Promise.resolve();
  }
  accountsState.watchlistSavingAccountId = accountId;
  accountsState.watchlistError = "";
  render();
  return sendJson("/api/service-accounts/" + encodeURIComponent(accountId) + "/watchlist/" + encodeURIComponent(removeSymbol), "DELETE", {})
    .then(function () {
      accountsState.watchlistAccountPickerSymbol = "";
      return loadServiceAccounts();
    })
    .then(function () {
      showSnackbar("관심 종목에서 삭제했습니다.");
      return load({ refresh: true });
    })
    .catch(function (error) {
      accountsState.watchlistError = error.message || "관심 종목을 삭제하지 못했습니다.";
      showSnackbar(accountsState.watchlistError, "danger");
    })
    .finally(function () {
      accountsState.watchlistSavingAccountId = "";
      render();
    });
}

function replaceAccountWatchSymbol(accountId, original, nextValue) {
  var originalSymbol = String(original || "").toUpperCase();
  var next = normalizeSymbols(nextValue || "");
  if (!next.length) {
    accountsState.watchlistError = "수정할 종목을 입력하세요.";
    render();
    return Promise.resolve();
  }
  var symbols = accountWatchlistSymbols(accountById(accountId));
  if (next[0] !== originalSymbol && symbols.indexOf(next[0]) >= 0) {
    accountsState.watchlistError = "선택한 계정에 이미 추가된 관심 종목입니다.";
    render();
    return Promise.resolve();
  }
  return saveAccountWatchlistSymbols(accountId, symbols.map(function (symbol) {
    return symbol === originalSymbol ? next[0] : symbol;
  }));
}

function addSymbolToPreferredWatchlist(symbol) {
  var account = activeWatchAccount();
  return account ? addAccountWatchSymbol(accountIdOf(account), symbol) : addWatchSymbol(symbol);
}

function saveWatchlistSymbols(symbols) {
  settingsState.settings.watchlistSymbols = normalizeSymbols(symbols.join(",")).join(",");
  accountsState.editingWatchSymbol = "";
  accountsState.watchlistError = "";
  accountsState.watchSuggestQuery = "";
  accountsState.watchSuggestItems = [];
  accountsState.watchSuggestLoading = false;
  accountsState.watchSuggestError = "";
  persistSettings();
  var save = isStaticPreviewHost()
    ? Promise.resolve()
    : saveSettingsToServer();
  return save.then(function () {
    showSnackbar("관심 종목을 저장했습니다.");
  }).catch(function (error) {
    accountsState.watchlistError = error.message || "관심 종목을 서버 설정 DB에 저장하지 못했습니다.";
    showSnackbar(accountsState.watchlistError, "danger");
  }).finally(function () {
    render();
  });
}

function addWatchSymbol(symbol) {
  var next = normalizeSymbols(symbol || "");
  if (!next.length) {
    accountsState.watchlistError = "추가할 종목을 입력하세요.";
    render();
    return Promise.resolve();
  }
  var symbols = watchlistSymbols();
  if (symbols.indexOf(next[0]) >= 0) {
    accountsState.watchlistError = "이미 추가된 관심 종목입니다.";
    render();
    return Promise.resolve();
  }
  return saveWatchlistSymbols(symbols.concat(next[0]));
}

export { accountDraftFromAccount, addAccountWatchSymbol, addWatchSymbol, createNewAccountDraft, defaultAccountDraft, loadServiceAccounts, removeAccountWatchSymbol, removeServiceAccount, replaceAccountWatchSymbol, saveAccountWatchlistSymbols, saveServiceAccount, saveWatchlistSymbols, syncAccountDraftFromLoadedAccounts };
