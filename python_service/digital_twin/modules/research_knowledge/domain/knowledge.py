"""Scoped research claims and immutable revisions; no action authority."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from .frameworks import VERSION, PURPOSES

CONTEXT_VERSION = "research-knowledge-context-v1"
SCOPE = ("accountId", "symbol", "worldId")
TEXT_FIELDS = ("question", "mechanism", "assumption", "alternative", "invalidation", "reason")
DISPOSITIONS = ("create", "retain", "revise", "retire")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_time(value):
    parsed = instant(value)
    if parsed is None:
        raise ValueError("knowledge timestamp requires a timezone")
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def source_known_before(fact, cutoff):
    for field in ("sourceAsOf", "observedAt", "publishedAt", "historicalDocumentReference.publishedAt"):
        value = field_value(fact, field)
        if value in (None, ""):
            continue
        parsed = instant(value)
        if parsed is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            parsed = instant(str(value) + "T00:00:00Z")
        if parsed is None or parsed > instant(cutoff):
            return False
    return True


def field_value(row, path):
    value = row
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def claim_identity(scope, framework_id, question):
    normalized = " ".join(question.casefold().split())
    return digest([VERSION, *[scope[key] for key in SCOPE], framework_id, normalized])


def proposal_schema(context, fact_ids):
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    def enum(values):
        return {"type": "string", "enum": list(values) or [""]}
    def array(items, maximum=6):
        return {"type": "array", "items": items, "maxItems": maximum}
    text = {"type": "string", "minLength": 8, "maxLength": 500}
    item = obj({"frameworkId": enum([row["id"] for row in context["frameworks"]]),
        "claimId": enum(["", *[row["claimId"] for row in context["claims"]]]),
        "disposition": enum(DISPOSITIONS), **{key: deepcopy(text) for key in TEXT_FIELDS},
        "evidenceIds": array(enum(fact_ids)), "counterEvidenceIds": array(enum(fact_ids)),
        "missingEvidence": array(text, 3),
        "businessThesisIndex": {"type": "integer", "minimum": -1, "maximum": 1},
        "reviewAfterDays": {"type": "integer", "minimum": 1, "maximum": 90}})
    return obj({"claims": array(item, 3), "coverageNote": text})


def validate_proposals(value, context, packet, usable_ids, business_theses=()):
    if not context:
        if value is not None:
            raise ValueError("knowledge proposal requires captured knowledge context")
        return None
    if (context.get("version") != CONTEXT_VERSION or any(context.get(key) != packet.get(key) for key in SCOPE)
            or context.get("asOf") != packet.get("capturedAt") or not instant(context.get("asOf"))):
        raise ValueError("knowledge context scope or cutoff mismatch")
    if not isinstance(value, dict) or set(value) != {"claims", "coverageNote"}:
        raise ValueError("knowledge research requires claims and coverageNote")
    frameworks = {row["id"]: row for row in context["frameworks"]}
    memories = {row["claimId"]: row for row in context["claims"]}
    known = {row["id"] for row in packet["facts"] if source_known_before(row, packet["capturedAt"])}
    allowed = set(TEXT_FIELDS) | {"frameworkId", "claimId", "disposition", "evidenceIds", "counterEvidenceIds", "missingEvidence", "businessThesisIndex", "reviewAfterDays"}
    def text(raw):
        if not isinstance(raw, str) or not 8 <= len(raw.strip()) <= 500 or re.search(r"\d", raw):
            raise ValueError("knowledge text must be bounded; numbers belong to captured evidence")
        return raw.strip()
    if not isinstance(value["claims"], list) or len(value["claims"]) > 3:
        raise ValueError("knowledge research exceeds claim budget")
    result, seen = [], set()
    for raw in value["claims"]:
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise ValueError("invalid knowledge claim fields")
        if any(not isinstance(raw[key], str) for key in ("frameworkId", "claimId", "disposition")):
            raise ValueError("knowledge identifiers must be strings")
        row = {**deepcopy(raw), **{key: text(raw[key]) for key in TEXT_FIELDS}}
        framework = frameworks.get(row["frameworkId"])
        if not framework or framework["purpose"] not in PURPOSES or row["disposition"] not in DISPOSITIONS:
            raise ValueError("unknown research framework or disposition")
        for key in ("evidenceIds", "counterEvidenceIds"):
            ids = row[key]
            if (not isinstance(ids, list) or len(ids) > 6 or any(not isinstance(key, str) or key not in known or key not in usable_ids for key in ids)):
                raise ValueError("knowledge claim requires usable captured evidence")
            row[key] = list(dict.fromkeys(ids))
        if not isinstance(row["missingEvidence"], list) or len(row["missingEvidence"]) > 3:
            raise ValueError("invalid knowledge evidence gaps")
        row["missingEvidence"] = [text(item) for item in row["missingEvidence"]]
        if not row["evidenceIds"] and not row["counterEvidenceIds"] and not row["missingEvidence"]:
            raise ValueError("unsupported knowledge must declare missing evidence")
        index = row["businessThesisIndex"]
        if type(index) is not int or index < -1 or index >= len(business_theses):
            raise ValueError("knowledge claim business thesis link missing")
        if index >= 0 and framework["purpose"] != "company-value":
            raise ValueError("financial metric checkpoints cannot evaluate another research purpose")
        if type(row["reviewAfterDays"]) is not int or not 1 <= row["reviewAfterDays"] <= 90:
            raise ValueError("invalid knowledge review interval")
        previous = memories.get(row["claimId"])
        if row["disposition"] == "create":
            if row["claimId"]:
                raise ValueError("new knowledge cannot name an existing claim")
            row["claimId"] = claim_identity(packet, row["frameworkId"], row["question"])
            if row["claimId"] in memories:
                raise ValueError("existing knowledge must be explicitly reviewed")
            row["expectedRevision"] = 0
        else:
            if (not previous or previous["frameworkId"] != row["frameworkId"]
                    or any(previous.get(key) != packet.get(key) for key in SCOPE)):
                raise ValueError("knowledge review requires captured scoped claim")
            row["expectedRevision"] = previous["revision"]
            if row["disposition"] == "retain" and previous["frameworkVersion"] != framework["version"]:
                raise ValueError("a new research method requires explicit knowledge revision")
            if row["disposition"] == "retain" and any(row[key] != previous[key] for key in TEXT_FIELDS if key != "reason"):
                raise ValueError("retaining knowledge cannot silently rewrite the claim")
            if previous.get("status") == "retired" and row["disposition"] != "retire":
                prior = {entry["factId"]: entry["contentHash"] for entry in previous.get("evidence", [])}
                changed = any(fact["id"] in row["evidenceIds"] + row["counterEvidenceIds"]
                              and prior.get(fact["id"]) != digest(fact) for fact in packet["facts"])
                if row["disposition"] != "revise" or not changed:
                    raise ValueError("retired knowledge requires explicit revision and new captured evidence")
        if row["claimId"] in seen:
            raise ValueError("duplicate knowledge claim review")
        seen.add(row["claimId"])
        row["purpose"], row["frameworkVersion"] = framework["purpose"], framework["version"]
        row["frameworkHash"] = framework["contentHash"]
        result.append(row)
    return {"claims": result, "coverageNote": text(value["coverageNote"])}


def raw_proposals(value):
    if value is None:
        return None
    fields = set(TEXT_FIELDS) | {"frameworkId", "claimId", "disposition", "evidenceIds", "counterEvidenceIds", "missingEvidence", "businessThesisIndex", "reviewAfterDays"}
    return {"coverageNote": value["coverageNote"], "claims": [
        {key: ("" if key == "claimId" and row["disposition"] == "create" else deepcopy(row[key])) for key in fields} for row in value["claims"]]}


def revise_claim(previous, proposal, packet, input_id, now, business_thesis_id=""):
    if any((previous or {}).get(key, packet[key]) != packet[key] for key in SCOPE):
        raise ValueError("knowledge revision scope mismatch")
    expected = proposal["expectedRevision"]
    if (previous or {}).get("revision", 0) != expected:
        raise ValueError("knowledge revision conflict")
    if not instant(now) or instant(packet["capturedAt"]) > instant(now):
        raise ValueError("knowledge revision cannot precede its input")
    now = canonical_time(now)
    basis = [{"factId": fact["id"], "role": "mixed" if fact["id"] in proposal["evidenceIds"] and fact["id"] in proposal["counterEvidenceIds"] else
              "supports" if fact["id"] in proposal["evidenceIds"] else "contradicts",
              "roles": [role for role, field in (("supports", "evidenceIds"), ("contradicts", "counterEvidenceIds")) if fact["id"] in proposal[field]],
              "independenceKey": digest((fact.get("historicalDocumentReference") or {}).get("evidenceId")
                  or fact.get("sourceDocumentId") or fact.get("reportObservationId") or fact.get("sourceEntityId") or fact["id"]),
              "sourceSnapshotId": fact.get("sourceSnapshotId", packet["sourceSnapshotId"]),
              "sourceWorldId": fact.get("sourceWorldId", packet["worldId"]), "contentHash": digest(fact)}
             for fact in packet["facts"] if fact["id"] in proposal["evidenceIds"] + proposal["counterEvidenceIds"]]
    revision = expected + 1
    return {**{key: packet[key] for key in SCOPE}, "claimId": proposal["claimId"], "revision": revision,
        "revisionId": digest([proposal["claimId"], revision, input_id]),
        "previousRevisionId": (previous or {}).get("revisionId", ""),
        "frameworkId": proposal["frameworkId"], "frameworkVersion": proposal["frameworkVersion"],
        "frameworkHash": proposal["frameworkHash"], "purpose": proposal["purpose"],
        **{key: deepcopy(proposal[key]) for key in TEXT_FIELDS}, "missingEvidence": deepcopy(proposal["missingEvidence"]),
        "evidence": basis, "disposition": proposal["disposition"],
        "status": "retired" if proposal["disposition"] == "retire" else "tracking" if basis else "data-needed",
        "businessThesisId": business_thesis_id or ("" if proposal["disposition"] == "revise" else (previous or {}).get("businessThesisId", "")),
        "assessment": deepcopy((previous or {}).get("assessment", {"status": "awaiting-observation", "qualification": "not-empirically-qualified"})),
        "knownAt": now, "asOf": packet["capturedAt"], "executionInputId": input_id,
        "nextCheckAt": (instant(now) + timedelta(days=proposal["reviewAfterDays"])).isoformat().replace("+00:00", "Z"),
        "authority": "research-only", "qualification": "not-empirically-qualified"}
