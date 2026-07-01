import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from ..domain.accounts import configured
from ..domain.parsing import parse_assignments


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = ROOT_DIR / "data"


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


def runtime_settings() -> Dict[str, str]:
    load_local_env()
    store = read_json(settings_path(), {})

    def value(key: str, env_name: str, fallback: str = "") -> str:
        stored = configured(store.get(key))
        if stored:
            return stored
        env_value = configured(os.environ.get(env_name))
        if env_value:
            return env_value
        return fallback

    return {
        "watchlistSymbols": value("watchlistSymbols", "WATCHLIST_SYMBOLS", "NVDA,TSLA,000660"),
        "tossApiBaseUrl": value("tossApiBaseUrl", "TOSS_API_BASE_URL", "https://openapi.tossinvest.com"),
        "tossClientId": value("tossClientId", "TOSS_CLIENT_ID"),
        "tossClientSecret": value("tossClientSecret", "TOSS_CLIENT_SECRET"),
        "tossAccountSeq": value("tossAccountSeq", "TOSS_ACCOUNT_SEQ"),
        "notifyProvider": value("notifyProvider", "NOTIFY_PROVIDER"),
        "telegramBotToken": value("telegramBotToken", "TELEGRAM_BOT_TOKEN"),
        "telegramChatId": value("telegramChatId", "TELEGRAM_CHAT_ID"),
        "notifyLinkUrl": value("notifyLinkUrl", "NOTIFY_LINK_URL", "http://127.0.0.1:3000?tab=notifications"),
        "notifyIntervalMinutes": value("notifyIntervalMinutes", "NOTIFY_INTERVAL_MINUTES", "10"),
        "alertRules": value("alertRules", "ALERT_RULES"),
        "alertThresholds": value("alertThresholds", "ALERT_THRESHOLDS"),
        "alertCadenceMinutes": value("alertCadenceMinutes", "ALERT_CADENCE_MINUTES"),
        "buyScoreFormula": value("buyScoreFormula", "BUY_SCORE_FORMULA", ""),
        "sellScoreFormula": value("sellScoreFormula", "SELL_SCORE_FORMULA", ""),
        "formulaWeights": value("formulaWeights", "FORMULA_WEIGHTS", ""),
        "modelDecisionThresholds": value("modelDecisionThresholds", "MODEL_DECISION_THRESHOLDS", ""),
        "modelTimingScenario": value("modelTimingScenario", "MODEL_TIMING_SCENARIO", "recent-one-year"),
        "modelTimingSymbols": value("modelTimingSymbols", "MODEL_TIMING_SYMBOLS", "NVDA,AAPL,005930,000660,TSLA"),
        "modelReviewCommand": value("modelReviewCommand", "MODEL_REVIEW_COMMAND", ""),
        "modelReviewUseCodex": value("modelReviewUseCodex", "MODEL_REVIEW_USE_CODEX", "1"),
        "modelReviewTimeoutSeconds": value("modelReviewTimeoutSeconds", "MODEL_REVIEW_TIMEOUT_SECONDS", "180"),
        "modelReviewIntervalSeconds": value("modelReviewIntervalSeconds", "MODEL_REVIEW_INTERVAL_SECONDS", "300"),
        "modelReviewBatchSize": value("modelReviewBatchSize", "MODEL_REVIEW_BATCH_SIZE", "1"),
        "fxRates": value("fxRates", "FX_RATES", "KRW=1\nUSD=1400"),
    }


def currency_rates(settings: Dict[str, str] = None) -> Dict[str, float]:
    settings = settings or runtime_settings()
    raw = str(settings.get("fxRates") or "").replace(";", "\n")
    rates = parse_assignments(raw, {"KRW": 1.0, "USD": 1400.0})
    return {str(key).upper(): float(value or 0) for key, value in rates.items()}
