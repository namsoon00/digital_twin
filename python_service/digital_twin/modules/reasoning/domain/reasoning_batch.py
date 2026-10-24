"""Immutable source boundaries shared by queue claims and execution batches."""

from copy import deepcopy
import hashlib
import json
from typing import Mapping

from .event_types import MAX_REASONING_SOURCE_FACTS_PER_EVENT, ONTOLOGY_REASONING_REQUESTED


SIBLING_EXECUTION_BATCH_VERSION = "verified-source-sibling-execution-v1"


def sibling_execution_identity(event):
    """Share preparation only between complete shards of one source event.

    This is execution grouping, not queue coalescing or a reusable history
    cache. Every job/source and per-subject result remains independently
    owned. Unknown payload fields remain in the compatibility fingerprint.
    """
    payload = event.get("payload") or {}
    if not isinstance(payload, Mapping):
        return None
    shard = payload.get("reasoningShard") or {}
    boundary = payload.get("verifiedSourceSnapshot") or {}
    if not all(isinstance(value, Mapping) for value in (payload, shard, boundary)):
        return None
    if (event.get("name") != ONTOLOGY_REASONING_REQUESTED
            or payload.get("trigger") != "verified-monitor-snapshot"
            or payload.get("candidateValidationReplay")
            or payload.get("validationPair")
            or payload.get("fullReconciliation")
            or payload.get("coalescedReasoningChanges")
            or str(payload.get("importanceGate") or "").lower().strip() == "non-fungible"
            or str(payload.get("workClass") or "").upper() in {"DIRECT", "RESEARCH", "GOVERNANCE"}
            or shard.get("contractVersion") != "reasoning-event-shard-v1"
            or not shard.get("parentEventId")):
        return None
    symbols = payload.get("affectedSymbols") or payload.get("symbols") or payload.get("targetSymbols") or []
    shard_symbols = shard.get("symbols") or []
    if (not isinstance(symbols, list) or not symbols or not isinstance(shard_symbols, list)
            or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
            or any(not isinstance(symbol, str) for symbol in shard_symbols)
            or len(set(symbols)) != len(symbols)
            or sorted(symbols) != sorted(shard_symbols)):
        return None
    if (type(shard.get("shardIndex")) is not int or type(shard.get("shardCount")) is not int
            or not 0 <= shard["shardIndex"] < shard["shardCount"]):
        return None
    account = str(payload.get("accountId") or "")
    if (not account or boundary.get("accountId") != account
            or payload.get("accountIds", [account]) != [account]
            or not boundary.get("snapshotId") or not boundary.get("generatedAt")
            or payload.get("sourceObservedAt") != boundary.get("generatedAt")
            or boundary.get("sourceFactCoverageComplete") is not True):
        return None
    facts = payload.get("sourceFacts") or []
    if (not isinstance(facts, list) or not facts
            or len(facts) > MAX_REASONING_SOURCE_FACTS_PER_EVENT
            or boundary.get("expectedSourceFactCount") != len(facts)
            or boundary.get("capturedSourceFactCount") != len(facts)
            or any(not isinstance(fact, Mapping)
                   or not isinstance(fact.get("factId"), str)
                   or not fact["factId"].strip() for fact in facts)
            or len({fact["factId"] for fact in facts}) != len(facts)):
        return None
    boundaries = payload.get("verifiedSourceSnapshots") or []
    if not isinstance(boundaries, list) or any(
        not isinstance(value, Mapping)
        or any(value.get(key) != boundary.get(key) for key in (
            "accountId", "snapshotId", "generatedAt", "sourceFactCoverageComplete",
            "expectedSourceFactCount", "capturedSourceFactCount"))
        for value in boundaries
    ):
        return None
    subject_fields = {
        "affectedSymbols", "symbols", "targetSymbols", "sourceFacts",
        "changedFieldsBySymbol", "factTypesBySymbol", "factRevisionsBySymbol", "revisionVectorsBySymbol",
    }
    common = deepcopy({key: value for key, value in payload.items() if key not in subject_fields})
    common["reasoningShard"].pop("shardIndex", None)
    common["reasoningShard"].pop("symbols", None)
    contract = common.get("factChangeContract") or {}
    if (not isinstance(contract, Mapping) or contract.get("status") != "ready"
            or contract.get("unclassifiedFactTypes")):
        return None
    unclassified = contract.get("unclassifiedFactTypesBySymbol") or {}
    if not isinstance(unclassified, Mapping) or any(unclassified.values()):
        return None
    for key in ("scopeFamiliesBySymbol", "dependencyKeysBySymbol", "dependencyKeysCompleteBySymbol", "unclassifiedFactTypesBySymbol", "eventClassesBySymbol"):
        contract.pop(key, None)
    for value in [common["verifiedSourceSnapshot"], *(common.get("verifiedSourceSnapshots") or [])]:
        for key in ("expectedSourceFactCount", "capturedSourceFactCount"):
            value.pop(key, None)
    digest = hashlib.sha256(json.dumps(common, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return (SIBLING_EXECUTION_BATCH_VERSION, digest)


def reasoning_batch_fits(events, symbol_limit):
    """Bound a combined execution before leasing or entering TypeDB.

    Repeated fact identities stay separate: request construction counts each
    event's declared facts, so deduplication must not overstate coverage.
    """
    symbols = set()
    seen_facts = set()
    seen_shards = set()
    identity = sibling_execution_identity(events[0]) if events else None
    sibling_batch = len(events) > 1 and identity is not None
    for event in events:
        payload = event.get("payload") or {}
        values = payload.get("affectedSymbols") or payload.get("symbols") or payload.get("targetSymbols") or []
        if isinstance(values, str):
            values = [values]
        current_symbols = {str(value or "").upper().strip() for value in values}
        if sibling_batch:
            if sibling_execution_identity(event) != identity:
                return False
            shard_index = payload["reasoningShard"]["shardIndex"]
            if shard_index in seen_shards or symbols.intersection(current_symbols):
                return False
            seen_shards.add(shard_index)
            facts = {str(fact["factId"]) for fact in payload.get("sourceFacts") or []}
            if seen_facts.intersection(facts):
                return False
            seen_facts.update(facts)
        symbols.update(current_symbols)
    return len(symbols - {""}) <= symbol_limit and len(seen_facts) <= MAX_REASONING_SOURCE_FACTS_PER_EVENT


def reasoning_batch_key(event, lane):
    sibling = sibling_execution_identity(event)
    if sibling is not None:
        return (*sibling, str(lane or ""))
    payload = dict(event.get("payload") or {})
    accounts = payload.get("accountIds") or []
    if isinstance(accounts, str):
        accounts = [accounts]
    symbols = payload.get("affectedSymbols") or payload.get("symbols") or payload.get("targetSymbols") or []
    if isinstance(symbols, str):
        symbols = [symbols]
    account_ids = tuple(sorted({
        str(value or "").strip()
        for value in [*accounts, payload.get("accountId")]
        if str(value or "").strip()
    }))
    boundary = payload.get("verifiedSourceSnapshot")
    boundary = dict(boundary) if isinstance(boundary, Mapping) else {}
    boundaries = [value for value in payload.get("verifiedSourceSnapshots") or []
                  if isinstance(value, Mapping) and value]
    pair = payload.get("validationPair")
    pair = dict(pair) if isinstance(pair, Mapping) else {}
    return (
        account_ids,
        bool(payload.get("candidateValidationReplay")),
        str(pair.get("inputMode") or ""),
        str(pair.get("portfolioContextHash") or ""),
        tuple(sorted({str(value).upper().strip() for value in symbols if str(value or "").strip()})),
        str(boundary.get("accountId") or ""),
        str(boundary.get("snapshotId") or ""),
        str(boundary.get("generatedAt") or ""),
        tuple(sorted((str(value.get("accountId") or ""),
                      str(value.get("snapshotId") or ""),
                      str(value.get("generatedAt") or "")) for value in boundaries)),
        str(lane or ""),
    )
