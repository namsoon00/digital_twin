import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[2]
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


def configured(value: Optional[str]) -> str:
    return str(value or "").strip()


def settings_path() -> Path:
    explicit = os.environ.get("SETTINGS_PATH")
    if explicit:
        return (ROOT_DIR / explicit).resolve()
    return data_dir() / "settings.json"


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
        "notifyLinkUrl": value("notifyLinkUrl", "NOTIFY_LINK_URL", "http://127.0.0.1:3000?tab=alerts"),
        "notifyIntervalMinutes": value("notifyIntervalMinutes", "NOTIFY_INTERVAL_MINUTES", "10"),
        "alertRules": value("alertRules", "ALERT_RULES"),
        "alertThresholds": value("alertThresholds", "ALERT_THRESHOLDS"),
        "alertCadenceMinutes": value("alertCadenceMinutes", "ALERT_CADENCE_MINUTES"),
        "buyScoreFormula": value("buyScoreFormula", "BUY_SCORE_FORMULA", ""),
        "sellScoreFormula": value("sellScoreFormula", "SELL_SCORE_FORMULA", ""),
        "formulaWeights": value("formulaWeights", "FORMULA_WEIGHTS", ""),
        "modelDecisionThresholds": value("modelDecisionThresholds", "MODEL_DECISION_THRESHOLDS", ""),
    }


def parse_assignments(raw: str, defaults: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    values = dict(defaults or {})
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        separator = "=" if "=" in stripped else ":" if ":" in stripped else "," if "," in stripped else ""
        if not separator:
            continue
        key, raw_value = stripped.split(separator, 1)
        key = key.strip()
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        try:
            values[key] = float(raw_value.strip())
        except ValueError:
            values[key] = 0.0
    return values


def split_symbols(raw: str) -> List[str]:
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


@dataclass
class AccountConfig:
    account_id: str
    label: str
    provider: str
    base_url: str
    client_id: str
    client_secret: str
    account_seq: str
    watchlist_symbols: List[str]
    enabled: bool = True

    @classmethod
    def from_dict(cls, payload: Dict[str, object], settings: Dict[str, str]) -> "AccountConfig":
        return cls(
            account_id=configured(payload.get("id") or payload.get("accountId") or "default"),
            label=configured(payload.get("label") or payload.get("name") or payload.get("id") or "기본 계정"),
            provider=configured(payload.get("provider") or "toss"),
            base_url=configured(payload.get("baseUrl") or settings.get("tossApiBaseUrl") or "https://openapi.tossinvest.com"),
            client_id=configured(payload.get("clientId") or payload.get("client_id") or ""),
            client_secret=configured(payload.get("clientSecret") or payload.get("client_secret") or ""),
            account_seq=configured(payload.get("accountSeq") or payload.get("account_seq") or ""),
            watchlist_symbols=split_symbols(configured(payload.get("watchlistSymbols") or settings.get("watchlistSymbols"))),
            enabled=bool(payload.get("enabled", True)),
        )

    def to_private_dict(self) -> Dict[str, object]:
        return {
            "id": self.account_id,
            "label": self.label,
            "provider": self.provider,
            "baseUrl": self.base_url,
            "clientId": self.client_id,
            "clientSecret": self.client_secret,
            "accountSeq": self.account_seq,
            "watchlistSymbols": ",".join(self.watchlist_symbols),
            "enabled": self.enabled,
        }

    def masked(self) -> Dict[str, object]:
        return {
            "id": self.account_id,
            "label": self.label,
            "provider": self.provider,
            "baseUrl": self.base_url,
            "clientId": bool(self.client_id),
            "clientSecret": bool(self.client_secret),
            "accountSeq": self.account_seq,
            "watchlistSymbols": self.watchlist_symbols,
            "enabled": self.enabled,
        }


class AccountRegistry:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or data_dir() / "accounts.json"
        self.settings = runtime_settings()

    def load(self) -> List[AccountConfig]:
        payload = read_json(self.path, {})
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        if isinstance(accounts, list) and accounts:
            return [
                AccountConfig.from_dict(item, self.settings)
                for item in accounts
                if isinstance(item, dict) and item.get("enabled", True)
            ]
        return [self.default_account()]

    def load_all(self) -> List[AccountConfig]:
        payload = read_json(self.path, {})
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        if isinstance(accounts, list):
            return [AccountConfig.from_dict(item, self.settings) for item in accounts if isinstance(item, dict)]
        return [self.default_account()]

    def load_saved(self) -> List[AccountConfig]:
        payload = read_json(self.path, {})
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        if not isinstance(accounts, list):
            return []
        return [AccountConfig.from_dict(item, self.settings) for item in accounts if isinstance(item, dict)]

    def default_account(self) -> AccountConfig:
        return AccountConfig(
            account_id="default",
            label="기본 계정",
            provider="toss",
            base_url=self.settings.get("tossApiBaseUrl", "https://openapi.tossinvest.com"),
            client_id=self.settings.get("tossClientId", ""),
            client_secret=self.settings.get("tossClientSecret", ""),
            account_seq=self.settings.get("tossAccountSeq", ""),
            watchlist_symbols=split_symbols(self.settings.get("watchlistSymbols", "NVDA,TSLA,000660")),
            enabled=True,
        )

    def save_all(self, accounts: List[AccountConfig]) -> None:
        write_private_json(self.path, {
            "schemaVersion": 1,
            "accounts": [account.to_private_dict() for account in accounts],
        })

    def upsert(self, account: AccountConfig) -> None:
        accounts = [item for item in self.load_saved() if item.account_id != account.account_id]
        accounts.append(account)
        self.save_all(accounts)

    def remove(self, account_id: str) -> bool:
        accounts = self.load_saved()
        next_accounts = [item for item in accounts if item.account_id != account_id]
        if len(next_accounts) == len(accounts):
            return False
        self.save_all(next_accounts)
        return True
