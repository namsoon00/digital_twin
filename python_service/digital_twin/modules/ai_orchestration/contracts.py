from digital_twin.modules._exports import resolve_export

_EXPORTS = {
    "resolve_observation_ref": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "resolve_ref"),
    "compare_observation_values": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "compare_values"),
    "observation_instant": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "instant"),
    "OBSERVATION_METRICS": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "METRICS"),
    "comparable_observation_refs": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "comparable_refs"),
    "legacy_route_retired": ("digital_twin.modules.ai_orchestration.domain.publication", "legacy_route_retired"),
    "RETIRED_REASON": ("digital_twin.modules.ai_orchestration.domain.publication", "RETIRED_REASON"),
    "CAPABILITIES": ("digital_twin.modules.ai_orchestration.domain.planning", "CAPABILITIES"),
    "enabled": ("digital_twin.modules.ai_orchestration.domain.planning", "enabled"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
