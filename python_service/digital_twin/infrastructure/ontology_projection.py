from typing import Dict, List, Set
import hashlib

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_governance import rulebox_rules_hash
from ..domain.ontology_validator import validate_ontology
from ..domain.portfolio_ontology_builder import build_portfolio_ontology
from ..domain.portfolio import AccountSnapshot
from .graph_store_rulebox import rulebox_rules_to_payload


class PortfolioOntologyProjectionRecorder:
    def __init__(
        self,
        repository,
        quality_store=None,
        decision_episode_store=None,
        hypothesis_proposal_store=None,
        settings: Dict[str, object] = None,
        source: str = "monitoring",
    ):
        self.repository = repository
        self.quality_store = quality_store
        self.decision_episode_store = decision_episode_store
        self.hypothesis_proposal_store = hypothesis_proposal_store
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
            result = self.repository.save_graph(persistence_graph)
            if not isinstance(result, dict):
                result = {"saved": False, "status": "error", "reason": "ontology repository returned non-dict result"}
            result["projectionMode"] = "abox-facts-only-" + self.active_graph_store_key(result) + "-rulebox"
            result["aboxValidation"] = validation.to_dict()
            if rulebox_bootstrap:
                result["ruleboxBootstrap"] = rulebox_bootstrap
            if result.get("saved"):
                self.attach_graph_store_inference_result(result, snapshot)
            if self.quality_store:
                sample = self.quality_store.record_graph(graph, source=self.source)
                result["qualitySampleId"] = sample.sample_id
                result["qualityScore"] = sample.overall_score
        except Exception as error:  # noqa: BLE001 - ontology projection must not block realtime monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
        self.store_projection_result(snapshot, result)
        return result

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

    def graph_for_graph_store_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
        stripped_boxes = {"RuleBox", "InferenceBox"}
        stripped_ids: Set[str] = set()
        entities = []
        for item in graph.entities:
            box = str((item.properties or {}).get("ontologyBox") or "ABox")
            if box in stripped_boxes:
                stripped_ids.add(item.entity_id)
                continue
            entities.append(item)
        relations = [
            item
            for item in graph.relations
            if str((item.properties or {}).get("ontologyBox") or "ABox") not in stripped_boxes
            and item.source not in stripped_ids
            and item.target not in stripped_ids
        ]
        evidence = [
            item
            for item in graph.evidence
            if str((item.value or {}).get("ontologyBox") or "ABox") not in stripped_boxes
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
            "hypothesisProposals": self.hypothesis_proposal_context(snapshot),
        }

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
