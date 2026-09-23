const shellState = {};

function initializeShellState(cachedSnapshot) {
  return {
    loading: !cachedSnapshot,
    refreshing: false,
    readModel: cachedSnapshot && cachedSnapshot.readModel ? cachedSnapshot.readModel : {},
    error: "",
    snapshot: cachedSnapshot,
    snapshotFromCache: Boolean(cachedSnapshot),
    dashboardSummary: null,
    dashboardSummaryLoading: false,
    dashboardSummaryError: "",
    snackbar: null,
    realtime: {
    supported: typeof window.WebSocket !== "undefined",
    connected: false,
    lastEvent: "",
    lastEventAt: "",
    reconnects: 0,
    eventCounts: {},
    latestEvents: [],
    monitoring: {},
    notificationJobs: {}
  },
    consolePages: { today: 1, market: 1, decision: 1, alerts: 1, validation: 1 },
    staticBuildConfig: null,
    staticBuildConfigError: ""
  };
}

export { initializeShellState, shellState };
