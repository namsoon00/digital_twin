"""Web common boundary."""

from datetime import datetime
from datetime import timezone
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.modules.portfolio.domain.portfolio import utc_now_iso
from typing import Dict
from typing import List
import uuid


def now() -> str:
    return utc_now_iso()


def operational_read_settings() -> Dict[str, object]:
    """Return runtime settings for a read-only web request.

    History retention is a scheduled maintenance concern.  Running it while a
    screen is opening turns a simple read into an unbounded write path and can
    leave the first Flow Lens response waiting on a busy MySQL connection.
    """
    settings = dict(runtime_settings(fast_operational_read=True))
    settings["_skipOperationalHistoryRetention"] = "1"
    settings["_skipOperationalSchemaBootstrap"] = "1"
    settings["_skipNotificationRuleDefaultsSeed"] = "1"
    return settings


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:16]


def configured(value) -> str:
    return str(value or "").strip()


def request_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def safe_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def parse_utc(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def first_query(query: Dict[str, List[str]], key: str) -> str:
    value = query.get(key)
    if isinstance(value, list):
        return value[0] if value else ""
    return str(value or "")
