from datetime import datetime, timezone
from typing import Dict, Iterable

from ..domain.portfolio import AccountSnapshot, AlertEvent
from .settings import data_dir, read_json, write_private_json


class MonitorStore:
    def __init__(self, path=None):
        self.path = path or data_dir() / "python-monitor-state.json"
        self.payload = read_json(self.path, {"previous": {}, "sent": {}})
        if not isinstance(self.payload, dict):
            self.payload = {"previous": {}, "sent": {}}
        self.payload.setdefault("previous", {})
        self.payload.setdefault("sent", {})

    @property
    def previous(self) -> Dict[str, object]:
        return self.payload["previous"]

    @property
    def sent(self) -> Dict[str, object]:
        return self.payload["sent"]

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        self.previous[snapshot.account_id] = snapshot.to_monitor_state()

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for event in events:
            self.sent[event.key] = stamp
            self.sent[event.cadence_key()] = stamp

    def write(self) -> None:
        write_private_json(self.path, self.payload)

