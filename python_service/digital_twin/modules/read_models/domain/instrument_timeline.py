"""Domain vocabulary for one instrument's price and event timeline."""

from dataclasses import dataclass
from typing import Dict


TIMELINE_RANGES: Dict[str, Dict[str, object]] = {
    "1d": {"interval": "15m", "limit": 96},
    "1w": {"interval": "1h", "limit": 168},
    "1m": {"interval": "1d", "limit": 31},
    "3m": {"interval": "1d", "limit": 92},
    "6m": {"interval": "1d", "limit": 184},
    "1y": {"interval": "1d", "limit": 366},
    "3y": {"interval": "1d", "limit": 780},
    "all": {"interval": "1d", "limit": 1000},
}
TIMELINE_INTERVALS = {"3m", "15m", "1h", "1d"}


def normalize_instrument_symbol(value: object) -> str:
    return str(value or "").upper().strip()[:32]


@dataclass(frozen=True)
class InstrumentTimelineQuery:
    symbol: str
    account_id: str = ""
    range_key: str = "3m"
    interval: str = ""

    def normalized(self) -> "InstrumentTimelineQuery":
        range_key = str(self.range_key or "3m").strip().lower()
        if range_key not in TIMELINE_RANGES:
            range_key = "3m"
        interval = str(self.interval or TIMELINE_RANGES[range_key]["interval"]).strip().lower()
        if interval not in TIMELINE_INTERVALS:
            interval = str(TIMELINE_RANGES[range_key]["interval"])
        return InstrumentTimelineQuery(
            symbol=normalize_instrument_symbol(self.symbol),
            account_id=str(self.account_id or "").strip()[:191],
            range_key=range_key,
            interval=interval,
        )

    @property
    def limit(self) -> int:
        return int(TIMELINE_RANGES[self.range_key]["limit"])

