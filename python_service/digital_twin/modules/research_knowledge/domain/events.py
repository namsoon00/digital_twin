"""Durable knowledge revision contract without source-data duplication."""
from digital_twin.shared_kernel.events import DomainEvent
from .knowledge import digest


def knowledge_revised_event(claim):
    return DomainEvent(name="research_knowledge.claim_revised", aggregate_id=claim["claimId"],
        occurred_at=claim["knownAt"], event_id="event:" + digest(["research_knowledge.claim_revised", claim["revisionId"]]),
        correlation_id=claim["executionInputId"], payload={key: claim[key] for key in (
            "claimId", "revisionId", "previousRevisionId", "accountId", "symbol", "worldId", "purpose", "frameworkId", "knownAt", "authority")})


def progress_recorded_event(record):
    return DomainEvent(name="research_knowledge.progress_recorded", aggregate_id=record["progressId"],
        occurred_at=record["knownAt"], event_id="event:" + digest(["research_knowledge.progress_recorded", record["progressId"]]),
        correlation_id=record["executionInputIds"][-1], payload={key: record[key] for key in
            ("progressId", "accountId", "symbol", "worldId", "knownAt", "status", "authority")})
