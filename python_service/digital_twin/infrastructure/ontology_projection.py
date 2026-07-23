from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Dict, List, Set
import hashlib
import json
import time

from ..application.investment_outcome_observation_service import InvestmentOutcomeObservationService
from ..domain.ontology_contracts import PortfolioOntology
from ..domain.decision_performance import evaluate_decision_performance
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_governance import rulebox_rules_hash
from ..domain.ontology_change_impact import (
    build_inference_impact_plan,
    compact_inference_impact_plan,
)
from ..domain.ontology_projection_fingerprint import (
    active_material_fingerprint,
    apply_material_graph_identity,
    material_graph_fingerprint,
    stable_value,
)
from ..domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
    apply_scoped_manifest_plan,
    apply_scoped_abox_identity,
    merge_target_scoped_abox_manifest,
    select_target_scoped_manifest_patch,
    scoped_manifest_id,
)
from ..domain.ontology_worlds import market_world, world_from_snapshot, world_metadata
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
    complete_ontology_projection_run,
    inference_reuse_scope_plan,
    inference_reuse_scope_plan_for_targets,
    inference_reuse_scope_plan_fingerprint,
    projection_source_snapshot,
    projection_run_from_payload,
)
from ..domain.ontology_runtime_operations import build_projection_runtime_observation
from ..domain.ontology_validator import validate_ontology
from ..domain.portfolio_ontology_builder import build_portfolio_ontology
from ..domain.portfolio_ontology_coverage import CATEGORY_RELATIONS
from ..domain.ontology_native_rule_planning import (
    native_rule_planner_manifest_fingerprint,
    native_rule_planner_topology,
)
from ..domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
from ..domain.portfolio import AccountSnapshot
from .graph_store_rulebox import rulebox_rules_to_payload


DEPRECATED_TYPEDB_RULE_IDS = {"shadow.market_psychology.state.v1"}

# These edges preserve the factual shape needed to inspect and extend native
# TypeDB reasoning even when the active catalog currently reads aggregate
# window properties only.
ABOX_STRUCTURAL_RELATION_TYPES = {
    "WINDOW_CONTAINS_OBSERVATION",
    "PRECEDES",
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
            }

    def put(
        self,
        key: str,
        graph: PortfolioOntology,
        persistence_graph: PortfolioOntology,
        max_entries: int,
    ) -> None:
        if not key or max_entries <= 0:
            return
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = {
                "createdMonotonic": time.monotonic(),
                "graph": deepcopy(graph),
                "persistenceGraph": deepcopy(persistence_graph),
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
SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR = SharedOntologyQualityRecordCoordinator()


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


def migrate_typedb_rule_catalog(
    stored_rules: List[Dict[str, object]],
    bootstrap_rules: List[Dict[str, object]],
) -> Dict[str, object]:
    """Remove retired rules and add explicit policy only to known bootstrap rules."""
    defaults_by_id = {rule_id_from_payload(item): item for item in bootstrap_rules or [] if isinstance(item, dict)}
    migrated = []
    removed = []
    updated = []
    for raw_rule in stored_rules or []:
        if not isinstance(raw_rule, dict):
            continue
        rule_id = rule_id_from_payload(raw_rule)
        if rule_id in DEPRECATED_TYPEDB_RULE_IDS:
            removed.append(rule_id)
            continue
        rule = deepcopy(raw_rule)
        default_rule = defaults_by_id.get(rule_id) or {}
        default_derivations = default_rule.get("derivations") or []
        changed = False
        for index, derivation in enumerate(rule.get("derivations") or []):
            if not isinstance(derivation, dict) or derivation.get("decision_stage") or derivation.get("decisionStage"):
                continue
            default_derivation = default_derivations[index] if index < len(default_derivations) else {}
            stage = str((default_derivation or {}).get("decision_stage") or (default_derivation or {}).get("decisionStage") or "").strip()
            if stage:
                derivation["decision_stage"] = stage
                changed = True
        if changed:
            updated.append(rule_id)
        migrated.append(rule)
    return {
        "changed": bool(removed or updated),
        "rules": migrated,
        "removedRuleIds": sorted(set(removed)),
        "decisionPolicyUpdatedRuleIds": sorted(set(updated)),
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
        outcome_observation_service=None,
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
        self.settings = dict(settings or {})
        self.outcome_observation_service = outcome_observation_service or InvestmentOutcomeObservationService(
            decision_episode_store=decision_episode_store,
            market_time_series_store=market_time_series_store,
            settings=self.settings,
        )
        self.source = source or "monitoring"

    def record_snapshot(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
    ) -> Dict[str, object]:
        projection_started = time.perf_counter()
        runtime_stages: Dict[str, int] = {}
        projection_run = None
        pending_activation_recovery: Dict[str, object] = {}
        portfolio_world_context = world_from_snapshot(snapshot, self.settings)
        market_world_context = market_world(
            portfolio_world_context.market_id,
            self.settings.get("ontologySharedMarketTenantId") or "shared",
        )
        if not self.repository:
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
            return result
        if self.typedb_projection_deferred():
            result = {
                "saved": False,
                "status": "deferred-to-reasoning-worker",
                "reason": "TypeDB ABox와 InferenceBox는 전용 온톨로지 추론 워커가 같은 주기에서 생성합니다.",
                "preservedActiveGeneration": True,
                "singleWriter": True,
                "ontologyWorld": world_metadata(portfolio_world_context),
            }
            self.store_projection_result(snapshot, result)
            return result
        pending_activation_recovery = self.recover_pending_abox_activation(portfolio_world_context.world_id)
        recovery_status = str(pending_activation_recovery.get("status") or "skipped")
        if recovery_status not in {
            "skipped",
            "disabled",
            "finalized",
            "finalized-empty-target",
            "restored",
            "cleared-stale",
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
            return result
        # A staged or targetless legacy activation has no bounded InferenceBox
        # proof to reconcile yet. The latter is finalized as control-only
        # repair, then this cycle stages the current manifest. Avoid reading
        # historical InferenceBox rows before that bounded retry begins.
        if recovery_status not in {"retry-required", "staged", "finalized-empty-target"}:
            self.reconcile_interrupted_projection_audit(portfolio_world_context.world_id)
        try:
            rulebox_bootstrap = self.ensure_rulebox_ready()
            if str(rulebox_bootstrap.get("status") or "") not in {"ready", "seeded"}:
                result = {
                    "saved": False,
                    "status": "typedb-rule-catalog-not-ready",
                    "reason": str(rulebox_bootstrap.get("reason") or "TypeDB 추론 규칙을 사용할 수 없습니다."),
                    "preservedActiveGeneration": True,
                    "ruleCatalog": rulebox_bootstrap,
                }
                self.store_projection_result(snapshot, result, projection_run)
                return result
            graph_build_started = time.perf_counter()
            graph, persistence_graph, graph_assembly = self.build_graph_assembly(
                snapshot,
                rulebox_bootstrap,
            )
            runtime_stages.update(dict(graph_assembly.get("runtimeStages") or {}))
            runtime_stages["graphAssemblyCacheHit"] = (
                1 if str(graph_assembly.get("status") or "") == "hit" else 0
            )
            if graph_assembly.get("ageMs") is not None:
                runtime_stages["graphAssemblyCacheAgeMs"] = int(graph_assembly.get("ageMs") or 0)
            # This is a structural index from the exact graph being persisted.
            # It only bounds TypeDB function scheduling; TypeDB still evaluates
            # all selected rule conditions and materializes the result.
            planner_topology = native_rule_planner_topology(persistence_graph)
            persistence_graph.worldview["nativeRulePlannerTopology"] = planner_topology
            graph.worldview.update({
                **world_metadata(portfolio_world_context),
                "marketWorldId": market_world_context.world_id,
                "marketContextMode": "shared-market-world-with-portfolio-rule-mirror",
            })
            persistence_graph.worldview.update({
                **world_metadata(portfolio_world_context),
                "marketWorldId": market_world_context.world_id,
                "marketContextMode": "shared-market-world-with-portfolio-rule-mirror",
            })
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
            # The full in-memory graph remains the validation and AI context,
            # while persistence gets independent immutable generations for
            # each owning scope.  The manifest id remains the compatibility
            # ABox generation id used by InferenceBox alignment and audits.
            scoped_identity = apply_scoped_abox_identity(
                persistence_graph,
                snapshot.account_id,
                world_id=portfolio_world_context.world_id,
                tenant_id=portfolio_world_context.tenant_id,
                world_type=portfolio_world_context.world_type,
            )
            material_snapshot_id = str(scoped_identity.get("manifestId") or material_snapshot_id)
            runtime_stages["graphBuildMs"] = int((time.perf_counter() - graph_build_started) * 1000)
            validation_started = time.perf_counter()
            validation = validate_ontology(persistence_graph)
            runtime_stages["aboxValidationMs"] = int((time.perf_counter() - validation_started) * 1000)
            if validation.error_count:
                result = {
                    "saved": False,
                    "status": "invalid-abox",
                    "reason": "ABox validation failed before graph-store persistence.",
                    "graphStore": self.active_graph_store_key(),
                    "aboxValidation": validation.to_dict(),
                }
                self.store_projection_result(snapshot, result, projection_run)
                return result
            active_abox = self.active_abox_metadata(portfolio_world_context.world_id)
            evidence_index_upgrade = {}
            active_abox_complete = str(active_abox.get("status") or "ok") == "ok"
            active_abox_is_scoped_manifest = (
                str(active_abox.get("scopedAboxManifestVersion") or "")
                == SCOPED_ABOX_MANIFEST_VERSION
            )
            target_scoped_patch = self.target_scoped_patch_targets(
                snapshot,
                active_abox,
                scoped_identity,
                target_symbols,
            )
            if target_scoped_patch.get("eligible"):
                target_patch_started = time.perf_counter()
                applied_target_patch = merge_target_scoped_abox_manifest(
                    persistence_graph,
                    active_abox,
                    target_scoped_patch.get("targetSymbols") or [],
                )
                runtime_stages["targetScopedManifestPatchMs"] = int(
                    (time.perf_counter() - target_patch_started) * 1000
                )
                if applied_target_patch.get("applied"):
                    # The source graph can contain newer observations for
                    # deferred symbols. The persisted identity must describe
                    # the merged active manifest, not facts intentionally held
                    # for their own target cycle or the periodic reconciliation.
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
                    target_scoped_patch = {
                        "status": "applied",
                        "mode": "incremental-target-scoped-manifest-patch",
                        "targetSymbols": list(applied_target_patch.get("targetSymbols") or []),
                        "selectedIncomingScopeCount": len(
                            applied_target_patch.get("selectedIncomingScopeIds") or []
                        ),
                        "reusedActiveScopeCount": len(
                            applied_target_patch.get("reusedActiveScopeIds") or []
                        ),
                        "deferredScopeCount": len(
                            applied_target_patch.get("deferredScopeIds") or []
                        ),
                        "fullReconcileMinutes": self.scoped_full_reconcile_minutes(),
                    }
                    persistence_graph.worldview["targetScopedManifestPatch"] = dict(target_scoped_patch)
                else:
                    target_scoped_patch = {
                        "status": str(applied_target_patch.get("status") or "skipped"),
                        "mode": "full-manifest-fallback",
                        "targetSymbols": list(target_scoped_patch.get("targetSymbols") or []),
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
            impact_planning_started = time.perf_counter()
            inference_impact_plan = self.inference_impact_plan(
                snapshot,
                active_abox,
                scoped_identity,
                target_symbols,
            )
            compact_impact_plan = compact_inference_impact_plan(inference_impact_plan)
            inference_symbols = self.inference_symbols(
                snapshot,
                inference_impact_plan.get("inferenceTargetSymbols") or target_symbols,
            )
            inference_symbols = self.bounded_native_inference_symbols(
                snapshot,
                inference_symbols,
                target_symbols,
            )
            runtime_stages["impactPlanningMs"] = int((time.perf_counter() - impact_planning_started) * 1000)
            persistence_graph.worldview["scopeDelta"] = dict(compact_impact_plan.get("scopeDelta") or {})
            persistence_graph.worldview["inferenceImpactPlan"] = compact_impact_plan
            projection_scope = {
                "triggerMode": "scope-change-impact-native",
                "targetSymbols": list(inference_symbols),
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
                "inferenceImpactPlan": compact_impact_plan,
                "reason": (
                    "변경된 사실군과 ABox 의존 관계에서 재평가 대상을 계산하고, 변경 범위만 새 세대로 기록한 뒤 "
                    "대상별 TypeDB 네이티브 규칙을 완전 평가합니다."
                ),
            }
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
                    "inferenceImpactPlan": compact_impact_plan,
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
            projection_run, audit_error = self.begin_projection_audit_run(
                snapshot,
                persistence_graph,
                material_fingerprint,
                material_snapshot_id,
                inference_symbols=inference_symbols,
                rulebox_metadata=rulebox_bootstrap,
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
            abox_persistence_started = time.perf_counter()
            result = self.repository.save_graph(persistence_graph)
            runtime_stages["aboxPersistenceMs"] = int((time.perf_counter() - abox_persistence_started) * 1000)
            if not isinstance(result, dict):
                result = {"saved": False, "status": "error", "reason": "ontology repository returned non-dict result"}
            result["projectionMode"] = "abox-facts-only-typedb-native-rules"
            result["materialFingerprint"] = material_fingerprint
            result["aboxSnapshotId"] = material_snapshot_id
            result["nativeRulePlannerTopology"] = dict(
                persistence_graph.worldview.get("nativeRulePlannerTopology") or {}
            )
            result["materialChangeDetected"] = True
            result["projectionScope"] = projection_scope
            result["inferenceImpactPlan"] = compact_impact_plan
            result["aboxValidation"] = validation.to_dict()
            result["runtimeStages"] = runtime_stages
            result["ontologyWorld"] = world_metadata(portfolio_world_context)
            if rulebox_bootstrap:
                result["ruleboxBootstrap"] = rulebox_bootstrap
            if pending_activation_recovery:
                result["pendingAboxActivationRecovery"] = pending_activation_recovery
            if result.get("saved") or str(result.get("status") or "") in {
                "staged-scoped-manifest",
                "deferred-pending-scoped-manifest",
            }:
                pending = result.get("pendingAboxActivation") if isinstance(result.get("pendingAboxActivation"), dict) else {}
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
            market_projection_started = time.perf_counter()
            if bool(result.get("saved")):
                # MarketWorld is an account-independent derived mirror.  It
                # is intentionally scheduled only after the portfolio ABox
                # and its decision-critical TypeDB inference are verified.
                # This keeps a slow shared write out of the alert path while
                # never letting an unverified account projection publish
                # facts to the shared world.
                result["marketWorld"] = self.schedule_market_world_projection(
                    persistence_graph,
                    market_world_context,
                )
            else:
                result["marketWorld"] = {
                    **world_metadata(market_world_context),
                    "status": "deferred-portfolio-inference-not-verified",
                    "preservedActiveGeneration": True,
                    "reason": "계좌 ABox 또는 TypeDB 추론이 확정되지 않아 공용 시장 읽기 모델 갱신을 건너뛰었습니다.",
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
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
        runtime_stages["totalMs"] = int((time.perf_counter() - projection_started) * 1000)
        result.setdefault("runtimeStages", runtime_stages)
        self.store_projection_result(snapshot, result, projection_run)
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

    def recover_pending_abox_activation(self, world_id: str = "") -> Dict[str, object]:
        if self.active_graph_store_key() != "typedb":
            return {"status": "skipped", "reason": "Active graph store is not TypeDB."}
        recovery = getattr(self.repository, "recover_pending_abox_activation", None)
        if not callable(recovery):
            return {"status": "skipped", "reason": "Graph store has no pending ABox activation journal."}
        try:
            result = self.repository_world_call("recover_pending_abox_activation", world_id=world_id)
        except Exception as error:  # noqa: BLE001 - do not replace a potentially recoverable active generation.
            return {"status": "error", "reason": str(error)[:180]}
        return dict(result or {}) if isinstance(result, dict) else {
            "status": "error",
            "reason": "Graph store returned an invalid ABox activation recovery result.",
        }

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
        if not row:
            return {"status": "skipped", "reason": "No interrupted projection audit matches the active ABox."}
        run = projection_run_from_payload(row)
        if not run.run_id or run.abox_snapshot_id != active_snapshot_id:
            return {"status": "skipped", "reason": "Interrupted audit ABox identity does not match the active ABox."}
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
        result = {
            "saved": True,
            "status": "ok",
            "reason": "TypeDB ABox와 InferenceBox 정합성을 확인해 중단된 투영 감사를 복구했습니다.",
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
                "matchedRuleCount": int(inferencebox.get("traceCount") or 0),
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
        }
        try:
            completed = complete_ontology_projection_run(run, result)
            self.projection_run_store.complete(completed)
        except Exception as error:  # noqa: BLE001 - a later cycle can prove and retry the same row.
            return {"status": "error", "reason": str(error)[:180], "runId": run.run_id}
        return {
            "status": "recovered",
            "runId": run.run_id,
            "aboxSnapshotId": active_snapshot_id,
            "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
        }

    def ensure_rulebox_ready(self) -> Dict[str, object]:
        self._rulebox_impact_rules = None
        if not hasattr(self.repository, "rulebox_snapshot"):
            return {}
        expected_rules = rulebox_rules_to_payload(default_graph_inference_rules())
        expected_hash = rulebox_rules_hash(expected_rules)
        expected_count = len(expected_rules)
        try:
            snapshot = self.repository.rulebox_snapshot()
        except Exception as error:  # noqa: BLE001 - projection will still expose the persistence error later.
            return {"status": "error", "reason": str(error)[:180]}
        if not isinstance(snapshot, dict):
            return {"status": "invalid", "reason": "RuleBox snapshot returned non-dict result."}
        self._rulebox_impact_rules = [
            dict(item) for item in snapshot.get("rules") or []
            if isinstance(item, dict)
        ]
        if not snapshot.get("configured"):
            return {
                "status": "disabled",
                "reason": str(snapshot.get("reason") or "Ontology graph storage is not configured."),
            }
        migration = self.migrate_typedb_rule_catalog(snapshot, expected_rules)
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
        stored_count = int(snapshot.get("ruleboxRuleCount") or snapshot.get("ruleCount") or 0)
        stored_hash = str(snapshot.get("ruleboxRulesHash") or snapshot.get("rulesHash") or "").strip()
        if not stored_hash and isinstance(snapshot.get("rules"), list) and snapshot.get("rules"):
            stored_hash = rulebox_rules_hash(snapshot.get("rules") or [])
        missing_decision_policy = rulebox_rules_missing_decision_stage(snapshot.get("rules") or [])
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
                "sourceOfTruth": "typedb-schema-function-rules",
                "ruleCatalogStore": "typedb",
                "inputRelationTypes": rulebox_input_relation_types(snapshot.get("rules") or []),
                "bootstrapRuleCount": expected_count,
                "bootstrapRulesHash": expected_hash,
                "codeDefaultHashMismatch": bool(stored_hash and stored_hash != expected_hash),
                "ruleCatalogMigration": migration,
            }
            if result["codeDefaultHashMismatch"]:
                result["reason"] = (
                    "TypeDB 추론 규칙이 운영 기준입니다. 코드 기본값은 빈 저장소를 시작할 때만 사용하며 "
                    "저장된 규칙을 덮어쓰지 않습니다."
                )
            return result
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
            "sourceOfTruth": "typedb-schema-function-rules",
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
                "changeReason": "TypeDB 단일 추론 경로 전환: 폐기 규칙 제거 및 판단 단계 명시",
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
        while True:
            additions = [
                item
                for item in structural_relations
                if item not in relations
                and (item.source in persisted_endpoint_ids or item.target in persisted_endpoint_ids)
            ]
            if not additions:
                break
            relations.extend(additions)
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
        payload = {
            "version": "portfolio-graph-assembly-cache-v1",
            "namespace": self.graph_assembly_cache_namespace(),
            "sourceSnapshot": stable_value(source_snapshot),
            "settings": stable_value(self.settings),
            "activeTBox": stable_value(active_tbox),
            "ruleboxRulesHash": str((rule_catalog or {}).get("ruleboxRulesHash") or ""),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def build_graph_assembly(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
    ) -> tuple:
        """Build or safely clone the immutable pre-identity ABox graph pair."""
        stage_timings: Dict[str, int] = {}
        active_tbox_started = time.perf_counter()
        active_tbox = self.active_tbox_context()
        stage_timings["activeTBoxReadMs"] = int((time.perf_counter() - active_tbox_started) * 1000)
        cache_enabled = self.graph_assembly_cache_enabled()
        cache_key = self.graph_assembly_cache_key(snapshot, rule_catalog, active_tbox) if cache_enabled else ""
        cache_read_started = time.perf_counter()
        cache_result = SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.get(
            cache_key,
            self.graph_assembly_cache_ttl_seconds(),
        ) if cache_enabled else {"status": "disabled"}
        stage_timings["graphAssemblyCacheReadMs"] = int(
            (time.perf_counter() - cache_read_started) * 1000
        )
        if str(cache_result.get("status") or "") == "hit":
            return (
                cache_result["graph"],
                cache_result["persistenceGraph"],
                {
                    "status": "hit",
                    "ageMs": int(cache_result.get("ageMs") or 0),
                    "runtimeStages": stage_timings,
                },
            )

        runtime_context_started = time.perf_counter()
        runtime_context = self.runtime_context(snapshot, active_tbox=active_tbox)
        stage_timings["runtimeContextMs"] = int((time.perf_counter() - runtime_context_started) * 1000)
        assembly_started = time.perf_counter()
        graph = build_portfolio_ontology(
            list(snapshot.positions or []) + list(snapshot.watchlist or []),
            snapshot.portfolio,
            # Current decisions are derived from a preceding TypeDB/AI
            # pass. Native rules must start from observed portfolio and
            # market facts, not use their own previous output as evidence.
            legacy_by_symbol={},
            external_signals=snapshot.external_signals,
            portfolio_id=snapshot.account_id,
            runtime_context=runtime_context,
            # The realtime path persists only ABox facts. Static TBox
            # vocabulary is seeded independently and presentation output is
            # rebuilt later from the active InferenceBox for an alert or UI.
            include_tbox=False,
            include_presentation=False,
            include_derived_decision_items=False,
        )
        persistence_graph = self.graph_for_graph_store_persistence(graph, rule_catalog)
        stage_timings["ontologyGraphAssemblyMs"] = int((time.perf_counter() - assembly_started) * 1000)
        if cache_enabled:
            SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.put(
                cache_key,
                graph,
                persistence_graph,
                self.graph_assembly_cache_max_entries(),
            )
        return graph, persistence_graph, {
            "status": "miss" if cache_enabled else "disabled",
            "runtimeStages": stage_timings,
        }

    def async_quality_record_enabled(self) -> bool:
        value = self.settings.get("ontologyAsyncQualityRecordEnabled")
        if value is None:
            # Existing focused tests rely on a synchronously visible sample;
            # runtime_settings() explicitly opts into the non-blocking path.
            return False
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def graph_for_typedb_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
        return self.graph_for_graph_store_persistence(graph)

    def shared_market_world_retention_hours(self) -> float:
        try:
            value = float(str(self.settings.get("ontologySharedMarketWorldRetentionHours") or "72"))
        except (TypeError, ValueError):
            value = 72.0
        return max(1.0, min(24.0 * 90.0, value))

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

    def schedule_market_world_projection(self, portfolio_graph: PortfolioOntology, shared_world) -> Dict[str, object]:
        """Project the shared market mirror without delaying a verified alert path."""
        if self.active_graph_store_key() != "typedb" or not self.shared_market_world_async_projection_enabled():
            return self.project_market_world(portfolio_graph, shared_world)
        return SHARED_MARKET_WORLD_PROJECTION_COORDINATOR.enqueue(
            self,
            portfolio_graph,
            shared_world,
        )

    def project_market_world(self, portfolio_graph: PortfolioOntology, shared_world) -> Dict[str, object]:
        """Persist the account-independent market slice without blocking a portfolio run.

        Market observations can be supplied by any account cycle.  We merge the
        changed instrument slice into the current shared world, then use the
        same immutable scoped-ABox lifecycle as a PortfolioWorld.  MarketWorld
        has no account-specific RuleBox output, so its verified manifest can be
        activated directly after persistence.
        """
        if self.active_graph_store_key() != "typedb":
            return {
                **world_metadata(shared_world),
                "status": "skipped-non-typedb-store",
                "reason": "Shared MarketWorld is enabled on the TypeDB ontology adapter.",
            }
        metadata_reader = getattr(self.repository, "active_abox_metadata", None)
        scoped_saver = getattr(self.repository, "save_scoped_abox_graph", None)
        if not callable(metadata_reader) or not callable(scoped_saver):
            return {
                **world_metadata(shared_world),
                "status": "deferred-adapter-not-scoped-market-world",
                "reason": "The active graph adapter cannot update a shared MarketWorld through scoped Manifests yet.",
            }
        merge_lease: Dict[str, object] = {}
        acquire_lease = getattr(self.repository, "acquire_scoped_abox_write_lease", None)
        release_lease = getattr(self.repository, "release_scoped_abox_write_lease", None)
        if callable(acquire_lease) and callable(release_lease) and callable(scoped_saver):
            try:
                merge_lease = self.repository_world_call(
                    "acquire_scoped_abox_write_lease",
                    "market-world-merge",
                    world_id=shared_world.world_id,
                )
            except Exception as error:  # noqa: BLE001 - the portfolio world must remain independently usable.
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-market-world-write-lease",
                    "reason": "Shared MarketWorld write lease lookup failed: " + str(error)[:180],
                }
            if not bool(merge_lease.get("acquired")):
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-market-world-write-lease",
                    "preservedActiveGeneration": True,
                    "reason": "Another account is merging or activating the shared MarketWorld.",
                    "writeLease": {
                        key: value
                        for key, value in dict(merge_lease or {}).items()
                        if key != "propertiesJson"
                    },
                }
        try:
            observed_at = str((portfolio_graph.worldview or {}).get("asOf") or "")
            update = build_market_world_graph(portfolio_graph, shared_world, observed_at=observed_at)
            try:
                active_market = self.repository_world_call(
                    "active_abox_metadata",
                    world_id=shared_world.world_id,
                )
            except Exception as error:  # noqa: BLE001 - a shared read must never hold a portfolio projection.
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-market-world-metadata",
                    "preservedActiveGeneration": True,
                    "reason": "Shared MarketWorld Manifest could not be read: " + str(error)[:180],
                }
            active_market = dict(active_market or {}) if isinstance(active_market, dict) else {}
            active_status = str(active_market.get("status") or "empty").strip().lower()
            if active_status not in {"ok", "empty"}:
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-market-world-metadata",
                    "preservedActiveGeneration": True,
                    "reason": str(
                        active_market.get("reason")
                        or "Shared MarketWorld Manifest is not complete."
                    )[:220],
                }
            if (
                active_status == "ok"
                and str(active_market.get("scopedAboxManifestVersion") or "") != SCOPED_ABOX_MANIFEST_VERSION
            ):
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-market-world-legacy-manifest",
                    "preservedActiveGeneration": True,
                    "reason": "Shared MarketWorld must be migrated to a scoped Manifest before incremental updates can preserve every active symbol.",
                }
            update.worldview.update({
                **world_metadata(shared_world),
                "marketWorldProjection": True,
                "marketContextMode": "shared-market-world-with-portfolio-rule-mirror",
            })
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
            market_target_patch = {
                "status": "full-manifest",
                "selectedIncomingScopeCount": len(incoming_scope_plan),
            }
            source_patch = dict((portfolio_graph.worldview or {}).get("targetScopedManifestPatch") or {})
            target_symbols = list(source_patch.get("targetSymbols") or [])
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
                active_market,
                incoming_scope_plan,
                observed_at=observed_at,
                retention_hours=self.shared_market_world_retention_hours(),
                max_symbols=self.shared_market_world_symbol_limit(),
            )
            scope_generations = dict(manifest_state.get("scopeGenerationIds") or {})
            if not scope_generations:
                return {
                    **world_metadata(shared_world),
                    "status": "skipped-empty-market-world-patch",
                    "reason": "No shareable market fact scope was produced by this portfolio observation.",
                }
            manifest_id = scoped_manifest_id(
                shared_world.world_id,
                scope_generations,
                world_id=shared_world.world_id,
            )
            fingerprint = str(manifest_state.get("materialFingerprint") or incoming_fingerprint)
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
                and active_material_fingerprint(active_market) == fingerprint
            ):
                observation_refresh = {}
                refreshed_scope_ids = list(manifest_state.get("observationRefreshedScopeIds") or [])
                refresher = getattr(self.repository, "refresh_market_world_observation_metadata", None)
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
                            "status": "deferred-market-world-observation-metadata",
                            "saved": False,
                            "preservedActiveGeneration": True,
                            "materialFingerprint": fingerprint,
                            "worldviewManifestId": str(active_market.get("aboxSnapshotId") or manifest_id),
                            "projectionMode": "incremental-scoped-manifest-reuse",
                            "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                            "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                            "observationRefreshedScopeIds": refreshed_scope_ids,
                            "observationMetadata": observation_refresh,
                            "targetScopedManifestPatch": market_target_patch,
                            "reason": "공용 시장 사실은 같지만 소스 관측 시각을 안전하게 갱신하지 못했습니다.",
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
                    "reason": "공용 시장 사실이 현재 활성 MarketWorld와 같아 저장과 활성화를 생략했습니다.",
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
                "marketWorldProjectionMode": "incremental-scoped-manifest-patch",
                "marketWorldActiveScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                "marketWorldActiveSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                "marketWorldRetiredScopeIds": list(manifest_state.get("retiredScopeIds") or []),
                "targetScopedManifestPatch": market_target_patch,
            })
            validation = validate_ontology(update)
            coverage = market_world_coverage(update)
            coverage.update({
                "coverageScope": "incoming-market-patch",
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
                    "status": "invalid-market-world",
                    "materialFingerprint": fingerprint,
                    "coverage": coverage,
                    "validation": validation.to_dict(),
                    "reason": "Shared market observations failed ontology validation.",
                }
            # The shared lease covers Manifest metadata read -> scope patch ->
            # stage -> activate. Existing scopes stay in the active Manifest,
            # so no account can erase another account's market observation.
            save_result = scoped_saver(update, adopted_write_lease=merge_lease) if merge_lease else scoped_saver(update)
            save_result = dict(save_result or {}) if isinstance(save_result, dict) else {
                "saved": False,
                "status": "invalid-save-result",
            }
            activation = {}
            if manifest_id and str(save_result.get("status") or "") in {
                "ok",
                "staged-scoped-manifest",
                "deferred-pending-scoped-manifest",
            }:
                activation = self.repository_world_call(
                    "activate_scoped_abox_manifest",
                    manifest_id,
                    pending_activation=False,
                    world_id=shared_world.world_id,
                )
            return {
                **world_metadata(shared_world),
                "status": (
                    "ok"
                    if str((activation or {}).get("status") or "") == "ok"
                    else str(save_result.get("status") or "error")
                ),
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
                "coverage": coverage,
                "validation": validation.to_dict(),
                "save": save_result,
                "activation": activation,
                "writeLease": {
                    key: value
                    for key, value in dict(merge_lease or {}).items()
                    if key != "propertiesJson"
                },
            }
        except Exception as error:  # noqa: BLE001 - market sharing must never suppress account reasoning.
            return {
                **world_metadata(shared_world),
                "status": "error",
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
        if compact_impact_plan:
            result.setdefault("inferenceImpactPlan", compact_impact_plan)
        active_key = self.active_graph_store_key(result)
        world_id = str(world_id or result.get("worldId") or ((result.get("ontologyWorld") or {}).get("worldId") if isinstance(result.get("ontologyWorld"), dict) else "") or "").strip()
        runtime_stages = result.setdefault("runtimeStages", {})
        selection_context = {
            "reusable": False,
            "matchedRuleIds": [],
            "matchedRuleCount": 0,
        }
        inference_write_lease: Dict[str, object] = {}
        if active_key == "typedb":
            inference_write_lease = self.acquire_inference_write_lease(result, world_id=world_id)
            if inference_write_lease.get("acquired") is False:
                lease_summary = {
                    key: value
                    for key, value in dict(inference_write_lease or {}).items()
                    if key != "propertiesJson"
                }
                result["inferenceWriteLease"] = lease_summary
                reason = "다른 ABox 활성화 또는 TypeDB 네이티브 추론 세대가 실행 중입니다."
                result["ruleboxExecution"] = {
                    "configured": True,
                    "status": "deferred-inference-write-lease",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "nativeTypeDbReasoningUsed": False,
                    "reason": reason,
                }
                result["inferenceBox"] = {
                    "configured": True,
                    "status": "deferred-inference-write-lease",
                    "graphStore": "typedb",
                    "source": "typedbInferenceBox",
                    "nativeTypeDbReasoningUsed": False,
                    "reason": reason,
                }
                result["aboxStaged"] = bool(result.get("saved"))
                result["saved"] = False
                result["status"] = "deferred-inference-write-lease"
                result["preservedActiveGeneration"] = True
                result["reason"] = reason
                return
            if inference_write_lease:
                result["inferenceWriteLease"] = {
                    key: value
                    for key, value in dict(inference_write_lease or {}).items()
                    if key != "propertiesJson"
                }
            if bool(compact_impact_plan.get("nativeRuleSelectionEligible")):
                # Read the old aligned InferenceBox only after owning the
                # writer lease. This makes the reuse proof and the following
                # ABox pointer transition one serialized operation.
                selection_started = time.perf_counter()
                selection_context = self.prior_rule_selection_context(
                    snapshot,
                    inference_symbols,
                    world_id=world_id,
                    candidate_scope_plan=candidate_scope_plan,
                    rulebox_rules_hash=rulebox_rules_hash,
                    tbox_fingerprint=tbox_fingerprint,
                )
                runtime_stages["priorInferenceReuseReadMs"] = int((time.perf_counter() - selection_started) * 1000)
                recomputed_impact_plan = selection_context.get("inferenceImpactPlan")
                if isinstance(recomputed_impact_plan, dict) and recomputed_impact_plan:
                    compact_impact_plan = compact_inference_impact_plan(recomputed_impact_plan)
                    result["inferenceImpactPlan"] = compact_impact_plan
                    projection_scope = result.get("projectionScope")
                    if isinstance(projection_scope, dict):
                        projection_scope["inferenceImpactPlan"] = compact_impact_plan
                result["priorInferenceReuse"] = {
                    key: value
                    for key, value in selection_context.items()
                    if key not in {"matchedRuleIds", "inferenceImpactPlan"}
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
                "nativeRulePlannerTopology": dict(
                    (result.get("nativeRulePlannerTopology") or {})
                    if isinstance(result.get("nativeRulePlannerTopology"), dict)
                    else {}
                ),
                "typedbNativeRuleSelectionEnabled": self.settings.get("typedbNativeRuleSelectionEnabled", "1"),
                "priorInferenceReusable": bool(selection_context.get("reusable")),
                "priorMatchedRuleIds": list(selection_context.get("matchedRuleIds") or []),
                "priorInferenceProofSource": str(selection_context.get("proofSource") or ""),
                "priorInferenceProofRunId": str(selection_context.get("proofRunId") or ""),
            }
            if inference_write_lease.get("acquired"):
                payload["_inferenceWriteLeaseOwner"] = str(inference_write_lease.get("leaseOwner") or "")
            if isinstance(preflight_graph, PortfolioOntology):
                # The graph was just validated and staged by this same writer
                # lease. TypeDB still evaluates every schema function; this
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
            # A native RuleBox execution first builds an in-memory graph and
            # then writes it to TypeDB. Only a fresh TypeDB read proves that
            # the active InferenceBox still exists after publication/pruning.
            if active_key == "typedb" and hasattr(self.repository, "inferencebox_snapshot"):
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
            elif isinstance(execution.get("inferenceBox"), dict):
                snapshot_payload = dict(execution.get("inferenceBox") or {})
                snapshot_payload.setdefault("graphStore", active_key)
                result["inferenceBox"] = snapshot_payload
            elif hasattr(self.repository, "inferencebox_snapshot"):
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
        finally:
            if inference_write_lease.get("acquired"):
                releaser = getattr(self.repository, "release_scoped_abox_write_lease", None)
                if callable(releaser):
                    try:
                        result["inferenceWriteLeaseRelease"] = releaser(inference_write_lease)
                    except Exception as error:  # noqa: BLE001 - expiry/recovery remains available.
                        result["inferenceWriteLeaseRelease"] = {"status": "error", "reason": str(error)[:180]}

    def acquire_inference_write_lease(self, result: Dict[str, object], world_id: str = "") -> Dict[str, object]:
        """Serialize ABox preparation and native InferenceBox publication."""
        acquire = getattr(self.repository, "acquire_scoped_abox_write_lease", None)
        if not callable(acquire):
            return {"status": "unsupported"}
        pending = result.get("pendingAboxActivation") if isinstance(result.get("pendingAboxActivation"), dict) else {}
        candidate_id = str(
            pending.get("candidateAboxSnapshotId")
            or result.get("aboxSnapshotId")
            or result.get("worldviewManifestId")
            or "native-rule"
        ).strip()
        try:
            return dict(self.repository_world_call(
                "acquire_scoped_abox_write_lease",
                "inference:" + candidate_id,
                world_id=world_id,
            ) or {})
        except Exception as error:  # noqa: BLE001 - do not activate without the writer boundary.
            return {"acquired": False, "status": "error", "reason": str(error)[:180]}

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
        if self.inference_result_is_reusable(
            inferencebox,
            {"aboxSnapshotId": active_snapshot_id},
            inference_symbols,
        ):
            finalizer = getattr(self.repository, "finalize_abox_generation", None)
            if callable(finalizer):
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
        result["reason"] = (
            "TypeDB native InferenceBox가 새 ABox 세대와 정렬되지 않아 "
            + ("이전 검증 세대로 복원했습니다." if result["preservedActiveGeneration"] else "투자 추론을 차단했습니다.")
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
    ) -> Dict[str, object]:
        """Prove which unaffected native rules must be re-materialized.

        This is a reuse proof, not a Python rule evaluation. The fast path
        reads an InferenceBox aligned to the active immutable ABox. When
        another symbol has moved the active Manifest meanwhile, a verified
        projection audit can still prove the prior target's complete native
        outcome. In that case we compare its saved scope identities with the
        candidate Manifest and ask TypeDB to re-run every rule affected since
        that target was last evaluated, plus every prior match.

        Missing provenance, a RuleBox/TBox change, or an opaque old scope
        deliberately falls back to a complete RuleBox slice.
        """
        active_abox = self.active_abox_metadata(world_id)
        inferencebox = self.existing_inference_result(snapshot, inference_symbols, world_id=world_id)
        reusable = self.inference_result_is_reusable(
            inferencebox,
            active_abox,
            inference_symbols,
        )
        rule_ids = []
        if reusable:
            rule_ids = self.matched_rule_ids_from_inference_payload(inferencebox)
            return {
                "reusable": True,
                "proofSource": "active-aligned-inference",
                "matchedRuleIds": rule_ids[:160],
                "matchedRuleCount": len(rule_ids),
                "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
                "sourceAboxSnapshotId": str(inferencebox.get("sourceAboxSnapshotId") or ""),
                "fallbackReason": "",
            }
        audited = self.audited_prior_rule_selection_context(
            snapshot,
            inference_symbols,
            candidate_scope_plan=candidate_scope_plan,
            rulebox_rules_hash=rulebox_rules_hash,
            tbox_fingerprint=tbox_fingerprint,
            world_id=world_id,
        )
        if audited:
            return audited
        return {
            "reusable": False,
            "proofSource": "",
            "matchedRuleIds": [],
            "matchedRuleCount": 0,
            "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
            "sourceAboxSnapshotId": str(inferencebox.get("sourceAboxSnapshotId") or ""),
            "fallbackReason": "prior-aligned-inference-unavailable",
        }

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
    ) -> Dict[str, object]:
        """Recover a target-specific native-rule proof from projection audit.

        MySQL stores only the immutable scope fingerprints and TypeDB's
        completed match set. It never asserts a new match or decides an
        investment action; the next TypeDB schema-function query remains the
        evaluator.
        """
        if not self.projection_run_store or not hasattr(self.projection_run_store, "latest"):
            return {}
        targets = [
            str(symbol or "").upper().strip()
            for symbol in inference_symbols or []
            if str(symbol or "").strip()
        ]
        if len(targets) != 1:
            return {}
        current_scope_plan = inference_reuse_scope_plan(candidate_scope_plan or [])
        if not current_scope_plan or not str(rulebox_rules_hash or "").strip():
            return {}
        try:
            try:
                rows = self.projection_run_store.latest(
                    account_id=str(snapshot.account_id or ""),
                    limit=160,
                    world_id=world_id,
                )
            except TypeError as error:
                if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                    raise
                rows = self.projection_run_store.latest(account_id=str(snapshot.account_id or ""), limit=160)
        except Exception:
            return {}
        current_rules = self.rulebox_rules_for_impact()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "").lower() != "ok":
                continue
            if str(row.get("graphStore") or "").strip() != "typedb":
                continue
            source_symbols = {
                str(symbol or "").upper().strip()
                for symbol in row.get("sourceSymbols") or []
                if str(symbol or "").strip()
            }
            if targets[0] not in source_symbols:
                continue
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            proof = result.get("inferenceReuseProof") if isinstance(result.get("inferenceReuseProof"), dict) else {}
            if str(proof.get("status") or "") != "verified" or not bool(proof.get("coverageComplete")):
                continue
            proof_targets = {
                str(symbol or "").upper().strip()
                for symbol in proof.get("targetSymbols") or []
                if str(symbol or "").strip()
            }
            if targets[0] not in proof_targets:
                continue
            if str(proof.get("ruleboxRulesHash") or "") != str(rulebox_rules_hash or ""):
                continue
            if tbox_fingerprint and str(proof.get("tboxFingerprint") or "") != str(tbox_fingerprint):
                continue
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            topology = context.get("scopeTopology") if isinstance(context.get("scopeTopology"), dict) else {}
            prior_scope_plan = inference_reuse_scope_plan(topology.get("inferenceReuseScopePlan") or [])
            if not prior_scope_plan:
                continue
            prior_scope_fingerprint = inference_reuse_scope_plan_fingerprint(prior_scope_plan)
            if (
                str(topology.get("inferenceReuseScopePlanFingerprint") or "") != prior_scope_fingerprint
                or str(proof.get("scopePlanFingerprint") or "") != prior_scope_fingerprint
            ):
                continue
            source_abox_snapshot_id = str(proof.get("sourceAboxSnapshotId") or "").strip()
            # Older audit rows can retain the predecessor in
            # ``activeAboxSnapshotId`` even though their own immutable ABox
            # and aligned InferenceBox are complete. The run's ABox identity
            # is equally authoritative here; the verified proof and matching
            # scope/rule/TBox contracts above still remain mandatory.
            audited_abox_snapshot_ids = {
                str(row.get("activeAboxSnapshotId") or "").strip(),
                str(row.get("aboxSnapshotId") or "").strip(),
            }
            audited_abox_snapshot_ids.discard("")
            if not source_abox_snapshot_id or source_abox_snapshot_id not in audited_abox_snapshot_ids:
                continue
            # Validate the whole immutable plan above, then calculate this
            # worker's candidate slice from its own scopes and dependencies.
            # Other holdings can change while a one-symbol worker waits for
            # the TypeDB lease; they must not reopen every rule for this
            # target.
            prior_target_scope_plan = inference_reuse_scope_plan_for_targets(
                prior_scope_plan,
                targets,
            )
            current_target_scope_plan = inference_reuse_scope_plan_for_targets(
                current_scope_plan,
                targets,
            )
            historical_plan = build_inference_impact_plan(
                prior_target_scope_plan,
                current_target_scope_plan,
                targets,
                explicit_target_symbols=targets,
                rules=current_rules,
            )
            if not bool(historical_plan.get("nativeRuleSelectionEligible")):
                continue
            matched_rule_ids = self.matched_rule_ids_from_inference_payload(proof)
            return {
                "reusable": True,
                "proofSource": "audited-target-scope-proof",
                "proofRunId": str(row.get("runId") or ""),
                "matchedRuleIds": matched_rule_ids,
                "matchedRuleCount": len(matched_rule_ids),
                "inferenceGenerationId": str(proof.get("inferenceGenerationId") or ""),
                "sourceAboxSnapshotId": source_abox_snapshot_id,
                "inferenceImpactPlan": historical_plan,
                "recomputedCandidateRuleCount": int(historical_plan.get("candidateRuleCount") or 0),
                "recomputedChangedScopeCount": len(
                    list((historical_plan.get("scopeDelta") or {}).get("changedScopeIds") or [])
                ),
                "fallbackReason": "",
            }
        return {}

    def snapshot_symbols(self, snapshot: AccountSnapshot) -> List[str]:
        symbols = []
        for item in list(snapshot.positions or []) + list(snapshot.watchlist or []):
            symbol = str(getattr(item, "symbol", "") or "").upper().strip()
            if symbol and symbol not in symbols:
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
        return selected or self.snapshot_symbols(snapshot)

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
    ) -> List[str]:
        """Prioritize triggering subjects without dropping global ABox context.

        A portfolio or macro scope can affect many holdings, but evaluating all
        of them in one native TypeDB cycle defeats the worker's configured
        per-cycle symbol bound. The complete ABox remains active for each
        rule; only the current RuleBox subjects are sequenced across cycles.
        """
        limit = self.native_inference_symbol_limit()
        if not limit:
            return list(inferred_symbols or [])
        requested = self.inference_symbols(snapshot, requested_symbols)
        available = self.snapshot_symbols(snapshot)
        ordered = []
        for symbol in list(requested) + list(inferred_symbols or []) + available:
            clean = str(symbol or "").upper().strip()
            if clean and clean in available and clean not in ordered:
                ordered.append(clean)
        return ordered[:limit]

    def scoped_full_reconcile_minutes(self) -> float:
        """Bound deferred-symbol freshness during target-scoped projection."""
        try:
            value = float(str(self.settings.get("ontologyScopedFullReconcileMinutes") or "30"))
        except (TypeError, ValueError):
            value = 30.0
        return max(5.0, min(24.0 * 60.0, value))

    def scoped_full_reconcile_due(self, active_metadata: Dict[str, object]) -> bool:
        """Require a periodic whole-world pass instead of hiding deferred facts."""
        stamp = str(
            (active_metadata or {}).get("lastFullScopeReconcileAt")
            or (active_metadata or {}).get("asOf")
            or ""
        ).strip()
        if not stamp:
            return True
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        age_minutes = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60.0
        return age_minutes >= self.scoped_full_reconcile_minutes()

    def target_scoped_patch_targets(
        self,
        snapshot: AccountSnapshot,
        active_metadata: Dict[str, object],
        scoped_identity: Dict[str, object],
        requested_symbols: List[str] = None,
    ) -> Dict[str, object]:
        """Choose a safe incremental write set before replacing a manifest.

        TypeDB still receives the full active world through the manifest. This
        only prevents a one-symbol observation from rewriting unchanged
        symbols. A global change or an overdue reconciliation always keeps the
        existing full projection path.
        """
        preliminary = self.inference_impact_plan(
            snapshot,
            active_metadata,
            scoped_identity,
            requested_symbols,
        )
        available = self.snapshot_symbols(snapshot)
        inferred = self.inference_symbols(
            snapshot,
            preliminary.get("inferenceTargetSymbols") or requested_symbols,
        )
        inferred = self.bounded_native_inference_symbols(snapshot, inferred, requested_symbols)
        explicit = self.inference_symbols(snapshot, requested_symbols)
        base = {
            "preliminaryImpactPlan": compact_inference_impact_plan(preliminary),
            "targetSymbols": list(inferred),
            "explicitTargetSymbols": list(explicit),
            "availableSymbolCount": len(available),
            "fullReconcileMinutes": self.scoped_full_reconcile_minutes(),
        }
        # A reasoning worker can intentionally schedule one subject even when
        # a shared macro or portfolio fact also changed. Persist that subject
        # and the shared scopes now; the untouched subjects retain their last
        # coherent context until their own queued turn or the periodic full
        # reconciliation. Without an explicit worker target, preserve the
        # conservative whole-portfolio path for a global change.
        if preliminary.get("globalImpact") and not explicit:
            return {**base, "status": "full-global-impact", "eligible": False}
        if self.scoped_full_reconcile_due(active_metadata):
            return {**base, "status": "full-reconciliation-due", "eligible": False}
        if not inferred or len(inferred) >= len(available):
            return {**base, "status": "full-target-set", "eligible": False}
        return {
            **base,
            "status": (
                "target-scoped-explicit-global-context"
                if preliminary.get("globalImpact")
                else "target-scoped"
            ),
            "eligible": True,
        }

    def inference_impact_plan(
        self,
        snapshot: AccountSnapshot,
        active_abox: Dict[str, object],
        scoped_identity: Dict[str, object],
        target_symbols: List[str] = None,
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
        return any(
            item
            for item in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if not item.is_cash()
        )

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

    def begin_projection_audit_run(
        self,
        snapshot: AccountSnapshot,
        graph: PortfolioOntology,
        material_fingerprint: str,
        abox_snapshot_id: str,
        inference_symbols: List[str],
        rulebox_metadata: Dict[str, object],
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

    def runtime_context(
        self,
        snapshot: AccountSnapshot,
        active_tbox: Dict[str, object] = None,
    ) -> Dict[str, object]:
        if active_tbox is None:
            active_tbox = self.active_tbox_context()
        as_of = str(snapshot.generated_at or "").strip()
        snapshot_seed = "|".join([str(snapshot.account_id or ""), as_of or "unknown"])
        decision_episodes = self.decision_episode_context(snapshot)
        metadata = self.factual_runtime_metadata(snapshot.metadata)
        # Projection output is derived state, not a new market observation.
        # Feeding the previous ABox result back into the next ABox makes an
        # otherwise unchanged snapshot look materially different.
        metadata.pop("ontology", None)
        account_context = metadata.get("accountContext") if isinstance(metadata.get("accountContext"), dict) else {}
        decision_performance = evaluate_decision_performance(
            decision_episodes,
            minimum_sample_count=int(self.performance_setting("investmentBrainPerformanceMinimumSamples", 5)),
        )
        return {
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
            "decisionPerformance": decision_performance,
            "hypothesisProposals": self.hypothesis_proposal_context(snapshot),
            "hypothesisLifecycles": self.hypothesis_lifecycle_context(snapshot),
            # A live pipeline health row may change while a delayed retry is
            # rebuilding the same account snapshot. Only health captured with
            # the snapshot is causal ABox input; current worker telemetry is
            # exposed through operational monitoring instead.
            "dataPipelineHealth": self.data_pipeline_health_context(snapshot),
            "temporalObservationWindows": self.temporal_observation_windows(snapshot),
        }

    @staticmethod
    def factual_runtime_metadata(metadata: Dict[str, object] = None) -> Dict[str, object]:
        """Keep historical market facts while removing derived decision output.

        Trend and change concepts still need the prior positions/watchlist
        snapshots. Their embedded decisions, AI context, and prior ontology
        output are rendered results, however, so carrying them into the next
        ABox would create a self-triggering inference loop.
        """
        values = deepcopy(dict(metadata or {}))
        values.pop("ontology", None)

        def factual_state(state: object) -> object:
            if not isinstance(state, dict):
                return state
            result = dict(state)
            result.pop("decisions", None)
            nested = result.get("metadata")
            if isinstance(nested, dict):
                nested = dict(nested)
                nested.pop("ontology", None)
                nested.pop("previousMonitorState", None)
                nested.pop("monitorStateHistory", None)
                result["metadata"] = nested
            return result

        if "previousMonitorState" in values:
            values["previousMonitorState"] = factual_state(values.get("previousMonitorState"))
        if isinstance(values.get("previousState"), dict):
            values["previousState"] = factual_state(values.get("previousState"))
        if isinstance(values.get("monitorStateHistory"), list):
            values["monitorStateHistory"] = [
                factual_state(item)
                for item in values.get("monitorStateHistory") or []
                if isinstance(item, dict)
            ]
        return values

    def temporal_observation_windows(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        if not self.market_time_series_store or not hasattr(self.market_time_series_store, "load_temporal_windows"):
            return {}
        symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
        }
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

    def decision_episode_context(self, snapshot: AccountSnapshot) -> List[Dict[str, object]]:
        if not self.decision_episode_store:
            return []
        try:
            observation = self.outcome_observation_service.observe_snapshot(snapshot)
            snapshot.metadata.setdefault("investmentBrain", {})["outcomeObservation"] = observation
        except Exception as error:  # noqa: BLE001 - feedback memory must not block ABox projection.
            snapshot.metadata.setdefault("investmentBrain", {})["outcomeObservation"] = {
                "status": "error",
                "reason": str(error)[:180],
            }
        try:
            # Outcome facts must remain visible after a position is sold or
            # removed from a watchlist. The cognitive ABox is bounded, but it
            # is account-history based rather than current-symbol based.
            return [
                item.to_dict()
                for item in self.decision_episode_store.list(snapshot.account_id, limit=30)
            ]
        except Exception:  # noqa: BLE001 - projection remains valid without historical memory.
            return []

    def hypothesis_proposal_context(self, snapshot: AccountSnapshot) -> List[Dict[str, object]]:
        if not self.hypothesis_proposal_store or not hasattr(self.hypothesis_proposal_store, "list_hypothesis_proposals"):
            return []
        symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip()
        }
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

    def hypothesis_lifecycle_context(self, snapshot: AccountSnapshot) -> List[Dict[str, object]]:
        if not self.hypothesis_lifecycle_store or not hasattr(self.hypothesis_lifecycle_store, "current_for_subjects"):
            return []
        symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
        }
        if not symbols:
            return []
        try:
            records = self.hypothesis_lifecycle_store.current_for_subjects(snapshot.account_id, symbols)
        except Exception:  # noqa: BLE001 - lifecycle audit must not block a factual ABox projection.
            return []
        return [
            item.to_dict()
            for item in (records or {}).values()
            if hasattr(item, "to_dict")
        ]
