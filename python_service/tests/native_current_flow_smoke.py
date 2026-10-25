"""Opt-in source -> model -> ABox -> native strategy proof, with no delivery.

Run with PYTHONPATH=python_service:python_service/tests python3
python_service/tests/native_current_flow_smoke.py. Deletes its own temporary DB.
"""
import json

from test_current_flow_rules import position, graph_and_score
from native_strategy_smoke import run
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.model_registry.domain.current_flow_strategy import current_flow_strategy_rules
from digital_twin.modules.reasoning.domain.portfolio_ontology_statistical_concepts import add_position_statistical_signal_concepts


def source_cases():
    cases = {}
    for name in ("source-recovery", "source-divergence", "source-stale", "source-missing",
                 "source-trade-unusable", "source-late-receipt", "source-empty-fields"):
        p = position()
        expected = [1, 1, 0, 0]
        if name == "source-divergence":
            p.foreign_net_volume = -1000; p.institution_net_volume = -500; p.individual_net_volume = 1500
            expected = [0, 0, 1, 1]
        elif name == "source-trade-unusable":
            p.market_signal_coverage["ccnl"]["tradeStrengthDecisionUsable"] = False
            expected = [1, 0, 0, 0]
        elif name != "source-recovery":
            key, value = {
                "source-stale": ("sourceAsOf", "2026-10-23T00:00:00Z"),
                "source-missing": ("sourceAsOf", ""),
                "source-late-receipt": ("fetchedAt", "2026-10-23T01:02:00Z"),
                "source-empty-fields": ("observedFields", []),
            }[name]
            p.market_signal_coverage["investor"][key] = value
            expected = [0, 0, 0, 0]
        graph, snapshot = graph_and_score(p)
        stock = next(e for e in graph.entities if e.kind == "stock")
        add_position_statistical_signal_concepts(graph, stock.entity_id, p.symbol,
                                                {"statisticalSignalSnapshot": snapshot.to_dict()})
        evidence = {e.properties["hypothesisContractId"]: e.properties for e in graph.entities
                    if e.kind == "statistical-model-hypothesis-evidence"}
        cases[name] = {"evidence": evidence, "expected": expected}
    return cases


if __name__ == "__main__":
    phases = current_flow_strategy_rules(default_graph_inference_rules())
    contract = phases[0].resolved_claim_contract.strategy_contract
    print(json.dumps(run(contract, source_cases()), ensure_ascii=False))
