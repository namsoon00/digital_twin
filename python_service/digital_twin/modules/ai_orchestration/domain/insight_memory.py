"""Successful-delivery memory retains exactly the fields cited by explanations."""
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
        if fact.get("kind") == "stock" and fact["id"] in result["evidenceIds"]:
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
        parts = ref["field"].split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = copy.deepcopy(value)
    return list(selected.values())
