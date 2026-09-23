import { initialStrategyProposalSection } from "../navigation/routes.mjs";

const proposalsState = {};

function initializeProposalsState(cachedSnapshot) {
  return {
    strategyProposals: null,
    strategyProposalsLoading: false,
    strategyProposalsLoaded: false,
    strategyProposalsDetailLevel: "",
    strategyProposalsError: "",
    strategyProposalAction: "",
    activeStrategyProposalId: "",
    activeStrategyProposalSection: initialStrategyProposalSection()
  };
}

export { initializeProposalsState, proposalsState };
