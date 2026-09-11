import { timestampAgeMinutes } from "../accounts/balance.mjs";
import { allAccountWatchlistSymbols } from "../accounts/watchlist.mjs";
import { watchlistSymbols } from "../instruments/catalog.mjs";
import { primeActiveTabData, scheduleTabDataPreload } from "../navigation/preload.mjs";
import { snapshotHasFullOntologyDetail } from "../ontology/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { settingValue } from "../settings/fields.mjs";
import { isStaticPreviewHost, staticPreviewSnapshot } from "../shell/static-preview.mjs";
import { marketState } from "../state/market.mjs";
import { navigationState } from "../state/navigation.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { operationsState } from "../state/operations.mjs";
import { portfolioState } from "../state/portfolio.mjs";
import { shellState } from "../state/shell.mjs";
import { writeCachedSnapshot } from "../state/storage.mjs";

var snapshotPollTimer = null;

var snapshotLoadPromise = null;

var snapshotLastCheckedAt = 0;

var snapshotPollAttempts = 0;

var SNAPSHOT_RESUME_CHECK_INTERVAL_MS = 60000;

var SNAPSHOT_REFRESH_POLL_LIMIT = 18;

function tossLensPath(options) {
  options = options || {};
  var params = new URLSearchParams();
  params.set("detail", options.detail || "summary");
  var pageParams = new URLSearchParams(window.location.search || "");
  var mockMode = String(pageParams.get("mock") || "").toLowerCase();
  if (["1", "true", "mock"].indexOf(mockMode) >= 0) params.set("mock", "1");
  var symbols = allAccountWatchlistSymbols();
  if (!symbols.length) symbols = watchlistSymbols();
  if (symbols.length) params.set("watchlistSymbols", symbols.join(","));
  if (options.refresh) params.set("refresh", "1");
  var query = params.toString();
  return "/api/flow-lens" + (query ? "?" + query : "");
}

function load(options) {
  options = options || {};
  if (snapshotLoadPromise) return snapshotLoadPromise;
  shellState.loading = !shellState.snapshot;
  shellState.refreshing = Boolean(shellState.snapshot);
  shellState.error = "";
  render();

  var loadPromise = isStaticPreviewHost()
    ? Promise.resolve(staticPreviewSnapshot())
    : requestJson(tossLensPath(options), { key: "flow-lens", timeoutMs: 8000, force: true });

  snapshotLoadPromise = loadPromise
    .then(function (snapshot) {
      var readModel = snapshot && snapshot.readModel && typeof snapshot.readModel === "object" ? snapshot.readModel : {};
      var previousGeneratedAt = String((shellState.snapshot || {}).generatedAt || "");
      snapshotLastCheckedAt = Date.now();
      shellState.readModel = readModel;
      if (!readModel.ready && readModel.status === "pending") {
        shellState.error = readModel.error || "";
        scheduleSnapshotPoll();
        return snapshot;
      }
      shellState.snapshot = snapshot;
      shellState.snapshotFromCache = false;
      ontologyState.ontologyStrategyDetailLoaded = snapshotHasFullOntologyDetail(snapshot);
      ontologyState.ontologyStrategyDetailError = "";
      shellState.error = readModel.error || "";
      writeCachedSnapshot(snapshot);
      var snapshotChanged = Boolean(previousGeneratedAt && previousGeneratedAt !== String(snapshot.generatedAt || ""));
      if (snapshotChanged || (options.refresh && !snapshotRefreshInProgress(readModel))) {
        shellState.dashboardSummary = null;
        marketState.marketReadModel = null;
        portfolioState.portfolioReadModels = {};
        operationsState.operationsHealth = null;
      }
      var refreshReadModels = snapshotChanged || Boolean(options.refresh && !snapshotRefreshInProgress(readModel));
      primeActiveTabData(navigationState.activeTab, refreshReadModels);
      scheduleTabDataPreload({
        force: refreshReadModels,
        reason: options.reason || "snapshot-ready"
      });
      if (snapshotRefreshInProgress(readModel)) {
        scheduleSnapshotPoll();
      } else {
        snapshotPollAttempts = 0;
      }
      return snapshot;
    })
    .catch(function (error) {
      shellState.error = error.message;
      if (snapshotRefreshInProgress(shellState.readModel)) scheduleSnapshotPoll();
    })
    .finally(function () {
      shellState.loading = Boolean(!shellState.snapshot && shellState.readModel && shellState.readModel.status === "pending");
      shellState.refreshing = snapshotRefreshInProgress(shellState.readModel);
      snapshotLoadPromise = null;
      render();
    });
  return snapshotLoadPromise;
}

function snapshotRefreshInProgress(readModel) {
  var model = readModel && typeof readModel === "object" ? readModel : {};
  return Boolean(model.refreshing || model.status === "pending");
}

function snapshotFreshnessExpired(snapshot) {
  snapshot = snapshot || {};
  if (!snapshot.generatedAt) return true;
  var age = timestampAgeMinutes(snapshot.generatedAt);
  var freshness = snapshot.dataFreshness && typeof snapshot.dataFreshness === "object" ? snapshot.dataFreshness : {};
  var maxAge = Math.max(1, Number(freshness.maxAgeMinutes || settingValue("marketDataMaxAgeMinutes") || settingValue("dataFreshnessDefaultMaxAgeMinutes") || 30));
  return freshness.status === "stale" || age == null || age > maxAge;
}

function ensureFreshSnapshot(reason, force) {
  if (isStaticPreviewHost()) {
    return snapshotLastCheckedAt ? Promise.resolve(shellState.snapshot) : load({ reason: reason || "static-entry" });
  }
  if (typeof navigator !== "undefined" && navigator.onLine === false) return Promise.resolve(shellState.snapshot);
  if (typeof document !== "undefined" && document.visibilityState === "hidden") return Promise.resolve(shellState.snapshot);
  var now = Date.now();
  var recentlyChecked = snapshotLastCheckedAt && now - snapshotLastCheckedAt < SNAPSHOT_RESUME_CHECK_INTERVAL_MS;
  var shouldForce = Boolean(force || shellState.snapshotFromCache || (shellState.snapshot && snapshotFreshnessExpired(shellState.snapshot)));
  if (shellState.snapshot && recentlyChecked && !shouldForce && !snapshotRefreshInProgress(shellState.readModel)) {
    scheduleTabDataPreload({ reason: reason || "fresh-snapshot" });
    return Promise.resolve(shellState.snapshot);
  }
  return load({ refresh: shouldForce, background: Boolean(shellState.snapshot), reason: reason || "automatic" });
}

function scheduleSnapshotPoll() {
  if (snapshotPollTimer || isStaticPreviewHost()) return;
  if (snapshotPollAttempts >= SNAPSHOT_REFRESH_POLL_LIMIT) {
    shellState.refreshing = false;
    shellState.error = shellState.error || "최신 데이터 갱신이 지연되고 있습니다. 직전 데이터를 유지합니다.";
    render();
    return;
  }
  var delay = Math.min(5000, 1400 + snapshotPollAttempts * 250);
  snapshotPollTimer = setTimeout(function () {
    snapshotPollTimer = null;
    if (!snapshotRefreshInProgress(shellState.readModel)) {
      snapshotPollAttempts = 0;
      return;
    }
    snapshotPollAttempts += 1;
    requestJson(tossLensPath({ detail: "status" }), {
      key: "flow-lens-status",
      timeoutMs: 5000,
      force: true,
      silent: true
    }).then(function (status) {
      var readModel = status && status.readModel && typeof status.readModel === "object" ? status.readModel : {};
      snapshotLastCheckedAt = Date.now();
      shellState.readModel = readModel;
      if (snapshotRefreshInProgress(readModel)) {
        shellState.refreshing = true;
        scheduleSnapshotPoll();
        return null;
      }
      if (readModel.error) {
        shellState.refreshing = false;
        shellState.error = readModel.error;
        snapshotPollAttempts = 0;
        render();
        return null;
      }
      snapshotPollAttempts = 0;
      return load({ background: true, reason: "refresh-complete" });
    }).catch(function (error) {
      if (snapshotPollAttempts >= SNAPSHOT_REFRESH_POLL_LIMIT) {
        shellState.refreshing = false;
        shellState.error = error.message || "최신 데이터 확인이 지연되고 있습니다.";
        render();
        return;
      }
      scheduleSnapshotPoll();
    });
  }, delay);
}

export { ensureFreshSnapshot, load, snapshotRefreshInProgress, tossLensPath };
