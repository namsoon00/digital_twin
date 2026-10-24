"""Explain read and approval blockers without admitting a blocked calculation."""

VERSION = 'company-value-readiness-v1'


def assess(packet):
    facts = {f['id']: f for f in packet.get('facts', [])}
    rows = []
    for model in packet.get('companyValueContext', {}).get('models', []):
        assessment = facts.get(model['assessmentFactId'], {}).get('payload') or {}
        bundle = facts.get(model['inputFactId'], {}).get('payload') or {}
        # Candidate exclusions are audit context. They do not prove that the
        # selected inputs are absent, and cannot override the admission flag.
        excluded = sorted({str(r.get('reason')) for r in bundle.get('exclusions', []) if r.get('reason')})
        approval = assessment.get('modelApprovalState', '')
        admission = model.get('status') == 'available'
        input_state = assessment.get('inputEligibility', 'unconfirmed')
        rows.append({'assessmentFactId': model['assessmentFactId'],
            'calculationStatus': assessment.get('calculationStatus', 'unconfirmed'),
            'inputEligibility': input_state, 'modelApprovalState': approval,
            'selectedInputEvidenceIds': list(assessment.get('inputEvidenceIds', [])),
            'excludedCandidateReasons': excluded,
            'recordedReasons': list(model.get('blockedReasons', [])),
            'nextStep': ('none' if admission else 'model-review' if approval == 'ai_applied_pending_review'
                         else 'model-validation' if approval in {'shadow', 'unapproved', 'not-registered', 'reference-released'}
                         else 'source-inputs' if input_state in {'partial', 'unavailable'} else 'lineage-and-admission-check'),
            'sourceCollectionResolvesApproval': False, 'valuationAdmitted': admission})
    return {'version': VERSION, 'models': rows, 'authority': 'diagnostic-only'}
