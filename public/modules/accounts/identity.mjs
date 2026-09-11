import { activeOntologyAccountId } from "../ontology/requests.mjs";
import { latestChangedFirst } from "../shared/format.mjs";
import { accountsState } from "../state/accounts.mjs";

function consoleReadModelAccountId() {
  return activeOntologyAccountId() || "default";
}

function serviceAccounts() {
  return latestChangedFirst(Array.isArray(accountsState.serviceAccounts) ? accountsState.serviceAccounts : []);
}

function enabledServiceAccounts() {
  return serviceAccounts().filter(function (account) {
    return account && account.enabled !== false;
  });
}

export { consoleReadModelAccountId, enabledServiceAccounts, serviceAccounts };
