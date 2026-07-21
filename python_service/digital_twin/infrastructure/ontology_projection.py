from typing import Dict, List, Set
import hashlib

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.decision_performance import evaluate_decision_performance
from ..domain.ontology_rulebox_catalog import additive_system_graph_inference_rules, default_graph_inference_rules
from ..domain.ontology_rulebox_governance import rulebox_rules_hash
from ..domain.ontology_projection_fingerprint import (
    active_material_fingerprint,
    apply_material_graph_identity,
    material_graph_fingerprint,
)
from ..domain.ontology_validator import validate_ontology
from ..domain.portfolio_ontology_builder import build_portfolio_ontology
from ..domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
from ..domain.portfolio import AccountSnapshot
from .graph_store_rulebox import rulebox_rules_to_payload


class PortfolioOntologyProjectionRecorder:
    def __init__(
        self,
        repository,
        quality_store=None,
        decision_episode_store=None,
        hypothesis_proposal_store=None,
        data_pipeline_health_store=None,
        market_time_series_store=None,
        settings: Dict[str, object] = None,
        source: str = "monitoring",
    ):
        self.repository = repository
        self.quality_store = quality_store
        self.decision_episode_store = decision_episode_store
        self.hypothesis_proposal_store = hypothesis_proposal_store
        self.data_pipeline_health_store = data_pipeline_health_store
        self.market_time_series_store = market_time_series_store
        self.settings = dict(settings or {})
        self.source = source or "monitoring"

    def record_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
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
        try:
            rulebox_bootstrap = self.ensure_rulebox_ready()
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
            )
            persistence_graph = self.graph_for_graph_store_persistence(graph)
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
                self.store_projection_result(snapshot, result)
                return result
            active_abox = self.active_abox_metadata()
            active_abox_complete = str(active_abox.get("status") or "ok") == "ok"
            if active_abox_complete and active_material_fingerprint(active_abox) == material_fingerprint:
                inferencebox = self.existing_inference_result(snapshot)
                result = {
                    "saved": False,
                    "status": (
                        "unchanged-material-facts"
                        if self.inference_result_is_reusable(
                            inferencebox,
                            active_abox,
                            self.snapshot_symbols(snapshot),
                        )
                        else "unchanged-material-facts-reasoning-retry"
                    ),
                    "reason": (
                        "가격·손익·수급·뉴스·신선도 등 추론 입력이 직전 ABox와 같습니다."
                        if self.inference_result_is_reusable(
                            inferencebox,
                            active_abox,
                            self.snapshot_symbols(snapshot),
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
                if self.inference_result_is_reusable(
                    inferencebox,
                    active_abox,
                    self.snapshot_symbols(snapshot),
                ):
                    inferencebox["reusedForUnchangedMaterialFacts"] = True
                    result["inferenceBox"] = inferencebox
                else:
                    result["reasoningRetryRequired"] = True
                    result["previousInferenceStatus"] = str(inferencebox.get("status") or "missing")
                    self.attach_graph_store_inference_result(result, snapshot)
                self.store_projection_result(snapshot, result)
                return result
            result = self.repository.save_graph(persistence_graph)
            if not isinstance(result, dict):
                result = {"saved": False, "status": "error", "reason": "ontology repository returned non-dict result"}
            result["projectionMode"] = "abox-facts-only-" + self.active_graph_store_key(result) + "-rulebox"
            result["materialFingerprint"] = material_fingerprint
            result["aboxSnapshotId"] = material_snapshot_id
            result["materialChangeDetected"] = True
            result["aboxValidation"] = validation.to_dict()
            if rulebox_bootstrap:
                result["ruleboxBootstrap"] = rulebox_bootstrap
            if result.get("saved"):
                self.attach_graph_store_inference_result(result, snapshot)
            if self.quality_store:
                sample = self.quality_store.record_graph(graph, source=self.source)
                result["qualitySampleId"] = sample.sample_id
                result["qualityState"] = sample.overall_state
        except Exception as error:  # noqa: BLE001 - ontology projection must not block realtime monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
        self.store_projection_result(snapshot, result)
        return result

    def active_abox_metadata(self) -> Dict[str, object]:
        if not hasattr(self.repository, "active_abox_metadata"):
            return {}
        try:
            result = self.repository.active_abox_metadata()
        except Exception:  # noqa: BLE001 - absence of comparison metadata falls back to persistence.
            return {}
        return dict(result or {}) if isinstance(result, dict) else {}

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
        migration = self.ensure_additive_system_rules(snapshot)
        if migration.get("saved"):
            try:
                snapshot = self.repository.rulebox_snapshot()
            except Exception:  # noqa: BLE001 - successful save metadata still proves the migration ran.
                snapshot = dict(migration.get("result") or snapshot)
        stored_count = int(snapshot.get("ruleboxRuleCount") or snapshot.get("ruleCount") or 0)
        stored_hash = str(snapshot.get("ruleboxRulesHash") or snapshot.get("rulesHash") or "").strip()
        if not stored_hash and isinstance(snapshot.get("rules"), list) and snapshot.get("rules"):
            stored_hash = rulebox_rules_hash(snapshot.get("rules") or [])
        if stored_count > 0 and str(snapshot.get("status") or "") == "ok":
            result = {
                "status": "ready",
                "ruleCount": stored_count,
                "ruleboxRulesHash": stored_hash,
                "sourceOfTruth": "typedb-rulebox",
                "bootstrapRuleCount": expected_count,
                "bootstrapRulesHash": expected_hash,
                "codeDefaultHashMismatch": bool(stored_hash and stored_hash != expected_hash),
                "additiveRuleMigration": migration,
            }
            if result["codeDefaultHashMismatch"]:
                result["reason"] = (
                    "TypeDB RuleBox is the source of truth. Code default rules are bootstrap-only "
                    "and will not overwrite stored RuleBox rules."
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
            "sourceOfTruth": "typedb-rulebox",
            "bootstrapRuleCount": expected_count,
            "bootstrapRulesHash": expected_hash,
            "reason": str((seeded or {}).get("reason") or ""),
        }

    def ensure_additive_system_rules(self, snapshot: Dict[str, object]) -> Dict[str, object]:
        if str(self.settings.get("psychologyShadowEnabled") or "1").strip().lower() in {"0", "false", "no", "off", "disabled"}:
            return {"status": "disabled", "saved": False, "missingRuleIds": []}
        stored_rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
        if not stored_rules:
            return {"status": "not-inspectable", "saved": False, "missingRuleIds": []}
        existing_ids = {
            str(item.get("rule_id") or item.get("ruleId") or "").strip()
            for item in stored_rules
            if isinstance(item, dict)
        }
        required = additive_system_graph_inference_rules()
        missing = [rule for rule in required if rule.rule_id not in existing_ids]
        if not missing:
            return {"status": "ready", "saved": False, "missingRuleIds": []}
        missing_ids = [rule.rule_id for rule in missing]
        if not hasattr(self.repository, "save_rulebox"):
            return {"status": "unsupported", "saved": False, "missingRuleIds": missing_ids}
        try:
            result = self.repository.save_rulebox({
                "rules": list(stored_rules) + rulebox_rules_to_payload(missing),
                "changeReason": "시스템 가산 마이그레이션: 시장 심리 Shadow 규칙 등록",
            })
        except Exception as error:  # noqa: BLE001 - Shadow migration must not block existing investment inference.
            return {"status": "error", "saved": False, "missingRuleIds": missing_ids, "reason": str(error)[:180]}
        saved = bool((result or {}).get("saved")) and str((result or {}).get("status") or "") == "ok"
        schema_sync = {}
        if saved and hasattr(self.repository, "sync_typedb_native_rule_functions"):
            try:
                schema_sync = self.repository.sync_typedb_native_rule_functions(missing, force=True)
            except Exception as error:  # noqa: BLE001 - the stored rule remains available for a later sync retry.
                schema_sync = {"status": "error", "reason": str(error)[:180]}
        return {
            "status": (
                "migrated"
                if saved and str(schema_sync.get("status") or "ok") == "ok"
                else "stored-sync-pending"
                if saved
                else str((result or {}).get("status") or "not-saved")
            ),
            "saved": saved,
            "missingRuleIds": missing_ids,
            "schemaFunctionSync": schema_sync,
            "result": {
                "status": str((result or {}).get("status") or ""),
                "ruleCount": int((result or {}).get("ruleCount") or (result or {}).get("ruleboxRuleCount") or 0),
                "reason": str((result or {}).get("reason") or "")[:180],
            },
        }

    def graph_for_graph_store_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
        # TBox, RuleBox, and language governance are seeded separately. Rewriting
        # them on every quote update turns a small ABox refresh into a full graph
        # replacement and quickly overwhelms TypeDB's transaction log.
        stripped_ids: Set[str] = set()
        entities = []
        for item in graph.entities:
            box = str((item.properties or {}).get("ontologyBox") or "ABox")
            if box != "ABox":
                stripped_ids.add(item.entity_id)
                continue
            entities.append(item)
        relations = [
            item
            for item in graph.relations
            if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox"
            and item.source not in stripped_ids
            and item.target not in stripped_ids
        ]
        evidence = [
            item
            for item in graph.evidence
            if str((item.value or {}).get("ontologyBox") or "ABox") == "ABox"
        ]
        beliefs = [
            item
            for item in graph.beliefs
            if not str(item.belief_id or "").startswith("belief:inference:")
        ]
        return PortfolioOntology(
            graph.portfolio_id,
            entities=entities,
            relations=relations,
            evidence=evidence,
            beliefs=beliefs,
            opinions=[],
            reasoning_cards=[],
            worldview={**dict(graph.worldview or {}), "runtimeProjectionMode": "abox-facts-only-graph-store-rulebox"},
            prompt=graph.prompt,
        )

    def graph_for_typedb_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
        return self.graph_for_graph_store_persistence(graph)

    def attach_graph_store_inference_result(self, result: Dict[str, object], snapshot: AccountSnapshot) -> None:
        if not hasattr(self.repository, "run_rulebox"):
            return
        try:
            execution = self.repository.run_rulebox({
                "symbols": self.snapshot_symbols(snapshot),
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
            return
        if not hasattr(self.repository, "inferencebox_snapshot"):
            return
        try:
            snapshot_payload = self.repository.inferencebox_snapshot(
                symbols=self.snapshot_symbols(snapshot),
                limit=self.inference_snapshot_limit(),
            )
        except Exception as error:  # noqa: BLE001 - snapshot read is best effort.
            snapshot_payload = {"status": "error", "reason": str(error)[:180], "graphStore": active_key}
        if isinstance(snapshot_payload, dict):
            snapshot_payload.setdefault("graphStore", active_key)
            if active_key == "typedb":
                snapshot_payload.setdefault("source", "typedbInferenceBox")
            result["inferenceBox"] = snapshot_payload

    def existing_inference_result(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        if not hasattr(self.repository, "inferencebox_snapshot"):
            return {}
        try:
            inferencebox = self.repository.inferencebox_snapshot(
                symbols=self.snapshot_symbols(snapshot),
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

    def store_projection_result(self, snapshot: AccountSnapshot, result: Dict[str, object]) -> None:
        ontology = snapshot.metadata.setdefault("ontology", {})
        active_key = self.active_graph_store_key(result)
        result.setdefault("graphStore", active_key)
        result.setdefault("activeGraphStore", active_key)
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
