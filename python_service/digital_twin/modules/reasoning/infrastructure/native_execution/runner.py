"""native_execution: runner through explicit injected capabilities."""

from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    TYPEDB_NATIVE_BLOCKED_MODE,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import typedb_bool
from typing import Dict
from .runner_ports import NativeExecutionRunnerStore, NativeExecutionRunnerRuntime


def run_rulebox(
    _store: NativeExecutionRunnerStore,
    payload: Dict[str, object] = None,
    *,
    _bindings: NativeExecutionRunnerRuntime
) -> Dict[str, object]:
    """Run native rules under the same durable writer boundary as ABox swaps.

    A native run writes a generation candidate and atomically replaces the
    active InferenceBox marker.  It must not overlap another ABox
    activation or direct RuleBox invocation, otherwise two otherwise valid
    candidates can prune or publish around each other.
    """
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().run_rulebox(payload)
    values = dict(payload or {})
    world_id = str(values.get("worldId") or values.get("ontologyWorldId") or "").strip()
    if world_id:
        values["worldId"] = world_id
    native_execution_value = values.get("typedbNativeRuleExecutionEnabled")
    if native_execution_value is None:
        native_execution_enabled = _store.native_rule_execution_enabled()
    else:
        native_execution_enabled = typedb_bool(native_execution_value)
    if not native_execution_enabled or not _store._inference_write_lease_enabled:
        return _store._run_rulebox_unlocked(values)

    supplied_owner = str(values.pop("_inferenceWriteLeaseOwner", "") or "").strip()
    supplied_lease = bool(supplied_owner)
    if supplied_lease:
        current = _store.scoped_abox_write_lease_status(world_id)
        if (
            str(current.get("status") or "") == "held"
            and str(current.get("leaseOwner") or "") == supplied_owner
        ):
            values["_nativeInferenceWriteLeaseHeld"] = True
            result = _store._run_rulebox_unlocked(values)
            if isinstance(result, dict):
                result["inferenceWriteLease"] = {
                    "status": "adopted",
                    "leaseOwner": supplied_owner,
                    "managedBy": "ontology-projection",
                }
            return result
        return {
            "configured": True,
            "status": "invalid-inference-write-lease",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reason": "Projection-owned TypeDB inference lease could not be verified.",
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "inferenceWriteLease": {
                "status": str(current.get("status") or "missing"),
                "leaseOwner": str(current.get("leaseOwner") or ""),
            },
        }

    lease = _store.acquire_scoped_abox_write_lease("inferencebox-native-rule", world_id=world_id)
    if not lease.get("acquired"):
        return {
            "configured": True,
            "status": "deferred-inference-write-lease",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reason": "Another ABox activation or native InferenceBox generation is running.",
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "preservedPreviousInference": True,
            "inferenceWriteLease": {
                key: value for key, value in dict(lease or {}).items() if key != "propertiesJson"
            },
        }
    try:
        values["_nativeInferenceWriteLeaseHeld"] = True
        result = _store._run_rulebox_unlocked(values)
    finally:
        release = _store.release_scoped_abox_write_lease(lease)
    if isinstance(result, dict):
        result["inferenceWriteLease"] = {
            key: value for key, value in dict(lease or {}).items() if key != "propertiesJson"
        }
        result["inferenceWriteLeaseRelease"] = release
    return result
