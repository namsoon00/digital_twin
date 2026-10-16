"""Dated read progress is research audit, never a fact or an authored thesis."""
from copy import deepcopy
import json
from .knowledge import SCOPE, digest, canonical_time, instant

VERSION = "research-knowledge-progress-v1"


def progress_record(packet, trace, now):
    if not trace:
        return None
    if instant(now) is None or instant(packet["capturedAt"]) > instant(now):
        raise ValueError("research progress cannot precede its input")
    selected = {row["id"] for row in packet["facts"]}
    read = {row["id"]: row for step in trace for item in step["reads"] for row in item["result"].get("facts", [])}
    decision = next((step["decision"] for step in reversed(trace) if step.get("decision")), {})
    references = [{"factId": key, "contentHash": digest(row), "sourceSnapshotId": row.get("sourceSnapshotId", packet["sourceSnapshotId"]),
                   "sourceWorldId": row.get("sourceWorldId", packet["worldId"]), "selected": key in selected}
                  for key, row in sorted({**read, **{row["id"]: row for row in packet["facts"]}}.items())]
    context = packet.get("researchKnowledge", {})
    input_ids = [step["inputId"] for step in trace]
    return {"version": VERSION, **{key: packet[key] for key in SCOPE},
        "progressId": digest([*[packet[key] for key in SCOPE], input_ids]),
        "knownAt": canonical_time(now), "asOf": packet["capturedAt"], "executionInputIds": input_ids,
        "sourceSnapshotId": packet["sourceSnapshotId"], "traceHash": digest(trace),
        "status": packet.get("retrieval", {}).get("status", "deferred"),
        "questionScopes": deepcopy(decision.get("questionScopes", [])),
        "missingEvidence": deepcopy(decision.get("missingEvidence", [])),
        "readReferences": references[:32], "omittedReferenceCount": max(0, len(references)-32),
        "recalledClaimRevisionIds": [row["revisionId"] for row in context.get("claims", [])],
        "recalledProgressIds": [row["progressId"] for row in context.get("progress", [])],
        "authority": "read-progress-only", "qualification": "not-an-answer-or-thesis"}


def progress_memory(record, max_bytes=5000):
    value = deepcopy(record)
    # References are pointers, and omitted pointers are explicit. Never truncate
    # a missing requirement into a different question or a factual assertion.
    for key in ("readReferences", "questionScopes"):
        while value[key] and len(json.dumps(value, ensure_ascii=False).encode()) > max_bytes:
            value[key].pop()
            field = "omittedReferenceCount" if key == "readReferences" else "omittedQuestionCount"
            value[field] = value.get(field, 0) + 1
    return value
