"""Replaceable boundaries inside the InvestmentReasoning bounded context."""

from typing import Dict, Iterable, Mapping, Optional, Protocol

from .case import ReasoningCase


class ReasoningCaseRepository(Protocol):
    def save(self, reasoning_case: ReasoningCase) -> ReasoningCase:
        ...

    def get(self, case_id: str) -> Optional[ReasoningCase]:
        ...

    def get_by_request(self, request_id: str) -> Optional[ReasoningCase]:
        ...

    def latest(self, deployment_id: str = "", symbol: str = "", limit: int = 20):
        ...


class OntologyInferencePort(Protocol):
    def execute(self, request, snapshots, progress_callback=None) -> Dict[str, object]:
        ...


class HypothesisManagerPort(Protocol):
    def from_candidates(
        self,
        candidates: Iterable[object],
        subject_symbols: Iterable[str] = (),
        inference_generation_ids: Iterable[str] = (),
        account_ids: Iterable[str] = (),
    ):
        ...

    def from_ai_context(self, context: Mapping[str, object]):
        ...


class AIJudgmentPort(Protocol):
    def enqueue(self, notification_job) -> Dict[str, object]:
        ...
