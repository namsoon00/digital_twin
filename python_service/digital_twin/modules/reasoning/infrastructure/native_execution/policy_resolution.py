"""Positive native proof that an authored management objective is met."""

from digital_twin.modules.model_registry.contracts import management_resolution_probe
from digital_twin.modules.reasoning.infrastructure.typeql.match_queries import typedb_native_match_query


VERSION = 'typedb-policy-resolution-v1'


def append_policy_resolutions(store, result, rules, symbols, world_id, evidence_index, leased):
    # Exact physical ABox identities are read under the existing write lease.
    # A failure here cannot erase a valid active restriction or imply recovery.
    report = {'version': VERSION, 'status': 'unavailable', 'matchedCount': 0, 'queryCount': 0, 'errors': []}
    result['policyResolutionEvaluation'] = report
    if not leased or result.get('status') != 'ok' or evidence_index.get('status') != 'verified' or not world_id:
        report['reasonCode'] = 'exact-abox-lease-or-native-proof-unavailable'
        return
    index = evidence_index.get('index') or {}
    selected = set(symbols or index.get('sourceIdsBySymbol') or [])
    source_ids = {sid for symbol in selected for sid in (index.get('sourceIdsBySymbol') or {}).get(symbol, [])}
    storage = index.get('sourceStorageIdsBySourceId') or {}
    if not source_ids or any(not storage.get(sid) for sid in source_ids):
        report['reasonCode'] = 'exact-subject-storage-identity-unavailable'
        return
    positive = {(m.get('ruleId'), m.get('sourceId')) for m in result.get('matches') or []}
    rows, errors = [], []
    for rule in rules:
        probe = management_resolution_probe(rule)
        if not probe:
            continue
        plan = typedb_native_match_query(probe, target_symbols=selected, world_id=world_id,
                                        active_source_storage_ids=[storage[sid] for sid in sorted(source_ids)])
        if not plan.get('query'):
            continue
        try:
            report['queryCount'] += 1
            matches = store.read_rows(plan['query'], plan['columns'], label='typedb.policy-resolution')
        except Exception as error:  # preserve active policy evidence on an optional read failure
            errors.append({'ruleId': rule.rule_id, 'reason': type(error).__name__})
            continue
        for match in matches:
            sid = match.get('sourceId')
            if sid not in source_ids or (rule.rule_id, sid) in positive:
                continue
            response = probe['managementResponse']
            conditions = []
            for condition in probe['conditions']:
                cid = condition.get('condition_id') or condition.get('conditionId')
                resolved = cid == response['conditionId']
                original = next(c for c in rule.conditions if c.condition_id == cid)
                conditions.append({'conditionId': cid, 'kind': 'subject_property', 'role': 'required',
                    'field': original.field, 'operator': original.operator, 'value': original.value,
                    'matched': not resolved, 'matchedByTypeDB': not resolved,
                    **({'resolutionMatchedByTypeDB': True, 'resolutionOperator': response['objective']['operator']} if resolved else {})})
            rows.append({'ruleId': rule.rule_id, 'sourceId': sid, 'sourceLabel': match.get('sourceLabel') or '',
                'matchedConditions': conditions, 'evidenceRelationIds': [],
                'managementResolution': {'version': VERSION, 'response': response, 'matched': True},
                'conditionDetailSource': VERSION, 'worldId': world_id})
    report.update(status='partial' if errors else 'complete', matchedCount=len(rows), errors=errors)
    result['matches'] = list(result.get('matches') or []) + rows
