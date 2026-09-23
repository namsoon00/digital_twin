import { accountDraftFromAccount, addAccountWatchSymbol, addWatchSymbol, removeAccountWatchSymbol, removeServiceAccount, replaceAccountWatchSymbol, saveServiceAccount } from "./commands.mjs";
import { updateAccountQuietHoursDraftSummary } from "./editor.mjs";
import { accountIdOf, accountWatchlistSymbols, activeWatchAccount, openWatchlistAccountPicker } from "./watchlist.mjs";
import { loadWatchSuggestions } from "../instruments/suggestions.mjs";
import { normalizeAccountSection, sectionModeForPage, setPageViewMode, writeAccountSectionHistory } from "../navigation/routes.mjs";
import { render } from "../render/scheduler.mjs";
import { accountsState } from "../state/accounts.mjs";
import { navigationState } from "../state/navigation.mjs";

const watchSuggestTimerCell = { value: null };

function bindAccountsControls(app) {
var accountForm = app.querySelector("[data-account-form]");
if (accountForm) {
    accountForm.addEventListener("submit", function (event) {
      event.preventDefault();
      saveServiceAccount();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-account-field]")).forEach(function (field) {
    field.addEventListener("input", function () {
      var name = field.getAttribute("data-account-field");
      if (!name) return;
      accountsState.accountDraft[name] = field.type === "checkbox" ? field.checked : field.value;
      accountsState.accountSaved = false;
      if (name.indexOf("quietHours") === 0) updateAccountQuietHoursDraftSummary();
    });
    field.addEventListener("change", function () {
      var name = field.getAttribute("data-account-field");
      if (!name) return;
      accountsState.accountDraft[name] = field.type === "checkbox" ? field.checked : field.value;
      if (name.indexOf("quietHours") === 0) updateAccountQuietHoursDraftSummary();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-account-edit]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var id = button.getAttribute("data-account-edit");
      var account = (accountsState.serviceAccounts || []).filter(function (item) { return item.id === id; })[0];
      if (!account) return;
      accountsState.editingAccountId = id;
      accountsState.accountDraft = accountDraftFromAccount(account);
      accountsState.accountSaved = false;
      accountsState.serviceAccountsError = "";
      navigationState.activeTab = "accounts";
      setPageViewMode("accounts", "settings");
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-account-remove]")).forEach(function (button) {
    button.addEventListener("click", function () {
      removeServiceAccount(button.getAttribute("data-account-remove"));
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-watch-account-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      accountsState.activeWatchAccountId = button.getAttribute("data-watch-account-select") || "";
      accountsState.editingWatchAccountId = "";
      accountsState.editingWatchSymbol = "";
      accountsState.watchlistError = "";
      accountsState.watchSuggestQuery = "";
      accountsState.watchSuggestItems = [];
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-account-section]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var section = normalizeAccountSection(button.getAttribute("data-account-section"));
      if (section === accountsState.activeAccountSection) return;
      accountsState.activeAccountSection = section;
      navigationState.pageViewModes.accounts = sectionModeForPage("accounts", section);
      writeAccountSectionHistory(section);
      render({ transition: "section" });
    });
  });
var settingsAccountSelect = app.querySelector("[data-settings-account-select]");
if (settingsAccountSelect) {
    settingsAccountSelect.addEventListener("change", function () {
      accountsState.activeWatchAccountId = settingsAccountSelect.value || "";
      render({ transition: "section" });
    });
  }
var watchAddForm = app.querySelector("[data-watch-add-form]");
if (watchAddForm) {
    var watchSymbolInput = watchAddForm.querySelector("[data-watch-symbol-input]");
    var watchSuggestBox = app.querySelector("[data-watch-suggest-list]");
    if (watchSymbolInput) {
      watchSymbolInput.addEventListener("input", function () {
        loadWatchSuggestions(watchSymbolInput.value, watchSuggestBox, watchSymbolInput);
      });
      watchSymbolInput.addEventListener("focus", function () {
        if (watchSymbolInput.value) {
          loadWatchSuggestions(watchSymbolInput.value, watchSuggestBox, watchSymbolInput);
        }
      });
    }
    watchAddForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var input = watchAddForm.querySelector('input[name="symbol"]');
      var accountId = watchAddForm.getAttribute("data-watch-account-id") || "";
      if (accountId) {
        addAccountWatchSymbol(accountId, input ? input.value : "");
      } else {
        addWatchSymbol(input ? input.value : "");
      }
    });
  }
var watchSuggestList = app.querySelector("[data-watch-suggest-list]");
if (watchSuggestList) {
    watchSuggestList.addEventListener("click", function (event) {
      var target = event.target;
      while (target && target !== watchSuggestList && !target.getAttribute("data-watch-suggest-symbol")) {
        target = target.parentNode;
      }
      if (!target || target === watchSuggestList) return;
      event.preventDefault();
      var accountId = watchSuggestList.getAttribute("data-watch-account-id") || "";
      if (accountId) {
        addAccountWatchSymbol(accountId, target.getAttribute("data-watch-suggest-symbol"));
      } else {
        addWatchSymbol(target.getAttribute("data-watch-suggest-symbol"));
      }
    });
  }
var marketWatchAccount = app.querySelector("[data-market-watch-account]");
if (marketWatchAccount) {
    marketWatchAccount.addEventListener("change", function () {
      accountsState.activeWatchAccountId = marketWatchAccount.value || "";
      accountsState.watchlistError = "";
      render();
    });
  }
var symbolAddAccount = app.querySelector("[data-symbol-add-account]");
if (symbolAddAccount) {
    symbolAddAccount.addEventListener("change", function () {
      accountsState.activeWatchAccountId = symbolAddAccount.value || "";
      render();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-symbol-watch-toggle]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      var symbol = String(button.getAttribute("data-symbol-watch-toggle") || "").toUpperCase();
      var account = activeWatchAccount();
      if (account && accountWatchlistSymbols(account).indexOf(symbol) >= 0) {
        removeAccountWatchSymbol(accountIdOf(account), symbol);
        return;
      }
      openWatchlistAccountPicker(symbol);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-watchlist-picker-close]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (button.classList && button.classList.contains("watchlist-picker-backdrop") && event.target !== button) return;
      accountsState.watchlistAccountPickerSymbol = "";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-watchlist-picker-account]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var accountId = button.getAttribute("data-watchlist-picker-account") || "";
      var action = button.getAttribute("data-watchlist-picker-action") || "add";
      var symbol = String(accountsState.watchlistAccountPickerSymbol || "").toUpperCase();
      accountsState.activeWatchAccountId = accountId;
      if (action === "remove") removeAccountWatchSymbol(accountId, symbol);
      else addAccountWatchSymbol(accountId, symbol);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-account-watch-edit]")).forEach(function (button) {
    button.addEventListener("click", function () {
      accountsState.editingWatchAccountId = button.getAttribute("data-watch-account-id") || "";
      accountsState.editingWatchSymbol = String(button.getAttribute("data-account-watch-edit") || "").toUpperCase();
      accountsState.watchlistError = "";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-account-watch-remove]")).forEach(function (button) {
    button.addEventListener("click", function () {
      removeAccountWatchSymbol(
        button.getAttribute("data-watch-account-id") || "",
        button.getAttribute("data-account-watch-remove") || ""
      );
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-account-watch-edit-form]")).forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var input = form.querySelector('input[name="symbol"]');
      replaceAccountWatchSymbol(
        form.getAttribute("data-watch-account-id") || "",
        form.getAttribute("data-account-watch-edit-form") || "",
        input ? input.value : ""
      );
    });
  });
var accountWatchCancel = app.querySelector("[data-account-watch-cancel]");
if (accountWatchCancel) {
    accountWatchCancel.addEventListener("click", function () {
      accountsState.editingWatchAccountId = "";
      accountsState.editingWatchSymbol = "";
      accountsState.watchlistError = "";
      render();
    });
  }
}

export { bindAccountsControls, watchSuggestTimerCell };
