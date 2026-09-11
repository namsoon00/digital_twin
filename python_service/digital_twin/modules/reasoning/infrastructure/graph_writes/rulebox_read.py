"""Rulebox read implementation; facade-independent dependencies."""

from __future__ import annotations
from .rulebox_read_ports import RuleboxReadPort, RuleboxSnapshotBindings
from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
from digital_twin.modules.model_registry.contracts import GRAPH_REASONER_VERSION
from digital_twin.modules.model_registry.contracts import rulebox_governance_candidates
from digital_twin.infrastructure.graph_store_rulebox import (
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
    rulebox_snapshot_from_rows,
)
from digital_twin.modules.reasoning.infrastructure.static_seed.identity import (
    rulebox_runtime_metadata,
)
from digital_twin.modules.reasoning.infrastructure.typeql.profiles import (
    typedb_native_reasoning_profile,
)
from typing import Dict
import copy
import time


def rulebox_snapshot(
    _store: RuleboxReadPort, *, _bindings: RuleboxSnapshotBindings
) -> Dict[str, object]:
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().rulebox_snapshot()
    cache_age = time.time() - float(_store._rulebox_snapshot_cache_at or 0)
    if (
        _store._rulebox_snapshot_cache_result
        and cache_age <= _store.rulebox_snapshot_cache_seconds()
    ):
        cached = copy.deepcopy(_store._rulebox_snapshot_cache_result)
        cached["cached"] = True
        cached["ruleBoxSnapshotCached"] = True
        return cached
    manifest = _store.read_seed_static_manifest()
    rulebox_snapshot_id = (
        str((manifest.get("metadata") or {}).get("ruleboxSnapshotId") or "").strip()
        if str(manifest.get("status") or "") == "ok"
        else ""
    )
    full_cache_age = time.time() - float(
        _store._rulebox_snapshot_cache_full_load_at or 0
    )
    maximum_full_cache_age = max(
        300.0,
        min(1800.0, _store.rulebox_snapshot_cache_seconds() * 10.0),
    )
    cached_snapshot_id = str(
        (_store._rulebox_snapshot_cache_result or {}).get("ruleboxSnapshotId") or ""
    ).strip()
    if (
        _store._rulebox_snapshot_cache_result
        and rulebox_snapshot_id
        and cached_snapshot_id == rulebox_snapshot_id
        and full_cache_age <= maximum_full_cache_age
    ):
        # RuleBox rows are immutable under a content-addressed static
        # manifest. A lightweight manifest read is enough to prove that
        # the executable policy is unchanged; periodically force a full
        # governance refresh so cross-process version history remains
        # visible as well.
        _store._rulebox_snapshot_cache_at = time.time()
        cached = copy.deepcopy(_store._rulebox_snapshot_cache_result)
        cached["cached"] = True
        cached["ruleBoxSnapshotCached"] = True
        cached["ruleBoxManifestRevalidated"] = True
        return cached
    try:
        if rulebox_snapshot_id:
            entities = [
                *_store.read_entity_rows(["RuleBox"], snapshot_id=rulebox_snapshot_id),
                *_store.read_entity_rows(["RuleBoxGovernance"]),
            ]
            relations = [
                *_store.read_relation_rows(
                    ["RuleBox"], snapshot_id=rulebox_snapshot_id
                ),
                *_store.read_relation_rows(["RuleBoxGovernance"]),
            ]
        else:
            entities = _store.read_entity_rows(["RuleBox", "RuleBoxGovernance"])
            relations = _store.read_relation_rows(["RuleBox", "RuleBoxGovernance"])
    except Exception as error:  # noqa: BLE001 - admin read model must fail closed.
        rules = rulebox_rules_to_payload(
            _store._last_rules or default_graph_inference_rules()
        )
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "source": "typedb-typeql",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
            "engineVersion": GRAPH_REASONER_VERSION,
            "rules": [],
            "ruleCount": 0,
            "conditionCount": 0,
            "derivationCount": 0,
            "relationTypes": [],
            "defaultsFallbackUsed": False,
            "bootstrapAvailable": True,
            "bootstrapRuleCount": len(rules),
            "bootstrapRules": rules,
            "versions": [],
            "versionCount": 0,
            "changeCandidates": rulebox_governance_candidates([], []),
        }
    rowsets = {
        "rules": [
            row
            for row in entities
            if _bindings.entity_node_kind(row) == "rule"
            and row.get("ontologyBox") == "RuleBox"
        ],
        "conditions": [
            row
            for row in entities
            if _bindings.entity_node_kind(row) == "rule-condition"
            and row.get("ontologyBox") == "RuleBox"
        ],
        "derivations": [
            row
            for row in entities
            if _bindings.entity_node_kind(row) == "relation-template"
            and row.get("ontologyBox") == "RuleBox"
        ],
        "relationTypes": _bindings.relation_type_rows_from_derivations(
            entities, relations
        ),
        "versions": [
            row
            for row in entities
            if _bindings.entity_node_kind(row) == "rulebox-version"
            and row.get("ontologyBox") == "RuleBoxGovernance"
        ],
        "candidates": [
            row
            for row in entities
            if _bindings.entity_node_kind(row) == "rule-change-candidate"
            and row.get("ontologyBox") == "RuleBoxGovernance"
        ],
    }
    snapshot = rulebox_snapshot_from_rows(rowsets, "typedb-typeql")
    snapshot.update(
        {
            "graphStore": "typedb",
            "source": "typedb-typeql",
            "ruleboxSnapshotId": rulebox_snapshot_id,
        }
    )
    snapshot.update(
        rulebox_runtime_metadata(
            snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
        )
    )
    if snapshot.get("status") == "ok":
        try:
            _store._last_rules = rulebox_rules_from_payload(
                {"rules": snapshot.get("rules") or []}
            )
        except ValueError:
            pass
    snapshot["nativeReasoningProfile"] = typedb_native_reasoning_profile(
        snapshot.get("rules") or []
    )
    snapshot["ruleBoxSnapshotCached"] = False
    _store._rulebox_snapshot_cache_at = time.time()
    _store._rulebox_snapshot_cache_full_load_at = _store._rulebox_snapshot_cache_at
    _store._rulebox_snapshot_cache_result = copy.deepcopy(snapshot)
    return snapshot
