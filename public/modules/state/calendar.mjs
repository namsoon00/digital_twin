import { defaultInvestmentCalendarDraft } from "../calendar/commands.mjs";

const calendarState = {};

function initializeCalendarState(cachedSnapshot) {
  return {
    investmentCalendar: null,
    investmentCalendarLoading: false,
    investmentCalendarError: "",
    investmentCalendarSaving: false,
    investmentCalendarDeleting: "",
    investmentCalendarRunning: false,
    investmentCalendarSyncing: false,
    investmentCalendarDiscovering: false,
    investmentCalendarDiscoveryResult: null,
    investmentCalendarResearching: false,
    investmentCalendarResearchResult: null,
    investmentCalendarCandidates: null,
    investmentCalendarCandidatesLoading: false,
    investmentCalendarCandidateReviewing: "",
    investmentCalendarCandidateConfirmation: null,
    investmentCalendarCandidatePage: 0,
    investmentCalendarFilters: { symbol: "", eventType: "", limit: "80" },
    investmentCalendarMonthOffset: 0,
    investmentCalendarFocusedDayKey: "",
    investmentCalendarDraft: defaultInvestmentCalendarDraft(),
    calendarEntryModalOpen: false
  };
}

export { calendarState, initializeCalendarState };
