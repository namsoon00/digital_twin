"""Account identity needed by instrument lists, without credentials or delivery settings."""

from dataclasses import dataclass, field
from typing import Dict, List, Protocol


@dataclass
class WatchlistAccount:
    account_id: str
    label: str
    provider: str
    watchlist_symbols: List[str] = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def masked(self) -> Dict[str, object]:
        return {
            "id": self.account_id,
            "label": self.label,
            "provider": self.provider,
            "watchlistSymbols": list(self.watchlist_symbols),
            "enabled": self.enabled,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class WatchlistAccountReader(Protocol):
    def load_saved(self) -> List[WatchlistAccount]: ...
    def load_all(self) -> List[WatchlistAccount]: ...
