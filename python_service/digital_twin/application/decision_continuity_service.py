"""Assemble bounded decision memory for the next investment AI request."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Mapping

from ..domain.decision_continuity import build_decision_continuity_packet
from ..domain.investment_decision_history import compact_decision_episode_memory


def _mapping(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return dict(value.to_dict() or {})
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _latest_feedback(value: object, keys) -> Dict[str, object]:
    source = _mapping(value)
    return {
        key: list(source.get(key) or [])[-3:]
        for key in keys
        if source.get(key)
    }


class DecisionContinuityService:
    """Read one prior decision and its observable aftermath without re-running inference."""

    def __init__(self, decision_episode_store=None, investment_domain_store=None):
        self.decision_episode_store = decision_episode_store
        self.investment_domain_store = investment_domain_store

    def build(
        self,
        *,
        account_id: str,
        symbol: str,
        exclude_episode_id: str = "",
        captured_at: str = "",
        existing_previous: object = None,
    ) -> Dict[str, object]:
        account_key = str(account_id or "").strip()
        symbol_key = str(symbol or "").upper().strip()
        excluded = str(exclude_episode_id or "").strip()
        previous_memory = compact_decision_episode_memory(existing_previous)
        source_status = {
            "decisionEpisode": "unavailable",
            "followUpAndOutcome": "unavailable",
            "accountObservation": "unavailable",
            "executionFeedback": "unavailable",
            "lifecycleFeedback": "unavailable",
        }
        episode = None

        if self.decision_episode_store and account_key and symbol_key:
            try:
                previous_id = str(previous_memory.get("episodeId") or "")
                if not previous_id and hasattr(
                    self.decision_episode_store,
                    "latest_decision_memory",
                ):
                    previous_memory = compact_decision_episode_memory(
                        self.decision_episode_store.latest_decision_memory(
                            account_key,
                            symbol_key,
                            exclude_episode_id=excluded,
                        )
                    )
                    previous_id = str(previous_memory.get("episodeId") or "")
                if previous_id and hasattr(self.decision_episode_store, "get"):
                    episode = self.decision_episode_store.get(previous_id)
                if (
                    episode is None
                    and not previous_memory
                    and hasattr(self.decision_episode_store, "list")
                ):
                    rows = self.decision_episode_store.list(
                        account_id=account_key,
                        symbol=symbol_key,
                        limit=3,
                    )
                    episode = next((
                        item for item in rows or []
                        if str(_mapping(item).get("episodeId") or getattr(item, "episode_id", "") or "").strip()
                        != excluded
                    ), None)
                source_status["decisionEpisode"] = "available" if episode or previous_memory else "not-found"
            except Exception:  # noqa: BLE001 - continuity is advisory and must not block a live alert.
                source_status["decisionEpisode"] = "error"

        episode_payload = _mapping(episode)
        if episode_payload:
            previous_memory = compact_decision_episode_memory(episode_payload)
            source_status["followUpAndOutcome"] = "available"
        elif previous_memory:
            source_status["followUpAndOutcome"] = "not-loaded"

        episode_id = str(
            episode_payload.get("episodeId")
            or previous_memory.get("episodeId")
            or ""
        ).strip()
        portfolio_id = str(episode_payload.get("portfolioId") or "portfolio:" + account_key).strip()
        selected_hypothesis = {}
        selected_id = str(
            episode_payload.get("selectedHypothesisId")
            or previous_memory.get("selectedHypothesisId")
            or ""
        ).strip()
        hypothesis_set = _mapping(episode_payload.get("hypothesisSet"))
        for item in hypothesis_set.get("hypotheses") or []:
            row = _mapping(item)
            if str(row.get("hypothesisId") or "").strip() == selected_id:
                selected_hypothesis = {
                    key: row.get(key)
                    for key in (
                        "hypothesisId", "templateId", "claim", "stance", "verificationStatus",
                        "supportingRuleIds", "supportingEvidenceIds", "counterEvidenceIds",
                    )
                    if row.get(key) not in (None, "", [], {})
                }
                break

        account_context = {}
        execution_feedback = {}
        lifecycle_feedback = {}
        if self.investment_domain_store and episode_id:
            try:
                if hasattr(self.investment_domain_store, "decision_continuity_context"):
                    account_context = self.investment_domain_store.decision_continuity_context(
                        portfolio_id,
                        account_key,
                        symbol_key,
                        episode_id,
                    )
                    source_status["accountObservation"] = "available"
            except Exception:  # noqa: BLE001 - each source reports its own partial state.
                source_status["accountObservation"] = "error"
            try:
                if hasattr(self.investment_domain_store, "execution_feedback_for_decisions"):
                    execution_feedback = _mapping(
                        self.investment_domain_store.execution_feedback_for_decisions([episode_id])
                    ).get(episode_id) or {}
                    source_status["executionFeedback"] = "available"
            except Exception:  # noqa: BLE001
                source_status["executionFeedback"] = "error"
            try:
                if hasattr(self.investment_domain_store, "lifecycle_feedback_for_decisions"):
                    lifecycle_feedback = _mapping(
                        self.investment_domain_store.lifecycle_feedback_for_decisions([episode_id])
                    ).get(episode_id) or {}
                    source_status["lifecycleFeedback"] = "available"
            except Exception:  # noqa: BLE001
                source_status["lifecycleFeedback"] = "error"

        return build_decision_continuity_packet(
            account_id=account_key,
            symbol=symbol_key,
            captured_at=captured_at or _utc_now(),
            previous_decision=episode_payload or previous_memory,
            selected_hypothesis=selected_hypothesis,
            follow_up_conditions=episode_payload.get("followUpConditions") or [],
            unsupported_follow_ups=episode_payload.get("unsupportedFollowUps") or [],
            observed_outcomes=list(episode_payload.get("outcomes") or [])[-6:],
            action_observations=_mapping(account_context).get("actionObservations") or [],
            current_position=_mapping(account_context).get("currentPosition") or {},
            execution_feedback=_latest_feedback(
                execution_feedback,
                ("actionPlans", "executionEpisodes", "fills"),
            ),
            lifecycle_feedback=_latest_feedback(
                lifecycle_feedback,
                ("decisionReviews", "performanceAttributions"),
            ),
            source_status=source_status,
        )
