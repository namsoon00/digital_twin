from typing import Dict, List, Set
import hashlib

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_validator import validate_ontology
from ..domain.portfolio_ontology_builder import build_portfolio_ontology
from ..domain.portfolio import AccountSnapshot


class PortfolioOntologyProjectionRecorder:
    def __init__(
        self,
        repository,
        quality_store=None,
        settings: Dict[str, object] = None,
        source: str = "monitoring",
    ):
        self.repository = repository
        self.quality_store = quality_store
        self.settings = dict(settings or {})
        self.source = source or "monitoring"

    def record_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        if not self.repository or not self.has_projectable_data(snapshot):
            return {}
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
                include_reasoning_outputs=False,
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
        if not hasattr(self.repository, "rulebox_snapshot") or not hasattr(self.repository, "seed_ontology"):
            return {}
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
        if int(snapshot.get("ruleCount") or 0) > 0 and str(snapshot.get("status") or "") == "ok":
            return {"status": "ready", "ruleCount": int(snapshot.get("ruleCount") or 0)}
        if str(snapshot.get("status") or "") != "empty":
            return {
                "status": "not-ready",
                "reason": str(snapshot.get("reason") or snapshot.get("status") or "RuleBox is not ready."),
                "ruleCount": int(snapshot.get("ruleCount") or 0),
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
            })
        except Exception as error:  # noqa: BLE001 - graph inference must not block monitoring.
            execution = {"status": "error", "reason": str(error)[:180]}
        active_key = self.active_graph_store_key(result)
        if isinstance(execution, dict):
            execution.setdefault("graphStore", active_key)
            if active_key == "typedb":
                execution.setdefault("source", "typedbRuleBox")
        else:
            execution = {"status": "error", "reason": "non-dict RuleBox result", "graphStore": active_key}
        result["ruleboxExecution"] = execution
        if not hasattr(self.repository, "inferencebox_snapshot"):
            return
        try:
            snapshot_payload = self.repository.inferencebox_snapshot(symbols=self.snapshot_symbols(snapshot), limit=80)
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

    def has_projectable_data(self, snapshot: AccountSnapshot) -> bool:
        if snapshot.has_live_account_data():
            return True
        if any(item for item in snapshot.watchlist or [] if not item.is_cash()):
            return True
        if isinstance(snapshot.external_signals, dict) and any(
            value not in ({}, [], "", None, False)
            for key, value in snapshot.external_signals.items()
            if key not in {"quality", "freshness", "provenance", "statuses"}
        ):
            return True
        return False

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
        return {
            "settings": dict(self.settings),
            "snapshotId": "abox-snapshot:" + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:16],
            "asOf": as_of,
            "activeTBox": active_tbox,
            "account": {
                "accountId": snapshot.account_id,
                "accountLabel": snapshot.account_label,
                "provider": snapshot.provider,
                "mode": snapshot.mode,
                "status": snapshot.status,
            },
            "metadata": dict(snapshot.metadata or {}),
            "decisionItems": [item.to_dict() for item in snapshot.decisions],
        }
