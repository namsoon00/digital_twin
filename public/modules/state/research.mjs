import { DEFAULT_RESEARCH_EVIDENCE_LIMIT } from "../research/constants.mjs";

const researchState = {};

function initializeResearchState(cachedSnapshot) {
  return {
    researchEvidence: null,
    researchEvidenceLoading: false,
    researchEvidenceError: "",
    researchEvidenceDetails: {},
    researchEvidenceFilters: { symbol: "", kind: "", limit: DEFAULT_RESEARCH_EVIDENCE_LIMIT },
    researchEvidenceDeleting: "",
    expandedFeedDetail: "",
    expandedResearchEvidenceKey: ""
  };
}

export { initializeResearchState, researchState };
