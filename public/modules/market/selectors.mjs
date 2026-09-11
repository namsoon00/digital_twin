import { investmentActionKey } from "../decisions/actions.mjs";
import { instrumentItems, marketSignalForItem, parseMarketSignals } from "../decisions/signals.mjs";
import { investmentAnalysisModel } from "../decisions/strategy.mjs";
import { stockDisplayName } from "../instruments/catalog.mjs";
import { researchEvidenceImpactMeta } from "../research/quality.mjs";
import { currentResearchEvidence } from "../research/requests.mjs";
import { compareResearchEvidenceForDisplay, feedEvidenceKey } from "../research/workspace.mjs";
import { consoleQualityMeta } from "../shared/console.mjs";
import { formatSignalVolume, hasNumericValue, latestChangedFirst, numeric, recordChangedAtValue } from "../shared/format.mjs";
import { marketState } from "../state/market.mjs";

function consoleResearchItems() {
  var payload = currentResearchEvidence();
  var items = Array.isArray(payload.items) ? payload.items : [];
  return latestChangedFirst(items.filter(function (item) {
    var kind = String(item && item.kind || "").toLowerCase();
    return kind !== "news" || item.displayEligible !== false;
  }));
}

function consoleEvidenceBySymbol() {
  return consoleResearchItems().reduce(function (map, item, index) {
    var symbol = String(item && item.symbol || "").toUpperCase();
    if (!symbol) return map;
    if (!map[symbol]) map[symbol] = [];
    map[symbol].push(Object.assign({ consoleKey: feedEvidenceKey(item, index) }, item));
    return map;
  }, {});
}

function consoleDecisionBySymbol(snapshot) {
  var analysis = investmentAnalysisModel(snapshot || {});
  var rows = Array.isArray(analysis.actionQueue) ? analysis.actionQueue : [];
  return rows.reduce(function (map, row, index) {
    var symbol = String(row && row.symbol || "").toUpperCase();
    if (symbol && !map[symbol]) map[symbol] = Object.assign({ consoleKey: investmentActionKey(row, index) }, row);
    return map;
  }, {});
}

function selectConsoleInstrumentRows(snapshot) {
  var evidenceMap = consoleEvidenceBySymbol();
  var decisionMap = consoleDecisionBySymbol(snapshot);
  var accountId = String(((snapshot || {}).toss || {}).accountId || ((snapshot || {}).accountId || "default"));
  var readModelItems = Array.isArray((marketState.marketReadModel || {}).items) ? marketState.marketReadModel.items : [];
  var sourceItems = readModelItems.length ? readModelItems : instrumentItems(snapshot || {});
  return sourceItems.map(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    var evidence = evidenceMap[symbol] || [];
    var decision = decisionMap[symbol] || null;
    var signal = marketSignalForItem(item, parseMarketSignals());
    var foreign = signal.foreignAvailable ? numeric(signal.foreignNet) : 0;
    var institution = signal.institutionAvailable ? numeric(signal.institutionNet) : 0;
    var combinedFlowAvailable = signal.foreignAvailable && signal.institutionAvailable;
    var partialFlowAvailable = signal.foreignAvailable || signal.institutionAvailable;
    var participantStatus = signal.investorCoverage && signal.investorCoverage.participantStatus || {};
    var nextProviderUpdateAt = String(signal.investorCoverage && signal.investorCoverage.nextProviderUpdateAt || "");
    var nextProviderClock = nextProviderUpdateAt.indexOf("T") >= 0 ? nextProviderUpdateAt.split("T")[1].slice(0, 5) : "";
    var flowDisplay = combinedFlowAvailable
      ? formatSignalVolume(foreign + institution)
      : (signal.foreignAvailable ? formatSignalVolume(foreign) : (signal.institutionAvailable ? formatSignalVolume(institution) : "-"));
    var flowLabel = combinedFlowAvailable
      ? "외국인+기관"
      : (signal.foreignAvailable ? "외국인 추정" : (signal.institutionAvailable ? "기관 추정" : "수급 미수집"));
    if (!combinedFlowAvailable && participantStatus.institution === "not-yet-published" && nextProviderClock) {
      flowLabel += " · 기관 " + nextProviderClock + " 예정";
    }
    var rawPrice = item.currentPrice;
    var quoteAvailable = hasNumericValue(rawPrice) && numeric(rawPrice) > 0;
    var changeValue = hasNumericValue(item.changeRate) ? item.changeRate : signal.priceChangeRate;
    var changeAvailable = quoteAvailable && hasNumericValue(changeValue);
    var profitLossAvailable = item.source !== "watchlist" && hasNumericValue(item.profitLossRate);
    var quality = quoteAvailable
      ? consoleQualityMeta(item.quoteStatus || item.dataQuality || (((snapshot || {}).toss || {}).mode))
      : { label: "시세 미수집", tone: "caution" };
    var strongestEvidence = evidence.slice().sort(compareResearchEvidenceForDisplay)[0] || null;
    var impact = strongestEvidence ? researchEvidenceImpactMeta(strongestEvidence) : { label: "근거 대기", tone: "hold" };
    return {
      key: [accountId, item.market || "-", symbol].join(":"),
      symbol: symbol,
      name: stockDisplayName(symbol, item),
      source: item.source || "watchlist",
      market: item.market || "-",
      currency: item.currency || "",
      currentPrice: quoteAvailable ? numeric(rawPrice) : 0,
      quoteAvailable: quoteAvailable,
      changeRate: numeric(changeValue),
      changeAvailable: changeAvailable,
      profitLossRate: numeric(item.profitLossRate),
      profitLossAvailable: profitLossAvailable,
      flowAvailable: combinedFlowAvailable,
      partialFlowAvailable: partialFlowAvailable,
      flowDisplay: flowDisplay,
      flowLabel: flowLabel,
      foreignInstitutionNet: foreign + institution,
      evidence: evidence,
      evidenceCount: evidence.length,
      impact: impact,
      decision: decision,
      quality: quality,
      apiSource: item.quoteSource || item.apiSource || item.provider || item.sourceApi || ((((snapshot || {}).toss || {}).mode === "live") ? "Toss Open API" : "로컬 저장 시세"),
      isMock: Boolean(item.isMock || item.mock || item.dataMode === "mock" || (snapshot || {}).preview),
      updatedAt: item.updatedAt || ((snapshot || {}).generatedAt || ""),
      raw: item
    };
  }).sort(function (a, b) {
    var changedDiff = recordChangedAtValue(b) - recordChangedAtValue(a);
    if (changedDiff) return changedDiff;
    if (a.source !== b.source) return a.source === "watchlist" ? 1 : -1;
    return String(a.symbol || "").localeCompare(String(b.symbol || ""));
  });
}

function filteredConsoleInstrumentRows(snapshot) {
  var query = String(marketState.consoleMarketSearch || "").trim().toLowerCase();
  var scope = String(marketState.consoleMarketScope || "all");
  return selectConsoleInstrumentRows(snapshot).filter(function (row) {
    if (scope === "holding" && row.source === "watchlist") return false;
    if (scope === "watchlist" && row.source !== "watchlist") return false;
    if (!query) return true;
    return [row.symbol, row.name, row.market, row.impact.label, row.decision && (row.decision.decision || row.decision.action)].filter(Boolean).join(" ").toLowerCase().indexOf(query) >= 0;
  });
}

export { consoleResearchItems, filteredConsoleInstrumentRows, selectConsoleInstrumentRows };
