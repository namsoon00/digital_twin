import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from ..domain.accounts import configured
from ..domain.ontology_rules import (
    DEFAULT_RELATION_THRESHOLDS,
    default_ai_prompt_policy_text,
    default_ai_prompt_templates_text,
    default_ontology_relation_rules_text,
)
from ..domain.parsing import parse_assignments


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = ROOT_DIR / "data"

TEXT_SETTING_KEYS = [
    "appTheme",
    "watchlistSymbols",
    "tossApiBaseUrl",
    "tossAccountSeq",
    "kisEnv",
    "kisBaseUrl",
    "kisMarketSignalsEnabled",
    "kisMarketSignalMaxSymbols",
    "kisMarketSignalCacheMinutes",
    "kisMarketSignalGapSeconds",
    "kisMarketSignalPreferLiveDuringMarketHours",
    "kisMarketSignalLiveRefreshSeconds",
    "notifyProvider",
    "telegramChatId",
    "notifyLinkUrl",
    "notifyIntervalMinutes",
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
    "ontologyNeo4jEnabled",
    "neo4jUri",
    "neo4jUser",
    "neo4jDatabase",
    "neo4jTimeoutSeconds",
    "dartDisclosureAiAnalysisEnabled",
    "dartDisclosureAiUseCodex",
    "dartDisclosureAiCommand",
    "dartDisclosureAiTimeoutSeconds",
    "notificationQueueIntervalSeconds",
    "notificationQueueBatchSize",
    "notificationSendGapSeconds",
    "symbolUniverseMaxAgeHours",
    "marketDataCollectionEnabled",
    "marketDataCollectionIntervalSeconds",
    "marketDataCollectionMarkets",
    "marketDataMaxAgeMinutes",
    "marketDataPriceBatchSize",
    "marketDataCandleBatchSize",
    "marketDataRefreshUniverse",
    "externalApiFetchIntervalMinutes",
    "externalAlphaEnabled",
    "externalCoinGeckoEnabled",
    "externalFredEnabled",
    "externalFredSeries",
    "externalCryptoIds",
    "externalAlphaMaxSymbols",
    "externalSecEnabled",
    "externalSecMaxSymbols",
    "externalSecCompanyCiks",
    "externalSecUserAgent",
    "externalDartEnabled",
    "externalDartLookbackDays",
    "externalDartCorpCodes",
    "externalNewsEnabled",
    "externalNewsMaxSymbols",
    "externalNewsLookbackHours",
    "externalApiRetryAttempts",
    "externalApiRateLimitSeconds",
    "externalApiCircuitFailures",
    "externalApiCircuitCooldownMinutes",
    "externalFredMaxSeries",
    "externalCryptoMaxIds",
    "externalDartMaxSymbols",
]

SECRET_SETTING_KEYS = [
    "tossClientId",
    "tossClientSecret",
    "telegramBotToken",
    "kisAppKey",
    "kisAppSecret",
    "kisAccountNo",
    "kisAccountProductCode",
    "alphaVantageApiKey",
    "coingeckoApiKey",
    "fredApiKey",
    "opendartApiKey",
    "neo4jPassword",
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
    ("modelBuy", 74),
    ("modelAdd", 70),
    ("modelSell", 72),
    ("modelReduce", 64),
    ("modelHold", 55),
]
DEFAULT_ALERT_THRESHOLDS = [
    ("modelBuyScore", 74),
    ("modelSellScore", 72),
    ("watchlistBuyScore", 74),
    ("modelScoreGap", 15),
    ("volumeRatioHigh", 2),
    ("buyShareHigh", 65),
    ("sellShareHigh", 65),
    ("orderbookImbalance", 25),
    ("momentumUp", 3),
    ("momentumDown", -3),
    ("marketCashLow", 10),
    ("recordGain", 10),
    ("recordLoss", -5),
    ("priceNearPercent", 1),
    ("staleMinutes", 30),
    ("pendingOrderMinutes", 30),
    ("watchlistPriceDelta", 3),
    ("monitorPnlDelta", 2),
    ("monitorValueDelta", 5),
    ("monitorMaDistance", 8),
    ("monitorCashDelta", 10),
    ("monitorExitPressureDelta", 15),
    ("externalEquityChangePct", 3),
    ("externalCryptoChange24hPct", 4),
    ("externalCryptoChange7dPct", 10),
    ("externalBitcoinChange24hPct", 3),
    ("externalBitcoinChange7dPct", 4),
    ("externalMacroRateDeltaBp", 15),
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
        "entryMa60SupportPct",
        "entryVolumeMinRatio",
        "entryVolumeMaxRatio",
        "entrySmartMoneyMin",
        "entryTradeStrengthMin",
        "entryOrderbookImbalanceMin",
        "entryMaxPositionWeight",
        "entryMaxSectorWeight",
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
    "ontologyRelationRules": default_ontology_relation_rules_text(),
    "aiPromptTemplates": default_ai_prompt_templates_text(),
    "aiPromptPolicy": default_ai_prompt_policy_text(),
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
    alerts["modelBuyScore"] = models.get("modelBuy", alerts.get("modelBuyScore", 0))
    alerts["watchlistBuyScore"] = models.get("modelBuy", alerts.get("watchlistBuyScore", alerts.get("modelBuyScore", 0)))
    alerts["modelSellScore"] = models.get("modelSell", alerts.get("modelSellScore", 0))
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


def service_db_path() -> Path:
    return Path(os.environ.get("DIGITAL_TWIN_SERVICE_DB", str(data_dir() / "service.db"))).resolve()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_settings_store() -> Dict[str, str]:
    try:
        from .sqlite_runtime import SQLiteRuntimeSettingsStore

        return SQLiteRuntimeSettingsStore().load()
    except Exception:
        return read_json(settings_path(), {})


def write_settings_store(settings: Dict[str, object]) -> Dict[str, str]:
    from .sqlite_runtime import SQLiteRuntimeSettingsStore

    clean = {str(key): str(value or "") for key, value in settings.items()}
    SQLiteRuntimeSettingsStore().replace(clean)
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
        "tossApiBaseUrl": value("tossApiBaseUrl", "TOSS_API_BASE_URL", "https://openapi.tossinvest.com"),
        "tossClientId": value("tossClientId", "TOSS_CLIENT_ID"),
        "tossClientSecret": value("tossClientSecret", "TOSS_CLIENT_SECRET"),
        "tossAccountSeq": value("tossAccountSeq", "TOSS_ACCOUNT_SEQ"),
        "kisEnv": value("kisEnv", "KIS_ENV", "prod"),
        "kisBaseUrl": value("kisBaseUrl", "KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
        "kisMarketSignalsEnabled": value("kisMarketSignalsEnabled", "KIS_MARKET_SIGNALS_ENABLED", "1"),
        "kisMarketSignalMaxSymbols": value("kisMarketSignalMaxSymbols", "KIS_MARKET_SIGNAL_MAX_SYMBOLS", "20"),
        "kisMarketSignalCacheMinutes": value("kisMarketSignalCacheMinutes", "KIS_MARKET_SIGNAL_CACHE_MINUTES", "3"),
        "kisMarketSignalGapSeconds": value("kisMarketSignalGapSeconds", "KIS_MARKET_SIGNAL_GAP_SECONDS", "0.35"),
        "kisMarketSignalPreferLiveDuringMarketHours": value("kisMarketSignalPreferLiveDuringMarketHours", "KIS_MARKET_SIGNAL_PREFER_LIVE_DURING_MARKET_HOURS", "1"),
        "kisMarketSignalLiveRefreshSeconds": value("kisMarketSignalLiveRefreshSeconds", "KIS_MARKET_SIGNAL_LIVE_REFRESH_SECONDS", "60"),
        "kisAppKey": value("kisAppKey", "KIS_APP_KEY"),
        "kisAppSecret": value("kisAppSecret", "KIS_APP_SECRET"),
        "kisAccountNo": value("kisAccountNo", "KIS_ACCOUNT_NO"),
        "kisAccountProductCode": value("kisAccountProductCode", "KIS_ACCOUNT_PRODUCT_CODE"),
        "notifyProvider": value("notifyProvider", "NOTIFY_PROVIDER"),
        "telegramBotToken": value("telegramBotToken", "TELEGRAM_BOT_TOKEN"),
        "telegramChatId": value("telegramChatId", "TELEGRAM_CHAT_ID"),
        "notifyLinkUrl": value("notifyLinkUrl", "NOTIFY_LINK_URL", "http://127.0.0.1:3000?tab=notifications"),
        "notifyIntervalMinutes": value("notifyIntervalMinutes", "NOTIFY_INTERVAL_MINUTES", "10"),
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
        "ontologyNeo4jEnabled": value("ontologyNeo4jEnabled", "ONTOLOGY_NEO4J_ENABLED", "1"),
        "neo4jUri": value("neo4jUri", "NEO4J_URI", ""),
        "neo4jUser": value("neo4jUser", "NEO4J_USER", "neo4j"),
        "neo4jPassword": value("neo4jPassword", "NEO4J_PASSWORD", ""),
        "neo4jDatabase": value("neo4jDatabase", "NEO4J_DATABASE", "neo4j"),
        "neo4jTimeoutSeconds": value("neo4jTimeoutSeconds", "NEO4J_TIMEOUT_SECONDS", "8"),
        "dartDisclosureAiAnalysisEnabled": value("dartDisclosureAiAnalysisEnabled", "DART_DISCLOSURE_AI_ANALYSIS_ENABLED", "1"),
        "dartDisclosureAiUseCodex": value("dartDisclosureAiUseCodex", "DART_DISCLOSURE_AI_USE_CODEX", "1"),
        "dartDisclosureAiCommand": value("dartDisclosureAiCommand", "DART_DISCLOSURE_AI_COMMAND", ""),
        "dartDisclosureAiTimeoutSeconds": value("dartDisclosureAiTimeoutSeconds", "DART_DISCLOSURE_AI_TIMEOUT_SECONDS", "90"),
        "notificationQueueIntervalSeconds": value("notificationQueueIntervalSeconds", "NOTIFICATION_QUEUE_INTERVAL_SECONDS", "30"),
        "notificationQueueBatchSize": value("notificationQueueBatchSize", "NOTIFICATION_QUEUE_BATCH_SIZE", "10"),
        "notificationSendGapSeconds": value("notificationSendGapSeconds", "NOTIFICATION_SEND_GAP_SECONDS", "1"),
        "symbolUniverseMaxAgeHours": value("symbolUniverseMaxAgeHours", "SYMBOL_UNIVERSE_MAX_AGE_HOURS", "24"),
        "marketDataCollectionEnabled": value("marketDataCollectionEnabled", "MARKET_DATA_COLLECTION_ENABLED", "1"),
        "marketDataCollectionIntervalSeconds": value("marketDataCollectionIntervalSeconds", "MARKET_DATA_COLLECTION_INTERVAL_SECONDS", "180"),
        "marketDataCollectionMarkets": value("marketDataCollectionMarkets", "MARKET_DATA_COLLECTION_MARKETS", "KOSPI,KOSDAQ,NASDAQ"),
        "marketDataMaxAgeMinutes": value("marketDataMaxAgeMinutes", "MARKET_DATA_MAX_AGE_MINUTES", "240"),
        "marketDataPriceBatchSize": value("marketDataPriceBatchSize", "MARKET_DATA_PRICE_BATCH_SIZE", "200"),
        "marketDataCandleBatchSize": value("marketDataCandleBatchSize", "MARKET_DATA_CANDLE_BATCH_SIZE", "25"),
        "marketDataRefreshUniverse": value("marketDataRefreshUniverse", "MARKET_DATA_REFRESH_UNIVERSE", "1"),
        "externalApiFetchIntervalMinutes": value("externalApiFetchIntervalMinutes", "EXTERNAL_API_FETCH_INTERVAL_MINUTES", "30"),
        "externalAlphaEnabled": value("externalAlphaEnabled", "EXTERNAL_ALPHA_ENABLED", "1"),
        "externalCoinGeckoEnabled": value("externalCoinGeckoEnabled", "EXTERNAL_COINGECKO_ENABLED", "1"),
        "externalFredEnabled": value("externalFredEnabled", "EXTERNAL_FRED_ENABLED", "1"),
        "externalFredSeries": value("externalFredSeries", "EXTERNAL_FRED_SERIES", "DGS10,DGS2,DFF"),
        "externalCryptoIds": value("externalCryptoIds", "EXTERNAL_CRYPTO_IDS", "bitcoin,ethereum"),
        "externalAlphaMaxSymbols": value("externalAlphaMaxSymbols", "EXTERNAL_ALPHA_MAX_SYMBOLS", "3"),
        "externalSecEnabled": value("externalSecEnabled", "EXTERNAL_SEC_ENABLED", "1"),
        "externalSecMaxSymbols": value("externalSecMaxSymbols", "EXTERNAL_SEC_MAX_SYMBOLS", "3"),
        "externalSecCompanyCiks": value("externalSecCompanyCiks", "EXTERNAL_SEC_COMPANY_CIKS", "AAPL=0000320193\nMSFT=0000789019\nNVDA=0001045810\nTSLA=0001318605\nAMD=0000002488\nMSTR=0001050446"),
        "externalSecUserAgent": value("externalSecUserAgent", "EXTERNAL_SEC_USER_AGENT", "DigitalTwin/1.0 local-contact"),
        "externalDartEnabled": value("externalDartEnabled", "EXTERNAL_DART_ENABLED", "1"),
        "externalDartLookbackDays": value("externalDartLookbackDays", "EXTERNAL_DART_LOOKBACK_DAYS", "14"),
        "externalDartCorpCodes": value("externalDartCorpCodes", "EXTERNAL_DART_CORP_CODES", "005930=00126380\n000660=00164779\n035420=00266961"),
        "externalNewsEnabled": value("externalNewsEnabled", "EXTERNAL_NEWS_ENABLED", "1"),
        "externalNewsMaxSymbols": value("externalNewsMaxSymbols", "EXTERNAL_NEWS_MAX_SYMBOLS", "3"),
        "externalNewsLookbackHours": value("externalNewsLookbackHours", "EXTERNAL_NEWS_LOOKBACK_HOURS", "48"),
        "externalApiRetryAttempts": value("externalApiRetryAttempts", "EXTERNAL_API_RETRY_ATTEMPTS", "2"),
        "externalApiRateLimitSeconds": value("externalApiRateLimitSeconds", "EXTERNAL_API_RATE_LIMIT_SECONDS", "60"),
        "externalApiCircuitFailures": value("externalApiCircuitFailures", "EXTERNAL_API_CIRCUIT_FAILURES", "2"),
        "externalApiCircuitCooldownMinutes": value("externalApiCircuitCooldownMinutes", "EXTERNAL_API_CIRCUIT_COOLDOWN_MINUTES", "30"),
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
