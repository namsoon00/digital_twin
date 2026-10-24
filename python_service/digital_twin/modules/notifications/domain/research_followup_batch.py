"""Group scheduling notices from one committed subject plan, not research answers."""
from copy import deepcopy
import hashlib
import json

VERSION = "research-followup-batch-v1"


def batch_identity(event):
    if event.get("stage") != "research-planned" or not event.get("sourceFollowUp"):
        raise ValueError("research-followup-event-required")
    keys = ("accountId", "symbol", "worldId", "taskId")
    if not all(event.get(key) for key in (*keys, "caseId", "eventId", "question")):
        raise ValueError("research-followup-scope-required")
    return hashlib.sha256(json.dumps([VERSION, *[event[key] for key in keys]]).encode()).hexdigest()


def merge_followup(event, previous=None):
    key = batch_identity(event)
    if previous and previous.get("batchId") != key:
        raise ValueError("research-followup-batch-mismatch")
    batch = deepcopy(previous) if previous else {
        "version": VERSION, "batchId": key, "caseId": "research-batch-" + key,
        **{name: event[name] for name in ("accountId", "symbol", "worldId", "taskId", "eventId", "at")},
        "name": event.get("name", ""), "stage": "research-planned-batch", "sourceQuestionIds": [],
        "authority": "research-status-only", "qualification": "not-empirically-qualified", "items": []}
    if any(row["caseId"] == event["caseId"] for row in batch["items"]):
        return batch
    if len(batch["items"]) >= 12:
        raise ValueError("research-followup-case-limit")
    batch["items"].append({key: deepcopy(event[key]) for key in
        ("caseId", "eventId", "question", "sourceFollowUp", "at")})
    return batch
