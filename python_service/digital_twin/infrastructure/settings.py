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
    "notifyLinkUrl",
    "fxRates",
    "valuationAssumptions",
    "marketSignalInputs",
    "fairValueFormula",
    "buyScoreFormula",
    "sellScoreFormula",
    "profitTakeScoreFormula",
    "lossCutScoreFormula",
    "notificationScoreFormula",
    "ontologyRelationRules",
    "instrumentProfiles",
    "aiPromptTemplates",
    "aiPromptPolicy",
    "modelName",
    "modelHypothesis",
    "customBuyModelFormula",
    "customSellModelFormula",
    "formulaWeights",
    "decisionThresholds",
    "modelDecisionThresholds",
    "modelTimingScenario",
    "modelTimingSymbols",
    "alertRules",
    "alertThresholds",
    "relationRuleThresholds",
    "alertCadenceMinutes",
    "modelReviewUseCodex",
    "modelReviewCommand",
    "modelReviewTimeoutSeconds",
    "modelReviewIntervalSeconds",
    "modelReviewBatchSize",
    "modelReviewTelegramMode",
    "ontologyTypeDbEnabled",
    "ontologyReasoningEnabled",
    "ontologyReasoningIntervalSeconds",
    "ontologyReasoningBatchSize",
    "ontologyLabEnabled",
    "ontologyLabIntervalSeconds",
    "ontologyLabBatchSize",
    "ontologyLabRunHistoryLimit",
    "ontologyRuleCandidateAiEnabled",
    "ontologyRuleCandidateAiUseCodex",
    "ontologyRuleCandidateAiCommand",
    "ontologyRuleCandidateAiTimeoutSeconds",
    "ontologyRuleCandidateAiIntervalMinutes",
    "ontologyRuleCandidateAiMaxCandidates",
    "materialityGateEnabled",
    "materialityMinimumScore",
    "marketMaterialityMinimumScore",
    "marketMaterialityPriceChangePct",
    "marketMaterialityTrendDistancePct",
    "marketMaterialityVolumeRatio",
    "newsMaterialityMinimumScore",
    "typedbAddress",
    "typedbUser",
    "typedbDatabase",
    "typedbTlsEnabled",
    "typedbTimeoutSeconds",
    "typedbRetryCount",
    "typedbInferenceGenerationKeepCount",
    "typedbAutoResetEnabled",
    "typedbDataRetentionHours",
    "typedbDataMaxSizeMb",
    "dartDisclosureAiAnalysisEnabled",
    "dartDisclosureAiUseCodex",
    "dartDisclosureAiCommand",
    "dartDisclosureAiTimeoutSeconds",
    "notificationQueueIntervalSeconds",
    "notificationQueueBatchSize",
    "notificationSendGapSeconds",
    "notificationProcessingStaleMinutes",
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
    "externalAlphaFundamentalsEnabled",
    "externalAlphaFundamentalsMaxSymbols",
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
    "newsCollectionMaxSymbols",
    "newsCollectionLookbackMinutes",
    "newsCollectionPerSymbolLimit",
    "newsCollectionProviders",
    "newsCollectionMinRelevanceScore",
    "newsCollectionRequireArticleBodyForRss",
    "newsCollectionIncludeWatchlist",
    "newsCollectionIncludeHoldings",
    "newsCollectionRateLimitSeconds",
    "newsCollectionTimeoutSeconds",
    "newsCollectionProviderTimeoutSeconds",
    "newsCollectionGdeltTimeoutSeconds",
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
    "investmentCalendarAutoExtractEnabled",
    "investmentCalendarAutoExtractRegisterUndated",
    "investmentCalendarAutoExtractMinConfidence",
    "investmentCalendarAutoExtractReviewEnabled",
    "investmentCalendarAutoExtractReviewMinConfidence",
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
    "mysqlPassword",
    "kisAppKey",
    "kisAppSecret",
    "alphaVantageApiKey",
    "coingeckoApiKey",
    "fredApiKey",
    "opendartApiKey",
    "typedbPassword",
]

DEFAULT_BUY_SCORE_FORMULA = (
    "50 + (executionScore * 0.42 + directionalVolumePressure * 0.9 + buyShareScore * 0.55 "
    "+ orderbookScore * 0.32 + momentumScore * 0.35 + trendScore * 0.45 "
    "+ investorFlowScore * 0.35) * flowWeight + undervalueBonus * valuationWeight - expensivePenalty * valuationWeight"
)
DEFAULT_SELL_SCORE_FORMULA = (
    "50 + (-executionScore * 0.38 - directionalVolumePressure * 0.85 - buyShareScore * 0.55 "
    "- orderbookScore * 0.3 - momentumScore * 0.4 - trendScore * 0.35 "
    "- investorFlowScore * 0.3) * flowWeight + expensiveBonus * valuationWeight"
)
DEFAULT_PROFIT_TAKE_SCORE_FORMULA = (
    "baseScore + profitTakePnlScore + sectorConcentrationScore + sellableScore + holdingSignalScore"
)
LEGACY_LOSS_CUT_SCORE_FORMULA = (
    "baseScore + lossCutPnlScore + sectorConcentrationScore + sellableScore + holdingSignalScore"
)
DEFAULT_LOSS_CUT_SCORE_FORMULA = (
    "baseScore + lossCutPnlScore + sectorConcentrationScore + sellableScore + holdingSignalScore "
    "+ lossGuardConfirmationScore - lossGuardWeakEvidencePenalty"
)
DEFAULT_NOTIFICATION_SCORE_FORMULA = "rawScore"
DEFAULT_FORMULA_WEIGHTS = [
    ("growthWeight", 1),
    ("qualityWeight", 1),
    ("riskWeight", 1),
    ("flowWeight", 1),
    ("valuationWeight", 1),
    ("buyReasonWeight", 0.25),
    ("confidenceWeight", 0.15),
    ("riskControlWeight", 0.35),
]
DEFAULT_DECISION_THRESHOLDS = [
    ("buyCandidate", 78),
    ("chaseCaution", 70),
    ("strongHold", 72),
    ("sellTrim", 70),
    ("riskReduce", 66),
    ("sellWatch", 64),
]
DEFAULT_MODEL_DECISION_THRESHOLDS = [
    ("graphSignalMinScore", 55),
    ("graphSignalAlertScore", 78),
    ("graphSignalConfidenceMin", 50),
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
    ("graphSignalMinScore", 55),
    ("graphSignalAlertScore", 78),
    ("graphSignalConfidenceMin", 50),
]
DEFAULT_RELATION_RULE_THRESHOLDS = [
    (key, DEFAULT_RELATION_THRESHOLDS[key])
    for key in [
        "lossRateLow",
        "lossRateBufferPct",
        "lossGuardVolumeConfirmRatio",
        "lossGuardMa60SupportPct",
        "lossGuardWeakEvidencePenalty",
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
        "newsDirectRelevanceMin",
        "newsDirectMaterialityMin",
    ]
]


def format_assignment_number(value) -> str:
    number = float(value or 0)
    return str(int(number)) if number.is_integer() else str(number).rstrip("0").rstrip(".")


def assignment_text(items) -> str:
    return "\n".join(str(key) + "=" + format_assignment_number(value) for key, value in items)


DEFAULT_STRATEGY_SETTINGS = {
    "fairValueFormula": "eps * targetPer * growthWeight * qualityWeight * riskWeight",
    "buyScoreFormula": DEFAULT_BUY_SCORE_FORMULA,
    "sellScoreFormula": DEFAULT_SELL_SCORE_FORMULA,
    "profitTakeScoreFormula": DEFAULT_PROFIT_TAKE_SCORE_FORMULA,
    "lossCutScoreFormula": DEFAULT_LOSS_CUT_SCORE_FORMULA,
    "notificationScoreFormula": DEFAULT_NOTIFICATION_SCORE_FORMULA,
    "ontologyRelationRules": default_ontology_relation_reasoning_text(),
    "instrumentProfiles": default_instrument_profiles_text(),
    "aiPromptTemplates": default_ai_prompt_templates_text(),
    "aiPromptPolicy": default_ai_prompt_policy_text(),
    "notificationAiGateEnabled": "1",
    "notificationAiGateMessageTypes": "investmentInsight",
    "notificationAiUseCodex": "1",
    "notificationAiTimeoutSeconds": "120",
    "modelName": "나의 매수/매도 모델",
    "modelHypothesis": "수급, 가치, 내 점수, 리스크를 함께 봐서 매수 후보와 매도 후보를 분리한다.",
    "customBuyModelFormula": "buyScore * 0.35 + buyReasonScore * buyReasonWeight + confidenceScore * confidenceWeight + max(0, targetReturn) * 0.15 + undervalueBonus * valuationWeight - riskScore * riskControlWeight",
    "customSellModelFormula": "sellScore * 0.35 + riskScore * riskControlWeight + expensivePenalty * valuationWeight + max(0, -targetReturn) * 0.2 - buyReasonScore * 0.1",
    "formulaWeights": assignment_text(DEFAULT_FORMULA_WEIGHTS),
    "decisionThresholds": assignment_text(DEFAULT_DECISION_THRESHOLDS),
    "modelDecisionThresholds": assignment_text(DEFAULT_MODEL_DECISION_THRESHOLDS),
    "alertThresholds": assignment_text(DEFAULT_ALERT_THRESHOLDS),
    "relationRuleThresholds": assignment_text(DEFAULT_RELATION_RULE_THRESHOLDS),
}


def assignment_defaults(items) -> Dict[str, float]:
    return {str(key): float(value) for key, value in items}


def assignment_text_from_map(values: Dict[str, float], ordered_items) -> str:
    ordered_keys = [key for key, _value in ordered_items]
    seen = set()
    rows = []
    for key in ordered_keys:
        if key in values:
            rows.append((key, values[key]))
            seen.add(key)
    for key in sorted(str(key) for key in values.keys() if str(key) not in seen):
        rows.append((key, values[key]))
    return assignment_text(rows)


def synced_model_alert_thresholds(alert_thresholds: str, model_thresholds: str) -> str:
    alerts = parse_assignments(alert_thresholds, assignment_defaults(DEFAULT_ALERT_THRESHOLDS))
    models = parse_assignments(model_thresholds, assignment_defaults(DEFAULT_MODEL_DECISION_THRESHOLDS))
    for key in ["graphSignalMinScore", "graphSignalAlertScore", "graphSignalConfidenceMin"]:
        alerts[key] = models.get(key, alerts.get(key, 0))
    return assignment_text_from_map(alerts, DEFAULT_ALERT_THRESHOLDS)


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


def read_settings_store() -> Dict[str, str]:
    try:
        from .mysql_operational import MySQLRuntimeSettingsStore

        return MySQLRuntimeSettingsStore({}).load()
    except Exception:
        return read_json(settings_path(), {})


def write_settings_store(settings: Dict[str, object]) -> Dict[str, str]:
    clean = {str(key): str(value or "") for key, value in settings.items()}
    from .mysql_operational import MySQLRuntimeSettingsStore

    MySQLRuntimeSettingsStore(clean).replace(clean)
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
    if input_settings.get("clearTossCredentials"):
        for key in ["tossClientId", "tossClientSecret", "tossAccountSeq"]:
            next_settings.pop(key, None)
    if input_settings.get("clearTelegramCredentials"):
        for key in ["telegramBotToken", "telegramChatId"]:
            next_settings.pop(key, None)
    if "modelDecisionThresholds" in input_settings:
        next_settings["alertThresholds"] = synced_model_alert_thresholds(
            next_settings.get("alertThresholds", ""),
            next_settings.get("modelDecisionThresholds", ""),
        )
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
        "notifyLinkUrl": value("notifyLinkUrl", "NOTIFY_LINK_URL", "http://127.0.0.1:3000?tab=notifications"),
        "valuationAssumptions": value("valuationAssumptions", "VALUATION_ASSUMPTIONS", ""),
        "marketSignalInputs": value("marketSignalInputs", "MARKET_SIGNAL_INPUTS", ""),
        "fairValueFormula": value("fairValueFormula", "FAIR_VALUE_FORMULA", DEFAULT_STRATEGY_SETTINGS["fairValueFormula"]),
        "alertRules": value("alertRules", "ALERT_RULES"),
        "alertThresholds": value("alertThresholds", "ALERT_THRESHOLDS", DEFAULT_STRATEGY_SETTINGS["alertThresholds"]),
        "relationRuleThresholds": value("relationRuleThresholds", "RELATION_RULE_THRESHOLDS", DEFAULT_STRATEGY_SETTINGS["relationRuleThresholds"]),
        "alertCadenceMinutes": value("alertCadenceMinutes", "ALERT_CADENCE_MINUTES"),
        "buyScoreFormula": value("buyScoreFormula", "BUY_SCORE_FORMULA", DEFAULT_STRATEGY_SETTINGS["buyScoreFormula"]),
        "sellScoreFormula": value("sellScoreFormula", "SELL_SCORE_FORMULA", DEFAULT_STRATEGY_SETTINGS["sellScoreFormula"]),
        "profitTakeScoreFormula": value("profitTakeScoreFormula", "PROFIT_TAKE_SCORE_FORMULA", DEFAULT_STRATEGY_SETTINGS["profitTakeScoreFormula"]),
        "lossCutScoreFormula": value("lossCutScoreFormula", "LOSS_CUT_SCORE_FORMULA", DEFAULT_STRATEGY_SETTINGS["lossCutScoreFormula"]),
        "notificationScoreFormula": value("notificationScoreFormula", "NOTIFICATION_SCORE_FORMULA", DEFAULT_STRATEGY_SETTINGS["notificationScoreFormula"]),
        "ontologyRelationRules": value("ontologyRelationRules", "ONTOLOGY_RELATION_RULES", DEFAULT_STRATEGY_SETTINGS["ontologyRelationRules"]),
        "aiPromptTemplates": value("aiPromptTemplates", "AI_PROMPT_TEMPLATES", DEFAULT_STRATEGY_SETTINGS["aiPromptTemplates"]),
        "aiPromptPolicy": value("aiPromptPolicy", "AI_PROMPT_POLICY", DEFAULT_STRATEGY_SETTINGS["aiPromptPolicy"]),
        "modelName": value("modelName", "MODEL_NAME", DEFAULT_STRATEGY_SETTINGS["modelName"]),
        "modelHypothesis": value("modelHypothesis", "MODEL_HYPOTHESIS", DEFAULT_STRATEGY_SETTINGS["modelHypothesis"]),
        "customBuyModelFormula": value("customBuyModelFormula", "CUSTOM_BUY_MODEL_FORMULA", DEFAULT_STRATEGY_SETTINGS["customBuyModelFormula"]),
        "customSellModelFormula": value("customSellModelFormula", "CUSTOM_SELL_MODEL_FORMULA", DEFAULT_STRATEGY_SETTINGS["customSellModelFormula"]),
        "formulaWeights": value("formulaWeights", "FORMULA_WEIGHTS", DEFAULT_STRATEGY_SETTINGS["formulaWeights"]),
        "decisionThresholds": value("decisionThresholds", "DECISION_THRESHOLDS", DEFAULT_STRATEGY_SETTINGS["decisionThresholds"]),
        "modelDecisionThresholds": value("modelDecisionThresholds", "MODEL_DECISION_THRESHOLDS", DEFAULT_STRATEGY_SETTINGS["modelDecisionThresholds"]),
        "modelTimingScenario": value("modelTimingScenario", "MODEL_TIMING_SCENARIO", "recent-one-year"),
        "modelTimingSymbols": value("modelTimingSymbols", "MODEL_TIMING_SYMBOLS", "NVDA,AAPL,005930,000660,TSLA"),
        "modelReviewCommand": value("modelReviewCommand", "MODEL_REVIEW_COMMAND", ""),
        "modelReviewUseCodex": value("modelReviewUseCodex", "MODEL_REVIEW_USE_CODEX", "1"),
        "modelReviewTimeoutSeconds": value("modelReviewTimeoutSeconds", "MODEL_REVIEW_TIMEOUT_SECONDS", "180"),
        "modelReviewIntervalSeconds": value("modelReviewIntervalSeconds", "MODEL_REVIEW_INTERVAL_SECONDS", "300"),
        "modelReviewBatchSize": value("modelReviewBatchSize", "MODEL_REVIEW_BATCH_SIZE", "1"),
        "modelReviewTelegramMode": value("modelReviewTelegramMode", "MODEL_REVIEW_TELEGRAM_MODE", "actionableOnly"),
        "ontologyTypeDbEnabled": value("ontologyTypeDbEnabled", "ONTOLOGY_TYPEDB_ENABLED", "1"),
        "ontologyReasoningEnabled": value("ontologyReasoningEnabled", "ONTOLOGY_REASONING_ENABLED", "1"),
        "ontologyReasoningIntervalSeconds": value("ontologyReasoningIntervalSeconds", "ONTOLOGY_REASONING_INTERVAL_SECONDS", "10"),
        "ontologyReasoningBatchSize": value("ontologyReasoningBatchSize", "ONTOLOGY_REASONING_BATCH_SIZE", "20"),
        "ontologyLabEnabled": value("ontologyLabEnabled", "ONTOLOGY_LAB_ENABLED", "1"),
        "ontologyLabIntervalSeconds": value("ontologyLabIntervalSeconds", "ONTOLOGY_LAB_INTERVAL_SECONDS", "300"),
        "ontologyLabBatchSize": value("ontologyLabBatchSize", "ONTOLOGY_LAB_BATCH_SIZE", "5"),
        "ontologyLabRunHistoryLimit": value("ontologyLabRunHistoryLimit", "ONTOLOGY_LAB_RUN_HISTORY_LIMIT", "50"),
        "ontologyRuleCandidateAiEnabled": value("ontologyRuleCandidateAiEnabled", "ONTOLOGY_RULE_CANDIDATE_AI_ENABLED", "1"),
        "ontologyRuleCandidateAiUseCodex": value("ontologyRuleCandidateAiUseCodex", "ONTOLOGY_RULE_CANDIDATE_AI_USE_CODEX", "1"),
        "ontologyRuleCandidateAiCommand": value("ontologyRuleCandidateAiCommand", "ONTOLOGY_RULE_CANDIDATE_AI_COMMAND", ""),
        "ontologyRuleCandidateAiTimeoutSeconds": value("ontologyRuleCandidateAiTimeoutSeconds", "ONTOLOGY_RULE_CANDIDATE_AI_TIMEOUT_SECONDS", "120"),
        "ontologyRuleCandidateAiIntervalMinutes": value("ontologyRuleCandidateAiIntervalMinutes", "ONTOLOGY_RULE_CANDIDATE_AI_INTERVAL_MINUTES", "60"),
        "ontologyRuleCandidateAiMaxCandidates": value("ontologyRuleCandidateAiMaxCandidates", "ONTOLOGY_RULE_CANDIDATE_AI_MAX_CANDIDATES", "3"),
        "materialityGateEnabled": value("materialityGateEnabled", "MATERIALITY_GATE_ENABLED", "1"),
        "materialityMinimumScore": value("materialityMinimumScore", "MATERIALITY_MINIMUM_SCORE", "65"),
        "marketMaterialityMinimumScore": value("marketMaterialityMinimumScore", "MARKET_MATERIALITY_MINIMUM_SCORE", "65"),
        "marketMaterialityPriceChangePct": value("marketMaterialityPriceChangePct", "MARKET_MATERIALITY_PRICE_CHANGE_PCT", "0.6"),
        "marketMaterialityTrendDistancePct": value("marketMaterialityTrendDistancePct", "MARKET_MATERIALITY_TREND_DISTANCE_PCT", "2"),
        "marketMaterialityVolumeRatio": value("marketMaterialityVolumeRatio", "MARKET_MATERIALITY_VOLUME_RATIO", "1.5"),
        "newsMaterialityMinimumScore": value("newsMaterialityMinimumScore", "NEWS_MATERIALITY_MINIMUM_SCORE", "65"),
        "typedbAddress": value("typedbAddress", "TYPEDB_ADDRESS", "127.0.0.1:1729"),
        "typedbUser": value("typedbUser", "TYPEDB_USER", "admin"),
        "typedbPassword": value("typedbPassword", "TYPEDB_PASSWORD", "password"),
        "typedbDatabase": value("typedbDatabase", "TYPEDB_DATABASE", "orbit_alpha_ontology"),
        "typedbTlsEnabled": value("typedbTlsEnabled", "TYPEDB_TLS_ENABLED", "0"),
        "typedbTimeoutSeconds": value("typedbTimeoutSeconds", "TYPEDB_TIMEOUT_SECONDS", "20"),
        "typedbRetryCount": value("typedbRetryCount", "TYPEDB_RETRY_COUNT", "2"),
        "typedbInferenceGenerationKeepCount": value("typedbInferenceGenerationKeepCount", "TYPEDB_INFERENCE_GENERATION_KEEP_COUNT", "1"),
        "typedbAutoResetEnabled": value("typedbAutoResetEnabled", "TYPEDB_AUTO_RESET_ENABLED", "1"),
        "typedbDataRetentionHours": value("typedbDataRetentionHours", "TYPEDB_DATA_RETENTION_HOURS", "24"),
        "typedbDataMaxSizeMb": value("typedbDataMaxSizeMb", "TYPEDB_DATA_MAX_SIZE_MB", "2048"),
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
        "dataFreshnessEnabled": value("dataFreshnessEnabled", "DATA_FRESHNESS_ENABLED", "1"),
        "dataFreshnessDefaultMaxAgeMinutes": value("dataFreshnessDefaultMaxAgeMinutes", "DATA_FRESHNESS_DEFAULT_MAX_AGE_MINUTES", "10"),
        "dataFreshnessQuoteMaxAgeMinutes": value("dataFreshnessQuoteMaxAgeMinutes", "DATA_FRESHNESS_QUOTE_MAX_AGE_MINUTES", "10"),
        "dataFreshnessKisPriceMaxAgeMinutes": value("dataFreshnessKisPriceMaxAgeMinutes", "DATA_FRESHNESS_KIS_PRICE_MAX_AGE_MINUTES", "3"),
        "dataFreshnessKisMicrostructureMaxAgeMinutes": value("dataFreshnessKisMicrostructureMaxAgeMinutes", "DATA_FRESHNESS_KIS_MICROSTRUCTURE_MAX_AGE_MINUTES", "2"),
        "dataFreshnessKisInvestorMaxAgeMinutes": value("dataFreshnessKisInvestorMaxAgeMinutes", "DATA_FRESHNESS_KIS_INVESTOR_MAX_AGE_MINUTES", "5"),
        "dataFreshnessExternalMaxAgeMinutes": value("dataFreshnessExternalMaxAgeMinutes", "DATA_FRESHNESS_EXTERNAL_MAX_AGE_MINUTES", "10"),
        "dataFreshnessExternalEquityMaxAgeMinutes": value("dataFreshnessExternalEquityMaxAgeMinutes", "DATA_FRESHNESS_EXTERNAL_EQUITY_MAX_AGE_MINUTES", "10"),
        "dataFreshnessExternalCryptoMaxAgeMinutes": value("dataFreshnessExternalCryptoMaxAgeMinutes", "DATA_FRESHNESS_EXTERNAL_CRYPTO_MAX_AGE_MINUTES", "10"),
        "dataFreshnessMacroMaxAgeMinutes": value("dataFreshnessMacroMaxAgeMinutes", "DATA_FRESHNESS_MACRO_MAX_AGE_MINUTES", "120"),
        "dataFreshnessDisclosureMaxAgeMinutes": value("dataFreshnessDisclosureMaxAgeMinutes", "DATA_FRESHNESS_DISCLOSURE_MAX_AGE_MINUTES", "120"),
        "externalApiFetchIntervalMinutes": value("externalApiFetchIntervalMinutes", "EXTERNAL_API_FETCH_INTERVAL_MINUTES", "30"),
        "externalSignalCacheMaxAgeMinutes": value("externalSignalCacheMaxAgeMinutes", "EXTERNAL_SIGNAL_CACHE_MAX_AGE_MINUTES", "10"),
        "externalAlphaEnabled": value("externalAlphaEnabled", "EXTERNAL_ALPHA_ENABLED", "1"),
        "externalAlphaRelatedSymbolsEnabled": value("externalAlphaRelatedSymbolsEnabled", "EXTERNAL_ALPHA_RELATED_SYMBOLS_ENABLED", "1"),
        "externalAlphaRelatedMaxSymbols": value("externalAlphaRelatedMaxSymbols", "EXTERNAL_ALPHA_RELATED_MAX_SYMBOLS", "8"),
        "externalCoinGeckoEnabled": value("externalCoinGeckoEnabled", "EXTERNAL_COINGECKO_ENABLED", "1"),
        "externalFredEnabled": value("externalFredEnabled", "EXTERNAL_FRED_ENABLED", "1"),
        "externalFredSeries": value("externalFredSeries", "EXTERNAL_FRED_SERIES", "DGS10,DGS2,DFF"),
        "externalCryptoIds": value("externalCryptoIds", "EXTERNAL_CRYPTO_IDS", "bitcoin,ethereum"),
        "externalAlphaMaxSymbols": value("externalAlphaMaxSymbols", "EXTERNAL_ALPHA_MAX_SYMBOLS", "3"),
        "externalAlphaRateLimitSeconds": value("externalAlphaRateLimitSeconds", "EXTERNAL_ALPHA_RATE_LIMIT_SECONDS", "15"),
        "externalAlphaFundamentalsEnabled": value("externalAlphaFundamentalsEnabled", "EXTERNAL_ALPHA_FUNDAMENTALS_ENABLED", "1"),
        "externalAlphaFundamentalsMaxSymbols": value("externalAlphaFundamentalsMaxSymbols", "EXTERNAL_ALPHA_FUNDAMENTALS_MAX_SYMBOLS", "1"),
        "securityLineMappings": value("securityLineMappings", "SECURITY_LINE_MAPPINGS"),
        "externalSecEnabled": value("externalSecEnabled", "EXTERNAL_SEC_ENABLED", "1"),
        "externalSecMaxSymbols": value("externalSecMaxSymbols", "EXTERNAL_SEC_MAX_SYMBOLS", "3"),
        "externalSecCompanyCiks": value("externalSecCompanyCiks", "EXTERNAL_SEC_COMPANY_CIKS", "AAPL=0000320193\nMSFT=0000789019\nNVDA=0001045810\nTSLA=0001318605\nAMD=0000002488\nMSTR=0001050446"),
        "externalSecUserAgent": value("externalSecUserAgent", "EXTERNAL_SEC_USER_AGENT", "DigitalTwin/1.0 local-contact"),
        "externalDartEnabled": value("externalDartEnabled", "EXTERNAL_DART_ENABLED", "1"),
        "externalDartLookbackDays": value("externalDartLookbackDays", "EXTERNAL_DART_LOOKBACK_DAYS", "14"),
        "externalDartCorpCodes": value("externalDartCorpCodes", "EXTERNAL_DART_CORP_CODES", "005930=00126380\n000660=00164779\n035420=00266961"),
        "externalNewsEnabled": value("externalNewsEnabled", "EXTERNAL_NEWS_ENABLED", "1"),
        "externalNewsProvider": value("externalNewsProvider", "EXTERNAL_NEWS_PROVIDER", "auto"),
        "externalNewsMaxSymbols": value("externalNewsMaxSymbols", "EXTERNAL_NEWS_MAX_SYMBOLS", "3"),
        "externalNewsLookbackHours": value("externalNewsLookbackHours", "EXTERNAL_NEWS_LOOKBACK_HOURS", "48"),
        "externalResearchEvidenceMaxItems": value("externalResearchEvidenceMaxItems", "EXTERNAL_RESEARCH_EVIDENCE_MAX_ITEMS", "8"),
        "newsCollectionEnabled": value("newsCollectionEnabled", "NEWS_COLLECTION_ENABLED", "1"),
        "newsCollectionIntervalSeconds": value("newsCollectionIntervalSeconds", "NEWS_COLLECTION_INTERVAL_SECONDS", "60"),
        "newsCollectionMaxSymbols": value("newsCollectionMaxSymbols", "NEWS_COLLECTION_MAX_SYMBOLS", "40"),
        "newsCollectionLookbackMinutes": value("newsCollectionLookbackMinutes", "NEWS_COLLECTION_LOOKBACK_MINUTES", "180"),
        "newsCollectionPerSymbolLimit": value("newsCollectionPerSymbolLimit", "NEWS_COLLECTION_PER_SYMBOL_LIMIT", "8"),
        "newsCollectionProviders": value("newsCollectionProviders", "NEWS_COLLECTION_PROVIDERS", "yahoo_finance,gdelt,google_rss_kr,google_rss_us"),
        "newsCollectionMinRelevanceScore": value("newsCollectionMinRelevanceScore", "NEWS_COLLECTION_MIN_RELEVANCE_SCORE", "35"),
        "newsCollectionRequireArticleBodyForRss": value("newsCollectionRequireArticleBodyForRss", "NEWS_COLLECTION_REQUIRE_ARTICLE_BODY_FOR_RSS", "1"),
        "newsCollectionIncludeWatchlist": value("newsCollectionIncludeWatchlist", "NEWS_COLLECTION_INCLUDE_WATCHLIST", "1"),
        "newsCollectionIncludeHoldings": value("newsCollectionIncludeHoldings", "NEWS_COLLECTION_INCLUDE_HOLDINGS", "1"),
        "newsCollectionRateLimitSeconds": value("newsCollectionRateLimitSeconds", "NEWS_COLLECTION_RATE_LIMIT_SECONDS", "0.25"),
        "newsCollectionTimeoutSeconds": value("newsCollectionTimeoutSeconds", "NEWS_COLLECTION_TIMEOUT_SECONDS", "8"),
        "newsCollectionProviderTimeoutSeconds": value("newsCollectionProviderTimeoutSeconds", "NEWS_COLLECTION_PROVIDER_TIMEOUT_SECONDS", "8"),
        "newsCollectionGdeltTimeoutSeconds": value("newsCollectionGdeltTimeoutSeconds", "NEWS_COLLECTION_GDELT_TIMEOUT_SECONDS", "4"),
        "newsEvidenceCleanupEnabled": value("newsEvidenceCleanupEnabled", "NEWS_EVIDENCE_CLEANUP_ENABLED", "1"),
        "newsEvidenceMaxAgeMinutes": value("newsEvidenceMaxAgeMinutes", "NEWS_EVIDENCE_MAX_AGE_MINUTES", "180"),
        "newsEvidenceCleanupBatchSize": value("newsEvidenceCleanupBatchSize", "NEWS_EVIDENCE_CLEANUP_BATCH_SIZE", "500"),
        "newsEvidenceKeepUndated": value("newsEvidenceKeepUndated", "NEWS_EVIDENCE_KEEP_UNDATED", "0"),
        "newsArticleBodyFailureWarnRate": value("newsArticleBodyFailureWarnRate", "NEWS_ARTICLE_BODY_FAILURE_WARN_RATE", "0.4"),
        "newsArticleBodyFailureMinimumCount": value("newsArticleBodyFailureMinimumCount", "NEWS_ARTICLE_BODY_FAILURE_MINIMUM_COUNT", "5"),
        "newsAiAnalysisEnabled": value("newsAiAnalysisEnabled", "NEWS_AI_ANALYSIS_ENABLED", "1"),
        "newsAiAnalysisUseCodex": value("newsAiAnalysisUseCodex", "NEWS_AI_ANALYSIS_USE_CODEX", "1"),
        "newsAiAnalysisCommand": value("newsAiAnalysisCommand", "NEWS_AI_ANALYSIS_COMMAND", ""),
        "newsAiAnalysisTimeoutSeconds": value("newsAiAnalysisTimeoutSeconds", "NEWS_AI_ANALYSIS_TIMEOUT_SECONDS", "90"),
        "investmentCalendarAutoExtractEnabled": value("investmentCalendarAutoExtractEnabled", "INVESTMENT_CALENDAR_AUTO_EXTRACT_ENABLED", "1"),
        "investmentCalendarAutoExtractRegisterUndated": value("investmentCalendarAutoExtractRegisterUndated", "INVESTMENT_CALENDAR_AUTO_EXTRACT_REGISTER_UNDATED", "0"),
        "investmentCalendarAutoExtractMinConfidence": value("investmentCalendarAutoExtractMinConfidence", "INVESTMENT_CALENDAR_AUTO_EXTRACT_MIN_CONFIDENCE", "0.45"),
        "investmentCalendarAutoExtractReviewEnabled": value("investmentCalendarAutoExtractReviewEnabled", "INVESTMENT_CALENDAR_AUTO_EXTRACT_REVIEW_ENABLED", "1"),
        "investmentCalendarAutoExtractReviewMinConfidence": value("investmentCalendarAutoExtractReviewMinConfidence", "INVESTMENT_CALENDAR_AUTO_EXTRACT_REVIEW_MIN_CONFIDENCE", "0.35"),
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
    if str(settings.get("lossCutScoreFormula") or "").strip() == LEGACY_LOSS_CUT_SCORE_FORMULA:
        settings["lossCutScoreFormula"] = DEFAULT_LOSS_CUT_SCORE_FORMULA
    settings["alertThresholds"] = synced_model_alert_thresholds(
        settings.get("alertThresholds", ""),
        settings.get("modelDecisionThresholds", ""),
    )
    return settings


def currency_rates(settings: Dict[str, str] = None) -> Dict[str, float]:
    settings = settings or runtime_settings()
    raw = str(settings.get("fxRates") or "").replace(";", "\n")
    rates = parse_assignments(raw, {"KRW": 1.0, "USD": 1400.0})
    return {str(key).upper(): float(value or 0) for key, value in rates.items()}
