"""Successful-delivery memory retains exactly the fields cited by explanations."""
from digital_twin.modules.reasoning.contracts import OBSERVATION_QUOTE_KINDS

import copy

from digital_twin.modules.ai_orchestration.domain.insight_contract import resolve_ref, instant


def restore_legacy_receipt(receipt, original):
    """Recover omitted fields from that delivery's frozen input, never today's quote.

    This creates memory for a new analysis. Stored receipts and historical model
    inputs remain unchanged, and every restored field retains its source task.
    """
    packet = original.get("input", {})
    if (not receipt or receipt.get("insightVersion") or not receipt.get("inputFingerprint")
            or receipt["inputFingerprint"] != original.get("inputFingerprint")
            or any(packet.get(key) != receipt.get(key) for key in ("accountId", "symbol", "taskId"))
            or original.get("publication", {}).get("jobId") != receipt.get("jobId")):
        return receipt
    captured, delivered = instant(packet.get("capturedAt")), instant(receipt.get("deliveredAt"))
    if not captured or not delivered or captured > delivered:
        return receipt
    facts = {fact["id"]: fact for fact in packet.get("facts", []) if fact.get("id")}
    result, restored = copy.deepcopy(receipt), []
    for index, saved in enumerate(result.get("facts", [])):
        source = facts.get(saved.get("id"), {})
        source_at = instant(source.get("sourceAsOf") or source.get("asOf"))
        if (source.get("kind") != "stock" or source.get("symbol") != receipt["symbol"]
                or saved.get("id") not in original.get("evidenceIds", [])
                or not source_at or source_at > captured
                or any(key not in source or source[key] != value for key, value in saved.items())):
            continue
        added = sorted(set(source) - set(saved))
        if added:
            result["facts"][index] = copy.deepcopy(source)
            restored.append({"factId": saved["id"], "fields": added})
    if restored:
        result["evidenceRestoration"] = {"source": "original-delivered-task-input", "taskId": receipt["taskId"],
            "inputFingerprint": receipt["inputFingerprint"], "capturedAt": packet["capturedAt"], "facts": restored}
    return result


def receipt_facts(result):
    packet, selected = result["input"], {}
    for fact in packet.get("facts", []):
        if fact.get("kind") in OBSERVATION_QUOTE_KINDS and fact["id"] in result["evidenceIds"]:
            selected[fact["id"]] = copy.deepcopy(fact)
    refs = [ref for values in result.get("claimEvidence", {}).values() for ref in values]
    refs += [row[side] for row in result.get("observations", []) for side in ("left", "right")]
    for ref in refs:
        if ref["period"] != "current":
            continue
        fact, value = resolve_ref(packet, ref)
        target = selected.setdefault(fact["id"], {key: copy.deepcopy(fact[key]) for key in (
            "id", "kind", "label", "symbol", "sourceEntityId", "sourceSnapshotId", "sourceWorldId", "source",
            "observationSource", "sourceAsOf", "asOf", "publishedAt", "currency", "freshnessStatus", "sourceTrustState",
            "verificationState", "valuationDecisionEligible", "judgementEvidenceUsable", "evidenceCategory") if key in fact})
        retain_validation_fields(target, fact)
        parts = ref["field"].split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = copy.deepcopy(value)
    return list(selected.values())


RECEIPT_EVIDENCE_VERSION = 'receipt-evidence-provenance-v1'
VALIDATION_FIELDS = ('historicalDocumentReference', 'questionSourceReference', 'evidenceView',
    'evidenceGovernance', 'promptEvidenceAdmission', 'reportObservationId', 'periodEnd',
    'sourceRevision', 'url', 'documentVerified')


def retain_validation_fields(target, fact):
    """Preserve the original validation witness alongside a cited scalar."""
    for key in VALIDATION_FIELDS:
        if key in fact:
            target[key] = copy.deepcopy(fact[key])
    return target


def restore_receipt_provenance(receipt, original):
    """Repair a projection for the next input only, from the exact delivered task.

    Recursive equality verifies all saved scalars before restoring omitted source
    witnesses. An ID match or today's source text never suffices.
    """
    packet = original.get('input', {})
    if (not receipt.get('inputFingerprint') or receipt['inputFingerprint'] != original.get('inputFingerprint')
            or any(not receipt.get(key) or packet.get(key) != receipt[key] for key in ('accountId', 'symbol', 'taskId'))
            or original.get('publication', {}).get('jobId') != receipt.get('jobId')
            or original.get('insightVersion') != receipt.get('insightVersion')):
        return receipt
    captured, delivered = instant(packet.get('capturedAt')), instant(receipt.get('deliveredAt'))
    if not captured or not delivered or captured > delivered:
        return receipt

    def subset(saved, source):
        if isinstance(saved, dict):
            return isinstance(source, dict) and all(k in source and subset(v, source[k]) for k, v in saved.items())
        return saved == source

    facts = {f['id']: f for f in packet.get('facts', [])}
    result, restored = copy.deepcopy(receipt), []
    for saved in result.get('facts', []):
        source = facts.get(saved.get('id'))
        if source and source['id'] in original.get('evidenceIds', []) and subset(saved, source):
            before = copy.deepcopy(saved)
            retain_validation_fields(saved, source)
            if saved != before:
                restored.append(saved['id'])
    if restored:
        from digital_twin.modules.reasoning.contracts import content_hash
        result['evidenceRestoration'] = {'version': RECEIPT_EVIDENCE_VERSION,
            'source': 'original-delivered-task-input', 'taskId': receipt['taskId'],
            'inputFingerprint': receipt['inputFingerprint'], 'capturedAt': packet['capturedAt'],
            'factIds': restored, 'sourceHashes': {key: content_hash(facts[key]) for key in restored}}
    return result
