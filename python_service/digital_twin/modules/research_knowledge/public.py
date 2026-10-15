"""Lazy use-case surface; importing it starts no workers or database calls."""
from digital_twin.modules._exports import resolve_export

_EXPORTS = {"ResearchKnowledgeService": ("digital_twin.modules.research_knowledge.application.service", "ResearchKnowledgeService")}
__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
