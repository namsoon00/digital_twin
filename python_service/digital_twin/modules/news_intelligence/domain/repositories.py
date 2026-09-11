"""Repository capabilities owned by news intelligence."""

from typing import Callable, Dict, Iterable, List, Protocol, Tuple
from digital_twin.domain.events import DomainEvent
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.investment_brain import NovelHypothesisProposal
from digital_twin.domain.investment_evidence_governance import ResearchRun


class InvestmentResearchRepository(Protocol):
    def save_run(self, run: ResearchRun) -> ResearchRun:
        ...

    def list_runs(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[Dict[str, object]]:
        ...

    def save_hypothesis_proposal(self, proposal: NovelHypothesisProposal) -> NovelHypothesisProposal:
        ...

    def list_hypothesis_proposals(self, status: str = "", symbol: str = "", limit: int = 50) -> List[Dict[str, object]]:
        ...

    def review_hypothesis_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        ...


class ResearchEvidenceRepository(Protocol):
    def upsert_many(self, items: Iterable[ResearchEvidence]) -> int:
        ...

    def upsert_many_with_events(
        self,
        items: Iterable[ResearchEvidence],
        event_builder: Callable[[object], Iterable[DomainEvent]],
    ) -> Tuple[int, List[DomainEvent]]:
        ...

    def latest(
        self,
        symbol: str = "",
        kind: str = "",
        limit: int = 50,
        include_inactive: bool = False,
    ) -> List[ResearchEvidence]:
        ...

    def delete(self, evidence_id: str) -> bool:
        ...

    def delete_stale_news(self, cutoff_iso: str, limit: int = 500) -> int:
        ...

    def expire_stale_news_with_events(
        self,
        cutoff_iso: str,
        limit: int,
        event_builder: Callable[[object], Iterable[DomainEvent]],
    ) -> Tuple[object, List[DomainEvent]]:
        ...

    def retract_many_with_events(
        self,
        evidence_ids: Iterable[str],
        reason: str,
        event_builder: Callable[[object], Iterable[DomainEvent]],
    ) -> Tuple[object, List[DomainEvent]]:
        ...

    def summary(self) -> Dict[str, object]:
        ...


class ResearchEvidenceGateway(Protocol):
    def collect_for_target(
        self,
        target: NewsCollectionTarget,
        source_types: Iterable[str] = None,
    ) -> Tuple[List[ResearchEvidence], List[Dict[str, object]]]:
        ...

    def providers(self) -> List[str]:
        ...
