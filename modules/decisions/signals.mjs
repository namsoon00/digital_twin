import { investmentAnalysisModel } from "./strategy.mjs";
import { clientKnownStockInfo, stockDisplayName } from "../instruments/catalog.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { settingValue } from "../settings/fields.mjs";
import { evaluateConfiguredFormula, formulaSetting } from "../settings/formulas.mjs";
import { clamp, formatPrice, formatSignalNumber, formatSignalVolume, numeric, signedPct } from "../shared/format.mjs";

var decisionStateCatalog = {
  review: {
    normal: { label: "평소 관찰", tone: "hold", rank: 0 },
    observe: { label: "변화 관찰", tone: "watch", rank: 1 },
    check: { label: "조건 확인", tone: "caution", rank: 2 },
    act: { label: "대응 준비", tone: "danger", rank: 3 },
    immediate: { label: "즉시 재확인", tone: "danger", rank: 4 },
    // A blocked decision is a data/inference problem, not the strongest
    // investment action. Keep it out of investment-priority ordering.
    blocked: { label: "판단 보류", tone: "caution", rank: -1 }
  },
  data: {
    sufficient: { label: "판단 자료 충분", tone: "watch" },
    partial: { label: "일부 자료만 있음", tone: "caution" },
    insufficient: { label: "핵심 자료 부족", tone: "danger" },
    unavailable: { label: "자료 사용 불가", tone: "danger" }
  },
  change: {
    unchanged: { label: "이전과 같음", tone: "hold" },
    "new-condition": { label: "새 조건 성립", tone: "caution" },
    improving: { label: "이전보다 개선", tone: "watch" },
    worsening: { label: "이전보다 악화", tone: "danger" },
    "direction-changed": { label: "판단 방향 변경", tone: "caution" },
    "new-evidence": { label: "새 뉴스·공시·근거", tone: "watch" }
  },
  conflict: {
    "risk-only": { label: "위험 근거만 확인", tone: "danger" },
    "support-only": { label: "버티거나 좋아질 근거만 확인", tone: "watch" },
    mixed: { label: "위험과 반대 근거가 함께 있음", tone: "caution" },
    "context-only": { label: "방향을 정하기 어려운 참고 근거", tone: "hold" }
  },
  validation: {
    ready: { label: "검증 완료", tone: "watch" },
    conditional: { label: "조건부 사용", tone: "caution" },
    blocked: { label: "판단 보류", tone: "danger" }
  },
  evidence: {
    risk: { label: "위험 근거", tone: "danger" },
    support: { label: "버티거나 좋아질 근거", tone: "watch" },
    counter: { label: "반대 근거", tone: "caution" },
    context: { label: "참고 근거", tone: "hold" },
    blocking: { label: "판단을 막는 자료 문제", tone: "danger" }
  }
};

function decisionStateMeta(kind, value, fallback) {
  var catalog = decisionStateCatalog[kind] || {};
  var key = String(value || fallback || "").trim().toLowerCase();
  if (catalog[key]) return Object.assign({ key: key }, catalog[key]);
  var fallbackKey = String(fallback || Object.keys(catalog)[0] || "");
  return Object.assign({ key: fallbackKey }, catalog[fallbackKey] || { label: key || "확인 필요", tone: "hold", rank: 0 });
}

function stateValueFromSources(sources, keys, fallback) {
  var result = "";
  (sources || []).some(function (source) {
    if (!source || typeof source !== "object") return false;
    return (keys || []).some(function (key) {
      var value = String(source[key] == null ? "" : source[key]).trim();
      if (!value) return false;
      result = value;
      return true;
    });
  });
  return result || fallback;
}

function evidenceConflictState(conditions) {
  var roles = (conditions || []).map(function (item) { return String(item.evidenceRole || "context"); });
  var hasRisk = roles.some(function (role) { return role === "risk" || role === "blocking"; });
  var hasSupport = roles.some(function (role) { return role === "support" || role === "counter"; });
  if (hasRisk && hasSupport) return "mixed";
  if (hasRisk) return "risk-only";
  if (hasSupport) return "support-only";
  return "context-only";
}

function parseValuationAssumptions() {
  var map = {};
  String(settingValue("valuationAssumptions") || "")
    .split(/\r?\n/)
    .map(function (line) { return line.trim(); })
    .filter(Boolean)
    .forEach(function (line) {
      var parts = line.split(",").map(function (part) { return part.trim(); });
      var symbol = String(parts[0] || "").toUpperCase();
      if (!symbol) return;
      map[symbol] = {
        symbol: symbol,
        eps: numeric(parts[1]),
        targetPer: numeric(parts[2]),
        margin: numeric(parts[3] || 15)
      };
    });
  return map;
}

function parseMarketSignals() {
  var map = {};
  String(settingValue("marketSignalInputs") || "")
    .split(/\r?\n/)
    .map(function (line) { return line.trim(); })
    .filter(Boolean)
    .forEach(function (line) {
      var parts = line.split(",").map(function (part) { return part.trim(); });
      var symbol = String(parts[0] || "").toUpperCase();
      if (!symbol) return;
      map[symbol] = {
        symbol: symbol,
        tradeStrength: numeric(parts[1]),
        volumeRatio: numeric(parts[2]),
        buyVolume: numeric(parts[3]),
        sellVolume: numeric(parts[4]),
        bidAskImbalance: numeric(parts[5]),
        priceChangeRate: numeric(parts[6]),
        ma20: numeric(parts[7]),
        ma60: numeric(parts[8]),
        foreignNet: numeric(parts[9]),
        institutionNet: numeric(parts[10]),
        individualNet: numeric(parts[11]),
        source: "manual"
      };
    });
  return map;
}

function currentPriceOf(item) {
  var currentPrice = numeric(item.currentPrice);
  if (currentPrice) return currentPrice;
  var quantity = numeric(item.quantity);
  var marketValue = numeric(item.marketValue);
  return quantity ? marketValue / quantity : 0;
}

function valuationStatus(currentPrice, fairValue, marginPrice) {
  if (!currentPrice || !fairValue) return { label: "입력 필요", tone: "hold", rank: 4 };
  if (currentPrice <= marginPrice) return { label: "싸다", tone: "watch", rank: 1 };
  if (currentPrice <= fairValue) return { label: "적정권", tone: "hold", rank: 2 };
  if (currentPrice <= fairValue * 1.15) return { label: "비싼 편", tone: "caution", rank: 3 };
  return { label: "비싸다", tone: "danger", rank: 3 };
}

function buildValuationForItem(item, assumptions, weights, formula) {
  var symbol = String(item.symbol || "").toUpperCase();
  var assumption = assumptions[symbol] || {};
  var currentPrice = currentPriceOf(item);
  var baseFairValue = assumption.eps && assumption.targetPer ? assumption.eps * assumption.targetPer : 0;
  var margin = assumption.margin || 15;
  var variables = Object.assign({}, weights, {
    eps: assumption.eps || 0,
    targetPer: assumption.targetPer || 0,
    margin: margin,
    currentPrice: currentPrice,
    averagePrice: numeric(item.averagePrice),
    quantity: numeric(item.quantity),
    marketValue: numeric(item.marketValue),
    profitLoss: numeric(item.profitLoss),
    profitLossRate: numeric(item.profitLossRate)
  });
  var formulaResult = formula
    ? evaluateConfiguredFormula(formula, variables, baseFairValue)
    : { value: baseFairValue, error: "", usedFallback: false };
  var fairValue = Math.max(0, numeric(formulaResult.value));
  var marginPrice = fairValue ? fairValue * (1 - margin / 100) : 0;
  var gap = currentPrice && fairValue ? ((fairValue / currentPrice) - 1) * 100 : 0;
  var status = valuationStatus(currentPrice, fairValue, marginPrice);
  var reasons = [];
  if (formulaResult.error) {
    reasons.push("적정가 공식 오류로 기본값을 사용했습니다: " + formulaResult.error);
  }
  if (!fairValue) {
    reasons.push("적정가 공식 결과가 0입니다. EPS, 목표 PER, 가중치를 확인하세요.");
  } else if (!currentPrice) {
    reasons.push("현재가가 필요합니다.");
  } else {
    reasons.push("적정가 " + formatPrice(fairValue, item.currency) + "와 현재가 차이는 " + signedPct(gap) + "입니다.");
    reasons.push("안전마진 " + margin + "% 기준 매수가 상한은 " + formatPrice(marginPrice, item.currency) + "입니다.");
  }
  return {
    symbol: symbol,
    name: item.name || symbol,
    source: item.source || "watchlist",
    sector: item.sector || "-",
    market: item.market || "",
    currency: item.currency || "",
    currentPrice: currentPrice,
    eps: assumption.eps || 0,
    targetPer: assumption.targetPer || 0,
    margin: margin,
    formula: formula,
    formulaError: formulaResult.error,
    fairValue: fairValue,
    marginPrice: marginPrice,
    gap: gap,
    status: status.label,
    tone: status.tone,
    rank: status.rank,
    reasons: reasons
  };
}

function buildValuationItems(snapshot) {
  var assumptions = parseValuationAssumptions();
  var formula = formulaSetting("fairValueFormula");
  return instrumentItems(snapshot)
    .map(function (item) {
      return buildValuationForItem(item, assumptions, {}, formula);
    })
    .sort(function (a, b) {
      if (a.rank !== b.rank) return a.rank - b.rank;
      return a.gap - b.gap;
    });
}

function signalValue(raw, keys) {
  var value = 0;
  keys.some(function (key) {
    if (raw && raw[key] != null && raw[key] !== "") {
      value = numeric(raw[key]);
      return true;
    }
    return false;
  });
  return value;
}

function optionalSignalValue(raw, keys) {
  var value = null;
  keys.some(function (key) {
    if (raw && raw[key] != null && raw[key] !== "") {
      value = numeric(raw[key]);
      return true;
    }
    return false;
  });
  return value;
}

function investorCoverageFromSignal(raw) {
  var coverage = raw && (raw.marketSignalCoverage || raw.market_signal_coverage);
  return coverage && coverage.investor && typeof coverage.investor === "object" ? coverage.investor : {};
}

function marketSignalForItem(item, signalMap) {
  var symbol = String(item.symbol || "").toUpperCase();
  var fromItem = Object.assign({}, item || {}, item.marketSignal || item.tradeSignal || item.signal || {});
  var fromSettings = signalMap[symbol] || {};
  var merged = Object.assign({}, fromItem, fromSettings);
  var investorCoverage = investorCoverageFromSignal(merged);
  var observedInvestorFields = Array.isArray(investorCoverage.observedFields)
    ? investorCoverage.observedFields
    : (Array.isArray(investorCoverage.fields) ? investorCoverage.fields : []);
  var hasInvestorCoverage = Object.keys(investorCoverage).length > 0;
  var foreignValue = optionalSignalValue(merged, ["foreignNet", "foreignNetVolume", "foreign_net_volume", "foreignNetBuy", "foreignInvestorNet", "foreignerNetBuy"]);
  var institutionValue = optionalSignalValue(merged, ["institutionNet", "institutionNetVolume", "institution_net_volume", "institutionNetBuy", "institutionalNet", "institutionInvestorNet"]);
  var individualValue = optionalSignalValue(merged, ["individualNet", "individualNetVolume", "individual_net_volume", "individualNetBuy", "retailNet", "personalNetBuy"]);
  var foreignAvailable = hasInvestorCoverage ? observedInvestorFields.indexOf("foreignNetVolume") >= 0 : foreignValue != null && foreignValue !== 0;
  var institutionAvailable = hasInvestorCoverage ? observedInvestorFields.indexOf("institutionNetVolume") >= 0 : institutionValue != null && institutionValue !== 0;
  var individualAvailable = hasInvestorCoverage ? observedInvestorFields.indexOf("individualNetVolume") >= 0 : individualValue != null && individualValue !== 0;
  return {
    symbol: symbol,
    tradeStrength: signalValue(merged, ["tradeStrength", "trade_strength", "executionStrength"]),
    volumeRatio: signalValue(merged, ["volumeRatio", "volume_ratio", "relativeVolume", "volumeMultiple"]),
    buyVolume: signalValue(merged, ["buyVolume", "buy_volume", "buyTradeVolume", "bidVolume"]),
    sellVolume: signalValue(merged, ["sellVolume", "sell_volume", "sellTradeVolume", "askVolume"]),
    bidAskImbalance: signalValue(merged, ["bidAskImbalance", "orderbookImbalance", "imbalance"]),
    priceChangeRate: signalValue(merged, ["priceChangeRate", "changeRate", "changePercent"]),
    ma20: signalValue(merged, ["ma20", "movingAverage20", "sma20"]),
    ma60: signalValue(merged, ["ma60", "movingAverage60", "sma60"]),
    foreignNet: foreignAvailable ? numeric(foreignValue) : null,
    institutionNet: institutionAvailable ? numeric(institutionValue) : null,
    individualNet: individualAvailable ? numeric(individualValue) : null,
    foreignAvailable: foreignAvailable,
    institutionAvailable: institutionAvailable,
    individualAvailable: individualAvailable,
    investorCoverage: investorCoverage,
    source: merged.signalSource || merged.provider || merged.quoteSource || (Object.keys(fromItem).length ? "account" : "")
  };
}

function hasMarketSignal(signal) {
  return [
    "tradeStrength",
    "volumeRatio",
    "buyVolume",
    "sellVolume",
    "bidAskImbalance",
    "priceChangeRate",
    "ma20",
    "ma60",
    "foreignNet",
    "institutionNet",
    "individualNet"
  ].some(function (key) {
    return Number(signal[key] || 0) !== 0;
  });
}

function buyVolumeShare(signal) {
  var buy = Number(signal.buyVolume || 0);
  var sell = Number(signal.sellVolume || 0);
  var total = buy + sell;
  return total > 0 ? (buy / total) * 100 : 50;
}

function modelFeatureVariables(item, signal, valuation) {
  item = item || {};
  signal = signal || {};
  valuation = valuation || {};
  var current = currentPriceOf(item);
  var ma20 = Number(signal.ma20 || item.ma20 || 0);
  var ma60 = Number(signal.ma60 || item.ma60 || 0);
  var tradeStrength = Number(signal.tradeStrength || 100);
  var volumeRatio = Number(signal.volumeRatio || 1);
  var buyVolume = Number(signal.buyVolume || 0);
  var sellVolume = Number(signal.sellVolume || 0);
  var buyShare = buyVolumeShare(signal);
  var foreignNet = Number(signal.foreignNet || 0);
  var institutionNet = Number(signal.institutionNet || 0);
  var individualNet = Number(signal.individualNet || 0);
  var smartMoneyNet = foreignNet + institutionNet;
  var bidAskImbalance = Number(signal.bidAskImbalance || 0);
  var priceChangeRate = Number(signal.priceChangeRate || 0);
  var trendDistance20 = current && ma20 ? ((current / ma20) - 1) * 100 : 0;
  var trendDistance60 = current && ma60 ? ((current / ma60) - 1) * 100 : 0;
  var maSpread = ma20 && ma60 ? ((ma20 / ma60) - 1) * 100 : 0;
  return {
    tradeStrength: tradeStrength,
    volumeRatio: volumeRatio,
    buyVolume: buyVolume,
    sellVolume: sellVolume,
    buyShare: buyShare,
    sellShare: Math.max(0, 100 - buyShare),
    bidAskImbalance: bidAskImbalance,
    priceChangeRate: priceChangeRate,
    // Raw observations only.  The browser must not combine them into a
    // buy/sell direction; that decision is TypeDB InferenceBox-owned.
    flowDirection: "unclassified",
    ma20: ma20,
    ma60: ma60,
    trendDistance20: trendDistance20,
    trendDistance60: trendDistance60,
    maSpread: maSpread,
    foreignNet: foreignNet,
    institutionNet: institutionNet,
    individualNet: individualNet,
    smartMoneyNet: smartMoneyNet,
    currentPrice: current,
    fairValue: Number(valuation.fairValue || 0),
    fairValueGap: Number(valuation.gap || 0),
    valuationRank: Number(valuation.rank || 0)
  };
}

function instrumentItems(snapshot) {
  var toss = snapshot.toss || { positions: [], watchlist: [] };
  var seen = {};
  var items = [];
  (toss.positions || []).forEach(function (item) {
    if (item.source === "cash" || item.sector === "현금" || String(item.symbol || "").toUpperCase() === "CASH") return;
    var symbol = String(item.symbol || "").toUpperCase();
    if (!symbol || seen[symbol]) return;
    seen[symbol] = true;
    items.push(Object.assign({}, item, { source: item.source || "holding" }));
  });
  (toss.watchlist || []).forEach(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    if (!symbol || seen[symbol]) return;
    seen[symbol] = true;
    items.push(Object.assign(clientKnownStockInfo(symbol), item, { source: "watchlist" }));
  });
  return items;
}

function tradeSignalReasons(signal, valuation, hasData, conditions, stateContract) {
  if (!hasData) {
    return ["현재가, 이동평균, 거래량·수급 중 필요한 자료가 부족해 투자 행동을 정하지 않습니다."];
  }
  var reasons = (conditions || []).slice(0, 4).map(function (condition) {
    return condition.label;
  });
  if (!reasons.length) reasons.push("서버 TypeDB 추론 결과가 아직 없어 가격·수급 원시 자료만 표시합니다.");
  reasons.push("자료 상태: " + decisionStateMeta("data", stateContract.dataState, "partial").label + ".");
  if (signal.ma20 || signal.ma60) {
    reasons.push("이동평균은 20일선 " + formatSignalNumber(signal.ma20, "") + ", 60일선 " + formatSignalNumber(signal.ma60, "") + "을 판단 항목으로 반영합니다.");
  }
  if (signal.foreignNet || signal.institutionNet || signal.individualNet) {
    reasons.push("투자자별 수급은 외국인 " + formatSignalVolume(signal.foreignNet) + ", 기관 " + formatSignalVolume(signal.institutionNet) + ", 개인 " + formatSignalVolume(signal.individualNet) + " 순매수로 검증합니다.");
  }
  if (valuation && valuation.status) {
    reasons.push("밸류에이션 분류는 " + valuation.status + "이며 관계 판단의 참고 정보로만 표시합니다.");
  } else {
    reasons.push("밸류에이션 가정이 없으면 가격·수급·추세 관계만으로 관찰 라벨을 만듭니다.");
  }
  return reasons;
}

function backendRelationContexts(sources) {
  var contexts = [];
  (sources || []).forEach(function (source) {
    if (!source || typeof source !== "object") return;
    [source, source.ontologyRelationContext, source.graph, source.decisionState].forEach(function (candidate) {
      if (candidate && typeof candidate === "object" && contexts.indexOf(candidate) < 0) contexts.push(candidate);
    });
  });
  return contexts;
}

function backendRelationConditions(sources) {
  var seen = {};
  var conditions = [];
  backendRelationContexts(sources).forEach(function (context) {
    [context.activeRules, context.matchedRules, context.relationRules].forEach(function (rows) {
      (Array.isArray(rows) ? rows : []).forEach(function (row) {
        if (!row || typeof row !== "object") return;
        var key = String(row.ruleId || row.rule_id || row.id || row.label || "");
        if (!key || seen[key]) return;
        seen[key] = true;
        conditions.push({
          label: String(row.label || row.ruleLabel || row.aiInfluenceLabel || key),
          evidenceRole: String(row.evidenceRole || row.evidence_role || "context"),
          reviewLevel: String(row.reviewLevel || row.review_level || "observe"),
          dataState: String(row.dataState || row.data_state || "partial"),
          changeState: String(row.changeState || row.change_state || "unchanged"),
          tone: String(row.tone || "watch")
        });
      });
    });
  });
  return conditions;
}

function backendDecisionState(sources, hasData) {
  var contexts = backendRelationContexts(sources);
  var decision = {};
  var state = {};
  contexts.some(function (context) {
    if (context.decision && typeof context.decision === "object") {
      decision = context.decision;
      return true;
    }
    return false;
  });
  contexts.some(function (context) {
    if (context.decisionState && typeof context.decisionState === "object") {
      state = context.decisionState;
      return true;
    }
    return false;
  });
  var reviewLevel = String(decision.reviewLevel || state.reviewLevel || "blocked");
  var dataState = String(decision.dataState || state.dataState || (hasData ? "partial" : "insufficient"));
  var changeState = String(decision.changeState || state.changeState || "unchanged");
  var conflictState = String(decision.conflictState || state.conflictState || "context-only");
  var validationState = String(decision.validationState || state.validationState || (reviewLevel === "blocked" ? "blocked" : "conditional"));
  var action = String(decision.label || decision.action || "").trim();
  return {
    reviewLevel: reviewLevel,
    dataState: dataState,
    changeState: changeState,
    conflictState: conflictState,
    validationState: validationState,
    action: action || "서버 추론 대기",
    tone: String(decision.tone || (reviewLevel === "blocked" ? "caution" : "watch")),
    priority: reviewLevel === "blocked"
      ? 99
      : 5 - decisionStateMeta("review", reviewLevel, "blocked").rank
  };
}

function buildTradeSignalItems(snapshot) {
  var signalMap = parseMarketSignals();
  var valuationMap = {};
  buildValuationItems(snapshot).forEach(function (item) {
    valuationMap[item.symbol] = item;
  });
  var backendRows = {};
  var analysis = investmentAnalysisModel(snapshot || {});
  (Array.isArray(analysis.actionQueue) ? analysis.actionQueue : []).forEach(function (row) {
    var key = String(row.symbol || "").toUpperCase();
    if (key) backendRows[key] = row;
  });
  return instrumentItems(snapshot).map(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    var signal = marketSignalForItem(item, signalMap);
    var hasData = hasMarketSignal(signal);
    var valuation = valuationMap[symbol] || null;
    var backend = backendRows[symbol] || {};
    var backendGraph = backend.graph || backend.ontologyRelationContext || {};
    var sources = [backend, backendGraph, item.ontologyOpinion, item.ontologyRelationContext, item];
    var conditions = backendRelationConditions(sources);
    var decision = backendDecisionState(sources, hasData);
    var stateContract = decision;
    return {
      symbol: symbol,
      name: item.name || symbol,
      source: item.source || "watchlist",
      sector: item.sector || "-",
      market: item.market || "",
      currency: item.currency || "",
      currentPrice: currentPriceOf(item),
      averagePrice: numeric(item.averagePrice),
      quantity: numeric(item.quantity),
      sellableQuantity: numeric(item.sellableQuantity || item.quantity),
      marketValue: numeric(item.marketValue),
      profitLoss: numeric(item.profitLoss),
      profitLossRate: numeric(item.profitLossRate),
      signal: signal,
      features: modelFeatureVariables(item, signal, valuation || {}),
      hasData: hasData,
      buyShare: Math.round(clamp(buyVolumeShare(signal), 0, 100)),
      valuation: valuation,
      action: decision.label,
      tone: decision.tone,
      priority: decision.priority,
      reviewLevel: decision.reviewLevel,
      dataState: decision.dataState,
      changeState: decision.changeState,
      conflictState: decision.conflictState,
      validationState: decision.validationState,
      relationRules: conditions,
      reasons: tradeSignalReasons(signal, valuation, hasData, conditions, stateContract),
      triggers: conditions.length ? ["서버 TypeDB 관계 추론"] : ["서버 TypeDB 추론 대기"]
    };
  }).sort(function (a, b) {
    if (a.priority !== b.priority) return a.priority - b.priority;
    return String(a.symbol || "").localeCompare(String(b.symbol || ""));
  });
}

function compactSymbolList(symbols) {
  var unique = [];
  (symbols || []).forEach(function (symbol) {
    var key = String(symbol || "").toUpperCase();
    if (key && unique.indexOf(key) < 0) unique.push(key);
  });
  if (!unique.length) return "없음";
  var visible = unique.slice(0, 8).map(function (symbol) {
    return stockDisplayName(symbol);
  }).join(", ");
  return unique.length > 8 ? visible + " 외 " + (unique.length - 8) + "개" : visible;
}

function compactStrategyDataSymbols(symbols) {
  var unique = [];
  (symbols || []).forEach(function (symbol) {
    var key = String(symbol || "").toUpperCase();
    if (key && unique.indexOf(key) < 0) unique.push(key);
  });
  if (!unique.length) return "없음";
  var visible = unique.slice(0, 4).map(function (symbol) {
    return stockDisplayName(symbol);
  }).join(", ");
  return unique.length > 4 ? visible + " 외 " + (unique.length - 4) + "개" : visible;
}

function diagnosticTone(missingCount, totalCount) {
  if (!totalCount) return "hold";
  if (missingCount >= totalCount) return "danger";
  if (missingCount > 0) return "caution";
  return "watch";
}

function diagnosticCoverage(totalCount, missingCount) {
  return Math.max(0, totalCount - missingCount) + "/" + totalCount;
}

function settingEnabled(name) {
  var value = String(settingValue(name) || defaultSettings[name] || "1").trim().toLowerCase();
  return ["0", "false", "no", "off", "disabled"].indexOf(value) < 0;
}

function newsProviderLabel(value) {
  var key = String(value || "auto").toLowerCase().replace(/[-\s]/g, "_");
  if (key === "alpha" || key === "alphavantage" || key === "alpha_vantage") return "Alpha Vantage";
  if (key === "gdelt") return "GDELT";
  return "Auto";
}

function newsStateSettingLabel(name, value) {
  var catalogs = {
    relevance: { context: "관련 맥락", related: "관련 기사", direct: "종목 직접 기사" },
    materiality: { routine: "일상 정보", notable: "확인할 정보", material: "중요 정보", critical: "즉시 확인 정보" },
    trust: { limited: "제한적 출처", standard: "일반 출처", trusted: "신뢰 출처", primary: "공식 원문" }
  };
  var catalog = catalogs[name] || {};
  var key = String(value || "").trim().toLowerCase();
  return catalog[key] || key || "설정 필요";
}

function strategyDataDiagnostics(snapshot) {
  var items = buildTradeSignalItems(snapshot);
  var total = items.length;
  var toss = snapshot && snapshot.toss ? snapshot.toss : {};

  function missingSymbols(predicate) {
    return items.filter(predicate).map(function (item) { return item.symbol; });
  }

  var missingValuation = missingSymbols(function (item) {
    return !item.valuation || !Number(item.valuation.fairValue || 0);
  });
  var missingPrice = missingSymbols(function (item) {
    return !Number(item.currentPrice || 0);
  });
  var missingTradeStrength = missingSymbols(function (item) {
    return !Number(item.signal && item.signal.tradeStrength || 0);
  });
  var missingExecutionVolume = missingSymbols(function (item) {
    var signal = item.signal || {};
    return !Number(signal.buyVolume || 0) || !Number(signal.sellVolume || 0);
  });
  var missingInvestorFlow = missingSymbols(function (item) {
    var signal = item.signal || {};
    return !Number(signal.foreignNet || 0) && !Number(signal.institutionNet || 0) && !Number(signal.individualNet || 0);
  });
  var missingOrderbook = missingSymbols(function (item) {
    return !Number(item.signal && item.signal.bidAskImbalance || 0);
  });

  return [
    {
      label: "Toss 계좌 데이터",
      value: toss.mode === "live" ? "live" : (toss.mode || "대기"),
      tone: toss.mode === "live" ? "watch" : "caution",
      description: toss.status || "계좌 연결 상태를 확인합니다.",
      symbols: [],
      action: "계정·연결의 Toss 연결값 확인"
    },
    {
      label: "현재가",
      value: diagnosticCoverage(total, missingPrice.length),
      tone: diagnosticTone(missingPrice.length, total),
      description: "현재가가 있어야 적정가와 현재가 차이, 가격 기준을 계산합니다.",
      symbols: missingPrice,
      action: "Toss prices/candles 응답 또는 종목 코드 확인"
    },
    {
      label: "적정가 가정",
      value: diagnosticCoverage(total, missingValuation.length),
      tone: diagnosticTone(missingValuation.length, total),
      description: "EPS, 목표 PER, 안전마진이 있어야 싸다/비싸다 판단이 안정됩니다.",
      symbols: missingValuation,
      action: "투자 판단의 종목별 EPS/PER 입력"
    },
    {
      label: "체결강도",
      value: diagnosticCoverage(total, missingTradeStrength.length),
      tone: diagnosticTone(missingTradeStrength.length, total),
      description: "체결강도가 없으면 당일 매수·매도 방향을 확정하지 않고 자료 상태를 일부 부족으로 표시합니다.",
      symbols: missingTradeStrength,
      action: "Toss 체결 데이터 연결 또는 수동 수급 입력"
    },
    {
      label: "매수/매도 체결량",
      value: diagnosticCoverage(total, missingExecutionVolume.length),
      tone: diagnosticTone(missingExecutionVolume.length, total),
      description: "매수 체결 비중과 방향성 거래량을 계산하는 핵심 입력입니다.",
      symbols: missingExecutionVolume,
      action: "marketSignalInputs에 매수량/매도량 보강"
    },
    {
      label: "투자자 수급",
      value: diagnosticCoverage(total, missingInvestorFlow.length),
      tone: diagnosticTone(missingInvestorFlow.length, total),
      description: "외국인·기관·개인 순매수가 없으면 큰 투자자의 수급 방향을 근거에 넣지 않습니다.",
      symbols: missingInvestorFlow,
      action: "외국인/기관/개인 순매수 입력 또는 공급자 연결"
    },
    {
      label: "호가 불균형",
      value: diagnosticCoverage(total, missingOrderbook.length),
      tone: diagnosticTone(missingOrderbook.length, total),
      description: "호가 압력이 없으면 단기 진입/축소 신호가 약해집니다.",
      symbols: missingOrderbook,
      action: "호가 데이터 연결 또는 수동 수급 입력"
    },
    {
      label: "자료 상태",
      value: items.filter(function (item) { return item.dataState === "sufficient"; }).length + "/" + total,
      tone: items.some(function (item) { return ["insufficient", "unavailable"].indexOf(item.dataState) >= 0; }) ? "caution" : "watch",
      description: "필수 자료의 존재와 신선도를 충분·일부·부족·사용 불가로 구분합니다.",
      symbols: missingPrice,
      action: "부족한 원천 데이터 연결"
    },
    {
      label: "AI 검증 상태",
      value: items.filter(function (item) { return item.validationState === "ready"; }).length + "/" + total,
      tone: items.some(function (item) { return item.validationState === "blocked"; }) ? "danger" : "watch",
      description: "근거, 반대 근거, 무효화 조건이 갖춰졌는지 검증 완료·조건부·보류로 나눕니다.",
      symbols: items.filter(function (item) { return item.validationState !== "ready"; }).map(function (item) { return item.symbol; }),
      action: "조건부 또는 보류 사유 확인"
    }
  ];
}

function categoricalModelState(item) {
  item = item || {};
  var review = decisionStateMeta("review", item.reviewLevel, item.hasData ? "observe" : "blocked");
  return {
    action: item.action || (item.source === "watchlist" ? "관심 유지" : "보유 유지"),
    tone: item.tone || review.tone,
    rank: review.key === "blocked" ? 99 : 5 - review.rank,
    reviewLevel: review.key,
    dataState: decisionStateMeta("data", item.dataState, "partial").key,
    changeState: decisionStateMeta("change", item.changeState, "unchanged").key,
    conflictState: decisionStateMeta("conflict", item.conflictState, "context-only").key,
    validationState: decisionStateMeta("validation", item.validationState, "conditional").key,
    variables: item.features || modelFeatureVariables(item, item.signal || {}, item.valuation || {}),
    conditions: item.relationRules || []
  };
}

function modelFeatureAudit(item) {
  var conditions = item.relationRules || [];
  var groups = ["risk", "support", "counter", "context", "blocking"].map(function (role) {
    return {
      key: role,
      label: decisionStateMeta("evidence", role, "context").label,
      count: conditions.filter(function (condition) { return String(condition.evidenceRole || "context") === role; }).length
    };
  }).filter(function (group) { return group.count > 0; });
  return {
    stable: item.changeState === "unchanged",
    groups: groups,
    variables: item.features || modelFeatureVariables(item, item.signal || {}, item.valuation || {}),
    conditions: conditions
  };
}

function modelStatsForItems(items) {
  var states = (items || []).map(categoricalModelState);
  var actionCount = states.filter(function (item) { return ["act", "immediate"].indexOf(item.reviewLevel) >= 0; }).length;
  return {
    actionCount: actionCount,
    checkCount: states.filter(function (item) { return item.reviewLevel === "check"; }).length,
    observeCount: states.filter(function (item) { return item.reviewLevel === "observe"; }).length,
    blockedCount: states.filter(function (item) { return item.reviewLevel === "blocked" || item.validationState === "blocked"; }).length,
    readyCount: states.filter(function (item) { return item.validationState === "ready"; }).length,
    conditionalCount: states.filter(function (item) { return item.validationState === "conditional"; }).length
  };
}

export { buildTradeSignalItems, categoricalModelState, compactStrategyDataSymbols, compactSymbolList, currentPriceOf, decisionStateMeta, instrumentItems, marketSignalForItem, modelFeatureAudit, modelFeatureVariables, modelStatsForItems, newsProviderLabel, newsStateSettingLabel, parseMarketSignals, settingEnabled, stateValueFromSources, strategyDataDiagnostics };
