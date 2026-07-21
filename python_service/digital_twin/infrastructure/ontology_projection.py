from copy import deepcopy
from typing import Dict, List, Set
import hashlib

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.decision_performance import evaluate_decision_performance
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_governance import rulebox_rules_hash
from ..domain.ontology_projection_fingerprint import (
    active_material_fingerprint,
    apply_material_graph_identity,
    material_graph_fingerprint,
)
from ..domain.ontology_projection_audit import (
    OntologyProjectionRun,
    apply_projection_run_identity,
    build_ontology_projection_run,
    complete_ontology_projection_run,
)
from ..domain.ontology_validator import validate_ontology
from ..domain.portfolio_ontology_builder import build_portfolio_ontology
from ..domain.portfolio_ontology_coverage import CATEGORY_RELATIONS
from ..domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
from ..domain.portfolio import AccountSnapshot
from .graph_store_rulebox import rulebox_rules_to_payload


DEPRECATED_TYPEDB_RULE_IDS = {"shadow.market_psychology.state.v1"}


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
        data_pipeline_health_store=None,
        market_time_series_store=None,
        projection_run_store=None,
        settings: Dict[str, object] = None,
        source: str = "monitoring",
    ):
        self.repository = repository
        self.quality_store = quality_store
        self.decision_episode_store = decision_episode_store
        self.hypothesis_proposal_store = hypothesis_proposal_store
        self.data_pipeline_health_store = data_pipeline_health_store
        self.market_time_series_store = market_time_series_store
        self.projection_run_store = projection_run_store
        self.settings = dict(settings or {})
        self.source = source or "monitoring"

    def record_snapshot(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
    ) -> Dict[str, object]:
        projection_run = None
        pending_activation_recovery: Dict[str, object] = {}
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
            }
            self.store_projection_result(snapshot, result)
            return result
        pending_activation_recovery = self.recover_pending_abox_activation()
        recovery_status = str(pending_activation_recovery.get("status") or "skipped")
        if recovery_status not in {"skipped", "disabled", "finalized", "restored", "cleared-stale", "retry-required"}:
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
            graph = build_portfolio_ontology(
                list(snapshot.positions or []) + list(snapshot.watchlist or []),
                snapshot.portfolio,
                legacy_by_symbol={
                    item.symbol.upper(): item.to_dict()
                    for item in snapshot.decisions
                },
                external_signals=snapshot.external_signals,
                portfolio_id=snapshot.account_id,
                runtime_context=self.runtime_context(snapshot),
                # The realtime path persists only ABox facts.  Static TBox
                # vocabulary is seeded independently and presentation output
                # is rebuilt later from the active InferenceBox for an alert
                # or UI request.
                include_tbox=False,
                include_presentation=False,
            )
            persistence_graph = self.graph_for_graph_store_persistence(graph, rulebox_bootstrap)
            material_fingerprint = material_graph_fingerprint(persistence_graph)
            material_snapshot_id = apply_material_graph_identity(
                persistence_graph,
                snapshot.account_id,
                material_fingerprint,
            )
            validation = validate_ontology(persistence_graph)
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
            inference_symbols = self.inference_symbols(snapshot, target_symbols)
            active_abox = self.active_abox_metadata()
            active_abox_complete = str(active_abox.get("status") or "ok") == "ok"
            if active_abox_complete and active_material_fingerprint(active_abox) == material_fingerprint:
                inferencebox = self.existing_inference_result(snapshot, inference_symbols)
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
                }
                if rulebox_bootstrap:
                    result["ruleboxBootstrap"] = rulebox_bootstrap
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
                    self.attach_graph_store_inference_result(result, snapshot, inference_symbols)
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
            result = self.repository.save_graph(persistence_graph)
            if not isinstance(result, dict):
                result = {"saved": False, "status": "error", "reason": "ontology repository returned non-dict result"}
            result["projectionMode"] = "abox-facts-only-typedb-native-rules"
            result["materialFingerprint"] = material_fingerprint
            result["aboxSnapshotId"] = material_snapshot_id
            result["materialChangeDetected"] = True
            result["aboxValidation"] = validation.to_dict()
            if rulebox_bootstrap:
                result["ruleboxBootstrap"] = rulebox_bootstrap
            if pending_activation_recovery:
                result["pendingAboxActivationRecovery"] = pending_activation_recovery
            if result.get("saved"):
                self.attach_graph_store_inference_result(result, snapshot, inference_symbols)
            if self.quality_store:
                sample = self.quality_store.record_graph(graph, source=self.source)
                result["qualitySampleId"] = sample.sample_id
                result["qualityState"] = sample.overall_state
        except Exception as error:  # noqa: BLE001 - ontology projection must not block realtime monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
        self.store_projection_result(snapshot, result, projection_run)
        return result

    def active_abox_metadata(self) -> Dict[str, object]:
        if not hasattr(self.repository, "active_abox_metadata"):
            return {}
        try:
            result = self.repository.active_abox_metadata()
        except Exception:  # noqa: BLE001 - absence of comparison metadata falls back to persistence.
            return {}
        return dict(result or {}) if isinstance(result, dict) else {}

    def recover_pending_abox_activation(self) -> Dict[str, object]:
        if self.active_graph_store_key() != "typedb":
            return {"status": "skipped", "reason": "Active graph store is not TypeDB."}
        recovery = getattr(self.repository, "recover_pending_abox_activation", None)
        if not callable(recovery):
            return {"status": "skipped", "reason": "Graph store has no pending ABox activation journal."}
        try:
            result = recovery()
        except Exception as error:  # noqa: BLE001 - do not replace a potentially recoverable active generation.
            return {"status": "error", "reason": str(error)[:180]}
        return dict(result or {}) if isinstance(result, dict) else {
            "status": "error",
            "reason": "Graph store returned an invalid ABox activation recovery result.",
        }

    def ensure_rulebox_ready(self) -> Dict[str, object]:
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
        # The active ABox is both TypeDB's native-rule input and the factual
        # investment world shown to diagnostics and AI. Keep the category
        # edges that define that world even when no currently enabled rule
        # consumes one of them. Otherwise a valid Price/Liquidity concept can
        # exist as an orphaned node, producing a misleading coverage gap.
        semantic_relation_types = {
            str(relation_type or "").upper().strip()
            for category_types in CATEGORY_RELATIONS.values()
            for relation_type in category_types
            if str(relation_type or "").strip()
        }
        persisted_relation_types = native_relation_types | semantic_relation_types
        source_ids = {
            item.entity_id
            for item in abox_entities
            if str(item.kind or "") in {"stock", "portfolio"}
        }
        relations = [
            item
            for item in abox_relations
            if (
                str(item.relation_type or "").upper().strip() in persisted_relation_types
                if persisted_relation_types
                else item.source in source_ids or item.target in source_ids
            )
        ]
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
        beliefs = [
            item
            for item in graph.beliefs
            if not str(item.belief_id or "").startswith("belief:inference:")
            and str(item.subject or "") in source_ids
        ]
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
                "runtimeProjectionRuleInputRelationTypeCount": len(native_relation_types),
                "runtimeProjectionSemanticRelationTypeCount": len(semantic_relation_types),
            },
            prompt=graph.prompt,
        )

    def graph_for_typedb_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
        return self.graph_for_graph_store_persistence(graph)

    def attach_graph_store_inference_result(
        self,
        result: Dict[str, object],
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
    ) -> None:
        if not hasattr(self.repository, "run_rulebox"):
            return
        inference_symbols = self.inference_symbols(snapshot, target_symbols)
        try:
            execution = self.repository.run_rulebox({
                "symbols": inference_symbols,
                "pruneOldGenerations": True,
                "inferenceSnapshotLimit": self.inference_snapshot_limit(),
            })
        except Exception as error:  # noqa: BLE001 - graph inference must not block monitoring.
            execution = {"status": "error", "reason": str(error)[:180]}
        active_key = self.active_graph_store_key(result)
        if isinstance(execution, dict):
            execution.setdefault("graphStore", active_key)
            if active_key == "typedb":
                execution.setdefault("source", "typedbNativeRule")
        else:
            execution = {"status": "error", "reason": "non-dict RuleBox result", "graphStore": active_key}
        result["ruleboxExecution"] = execution
        if isinstance(execution.get("inferenceBox"), dict):
            snapshot_payload = dict(execution.get("inferenceBox") or {})
            snapshot_payload.setdefault("graphStore", active_key)
            if active_key == "typedb":
                snapshot_payload.setdefault("source", "typedbInferenceBox")
            result["inferenceBox"] = snapshot_payload
        elif hasattr(self.repository, "inferencebox_snapshot"):
            try:
                snapshot_payload = self.repository.inferencebox_snapshot(
                    symbols=inference_symbols,
                    limit=self.inference_snapshot_limit(),
                )
            except Exception as error:  # noqa: BLE001 - snapshot read is best effort.
                snapshot_payload = {"status": "error", "reason": str(error)[:180], "graphStore": active_key}
            if isinstance(snapshot_payload, dict):
                snapshot_payload.setdefault("graphStore", active_key)
                if active_key == "typedb":
                    snapshot_payload.setdefault("source", "typedbInferenceBox")
                result["inferenceBox"] = snapshot_payload
        self.reconcile_abox_activation_after_inference(result, inference_symbols)

    def reconcile_abox_activation_after_inference(
        self,
        result: Dict[str, object],
        inference_symbols: List[str],
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
                pending = pending_reader()
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
                    result["aboxActivationFinalization"] = finalizer(
                        active_snapshot_id,
                        previous_snapshot_id,
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
                rollback = restore(previous_snapshot_id)
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

    def existing_inference_result(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
    ) -> Dict[str, object]:
        if not hasattr(self.repository, "inferencebox_snapshot"):
            return {}
        inference_symbols = self.inference_symbols(snapshot, target_symbols)
        try:
            inferencebox = self.repository.inferencebox_snapshot(
                symbols=inference_symbols,
                limit=self.inference_snapshot_limit(),
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
        if str((inferencebox or {}).get("status") or "") != "ok":
            return False
        if not bool((inferencebox or {}).get("nativeTypeDbReasoningUsed")):
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
                completed_run = complete_ontology_projection_run(projection_run, result)
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

    def runtime_context(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        active_tbox = {}
        if hasattr(self.repository, "active_tbox_metadata"):
            try:
                active_tbox = self.repository.active_tbox_metadata()
            except Exception as error:  # noqa: BLE001 - projection can still use code fallback in builder.
                active_tbox = {"status": "error", "reason": str(error)[:180], "source": "code-fallback"}
        as_of = str(snapshot.generated_at or "").strip()
        snapshot_seed = "|".join([str(snapshot.account_id or ""), as_of or "unknown"])
        metadata = dict(snapshot.metadata or {})
        account_context = metadata.get("accountContext") if isinstance(metadata.get("accountContext"), dict) else {}
        decision_episodes = self.decision_episode_context(snapshot)
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
            "decisionItems": [item.to_dict() for item in snapshot.decisions],
            "decisionEpisodes": decision_episodes,
            "decisionPerformance": decision_performance,
            "hypothesisProposals": self.hypothesis_proposal_context(snapshot),
            "dataPipelineHealth": self.data_pipeline_health_context(),
            "temporalObservationWindows": self.temporal_observation_windows(snapshot),
        }

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
            )
        except Exception:  # noqa: BLE001 - short snapshot history remains a valid compatibility fallback.
            return {}

    def performance_setting(self, key: str, fallback: float) -> float:
        try:
            return float(str(self.settings.get(key) or fallback))
        except (TypeError, ValueError):
            return float(fallback)

    def data_pipeline_health_context(self) -> Dict[str, object]:
        if not self.data_pipeline_health_store or not hasattr(self.data_pipeline_health_store, "load"):
            return {}
        try:
            payload = self.data_pipeline_health_store.load()
            return dict(payload or {}) if isinstance(payload, dict) else {}
        except Exception:  # noqa: BLE001 - operational health is confidence context, not a projection blocker.
            return {}

    def decision_episode_context(self, snapshot: AccountSnapshot) -> List[Dict[str, object]]:
        if not self.decision_episode_store:
            return []
        symbols = []
        for position in list(snapshot.positions or []) + list(snapshot.watchlist or []):
            symbol = str(getattr(position, "symbol", "") or "").upper().strip()
            if not symbol or position.is_cash():
                continue
            symbols.append(symbol)
            try:
                self.decision_episode_store.record_observation(
                    snapshot.account_id,
                    symbol,
                    {
                        "currentPrice": getattr(position, "current_price", 0),
                        "profitLossRate": getattr(position, "profit_loss_rate", 0),
                        "priceChangeRate": getattr(position, "change_rate", 0),
                        "observedAt": snapshot.generated_at,
                    },
                    snapshot.generated_at,
                )
            except Exception:  # noqa: BLE001 - feedback memory must not block ABox projection.
                continue
        episodes = []
        for symbol in list(dict.fromkeys(symbols)):
            try:
                episodes.extend(
                    item.to_dict()
                    for item in self.decision_episode_store.list(snapshot.account_id, symbol, limit=6)
                )
            except Exception:  # noqa: BLE001 - projection remains valid without historical memory.
                continue
        return episodes[:30]

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
