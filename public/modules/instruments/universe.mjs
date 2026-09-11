import { defaultSymbolUniversePayload } from "./catalog.mjs";
import { DEFAULT_SYMBOL_UNIVERSE_LIMIT } from "./constants.mjs";
import { symbolUniverseRefreshCollapseTimerCell } from "./refresh-runtime.mjs";
import { marketLabel, symbolUniverseKey } from "./universe-view.mjs";
import { mergeUniqueItems, mobileInfiniteScrollEnabled } from "../navigation/infinite-list.mjs";
import { navigateToTab } from "../navigation/router.mjs";
import { writeMarketWorkspaceHistory } from "../navigation/routes.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { createLatestRequestLane } from "../requests/latest.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { latestChangedFirst } from "../shared/format.mjs";
import { app } from "../shell/root.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";
import { loadCachedPayload, persistSymbolUniverseRefreshHistory, writeCachedPayload, writePersistentPayload } from "../state/storage.mjs";
import { universeState } from "../state/universe.mjs";

var symbolUniverseMemoryStore = "";

var symbolUniverseRefreshPollTimer = null;

var symbolUniverseRefreshFocusTimer = null;

var symbolUniverseRefreshLoadedJobId = "";

var symbolUniverseRefreshNotifiedJobId = "";

function applySymbolUniverse(payload) {
  var payloadOffset = Math.max(0, Number(payload.offset == null ? universeState.symbolUniverseOffset : payload.offset));
  var incomingItems = Array.isArray(payload.items) ? payload.items : [];
  var items = mobileInfiniteScrollEnabled() && payloadOffset > 0
    ? mergeUniqueItems((universeState.symbolUniverse || {}).items, incomingItems, symbolUniverseKey)
    : incomingItems;
  items = latestChangedFirst(items);
  var refreshContext = universeState.symbolUniverseRefreshContext;
  var currentContext = symbolUniverseRefreshContext();
  var sameRefreshContext = refreshContext
    && refreshContext.query === currentContext.query
    && refreshContext.market === currentContext.market
    && Number(refreshContext.limit) === Number(currentContext.limit);
  var stableOrder = sameRefreshContext ? (universeState.symbolUniverseRefreshStableOrder || []) : [];
  if (stableOrder.length && payloadOffset === 0) {
    var positions = {};
    stableOrder.forEach(function (key, index) { positions[key] = index; });
    items = items.map(function (item, index) {
      var key = symbolUniverseKey(item);
      return { item: item, index: index, position: Object.prototype.hasOwnProperty.call(positions, key) ? positions[key] : stableOrder.length + index };
    }).sort(function (left, right) {
      return left.position - right.position || left.index - right.index;
    }).map(function (entry) { return entry.item; });
  }
  if (sameRefreshContext && universeState.symbolUniverseRefreshBaseline && Object.keys(universeState.symbolUniverseRefreshBaseline).length) {
    var changedKeys = {};
    items.forEach(function (item) {
      var key = symbolUniverseKey(item);
      var baseline = universeState.symbolUniverseRefreshBaseline[key];
      if (baseline == null || baseline !== symbolUniverseItemSignature(item)) changedKeys[key] = true;
    });
    universeState.symbolUniverseChangedKeys = changedKeys;
  } else if (universeState.symbolUniverseRefreshContext) {
    universeState.symbolUniverseChangedKeys = {};
  }
  if (universeState.symbolUniverseRefreshContext) {
    universeState.symbolUniverseRefreshBaseline = {};
    universeState.symbolUniverseRefreshStableOrder = [];
    universeState.symbolUniverseRefreshContext = null;
  }
  universeState.symbolUniverse = {
    items: items,
    summary: payload.summary || { markets: [], sources: [], total: 0, maxAgeHours: 24 },
    resultTotal: Number(payload.resultTotal || 0),
    limit: Number(payload.limit || universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT),
    offset: payloadOffset,
    hasMore: Boolean(payload.hasMore)
  };
  universeState.symbolUniverseLimit = universeState.symbolUniverse.limit;
  universeState.symbolUniverseOffset = universeState.symbolUniverse.offset;
  universeState.symbolUniverseLoaded = true;
  universeState.symbolUniverseError = "";
}

function symbolUniversePath() {
  var params = new URLSearchParams();
  if (universeState.symbolUniverseQuery) params.set("query", universeState.symbolUniverseQuery);
  if (universeState.symbolUniverseMarket) params.set("market", universeState.symbolUniverseMarket);
  params.set("limit", String(universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT));
  params.set("offset", String(universeState.symbolUniverseOffset || 0));
  return "/api/symbol-universe?" + params.toString();
}

function loadCachedSymbolUniverse() {
  return loadCachedPayload("orbitAlphaSymbolUniverse", symbolUniverseMemoryStore);
}

function writeCachedSymbolUniverse(payload) {
  return writeCachedPayload("orbitAlphaSymbolUniverse", payload, function (serialized) {
    symbolUniverseMemoryStore = serialized;
  });
}

const universeLane = createLatestRequestLane();

function loadSymbolUniverse() {
  var identity = symbolUniversePath();
  var active = universeLane.active(identity);
  if (active) return active;
  var operation = universeLane.begin(identity);
  var cached = loadCachedSymbolUniverse();
  var requestedLimit = Math.max(1, Math.min(500, Number(universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT)));
  var requestedOffset = Math.max(0, Number(universeState.symbolUniverseOffset || 0));
  var incrementalLoad = mobileInfiniteScrollEnabled()
    && requestedOffset > 0
    && Boolean(((universeState.symbolUniverse || {}).items || []).length);
  if (cached && (!universeState.symbolUniverseLoaded || !(universeState.symbolUniverse.items || []).length)) {
    applySymbolUniverse(cached);
    universeState.symbolUniverseLimit = requestedLimit;
    universeState.symbolUniverseOffset = requestedOffset;
    universeState.symbolUniverse.limit = requestedLimit;
    universeState.symbolUniverse.offset = requestedOffset;
    universeState.symbolUniverse.items = (universeState.symbolUniverse.items || []).slice(0, requestedLimit);
    if (shellState.snapshot) render();
  }
  universeState.symbolUniverseLoading = true;
  universeState.symbolUniverseError = "";
  if (!incrementalLoad && shellState.snapshot) render();
  if (isStaticPreviewHost()) {
    applySymbolUniverse(defaultSymbolUniversePayload());
    universeState.symbolUniverseLoading = false;
    operation.finish();
    return Promise.resolve();
  }
  return operation.track(requestJson(identity)
    .then(function (payload) {
      if (!operation.current() || symbolUniversePath() !== identity) return;
      applySymbolUniverse(payload);
      var cachedPayload = mobileInfiniteScrollEnabled()
        ? Object.assign({}, universeState.symbolUniverse, { offset: 0 })
        : payload;
      writeCachedSymbolUniverse(cachedPayload);
    })
    .catch(function (error) {
      if (!operation.current() || symbolUniversePath() !== identity) return;
      var message = error.message || "종목 유니버스를 읽지 못했습니다.";
      universeState.symbolUniverseError = message;
      if (!(universeState.symbolUniverse.items || []).length) {
        applySymbolUniverse(cached || defaultSymbolUniversePayload());
        universeState.symbolUniverseError = message;
      }
    })
    .finally(function () {
      if (!operation.current()) return;
      universeState.symbolUniverseLoading = false;
      operation.finish();
      if (shellState.snapshot) render();
    }));
}

function symbolUniverseRefreshActive(payload) {
  var status = String((payload || {}).status || "").toLowerCase();
  return Boolean((payload || {}).running) || ["submitting", "queued", "running"].indexOf(status) >= 0;
}

function symbolUniverseRefreshContext() {
  return {
    query: String(universeState.symbolUniverseQuery || ""),
    market: String(universeState.symbolUniverseMarket || ""),
    limit: Number(universeState.symbolUniverseLimit || DEFAULT_SYMBOL_UNIVERSE_LIMIT)
  };
}

function symbolUniverseItemSignature(item) {
  item = item || {};
  return [
    item.symbol,
    item.name,
    item.market,
    item.exchange,
    item.currency,
    item.sector,
    item.assetType,
    item.source
  ].map(function (value) { return String(value || "").trim(); }).join("|");
}

function snapshotSymbolUniverseRefreshContext() {
  var items = (universeState.symbolUniverse && universeState.symbolUniverse.items) || [];
  var baseline = {};
  universeState.symbolUniverseRefreshStableOrder = items.map(function (item) {
    var key = symbolUniverseKey(item);
    baseline[key] = symbolUniverseItemSignature(item);
    return key;
  });
  universeState.symbolUniverseRefreshBaseline = baseline;
  universeState.symbolUniverseRefreshContext = symbolUniverseRefreshContext();
  universeState.symbolUniverseChangedKeys = {};
}

function rememberSymbolUniverseRefreshJob(payload) {
  if (!payload || !payload.jobId) return;
  var item = {
    jobId: String(payload.jobId || ""),
    status: String(payload.status || "idle"),
    markets: Array.isArray(payload.markets) ? payload.markets.slice(0, 3) : [],
    completedCount: Number(payload.completedCount || 0),
    totalCount: Number(payload.totalCount || 0),
    results: Array.isArray(payload.results) ? payload.results.map(function (result) {
      return {
        market: String(result.market || ""),
        status: String(result.status || ""),
        count: Number(result.count || 0),
        error: String(result.error || "").slice(0, 300)
      };
    }) : [],
    requestedAt: String(payload.requestedAt || ""),
    finishedAt: String(payload.finishedAt || ""),
    lastError: String(payload.lastError || "").slice(0, 500)
  };
  var history = (universeState.symbolUniverseRefreshHistory || []).filter(function (existing) {
    return String(existing.jobId || "") !== item.jobId;
  });
  history.unshift(item);
  universeState.symbolUniverseRefreshHistory = history.slice(0, 5);
  persistSymbolUniverseRefreshHistory(universeState.symbolUniverseRefreshHistory);
}

function symbolUniverseRefreshStageMeta(payload) {
  payload = payload || {};
  var stage = String(payload.stage || payload.status || "idle").toLowerCase();
  var market = marketLabel(payload.currentMarket || "");
  var count = Number(payload.stageItemCount || 0);
  var labels = {
    submitting: "갱신 요청 전송",
    queued: "서버 작업 대기",
    connecting: (market ? market + " " : "") + "원천 연결",
    fetching: (market ? market + " " : "") + "종목 수집",
    saving: (market ? market + " " : "") + (count ? count.toLocaleString("ko-KR") + "개 " : "") + "저장",
    verifying: (market ? market + " " : "") + "저장 결과 확인",
    summarizing: "시장별 결과 정리",
    market_completed: (market ? market + " " : "") + "처리 완료",
    completed: "전체 갱신 완료",
    partial: "일부 시장 갱신 완료",
    failed: "갱신 실패"
  };
  return { stage: stage, label: labels[stage] || "갱신 상태 확인" };
}

function friendlySymbolUniverseRefreshError(value) {
  var message = String(value || "").trim();
  if (!message) return "원천 연결 상태를 확인한 뒤 실패한 시장만 다시 시도할 수 있습니다.";
  if (/timeout|timed out|시간.*초과/i.test(message)) return "원천 응답이 늦어 갱신을 마치지 못했습니다. 마지막 성공 목록은 그대로 유지됩니다.";
  if (/network|connection|연결|dns|name resolution/i.test(message)) return "원천 서버에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요.";
  if (/429|rate.?limit|too many/i.test(message)) return "원천 호출 한도를 초과했습니다. 잠시 후 실패한 시장만 다시 시도해 주세요.";
  return "일부 원천을 처리하지 못했습니다. 마지막 성공 목록은 그대로 유지됩니다.";
}

function failedSymbolUniverseRefreshMarkets(payload) {
  var results = Array.isArray((payload || {}).results) ? payload.results : [];
  var failed = results.filter(function (item) { return String(item.status || "").toLowerCase() !== "ok"; })
    .map(function (item) { return String(item.market || "").toUpperCase(); })
    .filter(Boolean);
  return failed.length ? failed : (String((payload || {}).status || "") === "failed" ? ((payload || {}).markets || []) : []);
}

function scheduleSymbolUniverseRefreshCollapse() {
  if (symbolUniverseRefreshCollapseTimerCell.value) clearTimeout(symbolUniverseRefreshCollapseTimerCell.value);
  symbolUniverseRefreshCollapseTimerCell.value = setTimeout(function () {
    universeState.symbolUniverseRefreshExpanded = false;
    render();
  }, 9000);
}

function focusSymbolUniverseRefreshStatus() {
  if (symbolUniverseRefreshFocusTimer) clearTimeout(symbolUniverseRefreshFocusTimer);
  symbolUniverseRefreshFocusTimer = setTimeout(function () {
    var target = app.querySelector("[data-symbol-refresh-anchor]");
    if (!target) return;
    if (target.scrollIntoView) target.scrollIntoView({ behavior: "smooth", block: "start" });
    if (target.focus) target.focus({ preventScroll: true });
  }, 180);
}

function openSymbolUniverseRefreshResult() {
  var refresh = universeState.symbolUniverseRefresh || {};
  universeState.symbolUniverseRefreshAcknowledgedJobId = String(refresh.jobId || "");
  universeState.symbolUniverseRefreshDismissedJobId = "";
  universeState.symbolUniverseRefreshExpanded = true;
  writePersistentPayload("orbitAlphaSymbolRefreshAcknowledged", universeState.symbolUniverseRefreshAcknowledgedJobId);
  marketState.marketWorkspaceMode = "universe";
  if (navigationState.activeTab !== "feed") navigateToTab("feed");
  writeMarketWorkspaceHistory("universe");
  render();
  focusSymbolUniverseRefreshStatus();
}

function symbolUniverseRefreshTerminal(payload) {
  return ["completed", "partial", "failed", "unknown"].indexOf(String((payload || {}).status || "").toLowerCase()) >= 0;
}

function normalizedSymbolUniverseRefreshStatus(payload) {
  payload = payload && typeof payload === "object" ? payload : {};
  var markets = Array.isArray(payload.markets) ? payload.markets : [];
  var completedMarkets = Array.isArray(payload.completedMarkets) ? payload.completedMarkets : [];
  var totalCount = Math.max(0, Number(payload.totalCount == null ? markets.length : payload.totalCount));
  var completedCount = Math.max(0, Number(payload.completedCount == null ? completedMarkets.length : payload.completedCount));
  var status = String(payload.status || "idle").toLowerCase();
  var progress = Number(payload.progressPercent);
  if (!Number.isFinite(progress)) progress = totalCount ? Math.round((completedCount / totalCount) * 100) : 0;
  return Object.assign({}, payload, {
    jobId: String(payload.jobId || ""),
    status: status,
    running: Boolean(payload.running) || ["submitting", "queued", "running"].indexOf(status) >= 0,
    markets: markets,
    completedMarkets: completedMarkets,
    completedCount: completedCount,
    totalCount: totalCount,
    progressPercent: Math.max(0, Math.min(100, progress)),
    results: Array.isArray(payload.results) ? payload.results : [],
    stage: String(payload.stage || status || "idle").toLowerCase(),
    currentMarket: String(payload.currentMarket || "").toUpperCase(),
    stageItemCount: Math.max(0, Number(payload.stageItemCount || 0)),
    updatedAt: String(payload.updatedAt || "")
  });
}

function symbolUniverseRefreshCompletionMessage(payload) {
  var status = String((payload || {}).status || "");
  if (status === "completed") return { message: "전체 종목 목록 갱신을 완료했습니다.", tone: "success" };
  if (status === "partial") return { message: "일부 시장만 갱신했습니다. 실패한 시장은 마지막 성공 목록을 유지합니다.", tone: "caution" };
  if (status === "failed") return { message: "전체 종목 목록 갱신에 실패했습니다. 마지막 성공 목록을 유지합니다.", tone: "danger" };
  if (status === "unknown") return { message: "이전 갱신 상태가 만료되어 최신 목록을 다시 확인합니다.", tone: "caution" };
  return null;
}

function clearSymbolUniverseRefreshPoll() {
  if (symbolUniverseRefreshPollTimer) clearTimeout(symbolUniverseRefreshPollTimer);
  symbolUniverseRefreshPollTimer = null;
}

function scheduleSymbolUniverseRefreshPoll(delayMs) {
  clearSymbolUniverseRefreshPoll();
  if (!symbolUniverseRefreshActive(universeState.symbolUniverseRefresh)) return;
  symbolUniverseRefreshPollTimer = setTimeout(function () {
    symbolUniverseRefreshPollTimer = null;
    loadSymbolUniverseRefreshStatus(true);
  }, Math.max(1000, Number(delayMs || 2000)));
}

function applySymbolUniverseRefreshStatus(payload, source) {
  var next = normalizedSymbolUniverseRefreshStatus(payload);
  var current = universeState.symbolUniverseRefresh || {};
  var previousStatus = String(current.status || "idle");
  var newJob = Boolean(next.jobId && next.jobId !== current.jobId);
  if (current.jobId && symbolUniverseRefreshActive(current)) {
    if (!next.jobId) return false;
    if (current.jobId !== next.jobId && source !== "websocket-request" && !next.superseded) return false;
  }
  if (newJob) {
    universeState.symbolUniverseRefreshDismissedJobId = "";
    universeState.symbolUniverseRefreshExpanded = true;
  }
  universeState.symbolUniverseRefresh = next;
  rememberSymbolUniverseRefreshJob(next);
  universeState.symbolUniverseRefreshing = symbolUniverseRefreshActive(next);
  universeState.symbolUniverseRefreshStatusLoaded = true;
  if (universeState.symbolUniverseRefreshing) {
    universeState.symbolUniverseError = "";
    scheduleSymbolUniverseRefreshPoll(2000);
    return true;
  }
  clearSymbolUniverseRefreshPoll();
  if (next.jobId && symbolUniverseRefreshTerminal(next)) universeState.symbolUniverseError = "";
  if (!symbolUniverseRefreshTerminal(next) || !next.jobId) return true;
  if (symbolUniverseRefreshActive(current) || previousStatus !== next.status) {
    universeState.symbolUniverseRefreshExpanded = true;
    if (next.status === "completed") {
      scheduleSymbolUniverseRefreshCollapse();
    } else if (symbolUniverseRefreshCollapseTimerCell.value) {
      clearTimeout(symbolUniverseRefreshCollapseTimerCell.value);
      symbolUniverseRefreshCollapseTimerCell.value = null;
    }
  }
  if (next.status === "failed") {
    universeState.symbolUniverseRefreshBaseline = {};
    universeState.symbolUniverseRefreshStableOrder = [];
    universeState.symbolUniverseRefreshContext = null;
  }
  if (next.jobId !== symbolUniverseRefreshNotifiedJobId && source === "poll") {
    var notice = symbolUniverseRefreshCompletionMessage(next);
    if (notice) showSnackbar(notice.message, notice.tone, { label: "결과 보기", name: "open-symbol-universe-refresh" });
    symbolUniverseRefreshNotifiedJobId = next.jobId;
  } else if (source === "websocket") {
    symbolUniverseRefreshNotifiedJobId = next.jobId;
  }
  if (next.jobId !== symbolUniverseRefreshLoadedJobId && source === "poll") {
    symbolUniverseRefreshLoadedJobId = next.jobId;
    loadSymbolUniverse();
  } else if (source === "websocket") {
    symbolUniverseRefreshLoadedJobId = next.jobId;
  }
  return true;
}

function loadSymbolUniverseRefreshStatus(force) {
  if (isStaticPreviewHost()) {
    universeState.symbolUniverseRefreshStatusLoaded = true;
    return Promise.resolve(null);
  }
  if (universeState.symbolUniverseRefreshStatusLoading && !force) return Promise.resolve(universeState.symbolUniverseRefresh);
  universeState.symbolUniverseRefreshStatusLoading = true;
  if (shellState.snapshot) render();
  var jobId = String((universeState.symbolUniverseRefresh || {}).jobId || "");
  var path = "/api/symbol-universe/refresh/status" + (jobId ? "?jobId=" + encodeURIComponent(jobId) : "");
  return requestJson(path, { key: "symbol-universe-refresh-status", timeoutMs: 8000, force: true, silent: true })
    .then(function (payload) {
      applySymbolUniverseRefreshStatus(payload, force ? "poll" : "initial");
      return payload;
    })
    .catch(function (error) {
      if (symbolUniverseRefreshActive(universeState.symbolUniverseRefresh)) {
        universeState.symbolUniverseRefresh = Object.assign({}, universeState.symbolUniverseRefresh, {
          pollError: error.message || "갱신 상태를 확인하지 못했습니다."
        });
        scheduleSymbolUniverseRefreshPoll(4000);
      }
      return null;
    })
    .finally(function () {
      universeState.symbolUniverseRefreshStatusLoading = false;
      if (shellState.snapshot) render();
    });
}

function refreshSymbolUniverse(requestedMarkets) {
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    universeState.symbolUniverseError = "공유 모드에서는 종목 유니버스를 갱신할 수 없습니다.";
    showSnackbar(universeState.symbolUniverseError, "danger");
    render();
    return Promise.resolve();
  }
  universeState.symbolUniverseRefreshing = true;
  universeState.symbolUniverseError = "";
  var markets = Array.isArray(requestedMarkets) && requestedMarkets.length
    ? requestedMarkets
    : (universeState.symbolUniverseMarket ? [universeState.symbolUniverseMarket] : ["KOSPI", "KOSDAQ", "NASDAQ"]);
  snapshotSymbolUniverseRefreshContext();
  universeState.symbolUniverseRefreshDismissedJobId = "";
  universeState.symbolUniverseRefreshExpanded = true;
  universeState.symbolUniverseRefresh = normalizedSymbolUniverseRefreshStatus({
    status: "submitting",
    running: true,
    markets: markets,
    completedMarkets: [],
    completedCount: 0,
    totalCount: markets.length,
    progressPercent: 0
  });
  render();
  return sendJson("/api/symbol-universe/refresh", "POST", { markets: markets }, { timeoutMs: 10000 })
    .then(function (payload) {
      applySymbolUniverseRefreshStatus(payload, "request");
      showSnackbar(payload.coalesced ? "진행 중인 목록 갱신에 요청을 합쳤습니다." : "목록 갱신을 접수했습니다. 화면을 벗어나도 계속 처리됩니다.");
      scheduleSymbolUniverseRefreshPoll(1200);
      return payload;
    })
    .catch(function (error) {
      universeState.symbolUniverseRefreshing = false;
      universeState.symbolUniverseRefresh = normalizedSymbolUniverseRefreshStatus({ status: "failed", lastError: error.message || "" });
      universeState.symbolUniverseError = error.message || "종목 유니버스를 갱신하지 못했습니다.";
      showSnackbar(universeState.symbolUniverseError, "danger");
    })
    .finally(function () {
      render();
    });
}

export { applySymbolUniverseRefreshStatus, failedSymbolUniverseRefreshMarkets, friendlySymbolUniverseRefreshError, loadSymbolUniverse, loadSymbolUniverseRefreshStatus, openSymbolUniverseRefreshResult, refreshSymbolUniverse, symbolUniverseRefreshActive, symbolUniverseRefreshStageMeta, symbolUniverseRefreshTerminal };
