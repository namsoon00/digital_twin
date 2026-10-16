"""Retrieval uses persisted methods and historical revisions, never latest facts."""
from ..domain.context import build_context
from ..domain.frameworks import service_research_policy


class ResearchKnowledgeService:
    def __init__(self, repository):
        self.repository = repository

    def context(self, packet, usable_ids=()):
        return build_context(packet, self.repository.frameworks(packet["capturedAt"]),
                             self.repository.records(packet, packet["capturedAt"]), usable_ids=usable_ids,
                             progress=self.repository.progress(packet, packet["capturedAt"]))

    def status(self, account_id=""):
        return {"policy": service_research_policy(), "frameworks": self.repository.frameworks(),
                "claims": self.repository.recent(account_id), "activity": self.repository.activity(account_id), "authority": "research-only"}
