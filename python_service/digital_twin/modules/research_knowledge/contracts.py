"""Explicit pure cross-domain contracts."""
from digital_twin.modules._exports import resolve_export

_EXPORTS = {
    "framework_catalog": ("digital_twin.modules.research_knowledge.domain.frameworks", "framework_catalog"),
    "service_research_policy": ("digital_twin.modules.research_knowledge.domain.frameworks", "service_research_policy"),
    "knowledge_proposal_schema": ("digital_twin.modules.research_knowledge.domain.knowledge", "proposal_schema"),
    "validate_knowledge_proposals": ("digital_twin.modules.research_knowledge.domain.knowledge", "validate_proposals"),
    "raw_knowledge_proposals": ("digital_twin.modules.research_knowledge.domain.knowledge", "raw_proposals"),
    "knowledge_context_identity": ("digital_twin.modules.research_knowledge.domain.context", "context_material_identity"),
    "validate_knowledge_context": ("digital_twin.modules.research_knowledge.domain.context", "validate_context"),
    "knowledge_revised_event": ("digital_twin.modules.research_knowledge.domain.events", "knowledge_revised_event"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
