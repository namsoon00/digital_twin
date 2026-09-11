from digital_twin.modules.reasoning.infrastructure.projection_policy import coordinator as _projection_policy_coordinator
from digital_twin.modules.reasoning.infrastructure.projection_policy import current_state as _projection_policy_current_state
from digital_twin.modules.reasoning.infrastructure.projection_policy import scope as _projection_policy_scope
from digital_twin.modules.reasoning.infrastructure.projection_policy import shared_world as _projection_policy_shared_world

from digital_twin.modules.reasoning.infrastructure.projection_write import audit as _projection_write_audit
from digital_twin.modules.reasoning.infrastructure.projection_write import catalog as _projection_write_catalog
from digital_twin.modules.reasoning.infrastructure.projection_write import current_state as _projection_write_current_state
from digital_twin.modules.reasoning.infrastructure.projection_write import detail_outbox as _projection_write_detail_outbox
from digital_twin.modules.reasoning.infrastructure.projection_write import inference as _projection_write_inference
from digital_twin.modules.reasoning.infrastructure.projection_write import publication as _projection_write_publication
from digital_twin.modules.reasoning.infrastructure.projection_write import record as _projection_write_record
from digital_twin.modules.reasoning.infrastructure.projection_write import recovery as _projection_write_recovery
from digital_twin.modules.reasoning.infrastructure.projection_write import reuse as _projection_write_reuse
from digital_twin.modules.reasoning.infrastructure.projection_write import scope_policy as _projection_write_scope_policy
from digital_twin.modules.reasoning.infrastructure.projection_write import selection as _projection_write_selection
from digital_twin.modules.reasoning.infrastructure.projection_write import shared_dispatch as _projection_write_shared_dispatch
from digital_twin.modules.reasoning.infrastructure.projection_write import shared_premises as _projection_write_shared_premises
from digital_twin.modules.reasoning.infrastructure.projection_write import shared_world as _projection_write_shared_world
from digital_twin.modules.reasoning.infrastructure.projection_write.catalog_ports import EnsureRuleboxReadyBindings
from digital_twin.modules.reasoning.infrastructure.projection_write.record_ports import RecordSnapshotBindings
from digital_twin.modules.reasoning.infrastructure.projection_write.reuse_ports import CompactSharedInferenceReuseBindings
from digital_twin.modules.reasoning.infrastructure.projection_write.reuse_ports import ExecutionNamespaceBindings
from digital_twin.modules.reasoning.infrastructure.projection_write.shared_dispatch_ports import ScheduleSharedWorldProjectionBindings
from digital_twin.modules.reasoning.infrastructure.projection_write.shared_premises_ports import PrepareSharedPremisesBindings

from digital_twin.modules.reasoning.domain.projection_input_policy import (
    ProjectionInputPolicy,
)
from digital_twin.modules.reasoning.domain.projection_cache_identity import (
    ProjectionCacheKeys,
    PORTFOLIO_GRAPH_ASSEMBLY_CACHE_CONTRACT_VERSION,
    PROJECTION_RUNTIME_CONTEXT_CACHE_CONTRACT_VERSION,
)
from digital_twin.modules.reasoning.domain import projection_facts
from digital_twin.modules.reasoning.domain.projection_facts import (
    ABOX_STRUCTURAL_RELATION_TYPES,
    rulebox_relation_subject_patterns,
    rule_id_from_payload,
)
from digital_twin.modules.reasoning.application.projection_input import (
    ports as projection_input_ports,
    assembly as projection_assembly,
)
from digital_twin.modules.reasoning.application.projection_input.model_evidence import (
    rule_catalog_requires_statistical_signal_scoring,
    governed_statistical_rules_for_catalog,
)
from digital_twin.modules.reasoning.infrastructure import projection_input_cache
from digital_twin.modules.reasoning.infrastructure.projection_input_cache import (
    SharedProjectionRuntimeContextCache,
    SharedPortfolioGraphAssemblyCache,
    SHARED_PROJECTION_RUNTIME_CONTEXT_CACHE,
    SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE,
)
import digital_twin.modules.reasoning.application.projection_input.decision_memory as projection_decision_memory
import digital_twin.modules.reasoning.application.projection_input.hypotheses as projection_hypotheses
import digital_twin.modules.reasoning.application.projection_input.temporal as projection_temporal
import digital_twin.modules.reasoning.application.projection_input.context as projection_context
import digital_twin.modules.reasoning.application.projection_input.identity as projection_identity

from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Callable, Dict, Iterable, List, Mapping, Set
import hashlib
import json
import time
import traceback

from digital_twin.modules.outcomes.public import InvestmentOutcomeObservationService
from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from ..domain.decision_performance import evaluate_decision_performance
from ..domain.crypto_market_signals import crypto_markets_by_symbol
from ..domain.market_signal_transitions import (
    MARKET_SIGNAL_TRANSITION_RESULTS_KEY,
    MARKET_SIGNAL_TRANSITION_STATE_KEY,
)
from ..domain.ontology_rulebox_catalog import (
    default_graph_inference_rules,
    governed_graph_inference_rules,
)
from ..domain.ontology_rulebox_governance import (
    rulebox_rules_hash as compute_rulebox_rules_hash,
)
from ..domain.ontology_rule_ownership import RULE_OWNERSHIP_CONTRACT_VERSION
from ..domain.rule_claim_contract import (
    RULE_CLAIM_CONTRACT_VERSION,
    resolved_rule_claim_contract,
)
from ..domain.ontology_change_impact import (
    build_dynamic_inference_preflight,
    build_inference_impact_plan,
    compact_inference_impact_plan,
    scope_symbol,
)
from ..domain.ontology_world_routing import route_world_impact
from ..domain.ontology_performance_contract import ontology_performance_assessment
from ..domain.ontology_projection_fingerprint import (
    active_material_fingerprint,
    apply_material_graph_identity,
    material_graph_fingerprint,
    stable_value,
)
from ..domain.reasoning_shadow import (
    frozen_projection_runtime_context,
    pack_projection_runtime_contexts,
    unpack_projection_runtime_contexts,
)
from ..domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
    SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
    apply_scoped_abox_repair_epochs,
    apply_scoped_manifest_plan,
    apply_scoped_abox_identity,
    merge_target_scoped_abox_manifest,
    plan_target_scoped_manifest_patch,
    scoped_manifest_id,
    target_scope_manifest_fingerprint,
)
from ..domain.ontology_worlds import (
    knowledge_world,
    market_world,
    shared_premise_world,
    world_from_snapshot,
    world_metadata,
)
from ..domain.knowledge_world_projection import build_knowledge_world_graph, knowledge_world_coverage
from ..domain.market_world_projection import (
    build_market_world_graph,
    market_scope_plan_with_observation_times,
    market_world_coverage,
    merge_market_world_scope_manifest,
)
from ..domain.ontology_projection_audit import (
    INFERENCE_REUSE_PROOF_VERSION,
    OntologyProjectionRun,
    apply_projection_run_identity,
    build_ontology_projection_run,
    compact_reasoning_request_context,
    complete_ontology_projection_run,
    inference_reuse_scope_plan,
    inference_reuse_scope_plan_for_targets,
    inference_reuse_scope_plan_fingerprint,
    projection_source_snapshot,
    projection_run_from_payload,
)
from ..domain.ontology_projection_input import (
    compact_external_signals_for_ontology,
    projection_input_summary,
)
from ..domain.ontology_projection_status import TYPEDB_REASONING_WORKER_DEFERRED
from ..domain.ontology_runtime_operations import (
    build_projection_runtime_observation,
    native_rule_adaptive_target_sharding_policy,
    native_rule_adaptive_target_sharding_profile,
    native_rule_failure_diagnostic,
    native_replay_validation,
)
from ..domain.ontology_schema import (
    abox_lifecycle_metadata,
    apply_abox_lifecycle,
    tbox_fingerprint,
)
from ..domain.ontology_validator import validate_ontology
from ..domain.portfolio_ontology_builder import build_portfolio_ontology
from ..domain.portfolio_ontology_outputs import dedupe_entities, dedupe_relations
from ..domain.portfolio_ontology_statistical_concepts import (
    add_position_statistical_signal_concepts,
)
from ..domain.portfolio_ontology_coverage import CATEGORY_RELATIONS
from ..domain.ontology_native_rule_planning import (
    merge_native_rule_planner_topology,
    native_rule_planner_manifest_fingerprint,
    native_rule_planner_topology,
)
from ..domain.ontology_fact_slots import build_fact_slot_projection_plan
from ..domain.ontology_rulebox_release_manifest import (
    DEPRECATED_TYPEDB_RULE_IDS,
    RULEBOX_DECISION_EFFECT_CONTRACT_RULE_IDS,
    RULEBOX_PLATFORM_RELEASE_ADDITION_IDS,
    RULEBOX_RUNTIME_CONTRACT_RULE_IDS,
    RULEBOX_RUNTIME_CONTRACT_RULE_VERSIONS,
)
from ..domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
from ..domain.portfolio import AccountSnapshot
from ..domain.investment_brain import decision_episode_ontology_context
from ..domain.incremental_inference_equivalence import compare_incremental_rule_states
from ..domain.hypothesis_lifecycle import HYPOTHESIS_LIFECYCLE_KEY_PREFIX
from ..domain.world_partitioned_reasoning import (
    ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION,
    WORLD_PARTITIONED_REASONING_VERSION,
    account_overlay_graph,
    compile_world_partitioned_rules,
    partitioned_phase_impact_plan,
    shared_premise_matches,
    shared_premise_world_graph,
)
from .graph_store_rulebox import (
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
)
from .runtime_identity import runtime_identity


_RULEBOX_BOOTSTRAP_CATALOG_LOCK = Lock()
_RULEBOX_BOOTSTRAP_CATALOG: Dict[str, object] = {}
RULE_EVALUATION_NAMESPACE_VERSION = "rule-evaluation-namespace-v3"


def compact_target_scope_selection_trace(
    target_patch: Mapping[str, object],
    item_limit: int = 40,
) -> Dict[str, object]:
    """Compact diagnostics without dropping the persistence rebind contract."""

    patch = dict(target_patch or {})
    raw_trace = dict(patch.get("scopeSelectionTrace") or {})
    relation_rebind_root_scope_ids = sorted({
        str(value or "").strip()
        for value in (
            patch.get("relationRebindRootScopeIds")
            or raw_trace.get("relationRebindRootScopeIds")
            or []
        )
        if str(value or "").strip()
    })
    limit = max(0, int(item_limit or 0))
    return {
        "version": str(raw_trace.get("version") or ""),
        "selected": [
            dict(item)
            for item in (raw_trace.get("selected") or [])[:limit]
            if isinstance(item, dict)
        ],
        "deferred": [
            dict(item)
            for item in (raw_trace.get("deferred") or [])[:limit]
            if isinstance(item, dict)
        ],
        # The repository consumes these fields to constrain physical
        # relation rebinding. They remain part of the write contract even
        # though the selected/deferred rows are bounded diagnostics.
        "relationRebindRootScopeIds": relation_rebind_root_scope_ids,
        "relationRebindRootScopeCount": len(relation_rebind_root_scope_ids),
    }


def shared_premise_evaluation_plan(
    selection_context: Mapping[str, object],
    dynamic_preflight: Mapping[str, object],
    shared_rule_count: int,
) -> Dict[str, object]:
    """Describe the work TypeDB must actually perform for this generation.

    The event dependency preflight reports rules invalidated by the incoming
    change.  A target without a complete result-slot catalog must still run the
    full shared catalog once so deferred non-matches are proven.  Keeping both
    counts prevents a cold-target warmup from being reported as a two-rule
    incremental turn.
    """

    context = dict(selection_context or {})
    preflight = dict(dynamic_preflight or {})
    full_rule_count = max(0, int(shared_rule_count or 0))
    direct_change_rule_count = max(
        0,
        int(preflight.get("candidateRuleCount") or 0),
    )
    incomplete_symbols = sorted({
        str(value or "").upper().strip()
        for value in context.get("incompleteSymbols") or []
        if str(value or "").strip()
    })
    cold_target_symbols = sorted({
        str(value or "").upper().strip()
        for value in context.get("coldTargetSymbols") or []
        if str(value or "").strip()
    })
    coverage_complete = bool(context.get("coverageComplete", True))
    reusable = bool(context.get("reusable"))
    reuse_eligible = bool(preflight.get("sharedReuseEligible"))

    if reuse_eligible and reusable and coverage_complete:
        mode = "reuse-complete-generation"
        planned_rule_count = 0
    elif not reusable or not coverage_complete or incomplete_symbols:
        mode = "full-catalog-cold-target-warmup"
        planned_rule_count = full_rule_count
    else:
        mode = "incremental-dependency-selection"
        planned_rule_count = min(full_rule_count, direct_change_rule_count)

    return {
        "mode": mode,
        "plannedRuleCount": planned_rule_count,
        "fullRuleCount": full_rule_count,
        "directChangeCandidateRuleCount": direct_change_rule_count,
        "resultSlotReusable": reusable,
        "resultSlotCoverageComplete": coverage_complete,
        "incompleteSymbols": incomplete_symbols,
        "coldTargetSymbols": cold_target_symbols,
        "coldTargetSymbolCount": len(cold_target_symbols),
    }


def compact_staged_abox_activation_lifecycle(
    execution: Dict[str, object],
) -> Dict[str, object]:
    """Keep generation hand-off diagnostics bounded for durable job traces."""

    values = dict(execution or {})

    def compact_step(key: str) -> Dict[str, object]:
        step = values.get(key)
        step = dict(step or {}) if isinstance(step, dict) else {}
        active = (
            dict(step.get("activeAbox") or {})
            if isinstance(step.get("activeAbox"), dict)
            else {}
        )
        compact = {
            field: step.get(field)
            for field in [
                "status", "reason", "candidateAboxSnapshotId",
                "previousAboxSnapshotId", "activeAboxSnapshotId",
                "clearedPendingActivation", "cleanupDeferred",
                "recoveryMode", "retryable", "recommendedRetryAfterSeconds",
            ]
            if field in step
        }
        if active:
            compact["activeAbox"] = {
                field: active.get(field)
                for field in [
                    "status", "aboxSnapshotId", "worldviewManifestId", "worldId",
                ]
                if field in active
            }
        return compact

    alignment = values.get("stagedAboxInferenceAlignment")
    alignment = dict(alignment or {}) if isinstance(alignment, dict) else {}
    return {
        "preparation": compact_step("aboxActivationPreparation"),
        "finalization": compact_step("aboxActivationFinalization"),
        "rollback": compact_step("activationRollback"),
        "alignment": {
            field: alignment.get(field)
            for field in [
                "verified", "candidateAboxSnapshotId", "activeAboxSnapshotId",
                "sourceAboxSnapshotId", "targetSymbols",
            ]
            if field in alignment
        },
        "preservedActiveGeneration": bool(
            values.get("preservedActiveGeneration")
        ),
    }


def shared_inference_from_result_slot_proof(
    *,
    world_id: str,
    active_abox: Mapping[str, object],
    recovery_metadata: Mapping[str, object],
    selection_context: Mapping[str, object],
    symbols: Iterable[str],
) -> Dict[str, object]:
    """Rehydrate a bounded shared premise read from one proven generation.

    Result slots are TypeDB-authored outcomes, not a Python rule evaluator.
    This path is allowed only when every requested symbol has a complete slot
    catalog and the slot provenance exactly matches the active published
    InferenceBox generation and ABox source.
    """

    requested = sorted({
        str(symbol or "").upper().strip()
        for symbol in symbols or []
        if str(symbol or "").strip()
    })
    active_id = str((active_abox or {}).get("aboxSnapshotId") or "").strip()
    generation_id = str(
        (recovery_metadata or {}).get("inferenceGenerationId") or ""
    ).strip()
    source_abox_id = str(
        (recovery_metadata or {}).get("sourceAboxSnapshotId") or ""
    ).strip()
    slot_generation_id = str(
        (selection_context or {}).get("inferenceGenerationId") or ""
    ).strip()
    slot_source_abox_id = str(
        (selection_context or {}).get("sourceAboxSnapshotId") or ""
    ).strip()
    evaluated = {
        str(symbol or "").upper().strip()
        for symbol in (recovery_metadata or {}).get("targetSymbols") or []
        if str(symbol or "").strip()
    }
    states_by_symbol = (
        dict((selection_context or {}).get("ruleStatesBySymbol") or {})
        if isinstance((selection_context or {}).get("ruleStatesBySymbol"), Mapping)
        else {}
    )
    if not (
        requested
        and bool((selection_context or {}).get("reusable"))
        and bool((selection_context or {}).get("coverageComplete", True))
        and not bool((selection_context or {}).get("partialCatalogProof"))
        and bool((selection_context or {}).get("fullGenerationReusable", True))
        and str((recovery_metadata or {}).get("status") or "") == "ok"
        and bool((recovery_metadata or {}).get("nativeTypeDbReasoningCompleted"))
        and active_id
        and active_id == source_abox_id == slot_source_abox_id
        and generation_id
        and generation_id == slot_generation_id
        and set(requested).issubset(evaluated)
        and all(symbol in states_by_symbol for symbol in requested)
        and all(
            len(states_by_symbol.get(symbol) or {})
            == int((selection_context or {}).get("expectedRuleCount") or 0)
            for symbol in requested
        )
    ):
        return {}
    traces = []
    matched_rule_ids = set()
    for symbol in requested:
        states = states_by_symbol.get(symbol)
        if not isinstance(states, Mapping):
            return {}
        for rule_id, state in sorted(states.items()):
            if str(state or "").strip().lower() != "matched":
                continue
            clean_rule_id = str(rule_id or "").strip()
            if not clean_rule_id:
                continue
            matched_rule_ids.add(clean_rule_id)
            trace_key = "|".join([
                world_id, generation_id, source_abox_id, symbol, clean_rule_id,
            ])
            traces.append({
                "traceId": "inference-trace:slot:" + hashlib.sha256(
                    trace_key.encode("utf-8")
                ).hexdigest()[:24],
                "ruleId": clean_rule_id,
                "sourceRuleId": clean_rule_id,
                "symbol": symbol,
                "inferenceGenerationId": generation_id,
                "sourceAboxSnapshotId": source_abox_id,
                "source": "typedb-rule-result-slot",
                "resultSlotProofReused": True,
            })
    matched = bool(traces)
    return {
        "configured": True,
        "status": "ok" if matched else "empty",
        "graphStore": "typedb",
        "source": "typedb-rule-result-slots",
        "reasoningMode": "typedb-result-slot-generation-reuse",
        "worldId": str(world_id or ""),
        "sourceAboxSnapshotId": source_abox_id,
        "inferenceGenerationId": generation_id,
        "targetSymbols": requested,
        "generationAligned": True,
        "nativeTypeDbReasoningUsed": matched,
        "typedbNativeRuleReasoningUsed": matched,
        "nativeTypeDbReasoningCompleted": True,
        "typedbNativeRuleEvaluationCompleted": True,
        "nativeTypeDbFullReasoningCompleted": True,
        "coreNativeInferenceEvaluationComplete": True,
        "nativeCoverageStatus": "complete",
        "nativeInferenceOutcome": "matched" if matched else "empty",
        "ruleExecutionPhase": "shared-premise",
        "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
        "relations": [],
        "traces": traces,
        "relationCount": 0,
        "traceCount": len(traces),
        "typedbNativeRuleMatchedRuleIds": sorted(matched_rule_ids),
        "typedbNativeRuleMatchedCount": len(matched_rule_ids),
        "nativeRuleSelectionApplied": False,
        "nativeRuleSelectionCandidateCount": 0,
        "nativeRuleSelectionExecutedCount": 0,
        "nativeRuleSelectionDeferredCount": int(
            (selection_context or {}).get("expectedRuleCount") or 0
        ),
        "nativeRuleSelectionFullRuleCount": int(
            (selection_context or {}).get("expectedRuleCount") or 0
        ),
        "resultSlotProofReused": True,
        "proofRunId": str((selection_context or {}).get("proofRunId") or ""),
    }


def bootstrap_rule_catalog() -> Dict[str, object]:
    """Return the code bootstrap only for an empty or incompatible catalog.

    TypeDB RuleBox rows are the runtime source of truth.  Constructing and
    canonicalising all default rules before every projection used CPU on a
    code fallback that a healthy persisted catalog never needs.  Keep one
    immutable process-local bootstrap for seeding and explicit compatibility
    repair paths only.
    """
    global _RULEBOX_BOOTSTRAP_CATALOG
    if _RULEBOX_BOOTSTRAP_CATALOG:
        return _RULEBOX_BOOTSTRAP_CATALOG
    with _RULEBOX_BOOTSTRAP_CATALOG_LOCK:
        if not _RULEBOX_BOOTSTRAP_CATALOG:
            rules = rulebox_rules_to_payload(default_graph_inference_rules())
            _RULEBOX_BOOTSTRAP_CATALOG = {
                "rules": rules,
                "ruleboxRulesHash": compute_rulebox_rules_hash(rules),
                "ruleCount": len(rules),
            }
    return _RULEBOX_BOOTSTRAP_CATALOG


def rulebox_catalog_requires_bootstrap_repair(stored_rules: List[Dict[str, object]]) -> bool:
    """Identify only structural RuleBox states that need code bootstrap data.

    Presentation-only differences are owned by the persisted RuleBox and do
    not justify rebuilding the default catalog on each realtime projection.
    The checks below mirror automatic migration cases that can make a native
    rule incompatible with the current ABox shape or decision-policy scope.
    """
    rules = [item for item in stored_rules or [] if isinstance(item, dict)]
    if not rules:
        return True
    rule_ids = {rule_id_from_payload(item) for item in rules if rule_id_from_payload(item)}
    if rule_ids.intersection(DEPRECATED_TYPEDB_RULE_IDS):
        return True
    if not RULEBOX_PLATFORM_RELEASE_ADDITION_IDS.issubset(rule_ids):
        return True
    if rulebox_rules_missing_decision_stage(rules):
        return True
    if any(
        not isinstance(item.get("knowledge_basis") or item.get("knowledgeBasis"), dict)
        or not str(
            (item.get("knowledge_basis") or item.get("knowledgeBasis") or {}).get("ruleKind")
            or ""
        ).strip()
        for item in rules
        if item.get("enabled") is not False
    ):
        return True
    if any(
        not isinstance(item.get("claim_contract") or item.get("claimContract"), dict)
        or str(
            (item.get("claim_contract") or item.get("claimContract") or {}).get("version")
            or ""
        ).strip()
        != RULE_CLAIM_CONTRACT_VERSION
        for item in rules
        if item.get("enabled") is not False
    ):
        return True
    if any(
        str(
            (item.get("claim_contract") or item.get("claimContract") or {}).get("claimType")
            or ""
        ).strip()
        == "market-hypothesis"
        and not list(
            (
                (item.get("hypothesis_lifecycle") or item.get("hypothesisLifecycle") or {}).get("outcomeContract")
                or (item.get("hypothesis_lifecycle") or item.get("hypothesisLifecycle") or {}).get("outcome_contract")
                or {}
            ).get("criteria")
            or []
        )
        for item in rules
        if item.get("enabled") is not False
    ):
        return True
    if any(
        str(
            (item.get("knowledge_basis") or item.get("knowledgeBasis") or {}).get("ruleKind")
            or ""
        ).strip()
        != "predictive-hypothesis"
        and any(
            str(
                (derivation or {}).get("candidate_action")
                or (derivation or {}).get("candidateAction")
                or ""
            ).strip()
            for derivation in item.get("derivations") or []
            if isinstance(derivation, dict)
        )
        for item in rules
        if item.get("enabled") is not False
    ):
        return True
    expected_model_rules = {
        rule.rule_id: rule
        for rule in default_graph_inference_rules()
        if rule.resolved_knowledge_basis.owner == "statistical-model"
    }
    for item in rules:
        # A governed model rule may deliberately remain disabled while its
        # scorer is awaiting promotion. It is retained for audit and future
        # activation, but it is not part of the executable release and must
        # not keep a candidate in an impossible migration loop.
        if item.get("enabled") is False:
            continue
        basis = item.get("knowledge_basis") or item.get("knowledgeBasis") or {}
        if str(basis.get("owner") or "") != "statistical-model":
            continue
        if str(basis.get("migrationDisposition") or "") != "model-signal-production":
            return True
        expected = expected_model_rules.get(rule_id_from_payload(item))
        if expected and str(item.get("version") or "").strip() != expected.version:
            return True
        stored_model_input = (
            item.get("model_input_contract")
            or item.get("modelInputContract")
            or {}
        )
        if (
            expected
            and expected.model_input_contract
            and (
                not isinstance(stored_model_input, Mapping)
                or str(stored_model_input.get("version") or "")
                != str(expected.model_input_contract.get("version") or "")
            )
        ):
            return True
    if any(
        str(
            (item.get("knowledge_basis") or item.get("knowledgeBasis") or {}).get(
                "ownershipContractVersion"
            )
            or ""
        ).strip()
        != RULE_OWNERSHIP_CONTRACT_VERSION
        for item in rules
    ):
        return True
    for item in rules:
        rule_id = rule_id_from_payload(item)
        basis = item.get("knowledge_basis") or item.get("knowledgeBasis") or {}
        if str(basis.get("owner") or "") == "statistical-model":
            continue
        expected_version = RULEBOX_RUNTIME_CONTRACT_RULE_VERSIONS.get(rule_id)
        if expected_version and str(item.get("version") or "").strip() != expected_version:
            return True
    return False

# These edges preserve the factual shape needed to inspect and extend native
# TypeDB reasoning even when the active catalog currently reads aggregate
# window properties only.


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SharedMarketWorldProjectionCoordinator:
    """Coalesce recoverable MarketWorld writes outside portfolio inference.

    A MarketWorld is an account-independent, derived read model.  Portfolio
    ABox persistence and its TypeDB InferenceBox are the decision-critical
    path; the shared mirror is intentionally not an input to that same
    projection's investment judgement.  A daemon worker therefore keeps a
    slow shared-world merge from delaying a verified account inference.

    Only one task per shared world runs in this process.  While it runs, a
    newer observation replaces the pending task, so a burst of quote updates
    results in the latest market state rather than an unbounded backlog.  A
    process stop can drop the in-memory task safely: the next live snapshot
    rebuilds the same derived MarketWorld from its source facts.
    """

    def __init__(self):
        self.lock = Lock()
        self.pending_by_world: Dict[str, Dict[str, object]] = {}
        self.running_world_ids: Set[str] = set()
        self.last_result_by_world: Dict[str, Dict[str, object]] = {}

    @staticmethod
    def result_summary(result: Dict[str, object]) -> Dict[str, object]:
        """Keep queue state observable without copying a full manifest into audits."""
        allowed = {
            "status",
            "reason",
            "materialFingerprint",
            "worldviewManifestId",
            "projectionMode",
            "activeScopeCount",
            "activeSymbolCount",
            "eventuallyConsistent",
            "queuedAt",
            "sourceObservedAt",
            "completedAt",
            "runtimeMs",
        }
        return {
            key: value
            for key, value in dict(result or {}).items()
            if key in allowed and value not in (None, "", [], {})
        }

    def enqueue(self, recorder, portfolio_graph: PortfolioOntology, shared_world) -> Dict[str, object]:
        world_id = str(getattr(shared_world, "world_id", "") or "").strip()
        if not world_id:
            return {
                "status": "deferred-market-world-invalid-world",
                "reason": "Shared MarketWorld has no stable world id.",
            }
        queued_at = utc_now_iso()
        # The projection continues to mutate its PortfolioWorld graph after a
        # task is queued.  Freeze the independent market input before handing
        # it to the daemon worker.
        job = {
            "recorder": recorder,
            "portfolioGraph": deepcopy(portfolio_graph),
            "sharedWorld": shared_world,
            "queuedAt": queued_at,
            "sourceObservedAt": str((portfolio_graph.worldview or {}).get("asOf") or ""),
        }
        with self.lock:
            replaced_pending = world_id in self.pending_by_world
            self.pending_by_world[world_id] = job
            last_result = dict(self.last_result_by_world.get(world_id) or {})
            already_running = world_id in self.running_world_ids
            if not already_running:
                self.running_world_ids.add(world_id)
                try:
                    Thread(
                        target=self.drain,
                        args=(world_id,),
                        name="market-world-projection-" + world_id.replace(":", "-")[-40:],
                        daemon=True,
                    ).start()
                except Exception as error:  # noqa: BLE001 - a derived mirror must not block account judgement.
                    self.running_world_ids.discard(world_id)
                    self.pending_by_world.pop(world_id, None)
                    return {
                        "status": "deferred-market-world-worker-start-failed",
                        "reason": str(error)[:180],
                    }
        return {
            **world_metadata(shared_world),
            "status": "queued-coalesced-market-world-projection",
            "projectionMode": "deferred-coalesced-market-world",
            "eventuallyConsistent": True,
            "queuedAt": queued_at,
            "sourceObservedAt": job["sourceObservedAt"],
            "coalescedPendingUpdate": bool(replaced_pending),
            "workerAlreadyRunning": bool(already_running),
            "lastCompleted": last_result,
            "reason": "공용 시장 읽기 모델은 계좌 추론 완료 뒤 최신 관측값으로 별도 갱신합니다.",
        }

    def drain(self, world_id: str) -> None:
        while True:
            with self.lock:
                job = self.pending_by_world.pop(world_id, None)
                if not isinstance(job, dict):
                    self.running_world_ids.discard(world_id)
                    return
            started = time.perf_counter()
            try:
                recorder = job["recorder"]
                completed = dict(recorder.project_market_world(
                    job["portfolioGraph"],
                    job["sharedWorld"],
                ) or {})
                completed["status"] = str(completed.get("status") or "ok")
            except Exception as error:  # noqa: BLE001 - retain the next queued source observation.
                completed = {
                    "status": "error",
                    "reason": str(error)[:220],
                }
            completed.update({
                "projectionMode": "deferred-coalesced-market-world",
                "eventuallyConsistent": True,
                "queuedAt": str(job.get("queuedAt") or ""),
                "sourceObservedAt": str(job.get("sourceObservedAt") or ""),
                "completedAt": utc_now_iso(),
                "runtimeMs": int((time.perf_counter() - started) * 1000),
            })
            with self.lock:
                self.last_result_by_world[world_id] = self.result_summary(completed)

    def status(self, shared_world) -> Dict[str, object]:
        world_id = str(getattr(shared_world, "world_id", "") or "").strip()
        with self.lock:
            pending = world_id in self.pending_by_world
            running = world_id in self.running_world_ids
            last_result = dict(self.last_result_by_world.get(world_id) or {})
        return {
            **world_metadata(shared_world),
            "pending": pending,
            "running": running,
            "lastCompleted": last_result,
        }


SHARED_MARKET_WORLD_PROJECTION_COORDINATOR = SharedMarketWorldProjectionCoordinator()








class SharedOntologyQualityRecordCoordinator:
    """Coalesce diagnostic quality samples after decision-critical inference.

    Quality samples are observability records. They must reflect a verified
    graph, but writing every intermediate sample must not delay the same
    snapshot's notification path. The latest complete graph per account and
    source is retained while a single daemon writer is active.
    """

    def __init__(self):
        self.lock = Lock()
        self.pending_by_key: Dict[str, Dict[str, object]] = {}
        self.running_keys: Set[str] = set()
        self.last_result_by_key: Dict[str, Dict[str, object]] = {}

    @staticmethod
    def result_summary(result: Dict[str, object]) -> Dict[str, object]:
        allowed = {
            "status",
            "reason",
            "sampleId",
            "qualityState",
            "queuedAt",
            "completedAt",
            "runtimeMs",
        }
        return {
            key: value
            for key, value in dict(result or {}).items()
            if key in allowed and value not in (None, "", [], {})
        }

    def enqueue(self, quality_store, graph: PortfolioOntology, source: str) -> Dict[str, object]:
        key = str(graph.portfolio_id or "portfolio") + ":" + str(source or "monitoring")
        queued_at = utc_now_iso()
        job = {
            "qualityStore": quality_store,
            "graph": deepcopy(graph),
            "source": source or "monitoring",
            "queuedAt": queued_at,
        }
        with self.lock:
            replaced_pending = key in self.pending_by_key
            self.pending_by_key[key] = job
            already_running = key in self.running_keys
            last_result = dict(self.last_result_by_key.get(key) or {})
            if not already_running:
                self.running_keys.add(key)
                try:
                    Thread(
                        target=self.drain,
                        args=(key,),
                        name="ontology-quality-record-" + key.replace(":", "-")[-40:],
                        daemon=True,
                    ).start()
                except Exception as error:  # noqa: BLE001 - diagnostics must not block investment inference.
                    self.running_keys.discard(key)
                    self.pending_by_key.pop(key, None)
                    return {
                        "status": "deferred-quality-record-worker-start-failed",
                        "reason": str(error)[:180],
                    }
        return {
            "status": "queued-coalesced-quality-record",
            "eventuallyConsistent": True,
            "queuedAt": queued_at,
            "coalescedPendingUpdate": bool(replaced_pending),
            "workerAlreadyRunning": bool(already_running),
            "lastCompleted": last_result,
        }

    def drain(self, key: str) -> None:
        while True:
            with self.lock:
                job = self.pending_by_key.pop(key, None)
                if not isinstance(job, dict):
                    self.running_keys.discard(key)
                    return
            started = time.perf_counter()
            try:
                sample = job["qualityStore"].record_graph(job["graph"], source=job["source"])
                completed = {
                    "status": "ok",
                    "sampleId": str(getattr(sample, "sample_id", "") or ""),
                    "qualityState": str(
                        getattr(sample, "overall_state", "")
                        or getattr(sample, "overall_score", "")
                        or ""
                    ),
                }
            except Exception as error:  # noqa: BLE001 - preserve the next queued sample.
                completed = {"status": "error", "reason": str(error)[:220]}
            completed.update({
                "queuedAt": str(job.get("queuedAt") or ""),
                "completedAt": utc_now_iso(),
                "runtimeMs": int((time.perf_counter() - started) * 1000),
            })
            with self.lock:
                self.last_result_by_key[key] = self.result_summary(completed)


SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR = SharedOntologyQualityRecordCoordinator()








def rulebox_input_relation_types(rules: List[Dict[str, object]]) -> List[str]:
    relation_types = set()
    for rule in rules or []:
        if not isinstance(rule, dict) or rule.get("enabled") is False:
            continue
        for condition in rule.get("conditions") or []:
            if not isinstance(condition, dict) or str(condition.get("kind") or "") != "relation":
                continue
            relation_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
            if relation_type:
                relation_types.add(relation_type)
    return sorted(relation_types)




def rulebox_rules_missing_decision_stage(rules: List[Dict[str, object]]) -> List[str]:
    missing = []
    for rule in rules or []:
        if not isinstance(rule, dict) or rule.get("enabled") is False:
            continue
        if any(
            isinstance(item, dict)
            and not str(item.get("decision_stage") or item.get("decisionStage") or "").strip()
            for item in rule.get("derivations") or []
        ):
            missing.append(rule_id_from_payload(rule))
    return sorted(set(item for item in missing if item))


RULEBOX_DERIVATION_GUIDANCE_FIELDS = (
    "decision_effect",
    "decision_label",
    "decision_tone",
    "primary_action",
    "primary_action_label",
    "candidate_action",
    "candidate_action_label",
    "blocked_action_labels",
    "strengthen_conditions",
    "weaken_conditions",
    "next_checks",
    "notification_category",
    "notification_severity",
)


def migrate_typedb_rule_catalog(
    stored_rules: List[Dict[str, object]],
    bootstrap_rules: List[Dict[str, object]],
) -> Dict[str, object]:
    """Migrate the persisted TypeDB RuleBox without reviving Python logic.

    Existing administrator edits remain authoritative.  Bootstrap data fills
    only missing derivation metadata or replaces a known incompatible ABox
    input shape.  The explicit platform-release allowlist is the narrow
    exception: it appends native rules introduced by this release so existing
    stores receive the new TypeDB reasoning path without a manual reseed.
    """
    defaults_by_id = {rule_id_from_payload(item): item for item in bootstrap_rules or [] if isinstance(item, dict)}
    migrated = []
    removed = []
    updated = []
    knowledge_basis_updated = []
    claim_contract_updated = []
    outcome_contract_updated = []
    ownership_contract_updated = []
    added = []
    runtime_shape_updated = []
    decision_effect_contract_updated = []
    model_signal_updated = []
    stored_rule_ids = set()
    for raw_rule in stored_rules or []:
        if not isinstance(raw_rule, dict):
            continue
        rule_id = rule_id_from_payload(raw_rule)
        if rule_id:
            stored_rule_ids.add(rule_id)
        if rule_id in DEPRECATED_TYPEDB_RULE_IDS:
            removed.append(rule_id)
            continue
        rule = deepcopy(raw_rule)
        default_rule = defaults_by_id.get(rule_id) or {}
        default_version = str(default_rule.get("version") or "").strip()
        stored_version = str(rule.get("version") or "").strip()
        stored_basis = rule.get("knowledge_basis") or rule.get("knowledgeBasis") or {}
        default_basis = default_rule.get("knowledge_basis") or default_rule.get("knowledgeBasis") or {}
        default_model_disposition = str(default_basis.get("migrationDisposition") or "")
        stored_model_disposition = str(stored_basis.get("migrationDisposition") or "")
        default_model_input = (
            default_rule.get("model_input_contract")
            or default_rule.get("modelInputContract")
            or {}
        )
        stored_model_input = (
            rule.get("model_input_contract")
            or rule.get("modelInputContract")
            or {}
        )
        model_input_contract_changed = bool(default_model_input) and (
            not isinstance(stored_model_input, Mapping)
            or str(stored_model_input.get("version") or "")
            != str(default_model_input.get("version") or "")
        )
        if (
            str(default_basis.get("owner") or "") == "statistical-model"
            and default_model_disposition == "model-signal-production"
            and (
                stored_model_disposition != default_model_disposition
                or stored_version != default_version
                or model_input_contract_changed
            )
        ):
            replacement = deepcopy(default_rule)
            if (
                rule.get("enabled") is False
                and stored_model_disposition not in {
                    "awaiting-governed-model-scorer",
                    "candidate-awaiting-promotion",
                    "shadow-signal-required",
                    "disabled-awaiting-model-signal",
                }
                and replacement.get("enabled") is not False
            ):
                replacement["enabled"] = False
            migrated.append(replacement)
            updated.append(rule_id)
            runtime_shape_updated.append(rule_id)
            model_signal_updated.append(rule_id)
            if not isinstance(stored_basis, dict) or not str(stored_basis.get("ruleKind") or "").strip():
                knowledge_basis_updated.append(rule_id)
                ownership_contract_updated.append(rule_id)
            continue
        if (
            rule_id in RULEBOX_DECISION_EFFECT_CONTRACT_RULE_IDS
            and bool(default_version)
            and stored_version != default_version
        ):
            # Decision effects are platform-owned execution semantics. Keep
            # operator-authored labels, conditions and enable state, but move
            # every derivation to the versioned default effect contract.
            rule["version"] = default_version
            default_derivations = default_rule.get("derivations") or []
            for index, derivation in enumerate(rule.get("derivations") or []):
                if not isinstance(derivation, dict):
                    continue
                default_derivation = (
                    default_derivations[index]
                    if index < len(default_derivations)
                    else {}
                )
                expected_effect = str(
                    (default_derivation or {}).get("decision_effect")
                    or (default_derivation or {}).get("decisionEffect")
                    or ""
                ).strip()
                if expected_effect:
                    derivation["decision_effect"] = expected_effect
                    derivation.pop("decisionEffect", None)
            migrated.append(rule)
            updated.append(rule_id)
            runtime_shape_updated.append(rule_id)
            decision_effect_contract_updated.append(rule_id)
            continue
        if (
            rule_id in RULEBOX_RUNTIME_CONTRACT_RULE_IDS
            and bool(default_version)
            and stored_version != default_version
        ):
            # The executable condition contract changed. Preserve only the
            # administrative enable/disable flag; keeping old conditions
            # would make native TypeDB inference read an incompatible ABox or
            # cross a decision-policy scope that the new catalog separates.
            replacement = deepcopy(default_rule)
            if "enabled" in rule:
                replacement["enabled"] = bool(rule.get("enabled"))
            migrated.append(replacement)
            updated.append(rule_id)
            runtime_shape_updated.append(rule_id)
            continue
        default_derivations = default_rule.get("derivations") or []
        changed = False
        stored_knowledge_basis = rule.get("knowledge_basis") or rule.get("knowledgeBasis")
        default_knowledge_basis = default_rule.get("knowledge_basis") or default_rule.get("knowledgeBasis")
        if not isinstance(stored_knowledge_basis, dict):
            if isinstance(default_knowledge_basis, dict):
                rule["knowledge_basis"] = deepcopy(default_knowledge_basis)
                changed = True
                knowledge_basis_updated.append(rule_id)
                ownership_contract_updated.append(rule_id)
        elif not str(stored_knowledge_basis.get("ruleKind") or "").strip():
            if isinstance(default_knowledge_basis, dict):
                rule["knowledge_basis"] = deepcopy(default_knowledge_basis)
                changed = True
                knowledge_basis_updated.append(rule_id)
                ownership_contract_updated.append(rule_id)
        elif isinstance(default_knowledge_basis, dict) and (
            str(stored_knowledge_basis.get("ownershipContractVersion") or "").strip()
            != str(default_knowledge_basis.get("ownershipContractVersion") or "").strip()
        ):
            basis_origin = str(stored_knowledge_basis.get("basisOrigin") or "").strip().lower()
            if basis_origin in {"operator-authored", "admin-authored", "user-authored"}:
                merged_basis = deepcopy(stored_knowledge_basis)
                for key in (
                    "owner", "inputContract", "outputContract", "decisionAuthority",
                    "migrationDisposition", "ownershipContractVersion",
                ):
                    merged_basis[key] = deepcopy(default_knowledge_basis.get(key))
                rule["knowledge_basis"] = merged_basis
            else:
                rule["knowledge_basis"] = deepcopy(default_knowledge_basis)
            changed = True
            knowledge_basis_updated.append(rule_id)
            ownership_contract_updated.append(rule_id)
        effective_basis = rule.get("knowledge_basis") or rule.get("knowledgeBasis") or {}
        stored_claim_contract = rule.get("claim_contract") or rule.get("claimContract")
        default_claim_contract = default_rule.get("claim_contract") or default_rule.get("claimContract")
        if not isinstance(default_claim_contract, dict) or not default_claim_contract:
            default_claim_contract = resolved_rule_claim_contract(rule).to_dict()
        if isinstance(default_claim_contract, dict) and (
            not isinstance(stored_claim_contract, dict)
            or str(stored_claim_contract.get("version") or "").strip()
            != str(default_claim_contract.get("version") or "").strip()
            or str(stored_claim_contract.get("ruleId") or stored_claim_contract.get("rule_id") or "").strip()
            != rule_id
        ):
            rule["claim_contract"] = deepcopy(default_claim_contract)
            rule.pop("claimContract", None)
            changed = True
            claim_contract_updated.append(rule_id)
            default_lifecycle = default_rule.get("hypothesis_lifecycle") or default_rule.get("hypothesisLifecycle") or {}
            default_outcome = (
                default_lifecycle.get("outcomeContract")
                or default_lifecycle.get("outcome_contract")
                or {}
            ) if isinstance(default_lifecycle, dict) else {}
            if not default_outcome and str(default_claim_contract.get("claimType") or "") == "market-hypothesis":
                default_outcome = default_claim_contract.get("outcomeContract") or {}
            if default_outcome.get("criteria"):
                lifecycle = rule.get("hypothesis_lifecycle") or rule.get("hypothesisLifecycle") or {}
                lifecycle = deepcopy(lifecycle) if isinstance(lifecycle, dict) else {}
                lifecycle["outcomeContract"] = deepcopy(default_outcome)
                rule["hypothesis_lifecycle"] = lifecycle
                rule.pop("hypothesisLifecycle", None)
                outcome_contract_updated.append(rule_id)
        effective_claim_contract = rule.get("claim_contract") or rule.get("claimContract") or {}
        default_lifecycle = default_rule.get("hypothesis_lifecycle") or default_rule.get("hypothesisLifecycle") or {}
        default_outcome = (
            default_lifecycle.get("outcomeContract")
            or default_lifecycle.get("outcome_contract")
            or {}
        ) if isinstance(default_lifecycle, dict) else {}
        if not default_outcome and str((default_claim_contract or {}).get("claimType") or "") == "market-hypothesis":
            default_outcome = default_claim_contract.get("outcomeContract") or {}
        stored_lifecycle = rule.get("hypothesis_lifecycle") or rule.get("hypothesisLifecycle") or {}
        stored_outcome = (
            stored_lifecycle.get("outcomeContract")
            or stored_lifecycle.get("outcome_contract")
            or {}
        ) if isinstance(stored_lifecycle, dict) else {}
        if (
            str((effective_claim_contract or {}).get("claimType") or "") == "market-hypothesis"
            and isinstance(default_outcome, dict)
            and default_outcome.get("criteria")
            and not (isinstance(stored_outcome, dict) and stored_outcome.get("criteria"))
        ):
            lifecycle = deepcopy(stored_lifecycle) if isinstance(stored_lifecycle, dict) else {}
            lifecycle["outcomeContract"] = deepcopy(default_outcome)
            rule["hypothesis_lifecycle"] = lifecycle
            rule.pop("hypothesisLifecycle", None)
            changed = True
            outcome_contract_updated.append(rule_id)
        if str((effective_basis or {}).get("ruleKind") or "") != "predictive-hypothesis":
            for index, derivation in enumerate(rule.get("derivations") or []):
                if not isinstance(derivation, dict):
                    continue
                candidate_action = str(
                    derivation.get("candidate_action")
                    or derivation.get("candidateAction")
                    or ""
                ).strip()
                if not candidate_action:
                    continue
                default_derivation = default_derivations[index] if index < len(default_derivations) else {}
                default_candidate_action = str(
                    (default_derivation or {}).get("candidate_action")
                    or (default_derivation or {}).get("candidateAction")
                    or ""
                ).strip()
                if default_candidate_action:
                    derivation["candidate_action"] = default_candidate_action
                else:
                    derivation.pop("candidate_action", None)
                    derivation.pop("candidateAction", None)
                changed = True
        for index, derivation in enumerate(rule.get("derivations") or []):
            if not isinstance(derivation, dict):
                continue
            default_derivation = default_derivations[index] if index < len(default_derivations) else {}
            stage = str((default_derivation or {}).get("decision_stage") or (default_derivation or {}).get("decisionStage") or "").strip()
            if stage and not (derivation.get("decision_stage") or derivation.get("decisionStage")):
                derivation["decision_stage"] = stage
                changed = True
            for field in RULEBOX_DERIVATION_GUIDANCE_FIELDS:
                camel_field = "".join(
                    [part if position == 0 else part.capitalize() for position, part in enumerate(field.split("_"))]
                )
                if derivation.get(field) not in (None, "", []) or derivation.get(camel_field) not in (None, "", []):
                    continue
                default_value = (default_derivation or {}).get(field)
                if default_value in (None, "", []):
                    default_value = (default_derivation or {}).get(camel_field)
                if default_value not in (None, "", []):
                    derivation[field] = deepcopy(default_value)
                    changed = True
        if changed:
            updated.append(rule_id)
        migrated.append(rule)
    for rule_id in sorted(RULEBOX_PLATFORM_RELEASE_ADDITION_IDS):
        if rule_id in stored_rule_ids:
            continue
        default_rule = defaults_by_id.get(rule_id)
        if not default_rule:
            continue
        migrated.append(deepcopy(default_rule))
        added.append(rule_id)
    return {
        "changed": bool(removed or updated or added),
        "rules": migrated,
        "removedRuleIds": sorted(set(removed)),
        "addedRuleIds": sorted(set(added)),
        "decisionPolicyUpdatedRuleIds": sorted(set(updated)),
        "knowledgeBasisUpdatedRuleIds": sorted(set(knowledge_basis_updated)),
        "claimContractUpdatedRuleIds": sorted(set(claim_contract_updated)),
        "outcomeContractUpdatedRuleIds": sorted(set(outcome_contract_updated)),
        "ownershipContractUpdatedRuleIds": sorted(set(ownership_contract_updated)),
        "rawAboxRuntimeUpdatedRuleIds": sorted(set(runtime_shape_updated)),
        "decisionEffectContractUpdatedRuleIds": sorted(
            set(decision_effect_contract_updated)
        ),
        "modelSignalUpdatedRuleIds": sorted(set(model_signal_updated)),
    }


class PortfolioOntologyProjectionRecorder:
    def __init__(
        self,
        repository,
        quality_store=None,
        decision_episode_store=None,
        hypothesis_proposal_store=None,
        hypothesis_lifecycle_store=None,
        data_pipeline_health_store=None,
        market_time_series_store=None,
        projection_run_store=None,
        world_projection_outbox=None,
        inference_detail_outbox=None,
        outcome_observation_service=None,
        investment_domain_store=None,
        graph_assembly_cache_store=None,
        statistical_signal_service=None,
        runtime_context_overrides: Dict[str, Dict[str, object]] = None,
        settings: Dict[str, object] = None,
        source: str = "monitoring",
        frozen_rulebox_catalog: Dict[str, object] = None,
        frozen_tbox_metadata: Dict[str, object] = None,
    ):
        self.repository = repository
        self.quality_store = quality_store
        self.decision_episode_store = decision_episode_store
        self.hypothesis_proposal_store = hypothesis_proposal_store
        self.hypothesis_lifecycle_store = hypothesis_lifecycle_store
        self.data_pipeline_health_store = data_pipeline_health_store
        self.market_time_series_store = market_time_series_store
        self.projection_run_store = projection_run_store
        self.world_projection_outbox = world_projection_outbox
        self.inference_detail_outbox = inference_detail_outbox
        self.graph_assembly_cache_store = graph_assembly_cache_store
        self.statistical_signal_service = statistical_signal_service
        self.runtime_context_overrides = {
            str(account_id or ""): frozen_projection_runtime_context(context)
            for account_id, context in dict(runtime_context_overrides or {}).items()
            if str(account_id or "") and isinstance(context, dict)
        }
        self.last_runtime_contexts: Dict[str, Dict[str, object]] = {}
        self.last_runtime_context_cache_status: Dict[str, Dict[str, object]] = {}
        self.investment_domain_store = investment_domain_store
        self.settings = dict(settings or {})
        self._frozen_rulebox_catalog = (
            deepcopy(frozen_rulebox_catalog)
            if isinstance(frozen_rulebox_catalog, dict)
            else None
        )
        self._frozen_tbox_metadata = (
            deepcopy(frozen_tbox_metadata)
            if isinstance(frozen_tbox_metadata, dict)
            else None
        )
        self._rulebox_impact_rules = (
            [
                dict(item)
                for item in (self._frozen_rulebox_catalog or {}).get("rules") or []
                if isinstance(item, dict)
            ]
            if self._frozen_rulebox_catalog is not None
            else None
        )
        self._frozen_rulebox_readiness = None
        self._frozen_world_rule_partition = None
        self._frozen_compiled_rule_catalogs: Dict[str, Dict[str, object]] = {}
        self.outcome_observation_service = outcome_observation_service or InvestmentOutcomeObservationService(
            decision_episode_store=decision_episode_store,
            market_time_series_store=market_time_series_store,
            settings=self.settings,
            investment_domain_store=investment_domain_store,
        )
        self.source = source or "monitoring"

    def execution_namespace(self) -> Dict[str, str]:
        return _projection_write_reuse.execution_namespace(
            self,
            _bindings=ExecutionNamespaceBindings(
                RULE_EVALUATION_NAMESPACE_VERSION=RULE_EVALUATION_NAMESPACE_VERSION
            ),
        )

    def compact_shared_inference_reuse(
        self,
        active_abox: Dict[str, object],
        selection_context: Dict[str, object],
        symbols: List[str],
        world_id: str,
    ) -> tuple:
        return _projection_write_reuse.compact_shared_inference_reuse(
            self,
            active_abox,
            selection_context,
            symbols,
            world_id,
            _bindings=CompactSharedInferenceReuseBindings(
                shared_inference_from_result_slot_proof=shared_inference_from_result_slot_proof
            ),
        )

    def reused_shared_premise_result(
        self,
        *,
        inference: Dict[str, object],
        active_abox: Dict[str, object],
        selection_context: Dict[str, object],
        shared_world,
        shared_rule_ids: List[str],
        overlay_rule_ids: List[str],
        shared_rulebox_hash: str,
        shared_tbox_fingerprint: str,
        requested_symbols: List[str],
        evaluated_symbols: List[str],
        not_evaluated_symbols: List[str],
        catalog: Dict[str, object],
        preflight: Dict[str, object],
        runtime_stages: Dict[str, int],
        started_at: float,
        reuse_mode: str
    ) -> Dict[str, object]:
        return _projection_write_reuse.reused_shared_premise_result(
            self,
            inference=inference,
            active_abox=active_abox,
            selection_context=selection_context,
            shared_world=shared_world,
            shared_rule_ids=shared_rule_ids,
            overlay_rule_ids=overlay_rule_ids,
            shared_rulebox_hash=shared_rulebox_hash,
            shared_tbox_fingerprint=shared_tbox_fingerprint,
            requested_symbols=requested_symbols,
            evaluated_symbols=evaluated_symbols,
            not_evaluated_symbols=not_evaluated_symbols,
            catalog=catalog,
            preflight=preflight,
            runtime_stages=runtime_stages,
            started_at=started_at,
            reuse_mode=reuse_mode,
        )

    def world_partitioned_reasoning_enabled(self) -> bool:
        return _projection_policy_current_state.world_partitioned_reasoning_enabled(self)

    def incremental_current_state_reasoning_enabled(self) -> bool:
        return _projection_policy_current_state.incremental_current_state_reasoning_enabled(
            self
        )

    def current_state_abox_storage_enabled(self) -> bool:
        return _projection_policy_current_state.current_state_abox_storage_enabled(self)

    def interrupted_projection_recovery_required(self, world_id: str) -> bool:
        return _projection_write_current_state.interrupted_projection_recovery_required(
            self, world_id
        )

    def advance_current_state_transition(
        self,
        projection_run: OntologyProjectionRun,
        stage: str,
        status: str = "running",
        inference_generation_id: str = "",
        detail: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _projection_write_current_state.advance_current_state_transition(
            self, projection_run, stage, status, inference_generation_id, detail
        )

    def finalize_current_state_transition(
        self, completed_run: OntologyProjectionRun, result: Dict[str, object]
    ) -> Dict[str, object]:
        return _projection_write_current_state.finalize_current_state_transition(
            self, completed_run, result
        )

    def world_rule_partition(self, rule_catalog: Dict[str, object]) -> Dict[str, object]:
        return _projection_write_catalog.world_rule_partition(self, rule_catalog)

    @staticmethod
    def copy_world_rule_partition(partition: Dict[str, object]) -> Dict[str, object]:
        return _projection_write_catalog.copy_world_rule_partition(partition)

    def catalog_for_rules(
        self, rule_catalog: Dict[str, object], rules
    ) -> Dict[str, object]:
        return _projection_write_catalog.catalog_for_rules(self, rule_catalog, rules)

    @staticmethod
    def copy_compiled_rule_catalog(catalog: Dict[str, object]) -> Dict[str, object]:
        return _projection_policy_current_state.copy_compiled_rule_catalog(catalog)

    def prepare_shared_premises(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
        progress_callback: Callable[[str, Dict[str, object]], None] = None,
    ) -> Dict[str, object]:
        return _projection_write_shared_premises.prepare_shared_premises(
            self,
            snapshot,
            target_symbols,
            reasoning_context,
            progress_callback,
            _bindings=PrepareSharedPremisesBindings(
                compact_staged_abox_activation_lifecycle=compact_staged_abox_activation_lifecycle,
                shared_premise_evaluation_plan=shared_premise_evaluation_plan,
            ),
        )

    def record_snapshot(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
        progress_callback: Callable[[str, Dict[str, object]], None] = None,
    ) -> Dict[str, object]:
        return _projection_write_record.record_snapshot(
            self,
            snapshot,
            target_symbols,
            reasoning_context,
            progress_callback,
            _bindings=RecordSnapshotBindings(
                SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR=SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR,
                compact_target_scope_selection_trace=compact_target_scope_selection_trace,
            ),
        )

    def repository_world_call(self, method_name: str, *args, world_id: str = "", **kwargs):
        return _projection_write_recovery.repository_world_call(
            self, method_name, *args, world_id=world_id, **kwargs
        )

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]:
        return _projection_write_recovery.active_abox_metadata(self, world_id)

    def recover_pending_abox_activation(
        self, world_id: str = "", max_staged_target_symbols: int = 0
    ) -> Dict[str, object]:
        return _projection_write_recovery.recover_pending_abox_activation(
            self, world_id, max_staged_target_symbols
        )

    def resume_staged_pending_abox_activation(
        self, snapshot: AccountSnapshot, world_id: str, recovery: Dict[str, object]
    ) -> Dict[str, object]:
        return _projection_write_recovery.resume_staged_pending_abox_activation(
            self, snapshot, world_id, recovery
        )

    def reconcile_interrupted_projection_audit(
        self, world_id: str = ""
    ) -> Dict[str, object]:
        return _projection_write_recovery.reconcile_interrupted_projection_audit(
            self, world_id
        )

    def active_projection_audit_run(self, world_id: str = ""):
        return _projection_write_recovery.active_projection_audit_run(self, world_id)

    def ensure_rulebox_ready(self) -> Dict[str, object]:
        return _projection_write_catalog.ensure_rulebox_ready(
            self,
            _bindings=EnsureRuleboxReadyBindings(
                bootstrap_rule_catalog=bootstrap_rule_catalog,
                rulebox_catalog_requires_bootstrap_repair=rulebox_catalog_requires_bootstrap_repair,
                rulebox_input_relation_types=rulebox_input_relation_types,
                rulebox_rules_missing_decision_stage=rulebox_rules_missing_decision_stage,
            ),
        )

    def migrate_typedb_rule_catalog(
        self, snapshot: Dict[str, object], bootstrap_rules: List[Dict[str, object]]
    ) -> Dict[str, object]:
        return _projection_write_catalog.migrate_typedb_rule_catalog(
            self, snapshot, bootstrap_rules
        )

    def graph_for_graph_store_persistence(
        self,
        graph: PortfolioOntology,
        rule_catalog: Dict[str, object] = None,
    ) -> PortfolioOntology:
        # TypeDB owns condition evaluation. Projection only retains relation
        # types referenced by the active TypeDB catalog and never evaluates
        # target values, thresholds, or polarity in Python.
        return projection_facts.graph_for_graph_store_persistence(
            graph,
            rule_catalog,
            fallback_rules=(
                self.rulebox_rules_for_impact()
                if not any(
                    isinstance(item, dict)
                    for item in (rule_catalog or {}).get("rules") or []
                )
                else None
            ),
        )

    def graph_assembly_cache_enabled(self) -> bool:
        return ProjectionInputPolicy(self.settings).graph_assembly_cache_enabled()

    def runtime_context_cache_enabled(self) -> bool:
        return ProjectionInputPolicy(self.settings).runtime_context_cache_enabled()

    def runtime_context_cache_ttl_seconds(self) -> float:
        return ProjectionInputPolicy(self.settings).runtime_context_cache_ttl_seconds()

    def runtime_context_cache_max_entries(self) -> int:
        return ProjectionInputPolicy(self.settings).runtime_context_cache_max_entries()

    def runtime_context_cache_key(
        self,
        snapshot: AccountSnapshot,
        active_tbox: Dict[str, object],
        target_symbols: Iterable[object] = None,
    ) -> str:
        return ProjectionCacheKeys(
            self.settings, self.graph_assembly_cache_namespace()
        ).runtime_context_cache_key(
            snapshot,
            active_tbox,
            target_symbols,
        )

    def graph_assembly_cache_ttl_seconds(self) -> float:
        return ProjectionInputPolicy(self.settings).graph_assembly_cache_ttl_seconds()

    def graph_assembly_cache_max_entries(self) -> int:
        return ProjectionInputPolicy(self.settings).graph_assembly_cache_max_entries()

    def graph_assembly_persistent_cache_enabled(self) -> bool:
        return ProjectionInputPolicy(
            self.settings, bool(self.graph_assembly_cache_store)
        ).graph_assembly_persistent_cache_enabled()

    def graph_assembly_persistent_cache_ttl_seconds(self) -> float:
        return ProjectionInputPolicy(
            self.settings
        ).graph_assembly_persistent_cache_ttl_seconds()

    def graph_assembly_persistent_cache_max_entries(self) -> int:
        return ProjectionInputPolicy(
            self.settings
        ).graph_assembly_persistent_cache_max_entries()

    def graph_assembly_persistent_cache_max_payload_bytes(self) -> int:
        return ProjectionInputPolicy(
            self.settings
        ).graph_assembly_persistent_cache_max_payload_bytes()

    def persistent_graph_assembly_cache_get(self, cache_key: str) -> Dict[str, object]:
        return projection_input_cache.persistent_graph_assembly_cache_get(
            projection_input_ports.PersistentCacheInputs(
                graph_assembly_cache_store=self.graph_assembly_cache_store,
                graph_assembly_persistent_cache_enabled=self.graph_assembly_persistent_cache_enabled,
                graph_assembly_persistent_cache_max_entries=self.graph_assembly_persistent_cache_max_entries,
                graph_assembly_persistent_cache_max_payload_bytes=self.graph_assembly_persistent_cache_max_payload_bytes,
                graph_assembly_persistent_cache_ttl_seconds=self.graph_assembly_persistent_cache_ttl_seconds,
            ),
            cache_key,
        )

    def persistent_graph_assembly_cache_put(
        self,
        cache_key: str,
        graph: PortfolioOntology,
        persistence_graph: PortfolioOntology,
        runtime_context_packet: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return projection_input_cache.persistent_graph_assembly_cache_put(
            projection_input_ports.PersistentCacheInputs(
                graph_assembly_cache_store=self.graph_assembly_cache_store,
                graph_assembly_persistent_cache_enabled=self.graph_assembly_persistent_cache_enabled,
                graph_assembly_persistent_cache_max_entries=self.graph_assembly_persistent_cache_max_entries,
                graph_assembly_persistent_cache_max_payload_bytes=self.graph_assembly_persistent_cache_max_payload_bytes,
                graph_assembly_persistent_cache_ttl_seconds=self.graph_assembly_persistent_cache_ttl_seconds,
            ),
            cache_key,
            graph,
            persistence_graph,
            runtime_context_packet,
        )

    def graph_assembly_cache_namespace(self) -> str:
        """Keep test doubles isolated while sharing a real TypeDB runtime."""
        store_key = str(getattr(self.repository, "store_key", "") or "repository")
        address = str(getattr(self.repository, "address", "") or "").strip()
        database = str(getattr(self.repository, "database", "") or "").strip()
        if address or database:
            return "|".join([store_key, address, database])
        return store_key + "|instance:" + str(id(self.repository))

    def active_tbox_context(self) -> Dict[str, object]:
        if self._frozen_tbox_metadata is not None:
            return {
                **deepcopy(self._frozen_tbox_metadata),
                "status": "ok",
                "runtimeCatalogSource": "frozen-v2-release",
                "releaseMetadataReused": True,
            }
        if not hasattr(self.repository, "active_tbox_metadata"):
            return {}
        try:
            return dict(self.repository.active_tbox_metadata() or {})
        except Exception as error:  # noqa: BLE001 - builder retains the code fallback contract.
            return {"status": "error", "reason": str(error)[:180], "source": "code-fallback"}

    def graph_assembly_cache_key(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        active_tbox: Dict[str, object],
        runtime_context: Dict[str, object],
        target_symbols: List[str] = None,
        input_mode: str = "full",
    ) -> str:
        return ProjectionCacheKeys(
            self.settings, self.graph_assembly_cache_namespace()
        ).graph_assembly_cache_key(
            snapshot,
            rule_catalog,
            active_tbox,
            runtime_context,
            target_symbols,
            input_mode,
        )

    def build_graph_assembly(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        target_symbols: List[str] = None,
        target_scoped_input: bool = False,
        progress_callback: Callable[..., None] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> tuple:
        return projection_assembly.build_graph_assembly(
            projection_input_ports.AssemblyInputs(
                cache=projection_input_ports.CacheFlowInputs(
                    graph_assembly_cache_max_entries=self.graph_assembly_cache_max_entries,
                    graph_assembly_cache_ttl_seconds=self.graph_assembly_cache_ttl_seconds,
                    graph_cache=SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE,
                    last_runtime_contexts=self.last_runtime_contexts,
                    persistent_graph_assembly_cache_get=self.persistent_graph_assembly_cache_get,
                    persistent_graph_assembly_cache_put=self.persistent_graph_assembly_cache_put,
                ),
                capture=projection_input_ports.CaptureInputs(
                    active_tbox_context=self.active_tbox_context,
                    last_runtime_context_cache_status=self.last_runtime_context_cache_status,
                    last_runtime_contexts=self.last_runtime_contexts,
                    runtime_context=self.runtime_context,
                    settings=self.settings,
                ),
                graph_assembly_cache_enabled=self.graph_assembly_cache_enabled,
                graph_assembly_cache_key=self.graph_assembly_cache_key,
                graph_for_graph_store_persistence=self.graph_for_graph_store_persistence,
                model=projection_input_ports.ModelEvidenceInputs(
                    last_runtime_contexts=self.last_runtime_contexts,
                    settings=self.settings,
                    statistical_signal_service=self.statistical_signal_service,
                ),
            ),
            snapshot,
            rule_catalog,
            target_symbols,
            target_scoped_input,
            progress_callback,
            reasoning_context,
        )

    def build_projection_graph(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        portfolio_world_context,
        market_world_context=None,
        target_symbols: List[str] = None,
        target_scoped_input: bool = False,
        progress_callback: Callable[..., None] = None,
        shared_premise_proof: Dict[str, object] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return projection_identity.build_projection_graph(
            projection_input_ports.ProjectionIdentityInputs(
                build_graph_assembly=self.build_graph_assembly,
                catalog_for_rules=self.catalog_for_rules,
                incremental_current_state_reasoning_enabled=self.incremental_current_state_reasoning_enabled,
                settings=self.settings,
                world_partitioned_reasoning_enabled=self.world_partitioned_reasoning_enabled,
                world_rule_partition=self.world_rule_partition,
            ),
            snapshot,
            rule_catalog,
            portfolio_world_context,
            market_world_context,
            target_symbols,
            target_scoped_input,
            progress_callback,
            shared_premise_proof,
            reasoning_context,
        )

    def async_quality_record_enabled(self) -> bool:
        return _projection_write_detail_outbox.async_quality_record_enabled(self)

    def inference_detail_outbox_enabled(self) -> bool:
        return _projection_write_detail_outbox.inference_detail_outbox_enabled(self)

    @staticmethod
    def inference_detail_outbox_summary(payload: Dict[str, object]) -> Dict[str, object]:
        return _projection_write_detail_outbox.inference_detail_outbox_summary(payload)

    def enqueue_inference_detail_readback(
        self,
        result: Dict[str, object],
        snapshot: AccountSnapshot,
        inference_symbols: List[str],
        world_id: str = "",
    ) -> Dict[str, object]:
        return _projection_write_detail_outbox.enqueue_inference_detail_readback(
            self, result, snapshot, inference_symbols, world_id
        )

    def graph_for_typedb_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
        return self.graph_for_graph_store_persistence(graph)

    def shared_market_world_retention_hours(self) -> float:
        return _projection_policy_shared_world.shared_market_world_retention_hours(self)

    def shared_knowledge_world_retention_hours(self) -> float:
        return _projection_policy_shared_world.shared_knowledge_world_retention_hours(self)

    def shared_world_retention_hours(self, projection_kind: str) -> float:
        return _projection_policy_shared_world.shared_world_retention_hours(
            self, projection_kind
        )

    def shared_market_world_symbol_limit(self) -> int:
        return _projection_policy_shared_world.shared_market_world_symbol_limit(self)

    def shared_market_world_async_projection_enabled(self) -> bool:
        return _projection_policy_shared_world.shared_market_world_async_projection_enabled(
            self
        )

    def schedule_market_world_projection(
        self, portfolio_graph: PortfolioOntology, shared_world, source_world=None
    ) -> Dict[str, object]:
        return _projection_write_shared_dispatch.schedule_market_world_projection(
            self, portfolio_graph, shared_world, source_world
        )

    def schedule_knowledge_world_projection(
        self, portfolio_graph: PortfolioOntology, shared_world, source_world=None
    ) -> Dict[str, object]:
        return _projection_write_shared_dispatch.schedule_knowledge_world_projection(
            self, portfolio_graph, shared_world, source_world
        )

    def schedule_shared_world_projection(
        self,
        projection_kind: str,
        portfolio_graph: PortfolioOntology,
        shared_world,
        source_world=None,
    ) -> Dict[str, object]:
        return _projection_write_shared_dispatch.schedule_shared_world_projection(
            self,
            projection_kind,
            portfolio_graph,
            shared_world,
            source_world,
            _bindings=ScheduleSharedWorldProjectionBindings(
                SHARED_MARKET_WORLD_PROJECTION_COORDINATOR=SHARED_MARKET_WORLD_PROJECTION_COORDINATOR
            ),
        )

    def shared_world_projection_input(
        self, projection_kind: str, portfolio_graph: PortfolioOntology, shared_world
    ) -> PortfolioOntology:
        return _projection_write_shared_dispatch.shared_world_projection_input(
            self, projection_kind, portfolio_graph, shared_world
        )

    def project_market_world(
        self, portfolio_graph: PortfolioOntology, shared_world
    ) -> Dict[str, object]:
        return _projection_write_shared_dispatch.project_market_world(
            self, portfolio_graph, shared_world
        )

    def project_knowledge_world(
        self, portfolio_graph: PortfolioOntology, shared_world
    ) -> Dict[str, object]:
        return _projection_write_shared_dispatch.project_knowledge_world(
            self, portfolio_graph, shared_world
        )

    def project_shared_world_update(
        self, update: PortfolioOntology, shared_world, projection_kind: str = "market"
    ) -> Dict[str, object]:
        return _projection_write_shared_world.project_shared_world_update(
            self, update, shared_world, projection_kind
        )

    def attach_graph_store_inference_result(
        self,
        result: Dict[str, object],
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        inference_impact_plan: Dict[str, object] = None,
        world_id: str = "",
        candidate_scope_plan: List[Dict[str, object]] = None,
        rulebox_rules_hash: str = "",
        tbox_fingerprint: str = "",
        preflight_graph: PortfolioOntology = None,
        preflight_manifest_id: str = "",
    ) -> None:
        return _projection_write_inference.attach_graph_store_inference_result(
            self,
            result,
            snapshot,
            target_symbols,
            inference_impact_plan,
            world_id,
            candidate_scope_plan,
            rulebox_rules_hash,
            tbox_fingerprint,
            preflight_graph,
            preflight_manifest_id,
        )

    def acquire_inference_write_lease(
        self, result: Dict[str, object], world_id: str = ""
    ) -> Dict[str, object]:
        return _projection_write_publication.acquire_inference_write_lease(
            self, result, world_id
        )

    def release_inference_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        return _projection_write_publication.release_inference_write_lease(self, lease)

    def reconcile_abox_activation_after_inference(
        self, result: Dict[str, object], inference_symbols: List[str], world_id: str = ""
    ) -> None:
        return _projection_write_publication.reconcile_abox_activation_after_inference(
            self, result, inference_symbols, world_id
        )

    @staticmethod
    def attach_abox_persistence_runtime_stages(
        runtime_stages: Dict[str, int], result: Dict[str, object]
    ) -> None:
        return _projection_write_publication.attach_abox_persistence_runtime_stages(
            runtime_stages, result
        )

    def native_preflight_projection_graph(
        self, graph: PortfolioOntology, persistence: Dict[str, object]
    ) -> PortfolioOntology:
        return _projection_write_publication.native_preflight_projection_graph(
            self, graph, persistence
        )

    @staticmethod
    def inference_alignment_diagnostics(
        inferencebox: Dict[str, object],
        expected_snapshot_id: str,
        required_symbols: List[str],
    ) -> Dict[str, object]:
        return _projection_write_publication.inference_alignment_diagnostics(
            inferencebox, expected_snapshot_id, required_symbols
        )

    def existing_inference_result(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _projection_write_reuse.existing_inference_result(
            self, snapshot, target_symbols, world_id
        )

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool:
        return _projection_write_reuse.inference_result_is_reusable(
            self, inferencebox, active_abox, required_symbols
        )

    def prior_rule_selection_context(
        self,
        snapshot: AccountSnapshot,
        inference_symbols: List[str],
        world_id: str = "",
        candidate_scope_plan: List[Dict[str, object]] = None,
        rulebox_rules_hash: str = "",
        tbox_fingerprint: str = "",
        requested_fact_families: List[str] = None,
        requested_fact_families_by_symbol: Dict[str, List[str]] = None,
    ) -> Dict[str, object]:
        return _projection_write_selection.prior_rule_selection_context(
            self,
            snapshot,
            inference_symbols,
            world_id,
            candidate_scope_plan,
            rulebox_rules_hash,
            tbox_fingerprint,
            requested_fact_families,
            requested_fact_families_by_symbol,
        )

    def adaptive_native_rule_target_sharding_profile(
        self, snapshot: AccountSnapshot, world_id: str = "", rulebox_rules_hash: str = ""
    ) -> Dict[str, object]:
        return _projection_write_selection.adaptive_native_rule_target_sharding_profile(
            self, snapshot, world_id, rulebox_rules_hash
        )

    @staticmethod
    def matched_rule_ids_from_inference_payload(payload: Dict[str, object]) -> List[str]:
        return _projection_write_selection.matched_rule_ids_from_inference_payload(payload)

    def audited_prior_rule_selection_context(
        self,
        snapshot: AccountSnapshot,
        inference_symbols: List[str],
        candidate_scope_plan: List[Dict[str, object]] = None,
        rulebox_rules_hash: str = "",
        tbox_fingerprint: str = "",
        world_id: str = "",
        requested_fact_families: List[str] = None,
        requested_fact_families_by_symbol: Dict[str, List[str]] = None,
    ) -> Dict[str, object]:
        return _projection_write_selection.audited_prior_rule_selection_context(
            self,
            snapshot,
            inference_symbols,
            candidate_scope_plan,
            rulebox_rules_hash,
            tbox_fingerprint,
            world_id,
            requested_fact_families,
            requested_fact_families_by_symbol,
        )

    def shared_inference_selection_context(
        self,
        impact_plan: Dict[str, object],
        reasoning_context: Dict[str, object],
        inference_symbols: List[str],
        account_selection_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _projection_write_selection.shared_inference_selection_context(
            self,
            impact_plan,
            reasoning_context,
            inference_symbols,
            account_selection_context,
        )

    def combine_audited_target_rule_selection_contexts(
        self, targets: List[str], target_contexts: List[Dict[str, object]]
    ) -> Dict[str, object]:
        return _projection_write_selection.combine_audited_target_rule_selection_contexts(
            self, targets, target_contexts
        )

    @staticmethod
    def impact_plan_with_audited_candidates(
        impact_plan: Dict[str, object], selection_context: Dict[str, object]
    ) -> Dict[str, object]:
        return _projection_write_selection.impact_plan_with_audited_candidates(
            impact_plan, selection_context
        )

    def incremental_equivalence_audit_sample_pct(self) -> int:
        return self.integer_setting(
            "typedbIncrementalEquivalenceAuditSamplePct",
            1,
            0,
            20,
        )

    def incremental_equivalence_audit_selected(
        self,
        snapshot: AccountSnapshot,
        symbols: List[str],
        impact_plan: Dict[str, object],
        selection_context: Dict[str, object],
    ) -> bool:
        return _projection_policy_scope.incremental_equivalence_audit_selected(
            self, snapshot, symbols, impact_plan, selection_context
        )

    def snapshot_symbols(self, snapshot: AccountSnapshot) -> List[str]:
        return _projection_write_scope_policy.snapshot_symbols(self, snapshot)

    def inference_symbols(
        self, snapshot: AccountSnapshot, target_symbols: List[str] = None
    ) -> List[str]:
        return _projection_write_scope_policy.inference_symbols(
            self, snapshot, target_symbols
        )

    def native_inference_symbol_limit(self) -> int:
        return _projection_policy_scope.native_inference_symbol_limit(self)

    def bounded_native_inference_symbols(
        self,
        snapshot: AccountSnapshot,
        inferred_symbols: List[str],
        requested_symbols: List[str] = None,
        scheduler_target_symbol_limit: int = 0,
    ) -> List[str]:
        return _projection_policy_scope.bounded_native_inference_symbols(
            self,
            snapshot,
            inferred_symbols,
            requested_symbols,
            scheduler_target_symbol_limit,
        )

    @staticmethod
    def scheduler_target_symbol_limit(reasoning_context: Dict[str, object] = None) -> int:
        return _projection_write_scope_policy.scheduler_target_symbol_limit(
            reasoning_context
        )

    def scope_integrity_audit_interval_minutes(self) -> float:
        return _projection_policy_scope.scope_integrity_audit_interval_minutes(self)

    def scope_integrity_audit_age_minutes(self, active_metadata: Dict[str, object]):
        return _projection_policy_scope.scope_integrity_audit_age_minutes(
            self, active_metadata
        )

    @staticmethod
    def reasoning_queue_pressure(
        reasoning_context: Dict[str, object] = None
    ) -> Dict[str, object]:
        return _projection_write_scope_policy.reasoning_queue_pressure(reasoning_context)

    def target_scoped_patch_targets(
        self,
        snapshot: AccountSnapshot,
        active_metadata: Dict[str, object],
        scoped_identity: Dict[str, object],
        requested_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _projection_write_scope_policy.target_scoped_patch_targets(
            self,
            snapshot,
            active_metadata,
            scoped_identity,
            requested_symbols,
            reasoning_context,
        )

    def inference_impact_plan(
        self,
        snapshot: AccountSnapshot,
        active_abox: Dict[str, object],
        scoped_identity: Dict[str, object],
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _projection_write_scope_policy.inference_impact_plan(
            self, snapshot, active_abox, scoped_identity, target_symbols, reasoning_context
        )

    def rulebox_rules_for_impact(self) -> List[Dict[str, object]]:
        return _projection_write_scope_policy.rulebox_rules_for_impact(self)

    def inference_snapshot_limit(self) -> int:
        return _projection_write_scope_policy.inference_snapshot_limit(self)

    def has_projectable_data(self, snapshot: AccountSnapshot) -> bool:
        return _projection_write_scope_policy.has_projectable_data(self, snapshot)

    def typedb_projection_deferred(self) -> bool:
        return _projection_write_scope_policy.typedb_projection_deferred(self)

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str:
        return _projection_write_scope_policy.active_graph_store_key(self, result)

    @staticmethod
    def projection_coordinator_summary(lease: Dict[str, object]) -> Dict[str, object]:
        return _projection_write_audit.projection_coordinator_summary(lease)

    def acquire_projection_coordinator_lease(
        self, owner: str, world_id: str
    ) -> Dict[str, object]:
        return _projection_policy_coordinator.acquire_projection_coordinator_lease(
            self, owner, world_id
        )

    def release_projection_coordinator_lease(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]:
        return _projection_policy_coordinator.release_projection_coordinator_lease(
            self, lease
        )

    def begin_projection_audit_run(
        self,
        snapshot: AccountSnapshot,
        graph: PortfolioOntology,
        material_fingerprint: str,
        abox_snapshot_id: str,
        inference_symbols: List[str],
        rulebox_metadata: Dict[str, object],
        reasoning_context: Dict[str, object] = None,
    ):
        return _projection_write_audit.begin_projection_audit_run(
            self,
            snapshot,
            graph,
            material_fingerprint,
            abox_snapshot_id,
            inference_symbols,
            rulebox_metadata,
            reasoning_context,
        )

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None:
        return _projection_write_audit.store_projection_result(
            self, snapshot, result, projection_run
        )

    def attach_inference_reuse_proof(
        self, projection_run: OntologyProjectionRun, result: Dict[str, object]
    ) -> None:
        return _projection_write_reuse.attach_inference_reuse_proof(
            self, projection_run, result
        )

    def runtime_context(
        self,
        snapshot: AccountSnapshot,
        active_tbox: Dict[str, object] = None,
        target_symbols: List[str] = None,
        progress_callback: Callable[..., None] = None,
    ) -> Dict[str, object]:
        return projection_context.runtime_context(
            projection_input_ports.RuntimeContextInputs(
                active_tbox_context=self.active_tbox_context,
                data_pipeline_health_context=self.data_pipeline_health_context,
                decision_episode_projection_context=self.decision_episode_projection_context,
                decision_episode_store=self.decision_episode_store,
                factual_runtime_metadata=self.factual_runtime_metadata,
                hypothesis_lifecycle_abox_projection_enabled=self.hypothesis_lifecycle_abox_projection_enabled,
                hypothesis_lifecycle_context=self.hypothesis_lifecycle_context,
                hypothesis_proposal_context=self.hypothesis_proposal_context,
                investment_domain_store=self.investment_domain_store,
                last_runtime_context_cache_status=self.last_runtime_context_cache_status,
                last_runtime_contexts=self.last_runtime_contexts,
                performance_setting=self.performance_setting,
                runtime_cache=SHARED_PROJECTION_RUNTIME_CONTEXT_CACHE,
                runtime_context_cache_enabled=self.runtime_context_cache_enabled,
                runtime_context_cache_key=self.runtime_context_cache_key,
                runtime_context_cache_max_entries=self.runtime_context_cache_max_entries,
                runtime_context_cache_ttl_seconds=self.runtime_context_cache_ttl_seconds,
                runtime_context_overrides=self.runtime_context_overrides,
                settings=self.settings,
                statistical_signal_service=self.statistical_signal_service,
                temporal_observation_windows=self.temporal_observation_windows,
            ),
            snapshot,
            active_tbox,
            target_symbols,
            progress_callback,
        )

    @staticmethod
    def factual_runtime_metadata(
        metadata: Dict[str, object] = None,
        target_symbols=None,
        settings: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return projection_facts.factual_runtime_metadata(
            metadata,
            target_symbols,
            settings,
        )

    def temporal_observation_windows(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> Dict[str, object]:
        return projection_temporal.temporal_observation_windows(
            projection_input_ports.TemporalInputs(
                market_time_series_store=self.market_time_series_store,
                settings=self.settings,
            ),
            snapshot,
            target_symbols,
        )

    def performance_setting(self, key: str, fallback: float) -> float:
        return ProjectionInputPolicy(self.settings).performance_setting(
            key,
            fallback,
        )

    def data_pipeline_health_context(
        self, snapshot: AccountSnapshot = None
    ) -> Dict[str, object]:
        return projection_facts.data_pipeline_health_context(
            snapshot,
        )

    def decision_episode_context(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> List[Dict[str, object]]:
        """Compatibility wrapper for callers that only need ABox memory rows."""
        return list(
            self.decision_episode_projection_context(
                snapshot,
                target_symbols=target_symbols,
            ).get("episodes") or []
        )

    def decision_episode_projection_context(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> Dict[str, object]:
        return projection_decision_memory.decision_episode_projection_context(
            projection_input_ports.DecisionMemoryInputs(
                decision_episode_context_hypothesis_limit=self.decision_episode_context_hypothesis_limit,
                decision_episode_context_maximum_episodes=self.decision_episode_context_maximum_episodes,
                decision_episode_context_outcome_limit=self.decision_episode_context_outcome_limit,
                decision_episode_context_per_symbol_limit=self.decision_episode_context_per_symbol_limit,
                decision_episode_store=self.decision_episode_store,
                decision_outcome_history_maximum_episodes=self.decision_outcome_history_maximum_episodes,
                decision_outcome_history_per_symbol_limit=self.decision_outcome_history_per_symbol_limit,
                investment_domain_store=self.investment_domain_store,
                outcome_observation_service=self.outcome_observation_service,
            ),
            snapshot,
            target_symbols,
        )

    def decision_episode_context_per_symbol_limit(self) -> int:
        return ProjectionInputPolicy(
            self.settings
        ).decision_episode_context_per_symbol_limit()

    def decision_episode_context_maximum_episodes(self) -> int:
        return ProjectionInputPolicy(
            self.settings
        ).decision_episode_context_maximum_episodes()

    def decision_episode_context_hypothesis_limit(self) -> int:
        return ProjectionInputPolicy(
            self.settings
        ).decision_episode_context_hypothesis_limit()

    def decision_episode_context_outcome_limit(self) -> int:
        return ProjectionInputPolicy(self.settings).decision_episode_context_outcome_limit()

    def decision_outcome_history_per_symbol_limit(self) -> int:
        return ProjectionInputPolicy(
            self.settings
        ).decision_outcome_history_per_symbol_limit()

    def decision_outcome_history_maximum_episodes(self) -> int:
        return ProjectionInputPolicy(
            self.settings
        ).decision_outcome_history_maximum_episodes()

    def integer_setting(self, key: str, fallback: int, minimum: int, maximum: int) -> int:
        return ProjectionInputPolicy(self.settings).integer_setting(
            key,
            fallback,
            minimum,
            maximum,
        )

    def hypothesis_proposal_context(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> List[Dict[str, object]]:
        return projection_hypotheses.hypothesis_proposal_context(
            projection_input_ports.HypothesisInputs(
                hypothesis_lifecycle_store=self.hypothesis_lifecycle_store,
                hypothesis_proposal_store=self.hypothesis_proposal_store,
            ),
            snapshot,
            target_symbols,
        )

    def hypothesis_lifecycle_context(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> List[Dict[str, object]]:
        return projection_hypotheses.hypothesis_lifecycle_context(
            projection_input_ports.HypothesisInputs(
                hypothesis_lifecycle_store=self.hypothesis_lifecycle_store,
                hypothesis_proposal_store=self.hypothesis_proposal_store,
            ),
            snapshot,
            target_symbols,
        )

    def hypothesis_lifecycle_abox_projection_enabled(self) -> bool:
        return ProjectionInputPolicy(
            self.settings
        ).hypothesis_lifecycle_abox_projection_enabled()
