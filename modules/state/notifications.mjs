import { initialNotificationSection } from "../navigation/routes.mjs";

const notificationsState = {};

function initializeNotificationsState(cachedSnapshot) {
  return {
    notificationAiPromptRelease: {},
    notificationTemplates: [],
    notificationTemplateVariables: [],
    notificationTemplatesLoading: false,
    notificationTemplatesLoaded: false,
    notificationTemplatesError: "",
    notificationTemplatesSaved: false,
    notificationTemplateSending: "",
    notificationRules: [],
    notificationRuleConditionTypes: [],
    notificationRulesLoading: false,
    notificationRulesLoaded: false,
    notificationRulesError: "",
    notificationRulesSaved: false,
    notificationExpandedTypes: {},
    notificationExpandedGroups: {},
    activeNotificationJobKey: "",
    notificationJobSearch: "",
    notificationJobStatusFilter: "all",
    notificationJobTypeFilter: "all",
    notificationInboxFilter: "all",
    notificationInboxSummary: {},
    notificationJobsCursor: "",
    notificationJobsNextCursor: "",
    activeNotificationSection: initialNotificationSection(),
    activeNotificationMessageType: "investmentInsight",
    notificationPolicyEditorOpen: false,
    activeNotificationTemplateType: "investmentInsight",
    notificationTemplateEditorOpen: false,
    notificationMarketHoursSessions: [],
    notificationJobItems: [],
    notificationJobsLoading: false,
    notificationJobsLoaded: false,
    notificationJobsError: "",
    notificationJobsSummary: {},
    notificationJobDiagnostics: {},
    notificationJobDetails: {},
    notificationJobDetailSections: {},
    notificationJobDetailTabs: {},
    notificationDetailDisclosureOpen: {},
    notificationJobsTotal: 0,
    notificationJobsOffset: 0,
    notificationJobsPageSize: 20,
    notificationExpandedJobs: {},
    messageSchedules: [],
    messageSchedulesLoading: false,
    messageSchedulesError: "",
    messageSchedulesLoaded: false
  };
}

export { initializeNotificationsState, notificationsState };
