const hypothesesState = {};

function initializeHypothesesState(cachedSnapshot) {
  return {
    hypothesisDevelopment: null,
    hypothesisDevelopmentLoading: false,
    hypothesisDevelopmentLoaded: false,
    hypothesisDevelopmentError: "",
    hypothesisDevelopmentAction: "",
    activeHypothesisDevelopmentCaseId: "",
    hypothesisWorkspace: null,
    hypothesisWorkspaceLoading: false,
    hypothesisWorkspaceLoaded: false,
    hypothesisWorkspaceError: "",
    hypothesisWorkspaceDetails: {},
    hypothesisWorkspaceDetailLoading: {},
    hypothesisWorkspaceDetailErrors: {},
    activeHypothesisLifecycleKey: "",
    hypothesisPolicySaving: "",
    hypothesisPolicyPreview: {},
    hypothesisPolicyVersions: null,
    hypothesisPolicyVersionsLoading: false,
    hypothesisPolicyVersionsError: "",
    hypothesisReplay: null,
    hypothesisReplayLoading: false,
    hypothesisQualityReviewAction: false
  };
}

export { hypothesesState, initializeHypothesesState };
