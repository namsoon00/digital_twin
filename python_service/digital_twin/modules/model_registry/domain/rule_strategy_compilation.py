"""Compile a frozen strategy into native TypeDB observation rules.

All predicates execute in TypeDB. No Python price threshold or action selector
is introduced here. Missing phases mean unknown, never a cancellation or exit.
"""
from copy import deepcopy
from dataclasses import replace

from .rule_strategy_contract import VERSION, LIVE_VERSION, SCENARIO_VERSION, PHASES, parse_strategy_contract, strategy_from_design


def phase_rule_id(contract, phase):
    suffix = {VERSION: ".v1", LIVE_VERSION: ".v2", SCENARIO_VERSION: ".v3"}[contract["version"]]
    return "graph.strategy." + contract["contractFingerprint"][:24] + "." + phase + suffix


def phase_conditions(contract, phase):
    from .ontology_rulebox_contracts import GraphRuleCondition
    from digital_twin.modules.reasoning.contracts import entity_id
    # The slim deployed Stock schema does not own ontology-market. The existing
    # TRADED_IN relation binds the market's stable identity without schema edits.
    result = [GraphRuleCondition("strategy-market", "relation", "전략 대상 시장",
              relation_type="TRADED_IN", target_kind="market",
              hypothesis_scope="market" if contract["version"] in {LIVE_VERSION, SCENARIO_VERSION} else "",
              target_property_filters={"entityId": entity_id("market", contract["market"])})]
    for index, rid in enumerate(contract["phases"][phase]):
        source = deepcopy(contract["models"][rid]["condition"])
        source["condition_id"] = "strategy-model-" + str(index + 1)
        # Exact contract nodes originate only from supported model assessments;
        # their native eligibility filter already excludes stale family inputs.
        # Bind only promoted TypeDB fields; JSON-only booleans are not predicates.
        source["target_property_filters"].update(marketSession=contract["session"],
            sourceAgeSeconds={"operator": "<=", "value": contract["signalValiditySeconds"]})
        result.append(GraphRuleCondition.from_dict(source))
    return result


def compile_strategy_rules(contract):
    from .ontology_rulebox_contracts import GraphInferenceRule, GraphRuleDerivation
    from .ontology_rulebox_catalog import with_rulebox_v3_governance
    from .ontology_rule_knowledge import RuleKnowledgeBasis
    contract = parse_strategy_contract(contract)
    rules = []
    for phase, label in PHASES.items():
        rid = phase_rule_id(contract, phase)
        conditions = phase_conditions(contract, phase)
        response = {"version": contract["version"], "strategyId": contract["strategyId"],
                    "contractFingerprint": contract["contractFingerprint"], "phase": phase, "label": label,
                    "mode": contract["mode"], "conditionIds": [c.condition_id for c in conditions],
                    "sourceModelRuleIds": list(contract["phases"][phase]),
                    "maximumHoldingSessions": contract["maximumHoldingSessions"],
                    "signalValiditySeconds": contract["signalValiditySeconds"]}
        live = contract["version"] in {LIVE_VERSION, SCENARIO_VERSION}
        if live:
            response.update(strategyLabel=contract["label"], nextCheck={
                "setup": "가격 회복과 장중 상승 전환이 함께 확인되는지 확인합니다.",
                "entry": "반등 실패 또는 장중 하락 가속이 확인되면 진입 근거를 다시 판단합니다.",
                "invalidation": "기존 진입 근거의 취소 조건입니다. 새 가격 회복과 장중 상승 전환을 기다립니다.",
                "exit": "보유 중이면 단기 회복 전략의 종료를 검토할 조건입니다. 보유하지 않으면 하락 위험 참고입니다.",
            }[phase])
        raw = GraphInferenceRule(rule_id=rid, label=contract["label"] + " · " + label, version=contract["version"],
            source_kind="stock", action_group="context", action_level="review", conditions=conditions,
            knowledge_basis=RuleKnowledgeBasis(rule_kind="context-observation", theory_family="strategy-phase-composition" if live else "strategy-research",
                thesis_family="strategy-phase-observation", basis_origin="authored-strategy-contract",
                threshold_origin="frozen-model-contract", validation_status="reference-only",
                decision_eligibility="reference-only", evidence_independence_key=contract["contractFingerprint"],
                plain_language_basis=("등록된 모델 조건을 함께 확인한 단기 전략 단계입니다. 성과 검증 상태와 구분합니다." if live else "등록된 모델 조건의 조합을 연구하며 매매 행동을 선택하지 않습니다.")),
            prompt_hint=("단기 전략의 현재 단계, 확인 수치와 취소 조건을 설명합니다. 매매 성과는 검증 중입니다." if live else "연구용 전략의 조건 관측입니다. 진입·청산 지시 또는 매매 성과 검증으로 해석하지 않습니다."),
            enabled=False, derivations=[GraphRuleDerivation(
                relation_type="REQUIRES_NEXT_CHECK", target_kind="next-check", target_key="{subjectId}:" + rid,
                target_label="{displayName} " + label, tbox_class="NextCheck", tbox_classes=["NextCheck"],
                polarity="context", evidence_role="context", decision_effect="defer", action_group="context",
                action_level="review", decision_stage="MACRO_REGIME", decision_label=label,
                next_checks=[response.get("nextCheck") or "새 자료에서 전략의 나머지 조건과 반대 근거를 함께 확인합니다."],
                strategy_response=response)])
        rule = with_rulebox_v3_governance([raw])[0]
        claim = replace(rule.resolved_claim_contract, claim_type="causal-context", decision_authority="reference-only",
                        strategy_contract=deepcopy(contract), strategy_phase=phase)
        rules.append(replace(rule, claim_contract=claim))
    return rules


def strategy_rule_violations(rule):
    from .rule_strategy_contract import fingerprint
    contract, phase = rule.resolved_claim_contract.strategy_contract, rule.resolved_claim_contract.strategy_phase
    responses = [d.strategy_response for d in rule.derivations if d.strategy_response]
    if contract is None:
        return ["strategy response has no frozen strategy contract"] if responses else []
    errors = []
    if phase not in PHASES:
        return ["unknown strategy phase"]
    if rule.rule_id != phase_rule_id(contract, phase):
        errors.append("strategy rule identity does not match the frozen contract")
    def predicate(c):
        values = {k: v for k, v in c.to_dict().items() if k not in {"description", "hypothesis_scope", "evidence_group_key"}}
        # TypeDB's legacy reader omits these optional routing flags. Absence
        # means true by the condition contract; an explicit false still differs.
        values.setdefault("change_trigger", True)
        values.setdefault("invalidation_trigger", True)
        return values
    if [predicate(c) for c in rule.conditions] != [predicate(c) for c in phase_conditions(contract, phase)]:
        errors.append("strategy predicates differ from the frozen model bindings")
    expected = compile_strategy_rules(contract)[list(PHASES).index(phase)]
    if fingerprint([d.to_dict() for d in rule.derivations]) != fingerprint([d.to_dict() for d in expected.derivations]):
        errors.append("strategy observation derivation cannot grant actions or change phase semantics")
    return errors


def assemble_strategy_design(candidate, catalog):
    if candidate.get("proposedRule") is not None or candidate.get("hypothesisDesign") is not None:
        raise ValueError("strategyDesign cannot override executable rules or mix authoring modes")
    contract = strategy_from_design(candidate["strategyDesign"], catalog, candidate.get("title") or "단기 전략 연구")
    rules = compile_strategy_rules(contract)
    entry = next(r for r in rules if r.resolved_claim_contract.strategy_phase == "entry")
    return {**candidate, "proposedRule": entry.to_dict(), "action": "research-strategy-bundle",
            "validationRequirements": [{"check": "review", "requirement": name, "dependencyKey": "strategy:" + name}
                                       for name in contract["validationRequirements"]]}


def strategy_phase_evaluation(contract, validation):
    """Expose query coverage separately from whether any subject matched."""
    native = validation.get("nativeMatchResult") or {}
    executed = {r.get("ruleId"): r for r in native.get("executedRules") or []}
    skipped = {r.get("ruleId"): r for r in native.get("skippedRules") or []}
    phases = []
    for phase, label in PHASES.items():
        rid = phase_rule_id(contract, phase)
        row = executed.get(rid) or skipped.get(rid) or {}
        phases.append({"phase": phase, "label": label, "ruleId": rid,
            "nativeQueryExecuted": rid in executed,
            "status": (row.get("status") or "executed") if rid in executed else (row.get("status") or "unknown"),
            "matchedCount": row.get("matchedCount", row.get("rowCount")) if rid in executed else None,
            "reason": row.get("reason") or "",
            "requiredRelationTypes": row.get("requiredRelationTypes") or []})
    count = sum(p["nativeQueryExecuted"] for p in phases)
    return {"status": "evaluated" if count == len(PHASES) else "partially-evaluated" if count else "not-evaluated",
            "executedPhaseCount": count, "phaseCount": len(PHASES), "phases": phases,
            "missingPhaseMeaning": "unknown-not-false", "tradeTimingQualified": False}
