"""Small durable event contract for observed scenario changes."""
from .event_types import INVESTMENT_STRATEGY_SCENARIO_CHANGED
from digital_twin.shared_kernel.events import DomainEvent


def strategy_scenario_changed_event(observation):
    change = observation["transition"]
    return DomainEvent(name=INVESTMENT_STRATEGY_SCENARIO_CHANGED,
        aggregate_id=observation["scenarioKey"], event_id=change["transitionId"],
        occurred_at=observation["assessedAt"], correlation_id=observation["inferenceGenerationId"],
        payload={k: observation[k] for k in ("scenarioKey", "observationId", "deploymentId", "accountId", "symbol",
            "worldId", "contractFingerprint", "state", "sourceObservedAt", "sourceAboxSnapshotId")})
