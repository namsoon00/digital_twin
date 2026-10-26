"""Receipt-backed management transitions, never a new investment opinion."""

from copy import deepcopy
import json


def policy_lifecycle(current, previous, events):
    selected = [e for e in events if e.get('policyState')]
    active = (current.get('decisionBrief') or {}).get('restrictions') or []
    changes = []
    for event in selected:
        before = event.get('previousManagementResponses') or []
        after = event.get('managementResponses') or []
        if event['policyState'] == 'released':
            after = [r for r in ((current.get('decisionBrief') or {}).get('managementDecision') or {}).get('resolutions') or []
                     if r.get('ruleId') in event['sourceRuleIds']]
        old_targets = {(r.get('code'), json.dumps(r.get('objective'), sort_keys=True)): r.get('targetValue') for r in before}
        new_targets = {(r.get('code'), json.dumps(r.get('objective'), sort_keys=True)): r.get('targetValue') for r in after}
        state = event['policyState']
        cause = ('policy-target-changed' if before and after and old_targets != new_targets else
                 'objective-met' if state == 'released' else
                 'policy-reapplied' if event.get('previousPolicyState') == 'released' else
                 'management-response-defined' if after and not before and event.get('previousBlockedActions') else
                 'first-delivered-confirmation' if not event.get('previousBlockedActions') else 'policy-contract-changed')
        changes.append({'eventId': event['eventId'], 'sourceRuleIds': list(event['sourceRuleIds']),
                        'previous': 'released' if event.get('previousPolicyState') == 'released' else 'restricted' if event.get('previousBlockedActions') else 'not-previously-reported',
                        'current': 'released' if state == 'released' else 'restricted',
                        'cause': cause, 'previousResponses': deepcopy(before), 'responses': deepcopy(after),
                        'conditions': deepcopy(event.get('conditions') or []),
                        'previousConditions': deepcopy(event.get('previousConditions') or [])})
        changes[-1]['targetChanges'] = [{'field': (r.get('objective') or {}).get('field'),
            'previous': prior.get('targetValue'), 'current': r.get('targetValue')}
            for r in after for prior in before if r.get('code') == prior.get('code')
            and (r.get('objective') or {}).get('field') == (prior.get('objective') or {}).get('field')
            and r.get('targetValue') != prior.get('targetValue')]
    resolved = {rid for e in selected if e.get('policyState') == 'released' for rid in e['sourceRuleIds']}
    reported = {rid for entry in (previous.get('notificationIntentContinuity') or {}).values()
                if entry.get('policyState') in {'applied', 'updated'} for rid in entry.get('sourceRuleIds') or []}
    reported.update(r.get('ruleId') for r in (previous.get('decisionBrief') or {}).get('restrictions') or [])
    unverified = sorted(reported - resolved - {r['ruleId'] for r in active})
    current_responses = ((current.get('decisionBrief') or {}).get('managementDecision') or {}).get('responses') or []
    prior_responses = list(((previous.get('decisionBrief') or {}).get('managementDecision') or {}).get('responses') or [])
    prior_responses += [r for entry in (previous.get('notificationIntentContinuity') or {}).values()
                        if entry.get('policyState') in {'applied', 'updated'} for r in entry.get('managementResponses') or []]
    def signature(response):
        return json.dumps({k: response.get(k) for k in ('ruleId', 'code', 'authority', 'objective', 'targetValue', 'releaseCondition')}, sort_keys=True)
    prior_signatures = {signature(r) for r in prior_responses}
    changed_rules = {rid for e in selected if e.get('policyState') != 'released' and (
                        e.get('managementResponseChanged') or not e.get('previousBlockedActions'))
                     for rid in e.get('sourceRuleIds') or []}
    changed_responses = [r for r in current_responses if r.get('ruleId') in changed_rules]
    remaining_responses = [r for r in current_responses if r.get('ruleId') not in changed_rules]
    impact = {'version': 'rule-notice-decision-impact-v1',
              'status': 'changed' if changed_responses or resolved else 'maintained' if current_responses and all(
                  signature(r) in prior_signatures for r in current_responses) else 'current-only' if current_responses else 'unavailable',
              'changedResponses': deepcopy(changed_responses), 'remainingResponses': deepcopy(remaining_responses),
              'releasedRuleIds': sorted(resolved), 'sourceEventIds': [e['eventId'] for e in selected],
              'forecastActionSelected': False, 'orderAuthorized': False}
    return {'version': 'rule-policy-lifecycle-v1',
            'baselineJobId': previous.get('receiptJobId') or '',
            'baselineDeliveredAt': previous.get('deliveredAt') or '',
            'transitions': changes, 'remainingRestrictions': deepcopy(active),
            'unverifiedPreviousRuleIds': unverified, 'decisionImpact': impact,
            'forecastActionSelected': False, 'orderAuthorized': False}


def release_conclusion(packet):
    events = [e for e in packet.get('customerEvents') or [] if e.get('policyState') == 'released']
    if not events:
        return ''
    names = {'RESTORE_POSITION_LIMIT': '비중 조정 요구', 'PAUSE_AVERAGING_DOWN': '손실 구간 추가매수 제한'}
    released = list(dict.fromkeys(names[r['code']] for e in events for r in e.get('previousManagementResponses') or [] if r.get('code') in names))
    lead = '·'.join(released) + ' 해제.' if released else '앞서 알린 관리 조건이 해제됐습니다.'
    current = packet.get('current') or {}
    remaining = ((current.get('decisionBrief') or {}).get('managementDecision') or {}).get('responses') or []
    labels = list(dict.fromkeys(names[r['code']] for r in remaining if r.get('code') in names))
    if labels:
        label = '·'.join(labels)
        particle = '은' if (ord(label[-1]) - 0xAC00) % 28 else '는'
        lead += ' ' + label + particle + ' 계속 적용됩니다.'
    elif (packet.get('policyLifecycle') or {}).get('remainingRestrictions'):
        lead += ' 다른 관리 제한은 계속 적용됩니다.'
    elif (packet.get('policyLifecycle') or {}).get('unverifiedPreviousRuleIds'):
        lead += ' 다른 기존 제한의 해제는 아직 확인되지 않았습니다.'
    return lead
