"""Admit whole optional baselines against the complete author input, before calls."""
from copy import deepcopy
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash

RESERVE_BYTES = 16384


def budget_error(error):
    return isinstance(error, EvidenceContractError) and any(code in error.code for code in
        ('exceeds-context-budget', 'exceed-context-budget'))


def admit_baseline(session, packet, views, research, history, prompt_bytes, contract):
    from .working_retrieval import author_packet, byte_size
    from ..domain.execution_input import freeze_execution_input
    original = deepcopy(packet)
    business = original['businessEvidence']['factIds']
    value = original.get('companyValueEvidence', {})
    value_ids = value.get('factIds', [])
    # Keep every model's assessment, input bundle and estimate together.
    groups = []
    for key in sorted(value_ids, key=lambda k: (views[k].get("valuationDecisionEligible") is not True, k)):
        fact = views[key]
        if fact['kind'] != 'valuation-assessment':
            continue
        groups.append([key] + [k for k in value_ids if k != key and (
            (views[k]['kind'] == 'valuation-input-bundle' and views[k].get('valuationBundleId') == fact.get('valuationBundleId')) or
            (views[k]['kind'] == 'fair-value-estimate' and views[k].get('valuationAssessmentId') == fact.get('valuationAssessmentId')))])
    selected_value, selected_business = [], []

    def build():
        result = deepcopy(original)
        result['businessEvidence'].update(factIds=list(selected_business),
            candidateFactIds=list(business),
            viewFactIds=[k for k in original['businessEvidence'].get('viewFactIds', []) if k in selected_business],
            omittedByBudget=[k for k in dict.fromkeys(original['businessEvidence']['omittedByBudget'] + business)
                if k not in set(selected_business + selected_value)],
            admissionPolicy='complete-author-input-v1')
        if value:
            result['companyValueEvidence'].update(factIds=list(selected_value),
                omitted=value.get('omitted', []) + [
                    {'factId': group[0], 'reason': 'company-value-budget',
                     'sourceFactHash': views[group[0]].get('evidenceView', {}).get('sourceFactHash') or content_hash(views[group[0]]), 'scope': 'complete-author-input'}
                    for group in groups if group[0] not in selected_value])
        ids = set(selected_business + selected_value)
        seen = {r['id']: r for r in session.select(ids, views=views, macro_baseline=False)['facts']}
        result['businessEvidence']['bytes'] = sum(byte_size(seen[k]) for k in selected_business)
        if value:
            result['companyValueEvidence']['bytes'] = sum(byte_size(seen[k]) for k in selected_value)
        candidate = author_packet(session, result, ids, seen, research, [], 'deferred', RESERVE_BYTES, contract)
        freeze_execution_input(candidate, history, research, prompt_bytes)
        return result

    # Required clocks, prior delivery and question contracts are never truncated.
    accepted = build()
    available = [group for group in groups if views[group[0]].get('valuationDecisionEligible') is True]
    blocked = [group for group in groups if group not in available]
    # Usable values and original business sources precede blocked calculations.
    # A blocked model must not crowd out the report that can answer the question.
    operations = [(selected_value, group) for group in available]
    operations += [(selected_business, [key]) for key in business if key not in value_ids]
    operations += [(selected_value, group) for group in blocked]
    for selected, additions in operations:
        before = list(selected)
        selected.extend(key for key in additions if key not in selected)
        try:
            accepted = build()
        except EvidenceContractError as error:
            if not budget_error(error):
                raise
            selected[:] = before
    return accepted
