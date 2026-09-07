"""Canonical user-observable delta between two investment decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


DECISION_DELTA_VERSION = "investment-decision-delta-v1"


@dataclass(frozen=True)
class DecisionDelta:
    """Typed decision meaning consumed by notification delivery policy."""

    target_role: str = ""
    final_action: str = ""
    previous_final_action: str = ""
    ai_transition_kind: str = ""
    history_available: bool = False
    graph_transition_present: bool = False
    graph_transition_kind: str = ""
    graph_transition_material: bool = False
    user_state_transition_kind: str = ""
    user_state_changed: bool = False
    user_state_material: bool = False
    selected_core_inference_eligible: bool = False
    typedb_fallback: bool = False
    publication_outcome: str = ""
    execution_status: str = ""
    ai_adoption_state: str = ""
    ai_authored: bool = False
    canonical_subject: bool = False
    validated_response_present: bool = False
    customer_action_contract_gaps: Tuple[str, ...] = ()
    verified_follow_up_transition_count: int = 0
    verified_market_transition_count: int = 0
    verified_market_transition_id: str = ""
    verified_market_transition_reason: str = ""
    material_source_event_count: int = 0
    observable_relation_evidence_changed: bool = False
    version: str = DECISION_DELTA_VERSION

    @property
    def final_action_changed(self) -> bool:
        return self.ai_transition_kind == "action-changed"

    @property
    def has_material_source_event(self) -> bool:
        return self.material_source_event_count > 0

    @property
    def has_verified_follow_up(self) -> bool:
        return self.verified_follow_up_transition_count > 0

    @property
    def has_verified_market_transition(self) -> bool:
        return self.verified_market_transition_count > 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "targetRole": self.target_role,
            "finalAction": self.final_action,
            "previousFinalAction": self.previous_final_action,
            "finalActionChanged": self.final_action_changed,
            "aiTransitionKind": self.ai_transition_kind,
            "historyAvailable": self.history_available,
            "graphTransitionPresent": self.graph_transition_present,
            "graphTransitionKind": self.graph_transition_kind,
            "graphTransitionMaterial": self.graph_transition_material,
            "userStateTransitionKind": self.user_state_transition_kind,
            "userStateChanged": self.user_state_changed,
            "userStateMaterial": self.user_state_material,
            "selectedCoreInferenceEligible": self.selected_core_inference_eligible,
            "typedbFallback": self.typedb_fallback,
            "publicationOutcome": self.publication_outcome,
            "executionStatus": self.execution_status,
            "aiAdoptionState": self.ai_adoption_state,
            "aiAuthored": self.ai_authored,
            "canonicalSubject": self.canonical_subject,
            "validatedResponsePresent": self.validated_response_present,
            "customerActionContractGaps": list(self.customer_action_contract_gaps),
            "verifiedFollowUpTransitionCount": self.verified_follow_up_transition_count,
            "verifiedMarketTransitionCount": self.verified_market_transition_count,
            "verifiedMarketTransitionId": self.verified_market_transition_id,
            "materialSourceEventCount": self.material_source_event_count,
            "observableRelationEvidenceChanged": self.observable_relation_evidence_changed,
        }

    def delivery_diagnostics(self) -> Dict[str, object]:
        return {
            "targetRole": self.target_role,
            "finalAction": self.final_action,
            "previousFinalAction": self.previous_final_action,
            "graphTransitionKind": self.graph_transition_kind,
            "materialSourceEventCount": self.material_source_event_count,
            "userStateTransitionKind": self.user_state_transition_kind,
            "userStateChanged": self.user_state_changed,
            "selectedCoreInferenceEligible": self.selected_core_inference_eligible,
            "typedbFallback": self.typedb_fallback,
            "publicationOutcome": self.publication_outcome,
            "aiAdoptionState": self.ai_adoption_state,
            "verifiedFollowUpTransitionCount": self.verified_follow_up_transition_count,
            "verifiedMarketTransitionCount": self.verified_market_transition_count,
            "observableRelationEvidenceChanged": self.observable_relation_evidence_changed,
            "customerActionContractGaps": list(self.customer_action_contract_gaps),
            "decisionDelta": self.to_dict(),
        }
