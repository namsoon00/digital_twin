"""Credential-aware account reads and atomic commands, owned by accounts."""

from typing import Iterable, List, Protocol

from digital_twin.domain.events import DomainEvent
from .configuration import AccountConfig


class AccountReader(Protocol):
    def load(self) -> List[AccountConfig]: ...
    def load_all(self) -> List[AccountConfig]: ...
    def load_saved(self) -> List[AccountConfig]: ...


class AccountRepository(AccountReader, Protocol):
    def upsert_with_event(self, account: AccountConfig, event: DomainEvent) -> None: ...
    def patch_with_event(
        self, account: AccountConfig, fields: Iterable[str], event: DomainEvent
    ) -> None: ...
    def remove_with_event(self, account_id: str, event: DomainEvent) -> bool: ...
