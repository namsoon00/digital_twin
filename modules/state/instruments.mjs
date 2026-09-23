const instrumentsState = {};

function initializeInstrumentsState(cachedSnapshot) {
  return {
    instrumentTimelines: {},
    instrumentTimelineLoading: {},
    instrumentTimelineErrors: {},
    instrumentTimelineLastKeys: {},
    instrumentValuations: {},
    instrumentValuationLoading: {},
    instrumentValuationErrors: {},
    instrumentWorkspaceTabs: {},
    instrumentTimelineRanges: {}
  };
}

export { initializeInstrumentsState, instrumentsState };
