"""Repository capabilities owned by model registry."""

from typing import Dict, Iterable, List, Protocol
from digital_twin.domain.hypothesis_lifecycle import HypothesisLifecycleRecord, HypothesisLifecycleTransition


class HypothesisLifecycleRepository(Protocol):
    """Durable audit state for generation-to-generation TypeDB hypotheses."""

    def current_for_keys(self, lifecycle_keys: Iterable[str]) -> Dict[str, HypothesisLifecycleRecord]:
        ...

    def list_current(
        self,
        account_id: str = "",
        symbol: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
    ) -> List[HypothesisLifecycleRecord]:
        ...

    def list_current_summary(
        self,
        account_id: str = "",
        symbol: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        state: str = "",
    ) -> List[HypothesisLifecycleRecord]:
        ...

    def count_current(
        self,
        account_id: str = "",
        symbol: str = "",
        market_id: str = "",
        scope: str = "",
        search: str = "",
        state: str = "",
    ) -> int:
        ...

    def current_for_subjects(
        self,
        account_id: str,
        symbols: Iterable[str],
        lifecycle_key_prefix: str = "",
    ) -> Dict[str, HypothesisLifecycleRecord]:
        ...

    def current_summary_for_subjects(
        self,
        account_id: str,
        symbols: Iterable[str],
        lifecycle_key_prefix: str = "",
    ) -> Dict[str, HypothesisLifecycleRecord]:
        ...

    def list_events(
        self,
        account_id: str = "",
        symbol: str = "",
        lifecycle_key: str = "",
        market_id: str = "",
        scope: str = "",
        limit: int = 100,
    ) -> List[HypothesisLifecycleTransition]:
        ...

    def save(
        self,
        record: HypothesisLifecycleRecord,
        transition: HypothesisLifecycleTransition = None,
    ) -> HypothesisLifecycleRecord:
        ...
