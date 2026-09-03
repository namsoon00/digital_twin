"""Current-state ABox persistence contracts.

Logical reasoning generations remain immutable audit identities.  These
contracts describe the bounded physical representation used by TypeDB so a
new observation does not require another permanent copy of every fact in an
affected scope.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping


CURRENT_STATE_ABOX_CONTRACT_VERSION = "current-abox-copy-on-write-v3"
LEGACY_CURRENT_STATE_ABOX_PERSISTENCE_MODE = "current-state-dual-slot-v1"
CURRENT_STATE_ABOX_PERSISTENCE_MODE = "current-state-copy-on-write-v2"
CURRENT_STATE_ABOX_PERSISTENCE_MODES = frozenset({
    LEGACY_CURRENT_STATE_ABOX_PERSISTENCE_MODE,
    CURRENT_STATE_ABOX_PERSISTENCE_MODE,
})
CURRENT_STATE_TRANSITION_STAGES = (
    "source-bound",
    "patch-applied",
    "inferred",
    "synthesis-persisted",
    "completed",
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def current_state_slot_id(world_id: object, scope_id: object, slot: object) -> str:
    """Return a bounded physical generation id for one scope slot."""

    clean_slot = "b" if _clean(slot).lower() == "b" else "a"
    seed = _clean(world_id) + "|" + _clean(scope_id)
    scope_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return "abox-current:" + scope_hash + ":" + clean_slot


def current_state_slot_name(generation_id: object) -> str:
    value = _clean(generation_id)
    if value.startswith("abox-current:") and value.endswith(":b"):
        return "b"
    if value.startswith("abox-current:") and value.endswith(":a"):
        return "a"
    return ""


def next_current_state_slot(
    world_id: object,
    scope_id: object,
    active_generation_id: object = "",
) -> str:
    """Choose the inactive physical slot for an atomic scope replacement."""

    active_slot = current_state_slot_name(active_generation_id)
    return current_state_slot_id(world_id, scope_id, "a" if active_slot != "a" else "b")


def copy_on_write_generation_id(
    world_id: object,
    scope_id: object,
    logical_generation_id: object,
    transition_id: object,
) -> str:
    """Return a retry-stable physical generation for one copy-on-write patch.

    The transition identity is the durable projection run when available. It
    prevents a later return to identical market values from colliding with a
    retained rollback generation, while giving retries of the same audited
    transition the same storage identity.
    """

    seed = "|".join([
        _clean(world_id),
        _clean(scope_id),
        _clean(logical_generation_id),
        _clean(transition_id),
    ])
    return "abox-current-cow:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def is_current_state_persistence_mode(value: object) -> bool:
    return _clean(value) in CURRENT_STATE_ABOX_PERSISTENCE_MODES


@dataclass(frozen=True)
class FactMutation:
    storage_id: str
    owner_kind: str
    operation: str
    scope_id: str
    logical_generation_id: str
    physical_generation_id: str
    content_fingerprint: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class WorldRevisionVector:
    world_id: str
    logical_manifest_id: str
    base_manifest_id: str = ""
    scope_logical_generations: Dict[str, str] = field(default_factory=dict)
    scope_physical_generations: Dict[str, str] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(asdict(self))

    def to_dict(self) -> Dict[str, object]:
        return {**asdict(self), "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class CurrentAboxPatch:
    patch_id: str
    world_id: str
    logical_manifest_id: str
    base_manifest_id: str
    changed_scope_ids: List[str]
    revision_vector: WorldRevisionVector
    mutations: List[FactMutation] = field(default_factory=list)
    contract_version: str = CURRENT_STATE_ABOX_CONTRACT_VERSION

    @property
    def fingerprint(self) -> str:
        return _fingerprint({
            "contractVersion": self.contract_version,
            "patchId": self.patch_id,
            "worldId": self.world_id,
            "logicalManifestId": self.logical_manifest_id,
            "baseManifestId": self.base_manifest_id,
            "changedScopeIds": sorted(set(self.changed_scope_ids)),
            "revisionVector": self.revision_vector.to_dict(),
            "mutations": [item.to_dict() for item in self.mutations],
        })

    def to_dict(self) -> Dict[str, object]:
        return {
            **asdict(self),
            "revision_vector": self.revision_vector.to_dict(),
            "mutations": [item.to_dict() for item in self.mutations],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class InferenceExecutionReceipt:
    run_id: str
    world_id: str
    logical_manifest_id: str
    inference_generation_id: str
    stage: str
    status: str
    completed_at: str = ""
    detail: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.stage not in CURRENT_STATE_TRANSITION_STAGES:
            raise ValueError("Unsupported current-state transition stage: " + self.stage)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def logical_scope_generations(scope_plan: Iterable[Mapping[str, object]]) -> Dict[str, str]:
    return {
        _clean(item.get("scopeId")): _clean(
            item.get("logicalGenerationId") or item.get("generationId")
        )
        for item in scope_plan or []
        if _clean(item.get("scopeId"))
        and _clean(item.get("logicalGenerationId") or item.get("generationId"))
    }
