import { accountIdOf, accountWatchlistSymbols, activeWatchAccount, allAccountWatchlistSymbols, renderWatchSymbolChip, watchlistAccountLabel } from "./watchlist.mjs";
import { clientKnownStockInfo, stockDisplayMeta, stockDisplayName } from "../instruments/catalog.mjs";
import { renderWatchSuggestList } from "../instruments/suggestions.mjs";
import { marketLabel, renderWatchAlertMeta } from "../instruments/universe-view.mjs";
import { formatCurrency, recordChangedAt, renderRecordChangedAt, signedPct } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs } from "../shell/layout.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { accountsState } from "../state/accounts.mjs";
import { settingsState } from "../state/settings.mjs";

function renderAccountDirectoryPanel(options) {
  options = options || {};
  var accounts = accountsState.serviceAccounts || [];
  var enabled = accounts.filter(function (account) { return account.enabled !== false; }).length;
  var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
  var accountSeqReady = accounts.filter(function (account) { return account.accountSeq; }).length;
  var classes = "panel account-directory-panel" + (options.full ? " account-directory-wide" : "");
  return [
    '<article class="' + classes + '">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Account DB</p>',
    '<h2>DB 저장 계정</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(enabled + "/" + accounts.length) + '</span>',
    '</div>',
    '<div class="account-directory-summary">',
    renderDirectoryStat("활성", enabled + "/" + accounts.length),
    renderDirectoryStat("토스 API", tossReady + "개"),
    renderDirectoryStat("계좌 seq", accountSeqReady + "개"),
    '</div>',
    '<div class="account-card-list">',
    accountsState.serviceAccountsLoading ? '<p class="subtle">계정 DB를 읽는 중입니다.</p>' : '',
    accountsState.serviceAccountsError ? '<p class="form-error">' + escapeHtml(accountsState.serviceAccountsError) + '</p>' : '',
    accounts.length ? accounts.map(function (account) {
      return renderAccountDirectoryRow(account, options);
    }).join("") : '<p class="subtle">아직 DB에 저장된 계정이 없습니다. 계정·연결에서 등록하세요.</p>',
    '</div>',
    '</article>'
  ].join("");
}

function renderDirectoryStat(label, value) {
  return [
    '<span>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '</span>'
  ].join("");
}

function renderAccountDirectoryRow(account, options) {
  options = options || {};
  var symbols = accountWatchlistSymbols(account);
  var enabled = account.enabled !== false;
  var provider = String(account.provider || "toss").toUpperCase();
  return [
    '<div class="account-card"' + cardTypeAttrs("ledger-row", enabled ? "watch" : "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="account-card-head">',
    '<span class="account-provider-badge">' + escapeHtml(provider.slice(0, 4)) + '</span>',
    '<div>',
    '<strong>' + escapeHtml(account.label || account.id || "-") + '</strong>',
    '<span>' + escapeHtml(account.id || "-") + ' · ' + escapeHtml(account.provider || "toss") + ' · ' + escapeHtml(enabled ? "사용" : "중지") + '</span>',
    renderRecordChangedAt(account),
    '</div>',
    '<button class="mini-button" data-account-edit="' + escapeHtml(account.id || "") + '">수정</button>',
    '</div>',
    options.compact ? renderAccountCredentialPills(account) : renderAccountCredentialSummary(account),
    '<div class="account-card-meta"><span class="chip">관심 ' + escapeHtml(symbols.length) + '개</span><span class="chip">' + escapeHtml(account.accountSeq ? "계좌 seq 저장" : "계좌 seq 선택 안함") + '</span></div>',
    options.compact ? '' : '<div class="chip-row">' + (symbols.length ? symbols.map(function (symbol) {
      return renderWatchSymbolChip(symbol);
    }).join("") : '<span class="subtle">계정에 저장된 관심 종목이 없습니다.</span>') + '</div>',
    '</div>'
  ].join("");
}

function renderAccountCredentialPills(account) {
  account = account || {};
  return [
    '<div class="account-credential-pills">',
    configuredChip("Toss API", Boolean(account.clientId && account.clientSecret)),
    configuredChip("계좌 seq", Boolean(account.accountSeq), account.accountSeq || "선택"),
    '</div>'
  ].join("");
}

function configuredChip(label, configured, detail) {
  return [
    '<span class="chip ' + (configured ? "ok" : "missing") + '">',
    escapeHtml(label + " " + (configured ? "설정됨" : "미설정")),
    detail ? '<em>' + escapeHtml(detail) + '</em>' : '',
    '</span>'
  ].join("");
}

function renderAccountCredentialSummary(account) {
  account = account || {};
  return [
    '<div class="account-credential-grid">',
    '<div>',
    '<strong>토스</strong>',
    '<span>' + escapeHtml(account.baseUrl || "https://openapi.tossinvest.com") + '</span>',
    '<div class="chip-row">',
    configuredChip("API key", Boolean(account.clientId)),
    configuredChip("Secret", Boolean(account.clientSecret)),
    configuredChip("계좌 seq", Boolean(account.accountSeq), account.accountSeq || "선택 안함"),
    '</div>',
    '</div>',
    '<div>',
    '<strong>계정 식별</strong>',
    '<span>' + escapeHtml((account.provider || "toss") + " · " + (account.enabled === false ? "중지" : "사용")) + '</span>',
    '<div class="chip-row">',
    configuredChip("표시 이름", Boolean(account.label), account.label || ""),
    configuredChip("계정 ID", Boolean(account.id), account.id || ""),
    '</div>',
    '</div>',
    '</div>'
  ].join("");
}

function renderEmptyAccountCredentialSummary() {
  return [
    '<div class="account-credential-grid empty">',
    '<div>',
    '<strong>토스</strong>',
    '<span>새 계정을 저장하면 API key, secret, 계좌 seq 설정 상태가 여기에 표시됩니다.</span>',
    '<div class="chip-row">' + configuredChip("API key", false) + configuredChip("Secret", false) + '</div>',
    '</div>',
    '<div>',
    '<strong>계정 식별</strong>',
    '<span>표시 이름, 증권사, 사용 여부는 계정 저장 후 요약됩니다.</span>',
    '<div class="chip-row">' + configuredChip("표시 이름", false) + configuredChip("계정 ID", false) + '</div>',
    '</div>',
    '</div>'
  ].join("");
}

function accountRowStatusChip(account) {
  var ready = Boolean(account.clientId && account.clientSecret);
  if (account.enabled === false) return '<span class="status-pill demo">중지</span>';
  return '<span class="status-pill ' + (ready ? "live" : "demo") + '">' + escapeHtml(ready ? "연결 완료" : "설정 확인") + '</span>';
}

function renderAccountWatchlistPanel(options, snapshot) {
  options = options || {};
  var accounts = accountsState.serviceAccounts || [];
  var merged = allAccountWatchlistSymbols();
  var activeAccount = activeWatchAccount();
  var editable = Boolean(options.editable);
  var classes = "panel account-watchlist-panel" + (options.full ? " account-watchlist-wide" : "");
  return [
    '<article class="' + classes + '">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Account Watchlist</p>',
    '<h2>' + escapeHtml(editable ? "계정별 관심 종목 등록" : "계정별 관심 종목") + '</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(merged.length) + '</span>',
    '</div>',
    editable ? renderAccountWatchlistWorkbench(accounts, activeAccount, snapshot) : [
      '<div class="watch-account-list">',
      accounts.length ? accounts.map(function (account) {
        return renderAccountWatchlistRow(account, { selectable: false });
      }).join("") : '<p class="subtle">계정 DB를 읽으면 계정별 관심 종목이 여기에 표시됩니다.</p>',
      '</div>'
    ].join(""),
    '</article>'
  ].join("");
}

function renderAccountWatchlistWorkbench(accounts, activeAccount, snapshot) {
  return [
    '<div class="account-watchlist-workbench">',
    '<div class="watch-account-rail">',
    '<div class="account-column-head"><strong>계정 선택</strong><span>관심 종목은 선택한 계정에 저장됩니다.</span></div>',
    accountsState.serviceAccountsLoading ? '<p class="subtle">계정 DB를 읽는 중입니다.</p>' : '',
    accountsState.serviceAccountsError ? '<p class="form-error">' + escapeHtml(accountsState.serviceAccountsError) + '</p>' : '',
    accounts.length ? accounts.map(function (account) {
      return renderAccountWatchlistRow(account, { selectable: true });
    }).join("") : '<p class="subtle">계정·연결에서 먼저 계정을 등록하세요.</p>',
    '</div>',
    renderAccountWatchlistEditor(activeAccount, snapshot),
    '</div>'
  ].join("");
}

function renderAccountWatchlistRow(account, options) {
  options = options || {};
  var symbols = accountWatchlistSymbols(account);
  var active = accountIdOf(account) === accountsState.activeWatchAccountId;
  var tag = options.selectable ? "button" : "div";
  var attrs = options.selectable
    ? ' type="button" data-watch-account-select="' + escapeHtml(accountIdOf(account)) + '"'
    : "";
  return [
    '<' + tag + ' class="watch-account-row' + (options.selectable ? " selectable" : "") + (active ? " active" : "") + '"' + cardTypeAttrs("ledger-row", active ? "watch" : "hold") + attrs + '>',
    '<div>',
    '<strong>' + escapeHtml(account.label || account.id || "-") + '</strong>',
    '<span>' + escapeHtml(account.id || "-") + ' · ' + escapeHtml(account.enabled === false ? "중지" : "사용") + '</span>',
    renderRecordChangedAt(account),
    '</div>',
    '<div class="chip-row">',
    symbols.length ? symbols.map(function (symbol) {
      return renderWatchSymbolChip(symbol);
    }).join("") : '<span class="subtle">저장된 관심 종목 없음</span>',
    '</div>',
    options.selectable ? '<span class="watch-account-action">' + escapeHtml(active ? "선택됨" : "관리") + '</span>' : '',
    '</' + tag + '>'
  ].join("");
}

function accountWatchlistQuoteLookup(snapshot) {
  var toss = snapshot && snapshot.toss ? snapshot.toss : {};
  var lookup = {};
  (toss.watchlist || []).forEach(function (item) {
    lookup[String(item.symbol || "").toUpperCase()] = item;
  });
  ((toss.positions || []) || []).forEach(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    if (!symbol) return;
    lookup[symbol] = Object.assign({}, item, {
      source: item.source || "holding",
      quoteStatus: "보유 종목으로 분류됨"
    });
  });
  return lookup;
}

function renderAccountWatchlistEditor(account, snapshot) {
  if (!account) {
    return [
      '<div class="account-watchlist-editor empty">',
      '<div class="settings-note">',
      '<strong>계정이 필요합니다</strong>',
      '<p>관심 종목은 계정별로 저장됩니다. 계정·연결에서 계정을 만든 뒤 이 화면에서 종목을 등록하세요.</p>',
      '</div>',
      '</div>'
    ].join("");
  }
  var accountId = accountIdOf(account);
  var symbols = accountWatchlistSymbols(account);
  var lookup = accountWatchlistQuoteLookup(snapshot);
  var locked = isStaticPreviewHost() || settingsState.serverSettingsLocked || accountsState.watchlistSavingAccountId === accountId;
  return [
    '<div class="account-watchlist-editor">',
    '<div class="account-watchlist-editor-head">',
    '<div>',
    '<strong>' + escapeHtml(watchlistAccountLabel(account)) + '</strong>',
    '<span>' + escapeHtml(accountId + " · 관심 " + symbols.length + "개 · " + (account.enabled === false ? "중지" : "사용")) + '</span>',
    '</div>',
    '<button class="mini-button" type="button" data-account-edit="' + escapeHtml(accountId) + '">계정 설정</button>',
    '</div>',
    '<div class="watch-editor account-watch-editor">',
    '<form class="watch-add-form" data-watch-add-form data-watch-account-id="' + escapeHtml(accountId) + '">',
    '<input name="symbol" data-watch-symbol-input role="combobox" aria-autocomplete="list" aria-controls="watch-symbol-suggestions" aria-expanded="' + (accountsState.watchSuggestQuery ? "true" : "false") + '" placeholder="회사명으로 검색" value="' + escapeHtml(accountsState.watchSuggestQuery || "") + '" autocomplete="off"' + (locked ? " disabled" : "") + ' />',
    '<button class="text-button primary"' + (locked ? " disabled" : "") + '>' + escapeHtml(accountsState.watchlistSavingAccountId === accountId ? "저장 중" : "추가") + '</button>',
    '</form>',
    '<div class="watch-suggest-box" id="watch-symbol-suggestions" role="listbox" data-watch-suggest-list data-watch-account-id="' + escapeHtml(accountId) + '">' + renderWatchSuggestList() + '</div>',
    '<p class="subtle">검색 후 저장하면 이 계정의 알림·모니터링 기준으로 쓰입니다. 시장 전체 목록은 종목 탐색 탭에서 따로 봅니다.</p>',
    accountsState.watchlistError ? '<p class="form-error">' + escapeHtml(accountsState.watchlistError) + '</p>' : '',
    '</div>',
    '<div class="account-watch-symbol-list">',
    symbols.length ? symbols.map(function (symbol) {
      return renderAccountWatchSymbolRow(account, symbol, lookup[symbol] || clientKnownStockInfo(symbol), locked);
    }).join("") : '<p class="subtle">이 계정에 등록된 관심 종목이 없습니다.</p>',
    '</div>',
    '</div>'
  ].join("");
}

function renderAccountWatchSymbolRow(account, symbol, item, locked) {
  var accountId = accountIdOf(account);
  var original = String(symbol || "").toUpperCase();
  if (accountsState.editingWatchAccountId === accountId && accountsState.editingWatchSymbol === original) {
    return [
      '<form class="account-watch-edit-row" data-account-watch-edit-form="' + escapeHtml(original) + '" data-watch-account-id="' + escapeHtml(accountId) + '">',
      '<input name="symbol" value="' + escapeHtml(original) + '" autocomplete="off" />',
      '<button class="text-button primary">저장</button>',
      '<button class="text-button" type="button" data-account-watch-cancel>취소</button>',
      '</form>'
    ].join("");
  }
  var merged = Object.assign(clientKnownStockInfo(original), item || {}, { symbol: original });
  return [
    '<div class="account-watch-symbol-row"' + cardTypeAttrs("ledger-row") + '>',
    '<div class="account-watch-symbol-main">',
    '<strong>' + escapeHtml(stockDisplayName(original, merged)) + '</strong>',
    '<span>' + escapeHtml(stockDisplayMeta(merged, [marketLabel(merged.market || "-"), merged.sector || "-"])) + '</span>',
    renderRecordChangedAt(merged, recordChangedAt(account)),
    renderWatchAlertMeta(merged),
    '</div>',
    '<div class="account-watch-symbol-side">',
    '<strong>' + escapeHtml(merged.currentPrice ? formatCurrency(merged.currentPrice, merged.currency) : "시세 대기") + '</strong>',
    '<span>' + escapeHtml(merged.changeRate == null ? merged.quoteStatus || "토스 시세 연결 후 표시" : signedPct(merged.changeRate)) + '</span>',
    '<div class="row-actions">',
    '<button class="mini-button" data-account-watch-edit="' + escapeHtml(original) + '" data-watch-account-id="' + escapeHtml(accountId) + '"' + (locked ? " disabled" : "") + '>수정</button>',
    '<button class="mini-button danger" data-account-watch-remove="' + escapeHtml(original) + '" data-watch-account-id="' + escapeHtml(accountId) + '"' + (locked ? " disabled" : "") + '>삭제</button>',
    '</div>',
    '</div>',
    '</div>'
  ].join("");
}

export { accountRowStatusChip, configuredChip, renderAccountCredentialSummary, renderDirectoryStat, renderEmptyAccountCredentialSummary };
