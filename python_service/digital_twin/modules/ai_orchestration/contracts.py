from digital_twin.modules._exports import resolve_export

_EXPORTS = {
    "CAPABILITIES": ("digital_twin.modules.ai_orchestration.domain.planning", "CAPABILITIES"),
    "enabled": ("digital_twin.modules.ai_orchestration.domain.planning", "enabled"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
