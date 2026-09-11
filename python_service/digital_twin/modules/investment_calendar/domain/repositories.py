"""Repository capabilities owned by investment calendar."""

from typing import Dict, List, Protocol
from digital_twin.modules.investment_calendar.contracts import InvestmentCalendarEvent


class InvestmentCalendarRepository(Protocol):
    def upsert(self, event: InvestmentCalendarEvent) -> InvestmentCalendarEvent:
        ...

    def get(self, event_id: str):
        ...

    def delete(self, event_id: str) -> bool:
        ...

    def list(
        self,
        from_at: str = "",
        to_at: str = "",
        status: str = "",
        symbol: str = "",
        event_type: str = "",
        limit: int = 200,
    ) -> List[InvestmentCalendarEvent]:
        ...

    def reminder_candidates(self, now_at: str = "", lookback_minutes: int = 180) -> List[InvestmentCalendarEvent]:
        ...

    def summary(self) -> Dict[str, object]:
        ...
