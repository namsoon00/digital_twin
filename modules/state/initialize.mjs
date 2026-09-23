import { accountsState, initializeAccountsState } from "./accounts.mjs";
import { calendarState, initializeCalendarState } from "./calendar.mjs";
import { decisionsState, initializeDecisionsState } from "./decisions.mjs";
import { experimentsState, initializeExperimentsState } from "./experiments.mjs";
import { hypothesesState, initializeHypothesesState } from "./hypotheses.mjs";
import { initializeInstrumentsState, instrumentsState } from "./instruments.mjs";
import { initializeMarketState, marketState } from "./market.mjs";
import { initializeNavigationState, navigationState } from "./navigation.mjs";
import { initializeNotificationsState, notificationsState } from "./notifications.mjs";
import { initializeOntologyState, ontologyState } from "./ontology.mjs";
import { initializeOperationsState, operationsState } from "./operations.mjs";
import { initializePortfolioState, portfolioState } from "./portfolio.mjs";
import { initializeProposalsState, proposalsState } from "./proposals.mjs";
import { initializeResearchState, researchState } from "./research.mjs";
import { initializeSettingsState, settingsState } from "./settings.mjs";
import { initializeShellState, shellState } from "./shell.mjs";
import { loadCachedSnapshot } from "./storage.mjs";
import { initializeUniverseState, universeState } from "./universe.mjs";

function initializeState() {
  var cachedSnapshot = loadCachedSnapshot();
  var initial = [
    [shellState, initializeShellState(cachedSnapshot)],
    [marketState, initializeMarketState(cachedSnapshot)],
    [portfolioState, initializePortfolioState(cachedSnapshot)],
    [operationsState, initializeOperationsState(cachedSnapshot)],
    [researchState, initializeResearchState(cachedSnapshot)],
    [calendarState, initializeCalendarState(cachedSnapshot)],
    [navigationState, initializeNavigationState(cachedSnapshot)],
    [settingsState, initializeSettingsState(cachedSnapshot)],
    [notificationsState, initializeNotificationsState(cachedSnapshot)],
    [decisionsState, initializeDecisionsState(cachedSnapshot)],
    [accountsState, initializeAccountsState(cachedSnapshot)],
    [ontologyState, initializeOntologyState(cachedSnapshot)],
    [experimentsState, initializeExperimentsState(cachedSnapshot)],
    [hypothesesState, initializeHypothesesState(cachedSnapshot)],
    [proposalsState, initializeProposalsState(cachedSnapshot)],
    [universeState, initializeUniverseState(cachedSnapshot)],
    [instrumentsState, initializeInstrumentsState(cachedSnapshot)]
  ];
  initial.forEach(function (entry) { Object.assign(entry[0], entry[1]); });
}

export { initializeState };
