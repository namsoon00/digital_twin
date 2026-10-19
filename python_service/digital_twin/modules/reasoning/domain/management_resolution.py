"""Persist clearance proof without materializing the old blocked action."""

from copy import deepcopy
import math
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, entity_id
from digital_twin.modules.reasoning.domain.ontology_inference_materializer import grounded_inference_context, inference_properties


def materialize_management_resolution(graph, rule, subject, match, evidence_index):
    response = match['managementResolution']['response']
    # Zero-filled missing holdings/cost/valuation fields cannot prove recovery.
    # These are source validity checks, not a new management boundary.
    required = ['quantity', 'currentPrice', response['objective']['field']]
    if response['code'] == 'PAUSE_AVERAGING_DOWN':
        required = ['quantity', 'currentPrice', 'averagePrice']
    values = [subject.properties.get(field) for field in required]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
        return
    positive = deepcopy(match)
    for condition in positive.get('matchedConditions') or []:
        if condition.get('resolutionMatchedByTypeDB') is True:
            condition.update(matched=True, matchedByTypeDB=True)
    context = grounded_inference_context(graph, rule, subject, positive, evidence_index=evidence_index)
    for condition in context['matchedConditions']:
        if condition.get('resolutionMatchedByTypeDB') is True:
            condition.update(matched=False, matchedByTypeDB=False)
    symbol = subject.properties.get('symbol')
    if not symbol or not context.get('evidenceUsableForJudgement'):
        return
    graph.entities.append(OntologyEntity(entity_id('inference-trace', symbol + ':' + rule.rule_id),
        subject.label + ' · 관리 조건 해소 확인', 'inference-trace', inference_properties({
            'tboxClass': 'InferenceTrace', 'tboxClasses': ['InferenceTrace', 'AIJudgmentAudit'],
            'symbol': symbol, 'ruleId': rule.rule_id, 'ruleLabel': rule.label, 'matched': False,
            'claimContract': rule.resolved_claim_contract.to_dict(),
            'knowledgeBasis': rule.resolved_knowledge_basis.to_dict(),
            'managementResolution': deepcopy(match['managementResolution']),
            **{key: context[key] for key in ('matchedConditions', 'conditionDetailSource', 'dataState',
                'freshnessStatus', 'evidenceUsableForJudgement', 'requiredConditionCount', 'groundedConditionCount')},
        })))
