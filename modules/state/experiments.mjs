import { initialExperimentSection, initialOntologyExperimentId } from "../navigation/routes.mjs";

const experimentsState = {};

function initializeExperimentsState(cachedSnapshot) {
  return {
    ontologyExperiments: null,
    ontologyExperimentsLoading: false,
    ontologyExperimentsLoaded: false,
    ontologyExperimentsError: "",
    ontologyExperimentAction: "",
    ontologyExperimentRecommendationSelections: {},
    activeExperimentSection: initialExperimentSection(),
    activeOntologyExperimentId: initialOntologyExperimentId()
  };
}

export { experimentsState, initializeExperimentsState };
