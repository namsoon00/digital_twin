import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from ..domain.accounts import configured
from ..domain.instrument_profiles import default_instrument_profiles_text
from ..domain.ontology_relation_reasoning import (
    DEFAULT_RELATION_THRESHOLDS,
    default_ai_prompt_policy_text,
    default_ai_prompt_templates_text,
    default_ontology_relation_reasoning_text,
)
from ..domain.parsing import parse_assignments


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = ROOT_DIR / "data"

TEXT_SETTING_KEYS = [
    "appTheme",
    "watchlistSymbols",
    "mysqlUrl",
    "mysqlHost",
    "mysqlPort",
    "mysqlDatabase",
    "mysqlUser",
    "mysqlUnixSocket",
    "mysqlTablePartitioning",
    "mysqlOperationTimeoutSeconds",
    "operationalHistoryRetentionEnabled",
    "operationalHistoryRetentionHours",
    "operationalHistoryRetentionBatchSize",
    "operationalHistoryRetentionCheckIntervalSeconds",
    "operationalSnapshotHistoryKeepCount",
    "operationalSuppressedNotificationRetentionMinutes",
    "operationalLargeDomainEventKeepCount",
    "operationalLargeDomainEventNames",
    "tossApiBaseUrl",
    "tossAccountSeq",
    "tossTokenRefreshSkewSeconds",
    "kisBaseUrl",
    "kisMarketSignalsEnabled",
    "kisMarketSignalMaxSymbols",
    "kisMarketSignalCacheMinutes",
    "kisMarketSignalGapSeconds",
    "kisMarketSignalPreferLiveDuringMarketHours",
    "kisMarketSignalLiveRefreshSeconds",
    "kisMarketSignalUnchangedStaleCount",
    "notifyProvider",
    "telegramChatId",
    "operationsTelegramChatId",
    "notifyLinkUrl",
    "fxRates",
    "valuationAssumptions",
    "aiValuationAutoProposalEnabled",
    "aiValuationCurrentPriceAnchorEnabled",
    "valuationReviewOverrides",
    "aiValuationPreferredParValue",
    "aiValuationPreferredRiskSpreadPct",
    "aiValuationPreferredRequiredYieldPct",
    "aiValuationPreferredMinimumMarginPct",
    "aiValuationBaselineMinimumMarginPct",
    "marketSignalInputs",
    "fairValueFormula",
    "ontologyRelationRules",
    "instrumentProfiles",
    "investmentLanguageRegistryJson",
    "aiPromptTemplates",
    "aiPromptPolicy",
    "modelName",
    "modelHypothesis",
    "modelTimingScenario",
    "modelTimingSymbols",
    "alertRules",
    "alertThresholds",
    "relationRuleThresholds",
    "alertCadenceMinutes",
    "monitorConnectionFailureAlertStreak",
    "modelReviewUseCodex",
    "modelReviewCommand",
    "modelReviewTimeoutSeconds",
    "modelReviewIntervalSeconds",
    "modelReviewBatchSize",
    "modelReviewTelegramMode",
    "operatorReasoningReportEnabled",
    "ontologyTypeDbEnabled",
    "ontologyTenantId",
    "ontologySharedMarketTenantId",
    "ontologySharedMarketWorldRetentionHours",
    "ontologySharedMarketWorldMaxSymbols",
    "ontologySharedMarketWorldAsyncProjectionEnabled",
    "ontologyProjectionGraphCacheEnabled",
    "ontologyProjectionGraphCacheTtlSeconds",
    "ontologyProjectionGraphCacheMaxEntries",
    "ontologyAsyncQualityRecordEnabled",
    "ontologyReasoningEnabled",
    "ontologyReasoningIntervalSeconds",
    "ontologyReasoningBatchSize",
    "ontologyReasoningMaxSymbolsPerRun",
    "ontologyReasoningCoherentSnapshotEnabled",
    "ontologyReasoningCoherentSnapshotMaxSymbols",
    "ontologyReasoningEventScanLimit",
    "ontologyReasoningMinIntervalSeconds",
    "ontologyReasoningUrgentMinIntervalSeconds",
    "ontologyReasoningProjectionRetrySeconds",
    "ontologyReasoningBackpressureEnabled",
    "ontologyReasoningBackpressureFactor",
    "ontologyReasoningBackpressureMaxSeconds",
    "ontologyReasoningFairnessMaxWaitSeconds",
    "ontologyReasoningMaintenanceEnabled",
    "ontologyReasoningMaintenanceIntervalSeconds",
    "ontologyProjectionCircuitFailureThreshold",
    "ontologyProjectionCircuitCooldownSeconds",
    "ontologyRuntimeProjectionSloSeconds",
    "ontologyRuntimeInferenceSloSeconds",
    "ontologyRuntimeSloConsecutiveBreachCount",
    "ontologyRuntimeAuditWindowRuns",
    "ontologyReasoningUrgentReviewLevels",
    "ontologyReasoningProcessedEventLimit",
    "ontologyReasoningTypeDbNativeRuleExecutionEnabled",
    "typedbNativeRuleTargetSymbolLimit",
    "typedbNativeRuleSelectionEnabled",
    "kisRealtimeWebSocketIncludeConfiguredInReasoning",
    "typedbABoxNodeBatchSize",
    "typedbABoxRelationBatchSize",
    "typedbABoxDeleteBatchSize",
    "typedbABoxIncrementalCleanupBatchSize",
    "typedbABoxIncrementalCleanupMaxBatchesPerSave",
    "typedbABoxInactiveGenerationKeepCount",
    "typedbABoxInactiveGenerationMaxPrunePerSave",
    "typedbABoxWriteTransactionQueryCount",
    "typedbScopedABoxLeaseSeconds",
    "typedbScopedABoxOrphanCleanupMaxGenerations",
    "typedbGraphWriteTransactionQueryCount",
    "typedbInferenceBoxNodeBatchSize",
    "typedbInferenceBoxRelationBatchSize",
    "typedbInferenceBoxWriteTransactionQueryCount",
    "typedbWriteMaxQueryBytes",
    "ontologyLabEnabled",
    "ontologyLabIntervalSeconds",
    "ontologyLabBatchSize",
    "ontologyLabRunHistoryLimit",
    "ontologyLabAutoApplyEnabled",
    "ontologyLabAutoApplyValidationStates",
    "ontologyLabAutoApplyNeedsReviewEnabled",
    "ontologyLabNotifyEnabled",
    "ontologyRuleCandidateAiEnabled",
    "ontologyRuleCandidateAiUseCodex",
        "ontologyRuleCandidateAiCommand",
        "ontologyRuleCandidateAiTimeoutSeconds",
        "ontologyRuleCandidateAiIntervalMinutes",
        "ontologyRuleCandidateAiMaxCandidates",
        "temporalWindowPeriods",
    "temporalWindowHistoryLimit",
    "materialityGateEnabled",
    "marketMaterialityPriceChangePct",
    "marketMaterialityTrendDistancePct",
    "marketMaterialityTrendDistanceChangePct",
    "marketMaterialityVolumeRatio",
    "typedbAddress",
    "typedbUser",
    "typedbAllowDefaultPassword",
    "typedbDatabase",
    "typedbTlsEnabled",
    "typedbTimeoutSeconds",
    "typedbRetryCount",
    "typedbInferenceGenerationKeepCount",
    "typedbQueryTimeoutSeconds",
    "typedbSchemaOperationTimeoutSeconds",
    "typedbWriteOperationTimeoutSeconds",
    "typedbNativeRuleExecutionEnabled",
    "typedbNativeRuleQueryTimeoutSeconds",
    "typedbNativeRuleExecutionBudgetSeconds",
    "typedbProcessSchemaFunctionCacheEnabled",
    "typedbSchemaFunctionProbeIntervalSeconds",
    "typedbAutoResetEnabled",
    "typedbAgeResetEnabled",
    "typedbDataRetentionHours",
    "typedbDataMaxSizeMb",
    "typedbSeedOnStart",
    "typedbSeedReplaceRuleBox",
    "typedbSeedKeepInference",
    "typedbSeedTimeoutSeconds",
    "typedbSeedRetryCount",
    "mysqlRuntimeManaged",
    "mysqlConnectionPoolSize",
    "externalSignalCacheMaxEntries",
    "webPort",
    "dartDisclosureAiAnalysisEnabled",
    "dartDisclosureAiUseCodex",
    "dartDisclosureAiCommand",
    "dartDisclosureAiTimeoutSeconds",
    "notificationQueueIntervalSeconds",
    "notificationQueueBatchSize",
    "notificationSendGapSeconds",
    "notificationProcessingStaleMinutes",
    "investmentBrainMinimumHypothesisCount",
    "investmentBrainMaximumHypothesisCount",
    "investmentBrainInferenceBoxLimit",
    "investmentBrainResearchEnabled",
    "investmentBrainResearchMaxRounds",
    "investmentBrainResearchEvidenceLimit",
    "investmentBrainResearchMinimumVerifiedCount",
    "investmentBrainResearchMinimumSourceTrustState",
    "investmentBrainResearchCooldownMinutes",
    "investmentBrainResearchWorkerIntervalSeconds",
    "investmentBrainResearchWorkerBatchSize",
    "investmentBrainResearchProcessingStaleMinutes",
    "investmentBrainPerformanceMinimumSamples",
    "investmentBrainOutcomeObservationMinutes",
    "investmentBrainOutcomeEpisodeBatchSize",
    "investmentBrainOutcomeMaxDelayMinutes",
    "investmentBrainNotificationResearchEnabled",
    "investmentBrainNovelHypothesisAiEnabled",
    "investmentBrainNovelHypothesisAiCommand",
    "investmentBrainNovelHypothesisAiTimeoutSeconds",
    "sentArticleFilterEnabled",
    "sentArticleFilterHistoryLimit",
    "monitorAccountQueueEnabled",
    "monitorAccountIntervalSeconds",
    "monitorAccountBatchSize",
    "monitorAccountLockSeconds",
    "symbolUniverseMaxAgeHours",
    "marketDataCollectionEnabled",
    "marketDataCollectionIntervalSeconds",
    "marketDataCollectionMarkets",
    "marketDataMaxAgeMinutes",
    "marketDataPriceBatchSize",
    "marketDataCandleBatchSize",
    "marketDataRefreshUniverse",
    "marketTimeSeriesEnabled",
    "marketTimeSeriesRawRetentionDays",
    "marketTimeSeries15mRetentionDays",
    "marketTimeSeries1hRetentionDays",
    "marketTimeSeriesDailyRetentionDays",
    "marketTimeSeriesMaxPointsPerWindow",
    "marketSignalDataCollectionEnabled",
    "marketSignalDataBatchSize",
    "dataFreshnessEnabled",
    "dataFreshnessDefaultMaxAgeMinutes",
    "dataFreshnessQuoteMaxAgeMinutes",
    "dataFreshnessKisPriceMaxAgeMinutes",
    "dataFreshnessKisMicrostructureMaxAgeMinutes",
    "dataFreshnessKisInvestorMaxAgeMinutes",
    "dataFreshnessExternalMaxAgeMinutes",
    "dataFreshnessExternalEquityMaxAgeMinutes",
    "dataFreshnessExternalCryptoMaxAgeMinutes",
    "dataFreshnessMacroMaxAgeMinutes",
    "dataFreshnessDisclosureMaxAgeMinutes",
    "externalApiFetchIntervalMinutes",
    "externalSignalCacheMaxAgeMinutes",
    "externalAlphaEnabled",
    "externalCoinGeckoEnabled",
    "externalFredEnabled",
    "externalFredSeries",
    "externalCryptoIds",
    "externalAlphaMaxSymbols",
    "externalAlphaRateLimitSeconds",
    "externalAlphaDailyRequestBudget",
    "externalAlphaQuotaCooldownMinutes",
    "externalAlphaFundamentalsEnabled",
    "externalAlphaFundamentalsMaxSymbols",
    "externalYFinanceEnabled",
    "externalYFinanceMaxSymbols",
    "externalYFinanceHistoryPeriod",
    "externalYFinanceHistoryInterval",
    "externalYFinanceHistoryRows",
    "externalYFinanceFinancialPeriods",
    "externalYFinanceTabularRows",
    "externalYFinanceOptionExpirations",
    "externalYFinanceOptionsMaxRows",
    "externalYFinanceEarningsLimit",
    "externalYFinanceNewsLimit",
    "externalYFinancePriceMaxAgeMinutes",
    "externalYFinanceOptionsMaxAgeMinutes",
    "externalYFinanceNewsMaxAgeMinutes",
    "externalYFinanceAnalystMaxAgeMinutes",
    "externalYFinanceFundamentalMaxAgeMinutes",
    "externalSecEnabled",
    "externalSecMaxSymbols",
    "externalSecCompanyCiks",
    "externalSecUserAgent",
    "externalDartEnabled",
    "externalDartLookbackDays",
    "externalDartCorpCodes",
    "externalNewsEnabled",
    "externalNewsProvider",
    "externalNewsMaxSymbols",
    "externalNewsLookbackHours",
    "externalResearchEvidenceMaxItems",
    "newsCollectionEnabled",
    "newsCollectionIntervalSeconds",
    "newsCollectionRunBudgetSeconds",
    "newsCollectionMaxSymbols",
    "newsCollectionLookbackMinutes",
    "newsCollectionPerSymbolLimit",
    "newsCollectionProviders",
    "newsCollectionKoreanProviders",
    "newsCollectionGoogleOriginalUrlResolveEnabled",
    "newsCollectionGoogleOriginalUrlMaxPerTarget",
    "newsCollectionGoogleOriginalUrlMaxPerRun",
    "newsCollectionMinimumRelevanceState",
    "newsDigestMinimumRelevanceState",
    "newsDigestMinimumMaterialityState",
    "newsDigestMinimumNeutralMaterialityState",
    "newsDigestMinimumSourceTrustState",
    "newsCollectionRequireArticleBodyForRss",
    "newsCollectionIncludeWatchlist",
    "newsCollectionIncludeHoldings",
    "newsCollectionRateLimitSeconds",
    "newsCollectionTimeoutSeconds",
    "newsCollectionProviderTimeoutSeconds",
    "newsCollectionGdeltTimeoutSeconds",
    "newsCollectionGdeltSyncEnabled",
    "newsCollectionArticleBodyTimeoutSeconds",
    "newsCollectionArticleBodyMaxPerTarget",
    "newsCollectionArticleBodyMaxPerRun",
    "newsCollectionQualityBlockedWarningStreak",
    "newsCollectionCoverageStaleMinutes",
    "dataPipelineHealthNotificationsEnabled",
    "newsEvidenceCleanupEnabled",
    "newsEvidenceMaxAgeMinutes",
    "newsEvidenceCleanupBatchSize",
    "newsEvidenceKeepUndated",
    "newsArticleBodyFailureWarnRate",
    "newsArticleBodyFailureMinimumCount",
    "newsAiAnalysisEnabled",
    "newsAiAnalysisUseCodex",
    "newsAiAnalysisCommand",
    "newsAiAnalysisTimeoutSeconds",
    "newsAiAnalysisInlineTimeoutSeconds",
    "newsAiAnalysisMaxPerTarget",
    "newsAiAnalysisMaxPerRun",
    "investmentCalendarAutoExtractEnabled",
    "investmentCalendarAutoExtractRegisterUndated",
    "investmentCalendarAutoExtractReviewEnabled",
    "investmentCalendarAiResearchEnabled",
    "investmentCalendarAiResearchRunCollection",
    "investmentCalendarAiResearchEvidenceLimit",
    "investmentCalendarAiResearchCandidateLimit",
    "investmentCalendarDiscoveryEnabled",
    "investmentCalendarDiscoveryIntervalHours",
    "investmentCalendarDiscoveryMaxSymbols",
    "investmentCalendarDiscoveryHorizonDays",
    "investmentCalendarOfficialMacroSyncEnabled",
    "investmentCalendarOfficialMacroSyncIntervalHours",
    "investmentCalendarOfficialMacroSyncRateLimitSeconds",
    "investmentCalendarOfficialMacroSyncTimeoutSeconds",
    "investmentCalendarBokPolicyDecisionEnabled",
    "investmentCalendarBokPolicyDecisionTimeKst",
    "investmentCalendarBokPolicyDecisionLookaheadYears",
    "externalApiRetryAttempts",
    "externalApiTimeoutSeconds",
    "externalApiRateLimitSeconds",
    "externalApiCircuitFailures",
    "externalApiCircuitCooldownMinutes",
    "externalFxRateEnabled",
    "externalFredMaxSeries",
    "externalCryptoMaxIds",
    "externalDartMaxSymbols",
]

SECRET_SETTING_KEYS = [
    "tossClientId",
    "tossClientSecret",
    "telegramBotToken",
    "operationsTelegramBotToken",
    "mysqlPassword",
    "kisAppKey",
    "kisAppSecret",
    "alphaVantageApiKey",
    "coingeckoApiKey",
    "fredApiKey",
    "opendartApiKey",
    "typedbPassword",
]

DEFAULT_ALERT_THRESHOLDS = [
    ("volumeRatioHigh", 2),
    ("buyShareHigh", 65),
    ("sellShareHigh", 65),
    ("orderbookImbalance", 25),
    ("momentumUp", 3),
    ("momentumDown", -3),
    ("marketCashLow", 10),
    ("priceNearPercent", 1),
    ("staleMinutes", 30),
    ("pendingOrderMinutes", 30),
]
DEFAULT_RELATION_RULE_THRESHOLDS = [
    (key, DEFAULT_RELATION_THRESHOLDS[key])
    for key in [
        "lossRateLow",
        "lossRateBufferPct",
        "lossGuardVolumeConfirmRatio",
        "lossGuardMa60SupportPct",
        "profitRateHigh",
        "sectorWeightHigh",
        "positionWeightHigh",
        "externalBitcoinChange24hPct",
        "externalBitcoinChange7dPct",
        "entryPullbackMa20BelowPct",
        "entryPullbackMa20DeepPct",
        "entryMa5TimingMinPct",
        "entryMomentumMa20MinPct",
        "entryMomentumMa60MinPct",
        "entryMa60SupportPct",
        "entryVolumeMinRatio",
        "entryVolumeMaxRatio",
        "entrySmartMoneyMin",
        "entryTradeStrengthMin",
        "entryOrderbookImbalanceMin",
        "entryMaxPositionWeight",
        "entryMaxSectorWeight",
        "macroRateDeltaBp",
        "macroRateHighPct",
        "macroRateLowPct",
        "macroCurveInversionPct",
        "usdKrwDeltaKrw",
        "usdKrwDeltaPct",
        "usdKrw7dDeltaKrw",
        "usdKrw7dDeltaPct",
        "usdKrwHigh",
        "usdKrwLow",
        "fxExposureReview",
        "fxExposureHigh",
        "newsDirectFreshMaxAgeMinutes",
    ]
]


def format_assignment_number(value) -> str:
    number = float(value or 0)
    return str(int(number)) if number.is_integer() else str(number).rstrip("0").rstrip(".")


def assignment_text(items) -> str:
    return "\n".join(str(key) + "=" + format_assignment_number(value) for key, value in items)


DEFAULT_STRATEGY_SETTINGS = {
    "fairValueFormula": "eps * targetPer * growthWeight * qualityWeight * riskWeight",
    "ontologyRelationRules": default_ontology_relation_reasoning_text(),
    "instrumentProfiles": default_instrument_profiles_text(),
    "aiPromptTemplates": default_ai_prompt_templates_text(),
    "aiPromptPolicy": default_ai_prompt_policy_text(),
    "notificationAiGateEnabled": "1",
    "notificationAiGateMessageTypes": "investmentInsight",
    "notificationAiUseCodex": "1",
    "notificationAiModel": "gpt-5.4",
    "notificationAiTimeoutSeconds": "120",
    "modelName": "나의 매수/매도 모델",
    "modelHypothesis": "손익, 가격 흐름, 수급, 가치, 뉴스와 반대 근거를 상태로 나눠 행동 조건을 결정한다.",
    "alertThresholds": assignment_text(DEFAULT_ALERT_THRESHOLDS),
    "relationRuleThresholds": assignment_text(DEFAULT_RELATION_RULE_THRESHOLDS),
}


def assignment_defaults(items) -> Dict[str, float]:
    return {str(key): float(value) for key, value in items}


def data_dir() -> Path:
    return Path(os.environ.get("DIGITAL_TWIN_DATA_DIR", str(DEFAULT_DATA_DIR))).resolve()


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_local_env() -> None:
    load_env_file(ROOT_DIR / ".env")
    load_env_file(ROOT_DIR / ".env.local")


def read_json(path: Path, fallback):
    try:
        if not path.exists():
            return fallback
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def write_private_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)


def settings_path() -> Path:
    explicit = os.environ.get("SETTINGS_PATH")
    if explicit:
        return (ROOT_DIR / explicit).resolve()
    return data_dir() / "settings.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def source_trust_state(value: object, fallback: str = "standard") -> str:
    """Normalize historical source-reliability settings at the settings edge."""
    text = str(value or "").strip().lower()
    if text in {"unknown", "limited", "standard", "trusted"}:
        return text
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        return fallback
    if numeric > 1:
        numeric /= 100.0
    if numeric >= 0.8:
        return "trusted"
    if numeric >= 0.55:
        return "standard"
    if numeric > 0:
        return "limited"
    return "unknown"


def normalized_ontology_reasoning_urgent_review_levels(value: object) -> str:
    """Keep only review levels that may bypass the normal reasoning interval.

    ``blocked`` means TypeDB did not provide a complete decision policy. It is
    not a more urgent investment judgement. Normalizing at this boundary also
    prevents stale settings from silently restoring the old behaviour.
    """

    allowed = ("act", "immediate")
    supplied = {
        item.strip().lower()
        for item in str(value or "").split(",")
        if item and item.strip()
    }
    selected = [level for level in allowed if level in supplied]
    return ",".join(selected or list(allowed))


def read_settings_store() -> Dict[str, str]:
    try:
        from .mysql_operational import MySQLRuntimeSettingsStore

        return MySQLRuntimeSettingsStore({"_skipOperationalHistoryRetention": "1"}).load()
    except Exception:
        return read_json(settings_path(), {})


def write_settings_store(settings: Dict[str, object]) -> Dict[str, str]:
    clean = {str(key): str(value or "") for key, value in settings.items()}
    try:
        from .mysql_operational import MySQLRuntimeSettingsStore

        store_settings = dict(clean)
        store_settings["_skipOperationalHistoryRetention"] = "1"
        MySQLRuntimeSettingsStore(store_settings).replace(clean)
    except Exception:
        write_private_json(settings_path(), clean)
    return clean


def save_runtime_settings(input_settings: Dict[str, object]) -> Dict[str, str]:
    current = read_settings_store()
    next_settings = dict(current)
    for key in TEXT_SETTING_KEYS:
        if key in input_settings:
            next_settings[key] = str(input_settings.get(key) or "").strip()
    for key in SECRET_SETTING_KEYS:
        if key in input_settings:
            value = str(input_settings.get(key) or "").strip()
            if value:
                next_settings[key] = value
    legacy_source_reliability = input_settings.get("investmentBrainResearchMinimumSourceReliability")
    if "investmentBrainResearchMinimumSourceTrustState" not in input_settings and legacy_source_reliability not in (None, ""):
        next_settings["investmentBrainResearchMinimumSourceTrustState"] = source_trust_state(legacy_source_reliability)
    if "ontologyReasoningUrgentReviewLevels" in input_settings:
        next_settings["ontologyReasoningUrgentReviewLevels"] = normalized_ontology_reasoning_urgent_review_levels(
            input_settings.get("ontologyReasoningUrgentReviewLevels")
        )
    if input_settings.get("clearTossCredentials"):
        for key in ["tossClientId", "tossClientSecret", "tossAccountSeq"]:
            next_settings.pop(key, None)
    if input_settings.get("clearTelegramCredentials"):
        for key in ["telegramBotToken", "telegramChatId"]:
            next_settings.pop(key, None)
    if input_settings.get("clearOperationsTelegramCredentials"):
        for key in ["operationsTelegramBotToken", "operationsTelegramChatId"]:
            next_settings.pop(key, None)
    for retired_key in {
        "buyScoreFormula",
        "sellScoreFormula",
        "profitTakeScoreFormula",
        "lossCutScoreFormula",
        "customBuyModelFormula",
        "customSellModelFormula",
        "formulaWeights",
        "decisionThresholds",
        "modelDecisionThresholds",
        "ontologyReasoningUrgentMaterialityScore",
        "ontologyLabAutoApplyMinScore",
        "materialityMinimumScore",
        "marketMaterialityMinimumScore",
        "newsMaterialityMinimumScore",
        "newsCollectionMinRelevanceScore",
        "newsDigestMinRelevanceScore",
        "newsDigestMinMaterialityScore",
        "newsDigestMinNeutralMaterialityScore",
        "newsDigestMinSourceReliability",
        "investmentBrainResearchMinimumSourceReliability",
        "investmentBrainPerformanceMinimumAccuracyPct",
        "investmentCalendarAutoExtractMinConfidence",
        "investmentCalendarAutoExtractReviewMinConfidence",
        "investmentCalendarAiResearchReviewMinConfidence",
    }:
        next_settings.pop(retired_key, None)
    next_settings["updatedAt"] = utc_now()
    write_settings_store(next_settings)
    return runtime_settings()


def runtime_settings() -> Dict[str, str]:
    load_local_env()
    store = read_settings_store()

    def value(key: str, env_name: str, fallback: str = "") -> str:
        stored = configured(store.get(key))
        if stored:
            return stored
        env_value = configured(os.environ.get(env_name))
        if env_value:
            return env_value
        return fallback

    settings = {
        "appTheme": value("appTheme", "APP_THEME", "light"),
        "watchlistSymbols": value("watchlistSymbols", "WATCHLIST_SYMBOLS", "TSLA,AAPL,NVDA,000660"),
        "mysqlUrl": value("mysqlUrl", "MYSQL_URL", ""),
        "mysqlHost": value("mysqlHost", "MYSQL_HOST", "127.0.0.1"),
        "mysqlPort": value("mysqlPort", "MYSQL_PORT", "3306"),
        "mysqlDatabase": value("mysqlDatabase", "MYSQL_DATABASE", "orbit_alpha"),
        "mysqlUser": value("mysqlUser", "MYSQL_USER", ""),
        "mysqlPassword": value("mysqlPassword", "MYSQL_PASSWORD", ""),
        "mysqlUnixSocket": value("mysqlUnixSocket", "MYSQL_UNIX_SOCKET", ""),
        "mysqlTablePartitioning": value("mysqlTablePartitioning", "MYSQL_TABLE_PARTITIONING", "auto"),
        "mysqlOperationTimeoutSeconds": value("mysqlOperationTimeoutSeconds", "MYSQL_OPERATION_TIMEOUT_SECONDS", "10"),
        "operationalHistoryRetentionEnabled": value(
            "operationalHistoryRetentionEnabled",
            "OPERATIONAL_HISTORY_RETENTION_ENABLED",
            "1",
        ),
        "operationalHistoryRetentionHours": value(
            "operationalHistoryRetentionHours",
            "OPERATIONAL_HISTORY_RETENTION_HOURS",
            "24",
        ),
        "operationalHistoryRetentionBatchSize": value(
            "operationalHistoryRetentionBatchSize",
            "OPERATIONAL_HISTORY_RETENTION_BATCH_SIZE",
            "1000",
        ),
        "operationalHistoryRetentionCheckIntervalSeconds": value(
            "operationalHistoryRetentionCheckIntervalSeconds",
            "OPERATIONAL_HISTORY_RETENTION_CHECK_INTERVAL_SECONDS",
            "300",
        ),
        "operationalSnapshotHistoryKeepCount": value(
            "operationalSnapshotHistoryKeepCount",
            "OPERATIONAL_SNAPSHOT_HISTORY_KEEP_COUNT",
            "6",
        ),
        "operationalSuppressedNotificationRetentionMinutes": value(
            "operationalSuppressedNotificationRetentionMinutes",
            "OPERATIONAL_SUPPRESSED_NOTIFICATION_RETENTION_MINUTES",
            "120",
        ),
        "operationalLargeDomainEventKeepCount": value(
            "operationalLargeDomainEventKeepCount",
            "OPERATIONAL_LARGE_DOMAIN_EVENT_KEEP_COUNT",
            "100",
        ),
        "operationalLargeDomainEventNames": value(
            "operationalLargeDomainEventNames",
            "OPERATIONAL_LARGE_DOMAIN_EVENT_NAMES",
            "monitoring.alerts_detected",
        ),
        "tossApiBaseUrl": value("tossApiBaseUrl", "TOSS_API_BASE_URL", "https://openapi.tossinvest.com"),
        "tossClientId": value("tossClientId", "TOSS_CLIENT_ID"),
        "tossClientSecret": value("tossClientSecret", "TOSS_CLIENT_SECRET"),
        "tossAccountSeq": value("tossAccountSeq", "TOSS_ACCOUNT_SEQ"),
        "tossTokenRefreshSkewSeconds": value("tossTokenRefreshSkewSeconds", "TOSS_TOKEN_REFRESH_SKEW_SECONDS", "60"),
        "kisEnv": value("kisEnv", "KIS_ENV", "prod"),
        "kisBaseUrl": value("kisBaseUrl", "KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
        "kisWebSocketUrl": value("kisWebSocketUrl", "KIS_WEBSOCKET_URL", ""),
        "kisRealtimeWebSocketEnabled": value("kisRealtimeWebSocketEnabled", "KIS_REALTIME_WEBSOCKET_ENABLED", "1"),
        "kisRealtimeWebSocketSymbols": value("kisRealtimeWebSocketSymbols", "KIS_REALTIME_WEBSOCKET_SYMBOLS", ""),
        "kisRealtimeWebSocketMaxSymbols": value("kisRealtimeWebSocketMaxSymbols", "KIS_REALTIME_WEBSOCKET_MAX_SYMBOLS", "20"),
        "kisRealtimeWebSocketCollectSeconds": value("kisRealtimeWebSocketCollectSeconds", "KIS_REALTIME_WEBSOCKET_COLLECT_SECONDS", "30"),
        "kisRealtimeWebSocketEventIntervalSeconds": value("kisRealtimeWebSocketEventIntervalSeconds", "KIS_REALTIME_WEBSOCKET_EVENT_INTERVAL_SECONDS", "15"),
        "kisRealtimeWebSocketReconnectSeconds": value("kisRealtimeWebSocketReconnectSeconds", "KIS_REALTIME_WEBSOCKET_RECONNECT_SECONDS", "5"),
        "kisRealtimeWebSocketTimeoutSeconds": value("kisRealtimeWebSocketTimeoutSeconds", "KIS_REALTIME_WEBSOCKET_TIMEOUT_SECONDS", "10"),
        "kisMarketSignalsEnabled": value("kisMarketSignalsEnabled", "KIS_MARKET_SIGNALS_ENABLED", "1"),
        "kisMarketSignalMaxSymbols": value("kisMarketSignalMaxSymbols", "KIS_MARKET_SIGNAL_MAX_SYMBOLS", "20"),
        "kisMarketSignalCacheMinutes": value("kisMarketSignalCacheMinutes", "KIS_MARKET_SIGNAL_CACHE_MINUTES", "3"),
        "kisMarketSignalGapSeconds": value("kisMarketSignalGapSeconds", "KIS_MARKET_SIGNAL_GAP_SECONDS", "0.35"),
        "kisMarketSignalPreferLiveDuringMarketHours": value("kisMarketSignalPreferLiveDuringMarketHours", "KIS_MARKET_SIGNAL_PREFER_LIVE_DURING_MARKET_HOURS", "1"),
        "kisMarketSignalLiveRefreshSeconds": value("kisMarketSignalLiveRefreshSeconds", "KIS_MARKET_SIGNAL_LIVE_REFRESH_SECONDS", "60"),
        "kisMarketSignalUnchangedStaleCount": value("kisMarketSignalUnchangedStaleCount", "KIS_MARKET_SIGNAL_UNCHANGED_STALE_COUNT", "3"),
        "kisAppKey": value("kisAppKey", "KIS_APP_KEY"),
        "kisAppSecret": value("kisAppSecret", "KIS_APP_SECRET"),
        "notifyProvider": value("notifyProvider", "NOTIFY_PROVIDER"),
        "telegramBotToken": value("telegramBotToken", "TELEGRAM_BOT_TOKEN"),
        "telegramChatId": value("telegramChatId", "TELEGRAM_CHAT_ID"),
        "operationsTelegramBotToken": value("operationsTelegramBotToken", "OPERATIONS_TELEGRAM_BOT_TOKEN"),
        "operationsTelegramChatId": value("operationsTelegramChatId", "OPERATIONS_TELEGRAM_CHAT_ID"),
        "notifyLinkUrl": value("notifyLinkUrl", "NOTIFY_LINK_URL", "http://127.0.0.1:3000?tab=notifications"),
        "valuationAssumptions": value("valuationAssumptions", "VALUATION_ASSUMPTIONS", ""),
        "aiValuationAutoProposalEnabled": value("aiValuationAutoProposalEnabled", "AI_VALUATION_AUTO_PROPOSAL_ENABLED", "1"),
        "aiValuationCurrentPriceAnchorEnabled": value("aiValuationCurrentPriceAnchorEnabled", "AI_VALUATION_CURRENT_PRICE_ANCHOR_ENABLED", "0"),
        "valuationReviewOverrides": value("valuationReviewOverrides", "VALUATION_REVIEW_OVERRIDES", ""),
        "aiValuationPreferredParValue": value("aiValuationPreferredParValue", "AI_VALUATION_PREFERRED_PAR_VALUE", "100"),
        "aiValuationPreferredRiskSpreadPct": value("aiValuationPreferredRiskSpreadPct", "AI_VALUATION_PREFERRED_RISK_SPREAD_PCT", ""),
        "aiValuationPreferredRequiredYieldPct": value("aiValuationPreferredRequiredYieldPct", "AI_VALUATION_PREFERRED_REQUIRED_YIELD_PCT", ""),
        "aiValuationPreferredMinimumMarginPct": value("aiValuationPreferredMinimumMarginPct", "AI_VALUATION_PREFERRED_MINIMUM_MARGIN_PCT", "8"),
        "aiValuationBaselineMinimumMarginPct": value("aiValuationBaselineMinimumMarginPct", "AI_VALUATION_BASELINE_MINIMUM_MARGIN_PCT", "15"),
        "marketSignalInputs": value("marketSignalInputs", "MARKET_SIGNAL_INPUTS", ""),
        "fairValueFormula": value("fairValueFormula", "FAIR_VALUE_FORMULA", DEFAULT_STRATEGY_SETTINGS["fairValueFormula"]),
        "alertRules": value("alertRules", "ALERT_RULES"),
        "alertThresholds": value("alertThresholds", "ALERT_THRESHOLDS", DEFAULT_STRATEGY_SETTINGS["alertThresholds"]),
        "relationRuleThresholds": value("relationRuleThresholds", "RELATION_RULE_THRESHOLDS", DEFAULT_STRATEGY_SETTINGS["relationRuleThresholds"]),
        "alertCadenceMinutes": value("alertCadenceMinutes", "ALERT_CADENCE_MINUTES"),
        "monitorConnectionFailureAlertStreak": value("monitorConnectionFailureAlertStreak", "MONITOR_CONNECTION_FAILURE_ALERT_STREAK", "3"),
        "ontologyRelationRules": value("ontologyRelationRules", "ONTOLOGY_RELATION_RULES", DEFAULT_STRATEGY_SETTINGS["ontologyRelationRules"]),
        "investmentLanguageRegistryJson": value("investmentLanguageRegistryJson", "INVESTMENT_LANGUAGE_REGISTRY_JSON", ""),
        "aiPromptTemplates": value("aiPromptTemplates", "AI_PROMPT_TEMPLATES", DEFAULT_STRATEGY_SETTINGS["aiPromptTemplates"]),
        "aiPromptPolicy": value("aiPromptPolicy", "AI_PROMPT_POLICY", DEFAULT_STRATEGY_SETTINGS["aiPromptPolicy"]),
        "modelName": value("modelName", "MODEL_NAME", DEFAULT_STRATEGY_SETTINGS["modelName"]),
        "modelHypothesis": value("modelHypothesis", "MODEL_HYPOTHESIS", DEFAULT_STRATEGY_SETTINGS["modelHypothesis"]),
        "modelTimingScenario": value("modelTimingScenario", "MODEL_TIMING_SCENARIO", "recent-one-year"),
        "modelTimingSymbols": value("modelTimingSymbols", "MODEL_TIMING_SYMBOLS", "NVDA,AAPL,005930,000660,TSLA"),
        "modelReviewCommand": value("modelReviewCommand", "MODEL_REVIEW_COMMAND", ""),
        "modelReviewUseCodex": value("modelReviewUseCodex", "MODEL_REVIEW_USE_CODEX", "1"),
        "modelReviewTimeoutSeconds": value("modelReviewTimeoutSeconds", "MODEL_REVIEW_TIMEOUT_SECONDS", "180"),
        "modelReviewIntervalSeconds": value("modelReviewIntervalSeconds", "MODEL_REVIEW_INTERVAL_SECONDS", "300"),
        "modelReviewBatchSize": value("modelReviewBatchSize", "MODEL_REVIEW_BATCH_SIZE", "1"),
        "modelReviewTelegramMode": value("modelReviewTelegramMode", "MODEL_REVIEW_TELEGRAM_MODE", "actionableOnly"),
        "operatorReasoningReportEnabled": value(
            "operatorReasoningReportEnabled",
            "OPERATOR_REASONING_REPORT_ENABLED",
            "1",
        ),
        "investmentBrainMinimumHypothesisCount": value("investmentBrainMinimumHypothesisCount", "INVESTMENT_BRAIN_MINIMUM_HYPOTHESIS_COUNT", "3"),
        "investmentBrainMaximumHypothesisCount": value("investmentBrainMaximumHypothesisCount", "INVESTMENT_BRAIN_MAXIMUM_HYPOTHESIS_COUNT", "8"),
        "investmentBrainInferenceBoxLimit": value("investmentBrainInferenceBoxLimit", "INVESTMENT_BRAIN_INFERENCEBOX_LIMIT", "500"),
        "investmentBrainResearchEnabled": value("investmentBrainResearchEnabled", "INVESTMENT_BRAIN_RESEARCH_ENABLED", "1"),
        "investmentBrainResearchMaxRounds": value("investmentBrainResearchMaxRounds", "INVESTMENT_BRAIN_RESEARCH_MAX_ROUNDS", "2"),
        "investmentBrainResearchEvidenceLimit": value("investmentBrainResearchEvidenceLimit", "INVESTMENT_BRAIN_RESEARCH_EVIDENCE_LIMIT", "40"),
        "investmentBrainResearchMinimumVerifiedCount": value("investmentBrainResearchMinimumVerifiedCount", "INVESTMENT_BRAIN_RESEARCH_MINIMUM_VERIFIED_COUNT", "2"),
        "investmentBrainResearchMinimumSourceTrustState": source_trust_state(
            value("investmentBrainResearchMinimumSourceTrustState", "INVESTMENT_BRAIN_RESEARCH_MINIMUM_SOURCE_TRUST_STATE", ""),
            source_trust_state(
                value("investmentBrainResearchMinimumSourceReliability", "INVESTMENT_BRAIN_RESEARCH_MINIMUM_SOURCE_RELIABILITY", "55"),
            ),
        ),
        "investmentBrainResearchCooldownMinutes": value("investmentBrainResearchCooldownMinutes", "INVESTMENT_BRAIN_RESEARCH_COOLDOWN_MINUTES", "30"),
        "investmentBrainResearchWorkerIntervalSeconds": value("investmentBrainResearchWorkerIntervalSeconds", "INVESTMENT_BRAIN_RESEARCH_WORKER_INTERVAL_SECONDS", "15"),
        "investmentBrainResearchWorkerBatchSize": value("investmentBrainResearchWorkerBatchSize", "INVESTMENT_BRAIN_RESEARCH_WORKER_BATCH_SIZE", "3"),
        "investmentBrainResearchProcessingStaleMinutes": value("investmentBrainResearchProcessingStaleMinutes", "INVESTMENT_BRAIN_RESEARCH_PROCESSING_STALE_MINUTES", "30"),
        "investmentBrainPerformanceMinimumSamples": value("investmentBrainPerformanceMinimumSamples", "INVESTMENT_BRAIN_PERFORMANCE_MINIMUM_SAMPLES", "5"),
        "investmentBrainOutcomeObservationMinutes": value("investmentBrainOutcomeObservationMinutes", "INVESTMENT_BRAIN_OUTCOME_OBSERVATION_MINUTES", "60,1440,10080"),
        "investmentBrainOutcomeEpisodeBatchSize": value("investmentBrainOutcomeEpisodeBatchSize", "INVESTMENT_BRAIN_OUTCOME_EPISODE_BATCH_SIZE", "200"),
        "investmentBrainOutcomeMaxDelayMinutes": value("investmentBrainOutcomeMaxDelayMinutes", "INVESTMENT_BRAIN_OUTCOME_MAX_DELAY_MINUTES", "180"),
        "investmentBrainNotificationResearchEnabled": value("investmentBrainNotificationResearchEnabled", "INVESTMENT_BRAIN_NOTIFICATION_RESEARCH_ENABLED", "1"),
        "investmentBrainNovelHypothesisAiEnabled": value("investmentBrainNovelHypothesisAiEnabled", "INVESTMENT_BRAIN_NOVEL_HYPOTHESIS_AI_ENABLED", "1"),
        "investmentBrainNovelHypothesisAiCommand": value("investmentBrainNovelHypothesisAiCommand", "INVESTMENT_BRAIN_NOVEL_HYPOTHESIS_AI_COMMAND", ""),
        "investmentBrainNovelHypothesisAiTimeoutSeconds": value("investmentBrainNovelHypothesisAiTimeoutSeconds", "INVESTMENT_BRAIN_NOVEL_HYPOTHESIS_AI_TIMEOUT_SECONDS", "120"),
        "ontologyTypeDbEnabled": value("ontologyTypeDbEnabled", "ONTOLOGY_TYPEDB_ENABLED", "1"),
        "ontologyReasoningEnabled": value("ontologyReasoningEnabled", "ONTOLOGY_REASONING_ENABLED", "1"),
        "ontologyReasoningIntervalSeconds": value("ontologyReasoningIntervalSeconds", "ONTOLOGY_REASONING_INTERVAL_SECONDS", "10"),
        "ontologyReasoningBatchSize": value("ontologyReasoningBatchSize", "ONTOLOGY_REASONING_BATCH_SIZE", "200"),
        "ontologyReasoningMaxSymbolsPerRun": value("ontologyReasoningMaxSymbolsPerRun", "ONTOLOGY_REASONING_MAX_SYMBOLS_PER_RUN", "3"),
        "ontologyReasoningCoherentSnapshotEnabled": value("ontologyReasoningCoherentSnapshotEnabled", "ONTOLOGY_REASONING_COHERENT_SNAPSHOT_ENABLED", "1"),
        "ontologyReasoningCoherentSnapshotMaxSymbols": value("ontologyReasoningCoherentSnapshotMaxSymbols", "ONTOLOGY_REASONING_COHERENT_SNAPSHOT_MAX_SYMBOLS", "20"),
        "ontologyReasoningEventScanLimit": value("ontologyReasoningEventScanLimit", "ONTOLOGY_REASONING_EVENT_SCAN_LIMIT", "1500"),
        "ontologyReasoningMinIntervalSeconds": value("ontologyReasoningMinIntervalSeconds", "ONTOLOGY_REASONING_MIN_INTERVAL_SECONDS", "180"),
        "ontologyReasoningUrgentMinIntervalSeconds": value("ontologyReasoningUrgentMinIntervalSeconds", "ONTOLOGY_REASONING_URGENT_MIN_INTERVAL_SECONDS", "60"),
        "ontologyReasoningProjectionRetrySeconds": value("ontologyReasoningProjectionRetrySeconds", "ONTOLOGY_REASONING_PROJECTION_RETRY_SECONDS", "30"),
        "ontologyReasoningBackpressureEnabled": value("ontologyReasoningBackpressureEnabled", "ONTOLOGY_REASONING_BACKPRESSURE_ENABLED", "1"),
        "ontologyReasoningBackpressureFactor": value("ontologyReasoningBackpressureFactor", "ONTOLOGY_REASONING_BACKPRESSURE_FACTOR", "1.15"),
        "ontologyReasoningBackpressureMaxSeconds": value("ontologyReasoningBackpressureMaxSeconds", "ONTOLOGY_REASONING_BACKPRESSURE_MAX_SECONDS", "900"),
        "ontologyReasoningFairnessMaxWaitSeconds": value("ontologyReasoningFairnessMaxWaitSeconds", "ONTOLOGY_REASONING_FAIRNESS_MAX_WAIT_SECONDS", "900"),
        "ontologyReasoningMaintenanceEnabled": value("ontologyReasoningMaintenanceEnabled", "ONTOLOGY_REASONING_MAINTENANCE_ENABLED", "1"),
        "ontologyReasoningMaintenanceIntervalSeconds": value("ontologyReasoningMaintenanceIntervalSeconds", "ONTOLOGY_REASONING_MAINTENANCE_INTERVAL_SECONDS", "900"),
        "ontologyProjectionCircuitFailureThreshold": value("ontologyProjectionCircuitFailureThreshold", "ONTOLOGY_PROJECTION_CIRCUIT_FAILURE_THRESHOLD", "3"),
        "ontologyProjectionCircuitCooldownSeconds": value("ontologyProjectionCircuitCooldownSeconds", "ONTOLOGY_PROJECTION_CIRCUIT_COOLDOWN_SECONDS", "300"),
        "ontologyRuntimeProjectionSloSeconds": value("ontologyRuntimeProjectionSloSeconds", "ONTOLOGY_RUNTIME_PROJECTION_SLO_SECONDS", "120"),
        "ontologyRuntimeInferenceSloSeconds": value("ontologyRuntimeInferenceSloSeconds", "ONTOLOGY_RUNTIME_INFERENCE_SLO_SECONDS", "90"),
        "ontologyRuntimeSloConsecutiveBreachCount": value("ontologyRuntimeSloConsecutiveBreachCount", "ONTOLOGY_RUNTIME_SLO_CONSECUTIVE_BREACH_COUNT", "3"),
        "ontologyRuntimeAuditWindowRuns": value("ontologyRuntimeAuditWindowRuns", "ONTOLOGY_RUNTIME_AUDIT_WINDOW_RUNS", "40"),
        "ontologyReasoningUrgentReviewLevels": normalized_ontology_reasoning_urgent_review_levels(
            value("ontologyReasoningUrgentReviewLevels", "ONTOLOGY_REASONING_URGENT_REVIEW_LEVELS", "act,immediate")
        ),
        "ontologyReasoningProcessedEventLimit": value("ontologyReasoningProcessedEventLimit", "ONTOLOGY_REASONING_PROCESSED_EVENT_LIMIT", "10000"),
        "ontologyReasoningTypeDbNativeRuleExecutionEnabled": value("ontologyReasoningTypeDbNativeRuleExecutionEnabled", "ONTOLOGY_REASONING_TYPEDB_NATIVE_RULE_EXECUTION_ENABLED", "1"),
        "typedbNativeRuleTargetSymbolLimit": value(
            "typedbNativeRuleTargetSymbolLimit",
            "TYPEDB_NATIVE_RULE_TARGET_SYMBOL_LIMIT",
            "1",
        ),
        "typedbNativeRuleSelectionEnabled": value("typedbNativeRuleSelectionEnabled", "TYPEDB_NATIVE_RULE_SELECTION_ENABLED", "1"),
        "kisRealtimeWebSocketIncludeConfiguredInReasoning": value("kisRealtimeWebSocketIncludeConfiguredInReasoning", "KIS_REALTIME_WEBSOCKET_INCLUDE_CONFIGURED_IN_REASONING", "0"),
        "typedbABoxNodeBatchSize": value("typedbABoxNodeBatchSize", "TYPEDB_ABOX_NODE_BATCH_SIZE", "100"),
        "typedbABoxRelationBatchSize": value("typedbABoxRelationBatchSize", "TYPEDB_ABOX_RELATION_BATCH_SIZE", "1"),
        "typedbABoxDeleteBatchSize": value("typedbABoxDeleteBatchSize", "TYPEDB_ABOX_DELETE_BATCH_SIZE", "1000"),
        "typedbABoxIncrementalCleanupBatchSize": value("typedbABoxIncrementalCleanupBatchSize", "TYPEDB_ABOX_INCREMENTAL_CLEANUP_BATCH_SIZE", "50"),
        "typedbABoxIncrementalCleanupMaxBatchesPerSave": value("typedbABoxIncrementalCleanupMaxBatchesPerSave", "TYPEDB_ABOX_INCREMENTAL_CLEANUP_MAX_BATCHES_PER_SAVE", "1"),
        "typedbABoxInactiveGenerationKeepCount": value("typedbABoxInactiveGenerationKeepCount", "TYPEDB_ABOX_INACTIVE_GENERATION_KEEP_COUNT", "0"),
        "typedbABoxInactiveGenerationMaxPrunePerSave": value("typedbABoxInactiveGenerationMaxPrunePerSave", "TYPEDB_ABOX_INACTIVE_GENERATION_MAX_PRUNE_PER_SAVE", "2"),
        "typedbABoxWriteTransactionQueryCount": value("typedbABoxWriteTransactionQueryCount", "TYPEDB_ABOX_WRITE_TRANSACTION_QUERY_COUNT", "8"),
        "typedbScopedABoxLeaseSeconds": value("typedbScopedABoxLeaseSeconds", "TYPEDB_SCOPED_ABOX_LEASE_SECONDS", "900"),
        "typedbScopedABoxOrphanCleanupMaxGenerations": value("typedbScopedABoxOrphanCleanupMaxGenerations", "TYPEDB_SCOPED_ABOX_ORPHAN_CLEANUP_MAX_GENERATIONS", "4"),
        "typedbGraphWriteTransactionQueryCount": value("typedbGraphWriteTransactionQueryCount", "TYPEDB_GRAPH_WRITE_TRANSACTION_QUERY_COUNT", "8"),
        "typedbInferenceBoxNodeBatchSize": value("typedbInferenceBoxNodeBatchSize", "TYPEDB_INFERENCEBOX_NODE_BATCH_SIZE", "25"),
        "typedbInferenceBoxRelationBatchSize": value("typedbInferenceBoxRelationBatchSize", "TYPEDB_INFERENCEBOX_RELATION_BATCH_SIZE", "1"),
        "typedbInferenceBoxWriteTransactionQueryCount": value("typedbInferenceBoxWriteTransactionQueryCount", "TYPEDB_INFERENCEBOX_WRITE_TRANSACTION_QUERY_COUNT", "8"),
        "typedbWriteMaxQueryBytes": value("typedbWriteMaxQueryBytes", "TYPEDB_WRITE_MAX_QUERY_BYTES", "192000"),
        "temporalWindowPeriods": value("temporalWindowPeriods", "TEMPORAL_WINDOW_PERIODS", "1D=1:2\n3D=3:3\n5D=5:4\n20D=20:5"),
        "ontologyLabEnabled": value("ontologyLabEnabled", "ONTOLOGY_LAB_ENABLED", "1"),
        "ontologyLabIntervalSeconds": value("ontologyLabIntervalSeconds", "ONTOLOGY_LAB_INTERVAL_SECONDS", "300"),
        "ontologyLabBatchSize": value("ontologyLabBatchSize", "ONTOLOGY_LAB_BATCH_SIZE", "5"),
        "ontologyLabRunHistoryLimit": value("ontologyLabRunHistoryLimit", "ONTOLOGY_LAB_RUN_HISTORY_LIMIT", "50"),
        "ontologyLabAutoApplyEnabled": value("ontologyLabAutoApplyEnabled", "ONTOLOGY_LAB_AUTO_APPLY_ENABLED", "1"),
        "ontologyLabAutoApplyValidationStates": value("ontologyLabAutoApplyValidationStates", "ONTOLOGY_LAB_AUTO_APPLY_VALIDATION_STATES", "ready"),
        "ontologyLabAutoApplyNeedsReviewEnabled": value("ontologyLabAutoApplyNeedsReviewEnabled", "ONTOLOGY_LAB_AUTO_APPLY_NEEDS_REVIEW_ENABLED", "0"),
        "ontologyLabNotifyEnabled": value("ontologyLabNotifyEnabled", "ONTOLOGY_LAB_NOTIFY_ENABLED", "1"),
        "ontologyRuleCandidateAiEnabled": value("ontologyRuleCandidateAiEnabled", "ONTOLOGY_RULE_CANDIDATE_AI_ENABLED", "1"),
        "ontologyRuleCandidateAiUseCodex": value("ontologyRuleCandidateAiUseCodex", "ONTOLOGY_RULE_CANDIDATE_AI_USE_CODEX", "1"),
        "ontologyRuleCandidateAiCommand": value("ontologyRuleCandidateAiCommand", "ONTOLOGY_RULE_CANDIDATE_AI_COMMAND", ""),
        "ontologyRuleCandidateAiTimeoutSeconds": value("ontologyRuleCandidateAiTimeoutSeconds", "ONTOLOGY_RULE_CANDIDATE_AI_TIMEOUT_SECONDS", "120"),
        "ontologyRuleCandidateAiIntervalMinutes": value("ontologyRuleCandidateAiIntervalMinutes", "ONTOLOGY_RULE_CANDIDATE_AI_INTERVAL_MINUTES", "60"),
        "ontologyRuleCandidateAiMaxCandidates": value("ontologyRuleCandidateAiMaxCandidates", "ONTOLOGY_RULE_CANDIDATE_AI_MAX_CANDIDATES", "3"),
        "temporalWindowHistoryLimit": value("temporalWindowHistoryLimit", "TEMPORAL_WINDOW_HISTORY_LIMIT", "96"),
        "materialityGateEnabled": value("materialityGateEnabled", "MATERIALITY_GATE_ENABLED", "1"),
        "marketMaterialityPriceChangePct": value("marketMaterialityPriceChangePct", "MARKET_MATERIALITY_PRICE_CHANGE_PCT", "0.6"),
        "marketMaterialityTrendDistancePct": value("marketMaterialityTrendDistancePct", "MARKET_MATERIALITY_TREND_DISTANCE_PCT", "2"),
        "marketMaterialityTrendDistanceChangePct": value("marketMaterialityTrendDistanceChangePct", "MARKET_MATERIALITY_TREND_DISTANCE_CHANGE_PCT", "1"),
        "marketMaterialityVolumeRatio": value("marketMaterialityVolumeRatio", "MARKET_MATERIALITY_VOLUME_RATIO", "1.5"),
        "ontologyTenantId": value("ontologyTenantId", "ONTOLOGY_TENANT_ID", "local"),
        "ontologySharedMarketTenantId": value("ontologySharedMarketTenantId", "ONTOLOGY_SHARED_MARKET_TENANT_ID", "shared"),
        "ontologySharedMarketWorldRetentionHours": value(
            "ontologySharedMarketWorldRetentionHours",
            "ONTOLOGY_SHARED_MARKET_WORLD_RETENTION_HOURS",
            "72",
        ),
        "ontologySharedMarketWorldMaxSymbols": value(
            "ontologySharedMarketWorldMaxSymbols",
            "ONTOLOGY_SHARED_MARKET_WORLD_MAX_SYMBOLS",
            "1200",
        ),
        "ontologySharedMarketWorldAsyncProjectionEnabled": value(
            "ontologySharedMarketWorldAsyncProjectionEnabled",
            "ONTOLOGY_SHARED_MARKET_WORLD_ASYNC_PROJECTION_ENABLED",
            "1",
        ),
        "ontologyProjectionGraphCacheEnabled": value(
            "ontologyProjectionGraphCacheEnabled",
            "ONTOLOGY_PROJECTION_GRAPH_CACHE_ENABLED",
            "1",
        ),
        "ontologyProjectionGraphCacheTtlSeconds": value(
            "ontologyProjectionGraphCacheTtlSeconds",
            "ONTOLOGY_PROJECTION_GRAPH_CACHE_TTL_SECONDS",
            "45",
        ),
        "ontologyProjectionGraphCacheMaxEntries": value(
            "ontologyProjectionGraphCacheMaxEntries",
            "ONTOLOGY_PROJECTION_GRAPH_CACHE_MAX_ENTRIES",
            "16",
        ),
        "ontologyAsyncQualityRecordEnabled": value(
            "ontologyAsyncQualityRecordEnabled",
            "ONTOLOGY_ASYNC_QUALITY_RECORD_ENABLED",
            "1",
        ),
        "typedbAddress": value("typedbAddress", "TYPEDB_ADDRESS", "127.0.0.1:1729"),
        "typedbUser": value("typedbUser", "TYPEDB_USER", "admin"),
        "typedbAllowDefaultPassword": value(
            "typedbAllowDefaultPassword",
            "TYPEDB_ALLOW_DEFAULT_PASSWORD",
            "0",
        ),
        "typedbPassword": value("typedbPassword", "TYPEDB_PASSWORD", ""),
        "typedbDatabase": value("typedbDatabase", "TYPEDB_DATABASE", "orbit_alpha_ontology"),
        "typedbTlsEnabled": value("typedbTlsEnabled", "TYPEDB_TLS_ENABLED", "0"),
        "typedbTimeoutSeconds": value("typedbTimeoutSeconds", "TYPEDB_TIMEOUT_SECONDS", "20"),
        "typedbRetryCount": value("typedbRetryCount", "TYPEDB_RETRY_COUNT", "2"),
        "typedbInferenceGenerationKeepCount": value("typedbInferenceGenerationKeepCount", "TYPEDB_INFERENCE_GENERATION_KEEP_COUNT", "1"),
        "typedbInferenceWriteLeaseEnabled": value(
            "typedbInferenceWriteLeaseEnabled",
            "TYPEDB_INFERENCE_WRITE_LEASE_ENABLED",
            "1",
        ),
        "typedbQueryTimeoutSeconds": value("typedbQueryTimeoutSeconds", "TYPEDB_QUERY_TIMEOUT_SECONDS", "20"),
        "typedbSchemaOperationTimeoutSeconds": value("typedbSchemaOperationTimeoutSeconds", "TYPEDB_SCHEMA_OPERATION_TIMEOUT_SECONDS", "120"),
        "typedbWriteOperationTimeoutSeconds": value("typedbWriteOperationTimeoutSeconds", "TYPEDB_WRITE_OPERATION_TIMEOUT_SECONDS", "120"),
        "typedbNativeRuleExecutionEnabled": value(
            "typedbNativeRuleExecutionEnabled",
            "TYPEDB_NATIVE_RULE_EXECUTION_ENABLED",
            value(
                "ontologyReasoningTypeDbNativeRuleExecutionEnabled",
                "ONTOLOGY_REASONING_TYPEDB_NATIVE_RULE_EXECUTION_ENABLED",
                "1",
            ),
        ),
        "typedbNativeRuleQueryTimeoutSeconds": value(
            "typedbNativeRuleQueryTimeoutSeconds",
            "TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS",
            "10",
        ),
        "typedbNativeRuleExecutionBudgetSeconds": value(
            "typedbNativeRuleExecutionBudgetSeconds",
            "TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS",
            "105",
        ),
        "typedbProcessSchemaFunctionCacheEnabled": value(
            "typedbProcessSchemaFunctionCacheEnabled",
            "TYPEDB_PROCESS_SCHEMA_FUNCTION_CACHE_ENABLED",
            "1",
        ),
        "typedbSchemaFunctionProbeIntervalSeconds": value(
            "typedbSchemaFunctionProbeIntervalSeconds",
            "TYPEDB_SCHEMA_FUNCTION_PROBE_INTERVAL_SECONDS",
            "300",
        ),
        "typedbAutoResetEnabled": value("typedbAutoResetEnabled", "TYPEDB_AUTO_RESET_ENABLED", "0"),
        "typedbAgeResetEnabled": value("typedbAgeResetEnabled", "TYPEDB_AGE_RESET_ENABLED", "0"),
        "typedbDataRetentionHours": value("typedbDataRetentionHours", "TYPEDB_DATA_RETENTION_HOURS", "24"),
        "typedbDataMaxSizeMb": value("typedbDataMaxSizeMb", "TYPEDB_DATA_MAX_SIZE_MB", "2048"),
        "typedbSeedOnStart": value("typedbSeedOnStart", "TYPEDB_SEED_ON_START", "1"),
        "typedbSeedReplaceRuleBox": value("typedbSeedReplaceRuleBox", "TYPEDB_SEED_REPLACE_RULEBOX", "1"),
        "typedbSeedKeepInference": value("typedbSeedKeepInference", "TYPEDB_SEED_KEEP_INFERENCE", "1"),
        "typedbSeedTimeoutSeconds": value("typedbSeedTimeoutSeconds", "TYPEDB_SEED_TIMEOUT_SECONDS", "360"),
        "typedbSeedRetryCount": value("typedbSeedRetryCount", "TYPEDB_SEED_RETRY_COUNT", "2"),
        "dartDisclosureAiAnalysisEnabled": value("dartDisclosureAiAnalysisEnabled", "DART_DISCLOSURE_AI_ANALYSIS_ENABLED", "1"),
        "dartDisclosureAiUseCodex": value("dartDisclosureAiUseCodex", "DART_DISCLOSURE_AI_USE_CODEX", "1"),
        "dartDisclosureAiCommand": value("dartDisclosureAiCommand", "DART_DISCLOSURE_AI_COMMAND", ""),
        "dartDisclosureAiTimeoutSeconds": value("dartDisclosureAiTimeoutSeconds", "DART_DISCLOSURE_AI_TIMEOUT_SECONDS", "90"),
        "notificationQueueIntervalSeconds": value("notificationQueueIntervalSeconds", "NOTIFICATION_QUEUE_INTERVAL_SECONDS", "30"),
        "notificationQueueBatchSize": value("notificationQueueBatchSize", "NOTIFICATION_QUEUE_BATCH_SIZE", "10"),
        "notificationSendGapSeconds": value("notificationSendGapSeconds", "NOTIFICATION_SEND_GAP_SECONDS", "1"),
        "notificationProcessingStaleMinutes": value("notificationProcessingStaleMinutes", "NOTIFICATION_PROCESSING_STALE_MINUTES", "30"),
        "sentArticleFilterEnabled": value("sentArticleFilterEnabled", "SENT_ARTICLE_FILTER_ENABLED", "1"),
        "sentArticleFilterHistoryLimit": value("sentArticleFilterHistoryLimit", "SENT_ARTICLE_FILTER_HISTORY_LIMIT", "120"),
        "monitorAccountQueueEnabled": value("monitorAccountQueueEnabled", "MONITOR_ACCOUNT_QUEUE_ENABLED", "0"),
        "monitorAccountIntervalSeconds": value("monitorAccountIntervalSeconds", "MONITOR_ACCOUNT_INTERVAL_SECONDS", "180"),
        "monitorAccountBatchSize": value("monitorAccountBatchSize", "MONITOR_ACCOUNT_BATCH_SIZE", "10"),
        "monitorAccountLockSeconds": value("monitorAccountLockSeconds", "MONITOR_ACCOUNT_LOCK_SECONDS", "600"),
        "symbolUniverseMaxAgeHours": value("symbolUniverseMaxAgeHours", "SYMBOL_UNIVERSE_MAX_AGE_HOURS", "24"),
        "marketDataCollectionEnabled": value("marketDataCollectionEnabled", "MARKET_DATA_COLLECTION_ENABLED", "1"),
        "marketDataCollectionIntervalSeconds": value("marketDataCollectionIntervalSeconds", "MARKET_DATA_COLLECTION_INTERVAL_SECONDS", "180"),
        "marketDataCollectionMarkets": value("marketDataCollectionMarkets", "MARKET_DATA_COLLECTION_MARKETS", "KOSPI,KOSDAQ,NASDAQ"),
        "marketDataMaxAgeMinutes": value("marketDataMaxAgeMinutes", "MARKET_DATA_MAX_AGE_MINUTES", "240"),
        "marketDataPriceBatchSize": value("marketDataPriceBatchSize", "MARKET_DATA_PRICE_BATCH_SIZE", "200"),
        "marketDataCandleBatchSize": value("marketDataCandleBatchSize", "MARKET_DATA_CANDLE_BATCH_SIZE", "25"),
        "marketDataRefreshUniverse": value("marketDataRefreshUniverse", "MARKET_DATA_REFRESH_UNIVERSE", "1"),
        "marketTimeSeriesEnabled": value("marketTimeSeriesEnabled", "MARKET_TIME_SERIES_ENABLED", "1"),
        "marketTimeSeriesRawRetentionDays": value("marketTimeSeriesRawRetentionDays", "MARKET_TIME_SERIES_RAW_RETENTION_DAYS", "7"),
        "marketTimeSeries15mRetentionDays": value("marketTimeSeries15mRetentionDays", "MARKET_TIME_SERIES_15M_RETENTION_DAYS", "120"),
        "marketTimeSeries1hRetentionDays": value("marketTimeSeries1hRetentionDays", "MARKET_TIME_SERIES_1H_RETENTION_DAYS", "730"),
        "marketTimeSeriesDailyRetentionDays": value("marketTimeSeriesDailyRetentionDays", "MARKET_TIME_SERIES_DAILY_RETENTION_DAYS", "3650"),
        "marketTimeSeriesMaxPointsPerWindow": value("marketTimeSeriesMaxPointsPerWindow", "MARKET_TIME_SERIES_MAX_POINTS_PER_WINDOW", "500"),
        "marketSignalDataCollectionEnabled": value("marketSignalDataCollectionEnabled", "MARKET_SIGNAL_DATA_COLLECTION_ENABLED", "1"),
        "marketSignalDataBatchSize": value("marketSignalDataBatchSize", "MARKET_SIGNAL_DATA_BATCH_SIZE", "12"),
        "dataFreshnessEnabled": value("dataFreshnessEnabled", "DATA_FRESHNESS_ENABLED", "1"),
        "dataFreshnessDefaultMaxAgeMinutes": value("dataFreshnessDefaultMaxAgeMinutes", "DATA_FRESHNESS_DEFAULT_MAX_AGE_MINUTES", "10"),
        "dataFreshnessQuoteMaxAgeMinutes": value("dataFreshnessQuoteMaxAgeMinutes", "DATA_FRESHNESS_QUOTE_MAX_AGE_MINUTES", "10"),
        "dataFreshnessKisPriceMaxAgeMinutes": value("dataFreshnessKisPriceMaxAgeMinutes", "DATA_FRESHNESS_KIS_PRICE_MAX_AGE_MINUTES", "3"),
        "dataFreshnessKisMicrostructureMaxAgeMinutes": value("dataFreshnessKisMicrostructureMaxAgeMinutes", "DATA_FRESHNESS_KIS_MICROSTRUCTURE_MAX_AGE_MINUTES", "2"),
        "dataFreshnessKisInvestorMaxAgeMinutes": value("dataFreshnessKisInvestorMaxAgeMinutes", "DATA_FRESHNESS_KIS_INVESTOR_MAX_AGE_MINUTES", "30"),
        "dataFreshnessExternalMaxAgeMinutes": value("dataFreshnessExternalMaxAgeMinutes", "DATA_FRESHNESS_EXTERNAL_MAX_AGE_MINUTES", "10"),
        "dataFreshnessExternalEquityMaxAgeMinutes": value("dataFreshnessExternalEquityMaxAgeMinutes", "DATA_FRESHNESS_EXTERNAL_EQUITY_MAX_AGE_MINUTES", "10"),
        "dataFreshnessExternalCryptoMaxAgeMinutes": value("dataFreshnessExternalCryptoMaxAgeMinutes", "DATA_FRESHNESS_EXTERNAL_CRYPTO_MAX_AGE_MINUTES", "10"),
        "dataFreshnessMacroMaxAgeMinutes": value("dataFreshnessMacroMaxAgeMinutes", "DATA_FRESHNESS_MACRO_MAX_AGE_MINUTES", "120"),
        "dataFreshnessDisclosureMaxAgeMinutes": value("dataFreshnessDisclosureMaxAgeMinutes", "DATA_FRESHNESS_DISCLOSURE_MAX_AGE_MINUTES", "120"),
        "externalApiFetchIntervalMinutes": value("externalApiFetchIntervalMinutes", "EXTERNAL_API_FETCH_INTERVAL_MINUTES", "30"),
        "externalSignalCacheMaxAgeMinutes": value("externalSignalCacheMaxAgeMinutes", "EXTERNAL_SIGNAL_CACHE_MAX_AGE_MINUTES", "10"),
        "externalSignalCacheMaxEntries": value("externalSignalCacheMaxEntries", "EXTERNAL_SIGNAL_CACHE_MAX_ENTRIES", "6"),
        "mysqlRuntimeManaged": value("mysqlRuntimeManaged", "MYSQL_RUNTIME_MANAGED", "1"),
        "mysqlConnectionPoolSize": value("mysqlConnectionPoolSize", "MYSQL_CONNECTION_POOL_SIZE", "4"),
        "webPort": value("webPort", "WEB_PORT", "3000"),
        "externalAlphaEnabled": value("externalAlphaEnabled", "EXTERNAL_ALPHA_ENABLED", "1"),
        "externalAlphaRelatedSymbolsEnabled": value("externalAlphaRelatedSymbolsEnabled", "EXTERNAL_ALPHA_RELATED_SYMBOLS_ENABLED", "1"),
        "externalAlphaRelatedMaxSymbols": value("externalAlphaRelatedMaxSymbols", "EXTERNAL_ALPHA_RELATED_MAX_SYMBOLS", "8"),
        "externalCoinGeckoEnabled": value("externalCoinGeckoEnabled", "EXTERNAL_COINGECKO_ENABLED", "1"),
        "externalFredEnabled": value("externalFredEnabled", "EXTERNAL_FRED_ENABLED", "1"),
        "externalFredSeries": value("externalFredSeries", "EXTERNAL_FRED_SERIES", "DGS10,DGS2,DFF"),
        "externalCryptoIds": value("externalCryptoIds", "EXTERNAL_CRYPTO_IDS", "bitcoin,ethereum"),
        "externalAlphaMaxSymbols": value("externalAlphaMaxSymbols", "EXTERNAL_ALPHA_MAX_SYMBOLS", "3"),
        "externalAlphaRateLimitSeconds": value("externalAlphaRateLimitSeconds", "EXTERNAL_ALPHA_RATE_LIMIT_SECONDS", "15"),
        "externalAlphaDailyRequestBudget": value("externalAlphaDailyRequestBudget", "EXTERNAL_ALPHA_DAILY_REQUEST_BUDGET", "20"),
        "externalAlphaQuotaCooldownMinutes": value("externalAlphaQuotaCooldownMinutes", "EXTERNAL_ALPHA_QUOTA_COOLDOWN_MINUTES", "1440"),
        "externalAlphaFundamentalsEnabled": value("externalAlphaFundamentalsEnabled", "EXTERNAL_ALPHA_FUNDAMENTALS_ENABLED", "1"),
        "externalAlphaFundamentalsMaxSymbols": value("externalAlphaFundamentalsMaxSymbols", "EXTERNAL_ALPHA_FUNDAMENTALS_MAX_SYMBOLS", "1"),
        "externalYFinanceEnabled": value("externalYFinanceEnabled", "EXTERNAL_YFINANCE_ENABLED", "1"),
        "externalYFinanceMaxSymbols": value("externalYFinanceMaxSymbols", "EXTERNAL_YFINANCE_MAX_SYMBOLS", "8"),
        "externalYFinanceHistoryPeriod": value("externalYFinanceHistoryPeriod", "EXTERNAL_YFINANCE_HISTORY_PERIOD", "1y"),
        "externalYFinanceHistoryInterval": value("externalYFinanceHistoryInterval", "EXTERNAL_YFINANCE_HISTORY_INTERVAL", "1d"),
        "externalYFinanceHistoryRows": value("externalYFinanceHistoryRows", "EXTERNAL_YFINANCE_HISTORY_ROWS", "90"),
        "externalYFinanceFinancialPeriods": value("externalYFinanceFinancialPeriods", "EXTERNAL_YFINANCE_FINANCIAL_PERIODS", "4"),
        "externalYFinanceTabularRows": value("externalYFinanceTabularRows", "EXTERNAL_YFINANCE_TABULAR_ROWS", "40"),
        "externalYFinanceOptionExpirations": value("externalYFinanceOptionExpirations", "EXTERNAL_YFINANCE_OPTION_EXPIRATIONS", "2"),
        "externalYFinanceOptionsMaxRows": value("externalYFinanceOptionsMaxRows", "EXTERNAL_YFINANCE_OPTIONS_MAX_ROWS", "40"),
        "externalYFinanceEarningsLimit": value("externalYFinanceEarningsLimit", "EXTERNAL_YFINANCE_EARNINGS_LIMIT", "16"),
        "externalYFinanceNewsLimit": value("externalYFinanceNewsLimit", "EXTERNAL_YFINANCE_NEWS_LIMIT", "10"),
        "externalYFinancePriceMaxAgeMinutes": value("externalYFinancePriceMaxAgeMinutes", "EXTERNAL_YFINANCE_PRICE_MAX_AGE_MINUTES", "30"),
        "externalYFinanceOptionsMaxAgeMinutes": value("externalYFinanceOptionsMaxAgeMinutes", "EXTERNAL_YFINANCE_OPTIONS_MAX_AGE_MINUTES", "30"),
        "externalYFinanceNewsMaxAgeMinutes": value("externalYFinanceNewsMaxAgeMinutes", "EXTERNAL_YFINANCE_NEWS_MAX_AGE_MINUTES", "1440"),
        "externalYFinanceAnalystMaxAgeMinutes": value("externalYFinanceAnalystMaxAgeMinutes", "EXTERNAL_YFINANCE_ANALYST_MAX_AGE_MINUTES", "10080"),
        "externalYFinanceFundamentalMaxAgeMinutes": value("externalYFinanceFundamentalMaxAgeMinutes", "EXTERNAL_YFINANCE_FUNDAMENTAL_MAX_AGE_MINUTES", "129600"),
        "securityLineMappings": value("securityLineMappings", "SECURITY_LINE_MAPPINGS"),
        "externalSecEnabled": value("externalSecEnabled", "EXTERNAL_SEC_ENABLED", "1"),
        "externalSecMaxSymbols": value("externalSecMaxSymbols", "EXTERNAL_SEC_MAX_SYMBOLS", "3"),
        "externalSecCompanyCiks": value("externalSecCompanyCiks", "EXTERNAL_SEC_COMPANY_CIKS", "AAPL=0000320193\nMSFT=0000789019\nNVDA=0001045810\nTSLA=0001318605\nAMD=0000002488\nMSTR=0001050446"),
        "externalSecUserAgent": value("externalSecUserAgent", "EXTERNAL_SEC_USER_AGENT", "DigitalTwin/1.0 local-contact"),
        "externalDartEnabled": value("externalDartEnabled", "EXTERNAL_DART_ENABLED", "1"),
        "externalDartLookbackDays": value("externalDartLookbackDays", "EXTERNAL_DART_LOOKBACK_DAYS", "14"),
        "externalDartCorpCodes": value("externalDartCorpCodes", "EXTERNAL_DART_CORP_CODES", "005930=00126380\n000660=00164779\n035420=00266961"),
        "externalNewsEnabled": value("externalNewsEnabled", "EXTERNAL_NEWS_ENABLED", "0"),
        "externalNewsProvider": value("externalNewsProvider", "EXTERNAL_NEWS_PROVIDER", "auto"),
        "externalNewsMaxSymbols": value("externalNewsMaxSymbols", "EXTERNAL_NEWS_MAX_SYMBOLS", "3"),
        "externalNewsLookbackHours": value("externalNewsLookbackHours", "EXTERNAL_NEWS_LOOKBACK_HOURS", "48"),
        "externalResearchEvidenceMaxItems": value("externalResearchEvidenceMaxItems", "EXTERNAL_RESEARCH_EVIDENCE_MAX_ITEMS", "8"),
        "newsCollectionEnabled": value("newsCollectionEnabled", "NEWS_COLLECTION_ENABLED", "1"),
        "newsCollectionIntervalSeconds": value("newsCollectionIntervalSeconds", "NEWS_COLLECTION_INTERVAL_SECONDS", "60"),
        "newsCollectionRunBudgetSeconds": value("newsCollectionRunBudgetSeconds", "NEWS_COLLECTION_RUN_BUDGET_SECONDS", "45"),
        "newsCollectionMaxSymbols": value("newsCollectionMaxSymbols", "NEWS_COLLECTION_MAX_SYMBOLS", "3"),
        "newsCollectionLookbackMinutes": value("newsCollectionLookbackMinutes", "NEWS_COLLECTION_LOOKBACK_MINUTES", "180"),
        "newsCollectionPerSymbolLimit": value("newsCollectionPerSymbolLimit", "NEWS_COLLECTION_PER_SYMBOL_LIMIT", "8"),
        "newsCollectionProviders": value("newsCollectionProviders", "NEWS_COLLECTION_PROVIDERS", "yahoo_search,yahoo_finance"),
        "newsCollectionKoreanProviders": value("newsCollectionKoreanProviders", "NEWS_COLLECTION_KOREAN_PROVIDERS", "google_rss_kr"),
        "newsCollectionGoogleOriginalUrlResolveEnabled": value("newsCollectionGoogleOriginalUrlResolveEnabled", "NEWS_COLLECTION_GOOGLE_ORIGINAL_URL_RESOLVE_ENABLED", "1"),
        "newsCollectionGoogleOriginalUrlMaxPerTarget": value("newsCollectionGoogleOriginalUrlMaxPerTarget", "NEWS_COLLECTION_GOOGLE_ORIGINAL_URL_MAX_PER_TARGET", "2"),
        "newsCollectionGoogleOriginalUrlMaxPerRun": value("newsCollectionGoogleOriginalUrlMaxPerRun", "NEWS_COLLECTION_GOOGLE_ORIGINAL_URL_MAX_PER_RUN", "6"),
        "newsCollectionMinimumRelevanceState": value("newsCollectionMinimumRelevanceState", "NEWS_COLLECTION_MINIMUM_RELEVANCE_STATE", "context"),
        "newsDigestMinimumRelevanceState": value("newsDigestMinimumRelevanceState", "NEWS_DIGEST_MINIMUM_RELEVANCE_STATE", "direct"),
        "newsDigestMinimumMaterialityState": value("newsDigestMinimumMaterialityState", "NEWS_DIGEST_MINIMUM_MATERIALITY_STATE", "notable"),
        "newsDigestMinimumNeutralMaterialityState": value("newsDigestMinimumNeutralMaterialityState", "NEWS_DIGEST_MINIMUM_NEUTRAL_MATERIALITY_STATE", "material"),
        "newsDigestMinimumSourceTrustState": value("newsDigestMinimumSourceTrustState", "NEWS_DIGEST_MINIMUM_SOURCE_TRUST_STATE", "standard"),
        "newsCollectionRequireArticleBodyForRss": value("newsCollectionRequireArticleBodyForRss", "NEWS_COLLECTION_REQUIRE_ARTICLE_BODY_FOR_RSS", "1"),
        "newsCollectionIncludeWatchlist": value("newsCollectionIncludeWatchlist", "NEWS_COLLECTION_INCLUDE_WATCHLIST", "1"),
        "newsCollectionIncludeHoldings": value("newsCollectionIncludeHoldings", "NEWS_COLLECTION_INCLUDE_HOLDINGS", "1"),
        "newsCollectionRateLimitSeconds": value("newsCollectionRateLimitSeconds", "NEWS_COLLECTION_RATE_LIMIT_SECONDS", "0.25"),
        "newsCollectionTimeoutSeconds": value("newsCollectionTimeoutSeconds", "NEWS_COLLECTION_TIMEOUT_SECONDS", "8"),
        "newsCollectionProviderTimeoutSeconds": value("newsCollectionProviderTimeoutSeconds", "NEWS_COLLECTION_PROVIDER_TIMEOUT_SECONDS", "8"),
        "newsCollectionGdeltTimeoutSeconds": value("newsCollectionGdeltTimeoutSeconds", "NEWS_COLLECTION_GDELT_TIMEOUT_SECONDS", "4"),
        "newsCollectionGdeltSyncEnabled": value("newsCollectionGdeltSyncEnabled", "NEWS_COLLECTION_GDELT_SYNC_ENABLED", "0"),
        "newsCollectionArticleBodyTimeoutSeconds": value("newsCollectionArticleBodyTimeoutSeconds", "NEWS_COLLECTION_ARTICLE_BODY_TIMEOUT_SECONDS", "3"),
        "newsCollectionArticleBodyMaxPerTarget": value("newsCollectionArticleBodyMaxPerTarget", "NEWS_COLLECTION_ARTICLE_BODY_MAX_PER_TARGET", "4"),
        "newsCollectionArticleBodyMaxPerRun": value("newsCollectionArticleBodyMaxPerRun", "NEWS_COLLECTION_ARTICLE_BODY_MAX_PER_RUN", "12"),
        "newsCollectionQualityBlockedWarningStreak": value("newsCollectionQualityBlockedWarningStreak", "NEWS_COLLECTION_QUALITY_BLOCKED_WARNING_STREAK", "3"),
        "newsCollectionCoverageStaleMinutes": value("newsCollectionCoverageStaleMinutes", "NEWS_COLLECTION_COVERAGE_STALE_MINUTES", "180"),
        "dataPipelineHealthNotificationsEnabled": value("dataPipelineHealthNotificationsEnabled", "DATA_PIPELINE_HEALTH_NOTIFICATIONS_ENABLED", "1"),
        "newsEvidenceCleanupEnabled": value("newsEvidenceCleanupEnabled", "NEWS_EVIDENCE_CLEANUP_ENABLED", "1"),
        "newsEvidenceMaxAgeMinutes": value("newsEvidenceMaxAgeMinutes", "NEWS_EVIDENCE_MAX_AGE_MINUTES", "180"),
        "newsEvidenceCleanupBatchSize": value("newsEvidenceCleanupBatchSize", "NEWS_EVIDENCE_CLEANUP_BATCH_SIZE", "500"),
        "newsEvidenceKeepUndated": value("newsEvidenceKeepUndated", "NEWS_EVIDENCE_KEEP_UNDATED", "0"),
        "newsArticleBodyFailureWarnRate": value("newsArticleBodyFailureWarnRate", "NEWS_ARTICLE_BODY_FAILURE_WARN_RATE", "0.4"),
        "newsArticleBodyFailureMinimumCount": value("newsArticleBodyFailureMinimumCount", "NEWS_ARTICLE_BODY_FAILURE_MINIMUM_COUNT", "5"),
        "newsAiAnalysisEnabled": value("newsAiAnalysisEnabled", "NEWS_AI_ANALYSIS_ENABLED", "1"),
        "newsAiAnalysisUseCodex": value("newsAiAnalysisUseCodex", "NEWS_AI_ANALYSIS_USE_CODEX", "0"),
        "newsAiAnalysisCommand": value("newsAiAnalysisCommand", "NEWS_AI_ANALYSIS_COMMAND", ""),
        "newsAiAnalysisTimeoutSeconds": value("newsAiAnalysisTimeoutSeconds", "NEWS_AI_ANALYSIS_TIMEOUT_SECONDS", "15"),
        "newsAiAnalysisInlineTimeoutSeconds": value("newsAiAnalysisInlineTimeoutSeconds", "NEWS_AI_ANALYSIS_INLINE_TIMEOUT_SECONDS", "8"),
        "newsAiAnalysisMaxPerTarget": value("newsAiAnalysisMaxPerTarget", "NEWS_AI_ANALYSIS_MAX_PER_TARGET", "1"),
        "newsAiAnalysisMaxPerRun": value("newsAiAnalysisMaxPerRun", "NEWS_AI_ANALYSIS_MAX_PER_RUN", "2"),
        "investmentCalendarAutoExtractEnabled": value("investmentCalendarAutoExtractEnabled", "INVESTMENT_CALENDAR_AUTO_EXTRACT_ENABLED", "1"),
        "investmentCalendarAutoExtractRegisterUndated": value("investmentCalendarAutoExtractRegisterUndated", "INVESTMENT_CALENDAR_AUTO_EXTRACT_REGISTER_UNDATED", "0"),
        "investmentCalendarAutoExtractReviewEnabled": value("investmentCalendarAutoExtractReviewEnabled", "INVESTMENT_CALENDAR_AUTO_EXTRACT_REVIEW_ENABLED", "1"),
        "investmentCalendarAiResearchEnabled": value("investmentCalendarAiResearchEnabled", "INVESTMENT_CALENDAR_AI_RESEARCH_ENABLED", "1"),
        "investmentCalendarAiResearchRunCollection": value("investmentCalendarAiResearchRunCollection", "INVESTMENT_CALENDAR_AI_RESEARCH_RUN_COLLECTION", "1"),
        "investmentCalendarAiResearchEvidenceLimit": value("investmentCalendarAiResearchEvidenceLimit", "INVESTMENT_CALENDAR_AI_RESEARCH_EVIDENCE_LIMIT", "120"),
        "investmentCalendarAiResearchCandidateLimit": value("investmentCalendarAiResearchCandidateLimit", "INVESTMENT_CALENDAR_AI_RESEARCH_CANDIDATE_LIMIT", "50"),
        "investmentCalendarDiscoveryEnabled": value("investmentCalendarDiscoveryEnabled", "INVESTMENT_CALENDAR_DISCOVERY_ENABLED", "1"),
        "investmentCalendarDiscoveryIntervalHours": value("investmentCalendarDiscoveryIntervalHours", "INVESTMENT_CALENDAR_DISCOVERY_INTERVAL_HOURS", "12"),
        "investmentCalendarDiscoveryMaxSymbols": value("investmentCalendarDiscoveryMaxSymbols", "INVESTMENT_CALENDAR_DISCOVERY_MAX_SYMBOLS", "12"),
        "investmentCalendarDiscoveryHorizonDays": value("investmentCalendarDiscoveryHorizonDays", "INVESTMENT_CALENDAR_DISCOVERY_HORIZON_DAYS", "180"),
        "investmentCalendarOfficialMacroSyncEnabled": value("investmentCalendarOfficialMacroSyncEnabled", "INVESTMENT_CALENDAR_OFFICIAL_MACRO_SYNC_ENABLED", "1"),
        "investmentCalendarOfficialMacroSyncIntervalHours": value("investmentCalendarOfficialMacroSyncIntervalHours", "INVESTMENT_CALENDAR_OFFICIAL_MACRO_SYNC_INTERVAL_HOURS", "12"),
        "investmentCalendarOfficialMacroSyncRateLimitSeconds": value("investmentCalendarOfficialMacroSyncRateLimitSeconds", "INVESTMENT_CALENDAR_OFFICIAL_MACRO_SYNC_RATE_LIMIT_SECONDS", "600"),
        "investmentCalendarOfficialMacroSyncTimeoutSeconds": value("investmentCalendarOfficialMacroSyncTimeoutSeconds", "INVESTMENT_CALENDAR_OFFICIAL_MACRO_SYNC_TIMEOUT_SECONDS", "8"),
        "investmentCalendarBokPolicyDecisionEnabled": value("investmentCalendarBokPolicyDecisionEnabled", "INVESTMENT_CALENDAR_BOK_POLICY_DECISION_ENABLED", "1"),
        "investmentCalendarBokPolicyDecisionTimeKst": value("investmentCalendarBokPolicyDecisionTimeKst", "INVESTMENT_CALENDAR_BOK_POLICY_DECISION_TIME_KST", "09:00"),
        "investmentCalendarBokPolicyDecisionLookaheadYears": value("investmentCalendarBokPolicyDecisionLookaheadYears", "INVESTMENT_CALENDAR_BOK_POLICY_DECISION_LOOKAHEAD_YEARS", "1"),
        "externalApiRetryAttempts": value("externalApiRetryAttempts", "EXTERNAL_API_RETRY_ATTEMPTS", "2"),
        "externalApiTimeoutSeconds": value("externalApiTimeoutSeconds", "EXTERNAL_API_TIMEOUT_SECONDS", "3"),
        "externalApiRateLimitSeconds": value("externalApiRateLimitSeconds", "EXTERNAL_API_RATE_LIMIT_SECONDS", "60"),
        "externalApiCircuitFailures": value("externalApiCircuitFailures", "EXTERNAL_API_CIRCUIT_FAILURES", "2"),
        "externalApiCircuitCooldownMinutes": value("externalApiCircuitCooldownMinutes", "EXTERNAL_API_CIRCUIT_COOLDOWN_MINUTES", "30"),
        "externalFxRateEnabled": value("externalFxRateEnabled", "EXTERNAL_FX_RATE_ENABLED", "1"),
        "externalFxRateFetchIntervalHours": value("externalFxRateFetchIntervalHours", "EXTERNAL_FX_RATE_FETCH_INTERVAL_HOURS", "24"),
        "externalFredMaxSeries": value("externalFredMaxSeries", "EXTERNAL_FRED_MAX_SERIES", "5"),
        "externalCryptoMaxIds": value("externalCryptoMaxIds", "EXTERNAL_CRYPTO_MAX_IDS", "50"),
        "externalDartMaxSymbols": value("externalDartMaxSymbols", "EXTERNAL_DART_MAX_SYMBOLS", "5"),
        "alphaVantageApiKey": value("alphaVantageApiKey", "ALPHA_VANTAGE_API_KEY"),
        "coingeckoApiKey": value("coingeckoApiKey", "COINGECKO_API_KEY"),
        "fredApiKey": value("fredApiKey", "FRED_API_KEY"),
        "opendartApiKey": value("opendartApiKey", "OPENDART_API_KEY"),
        "fxRates": value("fxRates", "FX_RATES", "KRW=1\nUSD=1400"),
    }
    return settings


def currency_rates(settings: Dict[str, str] = None) -> Dict[str, float]:
    settings = settings or runtime_settings()
    raw = str(settings.get("fxRates") or "").replace(";", "\n")
    rates = parse_assignments(raw, {"KRW": 1.0, "USD": 1400.0})
    return {str(key).upper(): float(value or 0) for key, value in rates.items()}
