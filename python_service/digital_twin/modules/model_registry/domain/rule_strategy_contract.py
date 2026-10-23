"""Frozen research strategies owned by RuleBox, never by alert wording.

V1 deliberately grants no trading authority. Native phase matches are research
observations; a complete trade/cost/calendar validation needs a later release.
"""
from copy import deepcopy
import hashlib
import json

from .statistical_signals.registry import DEFAULT_PRICE_SIGNAL_RELEASE_ID, DEFAULT_FLOW_SIGNAL_RELEASE_ID


VERSION = "rule-strategy-contract-v1"
COMPILER_VERSION = "native-strategy-phase-v1"
# graph_scoring supplies source age and session only from these family scorers.
# Contract-only corporate/event/cross-asset evidence has no intraday source clock.
INTRADAY_CLOCK_RELEASES = {DEFAULT_PRICE_SIGNAL_RELEASE_ID, DEFAULT_FLOW_SIGNAL_RELEASE_ID}
PHASES = {"setup": "관찰 조건", "entry": "진입 검토 조건", "invalidation": "진입 판단 취소 조건", "exit": "보유 종료 검토 조건"}
CHECKS = ["point-in-time-replay", "independent-out-of-sample", "cost-adjusted-returns",
          "market-regime-comparison", "forward-paper-trades", "trading-session-clock", "delivery-latency"]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parse_strategy_contract(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("strategyContract must be an object")
    issues = strategy_contract_violations(value)
    if issues:
        raise ValueError("strategyContract: " + "; ".join(issues))
    return deepcopy(value)


def strategy_contract_violations(contract):
    expected = {"version", "compilerVersion", "strategyId", "label", "mode", "sourceRuleboxFingerprint", "sourceDeployment", "market",
                "session", "maximumHoldingSessions", "signalValiditySeconds", "phases", "models",
                "validationRequirements", "missingDataPolicy", "costPolicy", "contractFingerprint"}
    if not isinstance(contract, dict) or set(contract) != expected:
        return ["complete strategy fields are required"]
    errors = []
    if (contract["version"] != VERSION or contract["compilerVersion"] != COMPILER_VERSION
            or contract["mode"] != "research-only"):
        errors.append("V1 is research-only")
    for key in ("strategyId", "label", "sourceRuleboxFingerprint"):
        if not isinstance(contract[key], str) or not 1 <= len(contract[key]) <= 240:
            errors.append("invalid " + key)
    binding = contract["sourceDeployment"]
    if (not isinstance(binding, dict) or (binding and (set(binding) != {"deploymentId", "graphStoreBinding"}
            or any(not isinstance(v, str) or not v for v in binding.values())))):
        errors.append("invalid source deployment binding")
    if contract["market"] not in {"KR", "US"} or contract["session"] != "regular":
        errors.append("explicit supported market and regular session required")
    for key, low, high in (("maximumHoldingSessions", 1, 5), ("signalValiditySeconds", 30, 3600)):
        if type(contract[key]) is not int or not low <= contract[key] <= high:
            errors.append("invalid " + key)
    if (contract["missingDataPolicy"] != "unknown-not-false" or contract["costPolicy"] != "fees-taxes-spread-slippage"
            or contract["validationRequirements"] != CHECKS):
        errors.append("validation, cost and missing-data boundaries cannot be weakened")
    phases, models = contract["phases"], contract["models"]
    if not isinstance(phases, dict) or set(phases) != set(PHASES) or not isinstance(models, dict) or not 1 <= len(models) <= 16:
        return errors + ["all four phases and frozen source models required"]
    selected = set()
    for phase, ids in phases.items():
        if not isinstance(ids, list) or not 1 <= len(ids) <= 4 or any(not isinstance(i, str) for i in ids):
            errors.append("invalid model references for " + phase)
            continue
        if len(ids) != len(set(ids)) or not set(ids).issubset(models):
            errors.append("unknown or duplicate model references for " + phase)
        selected.update(ids)
    if selected != set(models):
        errors.append("unused or missing frozen models")
    if all(isinstance(ids, list) and all(isinstance(i, str) for i in ids) for ids in phases.values()) and (
            set(phases["entry"]) == set(phases["invalidation"]) or set(phases["entry"]) == set(phases["exit"])):
        errors.append("entry and ending conditions must differ")
    for rid, model in models.items():
        if not isinstance(model, dict) or set(model) != {"ruleFingerprint", "claimContract", "condition"}:
            errors.append("incomplete frozen model " + str(rid))
            continue
        claim, condition = model["claimContract"], model["condition"]
        if not isinstance(claim, dict) or not isinstance(condition, dict):
            errors.append("invalid model payload")
            continue
        filters = condition.get("target_property_filters") or {}
        if (claim.get("ruleId") != rid or claim.get("claimType") != "market-hypothesis"
                or not claim.get("outcomeContract") or condition.get("relation_type") != "HAS_MODEL_SIGNAL"
                or condition.get("target_kind") != "statistical-model-hypothesis-evidence"
                or condition.get("role") != "required" or not isinstance(filters, dict)
                or filters.get("hypothesisContractId") != rid or filters.get("releaseId") not in INTRADAY_CLOCK_RELEASES
                or filters.get("eligibilityStatus") not in ("eligible", "conditional", ["eligible", "conditional"])):
            errors.append("exact fresh predictive model binding required: " + str(rid))
    try:
        if fingerprint({k: v for k, v in contract.items() if k != "contractFingerprint"}) != contract["contractFingerprint"]:
            errors.append("strategy fingerprint mismatch")
    except (TypeError, ValueError):
        errors.append("strategy contains non-canonical values")
    return errors


def strategy_authoring_catalog(rulebox, source_deployment=None):
    """Offer existing exact model contracts, without inventing features or scores."""
    from .ontology_rulebox_contracts import GraphInferenceRule
    models, unavailable = {}, []
    for raw in rulebox.get("rules") or []:
        rule = GraphInferenceRule.from_dict(raw)
        if not rule.resolved_claim_contract.is_predictive:
            continue
        if not rule.enabled:
            unavailable.append({"ruleId": rule.rule_id, "reason": "disabled-rule"})
            continue
        exact = [c for c in rule.conditions if c.relation_type == "HAS_MODEL_SIGNAL"
                 and c.target_kind == "statistical-model-hypothesis-evidence"
                 and c.target_property_filters.get("hypothesisContractId") == rule.rule_id
                 and c.target_property_filters.get("releaseId")
                 and c.role == "required"]
        if len(exact) == 1:
            if exact[0].target_property_filters.get("releaseId") not in INTRADAY_CLOCK_RELEASES:
                unavailable.append({"ruleId": rule.rule_id, "reason": "intraday-source-clock-unavailable"})
                continue
            models[rule.rule_id] = {"label": rule.label, "ruleFingerprint": fingerprint(rule.to_dict()),
                "claimContract": rule.resolved_claim_contract.to_dict(), "condition": exact[0].to_dict()}
        else:
            unavailable.append({"ruleId": rule.rule_id, "reason": "exact-model-binding-unavailable"})
    return {"version": VERSION, "mode": "research-only", "models": models,
            "sourceDeployment": deepcopy(source_deployment or {}),
            "unavailableModels": unavailable,
            "phases": PHASES, "sourceRuleboxFingerprint": fingerprint(rulebox.get("rules") or []),
            "requiredValidation": CHECKS, "maximumHoldingSessions": 5,
            "scope": "registered-model-combinations-not-orders"}


def strategy_from_design(design, catalog, label):
    fields = {"market", "maximumHoldingSessions", "signalValiditySeconds", "phases"}
    if not isinstance(design, dict) or set(design) != fields or catalog.get("version") != VERSION:
        raise ValueError("strategyDesign must select market, holding sessions, validity and all four phases")
    if not isinstance(design["phases"], dict) or set(design["phases"]) != set(PHASES):
        raise ValueError("strategyDesign requires all four phases")
    selected = set()
    for ids in design["phases"].values():
        if not isinstance(ids, list) or not 1 <= len(ids) <= 4 or any(not isinstance(i, str) for i in ids):
            raise ValueError("phase selects one to four registered model IDs")
        selected.update(ids)
    if not selected.issubset(catalog["models"]):
        raise ValueError("strategyDesign refers to an unregistered model")
    contract = {"version": VERSION, "compilerVersion": COMPILER_VERSION,
                "strategyId": "strategy:" + fingerprint({"design": design, "label": label,
                "source": catalog["sourceRuleboxFingerprint"], "deployment": catalog.get("sourceDeployment") or {}})[:24], "label": label, "mode": "research-only",
                "sourceDeployment": deepcopy(catalog.get("sourceDeployment") or {}),
                "sourceRuleboxFingerprint": catalog["sourceRuleboxFingerprint"], **deepcopy(design),
                "session": "regular", "missingDataPolicy": "unknown-not-false",
                "costPolicy": "fees-taxes-spread-slippage", "validationRequirements": list(CHECKS),
                "models": {rid: {k: deepcopy(v) for k, v in catalog["models"][rid].items() if k != "label"}
                           for rid in sorted(selected)}}
    contract["contractFingerprint"] = fingerprint(contract)
    return parse_strategy_contract(contract)


def strategy_design_prompt(catalog):
    offered = {rid: {"label": row["label"], "domains": row["claimContract"]["requiredEvidenceDomains"],
                    "statement": row["claimContract"]["statement"]}
               for rid, row in catalog["models"].items()}
    return "\n".join([
        "단기 전략의 연구용 설계를 작성합니다. 관찰·진입 검토·진입 취소·보유 종료 검토 조건을 모두 선택합니다.",
        "각 단계는 제공된 모델 ID 1~4개를 모두 충족하는 조합입니다. 없는 수급·기업 모델이나 새로운 임계값을 만들지 않습니다.",
        "단계 충족은 주문 지시가 아닙니다. 최대 보유 기간은 거래 세션 단위의 검증 목표이며 실제 거래일 시계·비용·독립 성과 검증이 필요합니다.",
        "proposedRule, 점수, 검증 성공 여부를 작성하지 않습니다. 부족한 기능은 blockers에 명시합니다.",
        "JSON만 반환: " + json.dumps({"candidates": [{"title": "전략 연구 제목", "rationale": "모델 조합의 이유",
            "risk": "자료와 시장 상황의 한계", "strategyDesign": {"market": "KR", "maximumHoldingSessions": 3,
            "signalValiditySeconds": 180, "phases": {p: ["offered model ruleId"] for p in PHASES}}}]}, ensure_ascii=False),
        json.dumps({"version": VERSION, "models": offered, "unavailableModels": catalog.get("unavailableModels", []),
                    "requiredValidation": CHECKS}, ensure_ascii=False),
    ])
