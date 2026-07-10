from typing import Dict, List, Set

from ..domain.ontology import build_portfolio_ontology
from ..domain.ontology_contracts import PortfolioOntology
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
            persistence_graph = self.graph_for_neo4j_persistence(graph)
            result = self.repository.save_graph(persistence_graph)
            if not isinstance(result, dict):
                result = {"saved": False, "status": "error", "reason": "ontology repository returned non-dict result"}
            result["projectionMode"] = "abox-first-neo4j-rulebox"
            if result.get("saved"):
                self.attach_neo4j_inference_result(result, snapshot)
            if self.quality_store:
                sample = self.quality_store.record_graph(graph, source=self.source)
                result["qualitySampleId"] = sample.sample_id
                result["qualityScore"] = sample.overall_score
        except Exception as error:  # noqa: BLE001 - ontology projection must not block realtime monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
        snapshot.metadata.setdefault("ontology", {})["neo4j"] = result
        return result

    def graph_for_neo4j_persistence(self, graph: PortfolioOntology) -> PortfolioOntology:
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
            opinions=list(graph.opinions or []),
            reasoning_cards=list(graph.reasoning_cards or []),
            worldview={**dict(graph.worldview or {}), "runtimeProjectionMode": "abox-first-neo4j-rulebox"},
            prompt=graph.prompt,
        )

    def attach_neo4j_inference_result(self, result: Dict[str, object], snapshot: AccountSnapshot) -> None:
        if not hasattr(self.repository, "run_rulebox"):
            return
        try:
            execution = self.repository.run_rulebox({"clearInference": True})
        except Exception as error:  # noqa: BLE001 - Neo4j inference must not block monitoring.
            execution = {"status": "error", "reason": str(error)[:180]}
        result["ruleboxExecution"] = execution if isinstance(execution, dict) else {"status": "error", "reason": "non-dict RuleBox result"}
        if not hasattr(self.repository, "inferencebox_snapshot"):
            return
        try:
            snapshot_payload = self.repository.inferencebox_snapshot(symbols=self.snapshot_symbols(snapshot), limit=80)
        except Exception as error:  # noqa: BLE001 - snapshot read is best effort.
            snapshot_payload = {"status": "error", "reason": str(error)[:180]}
        if isinstance(snapshot_payload, dict):
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

    def runtime_context(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        return {
            "settings": dict(self.settings),
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
