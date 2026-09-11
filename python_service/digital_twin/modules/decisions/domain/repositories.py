"""Repository capabilities owned by decisions."""

from typing import Dict, Iterable, List, Optional, Protocol
from digital_twin.domain.investment_brain import DecisionEpisode, LearningProposal, ObservedOutcome


class DecisionEpisodeRepository(Protocol):
    def save(self, episode: DecisionEpisode) -> DecisionEpisode:
        ...

    def get(self, episode_id: str) -> Optional[DecisionEpisode]:
        ...

    def list(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[DecisionEpisode]:
        ...

    def record_observation(
        self,
        account_id: str,
        symbol: str,
        facts: Dict[str, object],
        observed_at: str = "",
    ) -> List[ObservedOutcome]:
        ...

    def evaluate_follow_up_observation(
        self,
        account_id: str,
        symbol: str,
        facts: Dict[str, object],
        observed_at: str,
    ) -> List[Dict[str, object]]:
        ...

    def pending_outcome_targets(
        self,
        account_id: str,
        observed_at: str = "",
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        ...

    def record_outcome_observations(
        self,
        account_id: str,
        observations: Iterable[Dict[str, object]],
    ) -> List[ObservedOutcome]:
        ...

    def save_learning_proposal(self, proposal: LearningProposal) -> LearningProposal:
        ...

    def list_learning_proposals(self, status: str = "", limit: int = 50) -> List[Dict[str, object]]:
        ...

    def review_learning_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        ...
