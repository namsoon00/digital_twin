"""Read exact native clearance proofs, without re-evaluating a price rule."""

from copy import deepcopy
import math
from digital_twin.modules.reasoning.contracts import native_account_weight_pct


def management_resolutions(relation):
    facts = relation.get('facts') or {}
    result = []
    for trace in (relation.get('graphStoreInference') or {}).get('traces') or []:
        proof = trace.get('managementResolution') or {}
        response = proof.get('response') or {}
        objective = response.get('objective') or {}
        conditions = trace.get('matchedConditions') or []
        predicate = next((c for c in conditions if c.get('conditionId') == response.get('conditionId')), {})
        if (proof.get('version') != 'typedb-policy-resolution-v1' or proof.get('matched') is not True
                or trace.get('matched') is not False or trace.get('nativeTypeDbReasoned') is not True
                or trace.get('evidenceUsableForJudgement') is not True or trace.get('freshnessStatus') != 'fresh'
                or predicate.get('matched') is not False or predicate.get('resolutionMatchedByTypeDB') is not True
                or objective.get('operator') != predicate.get('resolutionOperator')
                or objective.get('operator') != {'>': '<=', '<': '>=', '>=': '<', '<=': '>'}.get(predicate.get('operator'))
                or objective.get('field') != predicate.get('field')
                or response.get('authority') != 'account-policy' or response.get('version') != 'management-response-v1'
                or response.get('code') not in {'RESTORE_POSITION_LIMIT', 'PAUSE_AVERAGING_DOWN'}
                or any(c.get('judgementEvidenceUsable') is not True or c.get('freshnessStatus') not in {'fresh', 'not-applicable'} for c in conditions)):
            continue
        observed = facts.get(objective.get('field'))
        if objective.get('field') == 'positionAccountWeight':
            observed = native_account_weight_pct(observed)
        target = facts.get(objective['valueField']) if objective.get('valueField') else objective.get('value')
        expected = predicate.get('value', predicate.get('expectedValue'))
        expected = facts.get(expected.get('field')) if isinstance(expected, dict) else expected
        if (predicate.get('observedValue') != observed or expected != target
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (observed, target))):
            continue
        result.append({**deepcopy(response), 'ruleId': trace['ruleId'],
                       'observedValue': predicate.get('observedValue'),
                       'targetValue': target})
    return sorted(result, key=lambda r: (r['ruleId'], r['code']))
