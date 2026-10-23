"""Authored production phase contracts; empirical qualification stays separate.

These are explicit RuleBox release contents, not a runtime Python selector.
Existing immutable releases keep their original rule rows.
"""
from dataclasses import replace

from .rule_strategy_contract import (
    LIVE_VERSION, LIVE_COMPILER_VERSION, fingerprint, parse_strategy_contract,
    strategy_authoring_catalog, strategy_from_design,
)
from .rule_strategy_compilation import compile_strategy_rules
from .ontology_rulebox_contracts import GraphInferenceRule


def operational_strategy_rules(source_rules, source_deployment=None):
    rows = [r.to_dict() if hasattr(r, "to_dict") else r for r in source_rules]
    rows = [r for r in rows if not (r.get("claim_contract") or r.get("claimContract") or {}).get("strategyContract")]
    rows = sorted((GraphInferenceRule.from_dict(r).to_dict() for r in rows), key=lambda r: r["rule_id"])
    catalog = strategy_authoring_catalog({"rules": rows}, source_deployment)
    phases = {
        "setup": ["graph.temporal.decline_deceleration.defense.v1"],
        "entry": ["graph.price.reclaim.thesis_support.v1", "graph.temporal.intraday_reversal.defense.v1"],
        "invalidation": ["graph.price.rebound.failure.v1"],
        "exit": ["graph.temporal.intraday_downside_acceleration.risk.v1"],
    }
    result = []
    for market, label in (("KR", "국내 주식 단기 회복"), ("US", "미국 주식 단기 회복")):
        contract = strategy_from_design({"market": market, "maximumHoldingSessions": 3,
            "signalValiditySeconds": 600, "phases": phases}, catalog, label)
        contract.update(version=LIVE_VERSION, compilerVersion=LIVE_COMPILER_VERSION, mode="live-observation")
        contract.pop("contractFingerprint")
        contract["contractFingerprint"] = fingerprint(contract)
        result.extend(replace(r, enabled=True) for r in compile_strategy_rules(parse_strategy_contract(contract)))
    return result
