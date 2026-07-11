import hashlib
from dataclasses import fields
from typing import Dict, Iterable, List

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_experiments import (
    OntologyExperiment,
    clean_symbols,
    compact_rulebox_snapshot,
    experiment_id_for,
    normalize_candidate_rules,
    rule_payloads_from_snapshot,
    run_experiment_on_graph,
    summarize_experiment_result,
)
from ..domain.portfolio import AccountSnapshot, DecisionItem, PortfolioSummary, Position, utc_now_iso
from ..domain.portfolio_ontology_builder import build_portfolio_ontology


class OntologyLabService:
    def __init__(
        self,
        ontology_repository,
        experiment_store,
        monitor_store=None,
        settings: Dict[str, object] = None,
    ):
        self.ontology_repository = ontology_repository
        self.experiment_store = experiment_store
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})

    def list(self) -> Dict[str, object]:
        experiments = self.experiment_store.list()
        return {
            "experiments": [item.to_dict() for item in experiments],
            "count": len(experiments),
        }

    def create(self, payload: Dict[str, object]) -> Dict[str, object]:
        body = dict(payload or {})
        rulebox = self.rulebox_snapshot()
        candidate_rules, warnings = normalize_candidate_rules(body, rulebox)
        stamp = utc_now_iso()
        experiment = OntologyExperiment(
            experiment_id=str(body.get("id") or body.get("experimentId") or "") or experiment_id_for(body, stamp),
            title=str(body.get("title") or "Ontology Lab Experiment"),
            hypothesis=str(body.get("hypothesis") or ""),
            symbols=clean_symbols(body.get("symbols") or []),
            candidate_rules=candidate_rules,
            baseline_rulebox=compact_rulebox_snapshot(rulebox),
            status="draft",
            created_at=stamp,
            updated_at=stamp,
            validation_warnings=warnings,
        )
        self.experiment_store.save(experiment)
        return {"experiment": experiment.to_dict()}

    def report(self, experiment_id: str) -> Dict[str, object]:
        experiment = self.experiment_store.get(experiment_id)
        if not experiment:
            return {"status": "not-found", "id": experiment_id}
        return {"experiment": experiment.to_dict()}

    def run(self, experiment_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        experiment = self.experiment_store.get(experiment_id)
        if not experiment:
            return {"status": "not-found", "id": experiment_id}
        payload = dict(payload or {})
        symbols = clean_symbols(payload.get("symbols") or experiment.symbols or [])
        if symbols and symbols != experiment.symbols:
            experiment.symbols = symbols
        rulebox = self.rulebox_snapshot()
        baseline_rules = rule_payloads_from_snapshot(rulebox)
        graph_runs = []
        for graph in self.facts_graphs(symbols=symbols):
            graph_runs.append(run_experiment_on_graph(graph, baseline_rules, experiment.candidate_rules))
        result = summarize_experiment_result(experiment, baseline_rules, graph_runs)
        result["baselineRulebox"] = compact_rulebox_snapshot(rulebox)
        experiment.status = "completed"
        experiment.updated_at = utc_now_iso()
        experiment.last_result = result
        self.experiment_store.save(experiment)
        return {"experiment": experiment.to_dict(), "result": result}

    def rulebox_snapshot(self) -> Dict[str, object]:
        if not self.ontology_repository or not hasattr(self.ontology_repository, "rulebox_snapshot"):
            return {}
        snapshot = self.ontology_repository.rulebox_snapshot()
        return snapshot if isinstance(snapshot, dict) else {}

    def facts_graphs(self, symbols: Iterable[str] = None) -> List[object]:
        snapshots = self.monitor_snapshots(symbols)
        graphs = []
        for snapshot in snapshots:
            graph = build_portfolio_ontology(
                list(snapshot.positions or []) + list(snapshot.watchlist or []),
                snapshot.portfolio,
                legacy_by_symbol={item.symbol.upper(): item.to_dict() for item in snapshot.decisions},
                external_signals=snapshot.external_signals,
                portfolio_id=snapshot.account_id,
                runtime_context=self.runtime_context(snapshot),
                include_reasoning_outputs=False,
            )
            graphs.append(facts_only_graph(graph))
        return graphs

    def runtime_context(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        active_tbox = {}
        if self.ontology_repository and hasattr(self.ontology_repository, "active_tbox_metadata"):
            try:
                active_tbox = self.ontology_repository.active_tbox_metadata()
            except Exception as error:  # noqa: BLE001 - lab runs should report through results, not crash on metadata.
                active_tbox = {"status": "error", "reason": str(error)[:180], "source": "code-fallback"}
        as_of = str(snapshot.generated_at or "").strip()
        snapshot_seed = "|".join([str(snapshot.account_id or ""), as_of or "unknown"])
        return {
            "settings": dict(self.settings),
            "snapshotId": "lab-abox-snapshot:" + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:16],
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

    def monitor_snapshots(self, symbols: Iterable[str] = None) -> List[AccountSnapshot]:
        if not self.monitor_store or not hasattr(self.monitor_store, "previous"):
            return []
        allowed = set(clean_symbols(symbols or []))
        snapshots = []
        for state in (self.monitor_store.previous or {}).values():
            snapshot = account_snapshot_from_monitor_state(state)
            if not snapshot:
                continue
            if allowed and not snapshot_has_symbol(snapshot, allowed):
                continue
            snapshots.append(snapshot)
        return snapshots


def snapshot_has_symbol(snapshot: AccountSnapshot, symbols: set) -> bool:
    for item in list(snapshot.positions or []) + list(snapshot.watchlist or []):
        if str(item.symbol or "").upper().strip() in symbols:
            return True
    return False


def account_snapshot_from_monitor_state(state: Dict[str, object]) -> AccountSnapshot:
    if not isinstance(state, dict) or not isinstance(state.get("portfolio"), dict):
        return None
    portfolio = dataclass_from_dict(PortfolioSummary, state.get("portfolio") or {})
    positions = positions_from_map(state.get("positions"))
    watchlist = positions_from_map(state.get("watchlist"))
    decisions = decisions_from_map(state.get("decisions"))
    return AccountSnapshot(
        account_id=str(state.get("accountId") or state.get("account_id") or "portfolio"),
        account_label=str(state.get("accountLabel") or state.get("account_label") or "투자 계좌"),
        provider=str(state.get("provider") or ""),
        mode=str(state.get("mode") or ""),
        status=str(state.get("status") or ""),
        generated_at=str(state.get("generatedAt") or state.get("generated_at") or ""),
        portfolio=portfolio,
        positions=positions,
        decisions=decisions,
        external_signals=dict(state.get("externalSignals") or {}),
        watchlist=watchlist,
        metadata=dict(state.get("metadata") or {}),
    )


def positions_from_map(value: object) -> List[Position]:
    if isinstance(value, dict):
        rows = value.values()
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    return [dataclass_from_dict(Position, item) for item in rows if isinstance(item, dict)]


def decisions_from_map(value: object) -> List[DecisionItem]:
    if isinstance(value, dict):
        rows = value.values()
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    return [dataclass_from_dict(DecisionItem, item) for item in rows if isinstance(item, dict)]


def dataclass_from_dict(cls, payload: Dict[str, object]):
    payload = dict(payload or {})
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in payload.items() if key in allowed})


def facts_only_graph(graph: PortfolioOntology) -> PortfolioOntology:
    stripped_boxes = {"RuleBox", "InferenceBox"}
    stripped_ids = set()
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
        worldview={**dict(graph.worldview or {}), "runtimeProjectionMode": "ontology-lab-facts-only"},
        prompt=graph.prompt,
    )
