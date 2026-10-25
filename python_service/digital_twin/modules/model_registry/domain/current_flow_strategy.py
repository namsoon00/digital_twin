"""A separately versioned KR flow-confirmed recovery strategy."""
from dataclasses import replace

from .current_flow_rules import SUFFIX
from .rule_strategy_contract import (SCENARIO_VERSION, SCENARIO_COMPILER_VERSION,
    fingerprint, strategy_authoring_catalog, strategy_from_design)
from .rule_strategy_compilation import compile_strategy_rules
from .strategy_scenario_contract import flow_scenario_contract


def current_flow_strategy_rules(rules, source_deployment=None):
    rows = [r.to_dict() if hasattr(r, "to_dict") else r for r in rules]
    catalog = strategy_authoring_catalog({"rules": rows}, source_deployment)
    recovery = "graph.flow.recovery_confirmed_by_smart_money.v1" + SUFFIX
    contract = strategy_from_design({"market": "KR", "maximumHoldingSessions": 1,
        "signalValiditySeconds": 600, "phases": {
            "setup": [recovery],
            "entry": [recovery, "graph.flow.accumulation.entry.v1" + SUFFIX],
            "invalidation": ["graph.flow.price_up_smart_money_outflow.divergence.v1" + SUFFIX],
            "exit": ["graph.investor_flow.smart_money_outflow_risk.v1" + SUFFIX],
        }}, catalog, "국내 주식 평균 가격·수급 확인")
    predecessor = contract["contractFingerprint"]
    contract.update(version=SCENARIO_VERSION, compilerVersion=SCENARIO_COMPILER_VERSION,
        mode="live-observation", scenarioContract=flow_scenario_contract(),
        predecessorContractFingerprint=predecessor)
    contract.pop("contractFingerprint")
    contract["contractFingerprint"] = fingerprint(contract)
    return [replace(r, enabled=True) for r in compile_strategy_rules(contract)]
