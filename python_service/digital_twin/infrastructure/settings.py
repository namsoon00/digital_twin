import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from ..domain.accounts import configured
from ..domain.parsing import parse_assignments


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = ROOT_DIR / "data"

TEXT_SETTING_KEYS = [
    "appTheme",
    "watchlistSymbols",
    "tossApiBaseUrl",
    "tossAccountSeq",
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
    "alertCadenceMinutes",
    "modelReviewUseCodex",
    "modelReviewCommand",
    "modelReviewTimeoutSeconds",
    "modelReviewIntervalSeconds",
    "modelReviewBatchSize",
    "notificationQueueIntervalSeconds",
    "notificationQueueBatchSize",
    "notificationSendGapSeconds",
    "symbolUniverseMaxAgeHours",
    "externalApiFetchIntervalMinutes",
    "externalFredSeries",
    "externalCryptoIds",
    "externalAlphaMaxSymbols",
    "externalDartLookbackDays",
    "externalDartCorpCodes",
]

SECRET_SETTING_KEYS = [
    "tossClientId",
    "tossClientSecret",
    "telegramBotToken",
    "alphaVantageApiKey",
    "coingeckoApiKey",
    "fredApiKey",
    "opendartApiKey",
]


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

    return {
        "appTheme": value("appTheme", "APP_THEME", "light"),
        "watchlistSymbols": value("watchlistSymbols", "WATCHLIST_SYMBOLS", "TSLA,AAPL,NVDA,000660"),
        "tossApiBaseUrl": value("tossApiBaseUrl", "TOSS_API_BASE_URL", "https://openapi.tossinvest.com"),
        "tossClientId": value("tossClientId", "TOSS_CLIENT_ID"),
        "tossClientSecret": value("tossClientSecret", "TOSS_CLIENT_SECRET"),
        "tossAccountSeq": value("tossAccountSeq", "TOSS_ACCOUNT_SEQ"),
        "notifyProvider": value("notifyProvider", "NOTIFY_PROVIDER"),
        "telegramBotToken": value("telegramBotToken", "TELEGRAM_BOT_TOKEN"),
        "telegramChatId": value("telegramChatId", "TELEGRAM_CHAT_ID"),
        "notifyLinkUrl": value("notifyLinkUrl", "NOTIFY_LINK_URL", "http://127.0.0.1:3000?tab=notifications"),
        "notifyIntervalMinutes": value("notifyIntervalMinutes", "NOTIFY_INTERVAL_MINUTES", "10"),
        "valuationAssumptions": value("valuationAssumptions", "VALUATION_ASSUMPTIONS", ""),
        "marketSignalInputs": value("marketSignalInputs", "MARKET_SIGNAL_INPUTS", ""),
        "fairValueFormula": value("fairValueFormula", "FAIR_VALUE_FORMULA", ""),
        "alertRules": value("alertRules", "ALERT_RULES"),
        "alertThresholds": value("alertThresholds", "ALERT_THRESHOLDS"),
        "alertCadenceMinutes": value("alertCadenceMinutes", "ALERT_CADENCE_MINUTES"),
        "buyScoreFormula": value("buyScoreFormula", "BUY_SCORE_FORMULA", ""),
        "sellScoreFormula": value("sellScoreFormula", "SELL_SCORE_FORMULA", ""),
        "modelName": value("modelName", "MODEL_NAME", ""),
        "modelHypothesis": value("modelHypothesis", "MODEL_HYPOTHESIS", ""),
        "customBuyModelFormula": value("customBuyModelFormula", "CUSTOM_BUY_MODEL_FORMULA", ""),
        "customSellModelFormula": value("customSellModelFormula", "CUSTOM_SELL_MODEL_FORMULA", ""),
        "formulaWeights": value("formulaWeights", "FORMULA_WEIGHTS", ""),
        "decisionThresholds": value("decisionThresholds", "DECISION_THRESHOLDS", ""),
        "modelDecisionThresholds": value("modelDecisionThresholds", "MODEL_DECISION_THRESHOLDS", ""),
        "modelTimingScenario": value("modelTimingScenario", "MODEL_TIMING_SCENARIO", "recent-one-year"),
        "modelTimingSymbols": value("modelTimingSymbols", "MODEL_TIMING_SYMBOLS", "NVDA,AAPL,005930,000660,TSLA"),
        "modelReviewCommand": value("modelReviewCommand", "MODEL_REVIEW_COMMAND", ""),
        "modelReviewUseCodex": value("modelReviewUseCodex", "MODEL_REVIEW_USE_CODEX", "1"),
        "modelReviewTimeoutSeconds": value("modelReviewTimeoutSeconds", "MODEL_REVIEW_TIMEOUT_SECONDS", "180"),
        "modelReviewIntervalSeconds": value("modelReviewIntervalSeconds", "MODEL_REVIEW_INTERVAL_SECONDS", "300"),
        "modelReviewBatchSize": value("modelReviewBatchSize", "MODEL_REVIEW_BATCH_SIZE", "1"),
        "notificationQueueIntervalSeconds": value("notificationQueueIntervalSeconds", "NOTIFICATION_QUEUE_INTERVAL_SECONDS", "30"),
        "notificationQueueBatchSize": value("notificationQueueBatchSize", "NOTIFICATION_QUEUE_BATCH_SIZE", "10"),
        "notificationSendGapSeconds": value("notificationSendGapSeconds", "NOTIFICATION_SEND_GAP_SECONDS", "1"),
        "symbolUniverseMaxAgeHours": value("symbolUniverseMaxAgeHours", "SYMBOL_UNIVERSE_MAX_AGE_HOURS", "24"),
        "externalApiFetchIntervalMinutes": value("externalApiFetchIntervalMinutes", "EXTERNAL_API_FETCH_INTERVAL_MINUTES", "60"),
        "externalFredSeries": value("externalFredSeries", "EXTERNAL_FRED_SERIES", "DGS10,DGS2,DFF"),
        "externalCryptoIds": value("externalCryptoIds", "EXTERNAL_CRYPTO_IDS", "bitcoin,ethereum"),
        "externalAlphaMaxSymbols": value("externalAlphaMaxSymbols", "EXTERNAL_ALPHA_MAX_SYMBOLS", "3"),
        "externalDartLookbackDays": value("externalDartLookbackDays", "EXTERNAL_DART_LOOKBACK_DAYS", "14"),
        "externalDartCorpCodes": value("externalDartCorpCodes", "EXTERNAL_DART_CORP_CODES", "005930=00126380\n000660=00164779\n035420=00266961"),
        "alphaVantageApiKey": value("alphaVantageApiKey", "ALPHA_VANTAGE_API_KEY"),
        "coingeckoApiKey": value("coingeckoApiKey", "COINGECKO_API_KEY"),
        "fredApiKey": value("fredApiKey", "FRED_API_KEY"),
        "opendartApiKey": value("opendartApiKey", "OPENDART_API_KEY"),
        "fxRates": value("fxRates", "FX_RATES", "KRW=1\nUSD=1400"),
    }


def currency_rates(settings: Dict[str, str] = None) -> Dict[str, float]:
    settings = settings or runtime_settings()
    raw = str(settings.get("fxRates") or "").replace(";", "\n")
    rates = parse_assignments(raw, {"KRW": 1.0, "USD": 1400.0})
    return {str(key).upper(): float(value or 0) for key, value in rates.items()}
