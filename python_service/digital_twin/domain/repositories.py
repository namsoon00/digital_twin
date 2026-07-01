from typing import Dict, Iterable, List, Protocol

from .accounts import AccountConfig
from .portfolio import AccountSnapshot, AlertEvent


class AccountRepository(Protocol):
    def load(self) -> List[AccountConfig]:
        ...

    def load_all(self) -> List[AccountConfig]:
        ...

    def load_saved(self) -> List[AccountConfig]:
        ...

    def upsert(self, account: AccountConfig) -> None:
        ...

    def remove(self, account_id: str) -> bool:
        ...


class SnapshotProvider(Protocol):
    def build_snapshot(self, account: AccountConfig) -> AccountSnapshot:
        ...


class MonitorStateRepository(Protocol):
    @property
    def previous(self) -> Dict[str, object]:
        ...

    @property
    def sent(self) -> Dict[str, object]:
        ...

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        ...

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        ...

    def write(self) -> None:
        ...


class SnapshotMonitor(Protocol):
    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        ...

    def apply_cadence(self, events: List[AlertEvent], store: MonitorStateRepository, force: bool = False) -> List[AlertEvent]:
        ...


class NotificationGateway(Protocol):
    def send_events(self, events: List[AlertEvent], dry_run: bool = False, accounts=None):
        ...
