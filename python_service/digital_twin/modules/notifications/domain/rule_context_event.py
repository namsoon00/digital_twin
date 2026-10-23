"""Native causal-context notices, independent of predictive hypotheses."""

from copy import deepcopy
from collections.abc import Mapping

from digital_twin.modules.model_registry.contracts import rule_notification_intent
from .rule_notification_event import VERSION, decision_state, fingerprint, rows


def _mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def native_context_rule_ids(relation):
    """Recognize a pure context result before a subject publication exists.

    Context rules intentionally have no investment action/stage or predictive
    hypothesis. Their frozen claims and native traces own observation authority.
    Mixed predictive/policy results retain their existing publication paths.
    """
    graph = _mapping(relation.get('graphStoreInference'))
    subject = _mapping(relation.get('subject'))
    if (subject.get('market') not in {'CRYPTO', 'KR', 'US'} or relation.get('generationAligned') is False
            or not all(relation.get(k) and relation[k] == graph.get(k)
                       for k in ('sourceAboxSnapshotId', 'inferenceGenerationId'))):
        return []
    native = rows(graph.get('traces'))
    claims = [_mapping(row.get('claimContract')) for row in native if row.get('matched') is not False]
    if not claims or any(claim.get('claimType') != 'causal-context' for claim in claims):
        return []
    if subject.get('market') != 'CRYPTO' and any(
            (claim.get('strategyContract') or {}).get('mode') != 'live-observation' for claim in claims):
        return []
    result = []
    for row in native:
        claim = _mapping(row.get('claimContract'))
        basis = _mapping(row.get('knowledgeBasis'))
        intent = rule_notification_intent(claim)
        rule_id = row.get('ruleId') or row.get('rule_id') or row.get('sourceRuleId')
        if (not rule_id or row.get('matched') is False
                or (row.get('symbol') and row['symbol'] != subject.get('symbol'))
                or basis.get('ruleKind') != 'context-observation'
                or basis.get('decisionEligibility') != 'reference-only'
                or basis.get('requiresHypothesis') is not False
                or not intent or intent['purpose'] != 'context-evidence' or intent['ruleId'] != rule_id
                or not rows(row.get('matchedConditions') or row.get('conditionMatches'))):
            continue
        result.append(rule_id)
    return sorted(set(result))


def context_rule_events(current, previous):
    """Compare graph-authored context meaning with successful delivery only."""
    scope = {k: current.get(k) or '' for k in ('accountId', 'symbol', 'worldId')}
    if (current.get('market') != 'CRYPTO'
            or not current.get('proofIdentityVerified') or not all(scope.values())):
        return []
    if current.get('hypotheses') or any(
        _mapping(rule.get('claimContract')).get('claimType') != 'causal-context'
        for rule in rows(current.get('rules')) if rule.get('nativeProof') and rule.get('matched') is not False
    ):
        return []
    history = (previous.get('notificationIntentContinuity') or {}) if (
        previous.get('receiptJobId') and previous.get('deliveredAt')) else {}
    result = []
    for rule in rows(current.get('rules')):
        claim = _mapping(rule.get('claimContract'))
        intent = rule_notification_intent(claim)
        conditions = rows(rule.get('conditions'))
        if (claim.get('claimType') != 'causal-context' or not rule.get('nativeProof')
                or not rule.get('referenceOnly') or rule.get('matched') is False or not conditions
                or not intent or intent['purpose'] != 'context-evidence' or intent['ruleId'] != rule['id']
                or 'observed' not in intent['notifyOn']):
            continue
        # Rule identities and authored conditions define the market state.
        # New prices, source clocks and inference generations do not renew it.
        criteria = [{k: deepcopy(c.get(k)) for k in
                     ('conditionId', 'kind', 'field', 'operator', 'expectedValue', 'resolvedThreshold')}
                    for c in conditions]
        measurements = [deepcopy(c['contextMeasurement']) for c in conditions
                        if c.get('contextMeasurement') and c['contextMeasurement'].get('symbol') == scope['symbol']]
        signature = fingerprint({'claim': intent['claimFingerprint'], 'ruleId': rule['id'], 'criteria': criteria,
            'marketCriteria': [{k: m.get(k) for k in ('eventType', 'symbol', 'horizon', 'direction', 'thresholdPct')}
                               for m in measurements]})
        key = fingerprint({**scope, 'claimId': intent['claimContractId'], 'purpose': intent['purpose']})
        old = history.get(key) or {}
        if old.get('fingerprint') == signature:
            continue
        result.append({'version': VERSION, **scope,
            'eventId': 'rule-context:' + fingerprint([key, signature])[:24],
            'intentKey': key, 'intent': intent, 'purpose': intent['purpose'], 'changeKind': 'observed',
            'contextState': 'observed', 'inferenceGenerationId': current.get('inferenceGenerationId'),
            'sourceAboxSnapshotId': current.get('sourceAboxSnapshotId'), 'transitionId': '', 'lifecycleKey': '',
            'observedAt': current.get('observedAt'), 'hypothesis': {}, 'sourceRuleIds': [rule['id']],
            'ruleLabel': rule.get('label') or intent['expectedOutcome'], 'conditions': deepcopy(conditions),
            'measurements': measurements,
            'decisionImpact': {'status': 'unchanged' if old else 'first-observation',
                               'previous': decision_state(previous), 'current': decision_state(current)},
            'nextCheck': {'condition': intent['falsificationContract'], 'conditions': deepcopy(conditions),
                          'nextChecks': deepcopy(rule.get('nextChecks') or []),
                          'trackingStatus': 'context-reevaluation', 'tracking': []},
            'parentJobId': old.get('rootJobId') or '', 'fingerprint': signature,
            'eligible': True, 'reasonCode': 'verified-context-rule-observation'})
    return result
