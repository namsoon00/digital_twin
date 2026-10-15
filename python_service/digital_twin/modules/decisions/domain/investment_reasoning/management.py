"""Freeze native policy responses, independently of forecast qualification.

No raw fact can create a response here. TypeDB must have materialized the
authored response, with usable evidence, in this exact account generation.
The allocation numbers are arithmetic scenarios for an already selected
policy objective; they grant neither a forecast nor an order permission.
"""

from copy import deepcopy
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
    if not responses:
        return {}
    ordered = sorted(responses.values(), key=lambda row: (-(_number(row.get("displayPriority")) or 0), row["ruleId"], row["code"]))
    result = {"version": "management-decision-v1", "authority": "typedb-materialized-account-policy", **identity,
              "status": "POLICY_RESPONSE_SELECTED", "responses": ordered,
              "forecastActionSelected": False, "orderAuthorized": False}
    result["decisionId"] = "management-decision:" + hashlib.sha256(
        json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
    return result
