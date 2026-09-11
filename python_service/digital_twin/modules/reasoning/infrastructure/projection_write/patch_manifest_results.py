"""Frozen phase envelopes for one synchronous Manifest repair.

Graphs and diagnostic dictionaries retain the record-stage ownership model:
they are transferred, not cloned. These packets contain no runtime handles.
"""

from dataclasses import dataclass
from typing import Dict, List
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_worlds import OntologyWorld


@dataclass(frozen=True)
class RepairSourceInput:
    snapshot: AccountSnapshot
    rulebox_bootstrap: Dict[str, object]
    portfolio_world_context: OntologyWorld
    market_world_context: OntologyWorld
    target_symbols: List[str]
    shared_premise_proof: Dict[str, object]
    compact_reasoning_context: Dict[str, object]
    observation_followup_targets: List[str]
    active_abox: Dict[str, object]
    applied_target_patch: Dict[str, object]


@dataclass(frozen=True)
class RepairSourceResult:
    graph: PortfolioOntology
    persistence_graph: PortfolioOntology
    planner_topology: Dict[str, object]
    material_fingerprint: str
    material_snapshot_id: str
    scoped_identity: Dict[str, object]
    target_scoped_patch: Dict[str, object]
    scope_repair: Dict[str, object]
    applied_target_patch: Dict[str, object]
    repair_input_fallback: Dict[str, object]


@dataclass(frozen=True)
class ManifestIdentityInput:
    active_abox: Dict[str, object]
    planner_topology: Dict[str, object]
    applied_target_patch: Dict[str, object]
    persistence_graph: PortfolioOntology
    snapshot: AccountSnapshot
    portfolio_world_context: OntologyWorld
    material_snapshot_id: str


@dataclass(frozen=True)
class ManifestIdentityResult:
    material_fingerprint: str
    scoped_identity: Dict[str, object]
    material_snapshot_id: str
    replacement_symbols: List[str]
    semantic_noop_patch: bool


@dataclass(frozen=True)
class AppliedPatchInput:
    applied_target_patch: Dict[str, object]
    target_scoped_patch: Dict[str, object]
    persistence_graph: PortfolioOntology
    scope_repair: Dict[str, object]
    repair_input_fallback: Dict[str, object]
    identity: ManifestIdentityResult


@dataclass(frozen=True)
class FailedPatchInput:
    applied_target_patch: Dict[str, object]
    target_scoped_patch: Dict[str, object]
    repair_input_fallback: Dict[str, object]
    graph_input: Dict[str, object]
