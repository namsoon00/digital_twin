import { accountFreshness } from "../accounts/balance.mjs";
import { instrumentItems } from "../decisions/signals.mjs";
import { hasNumericValue, numeric } from "../shared/format.mjs";

function selectConsolePortfolio(snapshot) {
  snapshot = snapshot || {};
  var portfolio = snapshot.portfolio || {};
  var items = instrumentItems(snapshot);
  var holdings = items.filter(function (item) { return String(item.source || "") !== "watchlist"; });
  var pnlItems = holdings.filter(function (item) { return hasNumericValue(item.profitLoss); });
  var pnl = pnlItems.reduce(function (sum, item) { return sum + numeric(item.profitLoss); }, 0);
  var freshness = accountFreshness(snapshot);
  return {
    total: numeric(portfolio.total),
    invested: numeric(portfolio.invested),
    cash: numeric(portfolio.cash),
    valuationBasis: String(portfolio.valuationBasis || portfolio.valuation_basis || "legacy-unknown"),
    valuationSnapshotId: String(portfolio.valuationSnapshotId || portfolio.valuation_snapshot_id || ""),
    brokerComparableTotal: numeric(portfolio.brokerComparableTotal || portfolio.broker_comparable_total),
    brokerGrossTotal: numeric(portfolio.brokerGrossTotal || portfolio.broker_gross_total),
    brokerNetTotal: numeric(portfolio.brokerNetTotal || portfolio.broker_net_total),
    markToMarketTotal: numeric(portfolio.markToMarketTotal || portfolio.mark_to_market_total),
    pnl: pnl,
    pnlAvailable: pnlItems.length > 0,
    holdingCount: holdings.length,
    watchCount: items.length - holdings.length,
    freshness: freshness,
    holdings: holdings
  };
}

export { selectConsolePortfolio };
