import { currentPriceOf } from "../decisions/signals.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { settingValue } from "../settings/fields.mjs";
import { parseNumberAssignments } from "../settings/formulas.mjs";
import { formatClock, formatMoney, numeric, pct } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";

function portfolioFxRates() {
  return parseNumberAssignments(settingValue("fxRates"), parseNumberAssignments(defaultSettings.fxRates));
}

function portfolioFxRateText(rates) {
  return Object.keys(rates || {}).sort().map(function (key) {
    return key.toUpperCase() + "=" + Number(rates[key] || 0).toLocaleString("ko-KR");
  }).join(", ");
}

function portfolioItemCurrency(item) {
  var explicit = String(item && item.currency || "").toUpperCase();
  if (explicit) return explicit;
  var market = String(item && item.market || "").toUpperCase();
  var symbol = String(item && item.symbol || "");
  if (market === "US") return "USD";
  if (market === "KR" || market === "KOSPI" || market === "KOSDAQ" || symbol.match(/^\d{6}$/)) return "KRW";
  return "KRW";
}

function portfolioValueInBase(value, currency, rates) {
  var code = String(currency || "KRW").toUpperCase();
  return Math.max(0, numeric(value) * Number((rates || {})[code] || 1));
}

function portfolioPositionBaseValue(item, rates) {
  var accountValue = numeric(item && (item.accountValueKrw || item.account_value_krw));
  if (accountValue) return Math.max(0, accountValue);
  var marketValue = numeric(item && item.marketValue);
  if (!marketValue && numeric(item && item.quantity) && currentPriceOf(item || {})) {
    marketValue = numeric(item.quantity) * currentPriceOf(item);
  }
  return portfolioValueInBase(marketValue, portfolioItemCurrency(item || {}), rates);
}

function portfolioHoldingPositions(snapshot) {
  var toss = snapshot.toss || {};
  return (toss.positions || []).filter(function (item) {
    return item && item.source !== "cash" && item.sector !== "현금" && String(item.symbol || "").toUpperCase() !== "CASH";
  });
}

function portfolioCashPositions(snapshot) {
  var toss = snapshot.toss || {};
  return (toss.positions || []).filter(function (item) {
    return item && (item.source === "cash" || item.sector === "현금" || String(item.symbol || "").toUpperCase() === "CASH");
  });
}

function portfolioSum(items, picker) {
  return (items || []).reduce(function (total, item) {
    return total + numeric(picker(item));
  }, 0);
}

function exposureDiffText(actual, expected) {
  var diff = numeric(actual) - numeric(expected);
  if (Math.abs(diff) < 1) return "일치";
  return "차이 " + (diff > 0 ? "+" : "-") + formatMoney(Math.abs(diff));
}

function portfolioValuationBasisLabel(value) {
  return ({
    "broker-net": "토스 비용 반영",
    "broker-gross": "토스 비용 전",
    "mark-to-market": "분석 시가",
    "legacy-unknown": "과거 기준 미확인"
  })[String(value || "").toLowerCase()] || "평가 기준 미확인";
}

function portfolioValuationMetricLabel(value) {
  var basis = String(value || "").toLowerCase();
  if (basis === "mark-to-market") return "분석 시가 총 평가";
  if (basis === "legacy-unknown") return "총 평가";
  return "토스 기준 총 평가";
}

function portfolioInvestedMetricLabel(value) {
  var basis = String(value || "").toLowerCase();
  if (basis === "mark-to-market") return "분석 투자 평가";
  if (basis === "legacy-unknown") return "투자 평가";
  return "토스 내 투자";
}

function portfolioValuationFxText(portfolio) {
  var valuation = (portfolio && portfolio.valuation) || {};
  var context = valuation.fxContext || {};
  var rows = Object.keys(context).sort().map(function (currency) {
    var item = context[currency] || {};
    return currency + " " + Number(item.rate || 0).toLocaleString("ko-KR") + " (" + (item.source || item.state || "미확인") + ")";
  });
  return rows.length ? rows.join(", ") : portfolioFxRateText(portfolioFxRates());
}

function portfolioCashBasisText(snapshot, portfolio) {
  var cashPositions = portfolioCashPositions(snapshot);
  var account = (snapshot.toss || {}).account || {};
  if (cashPositions.length) return "CASH 포지션 marketValue 우선";
  if (numeric(account.orderableAmount)) return "계좌 orderableAmount / buying-power";
  if (numeric(portfolio.cash)) return "계좌 현금 필드";
  return "현금 없음 또는 API 미응답";
}

function renderPortfolioMarketRows(portfolio) {
  return (portfolio.markets || []).filter(function (market) {
    return Number(market.total || 0) > 0;
  }).map(function (market) {
    var label = market.label || market.key || "-";
    var value = [
      "투자 " + formatMoney(market.invested || 0),
      "현금 " + formatMoney(market.cash || 0),
      "합계 " + formatMoney(market.total || 0),
      "현금비중 " + pct(market.cashRatio || 0)
    ].join(" · ");
    return '<div class="source-row"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong></div>';
  }).join("");
}

function renderPortfolioBasisRows(snapshot, portfolio) {
  var rates = portfolioFxRates();
  var holdings = portfolioHoldingPositions(snapshot);
  var ledgerInvested = holdings.reduce(function (total, item) {
    return total + portfolioPositionBaseValue(item, rates);
  }, 0);
  var marketTotal = portfolioSum(portfolio.markets || [], function (market) { return market.total; });
  var sectorTotal = portfolioSum(portfolio.sectors || [], function (sector) { return sector.value; });
  var formulaTotal = numeric(portfolio.invested) + numeric(portfolio.cash);
  var source = (snapshot.toss && snapshot.toss.status ? snapshot.toss.status : "토스 스냅샷") + " · " + (snapshot.dataMode || (snapshot.mock ? "mock" : "live"));
  var basis = String(portfolio.valuationBasis || portfolio.valuation_basis || "legacy-unknown");
  var valuation = portfolio.valuation || {};
  var snapshotId = String(portfolio.valuationSnapshotId || portfolio.valuation_snapshot_id || valuation.valuationSnapshotId || "");
  var componentAsOf = valuation.componentAsOf || {};
  var rows = [
    ["데이터 원천", source],
    ["총 평가 기준", portfolioValuationBasisLabel(basis)],
    ["평가 스냅샷", snapshotId ? snapshotId.slice(-12) : "과거 기준 미확인"],
    ["환율 기준", portfolioValuationFxText(portfolio)],
    ["구성 시각", [componentAsOf.holdings && "잔고 " + formatClock(componentAsOf.holdings), componentAsOf.prices && "시세 " + formatClock(componentAsOf.prices), componentAsOf.fx && "환율 " + formatClock(componentAsOf.fx)].filter(Boolean).join(" · ") || formatClock(snapshot.generatedAt)],
    ["현금 기준", portfolioCashBasisText(snapshot, portfolio)],
    ["총 평가 산식", formatMoney(portfolio.invested || 0) + " + " + formatMoney(portfolio.cash || 0) + " = " + formatMoney(formulaTotal)],
    ["총 평가 차이", exposureDiffText(portfolio.total || 0, formulaTotal)],
    ["보유 원장 합계", holdings.length + "개 marketValue 원화환산 = " + formatMoney(ledgerInvested)],
    ["투자 평가액 차이", exposureDiffText(portfolio.invested || 0, ledgerInvested)],
    ["시장별 합계", (portfolio.markets || []).length + "개 시장 total = " + formatMoney(marketTotal)],
    ["시장 합계 차이", exposureDiffText(portfolio.total || 0, marketTotal)],
    ["섹터별 합계", (portfolio.sectors || []).length + "개 sector value = " + formatMoney(sectorTotal)],
    ["섹터 합계 차이", exposureDiffText(portfolio.total || 0, sectorTotal)]
  ];
  return rows.slice(0, 5).map(function (row) {
    return '<div class="source-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
  }).join("") + (rows.length > 5 ? '<div class="source-row"><span>상세 산식</span><strong>나머지 ' + escapeHtml(rows.length - 5) + '개 검증 행은 계정·연결의 자산 검증에서 확인</strong></div>' : '');
}

function renderPortfolioPanel(snapshot) {
  var portfolio = snapshot.portfolio || { sectors: [] };
  var marketRows = renderPortfolioMarketRows(portfolio);
  return [
    '<article class="panel portfolio-exposure-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Toss Portfolio</p>',
    '<h2>계좌 노출</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(formatMoney(portfolio.total)) + '</span>',
    '</div>',
    '<div class="allocation">',
    '<div class="source-row"><span>투자 평가액</span><strong>' + escapeHtml(formatMoney(portfolio.invested || 0)) + '</strong></div>',
    '<div class="source-row"><span>현금/주문 가능</span><strong>' + escapeHtml(formatMoney(portfolio.cash || 0)) + '</strong></div>',
    marketRows,
    (portfolio.sectors || []).slice(0, 6).map(function (sector) {
      return [
        '<div class="bar-row">',
        '<div class="bar-meta"><span>' + escapeHtml(sector.sector) + '</span><strong>' + escapeHtml(pct(sector.ratio)) + '</strong></div>',
        '<div class="bar-track"><span style="width:' + Math.min(100, Math.max(2, sector.ratio)) + '%"></span></div>',
        '</div>'
      ].join("");
    }).join(""),
    (portfolio.sectors || []).length > 6 ? '<div class="bar-row"><div class="bar-meta"><span>기타 섹터</span><strong>' + escapeHtml((portfolio.sectors || []).length - 6) + '개</strong></div><div class="bar-track"><span style="width:8%"></span></div></div>' : '',
    '</div>',
    '<div class="source-stack">',
    renderPortfolioBasisRows(snapshot, portfolio),
    '</div>',
    '<div class="rule-strip"><span>금액이 맞지 않으면 먼저 보유 원장 합계, 현금 기준, 환율 기준의 차이 행을 확인하세요.</span></div>',
    '</article>'
  ].join("");
}

export { exposureDiffText, portfolioCashBasisText, portfolioInvestedMetricLabel, portfolioValuationBasisLabel, portfolioValuationMetricLabel, renderPortfolioBasisRows, renderPortfolioPanel };
