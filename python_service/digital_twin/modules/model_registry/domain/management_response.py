"""Authoring contract for non-predictive, native RuleBox management decisions."""

from copy import deepcopy


def management_resolution_probe(rule):
    """Read an existing authored objective; never author a new policy boundary.

    A positive TypeQL match of this objective disproves that policy's exact
    required predicate. Missing predicates or missing profile links do not.
    """
    responses = [d for d in rule.derivations if d.management_response]
    if len(responses) != 1 or management_response_violations(rule, responses[0]):
        return {}
    response = responses[0].management_response
    condition = next(c for c in rule.conditions if c.condition_id == response['conditionId'])
    objective = response['objective']
    predicate = deepcopy(condition.to_dict())
    predicate['operator'] = objective['operator']
    predicate['value'] = ({'field': objective['valueField']} if objective.get('valueField') else objective['value'])
    holding = next((c.to_dict() for c in rule.conditions if c.kind == 'subject_property'
                    and c.field == 'source' and c.operator == '==' and c.value == 'holding'), None)
    if not holding or not rule.enabled:
        return {}
    return {'rule_id': rule.rule_id, 'source_kind': rule.source_kind,
            'conditions': [holding, predicate], 'managementResponse': deepcopy(response)}


def management_response_violations(rule, derivation):
    response = derivation.management_response
    errors = []
    if rule.resolved_claim_contract.claim_type != "risk-invariant":
        errors.append("management response requires a risk-invariant claim")
    if response.get("version") != "management-response-v1" or response.get("authority") != "account-policy":
        errors.append("invalid management response authority/version")
    if response.get("code") not in {"RESTORE_POSITION_LIMIT", "PAUSE_AVERAGING_DOWN"}:
        errors.append("unknown management response code")
    if derivation.candidate_action or derivation.allowed_actions:
        errors.append("management response cannot grant a market action")
    for key in ("label", "reason", "releaseCondition", "executionBoundary"):
        if not isinstance(response.get(key), str) or not response[key].strip():
            errors.append("management response requires " + key)
    condition = next((c for c in rule.conditions if c.condition_id == response.get("conditionId")), None)
    objective = response.get("objective") or {}
    if (not condition or condition.kind != "subject_property" or condition.role != "required"
            or objective.get("field") != condition.field
            or objective.get("operator") != {">": "<=", "<": ">=", ">=": "<", "<=": ">"}.get(condition.operator)):
        errors.append("management objective must invert an exact required policy condition")
    elif isinstance(condition.value, dict):
        if objective.get("valueField") != condition.value.get("field") or "value" in objective:
            errors.append("management target must use the matched policy parameter")
    elif objective.get("value") != condition.value or "valueField" in objective:
        errors.append("management target must use the matched policy boundary")
    if response.get("calculation") == "sale-proceeds-retained-in-account":
        if objective != {"field": "positionAccountWeight", "operator": "<=", "valueField": "strategyMaxPositionWeightPct"}:
            errors.append("allocation calculation requires an account-weight ceiling")
    elif response.get("calculation") != "none":
        errors.append("unknown management response calculation")
    return errors
