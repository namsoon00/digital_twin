import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
    notify_provider: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_link_url: str = ""
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
            notify_provider=configured(payload.get("notifyProvider") or payload.get("notify_provider") or settings.get("notifyProvider")),
            telegram_bot_token=configured(payload.get("telegramBotToken") or payload.get("telegram_bot_token") or settings.get("telegramBotToken")),
            telegram_chat_id=configured(payload.get("telegramChatId") or payload.get("telegram_chat_id") or settings.get("telegramChatId")),
            notify_link_url=configured(payload.get("notifyLinkUrl") or payload.get("notify_link_url") or settings.get("notifyLinkUrl")),
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
            "notifyProvider": self.notify_provider,
            "telegramBotToken": self.telegram_bot_token,
            "telegramChatId": self.telegram_chat_id,
            "notifyLinkUrl": self.notify_link_url,
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
            "notifyProvider": self.notify_provider,
            "telegramBotToken": bool(self.telegram_bot_token),
            "telegramChatId": bool(self.telegram_chat_id),
            "notifyLinkUrl": self.notify_link_url,
            "enabled": self.enabled,
        }


class AccountRegistry:
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.path = path or service_db_path()
        self.legacy_path = legacy_path or data_dir() / "accounts.json"
        self.settings = runtime_settings()
        self.ensure_schema()
        self.import_legacy_accounts_if_needed()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return connection

    def ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS service_accounts (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'toss',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    watchlist_symbols TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS toss_credentials (
                    account_id TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL DEFAULT '',
                    client_id TEXT NOT NULL DEFAULT '',
                    client_secret TEXT NOT NULL DEFAULT '',
                    account_seq TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES service_accounts(id) ON DELETE CASCADE
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS telegram_configs (
                    account_id TEXT PRIMARY KEY,
                    notify_provider TEXT NOT NULL DEFAULT '',
                    bot_token TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL DEFAULT '',
                    link_url TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES service_accounts(id) ON DELETE CASCADE
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_service_accounts_enabled ON service_accounts(enabled)")

    def rows_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM service_accounts").fetchone()
            return int(row["count"] if row else 0)

    def import_legacy_accounts_if_needed(self) -> None:
        if self.rows_count() > 0 or not self.legacy_path.exists():
            return
        payload = read_json(self.legacy_path, {})
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        if not isinstance(accounts, list) or not accounts:
            return
        for item in accounts:
            if isinstance(item, dict):
                self.upsert(AccountConfig.from_dict(item, self.settings))

    def account_from_row(self, row) -> AccountConfig:
        watchlist = row["watchlist_symbols"] if row["watchlist_symbols"] else self.settings.get("watchlistSymbols", "")
        return AccountConfig(
            account_id=row["id"],
            label=row["label"],
            provider=row["provider"],
            base_url=row["base_url"] or self.settings.get("tossApiBaseUrl", "https://openapi.tossinvest.com"),
            client_id=row["client_id"] or "",
            client_secret=row["client_secret"] or "",
            account_seq=row["account_seq"] or "",
            watchlist_symbols=split_symbols(watchlist),
            notify_provider=row["notify_provider"] or self.settings.get("notifyProvider", ""),
            telegram_bot_token=row["bot_token"] or self.settings.get("telegramBotToken", ""),
            telegram_chat_id=row["chat_id"] or self.settings.get("telegramChatId", ""),
            notify_link_url=row["link_url"] or self.settings.get("notifyLinkUrl", ""),
            enabled=bool(row["enabled"]),
        )

    def select_accounts(self, enabled_only: bool) -> List[AccountConfig]:
        sql = """
            SELECT
                a.id,
                a.label,
                a.provider,
                a.enabled,
                a.watchlist_symbols,
                COALESCE(t.base_url, '') AS base_url,
                COALESCE(t.client_id, '') AS client_id,
                COALESCE(t.client_secret, '') AS client_secret,
                COALESCE(t.account_seq, '') AS account_seq,
                COALESCE(g.notify_provider, '') AS notify_provider,
                COALESCE(g.bot_token, '') AS bot_token,
                COALESCE(g.chat_id, '') AS chat_id,
                COALESCE(g.link_url, '') AS link_url
            FROM service_accounts a
            LEFT JOIN toss_credentials t ON t.account_id = a.id
            LEFT JOIN telegram_configs g ON g.account_id = a.id
        """
        params = []
        if enabled_only:
            sql += " WHERE a.enabled = ?"
            params.append(1)
        sql += " ORDER BY a.created_at, a.id"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.account_from_row(row) for row in rows]

    def load(self) -> List[AccountConfig]:
        accounts = self.select_accounts(enabled_only=True)
        if accounts:
            return accounts
        return [self.default_account()]

    def load_all(self) -> List[AccountConfig]:
        accounts = self.select_accounts(enabled_only=False)
        if accounts:
            return accounts
        return [self.default_account()]

    def load_saved(self) -> List[AccountConfig]:
        return self.select_accounts(enabled_only=False)

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
            notify_provider=self.settings.get("notifyProvider", ""),
            telegram_bot_token=self.settings.get("telegramBotToken", ""),
            telegram_chat_id=self.settings.get("telegramChatId", ""),
            notify_link_url=self.settings.get("notifyLinkUrl", ""),
            enabled=True,
        )

    def save_all(self, accounts: List[AccountConfig]) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM telegram_configs")
            connection.execute("DELETE FROM toss_credentials")
            connection.execute("DELETE FROM service_accounts")
            for account in accounts:
                self.upsert_with_connection(connection, account)

    def upsert_with_connection(self, connection, account: AccountConfig) -> None:
        stamp = utc_now()
        existing = connection.execute("SELECT created_at FROM service_accounts WHERE id = ?", (account.account_id,)).fetchone()
        connection.execute(
            """
            INSERT INTO service_accounts (id, label, provider, enabled, watchlist_symbols, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label = excluded.label,
                provider = excluded.provider,
                enabled = excluded.enabled,
                watchlist_symbols = excluded.watchlist_symbols,
                updated_at = excluded.updated_at
            """,
            (
                account.account_id,
                account.label,
                account.provider,
                1 if account.enabled else 0,
                ",".join(account.watchlist_symbols),
                existing["created_at"] if existing else stamp,
                stamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO toss_credentials (account_id, base_url, client_id, client_secret, account_seq, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                base_url = excluded.base_url,
                client_id = excluded.client_id,
                client_secret = excluded.client_secret,
                account_seq = excluded.account_seq,
                updated_at = excluded.updated_at
            """,
            (account.account_id, account.base_url, account.client_id, account.client_secret, account.account_seq, stamp),
        )
        connection.execute(
            """
            INSERT INTO telegram_configs (account_id, notify_provider, bot_token, chat_id, link_url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                notify_provider = excluded.notify_provider,
                bot_token = excluded.bot_token,
                chat_id = excluded.chat_id,
                link_url = excluded.link_url,
                updated_at = excluded.updated_at
            """,
            (
                account.account_id,
                account.notify_provider,
                account.telegram_bot_token,
                account.telegram_chat_id,
                account.notify_link_url,
                stamp,
            ),
        )

    def upsert(self, account: AccountConfig) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self.upsert_with_connection(connection, account)

    def remove(self, account_id: str) -> bool:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute("DELETE FROM service_accounts WHERE id = ?", (account_id,))
            removed = cursor.rowcount > 0
        if not removed:
            return False
        return True
