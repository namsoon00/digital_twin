import { initialOperationsView } from "../navigation/routes.mjs";

const operationsState = {};

function initializeOperationsState(cachedSnapshot) {
  return {
    operationsHealth: null,
    operationsHealthLoading: false,
    operationsHealthError: "",
    activeOperationsView: initialOperationsView()
  };
}

export { initializeOperationsState, operationsState };
