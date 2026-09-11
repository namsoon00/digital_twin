import { initialStrategySection } from "../navigation/routes.mjs";

const decisionsState = {};

function initializeDecisionsState(cachedSnapshot) {
  return {
    consoleDecisionSearch: "",
    consoleDecisionScope: "all",
    consoleDecisionAction: "all",
    consoleDecisionQuality: "all",
    consoleDecisionStatus: "all",
    consoleDecisionView: "attention",
    investmentFlow: null,
    investmentFlowLoading: false,
    investmentFlowLoaded: false,
    investmentFlowAccountId: "",
    investmentFlowError: "",
    investmentFlowDetails: {},
    investmentFlowDetailLoading: {},
    investmentFlowDetailErrors: {},
    investmentCaseDetailTabs: {},
    investmentCaseHistories: {},
    investmentCaseHistoryLoading: {},
    investmentCaseHistoryErrors: {},
    investmentCaseTraces: {},
    investmentCaseTraceLoading: {},
    investmentCaseTraceErrors: {},
    investmentModel: null,
    investmentModelLoading: false,
    investmentModelLoaded: false,
    investmentModelError: "",
    investmentModelPollCount: 0,
    investmentModelManagementTab: "release",
    activeStrategySection: initialStrategySection(),
    activeInvestmentChartPeriod: "1d",
    activeInvestmentEvidenceKey: "",
    expandedInvestmentActionKey: "",
    investmentActionQuery: "",
    investmentActionPage: 1,
    investmentActionPageSize: 6,
    activeInvestmentLanguageTermId: ""
  };
}

export { decisionsState, initializeDecisionsState };
