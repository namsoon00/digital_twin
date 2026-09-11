import { watchSuggestTimerCell } from "../accounts/actions.mjs";
import { allAccountWatchlistSymbols } from "../accounts/watchlist.mjs";
import { clientKnownStockInfo, stockDisplayMeta, stockDisplayName, stockSearchAliasSymbols, watchlistSymbols } from "./catalog.mjs";
import { marketLabel } from "./universe-view.mjs";
import { requestJson } from "../requests/json.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { accountsState } from "../state/accounts.mjs";
import { shellState } from "../state/shell.mjs";
import { universeState } from "../state/universe.mjs";

var watchSuggestRequestId = 0;

function suggestionKey(item) {
  return String((item && item.symbol) || "").trim().toUpperCase();
}

function mergeSuggestionItems(primary, secondary, limit) {
  var seen = {};
  var merged = [];
  [primary || [], secondary || []].forEach(function (items) {
    items.forEach(function (item) {
      var key = suggestionKey(item);
      if (!key || seen[key]) return;
      seen[key] = true;
      merged.push(item);
    });
  });
  return merged.slice(0, limit || 8);
}

function localWatchSuggestItems(query) {
  var normalized = String(query || "").trim().toUpperCase();
  if (!normalized) return [];
  var aliasSymbols = stockSearchAliasSymbols(query);
  var candidates = [];
  aliasSymbols.forEach(function (symbol) {
    candidates.push(clientKnownStockInfo(symbol));
  });
  (universeState.symbolUniverse.items || []).forEach(function (item) {
    candidates.push(item);
  });
  watchlistSymbols().concat(allAccountWatchlistSymbols()).forEach(function (symbol) {
    candidates.push(clientKnownStockInfo(symbol));
  });
  var toss = shellState.snapshot && shellState.snapshot.toss ? shellState.snapshot.toss : {};
  (toss.positions || []).concat(toss.watchlist || []).forEach(function (item) {
    if (item && item.symbol) candidates.push(item);
  });
  return mergeSuggestionItems(candidates.filter(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    return aliasSymbols.indexOf(symbol) >= 0
      || symbol.indexOf(normalized) >= 0
      || String(item.name || "").toUpperCase().indexOf(normalized) >= 0;
  }), [], 8);
}

function watchSuggestPath(query) {
  var params = new URLSearchParams();
  params.set("query", String(query || "").trim());
  params.set("limit", "8");
  params.set("offset", "0");
  return "/api/symbol-universe/suggest?" + params.toString();
}

function renderWatchSuggestList() {
  var query = String(accountsState.watchSuggestQuery || "").trim();
  if (!query) return "";
  var items = accountsState.watchSuggestItems || [];
  if (accountsState.watchSuggestLoading && !items.length) {
    return '<p class="subtle watch-suggest-message">종목을 검색하는 중입니다.</p>';
  }
  if (accountsState.watchSuggestError) {
    return '<p class="form-error">' + escapeHtml(accountsState.watchSuggestError) + '</p>';
  }
  if (!items.length) {
    return '<p class="subtle watch-suggest-message">검색 결과가 없습니다. 종목명을 다시 확인하세요.</p>';
  }
  return items.map(function (item) {
    var symbol = suggestionKey(item);
    return [
      '<button class="watch-suggest-option" type="button" role="option" data-watch-suggest-symbol="' + escapeHtml(symbol) + '">',
      '<span>',
      '<strong>' + escapeHtml(stockDisplayName(symbol, item)) + '</strong>',
      '<em>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || item.exchange), item.currency || item.assetType || "-"])) + '</em>',
      '</span>',
      '<b>추가</b>',
      '</button>'
    ].join("");
  }).join("") + (accountsState.watchSuggestLoading ? '<p class="subtle watch-suggest-message">DB 후보를 확인하는 중입니다.</p>' : '');
}

function updateWatchSuggestBox(box, input) {
  if (box) box.innerHTML = renderWatchSuggestList();
  if (input) {
    input.setAttribute("aria-expanded", String(Boolean(accountsState.watchSuggestQuery)));
    input.setAttribute("aria-busy", String(Boolean(accountsState.watchSuggestLoading)));
  }
}

function loadWatchSuggestions(query, box, input) {
  var normalized = String(query || "").trim();
  var requestId = ++watchSuggestRequestId;
  accountsState.watchSuggestQuery = normalized;
  accountsState.watchSuggestError = "";
  if (watchSuggestTimerCell.value) clearTimeout(watchSuggestTimerCell.value);
  if (!normalized) {
    accountsState.watchSuggestItems = [];
    accountsState.watchSuggestLoading = false;
    updateWatchSuggestBox(box, input);
    return;
  }
  var localItems = localWatchSuggestItems(normalized);
  accountsState.watchSuggestItems = localItems;
  accountsState.watchSuggestLoading = true;
  updateWatchSuggestBox(box, input);
  watchSuggestTimerCell.value = setTimeout(function () {
    var request = isStaticPreviewHost()
      ? Promise.resolve({ items: localItems })
      : requestJson(watchSuggestPath(normalized));
    request
      .then(function (payload) {
        if (requestId !== watchSuggestRequestId) return;
        if (input && String(input.value || "").trim() !== normalized) return;
        accountsState.watchSuggestItems = mergeSuggestionItems(payload.items || [], localItems, 8);
        accountsState.watchSuggestError = "";
      })
      .catch(function (error) {
        if (requestId !== watchSuggestRequestId) return;
        accountsState.watchSuggestItems = localItems;
        accountsState.watchSuggestError = localItems.length ? "" : (error.message || "종목 검색에 실패했습니다.");
      })
      .finally(function () {
        if (requestId !== watchSuggestRequestId) return;
        accountsState.watchSuggestLoading = false;
        updateWatchSuggestBox(box, input);
      });
  }, 180);
}

export { loadWatchSuggestions, renderWatchSuggestList };
