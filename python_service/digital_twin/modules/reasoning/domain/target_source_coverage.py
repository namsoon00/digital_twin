"""Producer-bound completeness of a target's factual quality scopes.

This is a source/persistence contract, not investment evidence. A partial
portfolio graph remains partial; only quality assertions for actually assembled
subjects may use omission as information. Legacy or altered packets retain the
existing complete-source repair path.
"""

import hashlib
import json
from typing import Iterable, Mapping, Set

from .ontology_change_impact import scope_family, scope_symbol
from .reasoning_source_snapshot import reasoning_source_snapshot_id


TARGET_QUALITY_COVERAGE_VERSION = "target-source-quality-coverage-v1"
TARGET_QUALITY_COVERAGE_KEY = "targetSourceQualityCoverage"


def _fingerprint(value) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _plan_fingerprint(scope_plan) -> str:
    return _fingerprint(sorted(
        (dict(row) for row in scope_plan or []),
        key=lambda row: str(row.get("scopeId") or ""),
    ))


def attach_target_quality_coverage(
    graph, account_id: str, observed_at: str, symbols: Iterable[str],
) -> None:
    """Attest only after the bounded factual/model assembly and scoped identity."""
    selected = sorted({str(value).strip().upper() for value in symbols or [] if str(value).strip()})
    proof = {
        "version": TARGET_QUALITY_COVERAGE_VERSION,
        "accountId": str(account_id or ""),
        "worldId": str(graph.worldview.get("worldId") or ""),
        "sourceObservedAt": str(observed_at or ""),
        "sourceSnapshotId": reasoning_source_snapshot_id(account_id, observed_at),
        "symbols": selected,
        "scopePlanFingerprint": _plan_fingerprint(graph.worldview.get("scopePlan") or []),
    }
    graph.worldview[TARGET_QUALITY_COVERAGE_KEY] = {**proof, "fingerprint": _fingerprint(proof)}


def complete_target_quality_scope_ids(
    graph, active_metadata: Mapping[str, object], targets: Iterable[str],
) -> Set[str]:
    """Validate the producing image before granting target-local absence proof."""
    worldview = graph.worldview or {}
    proof = worldview.get(TARGET_QUALITY_COVERAGE_KEY)
    if not isinstance(proof, Mapping) or proof.get("version") != TARGET_QUALITY_COVERAGE_VERSION:
        return set()
    try:
        if proof.get("fingerprint") != _fingerprint({key: value for key, value in proof.items() if key != "fingerprint"}):
            return set()
    except (TypeError, ValueError):
        return set()
    account_id = str(proof.get("accountId") or "")
    observed_at = str(proof.get("sourceObservedAt") or "")
    world_id = str(worldview.get("worldId") or "")
    symbols = proof.get("symbols")
    if (
        not account_id or not observed_at or not world_id
        or not isinstance(symbols, (list, tuple))
        or account_id != str(graph.portfolio_id or "")
        or proof.get("worldId") != world_id
        or str((active_metadata or {}).get("worldId") or world_id) != world_id
        or str(worldview.get("sourceObservedAt") or observed_at) != observed_at
        or proof.get("sourceSnapshotId") != reasoning_source_snapshot_id(account_id, observed_at)
        or proof.get("scopePlanFingerprint") != _plan_fingerprint(worldview.get("scopePlan") or [])
    ):
        return set()
    requested = {str(value).strip().upper() for value in targets or [] if str(value).strip()}
    covered = requested.intersection(str(value).strip().upper() for value in symbols)
    return {
        str(row.get("scopeId") or "")
        for row in list(worldview.get("scopePlan") or []) + list((active_metadata or {}).get("scopePlan") or [])
        if scope_symbol(row.get("scopeId")) in covered
        and scope_family(row.get("scopeId")) == "quality"
        and str(row.get("scopeFamily") or scope_family(row.get("scopeId"))).lower() == "quality"
    }
