"""Successful-delivery memory retains exactly the fields cited by explanations."""
import copy

from digital_twin.modules.ai_orchestration.domain.insight_contract import resolve_ref


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
