import { allAccountWatchlistSymbols } from "../accounts/watchlist.mjs";
import { watchlistSymbols } from "../instruments/catalog.mjs";
import { render } from "../render/scheduler.mjs";
import { activeJsonRequest } from "../requests/active.mjs";
import { clearReadModelPoll, readModelIsWarming, requestJson, scheduleReadModelPoll } from "../requests/json.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { marketState } from "../state/market.mjs";
import { shellState } from "../state/shell.mjs";

function loadMarketReadModel(force) {
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (marketState.marketReadModelLoading) return activeJsonRequest("market-instruments") || Promise.resolve(marketState.marketReadModel);
  if (marketState.marketReadModel && !force) return Promise.resolve(marketState.marketReadModel);
  marketState.marketReadModelLoading = true;
  marketState.marketReadModelError = "";
  var params = new URLSearchParams();
  var symbols = allAccountWatchlistSymbols();
  if (!symbols.length) symbols = watchlistSymbols();
  if (symbols.length) params.set("watchlistSymbols", symbols.join(","));
  return requestJson("/api/market/instruments" + (params.toString() ? "?" + params.toString() : ""), {
    key: "market-instruments",
    force: Boolean(force),
    timeoutMs: 12000
  }).then(function (payload) {
    marketState.marketReadModel = payload || {};
    if (readModelIsWarming(payload)) {
      scheduleReadModelPoll("market-instruments", function () { loadMarketReadModel(true); });
    } else {
      clearReadModelPoll("market-instruments");
    }
    return marketState.marketReadModel;
  }).catch(function (error) {
    marketState.marketReadModelError = error.message || "시장 종목 요약을 읽지 못했습니다.";
    return null;
  }).finally(function () {
    marketState.marketReadModelLoading = false;
    if (shellState.snapshot) render();
  });
}

export { loadMarketReadModel };
