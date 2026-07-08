from typing import Dict

from ..domain.ontology import build_portfolio_ontology
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
        if not self.repository or not snapshot.has_live_account_data():
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
            result = self.repository.save_graph(graph)
            if not isinstance(result, dict):
                result = {"saved": False, "status": "error", "reason": "ontology repository returned non-dict result"}
            if self.quality_store:
                sample = self.quality_store.record_graph(graph, source=self.source)
                result["qualitySampleId"] = sample.sample_id
                result["qualityScore"] = sample.overall_score
        except Exception as error:  # noqa: BLE001 - ontology projection must not block realtime monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
        snapshot.metadata.setdefault("ontology", {})["neo4j"] = result
        return result

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
