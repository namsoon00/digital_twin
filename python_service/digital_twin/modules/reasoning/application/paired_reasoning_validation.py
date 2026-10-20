"""Request independent, zero-delivery comparisons of current durable facts."""

from copy import deepcopy
import hashlib
import json

from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.modules.reasoning.domain.independent_reasoning import shard_reasoning_event, reasoning_event_scope
from digital_twin.modules.reasoning.domain.verified_snapshot_reasoning import verified_monitor_snapshot_reasoning_event
from digital_twin.modules.reasoning.domain.reasoning_comparison_identity import PAIRED_VALIDATION_INPUT_VERSION
from digital_twin.modules.reasoning.domain.validation_portfolio_input import freeze_validation_portfolio_input


class PairedReasoningValidationService:
    def __init__(self, registry, snapshot_reader, event_recorder, now_provider, portfolio_context_reader=None):
        self.registry = registry
        self.snapshot_reader = snapshot_reader
        self.event_recorder = event_recorder
        self.now_provider = now_provider
        self.portfolio_context_reader = portfolio_context_reader

    def request(self, symbols, account_id="", limit=1):
        control = self.registry.control()
        baseline = str(control.delivery_deployment_id or control.active_deployment_id or "")
        candidate = str(control.candidate_deployment_id or "")
        if not baseline or not candidate or baseline == candidate:
            return {"status": "blocked", "reason": "distinct-delivery-and-candidate-required"}
        selected = {str(value).upper().strip() for value in symbols or [] if str(value or "").strip()}
        if not selected:
            return {"status": "blocked", "reason": "explicit-subjects-required"}
        row = dict(self.registry.get(candidate) or {})
        release = dict((row.get("health") or {}).get("runtimeOntologyRelease") or {})
        status = str(row.get("status") or "")
        warmed_bootstrap = status in {"provisioning", "replaying"} and release.get("status") == "ready" and release.get("warmed") is True
        if status not in {"shadow", "candidate"} and not warmed_bootstrap:
            return {"status": "blocked", "reason": "candidate-validation-not-ready"}
        if not callable(self.portfolio_context_reader):
            return {"status": "blocked", "reason": "validation-portfolio-context-unavailable"}
        queued = []
        covered = set()
        for snapshot in self.snapshot_reader(account_id, sorted(selected)):
            source = verified_monitor_snapshot_reasoning_event(snapshot, {})
            if source is None:
                continue
            for shard in shard_reasoning_event(source, 1):
                subject = reasoning_event_scope(shard)["symbols"]
                if not subject or subject[0] not in selected or subject[0] in covered:
                    continue
                payload = deepcopy(shard.payload)
                boundary = dict(payload.get("verifiedSourceSnapshot") or {})
                if boundary.get("sourceFactCoverageComplete") is not True:
                    continue
                portfolio_packet = freeze_validation_portfolio_input(
                    snapshot.account_id, str(boundary.get("snapshotId") or ""), snapshot.generated_at,
                    self.portfolio_context_reader(snapshot.account_id, snapshot.generated_at),
                )
                pair = {"baselineDeploymentId": baseline, "candidateDeploymentId": candidate,
                        "sourceSnapshotId": str(boundary.get("snapshotId") or ""), "symbols": subject,
                        "inputMode": PAIRED_VALIDATION_INPUT_VERSION,
                        "portfolioContextHash": portfolio_packet["sha256"]}
                identity = hashlib.sha256(json.dumps(pair, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                payload.update(candidateValidationReplay=True, validationPair=pair,
                               validationPortfolioContext=portfolio_packet,
                               trigger="paired-current-source-validation", workClass="GOVERNANCE",
                               importanceGate="non-fungible")
                event = DomainEvent(name=shard.name, aggregate_id=shard.aggregate_id,
                                    schema_version=shard.schema_version, payload=payload,
                                    occurred_at=self.now_provider(), event_id="reasoning-validation:" + identity[:40])
                receipt = dict(self.event_recorder(event) or {})
                if receipt.get("status") == "control-pair-changed":
                    return {"status": "blocked", "reason": "control-pair-changed", "requests": queued}
                queued.append({"eventId": event.event_id, "symbol": subject[0], **receipt})
                covered.add(subject[0])
                if len(queued) >= max(1, min(10, int(limit or 1))):
                    return {"status": "requested", "validationOnly": True, "requests": queued,
                            "baselineDeploymentId": baseline, "candidateDeploymentId": candidate}
        return {"status": "requested" if queued else "unavailable-source", "validationOnly": True,
                "requests": queued, "missingSymbols": sorted(selected - covered),
                "baselineDeploymentId": baseline, "candidateDeploymentId": candidate}
