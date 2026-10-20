from digital_twin.modules._exports import resolve_export

_EXPORTS = {
    "dated_observation": ("digital_twin.modules.ai_orchestration.domain.publication", "dated_observation"),
    "QUOTE_SELECTION_VERSIONS": ("digital_twin.modules.ai_orchestration.domain.publication", "QUOTE_SELECTION_VERSIONS"),
    "observation_subject_quote": ("digital_twin.modules.ai_orchestration.domain.publication", "quote_from"),
    "QUOTE_SELECTION_VERSION": ("digital_twin.modules.ai_orchestration.domain.publication", "QUOTE_SELECTION_VERSION"),
    "resolve_observation_ref": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "resolve_ref"),
    "compare_observation_values": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "compare_values"),
    "observation_instant": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "instant"),
    "OBSERVATION_METRICS": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "METRICS"),
    "OBSERVATION_WORDING_VERSION": ("digital_twin.modules.ai_orchestration.domain.observation_wording", "OBSERVATION_WORDING_VERSION"),
    "comparable_observation_refs": ("digital_twin.modules.ai_orchestration.domain.insight_contract", "comparable_refs"),
    "legacy_route_retired": ("digital_twin.modules.ai_orchestration.domain.publication", "legacy_route_retired"),
    "RETIRED_REASON": ("digital_twin.modules.ai_orchestration.domain.publication", "RETIRED_REASON"),
    "CAPABILITIES": ("digital_twin.modules.ai_orchestration.domain.planning", "CAPABILITIES"),
    "enabled": ("digital_twin.modules.ai_orchestration.domain.planning", "enabled"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
