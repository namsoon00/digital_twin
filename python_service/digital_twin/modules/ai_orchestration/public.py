"""Explicit central AI interface; importing it starts no workers or database calls."""
from digital_twin.modules._exports import resolve_export

_EXPORTS = {
    "current_execution_metrics": ("digital_twin.modules.ai_orchestration.infrastructure.execution", "current_execution_metrics"),
    "AIControlService": ("digital_twin.modules.ai_orchestration.application.control", "AIControlService"),
    "execute_ai_work": ("digital_twin.modules.ai_orchestration.infrastructure.execution", "execute_ai_work"),
    "ai_execution": ("digital_twin.modules.ai_orchestration.infrastructure.execution", "ai_execution"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
