"""Freeze native policy responses, independently of forecast qualification.

No raw fact can create a response here. TypeDB must have materialized the
authored response, with usable evidence, in this exact account generation.
The allocation numbers are arithmetic scenarios for an already selected
policy objective; they grant neither a forecast nor an order permission.
"""

from copy import deepcopy
from collections.abc import Mapping
import hashlib
import json
import math

from digital_twin.modules.decisions.domain.notification_ai_context import is_graph_backed_relation_context
from digital_twin.modules.reasoning.contracts import native_account_weight_pct


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def management_decision_from_relation(account_id, relation):
    graph = relation.get("graphStoreInference") or {}
    identity = {"accountId": account_id, "symbol": (relation.get("subject") or {}).get("symbol"),
                "inferenceGenerationId": relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId"),
                "sourceAboxSnapshotId": relation.get("sourceAboxSnapshotId") or graph.get("sourceAboxSnapshotId"),
                "worldId": relation.get("worldId") or graph.get("worldId")}
    if (not all(identity.values()) or not is_graph_backed_relation_context(relation)
            or relation.get("generationAligned") is False):
        return {}
    for key, value in identity.items():
        if any(source.get(key) and source[key] != value for source in (relation, graph)):
            return {}
    envelope = relation.get("actionEnvelope") or {}
    eligibility = {row.get("ruleId"): row for row in envelope.get("inferenceEligibilityAssessments") or []}
    responses = {}
    for rule in relation.get("matchedRules") or []:
        rule_id = rule.get("rule_id") or rule.get("ruleId")
        response = deepcopy(rule.get("managementResponse") or {})
        eligible = eligibility.get(rule_id) or {}
        if (not response or rule.get("matched") is False or rule.get("referenceOnly") or rule.get("reference_only")
                or (rule.get("knowledgeBasis") or {}).get("ruleKind") != "policy-constraint"
                or eligible.get("status") != "eligible" or eligible.get("freshnessStatus") != "fresh"
                or eligible.get("evidenceUsableForJudgement") is not True
                or response.get("version") != "management-response-v1" or response.get("authority") != "account-policy"):
            continue
        if response.get("code") not in {"RESTORE_POSITION_LIMIT", "PAUSE_AVERAGING_DOWN"}:
            continue
        facts, objective = response.get("boundFacts") or {}, response.get("objective") or {}
        observed = _number(facts.get(objective.get("field")))
        target = _number(facts.get(objective["valueField"]) if objective.get("valueField") else objective.get("value"))
        if observed is None or target is None:
            continue
        # A snapshot from another input cannot supplement this native proof.
        if any((native_account_weight_pct((relation.get("facts") or {}).get(key))
                if key == "positionAccountWeight" else (relation.get("facts") or {}).get(key)) != value for key, value in facts.items()
               if key in (relation.get("facts") or {})):
            continue
        response.update(ruleId=rule_id, observedValue=observed, targetValue=target)
        if response.get("calculation") == "sale-proceeds-retained-in-account":
            if not (0 < target < observed <= 100):
                continue
            fraction = (observed - target) / observed
            scenario = {"basis": "sale-proceeds-retained-in-account", "excessWeightPercentagePoints": observed - target,
                        "positionReductionPct": fraction * 100,
                        "externalDepositPctOfAccount": (observed / target - 1) * 100,
                        "assumptions": ["price-and-fx-unchanged", "sale-proceeds-stay-in-account", "fees-and-tax-excluded"],
                        "orderQuantity": None}
            quantity, price = _number(facts.get("quantity")), _number(facts.get("currentPrice"))
            if quantity and quantity > 0:
                scenario["equivalentQuantity"] = quantity * fraction
                if price and price > 0:
                    scenario["equivalentValue"] = quantity * fraction * price
                    scenario["currency"] = facts.get("currency") or ""
            response["adjustmentScenario"] = scenario
        responses[(rule_id, response["code"])] = response
    from .management_resolution import management_resolutions
    resolutions = management_resolutions(relation)
    if not responses and not resolutions:
        return {}
    ordered = sorted(responses.values(), key=lambda row: (-(_number(row.get("displayPriority")) or 0), row["ruleId"], row["code"]))
    result = {"version": "management-decision-v1", "authority": "typedb-materialized-account-policy", **identity,
              "status": "POLICY_RESPONSE_SELECTED" if ordered else "POLICY_RESOLUTION_VERIFIED", "responses": ordered,
              **({'resolutions': resolutions} if resolutions else {}),
              "forecastActionSelected": False, "orderAuthorized": False}
    result["decisionId"] = "management-decision:" + hashlib.sha256(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
    return result


def verified_management_decision(context):
    """Bind a captured policy decision to its original native proof.

    Candidate construction has a synthesis before a subject case exists.
    Delivery has the persisted (possibly compact) subject case. Neither stage
    may create a response from a flag, a raw weight or another generation.
    """
    def mapping(value):
        return dict(value) if isinstance(value, Mapping) else {}

    payload = mapping(context)
    relation = mapping(payload.get("ontologyRelationContext")) or mapping(
        mapping(payload.get("metadata")).get("ontologyRelationContext"))
    case = mapping(payload.get("investmentSubjectDecisionCase"))
    syntheses = [mapping(case.get("synthesis")), mapping(payload.get("v2DecisionSynthesis"))]
    captured = [mapping(case.get("managementDecision")), *[
        mapping(s.get("management_decision") or s.get("managementDecision")) for s in syntheses]]
    captured = [value for value in captured if value]
    if not captured:
        return {}
    account = captured[0].get("accountId")
    native = management_decision_from_relation(account, relation)
    if not native or any(value != native for value in captured):
        return {}
    fields = {"accountId": "account_id", "symbol": "symbol", "worldId": "world_id",
              "inferenceGenerationId": "inference_generation_id", "sourceAboxSnapshotId": "source_abox_snapshot_id"}
    for source in [case, *syntheses]:
        if not source:
            continue
        for key, alias in fields.items():
            values = [source.get(key), source.get(alias)]
            if key != "worldId" and not any(values):
                return {}
            if any(value and value != native[key] for value in values):
                return {}
    if not case and not any(syntheses):
        return {}
    if payload.get("accountId") and payload["accountId"] != account:
        return {}
    if payload.get("symbol") and payload["symbol"] != native["symbol"]:
        return {}
    if case and (not case.get("subjectCaseId") or (
            payload.get("investmentSubjectDecisionCaseId")
            and payload["investmentSubjectDecisionCaseId"] != case["subjectCaseId"])):
        return {}
    return native
