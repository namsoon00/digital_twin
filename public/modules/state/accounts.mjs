import { defaultAccountDraft } from "../accounts/commands.mjs";
import { initialAccountSection } from "../navigation/routes.mjs";

const accountsState = {};

function initializeAccountsState(cachedSnapshot) {
  return {
    activeAccountSection: initialAccountSection(),
    serviceAccounts: [],
    serviceAccountsLoading: false,
    serviceAccountsLoaded: false,
    serviceAccountsError: "",
    accountDraft: defaultAccountDraft(),
    editingAccountId: "",
    accountSaved: false,
    activeWatchAccountId: "",
    editingWatchAccountId: "",
    editingWatchSymbol: "",
    watchlistSavingAccountId: "",
    watchlistError: "",
    watchSuggestQuery: "",
    watchSuggestItems: [],
    watchSuggestLoading: false,
    watchSuggestError: "",
    watchlistAccountPickerSymbol: ""
  };
}

export { accountsState, initializeAccountsState };
