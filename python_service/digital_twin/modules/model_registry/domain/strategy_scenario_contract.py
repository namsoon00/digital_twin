"""Authored continuity of native strategy observations, without trading authority."""
from copy import deepcopy
from dataclasses import replace

VERSION = "strategy-scenario-continuity-v1"
STATES = {
    "unobserved": "이전 확인 없음", "observing": "회복 관찰",
    "entry-review": "진입 조건 확인", "invalidated": "진입 취소 조건 확인",
    "exit-review": "종료 검토 조건 확인", "conflicted": "회복·취소 근거 충돌",
    "reentry-review": "재진입 조건 재검토",
}


def scenario_contract():
    """Freeze every transition at release authoring, not in notification code.

    A later entry observation does not prove an earlier ending condition false.
    Reentry therefore remains a review and carries that unresolved boundary.
    """
    transitions = []
    for previous in STATES:
        for signal in ("setup", "entry", "invalidation", "exit", "conflict"):
            state, kind, meaning = previous, "maintained", "이전에 확인한 상태를 유지합니다."
            if signal == "conflict":
                state, meaning = "conflicted", "회복 조건과 취소·종료 조건이 함께 확인돼 방향을 확정할 수 없습니다."
            elif signal in {"invalidation", "exit"}:
                state = "invalidated" if signal == "invalidation" else "exit-review"
                meaning = ("반등 실패 조건이 확인돼 앞서 확인한 진입 근거를 다시 검토해야 합니다." if signal == "invalidation"
                           else "장중 하락 가속이 확인돼 앞서 확인한 진입 근거를 다시 검토해야 합니다.")
                if previous == "unobserved":
                    meaning = ("반등 실패 조건이 확인됐습니다. 연결할 이전 진입 확인 이력은 없습니다." if signal == "invalidation"
                               else "장중 하락 가속이 확인됐습니다. 연결할 이전 진입 확인 이력은 없습니다.")
                elif previous not in {"entry-review", "reentry-review"}:
                    meaning = ("반등 실패 조건이 확인됐습니다. 앞서 확인한 조건과 함께 회복 가능성을 다시 점검해야 합니다." if signal == "invalidation"
                               else "장중 하락 가속 조건이 확인됐습니다. 앞서 확인한 조건과 함께 회복 가능성을 다시 점검해야 합니다.")
                if previous == "conflicted":
                    state = previous  # A missing entry proof cannot resolve a conflict.
            elif signal == "entry":
                ending = previous in {"invalidated", "exit-review", "conflicted", "reentry-review"}
                state = "reentry-review" if ending else "entry-review"
                meaning = ("이전 취소·충돌 이후 진입 조건이 다시 확인됐습니다. 이전 취소 조건의 해제는 아직 확인되지 않았습니다."
                           if ending else "앞서 관찰한 회복 흐름에 가격 회복과 장중 반전 조건이 추가로 확인됐습니다."
                           if previous == "observing" else "가격 회복과 장중 반전 조건이 함께 확인됐습니다.")
            elif previous == "unobserved":
                state, meaning = "observing", "하락 둔화가 확인돼 회복 여부를 관찰하기 시작합니다."
            if state != previous:
                kind = "initial" if previous == "unobserved" else "changed"
            transitions.append({"from": previous, "signal": signal, "to": state, "kind": kind,
                                "meaning": meaning if kind != "maintained" else "이전에 확인한 상태를 유지합니다."})
    return {"version": VERSION, "states": dict(STATES), "transitions": transitions,
            "signalPriority": ["conflict", "exit", "invalidation", "entry", "setup"],
            "sequenceWindow": "same-regular-session", "sessionBoundary": "new-baseline-with-history",
            "observationGap": "preserve-history-without-assuming-continuity",
            "absenceMeaning": "unknown-not-false", "historyAuthority": "native-proof-not-delivery",
            "reentryAuthority": "review-with-unresolved-ending-conditions",
            "tradingAuthority": "none"}


def scenario_contract_valid(value):
    # V1 is a reviewed finite contract. A different policy needs a new version.
    return value == scenario_contract()


def scenario_successor_rules(source_rules):
    """Copy exact V2 predicates into additive V3 rules with frozen continuity."""
    from .rule_strategy_contract import SCENARIO_VERSION, SCENARIO_COMPILER_VERSION, fingerprint
    from .rule_strategy_compilation import compile_strategy_rules
    from .ontology_rulebox_contracts import GraphInferenceRule
    contracts = {}
    for raw in source_rules:
        rule = raw if isinstance(raw, GraphInferenceRule) else GraphInferenceRule.from_dict(raw)
        contract = rule.resolved_claim_contract.strategy_contract or {}
        if rule.enabled and contract.get("version") == "rule-strategy-contract-v2":
            contracts[contract["contractFingerprint"]] = contract
    if {c["market"] for c in contracts.values()} != {"KR", "US"} or len(contracts) != 2:
        raise ValueError("exact KR and US operational strategy predecessors required")
    result = []
    for predecessor in sorted(contracts.values(), key=lambda c: c["market"]):
        contract = deepcopy(predecessor)
        contract.update(version=SCENARIO_VERSION, compilerVersion=SCENARIO_COMPILER_VERSION,
                        scenarioContract=scenario_contract(), predecessorContractFingerprint=predecessor["contractFingerprint"])
        contract.pop("contractFingerprint")
        contract["contractFingerprint"] = fingerprint(contract)
        result.extend(replace(r, enabled=True) for r in compile_strategy_rules(contract))
    return result
