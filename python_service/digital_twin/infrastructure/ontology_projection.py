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

from ..application.investment_outcome_observation_service import InvestmentOutcomeObservationService
from ..domain.ontology_contracts import PortfolioOntology
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
    build_inference_impact_plan,
    compact_inference_impact_plan,
)
from ..domain.ontology_world_routing import route_world_impact
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
    select_target_scoped_manifest_patch,
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
        and str((recovery_metadata or {}).get("status") or "") == "ok"
        and bool((recovery_metadata or {}).get("nativeTypeDbReasoningCompleted"))
        and active_id
        and active_id == source_abox_id == slot_source_abox_id
        and generation_id
        and generation_id == slot_generation_id
        and set(requested).issubset(evaluated)
        and all(symbol in states_by_symbol for symbol in requested)
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
ABOX_STRUCTURAL_RELATION_TYPES = {
    "COMPARES_WITH_MARKET_PROXY",
    "ISSUES",
    "OCCURS_IN_SESSION_PHASE",
    "WINDOW_CONTAINS_OBSERVATION",
    "PRECEDES",
    "REPRESENTS_INSTRUMENT",
    "REPRESENTS_STOCK",
    "RECONCILES_PORTFOLIO",
    "RECORDS_PORTFOLIO_ACTIVITY",
    "INFERRED_FROM_SNAPSHOT_CHANGE",
    "GROUPS_LEDGER_ACTIVITY",
    "HAS_PORTFOLIO_ACTIVITY",
    "HAS_PORTFOLIO_STATE",
    "OBSERVES_ACCOUNT_ACTION",
    "OBSERVED_AFTER_DECISION",
    "OBSERVES_DECISION_CYCLE",
    "EVALUATES_PORTFOLIO_CANDIDATE",
    "HAS_RISK_SNAPSHOT",
    "HAS_POSITION_RISK",
    "HAS_REBALANCE_PROPOSAL",
    "HAS_REBALANCE_SCENARIO",
}


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


class SharedProjectionRuntimeContextCache:
    """Reuse immutable runtime context for one exact source boundary briefly."""

    def __init__(self):
        self.lock = Lock()
        self.entries: "OrderedDict[str, Dict[str, object]]" = OrderedDict()

    def get(self, key: str, ttl_seconds: float) -> Dict[str, object]:
        if not key or ttl_seconds <= 0:
            return {"status": "disabled"}
        now = time.monotonic()
        with self.lock:
            expired = [
                entry_key
                for entry_key, entry in self.entries.items()
                if now - float(entry.get("createdMonotonic") or 0) > ttl_seconds
            ]
            for entry_key in expired:
                self.entries.pop(entry_key, None)
            entry = self.entries.pop(key, None)
            if not isinstance(entry, dict):
                return {"status": "miss"}
            self.entries[key] = entry
            return {
                "status": "hit",
                "ageMs": int((now - float(entry.get("createdMonotonic") or now)) * 1000),
                "context": deepcopy(entry.get("context") or {}),
            }

    def put(self, key: str, context: Dict[str, object], max_entries: int) -> None:
        if not key or max_entries <= 0:
            return
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = {
                "createdMonotonic": time.monotonic(),
                "context": deepcopy(context or {}),
            }
            while len(self.entries) > max_entries:
                self.entries.popitem(last=False)


SHARED_PROJECTION_RUNTIME_CONTEXT_CACHE = SharedProjectionRuntimeContextCache()


class SharedPortfolioGraphAssemblyCache:
    """Reuse one immutable source snapshot's pure ABox assembly briefly.

    Target-scoped TypeDB inference runs can arrive one after another for the
    exact same account snapshot.  Rebuilding the complete ABox for every
    target adds several seconds without changing the facts TypeDB receives.
    The cache keeps only the pre-identity graph pair in process memory; every
    caller gets a deep copy before manifest/scoped-generation fields are
    applied.  A cache key includes the complete source snapshot, runtime
    settings, rule catalog hash, and graph-store namespace, so a fresh source
    observation or configuration change cannot reuse an old graph.
    """

    def __init__(self):
        self.lock = Lock()
        self.entries: "OrderedDict[str, Dict[str, object]]" = OrderedDict()

    def get(self, key: str, ttl_seconds: float) -> Dict[str, object]:
        if not key or ttl_seconds <= 0:
            return {"status": "disabled"}
        now = time.monotonic()
        with self.lock:
            expired = [
                entry_key
                for entry_key, entry in self.entries.items()
                if now - float(entry.get("createdMonotonic") or 0) > ttl_seconds
            ]
            for entry_key in expired:
                self.entries.pop(entry_key, None)
            entry = self.entries.pop(key, None)
            if not isinstance(entry, dict):
                return {"status": "miss"}
            self.entries[key] = entry
            return {
                "status": "hit",
                "ageMs": int((now - float(entry.get("createdMonotonic") or now)) * 1000),
                "graph": deepcopy(entry["graph"]),
                "persistenceGraph": deepcopy(entry["persistenceGraph"]),
                "runtimeContextPacket": deepcopy(entry.get("runtimeContextPacket") or {}),
            }

    def put(
        self,
        key: str,
        graph: PortfolioOntology,
        persistence_graph: PortfolioOntology,
        max_entries: int,
        runtime_context_packet: Dict[str, object] = None,
    ) -> None:
        if not key or max_entries <= 0:
            return
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = {
                "createdMonotonic": time.monotonic(),
                "graph": deepcopy(graph),
                "persistenceGraph": deepcopy(persistence_graph),
                "runtimeContextPacket": deepcopy(runtime_context_packet or {}),
            }
            while len(self.entries) > max_entries:
                self.entries.popitem(last=False)


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


SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE = SharedPortfolioGraphAssemblyCache()
PORTFOLIO_GRAPH_ASSEMBLY_CACHE_CONTRACT_VERSION = "portfolio-graph-assembly-cache-v10-model-input-routing"
PROJECTION_RUNTIME_CONTEXT_CACHE_CONTRACT_VERSION = "projection-runtime-context-cache-v1"
SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR = SharedOntologyQualityRecordCoordinator()


def rule_catalog_requires_statistical_signal_scoring(
    rule_catalog: Mapping[str, object] = None,
) -> bool:
    """Return whether this world phase owns market-model scoring.

    An omitted catalog keeps the compatibility path enabled. A partitioned
    account overlay has an explicit catalog without ``HAS_MODEL_SIGNAL`` and
    consumes shared premises instead of scoring the market ABox again.
    """

    catalog = dict(rule_catalog or {})
    relation_types = {
        str(value or "").upper().strip()
        for value in catalog.get("inputRelationTypes") or []
        if str(value or "").strip()
    }
    has_contract = bool(catalog.get("rules") or relation_types)
    return not has_contract or "HAS_MODEL_SIGNAL" in relation_types


def rule_id_from_payload(rule: Dict[str, object]) -> str:
    return str((rule or {}).get("rule_id") or (rule or {}).get("ruleId") or "").strip()


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


def rulebox_relation_subject_patterns(rules: List[Dict[str, object]]) -> Set[tuple]:
    """Return the exact subject side each native relation condition reads.

    The runtime ABox must not retain a relation merely because its type is
    used somewhere in RuleBox. For example, a portfolio-to-factor edge is not
    an input to a stock rule that reads ``stock -> HAS_FACTOR_EXPOSURE``.
    Keeping that distinction prevents volatile portfolio aggregates from
    forcing every stock scope into a new generation.
    """
    patterns = set()
    for rule in rules or []:
        if not isinstance(rule, dict) or rule.get("enabled") is False:
            continue
        source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock").strip() or "stock"
        for condition in rule.get("conditions") or []:
            if not isinstance(condition, dict) or str(condition.get("kind") or "") != "relation":
                continue
            relation_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
            if not relation_type:
                continue
            direction = str(condition.get("direction") or "out").strip().lower()
            patterns.add((source_kind, relation_type, "in" if direction == "in" else "out"))
    return patterns


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
        self.outcome_observation_service = outcome_observation_service or InvestmentOutcomeObservationService(
            decision_episode_store=decision_episode_store,
            market_time_series_store=market_time_series_store,
            settings=self.settings,
            investment_domain_store=investment_domain_store,
        )
        self.source = source or "monitoring"

    def execution_namespace(self) -> Dict[str, str]:
        """Return the compatibility boundary for reusable native rule slots.

        A deployment commit can change queueing, presentation, or collection
        code without changing one TypeDB rule result. Rule slots are already
        guarded by the exact RuleBox hash and TBox fingerprint, so binding the
        namespace to the whole release fingerprint forced an unnecessary full
        catalogue bootstrap after every deploy. The native engine contract is
        the remaining executable-code boundary and must be bumped whenever
        TypeQL generation/evaluation semantics change.
        """
        from .typedb_ontology import TYPEDB_NATIVE_RULE_ENGINE_VERSION

        deployment_id = str(
            self.settings.get("_reasoningEngineDeploymentId")
            or self.settings.get("reasoningEngineActiveDeploymentId")
            or "ontology-v1-active"
        ).strip()
        graph_database = str(
            self.settings.get("typedbDatabase") or "orbit_alpha_ontology"
        ).strip()
        release_fingerprint = str(
            self.settings.get("_reasoningEngineReleaseFingerprint")
            or runtime_identity().get("revision")
            or "local-runtime"
        ).strip()
        validation_cohort_id = str(
            self.settings.get("_reasoningEngineValidationCohortId") or ""
        ).strip()
        native_rule_engine_version = str(
            self.settings.get("_reasoningEngineNativeRuleEngineVersion")
            or self.settings.get("typedbNativeRuleEngineVersion")
            or TYPEDB_NATIVE_RULE_ENGINE_VERSION
        ).strip()
        material = "|".join([
            RULE_EVALUATION_NAMESPACE_VERSION,
            deployment_id,
            graph_database,
            native_rule_engine_version,
        ])
        return {
            "executionNamespaceId": "projection-namespace:" + hashlib.sha256(
                material.encode("utf-8")
            ).hexdigest()[:32],
            "engineDeploymentId": deployment_id,
            "graphDatabase": graph_database,
            "releaseFingerprint": release_fingerprint,
            "validationCohortId": validation_cohort_id,
            "nativeRuleEngineVersion": native_rule_engine_version,
            "namespaceVersion": RULE_EVALUATION_NAMESPACE_VERSION,
        }

    def compact_shared_inference_reuse(
        self,
        active_abox: Dict[str, object],
        selection_context: Dict[str, object],
        symbols: List[str],
        world_id: str,
    ) -> tuple:
        """Reuse shared inference only through the compact result-slot proof.

        The detailed InferenceBox can be large and belongs to a physical graph
        database shared by successive engine releases.  Reading it merely to
        discover that its execution namespace is incompatible made the first
        request after a release substantially slower than a fresh native pass.
        Result slots already bind reuse to the RuleBox, TBox, deployment and
        native-engine namespace, so a missing slot proof must fall through to
        new inference without expanding the previous generation.
        """
        recovery_metadata = {}
        try:
            recovery_metadata = self.repository_world_call(
                "inferencebox_recovery_metadata",
                world_id=world_id,
            )
        except Exception:
            recovery_metadata = {}
        existing = shared_inference_from_result_slot_proof(
            world_id=world_id,
            active_abox=active_abox,
            recovery_metadata=recovery_metadata,
            selection_context=selection_context,
            symbols=symbols,
        )
        if existing:
            return existing, "typedb-result-slot-generation"
        return {
            "status": "skipped-missing-compact-result-slot-proof",
            "reason": (
                "The prior SharedPremiseWorld generation has no compatible "
                "compact result-slot proof. Native TypeDB inference will run "
                "without expanding the detailed predecessor InferenceBox."
            ),
        }, "compact-result-slot-proof-unavailable"

    def world_partitioned_reasoning_enabled(self) -> bool:
        value = self.settings.get("ontologyWorldPartitionedReasoningEnabled")
        if value is None:
            return str(self.settings.get("_reasoningEngineVersion") or "").strip().lower() == "v2"
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def world_rule_partition(self, rule_catalog: Dict[str, object]) -> Dict[str, object]:
        rows = [
            dict(item) for item in (rule_catalog or {}).get("rules") or []
            if isinstance(item, dict)
        ]
        if not rows:
            rows = [
                dict(item) for item in self.rulebox_rules_for_impact()
                if isinstance(item, dict)
            ]
        try:
            parsed = rulebox_rules_from_payload({"rules": rows})
        except ValueError as error:
            return {"status": "invalid", "failures": [{"reason": str(error)}]}
        return compile_world_partitioned_rules(parsed)

    @staticmethod
    def catalog_for_rules(
        rule_catalog: Dict[str, object],
        rules,
    ) -> Dict[str, object]:
        payloads = rulebox_rules_to_payload(rules or [])
        relation_types = sorted({
            str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
            for rule in payloads
            for condition in rule.get("conditions") or []
            if isinstance(condition, dict)
            and str(condition.get("kind") or "") == "relation"
            and str(condition.get("relation_type") or condition.get("relationType") or "").strip()
        })
        return {
            **dict(rule_catalog or {}),
            "rules": payloads,
            "inputRelationTypes": relation_types,
            "ruleCount": len(payloads),
            "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
        }

    def prepare_shared_premises(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
        progress_callback: Callable[[str, Dict[str, object]], None] = None,
    ) -> Dict[str, object]:
        """Establish an exact SharedPremiseWorld generation for PortfolioWorld.

        A durable full-catalog result-slot proof allows this boundary to run
        only rules affected by the current fact revision plus prior matches.
        Missing or incoherent proof fails closed to one complete native pass.
        """

        if not self.world_partitioned_reasoning_enabled():
            return {"status": "disabled", "ready": False}

        started_at = time.perf_counter()
        runtime_stages: Dict[str, int] = {}

        def progress(stage: str, **payload) -> None:
            if callable(progress_callback):
                try:
                    progress_callback("shared_premise." + stage, dict(payload or {}))
                except Exception:
                    return

        progress("rule_catalog.start")
        stage_started = time.perf_counter()
        catalog = self.ensure_rulebox_ready()
        runtime_stages["ruleCatalogMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        if str(catalog.get("status") or "") not in {"ready", "seeded"}:
            return {
                "status": "rule-catalog-not-ready",
                "ready": False,
                "retryable": True,
                "reason": str(catalog.get("reason") or "TypeDB RuleBox is unavailable.")[:220],
            }
        stage_started = time.perf_counter()
        partition = self.world_rule_partition(catalog)
        runtime_stages["worldRulePartitionMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        if str(partition.get("status") or "") != "ready":
            return {
                "status": "invalid-world-rule-partition",
                "ready": False,
                "retryable": False,
                "failures": list(partition.get("failures") or [])[:40],
                "reason": "RuleBox contains conditions without an auditable world owner.",
            }
        shared_rules = list(partition.get("sharedRules") or [])
        shared_rule_ids = list(partition.get("sharedRuleIds") or [])
        shared_rulebox_hash = compute_rulebox_rules_hash(
            rulebox_rules_to_payload(shared_rules)
        )
        progress("graph.start", sharedRuleCount=int(partition.get("sharedRuleCount") or 0))
        stage_started = time.perf_counter()
        graph, _persistence_graph, assembly = self.build_graph_assembly(
            snapshot,
            self.catalog_for_rules(catalog, shared_rules),
            target_symbols=target_symbols,
            target_scoped_input=bool(target_symbols),
        )
        runtime_stages["graphAssemblyMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        portfolio_context = world_from_snapshot(snapshot, self.settings)
        shared_world = shared_premise_world(
            portfolio_context.market_id,
            self.settings.get("ontologySharedMarketTenantId") or "shared",
        )
        graph.worldview.update({
            **world_metadata(portfolio_context),
            "sharedPremiseWorldId": shared_world.world_id,
            "asOf": str(snapshot.generated_at or ""),
        })
        update = shared_premise_world_graph(
            graph,
            shared_rules,
            shared_world,
        )
        symbols = self.inference_symbols(snapshot, target_symbols)
        requested_symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol in target_symbols or symbols
            if str(symbol or "").strip()
        })
        not_evaluated_symbols = sorted(set(requested_symbols) - set(symbols))
        if requested_symbols and not symbols:
            return {
                "status": "shared-premise-target-not-in-source-snapshot",
                "ready": False,
                "retryable": False,
                "requestedSymbols": requested_symbols,
                "evaluatedSymbols": [],
                "notEvaluatedSymbols": requested_symbols,
                "targetCoverageComplete": False,
                "reason": (
                    "The immutable source snapshot does not contain any requested "
                    "SharedPremiseWorld subject."
                ),
            }
        if target_symbols:
            update.worldview["targetScopedManifestPatch"] = {
                "status": "applied",
                "mode": "shared-premise-target-scoped-input",
                "targetSymbols": symbols,
            }
        prior_active_abox = {}
        stage_started = time.perf_counter()
        try:
            prior_active_abox = self.repository_world_call(
                "active_abox_metadata",
                world_id=shared_world.world_id,
            )
        except Exception:
            prior_active_abox = {}
        runtime_stages["priorAboxMetadataMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        progress("projection.start", worldId=shared_world.world_id)
        stage_started = time.perf_counter()
        projection = self.project_shared_world_update(update, shared_world, projection_kind="premise")
        runtime_stages["projectionMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        projection_status = str(projection.get("status") or "")
        if projection_status not in {
            "ok",
            "staged-scoped-manifest",
            "unchanged-material-facts",
            "already-projected-material",
        }:
            return {
                "status": "shared-premise-projection-failed",
                "ready": False,
                "retryable": bool(projection.get("retryable", True)),
                "recommendedRetryAfterSeconds": int(projection.get("recommendedRetryAfterSeconds") or 10),
                "projection": projection,
                "reasonCode": str(
                    projection.get("reasonCode")
                    or "shared-premise-projection-failed"
                )[:96],
                "failureStage": str(
                    projection.get("failureStage")
                    or "shared-premise-projection"
                )[:96],
                "reason": str(projection.get("reason") or projection_status)[:220],
            }
        stage_started = time.perf_counter()
        if projection_status == "staged-scoped-manifest":
            staged_save = (
                dict(projection.get("save") or {})
                if isinstance(projection.get("save"), dict)
                else {}
            )
            active_abox = {
                "status": "ok",
                "worldId": shared_world.world_id,
                "aboxSnapshotId": str(
                    projection.get("worldviewManifestId")
                    or staged_save.get("aboxSnapshotId")
                    or (update.worldview or {}).get("worldviewManifestId")
                    or ""
                ),
                "worldviewManifestId": str(
                    projection.get("worldviewManifestId")
                    or staged_save.get("worldviewManifestId")
                    or (update.worldview or {}).get("worldviewManifestId")
                    or ""
                ),
                "scopePlan": list(
                    staged_save.get("scopePlan")
                    or (update.worldview or {}).get("scopePlan")
                    or []
                ),
                "materialFingerprint": str(
                    projection.get("materialFingerprint")
                    or (update.worldview or {}).get("materialFingerprint")
                    or ""
                ),
                "candidateState": "staged-native-inference",
            }
        else:
            try:
                active_abox = self.repository_world_call(
                    "active_abox_metadata",
                    world_id=shared_world.world_id,
                )
            except Exception as error:
                return {
                    "status": "shared-market-metadata-failed",
                    "ready": False,
                    "retryable": True,
                    "reason": str(error)[:220],
                }
        runtime_stages["activeAboxMetadataMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        shared_tbox_fingerprint = str(
            ((update.worldview or {}).get("activeTBox") or {}).get("fingerprint")
            or tbox_fingerprint()
        )
        stage_started = time.perf_counter()
        impact_prior_abox = dict(prior_active_abox or {})
        if projection_status == "staged-scoped-manifest":
            staged_save = (
                dict(projection.get("save") or {})
                if isinstance(projection.get("save"), dict)
                else {}
            )
            staged_verification = (
                dict(staged_save.get("aboxPersistenceVerification") or {})
                if isinstance(staged_save.get("aboxPersistenceVerification"), dict)
                else {}
            )
            verified_predecessor = (
                dict(staged_verification.get("activePointer") or {})
                if isinstance(staged_verification.get("activePointer"), dict)
                else {}
            )
            if str(verified_predecessor.get("status") or "") == "ok":
                impact_prior_abox = verified_predecessor
        source_impact_plan = build_inference_impact_plan(
            list((impact_prior_abox or {}).get("scopePlan") or []),
            list((active_abox or {}).get("scopePlan") or (update.worldview or {}).get("scopePlan") or []),
            self.snapshot_symbols(snapshot),
            explicit_target_symbols=symbols,
            rules=self.rulebox_rules_for_impact(),
            requested_fact_families=(reasoning_context or {}).get("requestedScopeFamilies") or [],
            requested_fact_families_by_symbol=(reasoning_context or {}).get("requestedScopeFamiliesBySymbol") or {},
            requested_dependency_keys=(reasoning_context or {}).get("requestedDependencyKeys") or [],
            requested_dependency_keys_by_symbol=(reasoning_context or {}).get("requestedDependencyKeysBySymbol") or {},
            dependency_boundary_authoritative=bool(
                (reasoning_context or {}).get("eventDependencyBoundaryAuthoritative")
            ),
        )
        shared_impact_plan = compact_inference_impact_plan(
            partitioned_phase_impact_plan(
                source_impact_plan,
                partition,
                "shared-premise",
            )
        )
        runtime_stages["impactPlanningMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        namespace = self.execution_namespace()
        target_scope_proof = target_scope_manifest_fingerprint(
            list((active_abox or {}).get("scopePlan") or (update.worldview or {}).get("scopePlan") or []),
            symbols,
        )
        scope_plan_fingerprint = str(
            target_scope_proof.get("fingerprint") or ""
        )
        selection_context = {
            "reusable": False,
            "proofSource": "",
            "matchedRuleIds": [],
            "ruleStatesBySymbol": {},
            "reason": "shared-premise-result-slot-store-unavailable",
        }
        slot_reader = getattr(
            self.projection_run_store,
            "active_rule_result_slot_context",
            None,
        ) if self.projection_run_store else None
        stage_started = time.perf_counter()
        if callable(slot_reader):
            try:
                selection_context = dict(slot_reader(
                    world_id=shared_world.world_id,
                    account_id="",
                    symbols=symbols,
                    rulebox_rules_hash=shared_rulebox_hash,
                    tbox_fingerprint=shared_tbox_fingerprint,
                    expected_rule_count=len(shared_rule_ids),
                    execution_namespace_id=str(namespace.get("executionNamespaceId") or ""),
                    engine_deployment_id=str(namespace.get("engineDeploymentId") or ""),
                    graph_database=str(namespace.get("graphDatabase") or ""),
                ) or {})
            except Exception as error:
                selection_context = {
                    "reusable": False,
                    "proofSource": "typedb-rule-result-slots",
                    "matchedRuleIds": [],
                    "ruleStatesBySymbol": {},
                    "reason": "shared-premise-result-slot-read-failed",
                    "detail": str(error)[:180],
                }
        runtime_stages["resultSlotReadMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        selection_enabled = bool(
            shared_impact_plan.get("nativeRuleSelectionEligible")
            and selection_context.get("reusable")
        )

        existing = {}
        existing_reuse_mode = ""
        runtime_stages["existingInferenceMetadataMs"] = 0
        runtime_stages["existingInferenceReadMs"] = 0
        if projection_status == "staged-scoped-manifest":
            existing = {
                "status": "skipped-new-shared-premise-candidate",
                "reason": (
                    "A predecessor InferenceBox cannot prove reuse for a newly "
                    "staged ABox Manifest."
                ),
            }
        else:
            stage_started = time.perf_counter()
            existing, existing_reuse_mode = self.compact_shared_inference_reuse(
                active_abox,
                selection_context,
                symbols,
                shared_world.world_id,
            )
            runtime_stages["existingInferenceMetadataMs"] = int(
                (time.perf_counter() - stage_started) * 1000
            )
        existing_reusable = bool(
            projection_status != "staged-scoped-manifest"
            and
            self.inference_result_is_reusable(existing, active_abox, symbols)
            and str(existing.get("ruleExecutionPhase") or "") == "shared-premise"
            and str(existing.get("worldPartitionedReasoningVersion") or "")
            == WORLD_PARTITIONED_REASONING_VERSION
        )
        execution = {}
        if existing_reusable:
            inference = dict(existing)
            execution_status = "reused-shared-premise-generation"
            execution = {**dict(existing), "status": "ok"}
        else:
            progress("inference.start", targetSymbolCount=len(symbols))
            stage_started = time.perf_counter()
            rulebox_payload = {
                "worldId": shared_world.world_id,
                "worldType": str(shared_world.world_type or "MarketWorld"),
                "tenantId": str(shared_world.tenant_id or "shared"),
                "accountId": "",
                "symbols": symbols,
                "ruleExecutionPhase": "shared-premise",
                "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
                "inferenceImpactPlan": shared_impact_plan,
                "typedbNativeRuleSelectionEnabled": "1" if selection_enabled else "0",
                "priorInferenceReusable": selection_enabled,
                "priorMatchedRuleIds": (
                    list(selection_context.get("matchedRuleIds") or [])
                    if selection_enabled
                    else []
                ),
                "priorInferenceProofSource": str(
                    selection_context.get("proofSource") or ""
                ),
                "pruneOldGenerations": False,
                "inferenceSnapshotLimit": self.inference_snapshot_limit(),
                "nativeRulePlannerTopology": dict(
                    (update.worldview or {}).get("nativeRulePlannerTopology") or {}
                ),
                "_nativePreflightProjectionGraph": update,
                "_nativePreflightProjectionManifestId": str(
                    (active_abox or {}).get("aboxSnapshotId")
                    or (update.worldview or {}).get("worldviewManifestId")
                    or ""
                ),
                "expectedAboxSnapshotId": str(
                    (active_abox or {}).get("aboxSnapshotId") or ""
                ),
            }
            staged_runner = getattr(
                self.repository,
                "run_rulebox_for_staged_abox",
                None,
            )
            if callable(staged_runner):
                execution = staged_runner(rulebox_payload)
            elif projection_status == "staged-scoped-manifest":
                execution = {
                    "status": "staged-abox-runner-unavailable",
                    "retryable": True,
                    "recommendedRetryAfterSeconds": 10,
                    "reason": (
                        "The graph adapter cannot atomically activate, infer, and "
                        "finalize a staged SharedPremiseWorld candidate."
                    ),
                }
            else:
                rulebox_payload.pop("expectedAboxSnapshotId", None)
                execution = self.repository.run_rulebox(rulebox_payload)
            runtime_stages["nativeInferenceMs"] = int(
                (time.perf_counter() - stage_started) * 1000
            )
            execution_status = str((execution or {}).get("status") or "error")
            inference = dict((execution or {}).get("inferenceBox") or {})
        source_abox_snapshot_id = str(
            inference.get("sourceAboxSnapshotId") or ""
        ).strip()
        active_abox_snapshot_id = str(
            (active_abox or {}).get("aboxSnapshotId") or ""
        ).strip()
        complete = bool(
            execution_status in {"ok", "reused-shared-premise-generation"}
            and (
                inference.get("nativeTypeDbReasoningCompleted")
                or inference.get("typedbNativeRuleEvaluationCompleted")
            )
            and inference.get("generationAligned") is not False
            and source_abox_snapshot_id
            and active_abox_snapshot_id
            and source_abox_snapshot_id == active_abox_snapshot_id
            and str(inference.get("ruleExecutionPhase") or "") == "shared-premise"
            and str(inference.get("worldPartitionedReasoningVersion") or "")
            == WORLD_PARTITIONED_REASONING_VERSION
        )
        if not complete:
            return {
                "status": "shared-premise-inference-incomplete",
                "ready": False,
                "retryable": True,
                "recommendedRetryAfterSeconds": 10,
                "projection": projection,
                "executionStatus": execution_status,
                "inferenceStatus": str(inference.get("status") or ""),
                "generationVector": {
                    "activeAboxSnapshotId": active_abox_snapshot_id,
                    "sourceAboxSnapshotId": source_abox_snapshot_id,
                    "inferenceGenerationId": str(
                        inference.get("inferenceGenerationId") or ""
                    ),
                    "ruleExecutionPhase": str(
                        inference.get("ruleExecutionPhase") or ""
                    ),
                    "worldPartitionedReasoningVersion": str(
                        inference.get("worldPartitionedReasoningVersion") or ""
                    ),
                },
                "activationLifecycle": compact_staged_abox_activation_lifecycle(
                    execution
                ),
                "requestedSymbols": requested_symbols,
                "evaluatedSymbols": symbols,
                "notEvaluatedSymbols": not_evaluated_symbols,
                "targetCoverageComplete": not not_evaluated_symbols,
                "reason": "SharedPremiseWorld TypeDB generation did not complete.",
            }
        result_slot_write = {
            "status": "result-slot-store-unavailable",
            "saved": False,
        }
        slot_writer = getattr(
            self.projection_run_store,
            "record_rule_result_slots",
            None,
        ) if self.projection_run_store else None
        stage_started = time.perf_counter()
        if existing_reusable and bool(existing.get("resultSlotProofReused")):
            result_slot_write = {
                "status": "reused-existing-result-slots",
                "saved": True,
                "reused": True,
                "worldId": shared_world.world_id,
                "symbolCount": len(symbols),
                "catalogRuleCount": len(shared_rule_ids),
                "slotCount": len(symbols) * len(shared_rule_ids),
            }
        elif callable(slot_writer):
            try:
                result_slot_write = dict(slot_writer(
                    world_id=shared_world.world_id,
                    account_id="",
                    symbols=symbols,
                    catalog_rule_ids=shared_rule_ids,
                    rulebox_rules_hash=shared_rulebox_hash,
                    tbox_fingerprint=shared_tbox_fingerprint,
                    scope_plan_fingerprint=scope_plan_fingerprint,
                    source_abox_snapshot_id=str(
                        inference.get("sourceAboxSnapshotId") or ""
                    ),
                    source_snapshot_fingerprint=str(
                        projection.get("materialFingerprint")
                        or active_material_fingerprint(active_abox)
                        or ""
                    ),
                    execution=execution,
                    inference=inference,
                    execution_namespace_id=str(
                        namespace.get("executionNamespaceId") or ""
                    ),
                    engine_deployment_id=str(
                        namespace.get("engineDeploymentId") or ""
                    ),
                    graph_database=str(namespace.get("graphDatabase") or ""),
                    release_fingerprint=str(
                        namespace.get("releaseFingerprint") or ""
                    ),
                    validation_cohort_id=str(
                        namespace.get("validationCohortId") or ""
                    ),
                    prior_rule_states_by_symbol=(
                        dict(selection_context.get("ruleStatesBySymbol") or {})
                        if bool(execution.get("nativeRuleSelectionApplied"))
                        else {}
                    ),
                    revision_vectors_by_symbol=dict(
                        (reasoning_context or {}).get("revisionVectorsBySymbol") or {}
                    ),
                    source_run_id="shared-premise:" + str(
                        inference.get("inferenceGenerationId") or ""
                    ),
                    tbox_version=str(
                        ((update.worldview or {}).get("activeTBox") or {}).get("version")
                        or ""
                    ),
                ) or {})
            except Exception as error:
                result_slot_write = {
                    "status": "result-slot-write-failed",
                    "saved": False,
                    "reason": str(error)[:180],
                }
        runtime_stages["resultSlotWriteMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        premises = shared_premise_matches(inference)
        symbol_rows = {}
        generation_id = str(inference.get("inferenceGenerationId") or "")
        for symbol in symbols:
            symbol_rows[symbol] = {
                "snapshotId": generation_id,
                "relations": [
                    dict(row) for row in inference.get("relations") or []
                    if isinstance(row, dict)
                    and str(row.get("symbol") or "").upper().strip() == symbol
                ],
                "traces": [
                    dict(row) for row in inference.get("traces") or []
                    if isinstance(row, dict)
                    and str(row.get("symbol") or "").upper().strip() == symbol
                ],
            }
        runtime_stages["totalMs"] = int(
            (time.perf_counter() - started_at) * 1000
        )
        progress(
            "done",
            matchedPremiseCount=sum(len(values) for values in premises.values()),
            totalMs=runtime_stages["totalMs"],
        )
        model_signal_bridge_execution = (
            dict(execution.get("modelSignalBridgeExecution") or {})
            if isinstance(execution.get("modelSignalBridgeExecution"), dict)
            else dict(
                (execution.get("nativeMatchResult") or {}).get(
                    "modelSignalBridgeExecution"
                ) or {}
            )
            if isinstance(execution.get("nativeMatchResult"), dict)
            else {}
        )
        return {
            "contractVersion": WORLD_PARTITIONED_REASONING_VERSION,
            "status": "ready",
            "ready": True,
            "worldId": shared_world.world_id,
            "projectionStatus": projection_status,
            "executionStatus": execution_status,
            "premisesBySymbol": premises,
            "sharedRuleIds": shared_rule_ids,
            "overlayRuleIds": list(partition.get("overlayRuleIds") or []),
            "inferenceGenerationId": generation_id,
            "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
            "relations": list(inference.get("relations") or [])[:480],
            "traces": list(inference.get("traces") or [])[:480],
            "symbols": symbol_rows,
            "requestedSymbols": requested_symbols,
            "evaluatedSymbols": symbols,
            "notEvaluatedSymbols": not_evaluated_symbols,
            "targetCoverageComplete": not not_evaluated_symbols,
            "inferenceImpactPlan": shared_impact_plan,
            "ruleSelectionProof": {
                "reusable": bool(selection_context.get("reusable")),
                "proofSource": str(selection_context.get("proofSource") or ""),
                "reason": str(
                    selection_context.get("reason")
                    or selection_context.get("fallbackReason")
                    or ""
                ),
                "selectionRequested": selection_enabled,
                "selectionApplied": bool(
                    execution.get("nativeRuleSelectionApplied")
                ),
                "generationReused": existing_reusable,
                "reuseMode": existing_reuse_mode,
                "candidateRuleCount": int(
                    execution.get("nativeRuleSelectionCandidateCount") or 0
                ),
                "executedRuleCount": int(
                    execution.get("nativeRuleSelectionExecutedCount") or 0
                ),
                "deferredRuleCount": int(
                    execution.get("nativeRuleSelectionDeferredCount") or 0
                ),
                "fullRuleCount": int(
                    execution.get("nativeRuleSelectionFullRuleCount") or 0
                ),
            },
            "resultSlotWrite": result_slot_write,
            "existingInferenceReuseMode": existing_reuse_mode,
            "generationVector": {
                "worldId": shared_world.world_id,
                "sourceAboxSnapshotId": str(
                    inference.get("sourceAboxSnapshotId") or ""
                ),
                "inferenceGenerationId": generation_id,
                "ruleboxRulesHash": shared_rulebox_hash,
                "tboxFingerprint": shared_tbox_fingerprint,
                "scopePlanFingerprint": scope_plan_fingerprint,
                "targetScopeCount": int(
                    target_scope_proof.get("scopeCount") or 0
                ),
            },
            "runtimeStages": runtime_stages,
            "modelSignalBridgeExecution": model_signal_bridge_execution,
            "activationLifecycle": compact_staged_abox_activation_lifecycle(
                execution
            ),
            "assembly": {
                "inputMode": str(assembly.get("inputMode") or ""),
                "targetSymbols": list(assembly.get("targetSymbols") or []),
            },
        }

    def record_snapshot(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
        progress_callback: Callable[[str, Dict[str, object]], None] = None,
    ) -> Dict[str, object]:
        projection_started = time.perf_counter()
        runtime_stages: Dict[str, int] = {}
        projection_run = None
        pending_activation_recovery: Dict[str, object] = {}
        current_stage = "start"

        def emit_progress(stage: str, **details) -> None:
            nonlocal current_stage
            current_stage = str(stage or "unknown")
            if not callable(progress_callback):
                return
            payload = dict(details or {})
            payload.setdefault("accountId", str(snapshot.account_id or ""))
            payload.setdefault("elapsedMs", int((time.perf_counter() - projection_started) * 1000))
            try:
                progress_callback("ontology_projection." + str(stage or "unknown"), payload)
            except Exception:
                return

        emit_progress(
            "start",
            targetSymbolCount=len(target_symbols or []),
            source=str(self.source or "monitoring"),
        )
        compact_reasoning_context = compact_reasoning_request_context(
            reasoning_context,
            target_symbols=target_symbols,
        )
        shared_premise_proof = (
            dict((reasoning_context or {}).get("sharedPremiseProof") or {})
            if isinstance(reasoning_context, dict)
            else {}
        )
        fresh_candidate_rebuild = str(
            self.settings.get("typedbFreshCandidateRebuild") or "0"
        ).strip().lower() in {"1", "true", "yes", "on", "enabled"}
        portfolio_world_context = world_from_snapshot(snapshot, self.settings)
        market_world_context = market_world(
            portfolio_world_context.market_id,
            self.settings.get("ontologySharedMarketTenantId") or "shared",
        )
        knowledge_world_context = knowledge_world(
            portfolio_world_context.market_id,
            self.settings.get("ontologySharedMarketTenantId") or "shared",
        )
        if not self.repository:
            emit_progress("skipped", status="repository-unavailable")
            return {}
        if not self.has_projectable_data(snapshot):
            result = {
                "saved": False,
                "status": "rejected-non-live-snapshot",
                "reason": "운영 ABox는 정상 live 계좌의 실제 보유·관심종목 스냅샷으로만 갱신합니다.",
                "snapshotMode": str(snapshot.mode or ""),
                "snapshotStatus": str(snapshot.status or ""),
                "preservedActiveGeneration": True,
                "ontologyWorld": world_metadata(portfolio_world_context),
            }
            self.store_projection_result(snapshot, result)
            emit_progress("rejected", status=result["status"])
            return result
        if target_symbols:
            target_input = snapshot.projection_observation_input(target_symbols)
            if str(target_input.get("mode") or "") == "empty":
                result = {
                    "saved": False,
                    "status": "skipped-inactive-target-symbols",
                    "reason": "추론 요청 종목이 현재 보유·관심종목 스냅샷에 없어 전체 계좌 재추론을 건너뛰었습니다.",
                    "targetSymbols": list(target_input.get("targetSymbols") or []),
                    "availableSymbols": list(target_input.get("availableSymbols") or []),
                    "preservedActiveGeneration": True,
                    "ontologyWorld": world_metadata(portfolio_world_context),
                }
                self.store_projection_result(snapshot, result)
                emit_progress("skipped", status=result["status"])
                return result
        if self.typedb_projection_deferred():
            result = {
                "saved": False,
                "status": TYPEDB_REASONING_WORKER_DEFERRED,
                "reason": "TypeDB ABox와 InferenceBox는 전용 온톨로지 추론 워커가 같은 주기에서 생성합니다.",
                "preservedActiveGeneration": True,
                "singleWriter": True,
                "ontologyWorld": world_metadata(portfolio_world_context),
            }
            self.store_projection_result(snapshot, result)
            emit_progress("deferred", status=result["status"])
            return result
        emit_progress("pending_activation_recovery.start")
        pending_recovery_started = time.perf_counter()
        pending_activation_recovery = (
            {
                "configured": True,
                "status": "skipped-fresh-candidate",
                "graphStore": "typedb",
                "reason": "The isolated blue-green candidate has no prior PortfolioWorld activation.",
            }
            if fresh_candidate_rebuild
            else self.recover_pending_abox_activation(
                portfolio_world_context.world_id,
                max_staged_target_symbols=self.scheduler_target_symbol_limit(compact_reasoning_context),
            )
        )
        runtime_stages["pendingAboxActivationRecoveryMs"] = int(
            (time.perf_counter() - pending_recovery_started) * 1000
        )
        recovery_status = str(pending_activation_recovery.get("status") or "skipped")
        emit_progress(
            "pending_activation_recovery.done",
            status=recovery_status,
            runtimeMs=runtime_stages["pendingAboxActivationRecoveryMs"],
        )
        # Recovery takes the same database-wide writer coordinator as an ABox
        # swap.  A held coordinator is normal back-pressure: keep the
        # existing generation and let the reasoning mailbox retry after the
        # current writer finishes.  Collapsing it into a generic recovery
        # failure incorrectly opened the projection circuit and left the
        # pending activation (and its retired Manifest cleanup) stranded.
        recovery_is_retryable = (
            recovery_status.startswith("deferred-")
            or bool(pending_activation_recovery.get("retryable"))
        )
        if recovery_is_retryable:
            try:
                recovery_retry_after = max(
                    1,
                    int(float(
                        pending_activation_recovery.get("recommendedRetryAfterSeconds")
                        or pending_activation_recovery.get("retryAfterSeconds")
                        or 10
                    )),
                )
            except (TypeError, ValueError):
                recovery_retry_after = 10
            result = {
                "saved": False,
                "status": (
                    recovery_status
                    if recovery_status.startswith("deferred-")
                    else "deferred-pending-abox-activation-recovery"
                ),
                "reason": str(
                    pending_activation_recovery.get("reason")
                    or "TypeDB ABox activation recovery is waiting for a safe retry."
                )[:220],
                "graphStore": self.active_graph_store_key(),
                "retryable": True,
                "recommendedRetryAfterSeconds": recovery_retry_after,
                "preservedActiveGeneration": True,
                "pendingAboxActivationRecovery": pending_activation_recovery,
            }
            if isinstance(pending_activation_recovery.get("projectionCoordinator"), dict):
                result["projectionCoordinator"] = dict(
                    pending_activation_recovery.get("projectionCoordinator") or {}
                )
            self.store_projection_result(snapshot, result)
            emit_progress("deferred", status=result["status"])
            return result
        if recovery_status not in {
            "skipped",
            "skipped-fresh-candidate",
            "disabled",
            "finalized",
            "finalized-empty-target",
            "restored",
            "cleared-stale",
            "discarded-staged-batch",
            "retry-required",
            "staged",
        }:
            result = {
                "saved": False,
                "status": "pending-abox-activation-recovery-failed",
                "reason": str(
                    pending_activation_recovery.get("reason")
                    or "TypeDB ABox activation recovery must complete before a new investment inference cycle."
                )[:220],
                "graphStore": self.active_graph_store_key(),
                "preservedActiveGeneration": True,
                "pendingAboxActivationRecovery": pending_activation_recovery,
            }
            self.store_projection_result(snapshot, result)
            emit_progress("blocked", status=result["status"])
            return result
        if recovery_status in {"staged", "retry-required"}:
            resume_started = time.perf_counter()
            result = self.resume_staged_pending_abox_activation(
                snapshot,
                portfolio_world_context.world_id,
                pending_activation_recovery,
            )
            runtime_stages["pendingAboxActivationResumeMs"] = int(
                (time.perf_counter() - resume_started) * 1000
            )
            result["pendingAboxActivationRecovery"] = pending_activation_recovery
            runtime_stages["totalMs"] = int((time.perf_counter() - projection_started) * 1000)
            result.setdefault("runtimeStages", runtime_stages)
            self.store_projection_result(snapshot, result, projection_run)
            emit_progress("completed", status=str(result.get("status") or ""))
            return result
        # A staged or targetless legacy activation has no bounded InferenceBox
        # proof to reconcile yet. The latter is finalized as control-only
        # repair, then this cycle stages the current manifest. Avoid reading
        # historical InferenceBox rows before that bounded retry begins.
        if (
            not fresh_candidate_rebuild
            and recovery_status not in {"retry-required", "staged", "finalized-empty-target"}
        ):
            audit_recovery_started = time.perf_counter()
            self.reconcile_interrupted_projection_audit(portfolio_world_context.world_id)
            runtime_stages["interruptedProjectionAuditRecoveryMs"] = int(
                (time.perf_counter() - audit_recovery_started) * 1000
            )
        try:
            emit_progress("rule_catalog.start")
            rulebox_bootstrap_started = time.perf_counter()
            rulebox_bootstrap = self.ensure_rulebox_ready()
            runtime_stages["ruleboxBootstrapMs"] = int(
                (time.perf_counter() - rulebox_bootstrap_started) * 1000
            )
            emit_progress(
                "rule_catalog.done",
                status=str(rulebox_bootstrap.get("status") or ""),
                runtimeMs=runtime_stages["ruleboxBootstrapMs"],
            )
            if str(rulebox_bootstrap.get("status") or "") not in {"ready", "seeded"}:
                result = {
                    "saved": False,
                    "status": "typedb-rule-catalog-not-ready",
                    "reason": str(rulebox_bootstrap.get("reason") or "TypeDB 추론 규칙을 사용할 수 없습니다."),
                    "preservedActiveGeneration": True,
                    "ruleCatalog": rulebox_bootstrap,
                }
                self.store_projection_result(snapshot, result, projection_run)
                emit_progress("blocked", status=result["status"])
                return result
            emit_progress("graph_assembly.start")
            projection_graph = self.build_projection_graph(
                snapshot,
                rulebox_bootstrap,
                portfolio_world_context,
                market_world_context=market_world_context,
                target_symbols=target_symbols,
                target_scoped_input=bool(target_symbols),
                progress_callback=emit_progress,
                shared_premise_proof=shared_premise_proof,
            )
            graph = projection_graph["graph"]
            persistence_graph = projection_graph["persistenceGraph"]
            graph_assembly = projection_graph["assembly"]
            planner_topology = projection_graph["plannerTopology"]
            material_fingerprint = projection_graph["materialFingerprint"]
            material_snapshot_id = projection_graph["materialSnapshotId"]
            scoped_identity = projection_graph["scopedIdentity"]
            runtime_stages.update(dict(projection_graph.get("runtimeStages") or {}))
            if str(graph_assembly.get("inputMode") or "") == "target-scoped":
                # A target-scoped graph is an incremental patch by contract.
                # Missing scopes therefore mean "reuse the active generation".
                # Deletion requires an explicit scoped source fact or operator
                # rebuild; no timer may expand this request to the whole world.
                # This avoids turning one-symbol mailbox work into a complete
                # manifest rewrite while preserving explicit deletion checks
                # on the full projection path.
                persistence_graph.worldview["targetScopeRetentionMode"] = "incremental-target-patch"
            observation_followup_targets = sorted({
                str(symbol or "").upper().strip()
                for symbol in compact_reasoning_context.get("observationFollowupSymbols") or []
                if str(symbol or "").strip()
            }.intersection({
                str(symbol or "").upper().strip()
                for symbol in target_symbols or []
                if str(symbol or "").strip()
            }))
            if observation_followup_targets:
                # A target-scoped source intentionally omits unrelated and
                # temporarily absent target facts. For a raw quote follow-up
                # that omission means "retain the last verified context", not
                # "delete the scope". This keeps the TypeDB operation bounded
                # to the notified quote scopes while TypeDB still evaluates
                # its rules over the merged active ABox.
                persistence_graph.worldview["targetScopeRetentionMode"] = "observation-followup"
                persistence_graph.worldview["observationFollowupTargets"] = observation_followup_targets
            emit_progress(
                "graph_assembly.done",
                cacheLayer=str((graph_assembly or {}).get("cacheLayer") or "none"),
                cacheStatus=str((graph_assembly or {}).get("status") or ""),
                runtimeMs=int(runtime_stages.get("graphBuildMs") or 0),
            )
            graph_input = {
                "mode": str(graph_assembly.get("inputMode") or "full"),
                "targetSymbols": list(graph_assembly.get("targetSymbols") or []),
                "requestedTargetSymbols": sorted({
                    str(symbol or "").upper().strip()
                    for symbol in target_symbols or []
                    if str(symbol or "").strip()
                }),
                "sourcePositionCount": int(graph_assembly.get("sourcePositionCount") or 0),
                "referencePositionCount": int(graph_assembly.get("referencePositionCount") or 0),
                "externalSignalProjection": dict(
                    graph_assembly.get("externalSignalProjection") or {}
                ),
                "fallback": False,
                "fallbackReason": "",
            }
            runtime_stages["targetScopedInputUsed"] = (
                1 if graph_input["mode"] == "target-scoped" else 0
            )
            emit_progress("active_abox_read.start")
            active_abox_started = time.perf_counter()
            active_abox = (
                {}
                if fresh_candidate_rebuild
                else self.active_abox_metadata(portfolio_world_context.world_id)
            )
            runtime_stages["activeAboxReadMs"] = int(
                (time.perf_counter() - active_abox_started) * 1000
            )
            emit_progress(
                "active_abox_read.done",
                status=str(active_abox.get("status") or ""),
                runtimeMs=runtime_stages["activeAboxReadMs"],
            )
            evidence_index_upgrade = {}
            active_abox_complete = str(active_abox.get("status") or "ok") == "ok"
            active_abox_is_scoped_manifest = (
                str(active_abox.get("scopedAboxManifestVersion") or "")
                == SCOPED_ABOX_MANIFEST_VERSION
            )
            emit_progress(
                "target_scope_plan.start",
                requestedTargetSymbolCount=len(target_symbols or []),
            )
            target_scope_plan_started = time.perf_counter()
            target_scoped_patch = self.target_scoped_patch_targets(
                snapshot,
                active_abox,
                scoped_identity,
                target_symbols,
                reasoning_context=compact_reasoning_context,
            )
            runtime_stages["targetScopedPatchPlanningMs"] = int(
                (time.perf_counter() - target_scope_plan_started) * 1000
            )
            emit_progress(
                "target_scope_plan.done",
                status=str(target_scoped_patch.get("status") or ""),
                eligible=bool(target_scoped_patch.get("eligible")),
                runtimeMs=runtime_stages["targetScopedPatchPlanningMs"],
            )
            # A first projection, a scheduled complete reconciliation, or a
            # shared-scope shape that cannot be retained must keep the full
            # source graph. The target input is merely a bounded assembly
            # optimization; it must never turn an unsafe patch into a partial
            # ABox replacement.
            if (
                str(graph_input.get("mode") or "") == "target-scoped"
                and not bool(target_scoped_patch.get("eligible"))
            ):
                emit_progress(
                    "full_input_fallback.start",
                    reason=str(
                        target_scoped_patch.get("fallbackReason")
                        or target_scoped_patch.get("status")
                        or "target-scoped-input-not-eligible"
                    ),
                )
                full_input_fallback_started = time.perf_counter()
                target_attempt_stages = dict(projection_graph.get("runtimeStages") or {})
                full_projection_graph = self.build_projection_graph(
                    snapshot,
                    rulebox_bootstrap,
                    portfolio_world_context,
                    market_world_context=market_world_context,
                    target_symbols=target_symbols,
                    target_scoped_input=False,
                    shared_premise_proof=shared_premise_proof,
                )
                graph = full_projection_graph["graph"]
                persistence_graph = full_projection_graph["persistenceGraph"]
                graph_assembly = full_projection_graph["assembly"]
                planner_topology = full_projection_graph["plannerTopology"]
                material_fingerprint = full_projection_graph["materialFingerprint"]
                material_snapshot_id = full_projection_graph["materialSnapshotId"]
                scoped_identity = full_projection_graph["scopedIdentity"]
                full_runtime_stages = dict(full_projection_graph.get("runtimeStages") or {})
                for stage, value in full_runtime_stages.items():
                    if stage == "graphBuildMs":
                        continue
                    runtime_stages["fullInput" + stage[:1].upper() + stage[1:]] = value
                runtime_stages["targetScopedInputUsed"] = 1
                runtime_stages["targetScopedInputAttemptGraphBuildMs"] = int(
                    target_attempt_stages.get("graphBuildMs") or 0
                )
                runtime_stages["targetScopedInputFallbackGraphBuildMs"] = int(
                    full_runtime_stages.get("graphBuildMs") or 0
                )
                runtime_stages["targetScopedInputFallback"] = 1
                runtime_stages["graphBuildMs"] = (
                    runtime_stages["targetScopedInputAttemptGraphBuildMs"]
                    + runtime_stages["targetScopedInputFallbackGraphBuildMs"]
                )
                runtime_stages["targetScopedInputFallbackTotalMs"] = int(
                    (time.perf_counter() - full_input_fallback_started) * 1000
                )
                emit_progress(
                    "full_input_fallback.done",
                    runtimeMs=runtime_stages["targetScopedInputFallbackTotalMs"],
                )
                graph_input.update({
                    "mode": "full",
                    "targetSymbols": list(graph_assembly.get("targetSymbols") or []),
                    "sourcePositionCount": int(graph_assembly.get("sourcePositionCount") or 0),
                    "referencePositionCount": int(graph_assembly.get("referencePositionCount") or 0),
                    "externalSignalProjection": dict(
                        graph_assembly.get("externalSignalProjection") or {}
                    ),
                    "fallback": True,
                    "fallbackReason": str(
                        target_scoped_patch.get("fallbackReason")
                        or target_scoped_patch.get("status")
                        or "target-scoped-input-not-eligible"
                    ),
                })
                target_scoped_patch = self.target_scoped_patch_targets(
                    snapshot,
                    active_abox,
                    scoped_identity,
                    target_symbols,
                    reasoning_context=compact_reasoning_context,
                )
            # Preserve the semantic scope produced from this immutable source
            # before it is merged with each deployment's older active
            # generations. Shadow parity is about equal inputs; the merged
            # store scope remains a separate diagnostic below.
            source_scope_plan = deepcopy(scoped_identity.get("scopePlan") or [])
            if target_scoped_patch.get("eligible"):
                emit_progress(
                    "target_manifest_patch.start",
                    targetSymbolCount=len(target_scoped_patch.get("targetSymbols") or []),
                )
                target_patch_started = time.perf_counter()
                scope_repair = apply_scoped_abox_repair_epochs(
                    persistence_graph,
                    active_abox,
                    compact_reasoning_context.get("scopeRepairRequestsBySymbol") or {},
                )
                applied_target_patch = merge_target_scoped_abox_manifest(
                    persistence_graph,
                    active_abox,
                    target_scoped_patch.get("targetSymbols") or [],
                    fact_slot_plan=target_scoped_patch.get("factSlotPlan") or {},
                )
                repair_input_fallback = {}
                if (
                    not applied_target_patch.get("applied")
                    and str(graph_input.get("mode") or "") == "target-scoped"
                ):
                    # A scoped source can legitimately omit a shared endpoint
                    # that is retained by the active Manifest. Reassemble the
                    # complete source in memory once, then persist only the
                    # originally requested target patch. This repairs the
                    # source boundary without turning local work into a full
                    # TypeDB world rewrite.
                    emit_progress(
                        "target_manifest_repair_input.start",
                        status=str(applied_target_patch.get("status") or "repair-required"),
                    )
                    repair_input_started = time.perf_counter()
                    first_patch_failure = dict(applied_target_patch or {})
                    repair_projection_graph = self.build_projection_graph(
                        snapshot,
                        rulebox_bootstrap,
                        portfolio_world_context,
                        market_world_context=market_world_context,
                        target_symbols=target_symbols,
                        target_scoped_input=False,
                        shared_premise_proof=shared_premise_proof,
                    )
                    graph = repair_projection_graph["graph"]
                    persistence_graph = repair_projection_graph["persistenceGraph"]
                    graph_assembly = repair_projection_graph["assembly"]
                    planner_topology = repair_projection_graph["plannerTopology"]
                    material_fingerprint = repair_projection_graph["materialFingerprint"]
                    material_snapshot_id = repair_projection_graph["materialSnapshotId"]
                    scoped_identity = repair_projection_graph["scopedIdentity"]
                    persistence_graph.worldview["targetScopeRetentionMode"] = (
                        "observation-followup"
                        if observation_followup_targets
                        else "incremental-target-patch"
                    )
                    if observation_followup_targets:
                        persistence_graph.worldview["observationFollowupTargets"] = list(
                            observation_followup_targets
                        )
                    target_scoped_patch = self.target_scoped_patch_targets(
                        snapshot,
                        active_abox,
                        scoped_identity,
                        target_symbols,
                        reasoning_context=compact_reasoning_context,
                    )
                    scope_repair = apply_scoped_abox_repair_epochs(
                        persistence_graph,
                        active_abox,
                        compact_reasoning_context.get("scopeRepairRequestsBySymbol") or {},
                    )
                    applied_target_patch = merge_target_scoped_abox_manifest(
                        persistence_graph,
                        active_abox,
                        target_scoped_patch.get("targetSymbols") or [],
                        fact_slot_plan=target_scoped_patch.get("factSlotPlan") or {},
                    )
                    repair_runtime_stages = dict(
                        repair_projection_graph.get("runtimeStages") or {}
                    )
                    for stage, value in repair_runtime_stages.items():
                        runtime_stages[
                            "targetManifestRepairInput" + stage[:1].upper() + stage[1:]
                        ] = value
                    runtime_stages["targetManifestRepairInputMs"] = int(
                        (time.perf_counter() - repair_input_started) * 1000
                    )
                    repair_input_fallback = {
                        "attempted": True,
                        "mode": "complete-source-assembly-target-persist",
                        "firstStatus": str(first_patch_failure.get("status") or ""),
                        "firstMissingEndpointScopeIds": list(
                            first_patch_failure.get("missingEndpointScopeIds") or []
                        )[:50],
                        "finalStatus": str(applied_target_patch.get("status") or ""),
                        "applied": bool(applied_target_patch.get("applied")),
                        "runtimeMs": runtime_stages["targetManifestRepairInputMs"],
                        "automaticFullProjectionBlocked": True,
                    }
                    graph_input.update({
                        "repairInputFallback": True,
                        "repairInputMode": "complete-source-assembly-target-persist",
                        "repairInputStatus": str(applied_target_patch.get("status") or ""),
                    })
                    emit_progress(
                        "target_manifest_repair_input.done",
                        status=str(applied_target_patch.get("status") or ""),
                        applied=bool(applied_target_patch.get("applied")),
                        runtimeMs=runtime_stages["targetManifestRepairInputMs"],
                    )
                runtime_stages["targetScopedManifestPatchMs"] = int(
                    (time.perf_counter() - target_patch_started) * 1000
                )
                emit_progress(
                    "target_manifest_patch.done",
                    status=str(applied_target_patch.get("status") or ""),
                    applied=bool(applied_target_patch.get("applied")),
                    runtimeMs=runtime_stages["targetScopedManifestPatchMs"],
                )
                if applied_target_patch.get("applied"):
                    # The source graph can contain newer observations for
                    # deferred symbols. The persisted identity must describe
                    # the merged active manifest, not facts intentionally held
                    # for their own target cycle.
                    incoming_planner_topology = dict(planner_topology or {})
                    semantic_noop_patch = bool(
                        not applied_target_patch.get("selectedIncomingScopeIds")
                        and not applied_target_patch.get("retiredScopeIds")
                    )
                    active_planner_topology = dict(
                        active_abox.get("nativeRulePlannerTopology") or {}
                    )
                    if semantic_noop_patch and str(
                        active_planner_topology.get("status") or ""
                    ) == "ok":
                        topology_merge = {
                            "status": "ok",
                            "reason": "No semantic scope changed; the verified active planner topology is reusable.",
                            "topology": active_planner_topology,
                            "replacedSymbols": [],
                            "retainedSymbols": list(
                                active_planner_topology.get("symbols") or []
                            ),
                            "activeSymbolCount": int(
                                active_planner_topology.get("symbolCount") or 0
                            ),
                            "incomingSymbolCount": int(
                                incoming_planner_topology.get("symbolCount") or 0
                            ),
                            "mergedSymbolCount": int(
                                active_planner_topology.get("symbolCount") or 0
                            ),
                            "semanticNoopReuse": True,
                        }
                    else:
                        topology_merge = merge_native_rule_planner_topology(
                            active_planner_topology,
                            incoming_planner_topology,
                            target_scoped_patch.get("targetSymbols") or [],
                        )
                    merged_topology_available = str(topology_merge.get("status") or "") == "ok"
                    planner_topology = dict(
                        topology_merge.get("topology") if merged_topology_available else incoming_planner_topology
                    )
                    if merged_topology_available:
                        persistence_graph.worldview["nativeRulePlannerTopologyIncoming"] = incoming_planner_topology
                    else:
                        # Older markers may predate the structural index. Keep
                        # this target correct through active-membership
                        # fallback, then establish the complete merged index on
                        # the next eligible scoped or full projection.
                        persistence_graph.worldview.pop("nativeRulePlannerTopologyIncoming", None)
                    persistence_graph.worldview["nativeRulePlannerTopology"] = planner_topology
                    persistence_graph.worldview["nativeRulePlannerTopologyMerge"] = {
                        key: topology_merge.get(key)
                        for key in [
                            "status", "reason", "replacedSymbols", "retainedSymbols",
                            "activeSymbolCount", "incomingSymbolCount", "mergedSymbolCount",
                            "semanticNoopReuse",
                        ]
                    }
                    material_fingerprint = native_rule_planner_manifest_fingerprint(
                        applied_target_patch.get("scopeManifestFingerprint"),
                        planner_topology,
                    )
                    scoped_identity = apply_scoped_manifest_plan(
                        persistence_graph,
                        applied_target_patch.get("scopePlan") or [],
                        account_id=snapshot.account_id,
                        world_id=portfolio_world_context.world_id,
                        material_fingerprint=material_fingerprint,
                    )
                    material_snapshot_id = str(
                        scoped_identity.get("manifestId") or material_snapshot_id
                    )
                    raw_scope_trace = dict(
                        applied_target_patch.get("scopeSelectionTrace") or {}
                    )
                    scope_selection_trace = {
                        "version": str(raw_scope_trace.get("version") or ""),
                        "selected": [
                            dict(item)
                            for item in (raw_scope_trace.get("selected") or [])[:40]
                            if isinstance(item, dict)
                        ],
                        "deferred": [
                            dict(item)
                            for item in (raw_scope_trace.get("deferred") or [])[:40]
                            if isinstance(item, dict)
                        ],
                    }
                    target_scoped_patch = {
                        "status": "applied",
                        "mode": "incremental-target-scoped-manifest-patch",
                        "targetSymbols": list(applied_target_patch.get("targetSymbols") or []),
                        "selectedIncomingScopeCount": len(
                            applied_target_patch.get("selectedIncomingScopeIds") or []
                        ),
                        "semanticNoop": semantic_noop_patch,
                        "reusedActiveScopeCount": len(
                            applied_target_patch.get("reusedActiveScopeIds") or []
                        ),
                        "deferredScopeCount": len(
                            applied_target_patch.get("deferredScopeIds") or []
                        ),
                        "retiredScopeIds": list(
                            applied_target_patch.get("retiredScopeIds") or []
                        ),
                        "scopeTopologyVersion": str(
                            (persistence_graph.worldview or {}).get("scopeTopologyVersion") or ""
                        ),
                        "scopeTopologyMigration": dict(
                            applied_target_patch.get("scopeTopologyMigration") or {}
                        ),
                        "boundedScopeCount": len([
                            item for item in applied_target_patch.get("scopePlan") or []
                            if ":bucket:" in str(item.get("scopeId") or "")
                            or ":window:" in str(item.get("scopeId") or "")
                        ]),
                        "selectedBoundedScopeCount": len([
                            scope_id
                            for scope_id in applied_target_patch.get("selectedIncomingScopeIds") or []
                            if ":bucket:" in str(scope_id) or ":window:" in str(scope_id)
                        ]),
                        "factSlotStatus": str(
                            (applied_target_patch.get("factSlot") or {}).get("status") or ""
                        ),
                        "factSlotSelectedScopeCount": len(
                            (applied_target_patch.get("factSlot") or {}).get("selectedScopeIds") or []
                        ),
                        "factSlotDeferredScopeCount": len(
                            (applied_target_patch.get("factSlot") or {}).get("deferredScopeIds") or []
                        ),
                        "factSlotFamilies": list(
                            (applied_target_patch.get("factSlot") or {}).get("slotFamilies") or []
                        )[:20],
                        "factSlotFamiliesBySymbol": dict(
                            (applied_target_patch.get("factSlot") or {}).get("slotFamiliesBySymbol") or {}
                        ),
                        "factSlotChangedFieldsBySymbol": dict(
                            (applied_target_patch.get("factSlot") or {}).get("changedFieldsBySymbol") or {}
                        ),
                        "factSlotPreciseFieldRoutingSymbols": list(
                            (applied_target_patch.get("factSlot") or {}).get("preciseFieldRoutingSymbols") or []
                        )[:20],
                        "factSlotUnclassifiedChangedFieldsBySymbol": dict(
                            (applied_target_patch.get("factSlot") or {}).get(
                                "unclassifiedChangedFieldsBySymbol"
                            ) or {}
                        ),
                        "factSlotFallbackReason": str(
                            (applied_target_patch.get("factSlot") or {}).get("fallbackReason") or ""
                        ),
                        "scopeSelectionTrace": scope_selection_trace,
                        "scopeIntegrityAuditIntervalMinutes": self.scope_integrity_audit_interval_minutes(),
                        "scopeIntegrityAuditDue": bool(
                            target_scoped_patch.get("scopeIntegrityAuditDue")
                        ),
                        "scopeRepair": {
                            key: scope_repair.get(key)
                            for key in [
                                "status", "applied", "requestedScopeIds",
                                "repairedScopeIds", "retainedRepairScopeIds",
                            ]
                            if key in scope_repair
                        },
                        "repairInputFallback": dict(repair_input_fallback),
                        "automaticFullProjectionBlocked": True,
                    }
                    persistence_graph.worldview["targetScopedManifestPatch"] = dict(target_scoped_patch)
                elif str(graph_input.get("mode") or "") == "target-scoped":
                    # A local event must never become a whole-world write merely
                    # because its incremental merge needs repair. Preserve the
                    # active Manifest and surface the exact scope failure. An
                    # operator can run the explicit rebuild path for a topology
                    # migration; normal workers remain bounded by subject.
                    result = {
                        "saved": False,
                        "status": "target-scope-repair-required",
                        "reason": "Target-scoped Manifest patch could not be applied safely.",
                        "graphStore": self.active_graph_store_key(),
                        "preservedActiveGeneration": True,
                        "recommendedRetryAfterSeconds": 60,
                        "graphInput": graph_input,
                        "targetScopedManifestPatch": {
                            "status": str(applied_target_patch.get("status") or "repair-required"),
                            "mode": "target-scope-repair-required",
                            "targetSymbols": list(target_scoped_patch.get("targetSymbols") or []),
                            "incomingScopeCount": int(
                                applied_target_patch.get("incomingScopeCount") or 0
                            ),
                            "activeScopeCount": int(
                                applied_target_patch.get("activeScopeCount") or 0
                            ),
                            "missingEndpointScopeIds": list(
                                applied_target_patch.get("missingEndpointScopeIds") or []
                            )[:50],
                            "removedRelevantScopeIds": list(
                                applied_target_patch.get("removedRelevantScopeIds") or []
                            )[:50],
                            "sharedRemovedScopeIds": list(
                                applied_target_patch.get("sharedRemovedScopeIds") or []
                            )[:50],
                            "retiredScopeIds": list(
                                applied_target_patch.get("retiredScopeIds") or []
                            )[:50],
                            "scopeTopologyMigration": dict(
                                applied_target_patch.get("scopeTopologyMigration") or {}
                            ),
                            "retainedDependencyScopeIds": list(
                                applied_target_patch.get("retainedDependencyScopeIds") or []
                            )[:50],
                            "selectedDependencyScopeIds": list(
                                applied_target_patch.get("selectedDependencyScopeIds") or []
                            )[:50],
                            "factSlot": dict(applied_target_patch.get("factSlot") or {}),
                            "fallbackReason": str(
                                applied_target_patch.get("fallbackReason")
                                or applied_target_patch.get("status")
                                or "target-scoped-manifest-patch-not-applied"
                            ),
                            "repairInputFallback": dict(repair_input_fallback),
                            "automaticFullProjectionBlocked": True,
                        },
                    }
                    self.store_projection_result(snapshot, result, projection_run)
                    return result
                else:
                    target_scoped_patch = {
                        "status": str(applied_target_patch.get("status") or "skipped"),
                        "mode": "full-manifest-fallback",
                        "targetSymbols": list(target_scoped_patch.get("targetSymbols") or []),
                        "fallbackReason": str(
                            applied_target_patch.get("fallbackReason")
                            or applied_target_patch.get("status")
                            or "target-scoped-manifest-patch-not-applied"
                        ),
                        "selectedIncomingScopeCount": len(
                            applied_target_patch.get("selectedIncomingScopeIds") or []
                        ),
                        "deferredScopeCount": len(
                            applied_target_patch.get("deferredScopeIds") or []
                        ),
                        "scopeTopologyMigration": dict(
                            applied_target_patch.get("scopeTopologyMigration") or {}
                        ),
                    }
            emit_progress("abox_validation.start")
            validation_started = time.perf_counter()
            validation = validate_ontology(persistence_graph)
            runtime_stages["aboxValidationMs"] = int((time.perf_counter() - validation_started) * 1000)
            emit_progress(
                "abox_validation.done",
                status=validation.status,
                errorCount=validation.error_count,
                runtimeMs=runtime_stages["aboxValidationMs"],
            )
            if validation.error_count:
                result = {
                    "saved": False,
                    "status": "invalid-abox",
                    "reason": "ABox validation failed before graph-store persistence.",
                    "graphStore": self.active_graph_store_key(),
                    "aboxValidation": validation.to_dict(),
                    "graphInput": graph_input,
                }
                self.store_projection_result(snapshot, result, projection_run)
                return result
            # Preserve the exact incremental path or safe fallback in the
            # manifest, so operational diagnostics do not infer it later.
            persistence_graph.worldview["targetScopedManifestPatch"] = dict(target_scoped_patch)
            persistence_graph.worldview["factSlotProjection"] = {
                "status": str(target_scoped_patch.get("factSlotStatus") or "not-applied"),
                "selectedScopeCount": int(target_scoped_patch.get("factSlotSelectedScopeCount") or 0),
                "deferredScopeCount": int(target_scoped_patch.get("factSlotDeferredScopeCount") or 0),
                "slotFamilies": list(target_scoped_patch.get("factSlotFamilies") or [])[:20],
                "slotFamiliesBySymbol": dict(
                    target_scoped_patch.get("factSlotFamiliesBySymbol") or {}
                ),
                "changedFieldsBySymbol": dict(
                    target_scoped_patch.get("factSlotChangedFieldsBySymbol") or {}
                ),
                "preciseFieldRoutingSymbols": list(
                    target_scoped_patch.get("factSlotPreciseFieldRoutingSymbols") or []
                )[:20],
                "unclassifiedChangedFieldsBySymbol": dict(
                    target_scoped_patch.get("factSlotUnclassifiedChangedFieldsBySymbol") or {}
                ),
                "fallbackReason": str(target_scoped_patch.get("factSlotFallbackReason") or ""),
            }
            if str(target_scoped_patch.get("status") or "") == "applied":
                full_reconcile_at = str(
                    active_abox.get("lastFullScopeReconcileAt")
                    or active_abox.get("asOf")
                    or ""
                ).strip()
            else:
                full_reconcile_at = str(
                    getattr(snapshot, "generated_at", "")
                    or persistence_graph.worldview.get("asOf")
                    or ""
                ).strip()
            if full_reconcile_at:
                persistence_graph.worldview["lastFullScopeReconcileAt"] = full_reconcile_at
            # A rolling deployment can encounter an already active immutable
            # ABox that predates the exact physical evidence-read index. The
            # index is marker metadata derived from this same verified graph;
            # it does not alter market facts or native rule semantics.
            if (
                active_abox_complete
                and active_abox_is_scoped_manifest
                and active_material_fingerprint(active_abox) == material_fingerprint
            ):
                upgrader = getattr(self.repository, "ensure_scoped_manifest_evidence_read_index", None)
                if callable(upgrader):
                    index_upgrade_started = time.perf_counter()
                    try:
                        evidence_index_upgrade = self.repository_world_call(
                            "ensure_scoped_manifest_evidence_read_index",
                            persistence_graph,
                            active_metadata=active_abox,
                            world_id=portfolio_world_context.world_id,
                        )
                    except Exception as error:  # noqa: BLE001 - do not run a new judgement without exact current evidence.
                        evidence_index_upgrade = {
                            "configured": True,
                            "saved": False,
                            "status": "error",
                            "reason": str(error)[:180],
                        }
                    runtime_stages["manifestEvidenceIndexUpgradeMs"] = int(
                        (time.perf_counter() - index_upgrade_started) * 1000
                    )
                    upgrade_status = str(evidence_index_upgrade.get("status") or "")
                    if upgrade_status in {"ok", "unchanged"}:
                        active_abox = self.active_abox_metadata(portfolio_world_context.world_id)
                    else:
                        result = {
                            "saved": False,
                            "status": "manifest-evidence-index-upgrade-pending",
                            "reason": (
                                "현재 ABox의 근거 조회 인덱스를 안전하게 보강하지 못해 새 투자 판단을 보류했습니다. "
                                + str(evidence_index_upgrade.get("reason") or upgrade_status)[:180]
                            ),
                            "graphStore": self.active_graph_store_key(),
                            "materialFingerprint": material_fingerprint,
                            "aboxSnapshotId": str(active_abox.get("aboxSnapshotId") or material_snapshot_id),
                            "preservedActiveGeneration": True,
                            "materialChangeDetected": False,
                            "aboxValidation": validation.to_dict(),
                            "manifestEvidenceIndexUpgrade": evidence_index_upgrade,
                            "runtimeStages": runtime_stages,
                            "ontologyWorld": world_metadata(portfolio_world_context),
                        }
                        self.store_projection_result(snapshot, result)
                        return result
            emit_progress("impact_planning.start")
            impact_planning_started = time.perf_counter()
            inference_impact_plan = self.inference_impact_plan(
                snapshot,
                active_abox,
                scoped_identity,
                target_symbols,
                reasoning_context=compact_reasoning_context,
            )
            compact_impact_plan = compact_inference_impact_plan(inference_impact_plan)
            world_impact_route = route_world_impact(
                compact_impact_plan,
                initial_projection=not bool(active_abox.get("aboxSnapshotId")),
            )
            explicit_inference_symbols = (
                self.inference_symbols(snapshot, target_symbols)
                if target_symbols
                else []
            )
            inference_symbols = explicit_inference_symbols or self.inference_symbols(
                snapshot,
                inference_impact_plan.get("inferenceTargetSymbols") or target_symbols,
            )
            scheduler_target_limit = self.scheduler_target_symbol_limit(compact_reasoning_context)
            inference_symbols = self.bounded_native_inference_symbols(
                snapshot,
                inference_symbols,
                target_symbols,
                scheduler_target_symbol_limit=scheduler_target_limit,
            )
            runtime_stages["impactPlanningMs"] = int((time.perf_counter() - impact_planning_started) * 1000)
            emit_progress(
                "impact_planning.done",
                targetSymbolCount=len(inference_symbols or []),
                runtimeMs=runtime_stages["impactPlanningMs"],
            )
            persistence_graph.worldview["scopeDelta"] = dict(compact_impact_plan.get("scopeDelta") or {})
            persistence_graph.worldview["inferenceImpactPlan"] = compact_impact_plan
            persistence_graph.worldview["worldImpactRoute"] = world_impact_route
            projection_scope = {
                "triggerMode": "scope-change-impact-native",
                "targetSymbols": list(inference_symbols),
                "schedulerTargetSymbolLimit": scheduler_target_limit,
                "explicitTargetSymbols": list(compact_impact_plan.get("explicitTargetSymbols") or []),
                "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
                "atomicActivation": True,
                "manifestId": material_snapshot_id,
                "worldId": portfolio_world_context.world_id,
                "marketWorldId": market_world_context.world_id,
                "scopeCount": len(scoped_identity.get("scopePlan") or []),
                "scopeFamilyCounts": dict(scoped_identity.get("scopeFamilyCounts") or {}),
                "scopeTopologyVersion": str(persistence_graph.worldview.get("scopeTopologyVersion") or ""),
                "targetScopedManifestPatch": dict(target_scoped_patch or {}),
                "graphInput": dict(graph_input),
                "inferenceImpactPlan": compact_impact_plan,
                "worldImpactRoute": world_impact_route,
                "reasoningContext": compact_reasoning_context,
                "reason": (
                    "변경된 사실군과 ABox 의존 관계에서 재평가 대상을 계산하고, 변경 범위만 새 세대로 기록한 뒤 "
                    "대상별 TypeDB 네이티브 규칙을 완전 평가합니다."
                ),
            }
            comparison_scope = target_scope_manifest_fingerprint(
                source_scope_plan,
                inference_symbols,
            )
            persisted_comparison_scope = target_scope_manifest_fingerprint(
                scoped_identity.get("scopePlan") or [],
                inference_symbols,
            )
            # Identical facts must still be persisted once when upgrading from
            # the legacy complete-generation pointer. Otherwise a quiet market
            # could leave the old full-rewrite ABox active indefinitely.
            if (
                active_abox_complete
                and active_abox_is_scoped_manifest
                and active_material_fingerprint(active_abox) == material_fingerprint
            ):
                inferencebox = self.existing_inference_result(
                    snapshot,
                    inference_symbols,
                    world_id=portfolio_world_context.world_id,
                )
                result = {
                    "saved": False,
                    "status": (
                        "unchanged-material-facts"
                        if self.inference_result_is_reusable(
                            inferencebox,
                            active_abox,
                            inference_symbols,
                        )
                        else "unchanged-material-facts-reasoning-retry"
                    ),
                    "reason": (
                        "가격·손익·수급·뉴스·신선도 등 추론 입력이 직전 ABox와 같습니다."
                        if self.inference_result_is_reusable(
                            inferencebox,
                            active_abox,
                            inference_symbols,
                        )
                        else "ABox 입력은 같지만 정상적으로 정렬된 InferenceBox가 없어 추론을 다시 실행합니다."
                    ),
                    "graphStore": self.active_graph_store_key(),
                    "materialFingerprint": material_fingerprint,
                    "aboxSnapshotId": str(active_abox.get("aboxSnapshotId") or material_snapshot_id),
                    "preservedActiveGeneration": True,
                    "materialChangeDetected": False,
                    "aboxValidation": validation.to_dict(),
                    "projectionScope": projection_scope,
                    "comparisonScope": comparison_scope,
                    "persistedComparisonScope": persisted_comparison_scope,
                    "inferenceImpactPlan": compact_impact_plan,
                    "reasoningContext": compact_reasoning_context,
                    "runtimeStages": runtime_stages,
                    "ontologyWorld": world_metadata(portfolio_world_context),
                    "marketWorld": {
                        **world_metadata(market_world_context),
                        "status": "unchanged-source-not-reprojected",
                    },
                }
                if rulebox_bootstrap:
                    result["ruleboxBootstrap"] = rulebox_bootstrap
                if evidence_index_upgrade:
                    result["manifestEvidenceIndexUpgrade"] = evidence_index_upgrade
                if pending_activation_recovery:
                    result["pendingAboxActivationRecovery"] = pending_activation_recovery
                if self.inference_result_is_reusable(
                    inferencebox,
                    active_abox,
                    inference_symbols,
                ):
                    inferencebox["reusedForUnchangedMaterialFacts"] = True
                    result["inferenceBox"] = inferencebox
                else:
                    result["reasoningRetryRequired"] = True
                    result["previousInferenceStatus"] = str(inferencebox.get("status") or "missing")
                    self.attach_graph_store_inference_result(
                        result,
                        snapshot,
                        inference_symbols,
                        compact_impact_plan,
                        world_id=portfolio_world_context.world_id,
                        candidate_scope_plan=active_abox.get("scopePlan") or scoped_identity.get("scopePlan") or [],
                        rulebox_rules_hash=str(rulebox_bootstrap.get("ruleboxRulesHash") or ""),
                        tbox_fingerprint=str(
                            ((persistence_graph.worldview or {}).get("activeTBox") or {}).get("fingerprint")
                            or ""
                        ),
                        preflight_graph=persistence_graph,
                        preflight_manifest_id=str(
                            (persistence_graph.worldview or {}).get("worldviewManifestId")
                            or material_snapshot_id
                        ),
                    )
                self.store_projection_result(snapshot, result)
                return result
            projection_audit_started = time.perf_counter()
            projection_run, audit_error = self.begin_projection_audit_run(
                snapshot,
                persistence_graph,
                material_fingerprint,
                material_snapshot_id,
                inference_symbols=inference_symbols,
                rulebox_metadata=rulebox_bootstrap,
                reasoning_context=compact_reasoning_context,
            )
            runtime_stages["projectionAuditCreateMs"] = int(
                (time.perf_counter() - projection_audit_started) * 1000
            )
            if audit_error:
                result = {
                    "saved": False,
                    "status": "source-audit-failed",
                    "reason": "MySQL source audit must succeed before the active ABox can change: " + audit_error,
                    "graphStore": self.active_graph_store_key(),
                    "materialFingerprint": material_fingerprint,
                    "aboxSnapshotId": material_snapshot_id,
                    "materialChangeDetected": True,
                    "preservedActiveGeneration": True,
                    "aboxValidation": validation.to_dict(),
                }
                self.store_projection_result(snapshot, result)
                return result
            # Target subjects do not change the material ABox identity. They
            # are persisted only in the activation journal so a restart can
            # verify that the eventual native InferenceBox covered the exact
            # requested incremental scope before predecessor cleanup.
            persistence_graph.worldview["inferenceTargetSymbols"] = list(inference_symbols)
            coordinator_lease = self.acquire_projection_coordinator_lease(
                "portfolio:" + material_snapshot_id,
                portfolio_world_context.world_id,
            )
            if not bool(coordinator_lease.get("acquired")):
                result = {
                    "saved": False,
                    "status": "deferred-projection-coordinator",
                    "reason": str(
                        coordinator_lease.get("reason")
                        or "다른 World 투영이 TypeDB 데이터베이스 쓰기 경계를 사용 중입니다."
                    )[:220],
                    "retryable": True,
                    "recommendedRetryAfterSeconds": int(
                        coordinator_lease.get("recommendedRetryAfterSeconds") or 10
                    ),
                    "preservedActiveGeneration": True,
                    "materialFingerprint": material_fingerprint,
                    "aboxSnapshotId": material_snapshot_id,
                    "projectionScope": projection_scope,
                    "inferenceImpactPlan": compact_impact_plan,
                    "reasoningContext": compact_reasoning_context,
                    "aboxValidation": validation.to_dict(),
                    "runtimeStages": runtime_stages,
                    "ontologyWorld": world_metadata(portfolio_world_context),
                    "projectionCoordinator": self.projection_coordinator_summary(coordinator_lease),
                }
                self.store_projection_result(snapshot, result, projection_run)
                return result
            result: Dict[str, object] = {}
            coordinator_release = {}
            try:
                emit_progress(
                    "abox_persistence.start",
                    targetSymbolCount=len(inference_symbols or []),
                    inputMode=str(graph_input.get("mode") or "full"),
                )
                abox_persistence_started = time.perf_counter()
                result = self.repository.save_graph(persistence_graph)
                runtime_stages["aboxPersistenceMs"] = int((time.perf_counter() - abox_persistence_started) * 1000)
                if not isinstance(result, dict):
                    result = {"saved": False, "status": "error", "reason": "ontology repository returned non-dict result"}
                if projection_run:
                    result["projectionRunId"] = projection_run.run_id
                self.attach_abox_persistence_runtime_stages(runtime_stages, result)
                result["projectionMode"] = "abox-facts-only-typedb-native-rules"
                result["materialFingerprint"] = material_fingerprint
                result["aboxSnapshotId"] = material_snapshot_id
                result["nativeRulePlannerTopology"] = dict(
                    persistence_graph.worldview.get("nativeRulePlannerTopology") or {}
                )
                result["materialChangeDetected"] = True
                result["projectionScope"] = projection_scope
                result["comparisonScope"] = comparison_scope
                result["persistedComparisonScope"] = persisted_comparison_scope
                result["graphInput"] = dict(graph_input)
                result["inferenceImpactPlan"] = compact_impact_plan
                result["reasoningContext"] = compact_reasoning_context
                result["aboxValidation"] = validation.to_dict()
                result["runtimeStages"] = runtime_stages
                result["ontologyWorld"] = world_metadata(portfolio_world_context)
                result["_projectionCoordinatorLease"] = coordinator_lease
                if rulebox_bootstrap:
                    result["ruleboxBootstrap"] = rulebox_bootstrap
                if pending_activation_recovery:
                    result["pendingAboxActivationRecovery"] = pending_activation_recovery
                save_status = str(result.get("status") or "")
                emit_progress(
                    "abox_persistence.done",
                    status=save_status,
                    saved=bool(result.get("saved")),
                    runtimeMs=runtime_stages["aboxPersistenceMs"],
                )
                # Candidate ABox writes have committed at this point and the
                # pending activation journal protects this world.  Do not
                # hold the database-wide writer coordinator while TypeDB
                # prepares read-side native rule candidates.  The inference
                # materialization claims its own short coordinator scope.
                # This lets an unrelated world stage its next bounded patch
                # instead of waiting behind a whole account inference cycle.
                if bool(coordinator_lease.get("acquired")):
                    early_coordinator_release = self.release_projection_coordinator_lease(
                        coordinator_lease
                    )
                    result["projectionCoordinatorPersistenceRelease"] = early_coordinator_release
                    result.pop("_projectionCoordinatorLease", None)
                    coordinator_lease = {
                        **coordinator_lease,
                        "acquired": False,
                        "status": "released-after-abox-persistence",
                    }
                if result.get("saved") or save_status == "staged-scoped-manifest":
                    pending = result.get("pendingAboxActivation") if isinstance(result.get("pendingAboxActivation"), dict) else {}
                    emit_progress(
                        "native_inference.start",
                        targetSymbolCount=len(pending.get("targetSymbols") or inference_symbols or []),
                    )
                    self.attach_graph_store_inference_result(
                        result,
                        snapshot,
                        pending.get("targetSymbols") or inference_symbols,
                        compact_impact_plan,
                        world_id=portfolio_world_context.world_id,
                        candidate_scope_plan=scoped_identity.get("scopePlan") or [],
                        rulebox_rules_hash=str(rulebox_bootstrap.get("ruleboxRulesHash") or ""),
                        tbox_fingerprint=str(
                            ((persistence_graph.worldview or {}).get("activeTBox") or {}).get("fingerprint")
                            or ""
                        ),
                        preflight_graph=persistence_graph,
                        preflight_manifest_id=str(
                            (persistence_graph.worldview or {}).get("worldviewManifestId")
                            or material_snapshot_id
                        ),
                    )
                    emit_progress(
                        "native_inference.done",
                        status=str(
                            ((result.get("inferenceBox") or {}).get("status"))
                            if isinstance(result.get("inferenceBox"), dict)
                            else result.get("status") or ""
                        ),
                        runtimeMs=int((result.get("runtimeStages") or {}).get("nativeInferenceMs") or 0),
                    )
                elif save_status == "deferred-pending-scoped-manifest":
                    # This input did not stage the pending candidate. Running
                    # native rules with its graph would compare a new Manifest
                    # to another writer's journal and create a false rollback.
                    result["retryable"] = True
                    result["recommendedRetryAfterSeconds"] = int(
                        result.get("recommendedRetryAfterSeconds") or 10
                    )
                    result["pendingManifestOwner"] = "another-projection"
            finally:
                coordinator_release = self.release_projection_coordinator_lease(coordinator_lease)
                if isinstance(result, dict):
                    result.pop("_projectionCoordinatorLease", None)
                    result["projectionCoordinator"] = self.projection_coordinator_summary(coordinator_lease)
                    result["projectionCoordinatorRelease"] = coordinator_release
            market_projection_started = time.perf_counter()
            result["worldImpactRoute"] = world_impact_route
            if bool(result.get("saved")) and bool(world_impact_route.get("market", {}).get("required")):
                # MarketWorld is an account-independent derived mirror.  It
                # is intentionally scheduled only after the portfolio ABox
                # and its decision-critical TypeDB inference are verified.
                # This keeps a slow shared write out of the alert path while
                # never letting an unverified account projection publish
                # facts to the shared world.
                result["marketWorld"] = self.schedule_market_world_projection(
                    graph,
                    market_world_context,
                    source_world=portfolio_world_context,
                )
            elif bool(result.get("saved")):
                result["marketWorld"] = {
                    **world_metadata(market_world_context),
                    "status": "skipped-world-impact-route",
                    "preservedActiveGeneration": True,
                    "reason": str(world_impact_route.get("market", {}).get("reason") or ""),
                }
            if bool(result.get("saved")) and bool(world_impact_route.get("knowledge", {}).get("required")):
                result["knowledgeWorld"] = self.schedule_knowledge_world_projection(
                    graph,
                    knowledge_world_context,
                    source_world=portfolio_world_context,
                )
            elif bool(result.get("saved")):
                result["knowledgeWorld"] = {
                    **world_metadata(knowledge_world_context),
                    "status": "skipped-world-impact-route",
                    "preservedActiveGeneration": True,
                    "reason": str(world_impact_route.get("knowledge", {}).get("reason") or ""),
                }
            else:
                result["marketWorld"] = {
                    **world_metadata(market_world_context),
                    "status": "deferred-portfolio-inference-not-verified",
                    "preservedActiveGeneration": True,
                    "reason": "계좌 ABox 또는 TypeDB 추론이 확정되지 않아 공용 시장 읽기 모델 갱신을 건너뛰었습니다.",
                }
                result["knowledgeWorld"] = {
                    **world_metadata(knowledge_world_context),
                    "status": "deferred-portfolio-inference-not-verified",
                    "preservedActiveGeneration": True,
                    "reason": "계좌 ABox 또는 TypeDB 추론이 확정되지 않아 공용 지식 세계 갱신을 건너뛰었습니다.",
                }
            runtime_stages["marketWorldQueueMs"] = int((time.perf_counter() - market_projection_started) * 1000)
            if self.quality_store:
                quality_started = time.perf_counter()
                if self.async_quality_record_enabled():
                    result["qualityRecord"] = SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR.enqueue(
                        self.quality_store,
                        graph,
                        self.source,
                    )
                    runtime_stages["qualityRecordQueueMs"] = int(
                        (time.perf_counter() - quality_started) * 1000
                    )
                else:
                    sample = self.quality_store.record_graph(graph, source=self.source)
                    runtime_stages["qualityRecordMs"] = int((time.perf_counter() - quality_started) * 1000)
                    result["qualitySampleId"] = getattr(sample, "sample_id", "")
                    result["qualityState"] = (
                        getattr(sample, "overall_state", "")
                        or getattr(sample, "overall_score", "")
                    )
        except Exception as error:  # noqa: BLE001 - ontology projection must not block realtime monitoring.
            result = {
                "saved": False,
                "status": "error",
                "reason": str(error)[:180],
                "errorType": type(error).__name__,
                "failureStage": current_stage,
                "errorTrace": traceback.format_exc()[-2000:],
            }
            emit_progress("error", status="error", reason=str(error)[:180])
        runtime_stages["totalMs"] = int((time.perf_counter() - projection_started) * 1000)
        result.setdefault("runtimeStages", runtime_stages)
        self.store_projection_result(snapshot, result, projection_run)
        emit_progress(
            "completed",
            status=str(result.get("status") or ""),
            saved=bool(result.get("saved")),
            runtimeMs=runtime_stages["totalMs"],
        )
        return result

    def repository_world_call(self, method_name: str, *args, world_id: str = "", **kwargs):
        """Call a world-aware adapter while retaining narrow test adapters.

        Older in-memory fakes deliberately implement only the original
        repository contract.  Production TypeDB receives an explicit world
        boundary; a fake without that optional keyword remains usable for
        projection unit tests.
        """
        method = getattr(self.repository, method_name, None)
        if not callable(method):
            raise AttributeError(method_name + " is unavailable")
        if not world_id:
            return method(*args, **kwargs)
        try:
            return method(*args, world_id=world_id, **kwargs)
        except TypeError as error:
            message = str(error)
            if "world_id" not in message and "unexpected keyword" not in message:
                raise
            return method(*args, **kwargs)

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]:
        if not hasattr(self.repository, "active_abox_metadata"):
            return {}
        try:
            result = self.repository_world_call("active_abox_metadata", world_id=world_id)
        except Exception:  # noqa: BLE001 - absence of comparison metadata falls back to persistence.
            return {}
        return dict(result or {}) if isinstance(result, dict) else {}

    def recover_pending_abox_activation(
        self,
        world_id: str = "",
        max_staged_target_symbols: int = 0,
    ) -> Dict[str, object]:
        if self.active_graph_store_key() != "typedb":
            return {"status": "skipped", "reason": "Active graph store is not TypeDB."}
        recovery = getattr(self.repository, "recover_pending_abox_activation", None)
        if not callable(recovery):
            return {"status": "skipped", "reason": "Graph store has no pending ABox activation journal."}
        # Recovery mutates the activation journal and therefore takes the
        # database-wide TypeDB writer lease. Most realtime cycles have no
        # pending candidate at all; perform the cheap read first so an empty
        # journal does not spend the entire alert budget contending for a
        # write lease.
        pending_reader = getattr(self.repository, "pending_abox_activation", None)
        if callable(pending_reader):
            try:
                pending = self.repository_world_call(
                    "pending_abox_activation",
                    world_id=world_id,
                )
            except Exception:
                pending = None
            if isinstance(pending, dict) and str(pending.get("status") or "").strip() == "empty":
                return {
                    "status": "skipped",
                    "reason": "No pending ABox activation exists.",
                    "recoveryPreflight": "empty-journal",
                }
        try:
            staged_cap = max(0, min(200, int(float(max_staged_target_symbols or 0))))
        except (TypeError, ValueError):
            staged_cap = 0
        try:
            result = self.repository_world_call(
                "recover_pending_abox_activation",
                world_id=world_id,
                max_staged_target_symbols=staged_cap,
            )
        except TypeError as error:
            # Lightweight compatibility repositories may not yet accept the
            # operational cap. Their legacy recovery behavior remains safe;
            # production TypeDB performs the comparison under its write lock.
            if "max_staged_target_symbols" not in str(error) and "unexpected keyword" not in str(error):
                return {"status": "error", "reason": str(error)[:180]}
            try:
                result = self.repository_world_call("recover_pending_abox_activation", world_id=world_id)
            except Exception as fallback_error:  # noqa: BLE001 - do not replace a potentially recoverable active generation.
                return {"status": "error", "reason": str(fallback_error)[:180]}
        except Exception as error:  # noqa: BLE001 - do not replace a potentially recoverable active generation.
            return {"status": "error", "reason": str(error)[:180]}
        return dict(result or {}) if isinstance(result, dict) else {
            "status": "error",
            "reason": "Graph store returned an invalid ABox activation recovery result.",
        }

    def resume_staged_pending_abox_activation(
        self,
        snapshot: AccountSnapshot,
        world_id: str,
        recovery: Dict[str, object],
    ) -> Dict[str, object]:
        """Complete a pending candidate before allowing a newer Manifest.

        A process can stop either after staging an immutable ABox candidate or
        after activating the initial candidate but before native inference
        completes. Rebuilding the latest snapshot cannot replace that
        candidate safely, so resume its exact target set first.
        """
        recovery = dict(recovery or {})
        pending = recovery.get("pendingActivation")
        pending = dict(pending or {}) if isinstance(pending, dict) else {}
        candidate_id = str(
            pending.get("candidateAboxSnapshotId")
            or recovery.get("candidateAboxSnapshotId")
            or ""
        ).strip()
        previous_id = str(
            pending.get("previousAboxSnapshotId")
            or recovery.get("previousAboxSnapshotId")
            or ""
        ).strip()
        target_symbols = [
            str(symbol or "").upper().strip()
            for symbol in (pending.get("targetSymbols") or recovery.get("targetSymbols") or [])
            if str(symbol or "").strip()
        ]
        target_symbols = list(dict.fromkeys(target_symbols))
        if not candidate_id or not target_symbols:
            return {
                "saved": False,
                "status": "blocked-pending-abox-activation",
                "graphStore": self.active_graph_store_key(),
                "worldId": str(world_id or ""),
                "pendingAboxActivation": pending,
                "preservedActiveGeneration": True,
                "retryable": True,
                "recommendedRetryAfterSeconds": 10,
                "reason": "스테이징된 TypeDB ABox 후보의 대상 종목을 확인하지 못해 안전하게 재개하지 않았습니다.",
            }

        result = {
            "saved": False,
            "status": "resuming-pending-abox-activation",
            "graphStore": self.active_graph_store_key(),
            "worldId": str(world_id or ""),
            "aboxSnapshotId": candidate_id,
            "worldviewManifestId": candidate_id,
            "pendingAboxActivation": {
                **pending,
                "candidateAboxSnapshotId": candidate_id,
                "previousAboxSnapshotId": previous_id,
                "targetSymbols": target_symbols,
            },
            "preservedActiveGeneration": True,
        }
        self.attach_graph_store_inference_result(
            result,
            snapshot,
            target_symbols=target_symbols,
            world_id=world_id,
        )
        finalization = result.get("aboxActivationFinalization")
        finalization = dict(finalization or {}) if isinstance(finalization, dict) else {}
        if str(finalization.get("status") or "") == "ok":
            result.update({
                "saved": True,
                "status": "ok",
                "resumedPendingAboxActivation": True,
                "reason": "중단된 TypeDB ABox 후보의 네이티브 추론과 완료 처리를 재개했습니다.",
            })
        elif str(result.get("status") or "") == "resuming-pending-abox-activation":
            result.update({
                "saved": False,
                "status": "blocked-pending-abox-activation",
                "retryable": True,
                "recommendedRetryAfterSeconds": 10,
                "reason": str(
                    finalization.get("reason")
                    or "스테이징된 TypeDB ABox 후보의 네이티브 추론 완료를 다시 확인해야 합니다."
                )[:220],
            })
        return result

    def reconcile_interrupted_projection_audit(self, world_id: str = "") -> Dict[str, object]:
        """Finish one audit row only when TypeDB already proves activation.

        The source row is written before an ABox pointer moves.  A process can
        stop after TypeDB has activated an aligned InferenceBox but before the
        final MySQL audit update.  This recovery is deliberately proof-based:
        it never promotes a row from a timer or a partial graph write.
        """
        if self.active_graph_store_key() != "typedb":
            return {"status": "skipped", "reason": "Active graph store is not TypeDB."}
        if not self.projection_run_store or not hasattr(self.projection_run_store, "latest"):
            return {"status": "skipped", "reason": "Projection audit store does not support recovery lookup."}
        if not hasattr(self.projection_run_store, "complete") or not hasattr(self.repository, "inferencebox_snapshot"):
            return {"status": "skipped", "reason": "Projection audit recovery dependencies are unavailable."}
        active_abox = self.active_abox_metadata(world_id)
        if str(active_abox.get("status") or "") != "ok":
            return {"status": "skipped", "reason": "No complete active ABox is available for audit recovery."}
        run_id = str(active_abox.get("projectionRunId") or "").strip()
        active_snapshot_id = str(active_abox.get("aboxSnapshotId") or "").strip()
        if not run_id or not active_snapshot_id:
            return {"status": "skipped", "reason": "Active ABox has no recoverable projection audit identity."}
        try:
            try:
                rows = self.projection_run_store.latest(limit=80, world_id=world_id)
            except TypeError as error:
                if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                    raise
                rows = self.projection_run_store.latest(limit=80)
        except Exception as error:  # noqa: BLE001 - audit recovery must not block a new graph cycle.
            return {"status": "error", "reason": str(error)[:180]}
        row = next((
            item for item in rows or []
            if isinstance(item, dict)
            and str(item.get("runId") or "") == run_id
            and str(item.get("status") or "").lower() == "projecting"
            and (
                not str(world_id or "").strip()
                or str(item.get("worldId") or "").strip() == str(world_id or "").strip()
            )
        ), None)
        recovery_mode = "interrupted-audit"
        if not row:
            # Older workers could prove a TypeDB activation after their
            # process was interrupted, but persisted only a thin recovery
            # audit. That loses the reusable native-rule coverage proof and
            # makes every later target run fall back to the full catalogue.
            # Repair only the active run, and only from the same aligned
            # TypeDB InferenceBox proof used for an interrupted audit.
            row = next((
                item for item in rows or []
                if isinstance(item, dict)
                and str(item.get("runId") or "") == run_id
                and str(item.get("status") or "").lower() == "ok"
                and (
                    not str(world_id or "").strip()
                    or str(item.get("worldId") or "").strip() == str(world_id or "").strip()
                )
                and str(
                    ((item.get("result") or {}).get("inferenceReuseProof") or {}).get("status")
                    if isinstance(item.get("result"), dict)
                    else ""
                ) != "verified"
                and not bool(
                    ((item.get("result") or {}).get("nativeReplayValidation") or {}).get("verified")
                    if isinstance(item.get("result"), dict)
                    else False
                )
            ), None)
            recovery_mode = "reuse-proof-repair"
        if not row:
            return {"status": "skipped", "reason": "No interrupted audit or missing active reuse proof matches the active ABox."}
        run = projection_run_from_payload(row)
        if not run.run_id or run.abox_snapshot_id != active_snapshot_id:
            return {"status": "skipped", "reason": "Active ABox identity does not match the recoverable projection audit."}
        try:
            inferencebox = self.repository_world_call(
                "inferencebox_snapshot",
                symbols=list(run.source_symbols or []),
                limit=self.inference_snapshot_limit(),
                world_id=world_id,
            )
        except Exception as error:  # noqa: BLE001 - preserve the durable projecting row for the next retry.
            return {"status": "error", "reason": str(error)[:180]}
        if not self.inference_result_is_reusable(inferencebox, active_abox, list(run.source_symbols or [])):
            return {
                "status": "skipped",
                "reason": "Active InferenceBox is not aligned with the interrupted ABox audit row.",
                "runId": run.run_id,
            }
        prior_result = row.get("result") if isinstance(row.get("result"), dict) else {}
        prior_execution = prior_result.get("ruleboxExecution") if isinstance(prior_result.get("ruleboxExecution"), dict) else {}
        prior_reuse = prior_result.get("priorInferenceReuse") if isinstance(prior_result.get("priorInferenceReuse"), dict) else {}
        matched_rule_ids = self.matched_rule_ids_from_inference_payload(inferencebox)
        selection_applied = bool(
            inferencebox.get("nativeRuleSelectionApplied")
            if "nativeRuleSelectionApplied" in inferencebox
            else prior_execution.get("nativeRuleSelectionApplied")
        )
        recovered_timing = (
            dict(inferencebox.get("typedbNativeRuleTimingProfile") or {})
            if isinstance(inferencebox.get("typedbNativeRuleTimingProfile"), dict)
            else {}
        )
        recovered_stage_timings = (
            dict(inferencebox.get("typedbNativeStageTimings") or {})
            if isinstance(inferencebox.get("typedbNativeStageTimings"), dict)
            else {}
        )
        result = {
            "saved": True,
            "status": "ok",
            "reason": (
                "TypeDB ABox와 InferenceBox 정합성을 확인해 중단된 투영 감사를 복구했습니다."
                if recovery_mode == "interrupted-audit"
                else "활성 TypeDB ABox와 InferenceBox에서 누락된 규칙 재사용 증명을 복구했습니다."
            ),
            "graphStore": "typedb",
            "projectionMode": run.projection_mode,
            "aboxSnapshotId": active_snapshot_id,
            "materialFingerprint": str(active_abox.get("materialFingerprint") or run.material_fingerprint),
            "entityCount": run.entity_count,
            "relationCount": run.relation_count,
            "inferenceBox": inferencebox,
            "ruleboxExecution": {
                "status": "ok",
                "reason": "Recovered from active TypeDB InferenceBox alignment.",
                "nativeInferenceEvaluationComplete": bool(
                    inferencebox.get("nativeTypeDbReasoningCompleted")
                    or inferencebox.get("typedbNativeRuleEvaluationCompleted")
                    or inferencebox.get("nativeTypeDbReasoningUsed")
                ),
                "nativeRuleSelectionApplied": selection_applied,
                "nativeRuleSelectionCandidateCount": int(
                    inferencebox.get("nativeRuleSelectionCandidateCount") or 0
                ),
                "nativeRuleSelectionExecutedCount": int(
                    inferencebox.get("nativeRuleSelectionExecutedCount") or 0
                ),
                "nativeRuleSelectionDeferredCount": int(
                    inferencebox.get("nativeRuleSelectionDeferredCount") or 0
                ),
                "nativeRuleSelectionFullRuleCount": int(
                    inferencebox.get("nativeRuleSelectionFullRuleCount") or 0
                ),
                "nativeRuleSelectionExecutedRuleIds": list(
                    inferencebox.get("nativeRuleSelectionExecutedRuleIds") or []
                )[:80],
                "nativeRuleSelectionDeferredRuleIds": list(
                    inferencebox.get("nativeRuleSelectionDeferredRuleIds") or []
                )[:80],
                "typedbNativeRuleExecutedCount": int(
                    inferencebox.get("typedbNativeRuleExecutedCount") or 0
                ),
                "typedbNativeRuleMatchedRuleIds": list(
                    inferencebox.get("typedbNativeRuleMatchedRuleIds")
                    or matched_rule_ids
                )[:160],
                "typedbNativeRuleMatchedCount": max(
                    int(inferencebox.get("typedbNativeRuleMatchedCount") or 0),
                    len(matched_rule_ids),
                    int(inferencebox.get("traceCount") or 0),
                ),
                "typedbNativeRuleTimingProfile": recovered_timing,
                "typedbNativeStageTimings": recovered_stage_timings,
            },
            "aboxPersistenceVerification": {
                "activePointer": {
                    "status": str(active_abox.get("status") or ""),
                    "aboxSnapshotId": active_snapshot_id,
                    "projectionRunId": run.run_id,
                },
                "activation": {
                    "status": "recovered-after-runtime-interruption",
                    "snapshotId": active_snapshot_id,
                    "atomic": True,
                },
            },
            "recoveredAfterRuntimeInterruption": True,
            "recoveryMode": recovery_mode,
        }
        if prior_reuse:
            result["priorInferenceReuse"] = prior_reuse
        # A recovery must preserve the same verified coverage contract as an
        # uninterrupted projection. Without this, the next narrow change
        # cannot reuse unaffected TypeDB matches and re-runs the full catalog.
        self.attach_inference_reuse_proof(run, result)
        replay_validation = dict(result.get("nativeReplayValidation") or {})
        if not bool(replay_validation.get("verified")):
            result.update({
                "saved": False,
                "status": "incomplete-native-coverage",
                "reason": str(
                    replay_validation.get("reason")
                    or "Recovered TypeDB generation has no complete native execution ledger."
                )[:300],
                "preservedActiveGeneration": True,
            })
        try:
            completed = complete_ontology_projection_run(run, result)
            complete_with_trace = getattr(
                self.projection_run_store,
                "complete_with_execution_trace",
                None,
            )
            if callable(complete_with_trace):
                complete_with_trace(completed, result)
            else:
                self.projection_run_store.complete(completed)
        except Exception as error:  # noqa: BLE001 - a later cycle can prove and retry the same row.
            return {"status": "error", "reason": str(error)[:180], "runId": run.run_id}
        if not bool(replay_validation.get("verified")):
            return {
                "status": "incomplete-native-coverage",
                "runId": run.run_id,
                "aboxSnapshotId": active_snapshot_id,
                "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
                "nativeReplayValidation": replay_validation,
                "reason": str(result.get("reason") or "")[:300],
            }
        return {
            "status": "recovered" if recovery_mode == "interrupted-audit" else "reuse-proof-repaired",
            "runId": run.run_id,
            "aboxSnapshotId": active_snapshot_id,
            "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
            "inferenceReuseProof": dict(result.get("inferenceReuseProof") or {}),
        }

    def ensure_rulebox_ready(self) -> Dict[str, object]:
        self._rulebox_impact_rules = None
        if not hasattr(self.repository, "rulebox_snapshot"):
            return {}
        try:
            snapshot = self.repository.rulebox_snapshot()
        except Exception as error:  # noqa: BLE001 - projection will still expose the persistence error later.
            return {"status": "error", "reason": str(error)[:180]}
        if not isinstance(snapshot, dict):
            return {"status": "invalid", "reason": "RuleBox snapshot returned non-dict result."}
        stored_rules = [
            dict(item) for item in snapshot.get("rules") or []
            if isinstance(item, dict)
        ]
        self._rulebox_impact_rules = stored_rules
        if not snapshot.get("configured"):
            return {
                "status": "disabled",
                "reason": str(snapshot.get("reason") or "Ontology graph storage is not configured."),
            }
        bootstrap = None
        requires_bootstrap_repair = rulebox_catalog_requires_bootstrap_repair(stored_rules)
        if requires_bootstrap_repair:
            bootstrap = bootstrap_rule_catalog()
            migration = self.migrate_typedb_rule_catalog(
                snapshot,
                list(bootstrap.get("rules") or []),
            )
        else:
            # The persisted TypeDB catalog is already structurally compatible
            # with the active raw ABox. Do not spend a realtime cycle
            # rebuilding code defaults merely to compare presentation fields.
            migration = {
                "status": "stored-catalog-ready",
                "required": False,
                "saved": False,
                "bootstrapChecked": False,
            }
        if migration.get("required") and not migration.get("saved"):
            return {
                "status": "not-ready",
                "reason": str(migration.get("reason") or "TypeDB 추론 규칙 마이그레이션에 실패했습니다."),
                "ruleCount": int(snapshot.get("ruleboxRuleCount") or snapshot.get("ruleCount") or 0),
                "ruleCatalogMigration": migration,
            }
        if migration.get("saved"):
            try:
                snapshot = self.repository.rulebox_snapshot()
            except Exception:  # noqa: BLE001 - successful save metadata still proves the migration ran.
                snapshot = dict(migration.get("result") or snapshot)
            self._rulebox_impact_rules = [
                dict(item) for item in snapshot.get("rules") or []
                if isinstance(item, dict)
            ]
            stored_rules = list(self._rulebox_impact_rules)
        stored_count = int(snapshot.get("ruleboxRuleCount") or snapshot.get("ruleCount") or 0)
        stored_hash = str(snapshot.get("ruleboxRulesHash") or snapshot.get("rulesHash") or "").strip()
        if not stored_hash and stored_rules:
            stored_hash = compute_rulebox_rules_hash(stored_rules)
        missing_decision_policy = rulebox_rules_missing_decision_stage(stored_rules)
        if missing_decision_policy:
            return {
                "status": "not-ready",
                "reason": "TypeDB 추론 규칙의 파생 관계에 decisionStage가 없습니다.",
                "ruleCount": stored_count,
                "missingDecisionStageRuleIds": missing_decision_policy,
                "ruleCatalogMigration": migration,
            }
        if stored_count > 0 and str(snapshot.get("status") or "") == "ok":
            result = {
                "status": "ready",
                "ruleCount": stored_count,
                "ruleboxRulesHash": stored_hash,
                "sourceOfTruth": "typedb-direct-typeql-rules",
                "ruleCatalogStore": "typedb",
                "inputRelationTypes": rulebox_input_relation_types(stored_rules),
                "bootstrapRuleCount": int(
                    (bootstrap or {}).get("ruleCount")
                    or snapshot.get("bootstrapRuleCount")
                    or 0
                ),
                "bootstrapRulesHash": str((bootstrap or {}).get("ruleboxRulesHash") or ""),
                "bootstrapCatalogChecked": bool(bootstrap),
                "codeDefaultHashMismatch": bool(
                    bootstrap
                    and stored_hash
                    and stored_hash != str(bootstrap.get("ruleboxRulesHash") or "")
                ),
                "ruleCatalogMigration": migration,
            }
            if result["codeDefaultHashMismatch"]:
                result["reason"] = (
                    "TypeDB 추론 규칙이 운영 기준입니다. 코드 기본값은 빈 저장소를 시작할 때만 사용하며 "
                    "저장된 규칙을 덮어쓰지 않습니다."
                )
            return result
        bootstrap = bootstrap or bootstrap_rule_catalog()
        expected_rules = list(bootstrap.get("rules") or [])
        expected_hash = str(bootstrap.get("ruleboxRulesHash") or "")
        expected_count = int(bootstrap.get("ruleCount") or len(expected_rules))
        if str(snapshot.get("status") or "") != "empty":
            return {
                "status": "not-ready",
                "reason": str(snapshot.get("reason") or snapshot.get("status") or "RuleBox is not ready."),
                "ruleCount": stored_count,
                "bootstrapRuleCount": expected_count,
                "ruleboxRulesHash": stored_hash,
                "bootstrapRulesHash": expected_hash,
            }
        if not hasattr(self.repository, "seed_ontology"):
            return {
                "status": "not-ready",
                "reason": "RuleBox is empty and repository does not support bootstrap seeding.",
                "ruleCount": stored_count,
                "bootstrapRuleCount": expected_count,
                "bootstrapRulesHash": expected_hash,
            }
        try:
            seeded = self.repository.seed_ontology({
                "replaceRuleBox": False,
                "clearInference": False,
                "changeReason": "자동 RuleBox 부트스트랩: ABox 투영 전에 그래프 추론 규칙을 준비합니다.",
            })
        except Exception as error:  # noqa: BLE001 - projection will report readiness failure instead of crashing.
            return {"status": "error", "reason": str(error)[:180]}
        self._rulebox_impact_rules = [dict(item) for item in expected_rules]
        return {
            "status": "seeded" if bool((seeded or {}).get("seeded")) else str((seeded or {}).get("status") or "not-seeded"),
            "ruleCount": int((seeded or {}).get("ruleCount") or 0),
            "sourceOfTruth": "typedb-direct-typeql-rules",
            "ruleCatalogStore": "typedb",
            "inputRelationTypes": rulebox_input_relation_types(expected_rules),
            "bootstrapRuleCount": expected_count,
            "bootstrapRulesHash": expected_hash,
            "reason": str((seeded or {}).get("reason") or ""),
        }

    def migrate_typedb_rule_catalog(
        self,
        snapshot: Dict[str, object],
        bootstrap_rules: List[Dict[str, object]],
    ) -> Dict[str, object]:
        stored_rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
        if not stored_rules:
            return {"status": "not-inspectable", "required": False, "saved": False}
        migration = migrate_typedb_rule_catalog(stored_rules, bootstrap_rules)
        if not migration.get("changed"):
            return {"status": "ready", "required": False, "saved": False}
        if not hasattr(self.repository, "save_rulebox"):
            return {**migration, "status": "unsupported", "required": True, "saved": False}
        try:
            result = self.repository.save_rulebox({
                "rules": migration.get("rules") or [],
                "changeReason": "TypeDB 단일 추론 경로 전환: 원시 ABox 조건·RuleBox 실행 지침으로 마이그레이션",
            })
        except Exception as error:  # noqa: BLE001 - failed migration must stop new inference generations.
            return {**migration, "status": "error", "required": True, "saved": False, "reason": str(error)[:180]}
        saved = bool((result or {}).get("saved")) and str((result or {}).get("status") or "") == "ok"
        return {
            **migration,
            "status": "migrated" if saved else str((result or {}).get("status") or "not-saved"),
            "required": True,
            "saved": saved,
            "reason": str((result or {}).get("reason") or "")[:180],
            "result": {
                "status": str((result or {}).get("status") or ""),
                "ruleCount": int((result or {}).get("ruleCount") or (result or {}).get("ruleboxRuleCount") or 0),
                "reason": str((result or {}).get("reason") or "")[:180],
            },
        }

    def graph_for_graph_store_persistence(
        self,
        graph: PortfolioOntology,
        rule_catalog: Dict[str, object] = None,
    ) -> PortfolioOntology:
        # TypeDB owns condition evaluation. Projection only retains relation
        # types referenced by the active TypeDB catalog and never evaluates
        # target values, thresholds, or polarity in Python.
        stripped_ids: Set[str] = set()
        abox_entities = []
        for item in graph.entities:
            box = str((item.properties or {}).get("ontologyBox") or "ABox")
            if box != "ABox":
                stripped_ids.add(item.entity_id)
                continue
            abox_entities.append(item)
        abox_relations = [
            item
            for item in graph.relations
            if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox"
            and item.source not in stripped_ids
            and item.target not in stripped_ids
        ]
        native_relation_types = {
            str(item or "").upper().strip()
            for item in (rule_catalog or {}).get("inputRelationTypes") or []
            if str(item or "").strip()
        }
        active_rules = [
            item
            for item in (rule_catalog or {}).get("rules") or []
            if isinstance(item, dict)
        ]
        subject_patterns = rulebox_relation_subject_patterns(active_rules)
        if not subject_patterns:
            # The bootstrap summary may omit full rules. Keep the historic
            # stock/portfolio surface in that narrow compatibility case.
            subject_patterns = {
                (source_kind, relation_type, direction)
                for source_kind in {"stock", "portfolio"}
                for relation_type in native_relation_types
                for direction in {"out", "in"}
            }
        source_kinds = {pattern[0] for pattern in subject_patterns}
        entity_by_id = {item.entity_id: item for item in abox_entities}
        # The active ABox is both TypeDB's native-rule input and the factual
        # investment world shown to diagnostics and AI. Keep the category
        # edges that define that world even when no currently enabled rule
        # consumes one of them. Otherwise a valid Price/Liquidity concept can
        # exist as an orphaned node, producing a misleading coverage gap.
        semantic_relation_types = ABOX_STRUCTURAL_RELATION_TYPES | {
            str(relation_type or "").upper().strip()
            for category_types in CATEGORY_RELATIONS.values()
            for relation_type in category_types
            if str(relation_type or "").strip()
        }
        persisted_relation_types = native_relation_types | semantic_relation_types
        source_ids = {
            item.entity_id
            for item in abox_entities
            if str(item.kind or "") in source_kinds
        }
        def matches_native_subject(relation) -> bool:
            relation_type = str(relation.relation_type or "").upper().strip()
            for source_kind, expected_type, direction in subject_patterns:
                if relation_type != expected_type:
                    continue
                subject_id = relation.target if direction == "in" else relation.source
                subject = entity_by_id.get(subject_id)
                if subject and str(subject.kind or "") == source_kind:
                    return True
            return False

        def should_persist_relation(relation) -> bool:
            if not persisted_relation_types:
                return relation.source in source_ids or relation.target in source_ids
            if matches_native_subject(relation):
                return True
            relation_type = str(relation.relation_type or "").upper().strip()
            return (
                relation_type in (semantic_relation_types - native_relation_types)
                and (relation.source in source_ids or relation.target in source_ids)
            )

        relations = [
            item
            for item in abox_relations
            if should_persist_relation(item)
        ]
        # Temporal observations are intentionally structural rather than
        # direct RuleBox predicates. Once a native subject reaches a window,
        # retain the small connected observation chain so time-series
        # reasoning and diagnostics see the same episode.
        persisted_endpoint_ids = {
            endpoint
            for relation in relations
            for endpoint in (relation.source, relation.target)
            if str(endpoint or "").strip()
        } | set(source_ids)
        structural_relations = [
            item
            for item in abox_relations
            if str(item.relation_type or "").upper().strip() in ABOX_STRUCTURAL_RELATION_TYPES
        ]
        def equality_key(value):
            if isinstance(value, dict):
                return (
                    "dict",
                    tuple(sorted(
                        ((key, equality_key(item)) for key, item in value.items()),
                        key=lambda row: repr(row[0]),
                    )),
                )
            if isinstance(value, list):
                return ("list", tuple(equality_key(item) for item in value))
            if isinstance(value, tuple):
                return ("tuple", tuple(equality_key(item) for item in value))
            if isinstance(value, set):
                return ("set", frozenset(equality_key(item) for item in value))
            try:
                hash(value)
            except TypeError:
                return ("object", type(value).__qualname__, repr(value))
            return ("value", value)

        def relation_equality_key(relation):
            # OntologyRelation is a dataclass. Preserve its equality contract
            # while avoiding an O(structural-relations * retained-relations)
            # list scan during full-world recovery and contract migrations.
            return (
                relation.source,
                relation.target,
                relation.relation_type,
                relation.weight,
                equality_key(relation.evidence_ids),
                equality_key(relation.properties),
            )

        retained_relation_keys = {
            relation_equality_key(item)
            for item in relations
        }
        while True:
            additions = [
                item
                for item in structural_relations
                if relation_equality_key(item) not in retained_relation_keys
                and (item.source in persisted_endpoint_ids or item.target in persisted_endpoint_ids)
            ]
            if not additions:
                break
            relations.extend(additions)
            retained_relation_keys.update(
                relation_equality_key(item)
                for item in additions
            )
            persisted_endpoint_ids.update(
                endpoint
                for relation in additions
                for endpoint in (relation.source, relation.target)
                if str(endpoint or "").strip()
            )
        persisted_entity_ids = source_ids | {
            endpoint
            for relation in relations
            for endpoint in [relation.source, relation.target]
            if str(endpoint or "").strip()
        }
        entities = [item for item in abox_entities if item.entity_id in persisted_entity_ids]
        evidence = [
            item
            for item in graph.evidence
            if str((item.value or {}).get("ontologyBox") or "ABox") == "ABox"
            and str(item.subject or "") in source_ids
        ]
        # Beliefs are reasoning output, not live observations. Persisting them
        # in the ABox duplicates the InferenceBox and forces unrelated scope
        # generations to be rewritten. Native rules consume the factual
        # entities, relations, and evidence above; derived beliefs remain in
        # their immutable InferenceBox generation.
        beliefs = []
        return PortfolioOntology(
            graph.portfolio_id,
            entities=entities,
            relations=relations,
            evidence=evidence,
            beliefs=beliefs,
            opinions=[],
            reasoning_cards=[],
            worldview={
                **dict(graph.worldview or {}),
                "runtimeProjectionMode": "abox-facts-only-typedb-native-rules",
                "runtimeProjectionScope": "typedb-rule-input-and-semantic-coverage-relations",
                "runtimeProjectionSourceEntityCount": len(source_ids),
                "runtimeProjectionRelationTypeCount": len(persisted_relation_types),
                "runtimeProjectionRelationSubjectPatternCount": len(subject_patterns),
                "runtimeProjectionRuleInputRelationTypeCount": len(native_relation_types),
                "runtimeProjectionSemanticRelationTypeCount": len(semantic_relation_types),
            },
            prompt=graph.prompt,
        )

    def graph_assembly_cache_enabled(self) -> bool:
        value = self.settings.get("ontologyProjectionGraphCacheEnabled")
        if value is None:
            # Direct recorder construction in focused unit tests remains
            # deterministic. The managed runtime explicitly enables the
            # cache through runtime_settings().
            return False
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def runtime_context_cache_enabled(self) -> bool:
        value = self.settings.get("ontologyProjectionRuntimeContextCacheEnabled")
        if value is None:
            return False
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def runtime_context_cache_ttl_seconds(self) -> float:
        try:
            value = float(str(self.settings.get("ontologyProjectionRuntimeContextCacheTtlSeconds") or "120"))
        except (TypeError, ValueError):
            value = 120.0
        return max(1.0, min(600.0, value))

    def runtime_context_cache_max_entries(self) -> int:
        try:
            value = int(float(str(self.settings.get("ontologyProjectionRuntimeContextCacheMaxEntries") or "64")))
        except (TypeError, ValueError):
            value = 64
        return max(1, min(256, value))

    def runtime_context_cache_key(
        self,
        snapshot: AccountSnapshot,
        active_tbox: Dict[str, object],
        target_symbols: Iterable[object] = None,
    ) -> str:
        source_snapshot = projection_source_snapshot(snapshot)
        metadata = dict(source_snapshot.get("metadata") or {})
        investment_brain = dict(metadata.get("investmentBrain") or {})
        investment_brain.pop("outcomeObservation", None)
        if investment_brain:
            metadata["investmentBrain"] = investment_brain
        else:
            metadata.pop("investmentBrain", None)
        source_snapshot["metadata"] = metadata
        payload = {
            "version": PROJECTION_RUNTIME_CONTEXT_CACHE_CONTRACT_VERSION,
            "namespace": self.graph_assembly_cache_namespace(),
            "sourceSnapshot": stable_value(source_snapshot),
            "settings": stable_value(self.settings),
            "activeTBox": stable_value(active_tbox),
            "targetSymbols": sorted({
                str(symbol or "").upper().strip()
                for symbol in target_symbols or []
                if str(symbol or "").strip()
            }),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def graph_assembly_cache_ttl_seconds(self) -> float:
        try:
            value = float(str(self.settings.get("ontologyProjectionGraphCacheTtlSeconds") or "45"))
        except (TypeError, ValueError):
            value = 45.0
        return max(1.0, min(300.0, value))

    def graph_assembly_cache_max_entries(self) -> int:
        try:
            value = int(float(str(self.settings.get("ontologyProjectionGraphCacheMaxEntries") or "16")))
        except (TypeError, ValueError):
            value = 16
        return max(1, min(128, value))

    def graph_assembly_persistent_cache_enabled(self) -> bool:
        """Enable only when the managed runtime supplies a local MySQL cache.

        Focused recorder tests intentionally construct no durable store. This
        keeps their graph assertions deterministic while production isolated
        workers can reuse an exact source assembly across process boundaries.
        """
        value = self.settings.get("ontologyProjectionGraphPersistentCacheEnabled")
        if value is None or not self.graph_assembly_cache_store:
            return False
        return str(value).strip().lower() not in {"", "0", "false", "no", "off", "disabled"}

    def graph_assembly_persistent_cache_ttl_seconds(self) -> float:
        try:
            value = float(str(self.settings.get("ontologyProjectionGraphPersistentCacheTtlSeconds") or "120"))
        except (TypeError, ValueError):
            value = 120.0
        return max(1.0, min(300.0, value))

    def graph_assembly_persistent_cache_max_entries(self) -> int:
        try:
            value = int(float(str(self.settings.get("ontologyProjectionGraphPersistentCacheMaxEntries") or "64")))
        except (TypeError, ValueError):
            value = 64
        return max(1, min(256, value))

    def graph_assembly_persistent_cache_max_payload_bytes(self) -> int:
        try:
            value = int(float(str(self.settings.get("ontologyProjectionGraphPersistentCacheMaxPayloadBytes") or 8 * 1024 * 1024)))
        except (TypeError, ValueError):
            value = 8 * 1024 * 1024
        return max(64 * 1024, min(32 * 1024 * 1024, value))

    def persistent_graph_assembly_cache_get(self, cache_key: str) -> Dict[str, object]:
        if not self.graph_assembly_persistent_cache_enabled():
            return {"status": "disabled"}
        getter = getattr(self.graph_assembly_cache_store, "get", None)
        if not callable(getter):
            return {"status": "unsupported"}
        try:
            result = getter(cache_key, self.graph_assembly_persistent_cache_ttl_seconds())
        except Exception as error:  # noqa: BLE001 - exact-cache loss must not block TypeDB reasoning.
            return {"status": "miss", "reason": str(error)[:180]}
        values = dict(result or {}) if isinstance(result, dict) else {}
        if (
            str(values.get("status") or "") == "hit"
            and isinstance(values.get("graph"), PortfolioOntology)
            and isinstance(values.get("persistenceGraph"), PortfolioOntology)
        ):
            return values
        return {"status": "miss", **({"reason": str(values.get("reason") or "")[:180]} if values.get("reason") else {})}

    def persistent_graph_assembly_cache_put(
        self,
        cache_key: str,
        graph: PortfolioOntology,
        persistence_graph: PortfolioOntology,
        runtime_context_packet: Dict[str, object] = None,
    ) -> Dict[str, object]:
        if not self.graph_assembly_persistent_cache_enabled():
            return {"status": "disabled"}
        saver = getattr(self.graph_assembly_cache_store, "put", None)
        if not callable(saver):
            return {"status": "unsupported"}
        try:
            result = saver(
                cache_key,
                graph,
                persistence_graph,
                self.graph_assembly_persistent_cache_ttl_seconds(),
                self.graph_assembly_persistent_cache_max_entries(),
                self.graph_assembly_persistent_cache_max_payload_bytes(),
                runtime_context_packet,
            )
        except Exception as error:  # noqa: BLE001 - durable cache writes are best effort.
            return {"status": "error", "reason": str(error)[:180]}
        return dict(result or {}) if isinstance(result, dict) else {"status": "invalid"}

    def graph_assembly_cache_namespace(self) -> str:
        """Keep test doubles isolated while sharing a real TypeDB runtime."""
        store_key = str(getattr(self.repository, "store_key", "") or "repository")
        address = str(getattr(self.repository, "address", "") or "").strip()
        database = str(getattr(self.repository, "database", "") or "").strip()
        if address or database:
            return "|".join([store_key, address, database])
        return store_key + "|instance:" + str(id(self.repository))

    def active_tbox_context(self) -> Dict[str, object]:
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
        """Hash only source inputs; no graph result or credentials are persisted."""
        source_snapshot = projection_source_snapshot(snapshot)
        metadata = dict(source_snapshot.get("metadata") or {})
        investment_brain = dict(metadata.get("investmentBrain") or {})
        # Outcome observation is attached by this projection's runtime-context
        # reader. It is derived state, not a new source observation, and must
        # not turn an otherwise identical retry into a cache miss.
        investment_brain.pop("outcomeObservation", None)
        if investment_brain:
            metadata["investmentBrain"] = investment_brain
        else:
            metadata.pop("investmentBrain", None)
        source_snapshot["metadata"] = metadata
        frozen_runtime_context = frozen_projection_runtime_context(runtime_context)
        payload = {
            # Bump this contract whenever graph-builder behavior changes. The
            # durable cache can outlive a worker restart, so source equality
            # alone is not enough to prove a cached graph is reusable.
            "version": PORTFOLIO_GRAPH_ASSEMBLY_CACHE_CONTRACT_VERSION,
            "namespace": self.graph_assembly_cache_namespace(),
            # Cache reuse is stricter than material-generation reuse.  The
            # observation clock and provider timestamps can change freshness,
            # session and data-quality facts even when price/volume values are
            # unchanged.  Removing those fields here previously returned a
            # stale flow/quality graph while the replay packet contained the
            # current context.
            "sourceSnapshot": source_snapshot,
            "settings": stable_value(self.settings),
            "activeTBox": stable_value(active_tbox),
            "runtimeContextHash": hashlib.sha256(
                json.dumps(
                    frozen_runtime_context,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            "ruleboxRulesHash": str((rule_catalog or {}).get("ruleboxRulesHash") or ""),
            "targetSymbols": sorted({str(symbol or "").upper().strip() for symbol in target_symbols or [] if str(symbol or "").strip()}),
            "inputMode": str(input_mode or "full"),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def build_graph_assembly(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        target_symbols: List[str] = None,
        target_scoped_input: bool = False,
        progress_callback: Callable[..., None] = None,
    ) -> tuple:
        """Build or safely clone the immutable pre-identity ABox graph pair."""
        stage_timings: Dict[str, int] = {}

        def emit(stage: str, **details) -> None:
            if not callable(progress_callback):
                return
            try:
                progress_callback("graph_assembly." + str(stage or "unknown"), **details)
            except Exception:
                return

        emit("observation_input.start")
        observation_input = snapshot.projection_observation_input(
            target_symbols if target_scoped_input else None
        )
        input_mode = str(observation_input.get("mode") or "full")
        input_symbols = list(observation_input.get("targetSymbols") or [])
        emit("observation_input.done", inputMode=input_mode, targetSymbolCount=len(input_symbols))
        # TypeDB rules consume facts, research claims, and bounded summaries.
        # The full provider archive stays on the monitor snapshot for the
        # research/notification read models and is never copied into this live
        # ABox assembly path.
        emit("external_signal_compaction.start")
        projection_external_signals = compact_external_signals_for_ontology(
            snapshot.external_signals,
            target_symbols=input_symbols if input_mode == "target-scoped" else None,
            settings=self.settings,
        )
        input_projection = projection_input_summary(
            snapshot.external_signals,
            projection_external_signals,
            target_symbols=input_symbols if input_mode == "target-scoped" else [],
        )
        emit(
            "external_signal_compaction.done",
            retainedBytes=int(input_projection.get("projectedExternalSignalBytes") or 0),
            sourceBytes=int(input_projection.get("sourceExternalSignalBytes") or 0),
        )
        graph_input_snapshot = replace(snapshot, external_signals=projection_external_signals)
        emit("active_tbox.start")
        active_tbox_started = time.perf_counter()
        active_tbox = self.active_tbox_context()
        stage_timings["activeTBoxReadMs"] = int((time.perf_counter() - active_tbox_started) * 1000)
        emit("active_tbox.done", runtimeMs=stage_timings["activeTBoxReadMs"], status=str(active_tbox.get("status") or ""))
        emit("runtime_context.start")
        runtime_context_started = time.perf_counter()
        runtime_context = self.runtime_context(
            snapshot,
            active_tbox=active_tbox,
            target_symbols=input_symbols if input_mode == "target-scoped" else None,
            progress_callback=lambda stage, **details: emit(
                "runtime_context." + str(stage or "unknown"), **details
            ),
        )
        try:
            runtime_context_packet = pack_projection_runtime_contexts({
                snapshot.account_id: self.last_runtime_contexts.get(snapshot.account_id)
                or frozen_projection_runtime_context(runtime_context),
            })
        except ValueError:
            # Graph assembly remains decision-critical. An oversized optional
            # shadow replay packet may skip V2 sampling, but must never block
            # the active V1 projection.
            runtime_context_packet = {}
        stage_timings["runtimeContextMs"] = int(
            (time.perf_counter() - runtime_context_started) * 1000
        )
        runtime_context_cache = dict(
            self.last_runtime_context_cache_status.get(str(snapshot.account_id or "")) or {}
        )
        stage_timings["runtimeContextCacheHit"] = (
            1 if str(runtime_context_cache.get("status") or "") == "hit" else 0
        )
        stage_timings["runtimeContextCacheAgeMs"] = int(
            runtime_context_cache.get("ageMs") or 0
        )
        emit("runtime_context.done", runtimeMs=stage_timings["runtimeContextMs"])
        decision_memory = (
            runtime_context.get("decisionEpisodeProjection")
            if isinstance(runtime_context, dict)
            else {}
        )
        if isinstance(decision_memory, dict):
            stage_timings["decisionEpisodeSourceCount"] = int(
                decision_memory.get("sourceEpisodeCount") or 0
            )
            stage_timings["decisionEpisodeIncludedCount"] = int(
                decision_memory.get("includedEpisodeCount") or 0
            )
            stage_timings["decisionEpisodeDroppedCount"] = int(
                decision_memory.get("droppedEpisodeCount") or 0
            )
        lifecycle_projection = (
            runtime_context.get("hypothesisLifecycleAboxProjection")
            if isinstance(runtime_context, dict)
            else {}
        )
        if isinstance(lifecycle_projection, dict):
            stage_timings["hypothesisLifecycleAboxProjectionMs"] = int(
                lifecycle_projection.get("readMs") or 0
            )
            stage_timings["hypothesisLifecycleAboxRecordCount"] = int(
                lifecycle_projection.get("recordCount") or 0
            )
            stage_timings["hypothesisLifecycleAboxProjectionEnabled"] = (
                1 if lifecycle_projection.get("enabled") else 0
            )
        cache_enabled = self.graph_assembly_cache_enabled()
        emit("cache_key.start")
        cache_key = self.graph_assembly_cache_key(
            graph_input_snapshot,
            rule_catalog,
            active_tbox,
            runtime_context,
            target_symbols=input_symbols,
            input_mode=input_mode,
        ) if cache_enabled else ""
        emit("cache_key.done", enabled=cache_enabled)
        emit("memory_cache.start")
        cache_read_started = time.perf_counter()
        cache_result = SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.get(
            cache_key,
            self.graph_assembly_cache_ttl_seconds(),
        ) if cache_enabled else {"status": "disabled"}
        stage_timings["graphAssemblyCacheReadMs"] = int(
            (time.perf_counter() - cache_read_started) * 1000
        )
        emit("memory_cache.done", runtimeMs=stage_timings["graphAssemblyCacheReadMs"], status=str(cache_result.get("status") or ""))
        if str(cache_result.get("status") or "") == "hit":
            try:
                cached_contexts = unpack_projection_runtime_contexts(
                    cache_result.get("runtimeContextPacket") or {}
                )
                if snapshot.account_id in cached_contexts:
                    self.last_runtime_contexts[snapshot.account_id] = cached_contexts[snapshot.account_id]
            except ValueError:
                pass
            return (
                cache_result["graph"],
                cache_result["persistenceGraph"],
                {
                    "status": "hit",
                    "cacheLayer": "memory",
                    "ageMs": int(cache_result.get("ageMs") or 0),
                    "inputMode": input_mode,
                    "targetSymbols": input_symbols,
                    "sourcePositionCount": len(observation_input.get("positions") or []),
                    "referencePositionCount": len(observation_input.get("referencePositions") or []),
                    "externalSignalProjection": input_projection,
                    "runtimeStages": stage_timings,
                },
            )

        emit("persistent_cache.start")
        persistent_cache_started = time.perf_counter()
        persistent_cache_result = self.persistent_graph_assembly_cache_get(cache_key) if cache_enabled else {"status": "disabled"}
        stage_timings["graphAssemblyPersistentCacheReadMs"] = int(
            (time.perf_counter() - persistent_cache_started) * 1000
        )
        stage_timings["graphAssemblyPersistentCacheHit"] = (
            1 if str(persistent_cache_result.get("status") or "") == "hit" else 0
        )
        emit("persistent_cache.done", runtimeMs=stage_timings["graphAssemblyPersistentCacheReadMs"], status=str(persistent_cache_result.get("status") or ""))
        if str(persistent_cache_result.get("status") or "") == "hit":
            graph = persistent_cache_result["graph"]
            persistence_graph = persistent_cache_result["persistenceGraph"]
            SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.put(
                cache_key,
                graph,
                persistence_graph,
                self.graph_assembly_cache_max_entries(),
                persistent_cache_result.get("runtimeContextPacket") or {},
            )
            try:
                cached_contexts = unpack_projection_runtime_contexts(
                    persistent_cache_result.get("runtimeContextPacket") or {}
                )
                if snapshot.account_id in cached_contexts:
                    self.last_runtime_contexts[snapshot.account_id] = cached_contexts[snapshot.account_id]
            except ValueError:
                pass
            return (
                deepcopy(graph),
                deepcopy(persistence_graph),
                {
                    "status": "hit",
                    "cacheLayer": "persistent",
                    "ageMs": int(persistent_cache_result.get("ageMs") or 0),
                    "inputMode": input_mode,
                    "targetSymbols": input_symbols,
                    "sourcePositionCount": len(observation_input.get("positions") or []),
                    "referencePositionCount": len(observation_input.get("referencePositions") or []),
                    "externalSignalProjection": input_projection,
                    "runtimeStages": stage_timings,
                },
            )

        emit("ontology_graph.start")
        assembly_started = time.perf_counter()
        graph = build_portfolio_ontology(
            observation_input.get("positions") or [],
            snapshot.portfolio,
            # Current decisions are derived from a preceding TypeDB/AI
            # pass. Native rules must start from observed portfolio and
            # market facts, not use their own previous output as evidence.
            legacy_by_symbol={},
            external_signals=projection_external_signals,
            portfolio_id=snapshot.account_id,
            runtime_context=runtime_context,
            # The realtime path persists only ABox facts. Static TBox
            # vocabulary is seeded independently and presentation output is
            # rebuilt later from the active InferenceBox for an alert or UI.
            include_tbox=False,
            include_presentation=False,
            include_derived_decision_items=False,
            reference_positions=observation_input.get("referencePositions") or [],
        )
        statistical_scoring_required = bool(
            self.statistical_signal_service
            and rule_catalog_requires_statistical_signal_scoring(rule_catalog)
        )
        stage_timings["statisticalSignalScoringRequired"] = (
            1 if statistical_scoring_required else 0
        )
        if statistical_scoring_required:
            emit("statistical_signals.start", symbolCount=len(input_symbols))
            signal_started = time.perf_counter()
            statistical_signal_context = {}
            statistical_result = {}
            try:
                statistical_result = self.statistical_signal_service.run(
                    account_id=snapshot.account_id,
                    backend_id=str(
                        self.settings.get("_reasoningTimeSeriesBackendId")
                        or self.settings.get("timeSeriesActiveBackendId")
                        or "market-time-series"
                    ),
                    windows=runtime_context.get("temporalObservationWindows") or {},
                    as_of=str(snapshot.generated_at or runtime_context.get("asOf") or ""),
                    source_event_id=str(
                        ((runtime_context.get("metadata") or {}).get("sourceEventId") or "")
                        if isinstance(runtime_context.get("metadata"), dict)
                        else ""
                    ),
                    graph=graph,
                    rules=governed_graph_inference_rules(),
                )
                feature_snapshot = statistical_result.get("featureSnapshot")
                signal_snapshot = statistical_result.get("signalSnapshot")
                signal_bundle = statistical_result.get("signalBundle")
                statistical_signal_context = {
                    "temporalFeatureSnapshot": (
                        feature_snapshot.to_dict(include_windows=False)
                        if hasattr(feature_snapshot, "to_dict")
                        else {}
                    ),
                    "statisticalSignalSnapshot": (
                        signal_bundle.to_dict()
                        if hasattr(signal_bundle, "to_dict")
                        else signal_snapshot.to_dict()
                        if hasattr(signal_snapshot, "to_dict")
                        else {}
                    ),
                    "statisticalSignalPipeline": {
                        "status": str(statistical_result.get("status") or ""),
                        "decisionEligible": bool(statistical_result.get("decisionEligible")),
                        "decisionBlockers": list(
                            statistical_result.get("decisionBlockers") or []
                        ),
                        "timings": dict(statistical_result.get("timings") or {}),
                        "persistence": dict(statistical_result.get("persistence") or {}),
                        "pointInTime": dict(statistical_result.get("pointInTime") or {}),
                        "skippedModelReleaseIds": list(
                            statistical_result.get("skippedModelReleaseIds") or []
                        ),
                    },
                }
            except Exception as error:  # noqa: BLE001 - fail closed: no model contract, no predictive rule.
                statistical_signal_context = {
                    "statisticalSignalPipeline": {
                        "status": "error",
                        "reason": str(error)[:300],
                    },
                }
            runtime_context = {
                **dict(runtime_context or {}),
                **statistical_signal_context,
            }
            if bool(statistical_result.get("decisionEligible")):
                stock_entities = [item for item in graph.entities if item.kind == "stock"]
                for stock in stock_entities:
                    symbol = str((stock.properties or {}).get("symbol") or "").upper().strip()
                    if symbol:
                        add_position_statistical_signal_concepts(
                            graph,
                            stock.entity_id,
                            symbol,
                            runtime_context,
                        )
            graph.entities = dedupe_entities(graph.entities)
            graph.relations = dedupe_relations(graph.relations)
            apply_abox_lifecycle(
                graph,
                abox_lifecycle_metadata(
                    graph.portfolio_id,
                    runtime_context,
                    active_tbox,
                ),
            )
            frozen_context = frozen_projection_runtime_context(runtime_context)
            self.last_runtime_contexts[snapshot.account_id] = frozen_context
            try:
                runtime_context_packet = pack_projection_runtime_contexts({
                    snapshot.account_id: frozen_context,
                })
            except ValueError:
                runtime_context_packet = {}
            stage_timings["statisticalSignalPipelineMs"] = int(
                (time.perf_counter() - signal_started) * 1000
            )
            emit(
                "statistical_signals.done",
                runtimeMs=stage_timings["statisticalSignalPipelineMs"],
                status=str(
                    statistical_signal_context.get("statisticalSignalPipeline", {}).get("status")
                    or "unavailable"
                ),
                signalCount=int(
                    statistical_signal_context.get("statisticalSignalSnapshot", {}).get("signalCount")
                    or 0
                ),
            )
        elif self.statistical_signal_service:
            runtime_context = {
                **dict(runtime_context or {}),
                "statisticalSignalPipeline": {
                    "status": "not-required-account-overlay",
                    "reason": (
                        "The PortfolioWorld consumes verified shared-premise references; "
                        "market model contracts are scored once in SharedPremiseWorld."
                    ),
                },
            }
            frozen_context = frozen_projection_runtime_context(runtime_context)
            self.last_runtime_contexts[snapshot.account_id] = frozen_context
            try:
                runtime_context_packet = pack_projection_runtime_contexts({
                    snapshot.account_id: frozen_context,
                })
            except ValueError:
                runtime_context_packet = {}
        emit("ontology_graph.done", runtimeMs=int((time.perf_counter() - assembly_started) * 1000))
        emit("persistence_graph.start")
        persistence_graph = self.graph_for_graph_store_persistence(graph, rule_catalog)
        stage_timings["ontologyGraphAssemblyMs"] = int((time.perf_counter() - assembly_started) * 1000)
        emit("persistence_graph.done", runtimeMs=stage_timings["ontologyGraphAssemblyMs"])
        if cache_enabled:
            SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.put(
                cache_key,
                graph,
                persistence_graph,
                self.graph_assembly_cache_max_entries(),
                runtime_context_packet,
            )
            emit("persistent_cache_write.start")
            persistent_cache_write_started = time.perf_counter()
            persistent_cache_write = self.persistent_graph_assembly_cache_put(
                cache_key,
                graph,
                persistence_graph,
                runtime_context_packet,
            )
            stage_timings["graphAssemblyPersistentCacheWriteMs"] = int(
                (time.perf_counter() - persistent_cache_write_started) * 1000
            )
            if str(persistent_cache_write.get("status") or "") == "stored":
                stage_timings["graphAssemblyPersistentCacheStored"] = 1
            emit("persistent_cache_write.done", runtimeMs=stage_timings["graphAssemblyPersistentCacheWriteMs"], status=str(persistent_cache_write.get("status") or ""))
        return graph, persistence_graph, {
            "status": "miss" if cache_enabled else "disabled",
            "cacheLayer": "none",
            "inputMode": input_mode,
            "targetSymbols": input_symbols,
            "sourcePositionCount": len(observation_input.get("positions") or []),
            "referencePositionCount": len(observation_input.get("referencePositions") or []),
            "externalSignalProjection": input_projection,
            "runtimeStages": stage_timings,
        }

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
    ) -> Dict[str, object]:
        """Assemble one immutable projection graph and its scoped identity."""
        graph_build_started = time.perf_counter()
        partition = {}
        assembly_catalog = rule_catalog
        if self.world_partitioned_reasoning_enabled():
            partition = self.world_rule_partition(rule_catalog)
            if str(partition.get("status") or "") != "ready":
                raise RuntimeError("RuleBox world partition is invalid; PortfolioWorld projection was blocked.")
            assembly_catalog = self.catalog_for_rules(
                rule_catalog,
                partition.get("overlayRules") or [],
            )
        graph, persistence_graph, graph_assembly = self.build_graph_assembly(
            snapshot,
            assembly_catalog,
            target_symbols=target_symbols,
            target_scoped_input=target_scoped_input,
            progress_callback=progress_callback,
        )
        if self.world_partitioned_reasoning_enabled():
            proof = dict(shared_premise_proof or {})
            if not bool(proof.get("ready")):
                raise RuntimeError("SharedPremiseWorld premises are not ready; PortfolioWorld projection was blocked.")
            shared_generation_id = str(
                proof.get("inferenceGenerationId") or ""
            ).strip()
            shared_source_abox_id = str(
                proof.get("sourceAboxSnapshotId") or ""
            ).strip()
            generation_vector = (
                dict(proof.get("generationVector") or {})
                if isinstance(proof.get("generationVector"), dict)
                else {}
            )
            if not shared_generation_id or not shared_source_abox_id:
                raise RuntimeError(
                    "SharedPremiseWorld generation identity is incomplete; PortfolioWorld projection was blocked."
                )
            if generation_vector and (
                str(generation_vector.get("inferenceGenerationId") or "").strip()
                != shared_generation_id
                or str(generation_vector.get("sourceAboxSnapshotId") or "").strip()
                != shared_source_abox_id
            ):
                raise RuntimeError(
                    "SharedPremiseWorld generation vector is incoherent; PortfolioWorld projection was blocked."
                )
            persistence_graph = account_overlay_graph(
                persistence_graph,
                partition.get("overlayRules") or [],
                proof.get("premisesBySymbol") or {},
                shared_generation_id=shared_generation_id,
                source_abox_snapshot_id=shared_source_abox_id,
            )
        runtime_stages = dict(graph_assembly.get("runtimeStages") or {})
        runtime_stages["graphAssemblyCacheHit"] = (
            1 if str(graph_assembly.get("status") or "") == "hit" else 0
        )
        if graph_assembly.get("ageMs") is not None:
            runtime_stages["graphAssemblyCacheAgeMs"] = int(graph_assembly.get("ageMs") or 0)
        planner_topology = native_rule_planner_topology(persistence_graph)
        persistence_graph.worldview["nativeRulePlannerTopology"] = planner_topology
        resolved_market_world = market_world_context or market_world(
            portfolio_world_context.market_id,
            self.settings.get("ontologySharedMarketTenantId") or "shared",
        )
        world_metadata_payload = {
            **world_metadata(portfolio_world_context),
            "marketWorldId": resolved_market_world.world_id,
            "marketContextMode": (
                "shared-premise-account-overlay"
                if self.world_partitioned_reasoning_enabled()
                else "shared-market-world-with-portfolio-rule-mirror"
            ),
        }
        if self.world_partitioned_reasoning_enabled():
            world_metadata_payload.update({
                "sharedPremiseWorldId": str((shared_premise_proof or {}).get("worldId") or ""),
                "sharedPremiseInferenceGenerationId": str(
                    (shared_premise_proof or {}).get("inferenceGenerationId") or ""
                ),
                "sharedPremiseGenerationVector": (
                    dict((shared_premise_proof or {}).get("generationVector") or {})
                    if isinstance(
                        (shared_premise_proof or {}).get("generationVector"),
                        dict,
                    )
                    else {}
                ),
            })
        graph.worldview.update(world_metadata_payload)
        persistence_graph.worldview.update(world_metadata_payload)
        material_fingerprint = native_rule_planner_manifest_fingerprint(
            material_graph_fingerprint(persistence_graph),
            planner_topology,
        )
        material_snapshot_id = apply_material_graph_identity(
            persistence_graph,
            snapshot.account_id,
            material_fingerprint,
            world_id=portfolio_world_context.world_id,
        )
        scoped_identity_started = time.perf_counter()
        scoped_identity = apply_scoped_abox_identity(
            persistence_graph,
            snapshot.account_id,
            world_id=portfolio_world_context.world_id,
            tenant_id=portfolio_world_context.tenant_id,
            world_type=portfolio_world_context.world_type,
        )
        runtime_stages["scopedAboxIdentityMs"] = int(
            (time.perf_counter() - scoped_identity_started) * 1000
        )
        material_snapshot_id = str(scoped_identity.get("manifestId") or material_snapshot_id)
        runtime_stages["graphBuildMs"] = int((time.perf_counter() - graph_build_started) * 1000)
        return {
            "graph": graph,
            "persistenceGraph": persistence_graph,
            "assembly": graph_assembly,
            "plannerTopology": planner_topology,
            "materialFingerprint": material_fingerprint,
            "materialSnapshotId": material_snapshot_id,
            "scopedIdentity": scoped_identity,
            "runtimeStages": runtime_stages,
        }

    def async_quality_record_enabled(self) -> bool:
        value = self.settings.get("ontologyAsyncQualityRecordEnabled")
        if value is None:
            # Existing focused tests rely on a synchronously visible sample;
            # runtime_settings() explicitly opts into the non-blocking path.
            return False
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def inference_detail_outbox_enabled(self) -> bool:
        """Whether full InferenceBox readback may leave the alert path.

        The TypeDB native writer still returns an in-memory materialization and
        the realtime path still verifies its active generation pointer.  This
        setting only controls the expensive second TypeDB expansion used for
        detailed audit/diagnostic storage.
        """
        if not self.inference_detail_outbox:
            return False
        value = self.settings.get("ontologyInferenceDetailOutboxEnabled")
        if value is None:
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    @staticmethod
    def inference_detail_outbox_summary(payload: Dict[str, object]) -> Dict[str, object]:
        """Keep a compact operational queue receipt in the projection audit."""
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        return {
            "status": str(values.get("status") or ""),
            "saved": bool(values.get("saved")),
            "eventuallyConsistent": bool(values.get("eventuallyConsistent")),
            "jobId": str(values.get("jobId") or ""),
            "inferenceGenerationId": str(values.get("inferenceGenerationId") or ""),
            "sourceAboxSnapshotId": str(values.get("sourceAboxSnapshotId") or ""),
            "reason": str(values.get("reason") or "")[:220],
        }

    def enqueue_inference_detail_readback(
        self,
        result: Dict[str, object],
        snapshot: AccountSnapshot,
        inference_symbols: List[str],
        world_id: str = "",
    ) -> Dict[str, object]:
        """Durably request detailed TypeDB rows after a verified publication.

        Only immutable identifiers and the bounded target list go into MySQL.
        The detailed facts stay in TypeDB until the low-priority worker reads
        the exact active generation while the live reasoning queue is empty.
        """
        inferencebox = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else {}
        if not self.inference_detail_outbox_enabled():
            return {
                "status": "disabled",
                "saved": False,
                "eventuallyConsistent": False,
                "reason": "Inference detail durable outbox is not configured.",
            }
        generation_id = str(inferencebox.get("inferenceGenerationId") or "").strip()
        source_abox_id = str(inferencebox.get("sourceAboxSnapshotId") or "").strip()
        clean_world_id = str(world_id or inferencebox.get("worldId") or "").strip()
        targets = sorted({
            str(symbol or "").upper().strip()
            for symbol in (inferencebox.get("targetSymbols") or inference_symbols or [])
            if str(symbol or "").strip()
        })
        if not (generation_id and source_abox_id and clean_world_id):
            return {
                "status": "deferred-incomplete-inference-detail-identity",
                "saved": False,
                "eventuallyConsistent": False,
                "inferenceGenerationId": generation_id,
                "sourceAboxSnapshotId": source_abox_id,
                "reason": "Verified InferenceBox identity is incomplete; detailed readback was not queued.",
            }
        try:
            queued = self.inference_detail_outbox.enqueue(
                world_id=clean_world_id,
                account_id=str(snapshot.account_id or ""),
                inference_generation_id=generation_id,
                source_abox_snapshot_id=source_abox_id,
                target_symbols=targets,
                projection_run_id=str(result.get("projectionRunId") or ""),
                detail_limit=self.inference_snapshot_limit(),
            )
        except Exception as error:  # noqa: BLE001 - detailed audit must not reopen a verified realtime judgement.
            queued = {
                "status": "error",
                "saved": False,
                "eventuallyConsistent": False,
                "inferenceGenerationId": generation_id,
                "sourceAboxSnapshotId": source_abox_id,
                "reason": str(error)[:220],
            }
        return dict(queued or {}) if isinstance(queued, dict) else {
            "status": "error",
            "saved": False,
            "eventuallyConsistent": False,
            "reason": "Inference detail outbox returned a non-dict receipt.",
        }

    def graph_for_typedb_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
        return self.graph_for_graph_store_persistence(graph)

    def shared_market_world_retention_hours(self) -> float:
        try:
            value = float(str(self.settings.get("ontologySharedMarketWorldRetentionHours") or "72"))
        except (TypeError, ValueError):
            value = 72.0
        return max(1.0, min(24.0 * 90.0, value))

    def shared_knowledge_world_retention_hours(self) -> float:
        """Keep durable real-world topology longer than quote observations."""
        try:
            value = float(str(self.settings.get("ontologySharedKnowledgeWorldRetentionHours") or str(24.0 * 365.0)))
        except (TypeError, ValueError):
            value = 24.0 * 365.0
        return max(24.0, min(24.0 * 3650.0, value))

    def shared_world_retention_hours(self, projection_kind: str) -> float:
        if str(projection_kind or "").strip().lower() == "knowledge":
            return self.shared_knowledge_world_retention_hours()
        return self.shared_market_world_retention_hours()

    def shared_market_world_symbol_limit(self) -> int:
        try:
            value = int(float(str(self.settings.get("ontologySharedMarketWorldMaxSymbols") or "1200")))
        except (TypeError, ValueError):
            value = 1200
        return max(50, min(20000, value))

    def shared_market_world_async_projection_enabled(self) -> bool:
        value = self.settings.get("ontologySharedMarketWorldAsyncProjectionEnabled")
        if value is None:
            # Keep direct recorder/unit-test construction deterministic.  The
            # production runtime explicitly enables this setting.
            return False
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def schedule_market_world_projection(
        self,
        portfolio_graph: PortfolioOntology,
        shared_world,
        source_world=None,
    ) -> Dict[str, object]:
        """Queue the latest account-independent market observation safely."""
        return self.schedule_shared_world_projection(
            "market",
            portfolio_graph,
            shared_world,
            source_world=source_world,
        )

    def schedule_knowledge_world_projection(
        self,
        portfolio_graph: PortfolioOntology,
        shared_world,
        source_world=None,
    ) -> Dict[str, object]:
        """Queue durable real-world relationship topology after verification."""
        # KnowledgeWorld has no legacy in-process writer. It is deliberately
        # durable-only so a process exit cannot quietly lose issuer/security
        # topology while a unit-test or a legacy adapter is exercising the
        # PortfolioWorld path.
        if not self.world_projection_outbox:
            return {
                **world_metadata(shared_world),
                "projectionKind": "knowledge",
                "status": "deferred-durable-world-projection-outbox-unavailable",
                "preservedActiveGeneration": True,
                "reason": "KnowledgeWorld requires the durable MySQL projection outbox.",
            }
        return self.schedule_shared_world_projection(
            "knowledge",
            portfolio_graph,
            shared_world,
            source_world=source_world,
        )

    def schedule_shared_world_projection(
        self,
        projection_kind: str,
        portfolio_graph: PortfolioOntology,
        shared_world,
        source_world=None,
    ) -> Dict[str, object]:
        """Use the durable outbox whenever production storage is available.

        The in-process coordinator remains only as a compatibility fallback
        for direct unit-test construction and legacy local adapters.  A
        verified PortfolioWorld must never rely on a daemon thread to retain
        the corresponding shared-world projection request.
        """
        kind = str(projection_kind or "market").strip().lower()
        if self.active_graph_store_key() == "typedb" and self.world_projection_outbox:
            # Never place a complete account ABox into the durable queue. The
            # source graph can contain hypothesis history and raw research
            # documents; each shared world receives only its bounded semantic
            # projection before the message is serialized to MySQL.
            projection_input = self.shared_world_projection_input(
                kind,
                portfolio_graph,
                shared_world,
            )
            return self.world_projection_outbox.enqueue(
                kind,
                shared_world,
                projection_input,
                source_world_id=str(getattr(source_world, "world_id", "") or (portfolio_graph.worldview or {}).get("worldId") or ""),
                source_account_id=str(getattr(source_world, "account_id", "") or ""),
                source_observed_at=str(
                    (projection_input.worldview or {}).get("sourceObservedAt")
                    or (projection_input.worldview or {}).get("marketObservedAt")
                    or (projection_input.worldview or {}).get("asOf")
                    or (portfolio_graph.worldview or {}).get("asOf")
                    or ""
                ),
            )
        if kind == "knowledge":
            return self.project_knowledge_world(portfolio_graph, shared_world)
        if self.active_graph_store_key() != "typedb" or not self.shared_market_world_async_projection_enabled():
            return self.project_market_world(portfolio_graph, shared_world)
        return SHARED_MARKET_WORLD_PROJECTION_COORDINATOR.enqueue(
            self,
            portfolio_graph,
            shared_world,
        )

    def shared_world_projection_input(
        self,
        projection_kind: str,
        portfolio_graph: PortfolioOntology,
        shared_world,
    ) -> PortfolioOntology:
        """Build the small, shareable ABox payload stored in the outbox."""
        kind = str(projection_kind or "market").strip().lower()
        observed_at = str((portfolio_graph.worldview or {}).get("asOf") or "")
        if kind == "knowledge":
            update = build_knowledge_world_graph(portfolio_graph, shared_world, observed_at=observed_at)
        else:
            update = build_market_world_graph(portfolio_graph, shared_world, observed_at=observed_at)
        update.worldview["targetScopedManifestPatch"] = deepcopy(
            dict((portfolio_graph.worldview or {}).get("targetScopedManifestPatch") or {})
        )
        update.worldview["sharedWorldProjection"] = kind
        update.worldview["sourcePortfolioWorldId"] = str(
            (portfolio_graph.worldview or {}).get("worldId") or ""
        )
        return update

    def project_market_world(self, portfolio_graph: PortfolioOntology, shared_world) -> Dict[str, object]:
        observed_at = str((portfolio_graph.worldview or {}).get("asOf") or "")
        update = build_market_world_graph(portfolio_graph, shared_world, observed_at=observed_at)
        update.worldview["targetScopedManifestPatch"] = deepcopy(
            dict((portfolio_graph.worldview or {}).get("targetScopedManifestPatch") or {})
        )
        return self.project_shared_world_update(update, shared_world, projection_kind="market")

    def project_knowledge_world(self, portfolio_graph: PortfolioOntology, shared_world) -> Dict[str, object]:
        observed_at = str((portfolio_graph.worldview or {}).get("asOf") or "")
        update = build_knowledge_world_graph(portfolio_graph, shared_world, observed_at=observed_at)
        return self.project_shared_world_update(update, shared_world, projection_kind="knowledge")

    def project_shared_world_update(
        self,
        update: PortfolioOntology,
        shared_world,
        projection_kind: str = "market",
    ) -> Dict[str, object]:
        """Persist one account-independent shared-world update.

        Market observations and durable knowledge facts can arrive from any
        account cycle.  The active scoped Manifest merges only the changed
        shareable slice, preserving facts contributed by other accounts.  The
        MarketWorld and KnowledgeWorld have no account-specific RuleBox output
        and can be activated directly after ontology validation. A
        SharedPremiseWorld is different: its candidate Manifest remains staged
        until native rules produce an aligned InferenceBox generation.
        """
        kind = str(projection_kind or "market").strip().lower()
        if kind not in {"market", "knowledge", "premise"}:
            kind = "market"
        world_label = {
            "knowledge": "KnowledgeWorld",
            "premise": "SharedPremiseWorld",
        }.get(kind, "MarketWorld")
        status_prefix = kind + "-world"
        shared_contract_version = str(
            (update.worldview or {}).get("sharedWorldProjectionContractVersion") or ""
        ).strip()
        if self.active_graph_store_key() != "typedb":
            return {
                **world_metadata(shared_world),
                "status": "skipped-non-typedb-store",
                "projectionKind": kind,
                "reason": "Shared " + world_label + " is enabled on the TypeDB ontology adapter.",
            }
        metadata_reader = getattr(self.repository, "active_abox_metadata", None)
        scoped_saver = getattr(self.repository, "save_scoped_abox_graph", None)
        if not callable(metadata_reader) or not callable(scoped_saver):
            return {
                **world_metadata(shared_world),
                "status": "deferred-adapter-not-scoped-" + status_prefix,
                "projectionKind": kind,
                "reason": "The active graph adapter cannot update a shared " + world_label + " through scoped Manifests yet.",
            }
        coordinator_lease = self.acquire_projection_coordinator_lease(
            kind + "-world-merge",
            shared_world.world_id,
        )
        if not bool(coordinator_lease.get("acquired")):
            return {
                **world_metadata(shared_world),
                "status": "deferred-projection-coordinator",
                "preservedActiveGeneration": True,
                "projectionKind": kind,
                "retryable": True,
                "recommendedRetryAfterSeconds": int(
                    coordinator_lease.get("recommendedRetryAfterSeconds") or 10
                ),
                "projectionCoordinator": self.projection_coordinator_summary(coordinator_lease),
                "reason": str(
                    coordinator_lease.get("reason")
                    or "다른 TypeDB World 투영이 데이터베이스 쓰기 경계를 사용 중입니다."
                )[:220],
            }
        merge_lease: Dict[str, object] = {}
        acquire_lease = getattr(self.repository, "acquire_scoped_abox_write_lease", None)
        release_lease = getattr(self.repository, "release_scoped_abox_write_lease", None)
        if callable(acquire_lease) and callable(release_lease) and callable(scoped_saver):
            try:
                merge_lease = self.repository_world_call(
                    "acquire_scoped_abox_write_lease",
                    kind + "-world-merge",
                    world_id=shared_world.world_id,
                )
            except Exception as error:  # noqa: BLE001 - the portfolio world must remain independently usable.
                coordinator_release = self.release_projection_coordinator_lease(coordinator_lease)
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-" + status_prefix + "-write-lease",
                    "projectionKind": kind,
                    "reason": "Shared " + world_label + " write lease lookup failed: " + str(error)[:180],
                    "projectionCoordinatorRelease": coordinator_release,
                }
            if not bool(merge_lease.get("acquired")):
                coordinator_release = self.release_projection_coordinator_lease(coordinator_lease)
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-" + status_prefix + "-write-lease",
                    "preservedActiveGeneration": True,
                    "projectionKind": kind,
                    "reason": "Another account is merging or activating the shared " + world_label + ".",
                    "writeLease": {
                        key: value
                        for key, value in dict(merge_lease or {}).items()
                        if key != "propertiesJson"
                    },
                    "projectionCoordinatorRelease": coordinator_release,
                }
        try:
            pending_activation_recovery: Dict[str, object] = {}
            if kind == "premise":
                pending_activation_recovery = self.recover_pending_abox_activation(
                    shared_world.world_id,
                )
                recovery_status = str(
                    pending_activation_recovery.get("status") or "skipped"
                )
                if recovery_status not in {
                    "skipped",
                    "disabled",
                    "finalized",
                    "finalized-empty-target",
                    "restored",
                    "cleared-stale",
                    "discarded-staged-shared-premise",
                }:
                    return {
                        **world_metadata(shared_world),
                        "status": "deferred-premise-world-activation-recovery",
                        "saved": False,
                        "preservedActiveGeneration": True,
                        "projectionKind": kind,
                        "retryable": True,
                        "recommendedRetryAfterSeconds": int(
                            pending_activation_recovery.get("recommendedRetryAfterSeconds") or 10
                        ),
                        "pendingAboxActivationRecovery": pending_activation_recovery,
                        "reason": str(
                            pending_activation_recovery.get("reason")
                            or "SharedPremiseWorld activation recovery must complete before a new projection."
                        )[:220],
                    }
            observed_at = str(
                (update.worldview or {}).get("sourceObservedAt")
                or (update.worldview or {}).get("marketObservedAt")
                or (update.worldview or {}).get("asOf")
                or ""
            )
            try:
                active_market = self.repository_world_call(
                    "active_abox_metadata",
                    world_id=shared_world.world_id,
                )
            except Exception as error:  # noqa: BLE001 - a shared read must never hold a portfolio projection.
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-" + status_prefix + "-metadata",
                    "preservedActiveGeneration": True,
                    "projectionKind": kind,
                    "reason": "Shared " + world_label + " Manifest could not be read: " + str(error)[:180],
                }
            active_market = dict(active_market or {}) if isinstance(active_market, dict) else {}
            active_status = str(active_market.get("status") or "empty").strip().lower()
            if active_status not in {"ok", "empty"}:
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-" + status_prefix + "-metadata",
                    "preservedActiveGeneration": True,
                    "projectionKind": kind,
                    "reason": str(
                        active_market.get("reason")
                        or "Shared " + world_label + " Manifest is not complete."
                    )[:220],
                }
            if (
                active_status == "ok"
                and str(active_market.get("scopedAboxManifestVersion") or "") != SCOPED_ABOX_MANIFEST_VERSION
            ):
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-" + status_prefix + "-legacy-manifest",
                    "preservedActiveGeneration": True,
                    "projectionKind": kind,
                    "reason": "Shared " + world_label + " must be migrated to a scoped Manifest before incremental updates can preserve every active scope.",
                }
            active_contract_version = str(
                active_market.get("sharedWorldProjectionContractVersion") or ""
            ).strip()
            full_contract_rebuild = bool(
                active_status == "ok"
                and shared_contract_version
                and active_contract_version != shared_contract_version
            )
            update.worldview.update({
                **world_metadata(shared_world),
                "sharedWorldProjection": kind,
                "marketWorldProjection": kind == "market",
                "knowledgeWorldProjection": kind == "knowledge",
                "sharedPremiseWorldProjection": kind == "premise",
                "marketContextMode": "shared-" + kind + "-world-direct-premises",
                "sharedWorldProjection": kind,
                "sharedWorldProjectionContractVersion": shared_contract_version,
                "sharedWorldFullRebuild": full_contract_rebuild,
            })
            incoming_planner_topology = {}
            if kind == "premise":
                incoming_planner_topology = native_rule_planner_topology(update)
                update.worldview["nativeRulePlannerTopology"] = incoming_planner_topology
            incoming_fingerprint = material_graph_fingerprint(update)
            apply_material_graph_identity(
                update,
                shared_world.world_id,
                incoming_fingerprint,
                world_id=shared_world.world_id,
            )
            scoped = apply_scoped_abox_identity(
                update,
                shared_world.world_id,
                world_id=shared_world.world_id,
                tenant_id=shared_world.tenant_id,
                world_type=shared_world.world_type,
                world_account_id="",
            )
            incoming_scope_plan = market_scope_plan_with_observation_times(
                update,
                scoped.get("scopePlan") or [],
            )
            # Source observation clocks are manifest metadata, not material
            # facts. They preserve retention/freshness without creating a new
            # generation for every successful polling cycle.
            scoped["scopePlan"] = incoming_scope_plan
            update.worldview["scopePlan"] = incoming_scope_plan
            scope_repair = apply_scoped_abox_repair_epochs(
                update,
                active_market,
                (update.worldview or {}).get("scopeRepairRequestsBySymbol") or {},
            )
            if scope_repair.get("applied"):
                incoming_scope_plan = market_scope_plan_with_observation_times(
                    update,
                    (update.worldview or {}).get("scopePlan") or incoming_scope_plan,
                )
                update.worldview["scopePlan"] = incoming_scope_plan
            market_target_patch = {
                "status": "full-contract-rebuild" if full_contract_rebuild else "full-manifest",
                "selectedIncomingScopeCount": len(incoming_scope_plan),
            }
            source_patch = dict((update.worldview or {}).get("targetScopedManifestPatch") or {})
            target_symbols = list(source_patch.get("targetSymbols") or [])
            if kind == "premise":
                update.worldview["inferenceTargetSymbols"] = list(target_symbols)
            if str(source_patch.get("status") or "") == "applied" and target_symbols:
                selection = select_target_scoped_manifest_patch(
                    update,
                    active_market,
                    target_symbols,
                )
                if selection.get("applied"):
                    incoming_scope_plan = list(selection.get("selectedIncomingScopePlan") or [])
                    market_target_patch = {
                        "status": "applied",
                        "mode": "incremental-target-scoped-manifest-patch",
                        "targetSymbols": list(selection.get("targetSymbols") or []),
                        "selectedIncomingScopeCount": len(
                            selection.get("selectedIncomingScopeIds") or []
                        ),
                        "reusedActiveScopeCount": len(
                            selection.get("reusedActiveScopeIds") or []
                        ),
                        "deferredScopeCount": len(selection.get("deferredScopeIds") or []),
                    }
                else:
                    market_target_patch = {
                        "status": str(selection.get("status") or "full-manifest-fallback"),
                        "mode": "full-manifest-fallback",
                        "targetSymbols": target_symbols,
                        "selectedIncomingScopeCount": len(incoming_scope_plan),
                    }
            manifest_state = merge_market_world_scope_manifest(
                {} if full_contract_rebuild else active_market,
                incoming_scope_plan,
                observed_at=observed_at,
                retention_hours=self.shared_world_retention_hours(kind),
                max_symbols=self.shared_market_world_symbol_limit(),
            )
            scope_generations = dict(manifest_state.get("scopeGenerationIds") or {})
            if not scope_generations:
                return {
                    **world_metadata(shared_world),
                    "status": "skipped-empty-" + status_prefix + "-patch",
                    "projectionKind": kind,
                    "reason": "No shareable " + kind + " fact scope was produced by this portfolio observation.",
                }
            manifest_id = scoped_manifest_id(
                shared_world.world_id,
                scope_generations,
                world_id=shared_world.world_id,
            )
            fingerprint = str(manifest_state.get("materialFingerprint") or incoming_fingerprint)
            if kind == "premise":
                planner_topology = incoming_planner_topology
                if (
                    active_status == "ok"
                    and not full_contract_rebuild
                    and str(market_target_patch.get("status") or "") == "applied"
                ):
                    topology_merge = merge_native_rule_planner_topology(
                        dict(active_market.get("nativeRulePlannerTopology") or {}),
                        incoming_planner_topology,
                        market_target_patch.get("targetSymbols") or [],
                    )
                    if str(topology_merge.get("status") or "") != "ok":
                        return {
                            **world_metadata(shared_world),
                            "status": "deferred-premise-world-planner-topology",
                            "saved": False,
                            "preservedActiveGeneration": True,
                            "projectionKind": kind,
                            "retryable": True,
                            "recommendedRetryAfterSeconds": 10,
                            "reason": str(
                                topology_merge.get("reason")
                                or "SharedPremiseWorld planner topology could not be merged."
                            )[:220],
                            "nativeRulePlannerTopologyMerge": topology_merge,
                        }
                    planner_topology = dict(topology_merge.get("topology") or {})
                    update.worldview["nativeRulePlannerTopologyIncoming"] = incoming_planner_topology
                    update.worldview["nativeRulePlannerTopologyMerge"] = {
                        key: topology_merge.get(key)
                        for key in [
                            "status", "reason", "replacedSymbols", "retainedSymbols",
                            "activeSymbolCount", "incomingSymbolCount", "mergedSymbolCount",
                        ]
                    }
                update.worldview["nativeRulePlannerTopology"] = planner_topology
                fingerprint = native_rule_planner_manifest_fingerprint(
                    fingerprint,
                    planner_topology,
                )
            # A selected link can still point to an untouched active market
            # fact. Rebind every in-memory endpoint to the merged manifest so
            # TypeDB writes the link against that active generation rather than
            # an intentionally deferred source generation.
            bound_manifest = apply_scoped_manifest_plan(
                update,
                manifest_state.get("scopePlan") or [],
                account_id=shared_world.world_id,
                world_id=shared_world.world_id,
                material_fingerprint=fingerprint,
            )
            manifest_id = str(bound_manifest.get("manifestId") or manifest_id)
            if (
                active_status == "ok"
                and not full_contract_rebuild
                and active_material_fingerprint(active_market) == fingerprint
            ):
                observation_refresh = {}
                refreshed_scope_ids = list(manifest_state.get("observationRefreshedScopeIds") or [])
                refresher = getattr(self.repository, "refresh_market_world_observation_metadata", None) if kind == "market" else None
                if refreshed_scope_ids and callable(refresher):
                    try:
                        observation_refresh = self.repository_world_call(
                            "refresh_market_world_observation_metadata",
                            manifest_id,
                            list(manifest_state.get("scopePlan") or []),
                            dict(manifest_state.get("marketScopeObservedAt") or {}),
                            adopted_write_lease=merge_lease,
                            world_id=shared_world.world_id,
                        )
                    except Exception as error:  # noqa: BLE001 - retain the active facts when the metadata heartbeat fails.
                        observation_refresh = {
                            "status": "error",
                            "reason": str(error)[:180],
                        }
                    if str(observation_refresh.get("status") or "") != "ok":
                        return {
                            **world_metadata(shared_world),
                            "status": "deferred-" + status_prefix + "-observation-metadata",
                            "saved": False,
                            "preservedActiveGeneration": True,
                            "projectionKind": kind,
                            "materialFingerprint": fingerprint,
                            "worldviewManifestId": str(active_market.get("aboxSnapshotId") or manifest_id),
                            "projectionMode": "incremental-scoped-manifest-reuse",
                            "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                            "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                            "observationRefreshedScopeIds": refreshed_scope_ids,
                            "observationMetadata": observation_refresh,
                            "targetScopedManifestPatch": market_target_patch,
                            "reason": "공용 " + ("시장" if kind == "market" else "지식") + " 사실은 같지만 소스 관측 시각을 안전하게 갱신하지 못했습니다.",
                        }
                # A portfolio inference retry must not rewrite the shared
                # market generation when this account contributed no new
                # market facts. The portfolio ABox has a separate lifecycle,
                # so preserving this already active MarketWorld cannot hide a
                # candidate portfolio failure or a data freshness change.
                return {
                    **world_metadata(shared_world),
                    "status": "unchanged-material-facts",
                    "saved": False,
                    "preservedActiveGeneration": True,
                    "projectionKind": kind,
                    "materialFingerprint": fingerprint,
                    "worldviewManifestId": str(active_market.get("aboxSnapshotId") or manifest_id),
                    "projectionMode": "incremental-scoped-manifest-reuse",
                    "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                    "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                    "retiredScopeIds": list(manifest_state.get("retiredScopeIds") or []),
                    "changedIncomingScopeIds": list(manifest_state.get("changedIncomingScopeIds") or []),
                    "reusedIncomingScopeIds": list(manifest_state.get("reusedIncomingScopeIds") or []),
                    "observationRefreshedScopeIds": refreshed_scope_ids,
                    "observationMetadata": observation_refresh,
                    "targetScopedManifestPatch": market_target_patch,
                    "pendingAboxActivationRecovery": pending_activation_recovery,
                    "scopeRepair": {
                        key: scope_repair.get(key)
                        for key in [
                            "status", "applied", "requestedScopeIds",
                            "repairedScopeIds", "retainedRepairScopeIds",
                        ]
                        if key in scope_repair
                    },
                    "reason": "공용 " + ("시장" if kind == "market" else "지식") + " 사실이 현재 활성 " + world_label + "와 같아 저장과 활성화를 생략했습니다.",
                }
            update.worldview.update({
                "materialFingerprint": fingerprint,
                "aboxSnapshotId": manifest_id,
                "snapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "scopePlan": list(manifest_state.get("scopePlan") or []),
                "scopeGenerationIds": scope_generations,
                "scopeFingerprints": dict(manifest_state.get("scopeFingerprints") or {}),
                "scopeFamilyCounts": dict(manifest_state.get("scopeFamilyCounts") or {}),
                "marketScopeObservedAt": dict(manifest_state.get("marketScopeObservedAt") or {}),
                "marketScopeObservedAtVersion": str(manifest_state.get("marketScopeObservedAtVersion") or ""),
                "sharedWorldProjectionMode": "incremental-scoped-manifest-patch",
                "sharedWorldActiveScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                "sharedWorldActiveSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                "sharedWorldRetiredScopeIds": list(manifest_state.get("retiredScopeIds") or []),
                "targetScopedManifestPatch": market_target_patch,
                "sharedWorldFullRebuild": full_contract_rebuild,
            })
            validation = validate_ontology(update)
            coverage = (
                knowledge_world_coverage(update)
                if kind == "knowledge"
                else market_world_coverage(update)
            )
            coverage.update({
                "coverageScope": "incoming-" + kind + "-patch",
                "projectionKind": kind,
                "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                "retiredScopeCount": len(manifest_state.get("retiredScopeIds") or []),
                "changedIncomingScopeCount": len(manifest_state.get("changedIncomingScopeIds") or []),
                "reusedIncomingScopeCount": len(manifest_state.get("reusedIncomingScopeIds") or []),
                "observationRefreshedScopeCount": len(manifest_state.get("observationRefreshedScopeIds") or []),
                "targetScopedManifestPatch": market_target_patch,
            })
            if validation.error_count:
                return {
                    **world_metadata(shared_world),
                    "status": "invalid-" + status_prefix,
                    "projectionKind": kind,
                    "materialFingerprint": fingerprint,
                    "coverage": coverage,
                    "validation": validation.to_dict(),
                    "reason": "Shared " + kind + " observations failed ontology validation.",
                }
            # The shared lease covers Manifest metadata read -> scope patch ->
            # stage. MarketWorld and KnowledgeWorld can activate immediately.
            # SharedPremiseWorld keeps the candidate journal staged; one
            # repository transaction boundary activates it only while native
            # rules run and restores the predecessor on any incomplete result.
            save_result = scoped_saver(update, adopted_write_lease=merge_lease) if merge_lease else scoped_saver(update)
            save_result = dict(save_result or {}) if isinstance(save_result, dict) else {
                "saved": False,
                "status": "invalid-save-result",
            }
            activation = {}
            premise_activation_deferred = bool(
                kind == "premise"
                and manifest_id
                and str(save_result.get("status") or "") in {
                    "ok", "staged-scoped-manifest",
                }
                and str(save_result.get("aboxSnapshotId") or manifest_id) == manifest_id
            )
            save_status = str(save_result.get("status") or "error")
            save_failed = save_status not in {"ok", "staged-scoped-manifest"}
            if (
                kind != "premise"
                and manifest_id
                and str(save_result.get("status") or "") in {"ok", "staged-scoped-manifest"}
                and str(save_result.get("aboxSnapshotId") or manifest_id) == manifest_id
            ):
                activation = self.repository_world_call(
                    "activate_scoped_abox_manifest",
                    manifest_id,
                    pending_activation=False,
                    world_id=shared_world.world_id,
                )
            elif premise_activation_deferred:
                activation = {
                    "status": "staged-native-inference",
                    "candidateAboxSnapshotId": manifest_id,
                    "previousAboxSnapshotId": str(
                        active_market.get("worldviewManifestId")
                        or active_market.get("aboxSnapshotId")
                        or ""
                    ),
                    "reason": (
                        "SharedPremiseWorld candidate remains staged until an aligned "
                        "native InferenceBox generation is committed."
                    ),
                }
            return {
                **world_metadata(shared_world),
                "projectionKind": kind,
                "status": (
                    "staged-scoped-manifest"
                    if premise_activation_deferred
                    else
                    "ok"
                    if str((activation or {}).get("status") or "") == "ok"
                    else save_status
                ),
                "retryable": bool(save_result.get("retryable", False)) if save_failed else False,
                "recommendedRetryAfterSeconds": (
                    int(save_result.get("recommendedRetryAfterSeconds") or 30)
                    if save_failed and bool(save_result.get("retryable", False))
                    else 0
                ),
                "reasonCode": str(save_result.get("reasonCode") or "")[:96] if save_failed else "",
                "failureStage": "shared-world-save" if save_failed else "",
                "errorType": str(save_result.get("errorType") or "")[:96] if save_failed else "",
                "reason": str(save_result.get("reason") or "")[:220] if save_failed else "",
                "materialFingerprint": fingerprint,
                "worldviewManifestId": manifest_id,
                "projectionMode": "incremental-scoped-manifest-patch",
                "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                "retiredScopeIds": list(manifest_state.get("retiredScopeIds") or []),
                "changedIncomingScopeIds": list(manifest_state.get("changedIncomingScopeIds") or []),
                "reusedIncomingScopeIds": list(manifest_state.get("reusedIncomingScopeIds") or []),
                "observationRefreshedScopeIds": list(manifest_state.get("observationRefreshedScopeIds") or []),
                "targetScopedManifestPatch": market_target_patch,
                "pendingAboxActivationRecovery": pending_activation_recovery,
                "fullRebuild": full_contract_rebuild,
                "coverage": coverage,
                "validation": validation.to_dict(),
                "save": save_result,
                "activation": activation,
                "activationDeferred": premise_activation_deferred,
                "pendingAboxActivation": dict(
                    save_result.get("pendingAboxActivation") or {}
                ),
                "writeLease": {
                    key: value
                    for key, value in dict(merge_lease or {}).items()
                    if key != "propertiesJson"
                },
                "projectionCoordinator": self.projection_coordinator_summary(coordinator_lease),
            }
        except Exception as error:  # noqa: BLE001 - market sharing must never suppress account reasoning.
            error_type = type(error).__name__
            return {
                **world_metadata(shared_world),
                "projectionKind": kind,
                "status": "error",
                "retryable": True,
                "recommendedRetryAfterSeconds": 30,
                "reasonCode": "typedb-shared-world-projection-error",
                "failureStage": "shared-world-projection",
                "errorType": error_type,
                "reason": str(error)[:220],
            }
        finally:
            if merge_lease and callable(release_lease):
                try:
                    release_lease(merge_lease)
                except Exception:  # noqa: BLE001 - durable expiry protects the next shared update.
                    # The PortfolioWorld inference remains independent; the
                    # short-lived shared lease will expire if a runtime stop
                    # prevents its normal release.
                    pass
            self.release_projection_coordinator_lease(coordinator_lease)

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
        if not hasattr(self.repository, "run_rulebox"):
            return
        inference_symbols = self.inference_symbols(snapshot, target_symbols)
        compact_impact_plan = compact_inference_impact_plan(inference_impact_plan or {}) if inference_impact_plan else {}
        reasoning_context = (
            dict(result.get("reasoningContext") or {})
            if isinstance(result.get("reasoningContext"), dict)
            else {}
        )
        if compact_impact_plan:
            result.setdefault("inferenceImpactPlan", compact_impact_plan)
        active_key = self.active_graph_store_key(result)
        world_id = str(world_id or result.get("worldId") or ((result.get("ontologyWorld") or {}).get("worldId") if isinstance(result.get("ontologyWorld"), dict) else "") or "").strip()
        runtime_stages = result.setdefault("runtimeStages", {})
        world_partition = (
            self.world_rule_partition({"rules": self.rulebox_rules_for_impact()})
            if self.world_partitioned_reasoning_enabled()
            else {}
        )
        if self.world_partitioned_reasoning_enabled():
            if compact_impact_plan:
                compact_impact_plan = compact_inference_impact_plan(
                    partitioned_phase_impact_plan(
                        compact_impact_plan,
                        world_partition,
                        "account-overlay",
                    )
                )
                result["inferenceImpactPlan"] = compact_impact_plan
                projection_scope = result.get("projectionScope")
                if isinstance(projection_scope, dict):
                    projection_scope["inferenceImpactPlan"] = compact_impact_plan
            catalog_rule_ids = list(world_partition.get("overlayRuleIds") or [])
            result_slot_rulebox_hash = compute_rulebox_rules_hash(
                rulebox_rules_to_payload(world_partition.get("overlayRules") or [])
            )
        else:
            catalog_rule_ids = [
                rule_id_from_payload(rule)
                for rule in self.rulebox_rules_for_impact()
                if rule_id_from_payload(rule) and rule.get("enabled", True) is not False
            ]
            result_slot_rulebox_hash = str(rulebox_rules_hash or "")
        # Private handoff to the MySQL execution-proof writer. This catalogue
        # never enters the ABox or the user-facing ontology snapshot.
        result["_ruleResultSlotCatalogRuleIds"] = catalog_rule_ids
        result["_ruleResultSlotRulesHash"] = result_slot_rulebox_hash
        selection_context = {
            "reusable": False,
            "matchedRuleIds": [],
            "matchedRuleCount": 0,
        }
        equivalence_audit_requested = False
        adaptive_target_sharding_profile: Dict[str, object] = {}
        if active_key == "typedb":
            adaptive_target_sharding_profile = self.adaptive_native_rule_target_sharding_profile(
                snapshot,
                world_id=world_id,
                rulebox_rules_hash=rulebox_rules_hash,
            )
            # This is bounded operational telemetry for the audit record. It
            # never becomes an ABox property or a user-facing investment fact.
            result["nativeRuleAdaptiveTargetSharding"] = {
                "status": str(adaptive_target_sharding_profile.get("status") or ""),
                "source": str(adaptive_target_sharding_profile.get("source") or ""),
                "sampledRunCount": int(adaptive_target_sharding_profile.get("sampledRunCount") or 0),
                "compatibleAuditRunCount": int(
                    adaptive_target_sharding_profile.get("compatibleAuditRunCount") or 0
                ),
                "preemptiveRuleIds": list(
                    adaptive_target_sharding_profile.get("preemptiveRuleIds") or []
                )[:20],
            }
        inference_write_lease: Dict[str, object] = {}
        if active_key == "typedb":
            inference_write_lease = self.acquire_inference_write_lease(result, world_id=world_id)
            if inference_write_lease.get("acquired") is False:
                lease_summary = {
                    key: value
                    for key, value in dict(inference_write_lease or {}).items()
                    if key != "propertiesJson" and not key.startswith("_")
                    and key != "projectionCoordinatorLeaseOwned"
                }
                result["inferenceWriteLease"] = lease_summary
                deferred_status = str(
                    inference_write_lease.get("status") or "deferred-inference-write-lease"
                ).strip()
                if deferred_status not in {
                    "deferred-projection-coordinator",
                    "deferred-inference-write-lease",
                }:
                    deferred_status = "deferred-inference-write-lease"
                reason = str(
                    inference_write_lease.get("reason")
                    or "다른 ABox 활성화 또는 TypeDB 네이티브 추론 세대가 실행 중입니다."
                )
                result["ruleboxExecution"] = {
                    "configured": True,
                    "status": deferred_status,
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "nativeTypeDbReasoningUsed": False,
                    "reason": reason,
                }
                result["inferenceBox"] = {
                    "configured": True,
                    "status": deferred_status,
                    "graphStore": "typedb",
                    "source": "typedbInferenceBox",
                    "nativeTypeDbReasoningUsed": False,
                    "reason": reason,
                }
                result["aboxStaged"] = bool(result.get("saved"))
                result["saved"] = False
                result["status"] = deferred_status
                result["preservedActiveGeneration"] = True
                result["retryable"] = True
                result["recommendedRetryAfterSeconds"] = int(
                    inference_write_lease.get("recommendedRetryAfterSeconds") or 10
                )
                result["reason"] = reason
                return
            if inference_write_lease:
                result["inferenceWriteLease"] = {
                    key: value
                    for key, value in dict(inference_write_lease or {}).items()
                    if key != "propertiesJson" and not key.startswith("_")
                    and key != "projectionCoordinatorLeaseOwned"
                }
            if bool(compact_impact_plan.get("nativeRuleSelectionEligible")):
                # Read the old aligned InferenceBox only after owning the
                # writer lease. This makes the reuse proof and the following
                # ABox pointer transition one serialized operation.
                selection_started = time.perf_counter()
                # Reuse only the compact MySQL audit proof. A missing proof no
                # longer triggers a full TypeDB InferenceBox read before the
                # changed candidate rules can run.
                selection_context = self.audited_prior_rule_selection_context(
                    snapshot,
                    inference_symbols,
                    candidate_scope_plan=candidate_scope_plan,
                    rulebox_rules_hash=result_slot_rulebox_hash,
                    tbox_fingerprint=tbox_fingerprint,
                    world_id=world_id,
                    requested_fact_families=compact_impact_plan.get("requestedFactFamilies") or [],
                    requested_fact_families_by_symbol=compact_impact_plan.get("requestedFactFamiliesBySymbol") or {},
                )
                shared_selection_context = self.shared_inference_selection_context(
                    compact_impact_plan,
                    reasoning_context,
                    inference_symbols,
                    selection_context,
                )
                if bool(shared_selection_context.get("reusable")):
                    selection_context = shared_selection_context
                    result["sharedInferenceExecutionReuse"] = {
                        key: value
                        for key, value in shared_selection_context.items()
                        if key in {
                            "reusable", "proofSource", "targetSymbols",
                            "sharedSnapshotIds", "marketRuleCatalogIds",
                            "matchedRuleCount", "candidateRuleCount",
                            "deferredMarketRuleCount", "fallbackReason",
                        }
                    }
                if not selection_context:
                    selection_context = {
                        "reusable": False,
                        "proofSource": "",
                        "matchedRuleIds": [],
                        "matchedRuleCount": 0,
                        "fallbackReason": "compact-prior-proof-unavailable",
                    }
                runtime_stages["priorInferenceReuseReadMs"] = int((time.perf_counter() - selection_started) * 1000)
                recomputed_impact_plan = self.impact_plan_with_audited_candidates(
                    compact_impact_plan,
                    selection_context,
                )
                if isinstance(recomputed_impact_plan, dict) and recomputed_impact_plan:
                    compact_impact_plan = compact_inference_impact_plan(recomputed_impact_plan)
                    result["inferenceImpactPlan"] = compact_impact_plan
                    projection_scope = result.get("projectionScope")
                    if isinstance(projection_scope, dict):
                        projection_scope["inferenceImpactPlan"] = compact_impact_plan
                equivalence_audit_requested = self.incremental_equivalence_audit_selected(
                    snapshot,
                    inference_symbols,
                    compact_impact_plan,
                    selection_context,
                )
                result["priorInferenceReuse"] = {
                    key: value
                    for key, value in selection_context.items()
                    if key not in {"matchedRuleIds", "inferenceImpactPlan", "ruleStatesBySymbol"}
                }
                if bool(selection_context.get("reusable")):
                    result["_priorRuleStatesBySymbol"] = dict(
                        selection_context.get("ruleStatesBySymbol") or {}
                    )
                if equivalence_audit_requested:
                    result["incrementalEquivalenceAudit"] = {
                        "status": "requested-full-evaluation",
                        "verified": False,
                        "samplePct": self.incremental_equivalence_audit_sample_pct(),
                        "reason": "A bounded sample is running the full TypeDB catalogue before slot reconciliation.",
                    }
        try:
            if active_key == "typedb":
                preparer = getattr(self.repository, "prepare_pending_abox_activation_for_inference", None)
                if callable(preparer):
                    preparation_started = time.perf_counter()
                    try:
                        preparation = self.repository_world_call(
                            "prepare_pending_abox_activation_for_inference",
                            world_id=world_id,
                        )
                    except Exception as error:  # noqa: BLE001 - never run native rules against an uncertain active pointer.
                        preparation = {"status": "error", "reason": str(error)[:180]}
                    runtime_stages["aboxActivationPreparationMs"] = int((time.perf_counter() - preparation_started) * 1000)
                    result["aboxActivationPreparation"] = preparation
                    if str(preparation.get("status") or "") not in {"skipped", "ready", "activated"}:
                        result["ruleboxExecution"] = {
                            "configured": True,
                            "status": "blocked-pending-abox-activation",
                            "graphStore": "typedb",
                            "source": "typedbNativeRule",
                            "nativeTypeDbReasoningUsed": False,
                            "reason": str(
                                preparation.get("reason")
                                or "ABox candidate could not be prepared for native inference."
                            )[:220],
                        }
                        result["inferenceBox"] = {
                            "configured": True,
                            "status": "pending-abox-activation",
                            "graphStore": "typedb",
                            "source": "typedbInferenceBox",
                            "nativeTypeDbReasoningUsed": False,
                            "reason": result["ruleboxExecution"]["reason"],
                        }
                        result["aboxStaged"] = bool(result.get("saved"))
                        result["saved"] = False
                        result["status"] = "blocked-pending-abox-activation"
                        result["preservedActiveGeneration"] = True
                        result["reason"] = result["ruleboxExecution"]["reason"]
                        return
            bootstrap_full_rule_coverage = bool(
                active_key == "typedb" and not selection_context.get("reusable")
            )
            if bootstrap_full_rule_coverage:
                result.setdefault("priorInferenceReuse", {}).update({
                    "bootstrapRequired": True,
                    "fallbackReason": str(
                        selection_context.get("fallbackReason")
                        or selection_context.get("reason")
                        or "coherent-rule-result-slot-proof-unavailable"
                    ),
                })
            payload = {
                "worldId": world_id,
                "worldType": str((result.get("ontologyWorld") or {}).get("worldType") or "") if isinstance(result.get("ontologyWorld"), dict) else "",
                "tenantId": str((result.get("ontologyWorld") or {}).get("tenantId") or "") if isinstance(result.get("ontologyWorld"), dict) else "",
                "accountId": str((result.get("ontologyWorld") or {}).get("accountId") or snapshot.account_id or "") if isinstance(result.get("ontologyWorld"), dict) else str(snapshot.account_id or ""),
                "symbols": inference_symbols,
                # Generation retention is intentionally outside the realtime
                # inference boundary. An idle maintenance pass prunes only
                # generations that are no longer active.
                "pruneOldGenerations": False,
                "inferenceSnapshotLimit": self.inference_snapshot_limit(),
                "inferenceImpactPlan": compact_impact_plan,
                "reasoningSubjectKinds": list(
                    []
                    if bootstrap_full_rule_coverage
                    else reasoning_context.get("subjectKinds") or []
                ),
                "reasoningSubjectIds": list(
                    reasoning_context.get("subjectIds") or []
                ),
                "reasoningAffectedSymbols": list(
                    reasoning_context.get("affectedSymbols") or []
                ),
                "nativeRulePlannerTopology": dict(
                    (result.get("nativeRulePlannerTopology") or {})
                    if isinstance(result.get("nativeRulePlannerTopology"), dict)
                    else {}
                ),
                "typedbNativeRuleSelectionEnabled": (
                    "0"
                    if equivalence_audit_requested or bootstrap_full_rule_coverage
                    else self.settings.get("typedbNativeRuleSelectionEnabled", "1")
                ),
                "priorInferenceReusable": bool(selection_context.get("reusable")),
                "priorMatchedRuleIds": list(selection_context.get("matchedRuleIds") or []),
                "priorInferenceProofSource": str(selection_context.get("proofSource") or ""),
                "priorInferenceProofRunId": str(selection_context.get("proofRunId") or ""),
                "nativeRuleAdaptiveTargetShardingProfile": adaptive_target_sharding_profile,
            }
            if self.world_partitioned_reasoning_enabled():
                payload.update({
                    "ruleExecutionPhase": "account-overlay",
                    "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
                })
            if inference_write_lease.get("acquired"):
                payload["_inferenceWriteLeaseOwner"] = str(inference_write_lease.get("leaseOwner") or "")
            if isinstance(preflight_graph, PortfolioOntology):
                # The graph was just validated and staged by this same writer
                # lease. TypeDB still evaluates every selected direct TypeQL rule; this
                # object can only prove an impossible condition and avoids a
                # second exact ABox read before that evaluation.
                payload["_nativePreflightProjectionGraph"] = preflight_graph
                payload["_nativePreflightProjectionManifestId"] = str(
                    preflight_manifest_id
                    or (preflight_graph.worldview or {}).get("worldviewManifestId")
                    or (preflight_graph.worldview or {}).get("aboxSnapshotId")
                    or ""
                )
            try:
                native_inference_started = time.perf_counter()
                execution = self.repository.run_rulebox(payload)
            except Exception as error:  # noqa: BLE001 - graph inference must not block monitoring.
                execution = {"status": "error", "reason": str(error)[:180]}
            finally:
                runtime_stages["nativeInferenceMs"] = int((time.perf_counter() - native_inference_started) * 1000)
            if isinstance(execution, dict):
                execution.setdefault("graphStore", active_key)
                if active_key == "typedb":
                    execution.setdefault("source", "typedbNativeRule")
            else:
                execution = {"status": "error", "reason": "non-dict RuleBox result", "graphStore": active_key}
            result["ruleboxExecution"] = execution
            if equivalence_audit_requested and str(execution.get("status") or "").lower() == "ok":
                result["incrementalEquivalenceAudit"] = compare_incremental_rule_states(
                    selection_context.get("ruleStatesBySymbol") or {},
                    execution,
                    inference_symbols,
                    compact_impact_plan.get("deferredRuleIds") or [],
                )
            if str(execution.get("status") or "") == "deferred-inference-write-lease":
                # Do not inspect an older generation or roll back a candidate
                # while the lease owner is still creating its aligned result.
                reason = str(execution.get("reason") or "Native inference is serialized by another writer.")
                result["inferenceBox"] = {
                    "configured": True,
                    "status": "deferred-inference-write-lease",
                    "graphStore": active_key,
                    "source": "typedbInferenceBox" if active_key == "typedb" else "graphInferenceBox",
                    "nativeTypeDbReasoningUsed": False,
                    "reason": reason,
                }
                result["saved"] = False
                result["status"] = "deferred-inference-write-lease"
                result["preservedActiveGeneration"] = True
                result["reason"] = reason
                return
            if str(execution.get("status") or "") == "invalid-abox-generation":
                # A stale InferenceBox can still be readable while the active
                # candidate cannot prove one source ABox generation. Never
                # let that unrelated durable readback finalize this candidate.
                result["inferenceBox"] = {
                    "configured": True,
                    "status": "invalid-abox-generation",
                    "graphStore": active_key,
                    "source": "typedbInferenceBox",
                    "nativeTypeDbReasoningUsed": False,
                    "reason": str(execution.get("reason") or "Native inference source ABox generation is invalid."),
                }
                finalization_started = time.perf_counter()
                self.reconcile_abox_activation_after_inference(result, inference_symbols, world_id=world_id)
                runtime_stages["aboxActivationFinalizationMs"] = int((time.perf_counter() - finalization_started) * 1000)
                return
            native_failure = (
                native_rule_failure_diagnostic(execution, inference_symbols)
                if active_key == "typedb"
                else {}
            )
            if native_failure:
                # A failed native query leaves the previous durable
                # InferenceBox readable. Reading it here used to turn the
                # original TypeDB timeout into a misleading ABox/InferenceBox
                # alignment failure. Reconcile only to restore the prior ABox
                # generation, while retaining the actual blocking rule in the
                # operational audit payload.
                result["nativeRuleFailure"] = native_failure
                reason = str(
                    native_failure.get("reason")
                    or execution.get("reason")
                    or "TypeDB native RuleBox execution did not complete."
                )
                result["inferenceBox"] = {
                    "configured": True,
                    "status": "native-rule-failed",
                    "graphStore": active_key,
                    "source": "typedbInferenceBox",
                    "nativeTypeDbReasoningUsed": False,
                    "nativeTypeDbReasoningCompleted": False,
                    "nativeInferenceOutcome": "failed",
                    "targetSymbols": list(native_failure.get("targetSymbols") or inference_symbols),
                    "reason": reason,
                }
                finalization_started = time.perf_counter()
                self.reconcile_abox_activation_after_inference(result, inference_symbols, world_id=world_id)
                runtime_stages["aboxActivationFinalizationMs"] = int(
                    (time.perf_counter() - finalization_started) * 1000
                )
                if result.get("preservedActiveGeneration") and bool(native_failure.get("retryable")):
                    result["retryable"] = True
                    result["recommendedRetryAfterSeconds"] = int(
                        native_failure.get("recommendedRetryAfterSeconds") or 30
                    )
                return
            # A native RuleBox execution first builds an in-memory graph and
            # then writes it to TypeDB.  The old path expanded every durable
            # InferenceBox row again before an alert could proceed.  In the
            # production outbox path, prove the active marker/ABox pointer
            # instead and retain the already materialized rows in memory.  A
            # low-priority worker reads the detailed durable snapshot later.
            deferred_detail_readback = False
            memory_snapshot = (
                dict(execution.get("inferenceBox") or {})
                if isinstance(execution.get("inferenceBox"), dict)
                else {}
            )
            commit_proof_reader = getattr(self.repository, "inferencebox_commit_proof", None)
            if (
                active_key == "typedb"
                and self.inference_detail_outbox_enabled()
                and memory_snapshot
                and callable(commit_proof_reader)
            ):
                expected_generation_id = str(
                    memory_snapshot.get("inferenceGenerationId")
                    or execution.get("inferenceGenerationId")
                    or ""
                ).strip()
                expected_source_abox_id = str(
                    memory_snapshot.get("sourceAboxSnapshotId")
                    or execution.get("sourceAboxSnapshotId")
                    or result.get("aboxSnapshotId")
                    or ""
                ).strip()
                if expected_generation_id and expected_source_abox_id:
                    commit_proof_started = time.perf_counter()
                    try:
                        commit_proof = self.repository_world_call(
                            "inferencebox_commit_proof",
                            expected_generation_id,
                            expected_source_abox_id,
                            target_symbols=inference_symbols,
                            world_id=world_id,
                        )
                    except Exception as error:  # noqa: BLE001 - legacy full readback remains the fail-closed fallback.
                        commit_proof = {
                            "status": "error",
                            "verified": False,
                            "reason": "TypeDB active InferenceBox commit proof failed: " + str(error)[:180],
                        }
                    runtime_stages["inferenceCommitProofMs"] = int(
                        (time.perf_counter() - commit_proof_started) * 1000
                    )
                    if isinstance(commit_proof, dict):
                        result["inferenceCommitProof"] = {
                            key: value
                            for key, value in commit_proof.items()
                            if key not in {"propertiesJson"}
                        }
                    if isinstance(commit_proof, dict) and bool(commit_proof.get("verified")):
                        snapshot_payload = dict(memory_snapshot)
                        # Only marker/pointer-proven fields may override the
                        # in-memory materialization. Relations, traces, and
                        # calibration remain the output of this native run.
                        for key in [
                            "status",
                            "graphStore",
                            "worldId",
                            "inferenceGenerationId",
                            "sourceAboxSnapshotId",
                            "activeAboxSnapshotId",
                            "targetSymbols",
                            "targetCoverageStatus",
                            "nativeTypeDbReasoningCompleted",
                            "typedbNativeRuleEvaluationCompleted",
                            "nativeTypeDbReasoningUsed",
                            "typedbNativeRuleReasoningUsed",
                            "nativeInferenceOutcome",
                            "nativeInferenceNoMatch",
                            "generationAligned",
                            "querySource",
                            "typedbReadStatus",
                            "durableCommitProof",
                            "durableReadback",
                        ]:
                            if key in commit_proof:
                                snapshot_payload[key] = commit_proof[key]
                        snapshot_payload.setdefault("graphStore", active_key)
                        snapshot_payload.setdefault("source", "typedbInferenceBox")
                        snapshot_payload["requestedSymbols"] = sorted({
                            str(symbol or "").upper().strip()
                            for symbol in inference_symbols or []
                            if str(symbol or "").strip()
                        })
                        snapshot_payload["durableReadback"] = False
                        snapshot_payload["durableCommitProof"] = True
                        result["inferenceBox"] = snapshot_payload
                        deferred_detail_readback = True
            if (
                not deferred_detail_readback
                and active_key == "typedb"
                and hasattr(self.repository, "inferencebox_snapshot")
            ):
                readback_started = time.perf_counter()
                try:
                    snapshot_payload = self.repository_world_call(
                        "inferencebox_snapshot",
                        symbols=inference_symbols,
                        limit=self.inference_snapshot_limit(),
                        world_id=world_id,
                    )
                except Exception as error:  # noqa: BLE001 - fail closed when durable inference cannot be read.
                    snapshot_payload = {
                        "status": "error",
                        "reason": "TypeDB InferenceBox 재조회 실패: " + str(error)[:180],
                        "graphStore": active_key,
                    }
                if isinstance(snapshot_payload, dict):
                    snapshot_payload = dict(snapshot_payload)
                    snapshot_payload.setdefault("graphStore", active_key)
                    snapshot_payload.setdefault("source", "typedbInferenceBox")
                    snapshot_payload["durableReadback"] = True
                    result["inferenceBox"] = snapshot_payload
                runtime_stages["inferenceDurableReadbackMs"] = int((time.perf_counter() - readback_started) * 1000)
            elif not deferred_detail_readback and isinstance(execution.get("inferenceBox"), dict):
                snapshot_payload = dict(execution.get("inferenceBox") or {})
                snapshot_payload.setdefault("graphStore", active_key)
                result["inferenceBox"] = snapshot_payload
            elif not deferred_detail_readback and hasattr(self.repository, "inferencebox_snapshot"):
                try:
                    snapshot_payload = self.repository_world_call(
                        "inferencebox_snapshot",
                        symbols=inference_symbols,
                        limit=self.inference_snapshot_limit(),
                        world_id=world_id,
                    )
                except Exception as error:  # noqa: BLE001 - snapshot read is best effort.
                    snapshot_payload = {"status": "error", "reason": str(error)[:180], "graphStore": active_key}
                if isinstance(snapshot_payload, dict):
                    snapshot_payload.setdefault("graphStore", active_key)
                    if active_key == "typedb":
                        snapshot_payload.setdefault("source", "typedbInferenceBox")
                    result["inferenceBox"] = snapshot_payload
            finalization_started = time.perf_counter()
            self.reconcile_abox_activation_after_inference(result, inference_symbols, world_id=world_id)
            runtime_stages["aboxActivationFinalizationMs"] = int((time.perf_counter() - finalization_started) * 1000)
            if deferred_detail_readback:
                detail_queue_started = time.perf_counter()
                verified_snapshot = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else {}
                reusable = self.inference_result_is_reusable(
                    verified_snapshot,
                    {"aboxSnapshotId": str(verified_snapshot.get("sourceAboxSnapshotId") or "")},
                    inference_symbols,
                )
                if bool(result.get("saved")) and reusable:
                    detail_receipt = self.enqueue_inference_detail_readback(
                        result,
                        snapshot,
                        inference_symbols,
                        world_id=world_id,
                    )
                else:
                    detail_receipt = {
                        "status": "not-queued-inference-not-finalized",
                        "saved": False,
                        "eventuallyConsistent": False,
                        "reason": "The native generation was not finalized as the active alert-safe result.",
                    }
                result["inferenceDetailOutbox"] = self.inference_detail_outbox_summary(detail_receipt)
                runtime_stages["inferenceDetailOutboxQueueMs"] = int(
                    (time.perf_counter() - detail_queue_started) * 1000
                )
        finally:
            if inference_write_lease.get("acquired"):
                result["inferenceWriteLeaseRelease"] = self.release_inference_write_lease(inference_write_lease)

    def acquire_inference_write_lease(self, result: Dict[str, object], world_id: str = "") -> Dict[str, object]:
        """Serialize ABox preparation and native InferenceBox publication."""
        acquire = getattr(self.repository, "acquire_scoped_abox_write_lease", None)
        if not callable(acquire):
            return {"status": "unsupported"}
        adopted_coordinator = result.get("_projectionCoordinatorLease")
        adopted_coordinator = dict(adopted_coordinator or {}) if isinstance(adopted_coordinator, dict) else {}
        coordinator_owned_here = False
        coordinator_lease = adopted_coordinator
        if not bool(coordinator_lease.get("acquired")):
            coordinator_lease = self.acquire_projection_coordinator_lease(
                "native-inference",
                world_id,
            )
            coordinator_owned_here = bool(coordinator_lease.get("acquired"))
        if not bool(coordinator_lease.get("acquired")):
            return {
                "acquired": False,
                "status": "deferred-projection-coordinator",
                "reason": str(
                    coordinator_lease.get("reason")
                    or "다른 TypeDB World 투영이 데이터베이스 쓰기 경계를 사용 중입니다."
                )[:220],
                "recommendedRetryAfterSeconds": int(
                    coordinator_lease.get("recommendedRetryAfterSeconds") or 10
                ),
                "projectionCoordinator": self.projection_coordinator_summary(coordinator_lease),
            }
        pending = result.get("pendingAboxActivation") if isinstance(result.get("pendingAboxActivation"), dict) else {}
        candidate_id = str(
            pending.get("candidateAboxSnapshotId")
            or result.get("aboxSnapshotId")
            or result.get("worldviewManifestId")
            or "native-rule"
        ).strip()
        try:
            lease = dict(self.repository_world_call(
                "acquire_scoped_abox_write_lease",
                "inference:" + candidate_id,
                world_id=world_id,
            ) or {})
        except Exception as error:  # noqa: BLE001 - do not activate without the writer boundary.
            lease = {"acquired": False, "status": "error", "reason": str(error)[:180]}
        coordinator_release = {}
        if not bool(lease.get("acquired")) and coordinator_owned_here:
            coordinator_release = self.release_projection_coordinator_lease(coordinator_lease)
            coordinator_lease = {
                **coordinator_lease,
                "acquired": False,
                "status": "released-after-world-lease-failure",
            }
        return {
            **lease,
            "projectionCoordinator": {
                **self.projection_coordinator_summary(coordinator_lease),
                **({"release": coordinator_release} if coordinator_release else {}),
            },
            "_projectionCoordinatorLease": coordinator_lease,
            "projectionCoordinatorLeaseOwned": coordinator_owned_here,
        }

    def release_inference_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        """Release the per-world lease first, then this call's global lease."""
        release = {"status": "not-owner"}
        releaser = getattr(self.repository, "release_scoped_abox_write_lease", None)
        if callable(releaser):
            try:
                release = dict(releaser(lease) or {})
            except Exception as error:  # noqa: BLE001 - the global release still must be attempted.
                release = {"status": "error", "reason": str(error)[:180]}
        coordinator_release = {"status": "adopted-by-caller"}
        coordinator = lease.get("_projectionCoordinatorLease") if isinstance(lease, dict) else {}
        if bool(lease.get("projectionCoordinatorLeaseOwned")) and isinstance(coordinator, dict):
            coordinator_release = self.release_projection_coordinator_lease(coordinator)
        return {
            "status": str(release.get("status") or "unknown"),
            "worldLease": release,
            "projectionCoordinator": coordinator_release,
        }

    def reconcile_abox_activation_after_inference(
        self,
        result: Dict[str, object],
        inference_symbols: List[str],
        world_id: str = "",
    ) -> None:
        """Keep active ABox and InferenceBox on the same verified generation.

        ABox candidate persistence must precede TypeDB function evaluation, so
        the pointer is briefly switched before the InferenceBox is known. The
        predecessor stays retained until this method confirms an aligned native
        generation. Any failed or incomplete native execution restores the
        predecessor instead of exposing an ABox that cannot support judgement.
        """
        if self.active_graph_store_key(result) != "typedb":
            return
        verification = result.get("aboxPersistenceVerification")
        verification = verification if isinstance(verification, dict) else {}
        activation = verification.get("activation") if isinstance(verification.get("activation"), dict) else {}
        active_snapshot_id = str(activation.get("snapshotId") or result.get("aboxSnapshotId") or "").strip()
        previous_snapshot_id = str(activation.get("previousSnapshotId") or "").strip()
        activation_is_new = str(activation.get("status") or "") == "activated" and bool(result.get("saved"))
        if not activation_is_new:
            pending_reader = getattr(self.repository, "pending_abox_activation", None)
            if not callable(pending_reader):
                return
            try:
                pending = self.repository_world_call("pending_abox_activation", world_id=world_id)
            except Exception:  # noqa: BLE001 - the current inference result remains independently observable.
                return
            if str((pending or {}).get("status") or "") != "pending":
                return
            active_snapshot_id = str(
                (pending or {}).get("candidateAboxSnapshotId") or active_snapshot_id
            ).strip()
            previous_snapshot_id = str((pending or {}).get("previousAboxSnapshotId") or "").strip()
            if not active_snapshot_id:
                return
        inferencebox = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else {}
        alignment = self.inference_alignment_diagnostics(
            inferencebox,
            active_snapshot_id,
            inference_symbols,
        )
        result["inferenceAlignment"] = alignment
        if self.inference_result_is_reusable(
            inferencebox,
            {"aboxSnapshotId": active_snapshot_id},
            inference_symbols,
        ):
            finalizer = getattr(self.repository, "finalize_abox_generation", None)
            if not callable(finalizer):
                return
            try:
                result["aboxActivationFinalization"] = self.repository_world_call(
                    "finalize_abox_generation",
                    active_snapshot_id,
                    previous_snapshot_id,
                    world_id=world_id,
                )
            except Exception as error:  # noqa: BLE001 - cleanup may be retried without invalidating aligned reasoning.
                result["aboxActivationFinalization"] = {
                    "status": "error",
                    "reason": str(error)[:180],
                    "activeAboxSnapshotId": active_snapshot_id,
                    "previousAboxSnapshotId": previous_snapshot_id,
                }
            finalization = (
                dict(result.get("aboxActivationFinalization") or {})
                if isinstance(result.get("aboxActivationFinalization"), dict)
                else {}
            )
            if str(finalization.get("status") or "") != "ok":
                # The native generation was already proven against this
                # active ABox.  A failure to clear the small activation
                # journal is a control-plane retry, not evidence that the
                # TypeDB projection itself is unsafe.  Keep the event pending
                # and resume finalization through the bounded recovery path;
                # importantly, do not let one journal write outage open the
                # global queue circuit for every other symbol.
                result["saved"] = False
                result["status"] = "inference-finalization-pending"
                result["preservedActiveGeneration"] = True
                result["retryable"] = True
                result["recommendedRetryAfterSeconds"] = 10
                result["reason"] = (
                    "TypeDB 네이티브 추론 세대는 검증됐지만 ABox 완료 표식 정리가 보류되었습니다. "
                    + str(finalization.get("reason") or "다음 짧은 재시도에서 완료 처리합니다.")[:180]
                )
            return

        rollback = {
            "status": "unavailable",
            "reason": "No verified predecessor ABox generation is available for restoration.",
        }
        restore = getattr(self.repository, "activate_abox_generation", None)
        if previous_snapshot_id and callable(restore):
            try:
                rollback = self.repository_world_call(
                    "activate_abox_generation",
                    previous_snapshot_id,
                    world_id=world_id,
                )
            except Exception as error:  # noqa: BLE001 - preserve the explicit blocked state when restore itself fails.
                rollback = {"status": "error", "reason": str(error)[:180]}
        result["activationRollback"] = rollback
        result["saved"] = False
        result["preservedActiveGeneration"] = str(rollback.get("status") or "") == "ok"
        result["status"] = (
            "inference-failed-rolled-back"
            if result["preservedActiveGeneration"]
            else "inference-failed-no-rollback"
        )
        native_failure = result.get("nativeRuleFailure")
        native_failure = dict(native_failure or {}) if isinstance(native_failure, dict) else {}
        failure_rule_id = str(native_failure.get("ruleId") or "").strip()
        failure_reason = str(native_failure.get("reason") or "").strip()
        if failure_reason:
            result["reason"] = (
                "TypeDB 네이티브 규칙 실행 실패"
                + ((" (" + failure_rule_id + ")") if failure_rule_id else "")
                + ": " + failure_reason[:500]
                + " "
                + ("이전 검증 세대로 복원했습니다." if result["preservedActiveGeneration"] else "투자 추론을 차단했습니다.")
                + " 정렬 진단: " + str(alignment.get("summary") or "")
            )
        else:
            result["reason"] = (
                "TypeDB native InferenceBox가 새 ABox 세대와 정렬되지 않아 "
                + ("이전 검증 세대로 복원했습니다." if result["preservedActiveGeneration"] else "투자 추론을 차단했습니다.")
                + " " + str(alignment.get("summary") or "")
            )
        if result["preservedActiveGeneration"]:
            # The prior verified generation is still active. Keep the source
            # event pending and retry with back-pressure instead of opening a
            # failure circuit against a safe rollback.
            result["retryable"] = True
            result["recommendedRetryAfterSeconds"] = int(
                native_failure.get("recommendedRetryAfterSeconds") or 30
            )
        if isinstance(rollback.get("activeAbox"), dict):
            verification["activePointer"] = dict(rollback.get("activeAbox") or {})
            result["aboxPersistenceVerification"] = verification
        if result["preservedActiveGeneration"] and active_snapshot_id:
            # The previous active Manifest is restored synchronously because
            # that preserves judgement correctness. Physical deletion of the
            # failed immutable candidate can involve thousands of rows and
            # belongs to the same idle maintenance pass as normal retention.
            result["failedCandidateCleanup"] = {
                "status": "deferred",
                "aboxSnapshotId": active_snapshot_id,
                "reason": "Failed scoped ABox candidate is retained for idle maintenance cleanup.",
            }

    @staticmethod
    def attach_abox_persistence_runtime_stages(
        runtime_stages: Dict[str, int],
        result: Dict[str, object],
    ) -> None:
        """Expose scoped ABox sub-stage cost without changing persistence behavior."""
        verification = result.get("aboxPersistenceVerification")
        timing = dict(verification.get("timing") or {}) if isinstance(verification, dict) else {}

        def record(source_key: str, target_key: str, source: Dict[str, object]) -> None:
            try:
                value = float(source.get(source_key))
            except (TypeError, ValueError):
                return
            runtime_stages[target_key] = int(round(value))

        for source_key, target_key in {
            "candidateCleanupMs": "aboxCandidateCleanupMs",
            "changedScopeWriteMs": "aboxChangedScopeWriteMs",
            "changedScopeVerificationMs": "aboxChangedScopeVerificationMs",
            "manifestControlWriteMs": "aboxManifestControlWriteMs",
            "totalMs": "aboxScopedPersistenceTotalMs",
        }.items():
            record(source_key, target_key, timing)
        write_plan = timing.get("changedScopeWritePlan")
        if isinstance(write_plan, dict):
            for source_key, target_key in {
                "totalQueryMs": "aboxChangedScopeQueryMs",
                "slowestQueryMs": "aboxChangedScopeSlowestQueryMs",
                "queryCount": "aboxChangedScopeQueryCount",
                "plannedRelationQueryCount": "aboxPlannedRelationQueryCount",
                "transactionCount": "aboxChangedScopeTransactionCount",
                "transactionQueryCount": "aboxChangedScopeTransactionQueryCount",
                "insertedNodeCount": "aboxInsertedNodeCount",
                "insertedRelationCount": "aboxInsertedRelationCount",
                "reusedNodeCount": "aboxReusedNodeCount",
                "reusedRelationCount": "aboxReusedRelationCount",
                "relationGivenBatchCount": "aboxRelationGivenBatchCount",
                "relationGivenRowCount": "aboxRelationGivenRowCount",
                "relationGivenFallbackCount": "aboxRelationGivenFallbackCount",
            }.items():
                record(source_key, target_key, write_plan)
            relation_write_mode = str(write_plan.get("relationWriteMode") or "").strip()
            if relation_write_mode:
                runtime_stages["aboxRelationWriteMode"] = relation_write_mode
        physical_verification = timing.get("changedScopeStorageIdentityVerification")
        if isinstance(physical_verification, dict):
            for source_key, target_key in {
                "manifestScopedReadCount": "aboxManifestVerificationReadCount",
                "reusedStorageIdentityCount": "aboxReusedPhysicalRowCount",
                "conflictCount": "aboxStorageIdentityConflictCount",
            }.items():
                record(source_key, target_key, physical_verification)

    @staticmethod
    def inference_alignment_diagnostics(
        inferencebox: Dict[str, object],
        expected_snapshot_id: str,
        required_symbols: List[str],
    ) -> Dict[str, object]:
        """Describe native generation alignment for retries and audit, not judgement."""
        payload = dict(inferencebox or {})
        expected_id = str(expected_snapshot_id or "").strip()
        actual_id = str(payload.get("sourceAboxSnapshotId") or "").strip()
        expected = sorted({
            str(value or "").upper().strip()
            for value in required_symbols or []
            if str(value or "").strip()
        })
        actual = sorted({
            str(value or "").upper().strip()
            for value in payload.get("targetSymbols") or []
            if str(value or "").strip()
        })
        native_completed = bool(
            payload.get("nativeTypeDbReasoningCompleted")
            or payload.get("typedbNativeRuleEvaluationCompleted")
            or payload.get("nativeTypeDbReasoningUsed")
        )
        issues: List[str] = []
        if not native_completed:
            issues.append("native-evaluation-not-complete")
        if not actual_id:
            issues.append("source-generation-missing")
        elif expected_id and actual_id != expected_id:
            issues.append("source-generation-mismatch")
        if payload.get("generationAligned") is False:
            issues.append("generation-alignment-flag-false")
        missing_symbols = sorted(set(expected).difference(actual))
        if missing_symbols:
            issues.append("target-symbol-coverage-missing")
        summary_by_issue = {
            "native-evaluation-not-complete": "네이티브 규칙 실행 완료 증거가 없습니다.",
            "source-generation-missing": "InferenceBox에 원본 ABox 세대가 없습니다.",
            "source-generation-mismatch": "InferenceBox 원본 ABox 세대가 후보 세대와 다릅니다.",
            "generation-alignment-flag-false": "InferenceBox가 세대 정렬 실패로 표시됐습니다.",
            "target-symbol-coverage-missing": "요청 종목 전체를 포함한 InferenceBox 결과가 아닙니다.",
        }
        return {
            "status": "aligned" if not issues else "misaligned",
            "retryable": bool(issues),
            "expectedAboxSnapshotId": expected_id,
            "actualSourceAboxSnapshotId": actual_id,
            "expectedTargetSymbols": expected,
            "actualTargetSymbols": actual,
            "missingTargetSymbols": missing_symbols,
            "nativeEvaluationCompleted": native_completed,
            "generationAligned": payload.get("generationAligned"),
            "issues": issues,
            "summary": " ".join(summary_by_issue[item] for item in issues),
        }

    def existing_inference_result(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        if not hasattr(self.repository, "inferencebox_snapshot"):
            return {}
        inference_symbols = self.inference_symbols(snapshot, target_symbols)
        try:
            inferencebox = self.repository_world_call(
                "inferencebox_snapshot",
                symbols=inference_symbols,
                limit=self.inference_snapshot_limit(),
                world_id=world_id,
            )
        except Exception as error:  # noqa: BLE001 - unchanged ABox remains valid even if readback fails.
            inferencebox = {"status": "error", "reason": str(error)[:180]}
        return dict(inferencebox or {}) if isinstance(inferencebox, dict) else {}

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool:
        inference_status = str((inferencebox or {}).get("status") or "").strip().lower()
        if inference_status not in {"ok", "empty"}:
            return False
        native_output_used = bool((inferencebox or {}).get("nativeTypeDbReasoningUsed"))
        native_evaluation_completed = bool(
            (inferencebox or {}).get("nativeTypeDbReasoningCompleted")
            or (inferencebox or {}).get("typedbNativeRuleEvaluationCompleted")
            or native_output_used
        )
        # A verified no-match is a complete current-generation result, not a
        # missing InferenceBox. It must be allowed to finalize the matching
        # ABox so the worker can continue to the next subject.
        if not native_evaluation_completed:
            return False
        if inference_status == "empty" and native_output_used:
            return False
        if inference_status == "ok" and not native_output_used:
            return False
        if (inferencebox or {}).get("generationAligned") is False:
            return False
        source_abox_id = str((inferencebox or {}).get("sourceAboxSnapshotId") or "").strip()
        active_abox_id = str((active_abox or {}).get("aboxSnapshotId") or "").strip()
        if not (source_abox_id and active_abox_id and source_abox_id == active_abox_id):
            return False
        expected = {
            str(symbol or "").upper().strip()
            for symbol in list(required_symbols or [])
            if str(symbol or "").strip()
        }
        actual = {
            str(symbol or "").upper().strip()
            for symbol in list((inferencebox or {}).get("targetSymbols") or [])
            if str(symbol or "").strip()
        }
        return not expected or expected.issubset(actual)

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
        """Prove which unaffected native rules must be re-materialized.

        This is a reuse proof, not a Python rule evaluation. Only the compact
        MySQL full-catalog slot generation is accepted. Reading an old
        InferenceBox can recover matched IDs, but cannot prove all non-matches
        came from one generation, so it must trigger a full bootstrap instead.
        """
        audited = self.audited_prior_rule_selection_context(
            snapshot,
            inference_symbols,
            candidate_scope_plan=candidate_scope_plan,
            rulebox_rules_hash=rulebox_rules_hash,
            tbox_fingerprint=tbox_fingerprint,
            world_id=world_id,
            requested_fact_families=requested_fact_families,
            requested_fact_families_by_symbol=requested_fact_families_by_symbol,
        )
        return audited or {
            "reusable": False,
            "proofSource": "",
            "matchedRuleIds": [],
            "matchedRuleCount": 0,
            "fallbackReason": "coherent-rule-result-slot-proof-unavailable",
        }

    def adaptive_native_rule_target_sharding_profile(
        self,
        snapshot: AccountSnapshot,
        world_id: str = "",
        rulebox_rules_hash: str = "",
    ) -> Dict[str, object]:
        """Build a bounded execution-only profile from compatible audits.

        Historical durations are not projected into the ABox and are never a
        RuleBox condition. They only let the TypeDB adapter avoid replaying a
        recently timed-out multi-symbol read at its known unsafe size.
        """

        policy = native_rule_adaptive_target_sharding_policy(self.settings)
        if not bool(policy.get("enabled")):
            return native_rule_adaptive_target_sharding_profile([], self.settings)
        if not self.projection_run_store or not hasattr(self.projection_run_store, "latest"):
            profile = native_rule_adaptive_target_sharding_profile([], self.settings)
            profile["source"] = "projection-audit-unavailable"
            return profile
        read_limit = max(
            20,
            min(160, int(policy.get("lookbackRunLimit") or 12) * 4),
        )
        try:
            namespace = self.execution_namespace()
            rows = self.projection_run_store.latest(
                account_id=str(snapshot.account_id or ""),
                limit=read_limit,
                world_id=world_id,
                execution_namespace_id=str(namespace.get("executionNamespaceId") or ""),
                engine_deployment_id=str(namespace.get("engineDeploymentId") or ""),
                graph_database=str(namespace.get("graphDatabase") or ""),
                release_fingerprint="",
            )
        except Exception:
            profile = native_rule_adaptive_target_sharding_profile([], self.settings)
            profile["source"] = "projection-audit-read-failed"
            return profile

        observations = []
        compatible_rows = 0
        expected_rulebox_hash = str(rulebox_rules_hash or "").strip()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("graphStore") or "").strip() != "typedb":
                continue
            if expected_rulebox_hash and str(row.get("ruleboxRulesHash") or "") != expected_rulebox_hash:
                continue
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            observation = result.get("runtimeObservation") if isinstance(result, dict) else {}
            if not isinstance(observation, dict):
                continue
            compatible_rows += 1
            observations.append(observation)

        profile = native_rule_adaptive_target_sharding_profile(observations, self.settings)
        profile.update({
            "source": "projection-audit",
            "compatibleAuditRunCount": compatible_rows,
        })
        return profile

    @staticmethod
    def matched_rule_ids_from_inference_payload(payload: Dict[str, object]) -> List[str]:
        """Read TypeDB-reported rule ids without treating them as a decision."""
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        collected = []
        for key in ["typedbNativeRuleMatchedRuleIds", "matchedRuleIds"]:
            for value in values.get(key) or []:
                rule_id = str(value or "").strip()
                if rule_id and rule_id not in collected:
                    collected.append(rule_id)
        for trace in values.get("traces") or []:
            if not isinstance(trace, dict):
                continue
            rule_id = str(trace.get("ruleId") or trace.get("sourceRuleId") or "").strip()
            if rule_id and rule_id not in collected:
                collected.append(rule_id)
        return collected[:160]

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
        """Recover a target-specific native-rule proof from projection audit.

        MySQL stores only the immutable scope fingerprints and TypeDB's
        completed match set. It never asserts a new match or decides an
        investment action; the next direct TypeQL query remains the
        evaluator.
        """
        if not self.projection_run_store:
            return {}
        targets = sorted({
            str(symbol or "").upper().strip()
            for symbol in inference_symbols or []
            if str(symbol or "").strip()
        })
        if not targets:
            return {}
        current_rules = [
            rule
            for rule in self.rulebox_rules_for_impact()
            if rule.get("enabled", True) is not False
        ]
        slot_reader = getattr(
            self.projection_run_store,
            "active_rule_result_slot_context",
            None,
        )
        if callable(slot_reader):
            try:
                namespace = self.execution_namespace()
                slot_context = slot_reader(
                    world_id=world_id,
                    account_id=str(snapshot.account_id or ""),
                    symbols=targets,
                    rulebox_rules_hash=rulebox_rules_hash,
                    tbox_fingerprint=tbox_fingerprint,
                    expected_rule_count=len(current_rules),
                    execution_namespace_id=str(namespace.get("executionNamespaceId") or ""),
                    engine_deployment_id=str(namespace.get("engineDeploymentId") or ""),
                    graph_database=str(namespace.get("graphDatabase") or ""),
                    release_fingerprint="",
                )
            except Exception:
                slot_context = {}
            if bool((slot_context or {}).get("reusable")):
                return dict(slot_context)
        # Old projection rows retained only matched IDs and could not prove
        # the state of every non-matching rule from one generation. Never use
        # that partial history as an incremental execution proof.
        return {}

    def shared_inference_selection_context(
        self,
        impact_plan: Dict[str, object],
        reasoning_context: Dict[str, object],
        inference_symbols: List[str],
        account_selection_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Combine exact market reuse proof with account-local rule coverage."""

        proof = (
            reasoning_context.get("sharedInferenceReuseProof")
            if isinstance(reasoning_context, dict)
            and isinstance(reasoning_context.get("sharedInferenceReuseProof"), dict)
            else {}
        )
        targets = sorted({
            str(symbol or "").upper().strip()
            for symbol in inference_symbols or []
            if str(symbol or "").strip()
        })
        proof_targets = sorted({
            str(symbol or "").upper().strip()
            for symbol in proof.get("targetSymbols") or []
            if str(symbol or "").strip()
        })
        if not bool(proof.get("reuseEligible")) or not targets or proof_targets != targets:
            return {}
        enabled_rule_ids = [
            str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
            for rule in self.rulebox_rules_for_impact()
            if isinstance(rule, dict) and rule.get("enabled", True) is not False
            and str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        ]
        if not enabled_rule_ids:
            return {}
        available = set(enabled_rule_ids)
        market_rule_ids = {
            str(rule_id or "").strip()
            for rule_id in proof.get("marketRuleCatalogIds") or []
            if str(rule_id or "").strip()
        }
        matched_market_rule_ids = {
            str(rule_id or "").strip()
            for rule_id in proof.get("matchedMarketRuleIds") or []
            if str(rule_id or "").strip()
        }
        if (
            not market_rule_ids
            or not market_rule_ids.issubset(available)
            or not matched_market_rule_ids.issubset(market_rule_ids)
        ):
            return {}
        account_context = (
            dict(account_selection_context or {})
            if isinstance(account_selection_context, dict)
            else {}
        )
        if bool(account_context.get("reusable")):
            base_candidates = {
                str(rule_id or "").strip()
                for rule_id in account_context.get("candidateRuleIds") or []
                if str(rule_id or "").strip() in available
            }
            if not base_candidates:
                base_candidates = {
                    str(rule_id or "").strip()
                    for rule_id in impact_plan.get("candidateRuleIds") or []
                    if str(rule_id or "").strip() in available
                }
            prior_matches = {
                str(rule_id or "").strip()
                for rule_id in account_context.get("matchedRuleIds") or []
                if str(rule_id or "").strip() in available
            }
        else:
            # Without an account proof every non-market rule is evaluated.
            # Only the exact-revision market catalogue may be deferred.
            base_candidates = set(available)
            prior_matches = set()
        candidate_ids = base_candidates.difference(market_rule_ids)
        prior_matches.difference_update(market_rule_ids)
        prior_matches.update(matched_market_rule_ids)
        if not candidate_ids and matched_market_rule_ids:
            # The native selector requires at least one candidate. Re-running
            # one already matched market rule still avoids all known market
            # non-matches while keeping TypeDB as the evaluator.
            candidate_ids.add(sorted(matched_market_rule_ids)[0])
        if not candidate_ids:
            return {}
        selected_ids = candidate_ids | prior_matches
        if len(selected_ids) >= len(enabled_rule_ids):
            return {}
        symbols = proof.get("symbols") if isinstance(proof.get("symbols"), dict) else {}
        snapshot_ids = sorted({
            str(dict(value or {}).get("snapshotId") or "").strip()
            for value in symbols.values()
            if isinstance(value, dict) and str(value.get("snapshotId") or "").strip()
        })
        return {
            "reusable": True,
            "proofSource": (
                "typedb-rule-result-slots+shared-market-head"
                if bool(account_context.get("reusable"))
                else "shared-market-head+complete-account-catalog"
            ),
            "targetSymbols": targets,
            "sharedSnapshotIds": snapshot_ids,
            "marketRuleCatalogIds": [
                rule_id for rule_id in enabled_rule_ids if rule_id in market_rule_ids
            ],
            "matchedRuleIds": [
                rule_id for rule_id in enabled_rule_ids if rule_id in prior_matches
            ],
            "matchedRuleCount": len(prior_matches),
            "candidateRuleIds": [
                rule_id for rule_id in enabled_rule_ids if rule_id in candidate_ids
            ],
            "candidateRuleCount": len(candidate_ids),
            "deferredMarketRuleCount": len(market_rule_ids.difference(selected_ids)),
            "fallbackReason": "",
        }

    def combine_audited_target_rule_selection_contexts(
        self,
        targets: List[str],
        target_contexts: List[Dict[str, object]],
    ) -> Dict[str, object]:
        """Combine independently verified target proofs for one batch.

        A batch still publishes one coherent TypeDB InferenceBox. Each subject
        can, however, have a different last completed projection. Requiring a
        previous *batch* with the identical target list disabled reuse whenever
        the adaptive scheduler grouped two or more pending symbols. A union of
        complete per-target proofs is safe: candidate rules and prior TypeDB
        matches are both re-executed, never asserted from this audit data.
        """
        contexts = [dict(context or {}) for context in target_contexts or []]
        if not targets or len(contexts) != len(targets):
            return {}
        if all(
            context.get("proofSource") == "typedb-rule-result-slots"
            and bool(context.get("reusable"))
            for context in contexts
        ):
            matched_rule_ids = sorted({
                str(rule_id or "").strip()
                for context in contexts
                for rule_id in context.get("matchedRuleIds") or []
                if str(rule_id or "").strip()
            })
            return {
                "reusable": True,
                "proofSource": "typedb-rule-result-slots",
                "matchedRuleIds": matched_rule_ids,
                "matchedRuleCount": len(matched_rule_ids),
                "reusedTargetSymbols": list(targets),
                "proofRunId": ",".join(
                    str(context.get("proofRunId") or "") for context in contexts
                )[:640],
                "inferenceGenerationId": ",".join(
                    str(context.get("inferenceGenerationId") or "") for context in contexts
                )[:640],
                "sourceAboxSnapshotId": ",".join(
                    str(context.get("sourceAboxSnapshotId") or "") for context in contexts
                )[:640],
                "fallbackReason": "",
            }
        plans = [
            context.get("inferenceImpactPlan")
            for context in contexts
            if isinstance(context.get("inferenceImpactPlan"), dict)
        ]
        if len(plans) != len(targets) or not all(
            bool(plan.get("nativeRuleSelectionEligible")) for plan in plans
        ):
            return {}

        def rule_id(rule: object) -> str:
            if isinstance(rule, dict):
                return str(rule.get("ruleId") or rule.get("rule_id") or "").strip()
            return str(getattr(rule, "rule_id", "") or "").strip()

        enabled_rule_ids = [
            value
            for value in (rule_id(rule) for rule in self.rulebox_rules_for_impact())
            if value
        ]
        if not enabled_rule_ids:
            return {}
        candidate_ids = {
            str(rule_id or "").strip()
            for plan in plans
            for rule_id in plan.get("candidateRuleIds") or []
            if str(rule_id or "").strip()
        }
        candidate_ids.intersection_update(enabled_rule_ids)
        ordered_candidates = [rule_id for rule_id in enabled_rule_ids if rule_id in candidate_ids]
        if not ordered_candidates or len(ordered_candidates) >= len(enabled_rule_ids):
            return {}

        matched_rule_ids: List[str] = []
        proof_run_ids: List[str] = []
        inference_generation_ids: List[str] = []
        source_abox_snapshot_ids: List[str] = []
        for context in contexts:
            for rule_id in context.get("matchedRuleIds") or []:
                clean_rule_id = str(rule_id or "").strip()
                if clean_rule_id and clean_rule_id not in matched_rule_ids:
                    matched_rule_ids.append(clean_rule_id)
            for key, values in [
                ("proofRunId", proof_run_ids),
                ("inferenceGenerationId", inference_generation_ids),
                ("sourceAboxSnapshotId", source_abox_snapshot_ids),
            ]:
                value = str(context.get(key) or "").strip()
                if value and value not in values:
                    values.append(value)

        merged_plan = deepcopy(plans[0])
        deferred_rule_ids = [rule_id for rule_id in enabled_rule_ids if rule_id not in candidate_ids]
        merged_plan.update({
            "explicitTargetSymbols": list(targets),
            "inferenceTargetSymbols": list(targets),
            "candidateRuleIds": ordered_candidates,
            "deferredRuleIds": deferred_rule_ids,
            "candidateRuleCount": len(ordered_candidates),
            "enabledRuleCount": len(enabled_rule_ids),
            "nativeRuleSelectionEligible": True,
            "nativeRuleSelectionEligibilityReason": "audited-multi-target-proof-candidate-subset",
            "auditedTargetReuse": {
                "mode": "independent-target-proofs",
                "targetSymbols": list(targets),
                "proofRunIds": list(proof_run_ids),
            },
        })
        diagnostics = dict(merged_plan.get("diagnostics") or {})
        diagnostics.update({
            "targetSymbolCount": len(targets),
            "candidateRuleCount": len(ordered_candidates),
            "enabledRuleCount": len(enabled_rule_ids),
            "candidateRuleRatioPct": round((len(ordered_candidates) / max(1, len(enabled_rule_ids))) * 100, 1),
            "candidateSubsetAvailable": True,
            "selectionEligibilityReason": "audited-multi-target-proof-candidate-subset",
        })
        reason_codes = list(diagnostics.get("reasonCodes") or [])
        if "audited-multi-target-proof-reuse" not in reason_codes:
            reason_codes.append("audited-multi-target-proof-reuse")
        diagnostics["reasonCodes"] = reason_codes
        merged_plan["diagnostics"] = diagnostics
        return {
            "reusable": True,
            "proofSource": "audited-target-scope-proofs",
            "proofRunId": ",".join(proof_run_ids)[:640],
            "proofRunIds": proof_run_ids,
            "matchedRuleIds": matched_rule_ids,
            "matchedRuleCount": len(matched_rule_ids),
            "inferenceGenerationId": ",".join(inference_generation_ids)[:640],
            "sourceAboxSnapshotId": ",".join(source_abox_snapshot_ids)[:640],
            "inferenceImpactPlan": merged_plan,
            "candidateRuleIds": ordered_candidates,
            "deferredRuleIds": deferred_rule_ids,
            "recomputedCandidateRuleCount": len(ordered_candidates),
            "recomputedChangedScopeCount": sum(
                int(context.get("recomputedChangedScopeCount") or 0)
                for context in contexts
            ),
            "reusedTargetSymbols": list(targets),
            "fallbackReason": "",
        }

    @staticmethod
    def impact_plan_with_audited_candidates(
        impact_plan: Dict[str, object],
        selection_context: Dict[str, object],
    ) -> Dict[str, object]:
        """Apply only proof-backed candidate ids to the current impact plan."""
        base = deepcopy(impact_plan or {}) if isinstance(impact_plan, dict) else {}
        context = dict(selection_context or {}) if isinstance(selection_context, dict) else {}
        if not base or not bool(context.get("reusable")):
            return {}
        candidate_ids = [
            str(rule_id or "").strip()
            for rule_id in context.get("candidateRuleIds") or []
            if str(rule_id or "").strip()
        ]
        all_rule_ids = []
        for rule_id in list(base.get("candidateRuleIds") or []) + list(base.get("deferredRuleIds") or []):
            clean_rule_id = str(rule_id or "").strip()
            if clean_rule_id and clean_rule_id not in all_rule_ids:
                all_rule_ids.append(clean_rule_id)
        if not candidate_ids or not all_rule_ids or any(rule_id not in all_rule_ids for rule_id in candidate_ids):
            return {}
        candidate_ids = [rule_id for rule_id in all_rule_ids if rule_id in candidate_ids]
        if len(candidate_ids) >= len(all_rule_ids):
            return {}
        deferred_rule_ids = [rule_id for rule_id in all_rule_ids if rule_id not in candidate_ids]
        base.update({
            "candidateRuleIds": candidate_ids,
            "deferredRuleIds": deferred_rule_ids,
            "candidateRuleCount": len(candidate_ids),
            "enabledRuleCount": max(int(base.get("enabledRuleCount") or 0), len(all_rule_ids)),
            "nativeRuleSelectionEligible": True,
            "nativeRuleSelectionEligibilityReason": "audited-target-proof-candidate-subset",
        })
        diagnostics = dict(base.get("diagnostics") or {})
        enabled_count = int(base.get("enabledRuleCount") or len(all_rule_ids))
        diagnostics.update({
            "candidateRuleCount": len(candidate_ids),
            "enabledRuleCount": enabled_count,
            "candidateRuleRatioPct": round((len(candidate_ids) / max(1, enabled_count)) * 100, 1),
            "candidateSubsetAvailable": True,
            "selectionEligibilityReason": "audited-target-proof-candidate-subset",
        })
        reason_codes = list(diagnostics.get("reasonCodes") or [])
        if "audited-target-proof-reuse" not in reason_codes:
            reason_codes.append("audited-target-proof-reuse")
        diagnostics["reasonCodes"] = reason_codes
        base["diagnostics"] = diagnostics
        return base

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
        """Select a deterministic low-rate full pass for reuse validation."""

        sample_pct = self.incremental_equivalence_audit_sample_pct()
        if (
            sample_pct <= 0
            or str(selection_context.get("proofSource") or "") != "typedb-rule-result-slots"
            or not isinstance(selection_context.get("ruleStatesBySymbol"), dict)
            or not impact_plan.get("deferredRuleIds")
        ):
            return False
        seed = "|".join([
            str(snapshot.account_id or ""),
            str(snapshot.generated_at or ""),
            ",".join(sorted(str(symbol or "").upper().strip() for symbol in symbols or [])),
            ",".join(str(rule_id or "") for rule_id in impact_plan.get("candidateRuleIds") or []),
        ])
        bucket = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < sample_pct

    def snapshot_symbols(self, snapshot: AccountSnapshot) -> List[str]:
        symbols = []
        for item in list(snapshot.positions or []) + list(snapshot.watchlist or []):
            symbol = str(getattr(item, "symbol", "") or "").upper().strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        for symbol in crypto_markets_by_symbol(getattr(snapshot, "external_signals", {})):
            if symbol not in symbols:
                symbols.append(symbol)
        return symbols

    def inference_symbols(self, snapshot: AccountSnapshot, target_symbols: List[str] = None) -> List[str]:
        """Limit the expensive native TypeQL match to changed subjects when known.

        The ABox still includes the complete live portfolio so portfolio and
        exposure rules retain their full context. Only the TypeDB rule query and
        InferenceBox generation are narrowed to the material event subjects.
        """
        available = set(self.snapshot_symbols(snapshot))
        selected = []
        for symbol in target_symbols or []:
            clean = str(symbol or "").upper().strip()
            if clean and clean in available and clean not in selected:
                selected.append(clean)
        if target_symbols:
            return selected
        return self.snapshot_symbols(snapshot)

    def native_inference_symbol_limit(self) -> int:
        """Return the configured TypeDB native-rule work bound, if enabled."""
        if self.active_graph_store_key() != "typedb":
            return 0
        raw = self.settings.get("typedbNativeRuleTargetSymbolLimit")
        if raw is None or not str(raw).strip():
            return 0
        try:
            return max(1, min(200, int(float(str(raw)))))
        except (TypeError, ValueError):
            return 0

    def bounded_native_inference_symbols(
        self,
        snapshot: AccountSnapshot,
        inferred_symbols: List[str],
        requested_symbols: List[str] = None,
        scheduler_target_symbol_limit: int = 0,
    ) -> List[str]:
        """Prioritize triggering subjects without dropping global ABox context.

        A portfolio or macro scope can affect many holdings, but evaluating all
        of them in one native TypeDB cycle defeats the worker's configured
        per-cycle symbol bound. The complete ABox remains active for each
        rule; only the current RuleBox subjects are sequenced across cycles.
        An explicit request is already a complete scheduler decision and must
        never be refilled with unrelated holdings merely because the configured
        upper bound has spare capacity.
        """
        requested = (
            self.inference_symbols(snapshot, requested_symbols)
            if requested_symbols
            else []
        )
        limit = self.native_inference_symbol_limit()
        if requested:
            return requested[:limit] if limit else requested
        if not limit:
            return list(inferred_symbols or [])
        try:
            scheduled_limit = max(0, min(200, int(float(scheduler_target_symbol_limit or 0))))
        except (TypeError, ValueError):
            scheduled_limit = 0
        if scheduled_limit:
            limit = min(limit, scheduled_limit)
        available = self.snapshot_symbols(snapshot)
        ordered = []
        for symbol in list(inferred_symbols or []) + available:
            clean = str(symbol or "").upper().strip()
            if clean and clean in available and clean not in ordered:
                ordered.append(clean)
        return ordered[:limit]

    @staticmethod
    def scheduler_target_symbol_limit(reasoning_context: Dict[str, object] = None) -> int:
        """Read the runner's operational cap without changing investment meaning.

        The adaptive queue decides how many requested subjects may share one
        coherent generation. The projection layer must enforce that same cap;
        otherwise its configured native limit can refill a one-subject retry
        with unrelated impact-plan symbols.
        """
        context = reasoning_context if isinstance(reasoning_context, dict) else {}
        targets = [
            str(symbol or "").upper().strip()
            for symbol in context.get("targetSymbols") or []
            if str(symbol or "").strip()
        ]
        plan = context.get("batchPlan")
        plan = plan if isinstance(plan, dict) else {}
        if not targets or not plan:
            return 0
        try:
            return max(0, min(200, int(float(plan.get("targetSymbolLimit") or 0))))
        except (TypeError, ValueError):
            return 0

    def scope_integrity_audit_interval_minutes(self) -> float:
        """Return the read-only Manifest integrity audit cadence."""
        try:
            value = float(str(
                self.settings.get("ontologyScopeIntegrityAuditIntervalMinutes") or "30"
            ))
        except (TypeError, ValueError):
            value = 30.0
        return max(5.0, min(24.0 * 60.0, value))

    def scope_integrity_audit_age_minutes(self, active_metadata: Dict[str, object]):
        """Return the last read-only audit age without changing projection scope."""
        stamp = str(
            (active_metadata or {}).get("lastScopeIntegrityAuditAt")
            or (active_metadata or {}).get("lastFullScopeReconcileAt")
            or (active_metadata or {}).get("asOf")
            or ""
        ).strip()
        if not stamp:
            return None
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        age_minutes = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60.0
        return max(0.0, age_minutes)

    @staticmethod
    def reasoning_queue_pressure(reasoning_context: Dict[str, object] = None) -> Dict[str, object]:
        context = reasoning_context if isinstance(reasoning_context, dict) else {}
        pressure = context.get("queuePressure")
        pressure = pressure if isinstance(pressure, dict) else {}

        def number(key: str) -> int:
            try:
                return max(0, int(float(pressure.get(key) or 0)))
            except (TypeError, ValueError):
                return 0

        effective_pending = number("effectivePendingCount")
        selected = number("selectedRequestCount")
        omitted_symbols = number("omittedSymbolCount")
        return {
            "effectivePendingCount": effective_pending,
            "selectedRequestCount": selected,
            "omittedSymbolCount": omitted_symbols,
            "hasDeferredWork": bool(
                pressure.get("hasDeferredWork")
                or omitted_symbols > 0
                or effective_pending > selected
            ),
        }

    def target_scoped_patch_targets(
        self,
        snapshot: AccountSnapshot,
        active_metadata: Dict[str, object],
        scoped_identity: Dict[str, object],
        requested_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Choose a safe incremental write set before replacing a manifest.

        TypeDB still receives the full active world through the manifest. This
        only prevents a one-symbol observation from rewriting unchanged
        symbols. A global request without an explicit subject, a first
        projection keeps the full path. A timer never expands a local event.
        """
        preliminary = self.inference_impact_plan(
            snapshot,
            active_metadata,
            scoped_identity,
            requested_symbols,
            reasoning_context=reasoning_context,
        )
        available = self.snapshot_symbols(snapshot)
        explicit = (
            self.inference_symbols(snapshot, requested_symbols)
            if requested_symbols
            else []
        )
        inferred = explicit or self.inference_symbols(
            snapshot,
            preliminary.get("inferenceTargetSymbols") or requested_symbols,
        )
        inferred = self.bounded_native_inference_symbols(
            snapshot,
            inferred,
            requested_symbols,
            scheduler_target_symbol_limit=self.scheduler_target_symbol_limit(reasoning_context),
        )
        # ``inference_symbols`` falls back to the full snapshot when its
        # argument is empty. An explicit scheduler request is authoritative:
        # impact analysis may select rule families and shared context, but it
        # must not append unrelated subjects to this execution turn.
        integrity_age_minutes = self.scope_integrity_audit_age_minutes(active_metadata)
        integrity_due = (
            integrity_age_minutes is None
            or integrity_age_minutes >= self.scope_integrity_audit_interval_minutes()
        )
        active_manifest_ready = bool(
            str((active_metadata or {}).get("status") or "").lower() == "ok"
            and (active_metadata or {}).get("scopePlan")
            and (active_metadata or {}).get("scopeGenerationIds")
            and str((active_metadata or {}).get("scopedAboxManifestVersion") or "")
            == SCOPED_ABOX_MANIFEST_VERSION
            and str((active_metadata or {}).get("scopeTopologyVersion") or "")
            == SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION
        )
        overlay_contract_migration_required = bool(
            self.world_partitioned_reasoning_enabled()
            and str((active_metadata or {}).get("status") or "").lower() == "ok"
            and str(
                (active_metadata or {}).get("accountOverlayProjectionContractVersion")
                or ""
            ) != ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION
        )
        base = {
            "preliminaryImpactPlan": compact_inference_impact_plan(preliminary),
            "targetSymbols": list(inferred),
            "explicitTargetSymbols": list(explicit),
            "availableSymbolCount": len(available),
            "scopeIntegrityAuditIntervalMinutes": self.scope_integrity_audit_interval_minutes(),
            "scopeIntegrityAuditAgeMinutes": integrity_age_minutes,
            "scopeIntegrityAuditDue": integrity_due,
            "automaticFullProjectionBlocked": active_manifest_ready,
            "accountOverlayContractMigrationRequired": overlay_contract_migration_required,
            "queuePressure": self.reasoning_queue_pressure(reasoning_context),
            "fallbackReason": "",
            "factSlotPlan": build_fact_slot_projection_plan(
                inferred,
                preliminary.get("requestedFactFamilies") or [],
                requested_fact_families_by_symbol=(reasoning_context or {}).get(
                    "requestedScopeFamiliesBySymbol"
                ) or {},
                changed_fields_by_symbol=(reasoning_context or {}).get(
                    "changedFieldsBySymbol"
                ) or {},
                event_boundary_authoritative=bool(
                    (reasoning_context or {}).get(
                        "eventFactBoundaryAuthoritative"
                    )
                ),
            ),
        }
        # A reasoning worker can intentionally schedule one subject even when
        # a shared macro or portfolio fact also changed. Persist that subject
        # and the shared scopes now; the untouched subjects retain their last
        # coherent context until their own queued turn or an explicit source
        # update. Without an explicit worker target, preserve the
        # conservative whole-portfolio path only for an explicitly global
        # change. Integrity checks are owned by the maintenance worker and
        # never promote a local event to this path.
        if preliminary.get("impactScope") == "MARKET_CONTEXT" and not explicit and not inferred:
            return {
                **base,
                "status": "market-context-awaiting-related-subjects",
                "eligible": True,
                "fallbackReason": "no-related-subject-for-market-context-revision",
            }
        if overlay_contract_migration_required:
            return {
                **base,
                "status": "account-overlay-contract-migration",
                "eligible": False,
                "fallbackReason": "legacy-portfolio-market-mirror-must-be-removed",
            }
        if not active_manifest_ready:
            return {
                **base,
                "status": "initial-scoped-manifest-bootstrap",
                "eligible": False,
                "fallbackReason": "active-scoped-manifest-unavailable",
            }
        if not inferred:
            return {
                **base,
                "status": "full-target-set",
                "eligible": False,
                "fallbackReason": "no-inference-target-symbol",
            }
        if len(inferred) >= len(available):
            return {
                **base,
                "status": "full-target-set",
                "eligible": False,
                "fallbackReason": "target-set-covers-active-portfolio",
            }
        return {
            **base,
            "status": (
                "target-scoped-integrity-audit-due"
                if integrity_due
                else
                "target-scoped-explicit-global-context"
                if preliminary.get("globalImpact")
                else "target-scoped-quality-global-context"
                if preliminary.get("qualityScopedGlobalContext")
                else "target-scoped"
            ),
            "eligible": True,
            "fallbackReason": "",
        }

    def inference_impact_plan(
        self,
        snapshot: AccountSnapshot,
        active_abox: Dict[str, object],
        scoped_identity: Dict[str, object],
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Route native inference from immutable scope changes, not a timer."""
        previous_scope_plan = list((active_abox or {}).get("scopePlan") or [])
        next_scope_plan = list((scoped_identity or {}).get("scopePlan") or [])
        return build_inference_impact_plan(
            previous_scope_plan,
            next_scope_plan,
            self.snapshot_symbols(snapshot),
            explicit_target_symbols=target_symbols,
            rules=self.rulebox_rules_for_impact(),
            requested_fact_families=(reasoning_context or {}).get("requestedScopeFamilies") or [],
            requested_fact_families_by_symbol=(reasoning_context or {}).get("requestedScopeFamiliesBySymbol") or {},
            requested_dependency_keys=(reasoning_context or {}).get("requestedDependencyKeys") or [],
            requested_dependency_keys_by_symbol=(reasoning_context or {}).get("requestedDependencyKeysBySymbol") or {},
            dependency_boundary_authoritative=bool(
                (reasoning_context or {}).get(
                    "eventDependencyBoundaryAuthoritative"
                )
            ),
        )

    def rulebox_rules_for_impact(self) -> List[Dict[str, object]]:
        cached = getattr(self, "_rulebox_impact_rules", None)
        if isinstance(cached, list):
            return [dict(item) for item in cached if isinstance(item, dict)]
        if not hasattr(self.repository, "rulebox_snapshot"):
            return []
        try:
            snapshot = self.repository.rulebox_snapshot()
        except Exception:  # noqa: BLE001 - complete native evaluation remains safe without dependency metadata.
            return []
        rules = snapshot.get("rules") if isinstance(snapshot, dict) else []
        return [dict(item) for item in rules or [] if isinstance(item, dict)]

    def inference_snapshot_limit(self) -> int:
        try:
            value = int(float(str(self.settings.get("investmentBrainInferenceBoxLimit") or 500)))
        except (TypeError, ValueError):
            value = 500
        return max(80, min(500, value))

    def has_projectable_data(self, snapshot: AccountSnapshot) -> bool:
        if not snapshot.has_live_account_data():
            return False
        if any(
            item
            for item in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if not item.is_cash()
        ):
            return True
        # BTC/ETH market subjects are not account holdings. They remain
        # projectable when CoinGecko supplies a current source fact.
        return bool(crypto_markets_by_symbol(snapshot.external_signals))

    def typedb_projection_deferred(self) -> bool:
        if self.active_graph_store_key() != "typedb":
            return False
        if "typedbNativeRuleExecutionEnabled" not in self.settings:
            return False
        return str(self.settings.get("typedbNativeRuleExecutionEnabled") or "").strip().lower() in {
            "0", "false", "no", "off", "disabled",
        }

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str:
        key = str(getattr(self.repository, "store_key", "") or "").strip()
        return key or "graph-store"

    @staticmethod
    def projection_coordinator_summary(lease: Dict[str, object]) -> Dict[str, object]:
        """Keep TypeDB coordination visible without leaking control JSON."""
        allowed = {
            "acquired",
            "status",
            "coordinator",
            "coordinatorVersion",
            "requestedWorldId",
            "leaseOwner",
            "leaseExpiresAtEpoch",
            "leaseRemainingSeconds",
            "recommendedRetryAfterSeconds",
            "reason",
        }
        return {
            key: value
            for key, value in dict(lease or {}).items()
            if key in allowed and value not in (None, "", [], {})
        }

    def acquire_projection_coordinator_lease(
        self,
        owner: str,
        world_id: str,
    ) -> Dict[str, object]:
        """Claim the narrow TypeDB physical-write boundary when available."""
        if self.active_graph_store_key() != "typedb":
            return {"acquired": True, "status": "not-typedb"}
        acquire = getattr(self.repository, "acquire_projection_coordinator_lease", None)
        if not callable(acquire):
            # Compatibility adapters retain their existing per-world lease.
            return {"acquired": True, "status": "unsupported"}
        try:
            return dict(acquire(owner, world_id=world_id) or {})
        except Exception as error:  # noqa: BLE001 - never replace the active generation without this boundary.
            return {
                "acquired": False,
                "status": "error",
                "requestedWorldId": str(world_id or ""),
                "recommendedRetryAfterSeconds": 10,
                "reason": str(error)[:180],
            }

    def release_projection_coordinator_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        if not bool((lease or {}).get("acquired")):
            return {"status": "not-owner"}
        releaser = getattr(self.repository, "release_projection_coordinator_lease", None)
        if not callable(releaser):
            return {"status": "unsupported"}
        last_result: Dict[str, object] = {}
        for attempt in range(1, 3):
            try:
                last_result = dict(releaser(lease) or {})
            except Exception as error:  # noqa: BLE001 - the same owner token is safe to retry.
                last_result = {"status": "error", "reason": str(error)[:180]}
            if str(last_result.get("status") or "") in {
                "released", "disabled", "not-owner", "missing", "unsupported",
            }:
                if attempt > 1:
                    last_result["releaseAttempts"] = attempt
                return last_result
        return {
            **last_result,
            "status": "error",
            "releaseAttempts": 2,
            "retryable": True,
            "reason": str(
                last_result.get("reason")
                or "TypeDB projection coordinator release did not reach a terminal state."
            )[:180],
        }

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
        """Persist source facts before replacing the active TypeDB generation."""
        if not self.projection_run_store:
            return None, ""
        run = build_ontology_projection_run(
            snapshot,
            graph,
            material_fingerprint,
            abox_snapshot_id,
            self.active_graph_store_key(),
            target_symbols=inference_symbols,
            rulebox_metadata=rulebox_metadata,
            reasoning_context=reasoning_context,
            execution_namespace=self.execution_namespace(),
        )
        try:
            self.projection_run_store.begin(run)
        except Exception as error:  # noqa: BLE001 - an un-audited generation must not replace the active ABox.
            return None, str(error)[:180]
        apply_projection_run_identity(graph, run.run_id)
        return run, ""

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None:
        ontology = snapshot.metadata.setdefault("ontology", {})
        # Runtime identity is audit metadata only. It never becomes an ABox
        # fact and therefore cannot influence an investment inference.
        # Cached snapshots may carry the identity of the process that produced
        # the previous generation. This result belongs to the current writer,
        # so stale audit metadata must never win through ``setdefault``.
        result["runtimeIdentity"] = runtime_identity()
        active_key = self.active_graph_store_key(result)
        result.setdefault("graphStore", active_key)
        result.setdefault("activeGraphStore", active_key)
        if projection_run and self.projection_run_store:
            try:
                self.attach_inference_reuse_proof(projection_run, result)
                completed_run = complete_ontology_projection_run(projection_run, result)
                # Keep projection cost, scope impact, native trace coverage,
                # and scoped ABox cleanup in the same durable audit row as
                # the factual source snapshot. This is operational telemetry;
                # it never participates in investment rule evaluation.
                result["runtimeObservation"] = build_projection_runtime_observation(
                    completed_run,
                    result,
                    self.settings,
                )
                completed_run = replace(completed_run, result_payload={
                    **dict(completed_run.result_payload or {}),
                    "runtimeObservation": dict(result["runtimeObservation"]),
                })
                complete_with_trace = getattr(
                    self.projection_run_store,
                    "complete_with_execution_trace",
                    None,
                )
                if callable(complete_with_trace):
                    complete_with_trace(completed_run, result)
                else:
                    self.projection_run_store.complete(completed_run)
                result["projectionAudit"] = {
                    "status": "recorded",
                    "runId": completed_run.run_id,
                    "sourceSnapshotRecorded": True,
                    "activeAboxSnapshotId": completed_run.active_abox_snapshot_id,
                }
            except Exception as error:  # noqa: BLE001 - TypeDB state stays observable when final audit sync is retried.
                result["projectionAudit"] = {
                    "status": "pending-sync",
                    "runId": projection_run.run_id,
                    "sourceSnapshotRecorded": True,
                    "reason": str(error)[:180],
                }
        result.pop("_ruleResultSlotCatalogRuleIds", None)
        result.pop("_ruleResultSlotRulesHash", None)
        result.pop("_priorRuleStatesBySymbol", None)
        ontology[active_key] = result
        ontology["projection"] = result
        ontology["activeGraphStore"] = active_key

    def attach_inference_reuse_proof(
        self,
        projection_run: OntologyProjectionRun,
        result: Dict[str, object],
    ) -> None:
        """Record a TypeDB-complete target result for later rule scheduling.

        The proof retains only scope fingerprints and TypeDB-reported rule
        identities. It does not carry ABox values or derive an investment
        outcome outside TypeDB.
        """
        context = dict(projection_run.context_payload or {})
        topology = context.get("scopeTopology") if isinstance(context.get("scopeTopology"), dict) else {}
        scope_plan = inference_reuse_scope_plan(topology.get("inferenceReuseScopePlan") or [])
        scope_plan_fingerprint = inference_reuse_scope_plan_fingerprint(scope_plan) if scope_plan else ""
        inference = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else {}
        execution = result.get("ruleboxExecution") if isinstance(result.get("ruleboxExecution"), dict) else {}
        matched_rule_ids = self.matched_rule_ids_from_inference_payload(execution)
        for rule_id in self.matched_rule_ids_from_inference_payload(inference):
            if rule_id not in matched_rule_ids:
                matched_rule_ids.append(rule_id)
        native_evaluation_complete = bool(
            inference.get("nativeTypeDbReasoningCompleted")
            or inference.get("typedbNativeRuleEvaluationCompleted")
            or execution.get("nativeInferenceEvaluationComplete")
        )
        target_symbols = [
            str(symbol or "").upper().strip()
            for symbol in list(inference.get("targetSymbols") or projection_run.source_symbols or [])
            if str(symbol or "").strip()
        ]
        source_abox_snapshot_id = str(inference.get("sourceAboxSnapshotId") or "").strip()
        expected_abox_snapshot_id = str(projection_run.abox_snapshot_id or "").strip()
        selection_applied = bool(execution.get("nativeRuleSelectionApplied"))
        inherited_coverage = bool(
            selection_applied
            and isinstance(result.get("priorInferenceReuse"), dict)
            and result["priorInferenceReuse"].get("reusable")
        )
        coverage_complete = bool(not selection_applied or inherited_coverage)
        matched_count = int(execution.get("typedbNativeRuleMatchedCount") or 0)
        match_ids_complete = not matched_count or bool(matched_rule_ids)
        verified = bool(
            str(result.get("status") or "") == "ok"
            and native_evaluation_complete
            and bool(inference.get("generationAligned"))
            and bool(scope_plan)
            and scope_plan_fingerprint == str(topology.get("inferenceReuseScopePlanFingerprint") or "")
            and bool(projection_run.rulebox_rules_hash)
            and bool(projection_run.tbox_fingerprint)
            and bool(projection_run.execution_namespace_id)
            and bool(projection_run.engine_deployment_id)
            and bool(projection_run.graph_database)
            and bool(projection_run.release_fingerprint)
            and bool(target_symbols)
            and source_abox_snapshot_id == expected_abox_snapshot_id
            and coverage_complete
            and match_ids_complete
        )
        if verified:
            reason = ""
        elif not coverage_complete:
            reason = "Previous native coverage was unavailable for a dependency-selected inference result."
        elif matched_count and not matched_rule_ids:
            reason = "TypeDB reported matches without persisted matched rule identities."
        else:
            reason = "Current TypeDB inference did not produce a complete reusable target proof."
        result["inferenceReuseProof"] = {
            "version": INFERENCE_REUSE_PROOF_VERSION,
            "status": "verified" if verified else "incomplete",
            "reason": reason,
            "coverageComplete": coverage_complete,
            "executionNamespaceId": str(projection_run.execution_namespace_id or ""),
            "engineDeploymentId": str(projection_run.engine_deployment_id or ""),
            "graphDatabase": str(projection_run.graph_database or ""),
            "releaseFingerprint": str(projection_run.release_fingerprint or ""),
            "sourceAboxSnapshotId": source_abox_snapshot_id,
            "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
            "targetSymbols": target_symbols,
            "matchedRuleIds": matched_rule_ids[:160],
            "matchedRuleCount": len(matched_rule_ids),
            "ruleboxRulesHash": str(projection_run.rulebox_rules_hash or ""),
            "tboxFingerprint": str(projection_run.tbox_fingerprint or ""),
            "scopePlanFingerprint": scope_plan_fingerprint,
            "scopePlanCount": len(scope_plan),
            "selectionApplied": selection_applied,
            "inheritedCoverage": inherited_coverage,
        }
        result["nativeReplayValidation"] = native_replay_validation(result)

    def runtime_context(
        self,
        snapshot: AccountSnapshot,
        active_tbox: Dict[str, object] = None,
        target_symbols: List[str] = None,
        progress_callback: Callable[..., None] = None,
    ) -> Dict[str, object]:
        account_id = str(snapshot.account_id or "")
        override = self.runtime_context_overrides.get(account_id)
        if override:
            frozen = frozen_projection_runtime_context(override)
            self.last_runtime_contexts[account_id] = frozen
            self.last_runtime_context_cache_status[account_id] = {"status": "override"}
            return deepcopy(frozen)

        def emit(stage: str, **details) -> None:
            if not callable(progress_callback):
                return
            try:
                progress_callback(str(stage or "unknown"), **details)
            except Exception:
                return

        if active_tbox is None:
            active_tbox = self.active_tbox_context()
        cache_enabled = self.runtime_context_cache_enabled()
        cache_key = self.runtime_context_cache_key(
            snapshot,
            active_tbox,
            target_symbols=target_symbols,
        ) if cache_enabled else ""
        cache_result = SHARED_PROJECTION_RUNTIME_CONTEXT_CACHE.get(
            cache_key,
            self.runtime_context_cache_ttl_seconds(),
        ) if cache_enabled else {"status": "disabled"}
        self.last_runtime_context_cache_status[account_id] = {
            "status": str(cache_result.get("status") or "miss"),
            "ageMs": int(cache_result.get("ageMs") or 0),
        }
        emit(
            "cache." + str(cache_result.get("status") or "miss"),
            ageMs=int(cache_result.get("ageMs") or 0),
        )
        if str(cache_result.get("status") or "") == "hit":
            frozen = frozen_projection_runtime_context(cache_result.get("context") or {})
            self.last_runtime_contexts[account_id] = frozen
            return deepcopy(frozen)
        as_of = str(snapshot.generated_at or "").strip()
        snapshot_seed = "|".join([str(snapshot.account_id or ""), as_of or "unknown"])
        selected_symbols = {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        }
        available_symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
        }
        selected_symbols.intersection_update(available_symbols)
        decision_memory_symbols = selected_symbols or available_symbols
        emit("decision_episodes.start", symbolCount=len(decision_memory_symbols))
        decision_memory = self.decision_episode_projection_context(
            snapshot,
            target_symbols=decision_memory_symbols,
        )
        decision_episodes = list(decision_memory.get("episodes") or [])
        emit("decision_episodes.done", episodeCount=len(decision_episodes))
        emit("metadata.start")
        metadata = self.factual_runtime_metadata(
            snapshot.metadata,
            target_symbols=selected_symbols or available_symbols,
            settings=self.settings,
        )
        emit("metadata.done", metadataKeyCount=len(metadata))
        # Projection output is derived state, not a new market observation.
        # Feeding the previous ABox result back into the next ABox makes an
        # otherwise unchanged snapshot look materially different.
        metadata.pop("ontology", None)
        account_context = metadata.get("accountContext") if isinstance(metadata.get("accountContext"), dict) else {}
        emit("decision_performance.start")
        decision_performance = evaluate_decision_performance(
            decision_episodes,
            minimum_sample_count=int(self.performance_setting("investmentBrainPerformanceMinimumSamples", 5)),
        )
        emit("decision_performance.done")
        emit("hypothesis_proposals.start")
        hypothesis_proposals = self.hypothesis_proposal_context(
            snapshot,
            target_symbols=selected_symbols,
        )
        emit("hypothesis_proposals.done", proposalCount=len(hypothesis_proposals))
        lifecycle_projection_started = time.perf_counter()
        lifecycle_projection = {
            "mode": "excluded-from-live-abox",
            "enabled": False,
            "recordCount": 0,
            "payloadBytesRead": 0,
            "keyPrefix": HYPOTHESIS_LIFECYCLE_KEY_PREFIX,
        }
        emit("hypothesis_lifecycles.start", mode=lifecycle_projection["mode"])
        hypothesis_lifecycles = []
        if self.hypothesis_lifecycle_abox_projection_enabled():
            hypothesis_lifecycles = self.hypothesis_lifecycle_context(
                snapshot,
                target_symbols=selected_symbols,
            )
            lifecycle_projection.update({
                "mode": "compact-opt-in-audit",
                "enabled": True,
                "recordCount": len(hypothesis_lifecycles),
            })
        lifecycle_projection["readMs"] = int((time.perf_counter() - lifecycle_projection_started) * 1000)
        emit(
            "hypothesis_lifecycles.done",
            lifecycleCount=len(hypothesis_lifecycles),
            mode=lifecycle_projection["mode"],
            runtimeMs=lifecycle_projection["readMs"],
        )
        emit("pipeline_health.start")
        data_pipeline_health = self.data_pipeline_health_context(snapshot)
        emit("pipeline_health.done")
        emit("temporal_windows.start")
        temporal_windows = self.temporal_observation_windows(
            snapshot,
            target_symbols=selected_symbols,
        )
        emit("temporal_windows.done", symbolCount=len(temporal_windows))
        # Model scoring runs after the factual ABox is complete. This lets all
        # six model families inspect the exact company, valuation, event,
        # cross-asset, price and flow facts that TypeDB will receive.
        statistical_signal_context = {
            "statisticalSignalPipeline": {
                "status": "pending-factual-abox",
            },
        } if self.statistical_signal_service else {}
        portfolio_lifecycle = {}
        if self.investment_domain_store and hasattr(self.investment_domain_store, "ontology_portfolio_lifecycle_context"):
            emit("portfolio_lifecycle.start")
            try:
                portfolio_lifecycle = self.investment_domain_store.ontology_portfolio_lifecycle_context(
                    "portfolio:" + str(snapshot.account_id or "default")
                )
            except Exception:  # noqa: BLE001 - lifecycle enrichment must not invalidate market inference.
                portfolio_lifecycle = {}
            emit("portfolio_lifecycle.done", status=str(portfolio_lifecycle.get("status") or "unavailable"))
        result = {
            "settings": dict(self.settings),
            "snapshotId": "abox-snapshot:" + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:16],
            "asOf": as_of,
            "activeTBox": active_tbox,
            "account": {
                **dict(account_context),
                "accountId": snapshot.account_id,
                "accountLabel": snapshot.account_label,
                "provider": snapshot.provider,
                "mode": snapshot.mode,
                "status": snapshot.status,
            },
            "metadata": metadata,
            # DecisionItem is an output projection, not a new observation.
            # The native ABox uses the aligned InferenceBox for prior
            # reasoning context and keeps this input empty to avoid feedback.
            "decisionItems": [],
            "decisionEpisodes": decision_episodes,
            "decisionEpisodeProjection": dict(decision_memory.get("projection") or {}),
            "decisionPerformance": decision_performance,
            "hypothesisProposals": hypothesis_proposals,
            "hypothesisLifecycles": hypothesis_lifecycles,
            "hypothesisLifecycleAboxProjection": lifecycle_projection,
            # A live pipeline health row may change while a delayed retry is
            # rebuilding the same account snapshot. Only health captured with
            # the snapshot is causal ABox input; current worker telemetry is
            # exposed through operational monitoring instead.
            "dataPipelineHealth": data_pipeline_health,
            "temporalObservationWindows": temporal_windows,
            **statistical_signal_context,
            "portfolioLifecycle": portfolio_lifecycle,
        }
        # V1 and any replay engine must consume the same ontology-owned
        # context. Returning the unfiltered runtime settings here while only
        # storing the filtered replay packet made shadow parity impossible and
        # could let infrastructure wiring affect factual graph construction.
        frozen = frozen_projection_runtime_context(result)
        self.last_runtime_contexts[account_id] = frozen
        if cache_enabled:
            SHARED_PROJECTION_RUNTIME_CONTEXT_CACHE.put(
                cache_key,
                frozen,
                self.runtime_context_cache_max_entries(),
            )
        return deepcopy(frozen)

    @staticmethod
    def factual_runtime_metadata(
        metadata: Dict[str, object] = None,
        target_symbols=None,
        settings: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Keep historical market facts while removing derived decision output.

        Trend and change concepts still need the prior positions/watchlist
        snapshots. Their embedded decisions, AI context, and prior ontology
        output are rendered results, however, so carrying them into the next
        ABox would create a self-triggering inference loop.
        """
        source = dict(metadata or {})
        selected_symbols = {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        }

        def bounded_transition_rows(key: str, value: object) -> object:
            if key not in {MARKET_SIGNAL_TRANSITION_STATE_KEY, MARKET_SIGNAL_TRANSITION_RESULTS_KEY}:
                return deepcopy(value)
            if not isinstance(value, dict) or not selected_symbols:
                return deepcopy(value)
            return {
                str(symbol): deepcopy(payload)
                for symbol, payload in value.items()
                if str(symbol or "").upper().strip() in selected_symbols
            }

        values = {}
        for key, value in source.items():
            if key in {"ontology", "hypothesisLifecycle", "reasoningSnapshotReplay", "previousMonitorState", "previousState", "monitorStateHistory"}:
                continue
            values[key] = bounded_transition_rows(str(key), value)
        # This marker describes how the worker acquired the snapshot. It is
        # operational replay provenance, not a market fact for the ABox.
        def factual_state(state: object) -> object:
            if not isinstance(state, dict):
                return state
            result = {
                key: deepcopy(value)
                for key, value in state.items()
                if key not in {"decisions", "externalSignals"}
            }
            signals = state.get("externalSignals")
            if isinstance(signals, dict):
                result["externalSignals"] = compact_external_signals_for_ontology(
                    signals,
                    target_symbols=target_symbols,
                    settings=settings,
                )
            nested = result.get("metadata")
            if isinstance(nested, dict):
                nested = {
                    key: bounded_transition_rows(str(key), value)
                    for key, value in nested.items()
                }
                nested.pop("ontology", None)
                nested.pop("hypothesisLifecycle", None)
                nested.pop("reasoningSnapshotReplay", None)
                nested.pop("previousMonitorState", None)
                nested.pop("previousState", None)
                nested.pop("monitorStateHistory", None)
                result["metadata"] = nested
            return result

        if "previousMonitorState" in source:
            values["previousMonitorState"] = factual_state(source.get("previousMonitorState"))
        if isinstance(source.get("previousState"), dict):
            values["previousState"] = factual_state(source.get("previousState"))
        if isinstance(source.get("monitorStateHistory"), list):
            values["monitorStateHistory"] = [
                factual_state(item)
                for item in source.get("monitorStateHistory") or []
                if isinstance(item, dict)
            ]
        return values

    def temporal_observation_windows(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> Dict[str, object]:
        if not self.market_time_series_store or not hasattr(self.market_time_series_store, "load_temporal_windows"):
            return {}
        symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
        }
        requested = {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        }
        if requested:
            symbols.intersection_update(requested)
        if not symbols:
            return {}
        definitions = parse_temporal_windows(self.settings.get("temporalWindowPeriods"))
        try:
            return self.market_time_series_store.load_temporal_windows(
                snapshot.account_id,
                symbols,
                definitions,
                as_of=str(snapshot.generated_at or ""),
            )
        except Exception:  # noqa: BLE001 - short snapshot history remains a valid compatibility fallback.
            return {}

    def performance_setting(self, key: str, fallback: float) -> float:
        try:
            return float(str(self.settings.get(key) or fallback))
        except (TypeError, ValueError):
            return float(fallback)

    def data_pipeline_health_context(self, snapshot: AccountSnapshot = None) -> Dict[str, object]:
        """Return only health that belongs to the snapshot being reasoned.

        The current pipeline read model is operational telemetry, not a market
        fact observed at an older snapshot. Feeding it into a retry made one
        frozen account snapshot alternately gain and lose missing-data facts.
        A future collector can persist ``dataPipelineHealth`` in snapshot
        metadata; until then, per-position source timestamps remain the
        investment freshness contract.
        """
        metadata = dict(getattr(snapshot, "metadata", {}) or {}) if snapshot else {}
        payload = metadata.get("dataPipelineHealth")
        return dict(payload or {}) if isinstance(payload, dict) else {}

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
        """Load a bounded, current-subject decision-memory slice for the ABox.

        The decision repository is the complete audit record. Realtime TypeDB
        projection only needs recent episode links and outcomes for subjects in
        the current snapshot. Keeping those two concerns separate prevents an
        old AI/research payload from expanding every live inference graph.
        """
        projection = {
            "mode": "bounded-current-subject-memory",
            "sourceEpisodeCount": 0,
            "includedEpisodeCount": 0,
            "droppedEpisodeCount": 0,
            "targetSymbolCount": 0,
            "perSymbolLimit": self.decision_episode_context_per_symbol_limit(),
            "maximumEpisodeCount": self.decision_episode_context_maximum_episodes(),
            "outcomeObservation": {},
        }
        if not self.decision_episode_store:
            projection["status"] = "unavailable"
            return {"episodes": [], "projection": projection}
        try:
            observation = self.outcome_observation_service.observe_snapshot(snapshot)
            snapshot.metadata.setdefault("investmentBrain", {})["outcomeObservation"] = observation
            projection["outcomeObservation"] = dict(observation or {})
        except Exception as error:  # noqa: BLE001 - feedback memory must not block ABox projection.
            observation = {
                "status": "error",
                "reason": str(error)[:180],
            }
            snapshot.metadata.setdefault("investmentBrain", {})["outcomeObservation"] = observation
            projection["outcomeObservation"] = observation
        symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        })
        projection["targetSymbolCount"] = len(symbols)
        per_symbol_limit = int(projection["perSymbolLimit"] or 1)
        maximum_episode_count = int(projection["maximumEpisodeCount"] or 1)
        try:
            if symbols and hasattr(self.decision_episode_store, "list_for_symbols"):
                source_episodes = self.decision_episode_store.list_for_symbols(
                    symbols,
                    account_id=snapshot.account_id,
                    limit_per_symbol=per_symbol_limit,
                )
            elif symbols:
                source_episodes = []
                for symbol in symbols:
                    source_episodes.extend(
                        self.decision_episode_store.list(
                            snapshot.account_id,
                            symbol=symbol,
                            limit=per_symbol_limit,
                        )
                    )
            else:
                source_episodes = self.decision_episode_store.list(
                    snapshot.account_id,
                    limit=maximum_episode_count,
                )
        except Exception:  # noqa: BLE001 - projection remains valid without historical memory.
            projection["status"] = "unavailable"
            return {"episodes": [], "projection": projection}
        source_by_id = {}
        for item in source_episodes or []:
            episode_id = str(getattr(item, "episode_id", "") or "").strip()
            symbol = str(getattr(item, "symbol", "") or "").upper().strip()
            if not episode_id or (symbols and symbol not in symbols):
                continue
            source_by_id[episode_id] = item
        ordered = sorted(
            source_by_id.values(),
            key=lambda item: (
                str(getattr(item, "decided_at", "") or ""),
                str(getattr(item, "episode_id", "") or ""),
            ),
            reverse=True,
        )
        projection["sourceEpisodeCount"] = len(ordered)
        selected_episodes = ordered[:maximum_episode_count]
        rows = [
            decision_episode_ontology_context(
                item,
                maximum_hypotheses=self.decision_episode_context_hypothesis_limit(),
                maximum_outcomes=self.decision_episode_context_outcome_limit(),
            )
            for item in selected_episodes
        ]
        rows = [item for item in rows if item]
        if rows and self.investment_domain_store:
            try:
                feedback = self.investment_domain_store.lifecycle_feedback_for_decisions(
                    item.get("episodeId") for item in rows
                )
                for item in rows:
                    item.update(dict(feedback.get(str(item.get("episodeId") or "")) or {}))
            except Exception:
                pass
        projection["includedEpisodeCount"] = len(rows)
        projection["droppedEpisodeCount"] = max(0, len(ordered) - len(rows))
        projection["status"] = "ok"
        return {"episodes": rows, "projection": projection}

    def decision_episode_context_per_symbol_limit(self) -> int:
        return self.integer_setting("ontologyDecisionEpisodeContextPerSymbolLimit", 3, 1, 12)

    def decision_episode_context_maximum_episodes(self) -> int:
        return self.integer_setting("ontologyDecisionEpisodeContextMaxEpisodes", 24, 1, 60)

    def decision_episode_context_hypothesis_limit(self) -> int:
        return self.integer_setting("ontologyDecisionEpisodeContextHypothesisLimit", 3, 1, 8)

    def decision_episode_context_outcome_limit(self) -> int:
        return self.integer_setting("ontologyDecisionEpisodeContextOutcomeLimit", 8, 1, 16)

    def integer_setting(self, key: str, fallback: int, minimum: int, maximum: int) -> int:
        try:
            value = int(float(str(self.settings.get(key) or fallback)))
        except (TypeError, ValueError):
            value = fallback
        return max(minimum, min(maximum, value))

    def hypothesis_proposal_context(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> List[Dict[str, object]]:
        if not self.hypothesis_proposal_store or not hasattr(self.hypothesis_proposal_store, "list_hypothesis_proposals"):
            return []
        symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip()
        }
        requested = {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        }
        if requested:
            symbols.intersection_update(requested)
        try:
            rows = self.hypothesis_proposal_store.list_hypothesis_proposals("", "", 200)
        except Exception:  # noqa: BLE001 - proposal memory must not block ABox projection.
            return []
        return [
            dict(item)
            for item in rows or []
            if isinstance(item, dict)
            and str(item.get("accountId") or "") == str(snapshot.account_id or "")
            and str(item.get("symbol") or "").upper().strip() in symbols
        ]

    def hypothesis_lifecycle_context(
        self,
        snapshot: AccountSnapshot,
        target_symbols=None,
    ) -> List[Dict[str, object]]:
        if not self.hypothesis_lifecycle_store:
            return []
        symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
        }
        requested = {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        }
        if requested:
            symbols.intersection_update(requested)
        if not symbols:
            return []
        try:
            if hasattr(self.hypothesis_lifecycle_store, "current_summary_for_subjects"):
                try:
                    records = self.hypothesis_lifecycle_store.current_summary_for_subjects(
                        snapshot.account_id,
                        symbols,
                        lifecycle_key_prefix=HYPOTHESIS_LIFECYCLE_KEY_PREFIX,
                    )
                except TypeError:
                    records = self.hypothesis_lifecycle_store.current_summary_for_subjects(snapshot.account_id, symbols)
            elif hasattr(self.hypothesis_lifecycle_store, "current_for_subjects"):
                try:
                    records = self.hypothesis_lifecycle_store.current_for_subjects(
                        snapshot.account_id,
                        symbols,
                        lifecycle_key_prefix=HYPOTHESIS_LIFECYCLE_KEY_PREFIX,
                    )
                except TypeError:
                    records = self.hypothesis_lifecycle_store.current_for_subjects(snapshot.account_id, symbols)
            else:
                return []
        except Exception:  # noqa: BLE001 - lifecycle audit must not block a factual ABox projection.
            return []
        return [
            item.to_dict()
            for item in (records or {}).values()
            if hasattr(item, "to_dict")
        ]

    def hypothesis_lifecycle_abox_projection_enabled(self) -> bool:
        """Keep audit history out of realtime TypeDB input unless explicitly needed.

        Lifecycle records explain a completed generation; they are not source
        facts or native-rule inputs. Their compact prompt summary is attached
        after a verified generation by ``HypothesisLifecycleService``.
        """

        value = self.settings.get("ontologyHypothesisLifecycleAboxProjectionEnabled")
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
