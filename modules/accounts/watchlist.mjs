import { addAccountWatchSymbol } from "./commands.mjs";
import { clientKnownStockInfo, normalizeSymbols, stockDisplayName, watchlistSymbols } from "../instruments/catalog.mjs";
import { render } from "../render/scheduler.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { accountsState } from "../state/accounts.mjs";
import { settingsState } from "../state/settings.mjs";

function accountWatchlistSymbols(account) {
  if (!account) return [];
  return Array.isArray(account.watchlistSymbols)
    ? normalizeSymbols(account.watchlistSymbols.join(","))
    : normalizeSymbols(account.watchlistSymbols || "");
}

function accountIdOf(account) {
  return String((account && (account.id || account.accountId)) || "").trim();
}

function accountById(id) {
  var normalized = String(id || "").trim();
  return (accountsState.serviceAccounts || []).filter(function (account) {
    return accountIdOf(account) === normalized;
  })[0] || null;
}

function syncActiveWatchAccountId() {
  var accounts = accountsState.serviceAccounts || [];
  if (!accounts.length) {
    accountsState.activeWatchAccountId = "";
    return;
  }
  if (accountById(accountsState.activeWatchAccountId)) return;
  accountsState.activeWatchAccountId = accountIdOf(accounts[0]);
}

function activeWatchAccount() {
  syncActiveWatchAccountId();
  return accountById(accountsState.activeWatchAccountId);
}

function preferredWatchlistSymbols() {
  var account = activeWatchAccount();
  return account ? accountWatchlistSymbols(account) : watchlistSymbols();
}

function watchlistAccountsForSymbol(symbol) {
  var normalized = String(symbol || "").toUpperCase();
  return (accountsState.serviceAccounts || []).filter(function (account) {
    return accountWatchlistSymbols(account).indexOf(normalized) >= 0;
  });
}

function openWatchlistAccountPicker(symbol) {
  var normalized = normalizeSymbols(symbol || "")[0] || "";
  if (!normalized) return;
  var accounts = (accountsState.serviceAccounts || []).filter(function (account) { return account.enabled !== false; });
  if (accounts.length <= 1) {
    if (accounts[0]) addAccountWatchSymbol(accountIdOf(accounts[0]), normalized);
    else showSnackbar("관심 종목을 저장할 계정을 먼저 등록하세요.", "danger");
    return;
  }
  accountsState.watchlistAccountPickerSymbol = normalized;
  render();
}

function renderWatchlistAccountPicker() {
  var symbol = String(accountsState.watchlistAccountPickerSymbol || "").toUpperCase();
  if (!symbol) return "";
  var info = clientKnownStockInfo(symbol);
  var locked = isStaticPreviewHost() || settingsState.serverSettingsLocked;
  return [
    '<div class="watchlist-picker-backdrop" data-watchlist-picker-close>',
    '<section class="watchlist-picker" role="dialog" aria-modal="true" aria-labelledby="watchlist-picker-title" tabindex="-1" data-watchlist-picker-dialog>',
    '<header><div><span>관심 종목 계정 선택</span><h2 id="watchlist-picker-title">' + escapeHtml(stockDisplayName(symbol, info)) + '</h2><p>' + escapeHtml(symbol + "을 저장할 계정을 선택하세요.") + '</p></div><button class="icon-button" type="button" data-watchlist-picker-close title="닫기" aria-label="닫기">&times;</button></header>',
    '<div class="watchlist-picker-accounts">',
    (accountsState.serviceAccounts || []).filter(function (account) { return account.enabled !== false; }).map(function (account) {
      var accountId = accountIdOf(account);
      var included = accountWatchlistSymbols(account).indexOf(symbol) >= 0;
      var busy = accountsState.watchlistSavingAccountId === accountId;
      return '<button type="button" data-watchlist-picker-account="' + escapeHtml(accountId) + '" data-watchlist-picker-action="' + (included ? "remove" : "add") + '"' + (locked || busy ? " disabled" : "") + '><span><strong>' + escapeHtml(account.label || accountId) + '</strong><em>' + escapeHtml(accountId + " · 관심 " + accountWatchlistSymbols(account).length + "개") + '</em></span><b>' + escapeHtml(busy ? "저장 중" : (included ? "삭제" : "추가")) + '</b></button>';
    }).join(""),
    '</div>',
    locked ? '<p class="watchlist-picker-readonly">GitHub Pages는 읽기 전용입니다. 로컬 앱에서 변경하세요.</p>' : '',
    '</section>',
    '</div>'
  ].join("");
}

function watchlistAccountLabel(account) {
  account = account || activeWatchAccount();
  return account ? String(account.label || account.id || "계정") : "기본 관심목록";
}

function watchSymbolDisplay(symbol, item) {
  var original = String(symbol || (item && item.symbol) || "").trim().toUpperCase();
  var name = stockDisplayName(original, item);
  return {
    symbol: original,
    name: name,
    label: name
  };
}

function watchSymbolListText(symbols) {
  var labels = (symbols || []).map(function (symbol) {
    return watchSymbolDisplay(symbol).label;
  }).filter(Boolean);
  return labels.length ? labels.join(", ") : "-";
}

function renderWatchSymbolChip(symbol, item) {
  var display = watchSymbolDisplay(symbol, item);
  return '<span class="chip" title="' + escapeHtml(display.name) + '">' + escapeHtml(display.label) + '</span>';
}

function allAccountWatchlistSymbols() {
  var seen = {};
  var symbols = [];
  (accountsState.serviceAccounts || []).forEach(function (account) {
    accountWatchlistSymbols(account).forEach(function (symbol) {
      if (seen[symbol]) return;
      seen[symbol] = true;
      symbols.push(symbol);
    });
  });
  return symbols;
}

export { accountById, accountIdOf, accountWatchlistSymbols, activeWatchAccount, allAccountWatchlistSymbols, openWatchlistAccountPicker, preferredWatchlistSymbols, renderWatchSymbolChip, renderWatchlistAccountPicker, syncActiveWatchAccountId, watchSymbolListText, watchlistAccountLabel };
