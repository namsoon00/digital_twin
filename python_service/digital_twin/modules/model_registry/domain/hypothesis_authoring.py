"""Compile bounded research designs from registered contracts, never market facts."""

from copy import deepcopy
import json

from .experiment_observations import authored_observation_requirements
from .hypothesis_compilation import compilation_fingerprint, ranked_authoring_rules, validation_requirements
from .ontology_rulebox_contracts import GraphInferenceRule
from .ontology_evolution import comparison_measurement


AUTHORING_CONTRACT = "registered-hypothesis-design-v1"


def _rules(context):
    return {row["rule_id"]: GraphInferenceRule.from_dict(row).to_dict()
            for row in (context.get("ruleBox") or {}).get("rules") or []
            if isinstance(row, dict) and row.get("rule_id")}


def _predictive(rule):
    return (rule.get("enabled") and rule.get("source_kind") == "stock"
            and GraphInferenceRule.from_dict(rule).resolved_claim_contract.is_predictive
            and any(row.get("relation_type") == "HAS_MODEL_SIGNAL" for row in rule["conditions"]))


def _context_condition(rule, row):
    return (rule.get("enabled") and rule.get("source_kind") == "stock"
            and row.get("role") == "required" and row.get("kind") in {"property", "relation"}
            and row.get("relation_type") != "HAS_MODEL_SIGNAL")


def authoring_catalog(context, cadence_seconds=180):
    rules = _rules(context)
    ordered = [rules[row["rule_id"]] for row in ranked_authoring_rules(context) if row.get("rule_id") in rules]
    baselines, conditions = [], []
    for rule in ordered:
        if _predictive(rule) and len(baselines) < 16:
            claim = rule["claim_contract"]
            baselines.append({"ruleId": rule["rule_id"], "label": rule["label"],
                              "statement": claim["statement"], "predictionTarget": claim["predictionTarget"],
                              "expectedDirection": claim["expectedDirection"],
                              "outcomeContract": claim["outcomeContract"]})
        for row in rule["conditions"]:
            if _context_condition(rule, row) and len(conditions) < 32:
                conditions.append({"ruleId": rule["rule_id"], "conditionId": row["condition_id"],
                                   "ruleLabel": rule["label"], "condition": row})
    measurements = {row["ruleId"]: comparison_measurement(rules[row["ruleId"]]) for row in baselines}
    for row in baselines:
        signature = measurements[row["ruleId"]]
        row["comparableRuleIds"] = [other["ruleId"] for other in baselines
                                    if measurements[other["ruleId"]] == signature]
    return {"contract": AUTHORING_CONTRACT, "baselines": baselines, "conditions": conditions,
            "additionalObservationMetrics": ["price", "volume", "profitLossRate"],
            "collectorCadenceSeconds": cadence_seconds, "maximumLookbackMinutes": 1440,
            "systemOwned": ["source-packet", "claim_contract", "model_input_contract", "derivations",
                            "outcomeContract", "qualificationPolicy", "enabled", "evolutionScope"],
            "scope": "registered-conditional-predictions-only"}


def _text(value, name, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("hypothesisDesign requires bounded " + name)
    return value.strip()


def _additional_conditions(design, catalog, rules, baseline):
    refs = design.get("conditionRefs")
    if not isinstance(refs, list) or not 1 <= len(refs) <= 4:
        raise ValueError("hypothesisDesign needs one to four additional registered conditions")
    allowed = {(row["ruleId"], row["conditionId"]) for row in catalog["conditions"]}
    result, seen = [], set()
    semantic = lambda row: compilation_fingerprint({k: v for k, v in row.items()
                                                    if k not in {"condition_id", "description", "evidence_group_key"}})
    original = {semantic(row) for row in baseline["conditions"]}
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"ruleId", "conditionId"}:
            raise ValueError("conditionRefs accepts only ruleId and conditionId")
        key = (ref["ruleId"], ref["conditionId"])
        if key not in allowed or key in seen:
            raise ValueError("Additional condition is not an offered unique registered condition")
        row = next(deepcopy(item) for item in rules[key[0]]["conditions"] if item["condition_id"] == key[1])
        if semantic(row) in original:
            raise ValueError("Additional condition duplicates the baseline predicate")
        original.add(semantic(row))
        seen.add(key)
        row["condition_id"] = "research-context-" + compilation_fingerprint(ref)[:16]
        row["evidence_group_key"] = row["condition_id"]
        result.append(row)
    return result


def assemble_hypothesis_design(candidate, context):
    """AI selects contracts; executable fields and outcome thresholds stay immutable."""
    design = candidate.get("hypothesisDesign")
    if candidate.get("proposedRule") is not None:
        raise ValueError("hypothesisDesign cannot author system-owned proposedRule")
    if not isinstance(design, dict) or set(design) != {"modelRuleId", "comparisonRuleId", "conditionRefs", "additionalObservations", "unverifiedClaims"}:
        raise ValueError("hypothesisDesign fields must match the registered design contract")
    catalog = context["authoringContract"]
    if catalog.get("contract") != AUTHORING_CONTRACT:
        raise ValueError("Unknown hypothesis authoring contract")
    baseline_id = _text(design["modelRuleId"], "modelRuleId", 191)
    rules = _rules(context)
    if baseline_id not in {row["ruleId"] for row in catalog["baselines"]} or not _predictive(rules[baseline_id]):
        raise ValueError("Predictive baseline is not offered by the loaded release")
    baseline = rules[baseline_id]
    comparator_id = _text(design["comparisonRuleId"], "comparisonRuleId", 191)
    offered = next(row for row in catalog["baselines"] if row["ruleId"] == baseline_id)
    if comparator_id not in offered["comparableRuleIds"]:
        raise ValueError("Comparison rule must have the identical measurement contract")
    extra = _additional_conditions(design, catalog, rules, baseline)
    if not isinstance(design["additionalObservations"], list):
        raise ValueError("additionalObservations must be an explicit list")
    observations = authored_observation_requirements(
        {"model_input_contract": {"observationRequirements": design["additionalObservations"]}},
        cadence_seconds=catalog["collectorCadenceSeconds"],
    )
    for row in observations:
        if (row["metric"] not in catalog["additionalObservationMetrics"]
                or row["lookbackMinutes"] > catalog["maximumLookbackMinutes"]
                or (row["minimumSamples"] > 1 and row["cadenceSeconds"] < catalog["collectorCadenceSeconds"])):
            raise ValueError("Additional observations exceed the registered collector capability")
    claims = design["unverifiedClaims"]
    if not isinstance(claims, list) or len(claims) > 8:
        raise ValueError("unverifiedClaims must be a bounded list")
    claims = [_text(item, "unverifiedClaims") for item in claims]
    rule = deepcopy(baseline)
    identity = compilation_fingerprint({"caseId": context["hypothesisProposal"]["caseId"],
                                        "design": design, "baseline": baseline,
                                        "comparator": rules[comparator_id], "conditions": extra})[:24]
    rule_id = "graph.research.conditional." + identity + ".v1"
    rule.update({"rule_id": rule_id, "label": _text(candidate.get("title"), "title", 240),
                 "version": AUTHORING_CONTRACT, "enabled": False,
                 "hypothesis_family_key": "research-conditional:" + identity,
                 "conditions": deepcopy(baseline["conditions"]) + extra})
    rule["claim_contract"].update({"ruleId": rule_id, "claimContractId": "rule-claim:" + rule_id,
                                   "statement": baseline["claim_contract"]["statement"] + " 추가 조건: " +
                                   "; ".join(row["description"] for row in extra)})
    for index, row in enumerate(rule["derivations"]):
        row["target_key"] = "research-conditional:" + identity + ":" + str(index)
    rule["hypothesis_lifecycle"]["formationConditionIds"] = (
        list(rule["hypothesis_lifecycle"]["formationConditionIds"]) + [row["condition_id"] for row in extra])
    rule["model_input_contract"].update({
        "comparisonBaselineRuleId": comparator_id, "observationRequirements": observations,
        "researchDesign": {"contract": AUTHORING_CONTRACT, "baselineFingerprint": compilation_fingerprint(baseline),
                           "modelRuleId": baseline_id, "comparisonRuleId": comparator_id,
                           "conditionRefs": deepcopy(design["conditionRefs"]), "unverifiedClaims": claims,
                           "scope": "conditional-prediction-only"},
    })
    requirements = validation_requirements(candidate)
    # A specification repair cannot erase prior causal/external review obligations.
    prior = context["hypothesisProposal"].get("validationRequirements") or []
    requirements += [deepcopy(row) for row in prior if row.get("check") == "review" and row not in requirements]
    requirements += [{"check": "review", "requirement": text, "dependencyKey": "unverified-causal-claim"} for text in claims]
    requirements += [{"check": "paired-forward-outcomes", "requirement": "같은 입력과 시점의 기존 가설 대비 독립 미래 결과를 비교합니다.",
                      "dependencyKey": "outcome-validation"}]
    return {**candidate, "proposedRule": GraphInferenceRule.from_dict(rule).to_dict(),
            "hypothesisDesign": deepcopy(design), "validationRequirements": validation_requirements({"validationRequirements": requirements})}


def build_hypothesis_design_prompt(context, payload):
    payload = deepcopy(payload)
    payload["authoringContract"] = context["authoringContract"]
    payload["ruleDesign"] = {k: v for k, v in payload.get("ruleDesign", {}).items()
                             if k in {"version", "rulesHash", "ruleboxSnapshotId", "scope", "observationState"}}
    contract = {"candidates": [{"title": "가설 제목", "rationale": "원래 가설과 기존 모델, 추가 조건의 연결", "risk": "한계",
                                "blockers": [], "validationRequirements": [],
                                "hypothesisDesign": {"modelRuleId": "offered ruleId", "comparisonRuleId": "offered comparableRuleId", "conditionRefs": [
                                    {"ruleId": "offered ruleId", "conditionId": "offered conditionId"}],
                                    "additionalObservations": [], "unverifiedClaims": []}}]}
    return "\n".join([
        "너는 투자 가설 연구 설계자다. 원래 가설 하나의 검증 가능한 조건부 예측을 작성한다.",
        "실행 규칙 전체를 작성하지 않는다. hypothesisDesign만 작성하고 proposedRule, 내부 필드, 점수, 검증 결과는 만들지 않는다.",
        "modelRuleId는 authoringContract.baselines 중 정확한 예측 모델 하나다. 결과 지표, 기간, 임계치, 행동과 모델 연결은 시스템이 그대로 상속한다.",
        "comparisonRuleId는 해당 모델의 comparableRuleIds 중 원래 주장과 비교할 기존 가설이다. 같은 방향의 같은 예측은 결과가 같으므로 우월성을 입증하지 못한다. 반대 가설도 실제로 비교 가능한 계약이며 원래 연구 질문에 맞을 때만 선택한다.",
        "conditionRefs는 authoringContract.conditions 중 기존 예측에 추가할 필요한 조건 1~4개다. 기존 숫자나 조건을 임의 수정하지 않는다.",
        "추가 조건은 원래 가설을 시험하는 의미가 있어야 한다. 통과만을 위한 임의 조건이나 동일 예측 복제는 금지다.",
        "원래 가설의 더 강한 인과 효과를 기존 예측 계약이 입증한다고 말하지 않는다. 검증하지 못하는 인과·장기 주장은 unverifiedClaims에 남긴다.",
        "현재 ABox는 not-queried다. 저장 모델 평가 not-supported는 조건 불성립이며 모델 미등록이나 자료 결측 증거가 아니다. 현재 성립은 TypeDB가 검증한다.",
        "source-packet 식별과 원천 보존, 미래 결과 수집은 시스템 소유다. 이를 additionalObservations에 작성하지 않는다.",
        "additionalObservations는 등록 모델 입력 외에 실제로 더 필요한 과거 자료만 명시한다. 미래 결과 관측을 과거 1440분 이력 요구로 잘못 작성하지 않는다. 불필요하면 빈 배열이다.",
        "추가 자료는 metric, label, lookbackMinutes, minimumSamples, cadenceSeconds, maximumDelayMinutes를 사용한다. 모든 수치는 정수, 실제 수집 주기 이상이어야 한다.",
        "등록된 모델/조건으로 원래 가설을 시험할 수 없으면 hypothesisDesign=null과 blockers를 반환한다. kind=unsupported-capability, requirement와 dependencyKey에 부족한 모델·자료 계약을 구체적으로 적는다.",
        "형식 오류는 schema-mismatch, 미확인 관측은 unverified-observation이다. 단순 다음 관측이나 현재 조건 미성립은 작성 차단 사유가 아니다.",
        "이전 실패와 후보는 피드백이다. 같은 오류를 반복하지 않되, 별도 review 검증을 삭제해 통과시키지 않는다.",
        "응답은 JSON 하나만 반환한다.", json.dumps(contract, ensure_ascii=False),
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    ])
