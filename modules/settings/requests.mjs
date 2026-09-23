import { loadNotificationSchedules } from "../notifications/requests.mjs";
import { loadOntologyRulebox } from "../ontology/requests.mjs";
import { requestJson } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { settingValue } from "./fields.mjs";
import { syncModelAlertThresholdSettings } from "./formulas.mjs";
import { applyServerSettings, persistSettings } from "./storage.mjs";
import { applyStaticBuildSettings, isStaticPreviewHost, loadStaticBuildConfig } from "../shell/static-preview.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { settingsState } from "../state/settings.mjs";

function loadServerSettings() {
  if (isStaticPreviewHost()) {
    return loadStaticBuildConfig().then(function (payload) {
      applyStaticBuildSettings(payload);
    });
  }
  return requestJson("/api/settings")
    .then(function (payload) {
      applyServerSettings(payload);
    })
    .catch(function (error) {
      settingsState.serverSettingsError = error.message || "서버 설정을 읽지 못했습니다.";
    });
}

function serverSettingsPayload() {
  syncModelAlertThresholdSettings();
  return {
    appTheme: settingValue("appTheme"),
    appTimezone: settingValue("appTimezone"),
    investmentCalendarCandidateDefaultTime: settingValue("investmentCalendarCandidateDefaultTime"),
    watchlistSymbols: settingValue("watchlistSymbols"),
    tossApiBaseUrl: settingValue("tossApiBaseUrl"),
    tossClientId: settingValue("tossClientId"),
    tossClientSecret: settingValue("tossClientSecret"),
    tossAccountSeq: settingValue("tossAccountSeq"),
    kisBaseUrl: settingValue("kisBaseUrl"),
    kisAppKey: settingValue("kisAppKey"),
    kisAppSecret: settingValue("kisAppSecret"),
    kisMarketSignalsEnabled: settingValue("kisMarketSignalsEnabled"),
    kisMarketSignalMaxSymbols: settingValue("kisMarketSignalMaxSymbols"),
    kisMarketSignalCacheMinutes: settingValue("kisMarketSignalCacheMinutes"),
    kisMarketSignalGapSeconds: settingValue("kisMarketSignalGapSeconds"),
    kisMarketSignalPreferLiveDuringMarketHours: settingValue("kisMarketSignalPreferLiveDuringMarketHours"),
    kisMarketSignalLiveRefreshSeconds: settingValue("kisMarketSignalLiveRefreshSeconds"),
    kisMarketSignalUnchangedStaleCount: settingValue("kisMarketSignalUnchangedStaleCount"),
    kisInvestorIntradayEstimateEnabled: settingValue("kisInvestorIntradayEstimateEnabled"),
    notifyProvider: settingValue("notifyProvider"),
    telegramBotToken: settingValue("telegramBotToken"),
    telegramChatId: settingValue("telegramChatId"),
    operationsTelegramBotToken: settingValue("operationsTelegramBotToken"),
    operationsTelegramChatId: settingValue("operationsTelegramChatId"),
    notifyLinkUrl: settingValue("notifyLinkUrl"),
    operatorReasoningReportEnabled: settingValue("operatorReasoningReportEnabled"),
    symbolUniverseMaxAgeHours: settingValue("symbolUniverseMaxAgeHours"),
    marketDataMaxAgeMinutes: settingValue("marketDataMaxAgeMinutes"),
    dataFreshnessEnabled: settingValue("dataFreshnessEnabled"),
    dataFreshnessDefaultMaxAgeMinutes: settingValue("dataFreshnessDefaultMaxAgeMinutes"),
    dataFreshnessQuoteMaxAgeMinutes: settingValue("dataFreshnessQuoteMaxAgeMinutes"),
    dataFreshnessKisPriceMaxAgeMinutes: settingValue("dataFreshnessKisPriceMaxAgeMinutes"),
    dataFreshnessKisMicrostructureMaxAgeMinutes: settingValue("dataFreshnessKisMicrostructureMaxAgeMinutes"),
    dataFreshnessKisInvestorMaxAgeMinutes: settingValue("dataFreshnessKisInvestorMaxAgeMinutes"),
    dataFreshnessExternalMaxAgeMinutes: settingValue("dataFreshnessExternalMaxAgeMinutes"),
    dataFreshnessExternalEquityMaxAgeMinutes: settingValue("dataFreshnessExternalEquityMaxAgeMinutes"),
    dataFreshnessExternalCryptoMaxAgeMinutes: settingValue("dataFreshnessExternalCryptoMaxAgeMinutes"),
    dataFreshnessMacroMaxAgeMinutes: settingValue("dataFreshnessMacroMaxAgeMinutes"),
    dataFreshnessDisclosureMaxAgeMinutes: settingValue("dataFreshnessDisclosureMaxAgeMinutes"),
    externalApiFetchIntervalMinutes: settingValue("externalApiFetchIntervalMinutes"),
    externalSignalCacheMaxAgeMinutes: settingValue("externalSignalCacheMaxAgeMinutes"),
    externalAlphaEnabled: settingValue("externalAlphaEnabled"),
    externalPublicDataStockEnabled: settingValue("externalPublicDataStockEnabled"),
    externalPublicDataReferenceEnabled: settingValue("externalPublicDataReferenceEnabled"),
    externalPublicDataTimeoutSeconds: settingValue("externalPublicDataTimeoutSeconds"),
    externalDataPublicStockCadenceSeconds: settingValue("externalDataPublicStockCadenceSeconds"),
    externalDataPublicStockFreshnessSeconds: settingValue("externalDataPublicStockFreshnessSeconds"),
    externalDataPublicStockMaxPartitions: settingValue("externalDataPublicStockMaxPartitions"),
    externalDataPublicReferenceCadenceSeconds: settingValue("externalDataPublicReferenceCadenceSeconds"),
    externalDataPublicReferenceFreshnessSeconds: settingValue("externalDataPublicReferenceFreshnessSeconds"),
    externalDataPublicReferenceMaxPartitions: settingValue("externalDataPublicReferenceMaxPartitions"),
    externalAlphaRelatedSymbolsEnabled: settingValue("externalAlphaRelatedSymbolsEnabled"),
    externalAlphaRelatedMaxSymbols: settingValue("externalAlphaRelatedMaxSymbols"),
    externalYFinanceEnabled: settingValue("externalYFinanceEnabled"),
    externalYFinanceMaxSymbols: settingValue("externalYFinanceMaxSymbols"),
    externalYFinanceHistoryPeriod: settingValue("externalYFinanceHistoryPeriod"),
    externalYFinanceHistoryInterval: settingValue("externalYFinanceHistoryInterval"),
    externalYFinanceHistoryRows: settingValue("externalYFinanceHistoryRows"),
    externalYFinanceFinancialPeriods: settingValue("externalYFinanceFinancialPeriods"),
    externalYFinanceTabularRows: settingValue("externalYFinanceTabularRows"),
    externalYFinanceOptionExpirations: settingValue("externalYFinanceOptionExpirations"),
    externalYFinanceOptionsMaxRows: settingValue("externalYFinanceOptionsMaxRows"),
    externalYFinanceEarningsLimit: settingValue("externalYFinanceEarningsLimit"),
    externalYFinanceNewsLimit: settingValue("externalYFinanceNewsLimit"),
    externalYFinancePriceMaxAgeMinutes: settingValue("externalYFinancePriceMaxAgeMinutes"),
    externalYFinanceOptionsMaxAgeMinutes: settingValue("externalYFinanceOptionsMaxAgeMinutes"),
    externalYFinanceNewsMaxAgeMinutes: settingValue("externalYFinanceNewsMaxAgeMinutes"),
    externalYFinanceAnalystMaxAgeMinutes: settingValue("externalYFinanceAnalystMaxAgeMinutes"),
    externalYFinanceFundamentalMaxAgeMinutes: settingValue("externalYFinanceFundamentalMaxAgeMinutes"),
    externalCoinGeckoEnabled: settingValue("externalCoinGeckoEnabled"),
    externalFredEnabled: settingValue("externalFredEnabled"),
    externalFredSeries: settingValue("externalFredSeries"),
    externalCryptoIds: settingValue("externalCryptoIds"),
    externalAlphaMaxSymbols: settingValue("externalAlphaMaxSymbols"),
    securityLineMappings: settingValue("securityLineMappings"),
    externalSecEnabled: settingValue("externalSecEnabled"),
    externalSecMaxSymbols: settingValue("externalSecMaxSymbols"),
    externalSecCompanyCiks: settingValue("externalSecCompanyCiks"),
    externalSecUserAgent: settingValue("externalSecUserAgent"),
    externalDartEnabled: settingValue("externalDartEnabled"),
    externalDartLookbackDays: settingValue("externalDartLookbackDays"),
    externalNewsEnabled: settingValue("externalNewsEnabled"),
    externalNewsProvider: settingValue("externalNewsProvider"),
    externalNewsMaxSymbols: settingValue("externalNewsMaxSymbols"),
    externalNewsLookbackHours: settingValue("externalNewsLookbackHours"),
    externalResearchEvidenceMaxItems: settingValue("externalResearchEvidenceMaxItems"),
    newsCollectionEnabled: settingValue("newsCollectionEnabled"),
    newsCollectionIntervalSeconds: settingValue("newsCollectionIntervalSeconds"),
    newsCollectionMaxSymbols: settingValue("newsCollectionMaxSymbols"),
    newsCollectionLookbackMinutes: settingValue("newsCollectionLookbackMinutes"),
    newsCollectionPerSymbolLimit: settingValue("newsCollectionPerSymbolLimit"),
    newsCollectionProviders: settingValue("newsCollectionProviders"),
    newsCollectionQualityGateEnabled: settingValue("newsCollectionQualityGateEnabled"),
    newsCollectionMinimumRelevanceState: settingValue("newsCollectionMinimumRelevanceState"),
    newsCollectionMinimumMaterialityState: settingValue("newsCollectionMinimumMaterialityState"),
    newsCollectionMinimumSourceTrustState: settingValue("newsCollectionMinimumSourceTrustState"),
    newsCollectionRequireArticleBody: settingValue("newsCollectionRequireArticleBody"),
    newsDigestMinimumRelevanceState: settingValue("newsDigestMinimumRelevanceState"),
    newsDigestMinimumMaterialityState: settingValue("newsDigestMinimumMaterialityState"),
    newsDigestMinimumNeutralMaterialityState: settingValue("newsDigestMinimumNeutralMaterialityState"),
    newsDigestMinimumSourceTrustState: settingValue("newsDigestMinimumSourceTrustState"),
    newsCollectionIncludeWatchlist: settingValue("newsCollectionIncludeWatchlist"),
    newsCollectionIncludeHoldings: settingValue("newsCollectionIncludeHoldings"),
    newsCollectionRateLimitSeconds: settingValue("newsCollectionRateLimitSeconds"),
    newsEvidenceCleanupIntervalSeconds: settingValue("newsEvidenceCleanupIntervalSeconds"),
    newsEvidenceCleanupBatchSize: settingValue("newsEvidenceCleanupBatchSize"),
    researchEvidenceWriteBatchSize: settingValue("researchEvidenceWriteBatchSize"),
    mysqlDeadlockRetryCount: settingValue("mysqlDeadlockRetryCount"),
    newsAiAnalysisEnabled: settingValue("newsAiAnalysisEnabled"),
    newsAiAnalysisUseCodex: settingValue("newsAiAnalysisUseCodex"),
    newsAiAnalysisCommand: settingValue("newsAiAnalysisCommand"),
    newsAiAnalysisTimeoutSeconds: settingValue("newsAiAnalysisTimeoutSeconds"),
    ontologyReasoningEnabled: settingValue("ontologyReasoningEnabled"),
    ontologyReasoningIntervalSeconds: settingValue("ontologyReasoningIntervalSeconds"),
    ontologyReasoningBatchSize: settingValue("ontologyReasoningBatchSize"),
    ontologyReasoningMailboxEnabled: settingValue("ontologyReasoningMailboxEnabled"),
    ontologyReasoningMailboxBatchSize: settingValue("ontologyReasoningMailboxBatchSize"),
    ontologyReasoningMailboxRetentionHours: settingValue("ontologyReasoningMailboxRetentionHours"),
    ontologyReasoningSourceFreshnessEnabled: settingValue("ontologyReasoningSourceFreshnessEnabled"),
    ontologyReasoningRealtimeEventMaxAgeMinutes: settingValue("ontologyReasoningRealtimeEventMaxAgeMinutes"),
    ontologyReasoningResearchEventMaxAgeMinutes: settingValue("ontologyReasoningResearchEventMaxAgeMinutes"),
    ontologyReasoningTelemetryHistoryLimit: settingValue("ontologyReasoningTelemetryHistoryLimit"),
    ontologyReasoningQueueAlertEnabled: settingValue("ontologyReasoningQueueAlertEnabled"),
    ontologyReasoningQueueWarningAgeMinutes: settingValue("ontologyReasoningQueueWarningAgeMinutes"),
    ontologyReasoningQueueCriticalAgeMinutes: settingValue("ontologyReasoningQueueCriticalAgeMinutes"),
    ontologyReasoningQueueWarningPendingCount: settingValue("ontologyReasoningQueueWarningPendingCount"),
    ontologyReasoningQueueCriticalPendingCount: settingValue("ontologyReasoningQueueCriticalPendingCount"),
    ontologyReasoningQueueWarningOverdueSymbols: settingValue("ontologyReasoningQueueWarningOverdueSymbols"),
    ontologyReasoningQueueCriticalOverdueSymbols: settingValue("ontologyReasoningQueueCriticalOverdueSymbols"),
    ontologyReasoningQueueConsecutiveObservations: settingValue("ontologyReasoningQueueConsecutiveObservations"),
    ontologyReasoningQueueNoProgressMinutes: settingValue("ontologyReasoningQueueNoProgressMinutes"),
    ontologyReasoningQueueAlertReminderMinutes: settingValue("ontologyReasoningQueueAlertReminderMinutes"),
    temporalWindowPeriods: settingValue("temporalWindowPeriods"),
    temporalWindowHistoryLimit: settingValue("temporalWindowHistoryLimit"),
    ontologyLabAutoApplyEnabled: settingValue("ontologyLabAutoApplyEnabled"),
    ontologyLabAutoApplyNeedsReviewEnabled: settingValue("ontologyLabAutoApplyNeedsReviewEnabled"),
    ontologyLabNotifyEnabled: settingValue("ontologyLabNotifyEnabled"),
    ontologyRuleCandidateAiEnabled: settingValue("ontologyRuleCandidateAiEnabled"),
    ontologyRuleCandidateAiUseCodex: settingValue("ontologyRuleCandidateAiUseCodex"),
    ontologyRuleCandidateAiCommand: settingValue("ontologyRuleCandidateAiCommand"),
    ontologyRuleCandidateAiTimeoutSeconds: settingValue("ontologyRuleCandidateAiTimeoutSeconds"),
    ontologyRuleCandidateAiIntervalMinutes: settingValue("ontologyRuleCandidateAiIntervalMinutes"),
    ontologyRuleCandidateAiMaxCandidates: settingValue("ontologyRuleCandidateAiMaxCandidates"),
    ontologyTypeDbEnabled: settingValue("ontologyTypeDbEnabled"),
    ontologyTenantId: settingValue("ontologyTenantId"),
    ontologySharedMarketTenantId: settingValue("ontologySharedMarketTenantId"),
    ontologySharedMarketWorldRetentionHours: settingValue("ontologySharedMarketWorldRetentionHours"),
    ontologySharedMarketWorldMaxSymbols: settingValue("ontologySharedMarketWorldMaxSymbols"),
    ontologySharedMarketWorldAsyncProjectionEnabled: settingValue("ontologySharedMarketWorldAsyncProjectionEnabled"),
    typedbAddress: settingValue("typedbAddress"),
    typedbUser: settingValue("typedbUser"),
    typedbDatabase: settingValue("typedbDatabase"),
    typedbTlsEnabled: settingValue("typedbTlsEnabled"),
    typedbTimeoutSeconds: settingValue("typedbTimeoutSeconds"),
    typedbRetryCount: settingValue("typedbRetryCount"),
    typedbInferenceGenerationKeepCount: settingValue("typedbInferenceGenerationKeepCount"),
    typedbAutoResetEnabled: settingValue("typedbAutoResetEnabled"),
    typedbCapacityAutoRotateEnabled: settingValue("typedbCapacityAutoRotateEnabled"),
    typedbCapacityThrottlePercent: settingValue("typedbCapacityThrottlePercent"),
    typedbCapacityAutoRotatePercent: settingValue("typedbCapacityAutoRotatePercent"),
    typedbCapacityAutoRotateCooldownMinutes: settingValue("typedbCapacityAutoRotateCooldownMinutes"),
    typedbDataRetentionHours: settingValue("typedbDataRetentionHours"),
    typedbDataMaxSizeMb: settingValue("typedbDataMaxSizeMb"),
    materialityGateEnabled: settingValue("materialityGateEnabled"),
    marketMaterialityPriceChangePct: settingValue("marketMaterialityPriceChangePct"),
    marketMaterialityTrendDistancePct: settingValue("marketMaterialityTrendDistancePct"),
    marketMaterialityVolumeRatio: settingValue("marketMaterialityVolumeRatio"),
    marketMaterialityInvestorFlowRatioPct: settingValue("marketMaterialityInvestorFlowRatioPct"),
    externalDartCorpCodes: settingValue("externalDartCorpCodes"),
    dartDisclosureAiAnalysisEnabled: settingValue("dartDisclosureAiAnalysisEnabled"),
    dartDisclosureAiUseCodex: settingValue("dartDisclosureAiUseCodex"),
    dartDisclosureAiCommand: settingValue("dartDisclosureAiCommand"),
    dartDisclosureAiTimeoutSeconds: settingValue("dartDisclosureAiTimeoutSeconds"),
    alphaVantageApiKey: settingValue("alphaVantageApiKey"),
    coingeckoApiKey: settingValue("coingeckoApiKey"),
    fredApiKey: settingValue("fredApiKey"),
    opendartApiKey: settingValue("opendartApiKey"),
    fxRates: settingValue("fxRates"),
    portfolioValuationBasis: settingValue("portfolioValuationBasis"),
    valuationAssumptions: settingValue("valuationAssumptions"),
    aiValuationAutoProposalEnabled: settingValue("aiValuationAutoProposalEnabled"),
    aiValuationCurrentPriceAnchorEnabled: settingValue("aiValuationCurrentPriceAnchorEnabled"),
    aiValuationPreferredParValue: settingValue("aiValuationPreferredParValue"),
    aiValuationPreferredRiskSpreadPct: settingValue("aiValuationPreferredRiskSpreadPct"),
    aiValuationPreferredRequiredYieldPct: settingValue("aiValuationPreferredRequiredYieldPct"),
    aiValuationPreferredMinimumMarginPct: settingValue("aiValuationPreferredMinimumMarginPct"),
    aiValuationBaselineMinimumMarginPct: settingValue("aiValuationBaselineMinimumMarginPct"),
    marketSignalInputs: settingValue("marketSignalInputs"),
    fairValueFormula: settingValue("fairValueFormula"),
    ontologyRelationRules: settingValue("ontologyRelationRules"),
    aiPromptTemplates: settingValue("aiPromptTemplates"),
    aiPromptPolicy: settingValue("aiPromptPolicy"),
    notificationAiGateEnabled: settingValue("notificationAiGateEnabled"),
    notificationAiGateMessageTypes: settingValue("notificationAiGateMessageTypes"),
    notificationAiUseCodex: settingValue("notificationAiUseCodex"),
    notificationAiModel: settingValue("notificationAiModel"),
    notificationAiReasoningEffort: settingValue("notificationAiReasoningEffort"),
    notificationAiTimeoutSeconds: settingValue("notificationAiTimeoutSeconds"),
    notificationAiDeliveryDeadlineSeconds: settingValue("notificationAiDeliveryDeadlineSeconds"),
    notificationAiTypeDbFallbackEnabled: settingValue("notificationAiTypeDbFallbackEnabled"),
    notificationAiFallbackOnFirstFailure: settingValue("notificationAiFallbackOnFirstFailure"),
    notificationAiQueueWorkerCount: settingValue("notificationAiQueueWorkerCount"),
    localAiMaxConcurrentProcesses: settingValue("localAiMaxConcurrentProcesses"),
    localAiInvestmentReservedProcesses: settingValue("localAiInvestmentReservedProcesses"),
    notificationAiCapacityWaitSeconds: settingValue("notificationAiCapacityWaitSeconds"),
    modelName: settingValue("modelName"),
    modelHypothesis: settingValue("modelHypothesis"),
    alertRules: settingValue("alertRules"),
    alertThresholds: settingValue("alertThresholds"),
    relationRuleThresholds: settingValue("relationRuleThresholds"),
    alertCadenceMinutes: settingValue("alertCadenceMinutes")
  };
}

function saveSettingsToServer() {
  if (isStaticPreviewHost()) {
    persistSettings();
    return Promise.resolve();
  }
  return sendJson("/api/settings", "PUT", { settings: serverSettingsPayload() })
    .then(function (payload) {
      applyServerSettings(payload);
      var reloads = [loadNotificationSchedules()];
      if (ontologyState.ontologyRuleboxLoaded) reloads.push(loadOntologyRulebox(true));
      return Promise.all(reloads)
        .catch(function (error) {
          notificationsState.messageSchedulesError = error.message || "설정 적용 후 운영 데이터를 다시 읽지 못했습니다.";
        });
    });
}

export { loadServerSettings, saveSettingsToServer };
