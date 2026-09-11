import { DEFAULT_SYMBOL_UNIVERSE_LIMIT } from "./constants.mjs";
import { settingValue } from "../settings/fields.mjs";
import { formatClock } from "../shared/format.mjs";
import { universeState } from "../state/universe.mjs";

function normalizeSymbols(value) {
  return String(value || "")
    .split(/[,\s]+/)
    .map(function (symbol) { return symbol.trim().toUpperCase(); })
    .filter(Boolean)
    .filter(function (symbol, index, list) { return list.indexOf(symbol) === index; })
    .slice(0, 30);
}

function watchlistSymbols() {
  return normalizeSymbols(settingValue("watchlistSymbols"));
}

function fallbackKnownStockInfo(symbol) {
  var normalized = String(symbol || "").trim().toUpperCase();
  var map = {
    "005930": { name: "삼성전자", market: "KR", currency: "KRW", sector: "반도체" },
    "000660": { name: "SK하이닉스", market: "KR", currency: "KRW", sector: "반도체" },
    "005380": { name: "현대차", market: "KR", currency: "KRW", sector: "모빌리티" },
    "000020": { name: "동화약품", market: "KR", currency: "KRW", sector: "헬스케어" },
    "035420": { name: "NAVER", market: "KR", currency: "KRW", sector: "AI/플랫폼" },
    "035720": { name: "카카오", market: "KR", currency: "KRW", sector: "AI/플랫폼" },
    "051910": { name: "LG화학", market: "KR", currency: "KRW", sector: "소재" },
    "068270": { name: "셀트리온", market: "KR", currency: "KRW", sector: "헬스케어" },
    AAPL: { name: "Apple", market: "US", currency: "USD", sector: "AI/플랫폼" },
    MSFT: { name: "Microsoft", market: "US", currency: "USD", sector: "AI/플랫폼" },
    NVDA: { name: "NVIDIA", market: "US", currency: "USD", sector: "반도체" },
    AMD: { name: "AMD", market: "US", currency: "USD", sector: "반도체" },
    TSLA: { name: "Tesla", market: "US", currency: "USD", sector: "모빌리티" },
    PLTR: { name: "Palantir Technologies", market: "US", currency: "USD", sector: "AI/플랫폼" },
    MSTR: { name: "Strategy", market: "US", currency: "USD", sector: "디지털자산" },
    STRC: { name: "Strategy Preferred", market: "US", currency: "USD", sector: "디지털자산" },
    GOOGL: { name: "Alphabet", market: "US", currency: "USD", sector: "AI/플랫폼" },
    META: { name: "Meta", market: "US", currency: "USD", sector: "AI/플랫폼" }
  };
  return Object.assign({
    symbol: normalized,
    name: normalized || "관심 종목",
    market: "",
    currency: "",
    sector: ""
  }, map[normalized] || {});
}

function fallbackKnownStockSymbols() {
  return ["005930", "000660", "005380", "000020", "035420", "035720", "051910", "068270", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "PLTR", "MSTR", "STRC", "GOOGL", "META"];
}

function compactStockSearchText(value) {
  return String(value || "").trim().toLowerCase().replace(/[\s._'’`-]+/g, "");
}

function stockSearchAliasCatalog() {
  return [
    { aliases: ["팔란티어", "palantir", "palantirtechnologies", "pltr"], symbols: ["PLTR"] },
    { aliases: ["애플", "apple", "aapl"], symbols: ["AAPL"] },
    { aliases: ["테슬라", "tesla", "tsla"], symbols: ["TSLA"] },
    { aliases: ["엔비디아", "nvidia", "nvda"], symbols: ["NVDA"] },
    { aliases: ["마이크로소프트", "microsoft", "msft"], symbols: ["MSFT"] },
    { aliases: ["삼성전자", "samsung", "005930"], symbols: ["005930"] },
    { aliases: ["하이닉스", "sk하이닉스", "skhynix", "000660"], symbols: ["000660"] },
    { aliases: ["네이버", "naver", "035420"], symbols: ["035420"] }
  ];
}

function stockSearchAliasSymbols(query) {
  var token = compactStockSearchText(query);
  if (!token) return [];
  var symbols = [];
  stockSearchAliasCatalog().forEach(function (entry) {
    var matched = entry.aliases.some(function (alias) {
      var compactAlias = compactStockSearchText(alias);
      return compactAlias.indexOf(token) >= 0 || token.indexOf(compactAlias) >= 0;
    });
    if (!matched) return;
    entry.symbols.forEach(function (symbol) {
      if (symbols.indexOf(symbol) < 0) symbols.push(symbol);
    });
  });
  return symbols;
}

function clientKnownStockInfo(symbol) {
  var normalized = String(symbol || "").trim().toUpperCase();
  var universeItem = (universeState.symbolUniverse.items || []).filter(function (item) {
    return String(item.symbol || "").toUpperCase() === normalized;
  })[0];
  if (universeItem) {
    return Object.assign(fallbackKnownStockInfo(normalized), {
      symbol: universeItem.symbol,
      name: universeItem.name || universeItem.symbol,
      market: universeItem.market || universeItem.exchange || "",
      currency: universeItem.currency || "",
      sector: universeItem.sector || "",
      source: universeItem.source || "",
      stale: Boolean(universeItem.stale)
    });
  }
  return fallbackKnownStockInfo(normalized);
}

function stockDisplayName(symbol, item) {
  var original = String(symbol || (item && (item.rawSymbol || item.symbol)) || "").trim().toUpperCase();
  var known = fallbackKnownStockInfo(original);
  var merged = Object.assign(clientKnownStockInfo(original), item || {}, { symbol: original });
  var explicit = String(
    (item && (item.symbolName || item.symbolDisplayName || item.displaySymbolName || item.displayName)) || ""
  ).trim();
  var knownName = String(known.name || "").trim();
  var name = knownName && knownName.toUpperCase() !== original
    ? knownName
    : (explicit || String(merged.name || "").trim());
  name = compactSecurityName(name, original);
  if (!name || (original && name.toUpperCase() === original)) {
    name = original || "종목";
  }
  return name;
}

function compactSecurityName(value, symbol) {
  var name = String(value || "").trim();
  var normalizedSymbol = String(symbol || "").trim().toUpperCase();
  var aliases = {
    "NVIDIA CORPORATION - COMMON STOCK": "NVIDIA",
    "TESLA, INC. - COMMON STOCK": "Tesla",
    "APPLE INC. - COMMON STOCK": "Apple",
    "STRATEGY INCORPORATED": "Strategy"
  };
  var alias = aliases[name.toUpperCase()];
  if (alias) return alias;
  name = name
    .replace(/\s+-\s+Common Stock$/i, "")
    .replace(/,?\s+(Incorporated|Corporation|Corp\.|Inc\.)$/i, "")
    .trim();
  return name || normalizedSymbol;
}

function userFacingTechnicalNarrative(value) {
  var raw = String(value == null ? "" : value).trim();
  var exact = {
    "deferred-pending-scoped-manifest": "판단에 필요한 추론 기록이 아직 준비되지 않았습니다.",
    "native-manifest-evidence-index-incomplete": "판단 근거 색인이 아직 준비되지 않았습니다.",
    "monitoring.alerts_detected": "새 투자 신호가 감지되었습니다.",
    "research_evidence.collected": "새 뉴스·공시 근거가 수집되었습니다."
  };
  if (exact[raw]) return exact[raw];
  var circuit = raw.match(/^circuit open until\s+(.+)$/i);
  if (circuit) {
    return "전송 오류가 반복되어 " + (formatClock(circuit[1]) || circuit[1]) + "까지 자동 재시도를 잠시 중단했습니다.";
  }
  if (/데이터베이스 쓰기 경계/.test(raw) || (/typedb/i.test(raw) && /write|boundary/i.test(raw))) {
    return "다른 추론 저장 작업이 진행 중이어서 이번 처리를 완료하지 못했습니다.";
  }
  return raw;
}

function formatConsoleNarrative(value) {
  var text = userFacingTechnicalNarrative(value);
  text = text.replace(/([+-]?\d+\.\d{2,})\s*%/g, function (_match, raw) {
    var number = Number(raw);
    if (!Number.isFinite(number)) return _match;
    return (number > 0 && raw.charAt(0) === "+" ? "+" : "") + number.toFixed(1) + "%";
  });
  text = text.replace(/([+-]?\d+\.\d{2,})(\s*점)/g, function (_match, raw, suffix) {
    var number = Number(raw);
    return Number.isFinite(number) ? number.toFixed(1) + suffix : _match;
  });
  [
    ["NVIDIA Corporation - Common Stock", "NVIDIA"],
    ["Tesla, Inc. - Common Stock", "Tesla"],
    ["Apple Inc. - Common Stock", "Apple"],
    ["Strategy Incorporated", "Strategy"]
  ].forEach(function (pair) {
    text = text.split(pair[0]).join(pair[1]);
  });
  return text;
}

function stockDisplayMeta(item, parts) {
  return (parts || [])
    .map(function (part) { return String(part || "").trim(); })
    .filter(Boolean)
    .join(" · ");
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function textWithDisplaySymbol(value, symbol, item) {
  var text = String(value || "");
  var original = String(symbol || (item && (item.rawSymbol || item.symbol)) || "").trim().toUpperCase();
  var display = stockDisplayName(original, item);
  if (!text || !original || !display || display.toUpperCase() === original) return text;
  var replaced = text.replace(new RegExp("\\b" + escapeRegExp(original) + "\\b", "g"), display);
  replaced = replaced.replace(new RegExp(escapeRegExp(display) + "\\s*[/·]\\s*" + escapeRegExp(display), "g"), display);
  return replaced;
}

function textWithKnownDisplaySymbols(value, preferredSymbol, item) {
  var text = textWithDisplaySymbol(value, preferredSymbol, item);
  fallbackKnownStockSymbols().forEach(function (symbol) {
    text = textWithDisplaySymbol(text, symbol, Object.assign({}, item || {}, { symbol: symbol }));
  });
  return text;
}

function inferKnownStockSymbolFromText(value) {
  var text = String(value || "").toUpperCase();
  if (!text) return "";
  var known = fallbackKnownStockSymbols();
  for (var index = 0; index < known.length; index += 1) {
    if (new RegExp("\\b" + escapeRegExp(known[index]) + "\\b").test(text)) return known[index];
  }
  var match = text.match(/\b\d{6}\b/);
  return match ? match[0] : "";
}

function defaultSymbolUniversePayload() {
  var items = ["005930", "000660", "TSLA", "AAPL", "NVDA", "MSFT", "AMD"].map(function (symbol) {
    var info = fallbackKnownStockInfo(symbol);
    var market = info.market === "US" ? "NASDAQ" : (info.market === "KR" ? "KOSPI" : info.market);
    return Object.assign({}, info, {
      market: market,
      exchange: market,
      assetType: "STOCK",
      source: "Orbit Alpha seed",
      sourceUrl: "local-default",
      fetchedAt: "",
      lastSeenAt: "",
      stale: true
    });
  });
  var query = String(universeState.symbolUniverseQuery || "").trim().toUpperCase();
  var marketFilter = String(universeState.symbolUniverseMarket || "").trim().toUpperCase();
  var filtered = items.filter(function (item) {
    var marketOk = !marketFilter || item.market === marketFilter;
    var queryOk = !query || String(item.symbol || "").toUpperCase().indexOf(query) >= 0 || String(item.name || "").toUpperCase().indexOf(query) >= 0;
    return marketOk && queryOk;
  });
  var limit = Math.max(1, Math.min(500, Number(universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT)));
  var offset = Math.max(0, Number(universeState.symbolUniverseOffset || 0));
  return {
    items: filtered.slice(offset, offset + limit),
    summary: {
      total: items.length,
      maxAgeHours: 24,
      sources: [],
      markets: ["KOSPI", "KOSDAQ", "NASDAQ"].map(function (market) {
        return {
          market: market,
          count: items.filter(function (item) { return item.market === market; }).length,
          lastSeenAt: "",
          stale: true,
          source: market === "NASDAQ" ? "Nasdaq Trader Symbol Directory" : "KRX KIND Listed Companies",
          sourceUrl: ""
        };
      })
    },
    resultTotal: filtered.length,
    limit: limit,
    offset: offset,
    hasMore: offset + limit < filtered.length
  };
}

export { clientKnownStockInfo, defaultSymbolUniversePayload, formatConsoleNarrative, inferKnownStockSymbolFromText, normalizeSymbols, stockDisplayMeta, stockDisplayName, stockSearchAliasSymbols, textWithDisplaySymbol, textWithKnownDisplaySymbols, watchlistSymbols };
