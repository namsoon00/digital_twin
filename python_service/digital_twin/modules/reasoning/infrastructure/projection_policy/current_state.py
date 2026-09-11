"""Current state implementation; facade-independent dependencies."""

from __future__ import annotations
from .current_state_ports import CurrentStatePort
from typing import Dict


def world_partitioned_reasoning_enabled(_store: CurrentStatePort) -> bool:
    if _store.incremental_current_state_reasoning_enabled():
        return False
    value = _store.settings.get("ontologyWorldPartitionedReasoningEnabled")
    if value is None:
        return (
            str(_store.settings.get("_reasoningEngineVersion") or "").strip().lower()
            == "v2"
        )
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def incremental_current_state_reasoning_enabled(_store: CurrentStatePort) -> bool:
    value = _store.settings.get("ontologyIncrementalCurrentStateReasoningEnabled")
    if value is None:
        return (
            str(_store.settings.get("_reasoningEngineVersion") or "").strip().lower()
            == "v2"
        )
    return str(value).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def current_state_abox_storage_enabled(_store: CurrentStatePort) -> bool:
    value = _store.settings.get("ontologyCurrentStateAboxStorageEnabled")
    if value is None:
        return False
    return str(value).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def copy_compiled_rule_catalog(catalog: Dict[str, object]) -> Dict[str, object]:
    copied = dict(catalog or {})
    copied["rules"] = [
        dict(item) for item in copied.get("rules") or [] if isinstance(item, dict)
    ]
    copied["inputRelationTypes"] = list(copied.get("inputRelationTypes") or [])
    return copied
