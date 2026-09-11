"""Per-stage results, local to one synchronous projection attempt.

Frozen envelopes do not copy the mutable graph; graph ownership transfers to the
next stage. No stage can replace the source observation with a newer snapshot.
"""

from __future__ import annotations
from typing import Optional, Union
from dataclasses import dataclass
from typing import Dict, List
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_worlds import OntologyWorld
from digital_twin.domain.ontology_validator import OntologyValidationReport
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun


@dataclass(frozen=True)
class CompletedProjection:
    result: Dict[str, object]


@dataclass(frozen=True)
class PrepareAttemptResult:
    compact_reasoning_context: Dict[str, object]
    fresh_candidate_rebuild: bool
    knowledge_world_context: OntologyWorld
    market_world_context: OntologyWorld
    pending_activation_recovery: Dict[str, object]
    portfolio_world_context: OntologyWorld
    shared_premise_proof: Dict[str, object]


@dataclass(frozen=True)
class AssembleSourceResult:
    graph: PortfolioOntology
    graph_input: Dict[str, object]
    material_fingerprint: str
    material_snapshot_id: str
    observation_followup_targets: List[str]
    persistence_graph: PortfolioOntology
    planner_topology: Dict[str, object]
    projection_graph: Dict[str, object]
    rulebox_bootstrap: Dict[str, object]
    scoped_identity: Dict[str, object]


@dataclass(frozen=True)
class SelectSourceResult:
    active_abox: Dict[str, object]
    active_abox_complete: bool
    active_abox_is_scoped_manifest: bool
    evidence_index_upgrade: Dict[str, object]
    graph: PortfolioOntology
    material_fingerprint: str
    material_snapshot_id: str
    persistence_graph: PortfolioOntology
    planner_topology: Dict[str, object]
    scoped_identity: Dict[str, object]
    source_scope_plan: List[Dict[str, object]]
    target_scoped_patch: Dict[str, object]


@dataclass(frozen=True)
class PatchManifestResult:
    graph: PortfolioOntology
    material_fingerprint: str
    material_snapshot_id: str
    persistence_graph: PortfolioOntology
    scoped_identity: Dict[str, object]
    target_scoped_patch: Dict[str, object]


@dataclass(frozen=True)
class ValidateManifestResult:
    active_abox: Dict[str, object]
    current_state_migration_mode: str
    desired_persistence_mode: str
    evidence_index_upgrade: Dict[str, object]
    physical_state_migration_required: bool
    validation: OntologyValidationReport


@dataclass(frozen=True)
class PlanInferenceResult:
    compact_impact_plan: Dict[str, object]
    comparison_scope: Dict[str, object]
    inference_symbols: List[str]
    persisted_comparison_scope: Dict[str, object]
    projection_scope: Dict[str, object]
    world_impact_route: Dict[str, object]


@dataclass(frozen=True)
class ReuseInferenceResult:
    pass


@dataclass(frozen=True)
class CreateAuditResult:
    projection_run: Optional[OntologyProjectionRun]


@dataclass(frozen=True)
class BeginPublicationResult:
    current_state_recovery: Dict[str, object]
    current_state_transition: Dict[str, object]


@dataclass(frozen=True)
class PublishCandidateResult:
    result: Dict[str, object]


@dataclass(frozen=True)
class ScheduleFollowupsResult:
    pass
