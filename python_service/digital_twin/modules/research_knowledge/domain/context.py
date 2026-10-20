"""Bounded knowledge retrieval with explicit scope, time and coverage limits."""
from copy import deepcopy
from collections import Counter
import json

from .frameworks import service_research_policy
from .knowledge import CONTEXT_VERSION, SCOPE, digest, field_value, instant, source_known_before


def build_context(packet, frameworks, records, max_bytes=32000, usable_ids=(), progress=()):
    cutoff = instant(packet.get("capturedAt"))
    if not cutoff or any(not packet.get(key) for key in SCOPE):
        raise ValueError("knowledge context requires a scoped cutoff")
    admitted = set(usable_ids)
    facts = [row for row in packet.get("facts", []) if row["id"] in admitted and source_known_before(row, packet["capturedAt"])]
    admitted = {row["id"] for row in facts}
    methods = []
    latest_methods = {}
    for source in frameworks:
        if not instant(source.get("knownAt")) or instant(source["knownAt"]) > cutoff:
            continue
        if source["id"] not in latest_methods or source["version"] > latest_methods[source["id"]]["version"]:
            latest_methods[source["id"]] = source
    crypto = any(row.get("kind") == "crypto-asset" and row.get("symbol") == packet["symbol"] for row in packet.get("facts", []))
    for source in latest_methods.values():
        if crypto != ("crypto" in source.get("assetClasses", [])):
            continue
        method = deepcopy(source)
        coverage = []
        for required in method["requirements"]:
            categories = required["category"] if isinstance(required["category"], list) else [required["category"]]
            candidates = [row for row in facts if row.get("evidenceCategory") in categories]
            fields = required["fields"]
            matches = {field: [row["id"] for row in candidates if field_value(row, field) not in (None, "", [], {})] for field in fields}
            ids = sorted({key for values in matches.values() for key in values}) if fields else [row["id"] for row in candidates]
            coverage.append({"requirement": required["key"], "label": required["label"], "factIds": ids[:8],
                "status": "present-needs-review" if (all(matches.values()) if fields else ids) else "missing",
                "missingFields": [field for field, values in matches.items() if not values],
                "qualification": "availability-only"})
        method["coverage"] = coverage
        method.update(authority="research-reference-only", qualification="methodology-not-prediction-validation")
        methods.append(method)
    value = {"version": "research-knowledge-context-v3", **{key: packet[key] for key in SCOPE}, "asOf": packet["capturedAt"],
             "policy": service_research_policy(), "frameworks": methods, "claims": [], "excluded": [],
             "authority": "historical-research-and-methodology-only"}
    value["companyModel"] = {"symbol": packet["symbol"],
        "sourceFactIds": [row["id"] for row in facts if row.get("evidenceCategory") == "company"][:12],
        "authority": "source-references-and-research-only"}
    latest = {}
    for row in records:
        if any(row.get(key) != packet[key] for key in SCOPE):
            raise ValueError("cross-scope research knowledge")
        known = instant(row.get("knownAt"))
        if not known or known > cutoff:
            continue
        if row["claimId"] not in latest or row["revision"] > latest[row["claimId"]]["revision"]:
            latest[row["claimId"]] = row
    ordered = sorted(latest.values(), key=lambda row: (row.get("status") == "retired", row.get("nextCheckAt", ""), row["claimId"]))
    if len(json.dumps(value, ensure_ascii=False).encode()) > max_bytes:
        raise ValueError("research methodology exceeds context budget")
    from .progress import progress_memory
    value["progress"] = []
    exclusions = []
    for row in progress[:2]:
        if any(row.get(key) != packet[key] for key in SCOPE):
            raise ValueError("cross-scope research progress")
        if not instant(row.get("knownAt")) or instant(row["knownAt"]) > cutoff:
            continue
        selected = progress_memory(row)
        value["progress"].append(selected)
        if len(json.dumps(value, ensure_ascii=False).encode()) > max_bytes - 6000:
            value["progress"].pop()
            exclusions.append({"progressId": row["progressId"], "reason": "progress-byte-budget"})
    for row in ordered:
        if len(value["claims"]) >= 6:
            exclusions.append({"claimId": row["claimId"], "reason": "claim-budget"})
            continue
        selected = deepcopy(row)
        current = {fact["id"]: fact for fact in packet.get("facts", [])}
        selected["evidenceState"] = [{"factId": ref["factId"], "state":
            "not-in-selected-input" if ref["factId"] not in current else
            "currently-ineligible" if ref["factId"] not in admitted else
            "same-captured-evidence" if digest(current[ref["factId"]]) == ref["contentHash"] else
            "changed-snapshot-needs-review"} for ref in row.get("evidence", [])]
        selected["reviewDue"] = row.get("status") != "retired" and bool(instant(row.get("nextCheckAt")) and instant(row["nextCheckAt"]) <= cutoff)
        selected["authority"] = "historical-context-only"
        value["claims"].append(selected)
        if len(json.dumps(value, ensure_ascii=False).encode()) > max_bytes - 1500:
            value["claims"].pop()
            exclusions.append({"claimId": row["claimId"], "reason": "byte-budget"})
    # Audit metadata is part of the limit too. A growing exclusion ledger must
    # not undo successful claim budgeting. Keep counts/hash and a small sample.
    while True:
        value["excluded"] = deepcopy(exclusions[:4])
        value["exclusionSummary"] = {"count": len(exclusions), "reasonCounts": dict(Counter(row["reason"] for row in exclusions)),
            "ledgerHash": digest(exclusions), "sampleLimit": 4,
            "meaning": "not-selected-not-disproved; source revisions remain in research storage"}
        value["contextHash"] = digest({key: item for key, item in value.items() if key != "contextHash"})
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) <= max_bytes:
            return value
        if value["claims"]:
            removed = value["claims"].pop()
            exclusions.append({"claimId": removed["claimId"], "reason": "byte-budget"})
        elif value["progress"]:
            removed = value["progress"].pop()
            exclusions.append({"progressId": removed["progressId"], "reason": "progress-byte-budget"})
        else:
            raise ValueError("research methodology and coverage metadata exceed context budget")


def context_material_identity(context):
    """Only authored method releases and independent observations can wake AI.

    An AI rewriting its own claim must not create an endless research loop.
    The revised claim will be retrieved on the next genuine evidence change.
    """
    return digest({"policy": context.get("policy", {}).get("id"),
                   "frameworks": [(row["id"], row["version"], row.get("contentHash")) for row in context.get("frameworks", [])],
                   "assessments": sorted({row.get("assessment", {}).get("assessmentId", "")
                                          for row in context.get("claims", [])} - {""})})


def validate_context(context, packet):
    if (context.get("version") not in {CONTEXT_VERSION, "research-knowledge-context-v2", "research-knowledge-context-v3"} or any(context.get(key) != packet.get(key) for key in SCOPE)
            or context.get("asOf") != packet.get("capturedAt")
            or context.get("authority") != "historical-research-and-methodology-only"):
        raise ValueError("research knowledge context ownership mismatch")
    material = {key: value for key, value in context.items() if key not in {"contextHash", "ontology"}}
    if digest(material) != context.get("contextHash"):
        raise ValueError("research knowledge context fingerprint mismatch")
    if len(json.dumps(context, ensure_ascii=False, allow_nan=False).encode()) > 64000:
        raise ValueError("research knowledge context exceeds its independent budget")
    graph = context.get("ontology")
    if graph and (graph.get("worldId") != packet["worldId"] or graph.get("actionAuthority") != "none"
                  or any(row.get("properties", {}).get("reasoningEligible") is not False
                         or row.get("properties", {}).get("investmentJudgmentEligible") is not False
                         for row in graph.get("entities", []))):
        raise ValueError("research-memory graph cannot acquire investment authority")
