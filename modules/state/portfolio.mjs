import { initialPortfolioView } from "../navigation/routes.mjs";

const portfolioState = {};

function initializePortfolioState(cachedSnapshot) {
  return {
    portfolioReadModels: {},
    portfolioReadModelLoading: false,
    portfolioReadModelError: "",
    portfolioReadModelLoadingByView: {},
    portfolioReadModelErrorsByView: {},
    portfolioInterpretation: null,
    portfolioInterpretationLoading: false,
    portfolioInterpretationError: "",
    activePortfolioView: initialPortfolioView(),
    portfolioLifecycle: null,
    portfolioLifecycleLoading: false,
    portfolioLifecycleError: "",
    portfolioLifecycleSaving: false
  };
}

export { initializePortfolioState, portfolioState };
