import { consoleReadModelAccountId } from "../accounts/identity.mjs";
import { instrumentItems } from "../decisions/signals.mjs";
import { stockDisplayName } from "../instruments/catalog.mjs";
import { normalizePortfolioView } from "../navigation/routes.mjs";
import { selectConsolePortfolio } from "./selectors.mjs";
import { render } from "../render/scheduler.mjs";
import { clearReadModelPoll, readModelIsWarming, requestJson, scheduleReadModelPoll } from "../requests/json.mjs";
import { createLatestRequestLane } from "../requests/latest.mjs";
import { numeric } from "../shared/format.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { portfolioState } from "../state/portfolio.mjs";
import { shellState } from "../state/shell.mjs";

var portfolioAccount = "";
const portfolioLanes = new Map();

function ensurePortfolioAccount() {
  var account = consoleReadModelAccountId();
  if (account !== portfolioAccount) {
    portfolioLanes.forEach(function (lane) { lane.invalidate(); });
    portfolioLanes.clear();
    portfolioState.portfolioReadModels = {};
    portfolioState.portfolioReadModelLoadingByView = {};
    portfolioState.portfolioReadModelErrorsByView = {};
    portfolioState.portfolioReadModelLoading = false;
    portfolioState.portfolioInterpretation = null;
    portfolioState.portfolioInterpretationLoading = false;
    portfolioAccount = account;
  }
  return account;
}

function portfolioLane(view) {
  if (!portfolioLanes.has(view)) portfolioLanes.set(view, createLatestRequestLane());
  return portfolioLanes.get(view);
}

function portfolioReadModelBusy(view) {
  if (portfolioAccount !== consoleReadModelAccountId()) return false;
  return Boolean(portfolioState.portfolioReadModelLoadingByView[normalizePortfolioView(view)]);
}

function prefetchPortfolioReadModelNeighbor(view) {
  if (isStaticPreviewHost()) return;
  var order = ["summary", "positions", "rebalance", "activity"];
  var index = order.indexOf(normalizePortfolioView(view));
  var neighbor = order[index + 1] || order[index - 1];
  if (!neighbor || portfolioState.portfolioReadModels[neighbor] || portfolioReadModelBusy(neighbor)) return;
  window.setTimeout(function () {
    loadPortfolioReadModel(neighbor, false, { prefetch: true });
  }, 0);
}

function loadPortfolioReadModel(view, force, options) {
  options = options || {};
  var accountId = ensurePortfolioAccount();
  var normalized = normalizePortfolioView(view || portfolioState.activePortfolioView);
  if (isStaticPreviewHost()) {
    var previewPortfolio = selectConsolePortfolio(shellState.snapshot || {});
    var previewPositions = instrumentItems(shellState.snapshot || {}).filter(function (item) {
      return String(item.source || "") !== "watchlist";
    }).map(function (item) {
      return Object.assign({}, item, {
        name: stockDisplayName(item.symbol, item),
        marketValueKrw: numeric(item.marketValueKrw || item.marketValue)
      });
    });
    portfolioState.portfolioReadModels[normalized] = {
      version: "console-read-model-v1",
      view: normalized,
      summary: {
        total: previewPortfolio.total,
        cash: previewPortfolio.cash,
        positionCount: previewPositions.length,
        status: "preview",
        reconciliationStatus: "preview",
        rebalanceStatus: "not-available"
      },
      positions: previewPositions,
      policyBreaches: [],
      risk: {},
      proposal: {},
      candidates: [],
      ledgerEntries: [],
      activityEpisodes: []
    };
    return Promise.resolve(portfolioState.portfolioReadModels[normalized]);
  }
  var lane = portfolioLane(normalized);
  if (!force && lane.active(accountId)) return lane.active(accountId);
  if (portfolioState.portfolioReadModels[normalized] && !force) {
    if (!options.prefetch) prefetchPortfolioReadModelNeighbor(normalized);
    return Promise.resolve(portfolioState.portfolioReadModels[normalized]);
  }
  var operation = lane.begin(accountId);
  portfolioState.portfolioReadModelLoadingByView[normalized] = true;
  portfolioState.portfolioReadModelLoading = true;
  portfolioState.portfolioReadModelErrorsByView[normalized] = "";
  if (normalized === portfolioState.activePortfolioView) portfolioState.portfolioReadModelError = "";
  var paths = {
    summary: "/api/portfolio/summary",
    positions: "/api/portfolio/positions",
    rebalance: "/api/portfolio/rebalance",
    activity: "/api/portfolio/activity"
  };
  var params = new URLSearchParams();
  params.set("accountId", accountId);
  return operation.track(requestJson(paths[normalized] + "?" + params.toString(), {
    key: "portfolio-read-model:" + accountId + ":" + normalized,
    force: Boolean(force),
    timeoutMs: 15000
  }).then(function (payload) {
    if (!operation.current() || accountId !== consoleReadModelAccountId()) return null;
    portfolioState.portfolioReadModels[normalized] = payload || {};
    if (readModelIsWarming(payload)) {
      scheduleReadModelPoll("portfolio-read-model:" + normalized, function () { loadPortfolioReadModel(normalized, true); });
    } else {
      clearReadModelPoll("portfolio-read-model:" + normalized);
      if (!options.prefetch) prefetchPortfolioReadModelNeighbor(normalized);
    }
    return portfolioState.portfolioReadModels[normalized];
  }).catch(function (error) {
    if (!operation.current() || accountId !== consoleReadModelAccountId()) return null;
    var message = error.message || "포트폴리오 원장을 읽지 못했습니다.";
    portfolioState.portfolioReadModelErrorsByView[normalized] = message;
    if (normalized === portfolioState.activePortfolioView) portfolioState.portfolioReadModelError = message;
    return null;
  }).finally(function () {
    if (!operation.current()) return;
    operation.finish();
    portfolioState.portfolioReadModelLoadingByView[normalized] = false;
    portfolioState.portfolioReadModelLoading = Object.keys(portfolioState.portfolioReadModelLoadingByView).some(function (key) {
      return Boolean(portfolioState.portfolioReadModelLoadingByView[key]);
    });
    if (shellState.snapshot && (!options.prefetch || normalized === portfolioState.activePortfolioView)) render();
  }));
}

function loadPortfolioInterpretation(force) {
  var accountId = ensurePortfolioAccount();
  if (isStaticPreviewHost()) {
    portfolioState.portfolioInterpretation = (portfolioState.portfolioReadModels.summary || {}).interpretation || {};
    return Promise.resolve(portfolioState.portfolioInterpretation);
  }
  var lane = portfolioLane("interpretation");
  if (!force && lane.active(accountId)) return lane.active(accountId);
  if (portfolioState.portfolioInterpretation && !force) return Promise.resolve(portfolioState.portfolioInterpretation);
  var operation = lane.begin(accountId);
  portfolioState.portfolioInterpretationLoading = true;
  portfolioState.portfolioInterpretationError = "";
  var params = new URLSearchParams();
  params.set("accountId", accountId);
  return operation.track(requestJson("/api/portfolio/interpretation?" + params.toString(), {
    key: "portfolio-interpretation:" + accountId,
    force: Boolean(force),
    timeoutMs: 15000
  }).then(function (payload) {
    if (!operation.current() || accountId !== consoleReadModelAccountId()) return null;
    if (readModelIsWarming(payload)) {
      portfolioState.portfolioInterpretation = (portfolioState.portfolioReadModels.summary || {}).interpretation || {
        contract: "portfolio-interpretation-v1",
        status: "pending",
        statusLabel: "해석 준비 중",
        headline: "최신 포트폴리오 해석을 준비하고 있습니다.",
        rationale: "현재 원장과 저장된 판단 리비전을 확인하는 중입니다.",
        drivers: [],
        ai: { executed: false, current: false },
        revision: { state: "unknown" }
      };
      scheduleReadModelPoll("portfolio-interpretation", function () { loadPortfolioInterpretation(true); });
      return portfolioState.portfolioInterpretation;
    }
    clearReadModelPoll("portfolio-interpretation");
    portfolioState.portfolioInterpretation = (payload || {}).interpretation || {};
    return portfolioState.portfolioInterpretation;
  }).catch(function (error) {
    if (!operation.current() || accountId !== consoleReadModelAccountId()) return null;
    portfolioState.portfolioInterpretationError = error.message || "포트폴리오 해석을 읽지 못했습니다.";
    return null;
  }).finally(function () {
    if (!operation.current()) return;
    operation.finish();
    portfolioState.portfolioInterpretationLoading = false;
    if (shellState.snapshot) render();
  }));
}

export { loadPortfolioInterpretation, loadPortfolioReadModel, portfolioReadModelBusy };
