import { initialPageViewModes, initialTab, initialWorkDetailLayer } from "../navigation/routes.mjs";

const navigationState = {};

function initializeNavigationState(cachedSnapshot) {
  return {
    activeTab: initialTab(),
    previousTab: "",
    tabBarScrollLeft: 0,
    tabScrollPositions: {},
    pageViewModes: initialPageViewModes(),
    commandPaletteOpen: false,
    commandPaletteQuery: "",
    commandPaletteMode: "search",
    monitoringDetail: null,
    workDetailLayer: initialWorkDetailLayer()
  };
}

export { initializeNavigationState, navigationState };
