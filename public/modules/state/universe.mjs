import { DEFAULT_SYMBOL_UNIVERSE_LIMIT } from "../instruments/constants.mjs";
import { loadSymbolUniverseRefreshHistory, readPersistentPayload } from "./storage.mjs";

const universeState = {};

function initializeUniverseState(cachedSnapshot) {
  return {
    symbolUniverse: { items: [], summary: { markets: [], sources: [], total: 0, maxAgeHours: 24 } },
    symbolUniverseLoading: false,
    symbolUniverseRefreshing: false,
    symbolUniverseRefresh: { status: "idle", running: false, jobId: "", completedCount: 0, totalCount: 0, progressPercent: 0 },
    symbolUniverseRefreshHistory: loadSymbolUniverseRefreshHistory(),
    symbolUniverseRefreshExpanded: true,
    symbolUniverseRefreshDismissedJobId: "",
    symbolUniverseRefreshAcknowledgedJobId: readPersistentPayload("orbitAlphaSymbolRefreshAcknowledged", ""),
    symbolUniverseRefreshBaseline: {},
    symbolUniverseRefreshStableOrder: [],
    symbolUniverseRefreshContext: null,
    symbolUniverseChangedKeys: {},
    symbolUniverseRefreshStatusLoading: false,
    symbolUniverseRefreshStatusLoaded: false,
    symbolUniverseLoaded: false,
    symbolUniverseError: "",
    symbolUniverseQuery: "",
    symbolUniverseMarket: "",
    symbolUniverseOffset: 0,
    symbolUniverseLimit: DEFAULT_SYMBOL_UNIVERSE_LIMIT,
    activeSymbolUniverseKey: ""
  };
}

export { initializeUniverseState, universeState };
