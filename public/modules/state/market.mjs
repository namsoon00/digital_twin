import { initialFeedSection, initialMarketWorkspaceMode } from "../navigation/routes.mjs";

const marketState = {};

function initializeMarketState(cachedSnapshot) {
  return {
    marketReadModel: null,
    marketReadModelLoading: false,
    marketReadModelError: "",
    consoleMarketSearch: "",
    consoleMarketScope: "all",
    marketWorkspaceMode: initialMarketWorkspaceMode(),
    activeFeedSection: initialFeedSection()
  };
}

export { initializeMarketState, marketState };
