from datetime import datetime, timezone
from typing import Dict, Optional, Protocol


class RuntimeCheckpointStore(Protocol):
    def load(self, checkpoint_id: str) -> Dict[str, object]:
        ...

    def save(self, checkpoint_id: str, payload: Dict[str, object]) -> None:
        ...


def checkpoint_datetime(payload: Dict[str, object], key: str) -> Optional[datetime]:
    text = str((payload or {}).get(key) or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def checkpoint_iso(value: datetime) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
