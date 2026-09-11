from digital_twin.modules.model_registry.domain.event_types import HYPOTHESIS_PROPOSED, HYPOTHESIS_REVIEWED, HYPOTHESIS_LIFECYCLE_TRANSITIONED, HYPOTHESIS_DEVELOPMENT_TRANSITIONED, HYPOTHESIS_DEVELOPMENT_VALIDATED, HYPOTHESIS_DEVELOPMENT_DEPLOYED, ONTOLOGY_RULEBOX_DEPLOYMENT_CHANGED, INVESTMENT_STRATEGY_PROPOSED, INVESTMENT_STRATEGY_VALIDATED, INVESTMENT_STRATEGY_APPROVED, INVESTMENT_STRATEGY_DEPLOYED, INVESTMENT_STRATEGY_PERFORMANCE_RECORDED
from digital_twin.shared_kernel.events import DomainEvent
from typing import Dict, Iterable, List, Mapping
import uuid


























def ontology_rulebox_deployment_changed_event(
    operation_id: str,
    phase: str,
    status: str,
    database: str = "",
    details: Mapping[str, object] = None,
) -> DomainEvent:
    """Record bounded governed RuleBox release evidence for operations."""

    clean_operation_id = str(operation_id or uuid.uuid4().hex).strip()[:191]
    return DomainEvent(
        name=ONTOLOGY_RULEBOX_DEPLOYMENT_CHANGED,
        aggregate_id=("rulebox-deployment:" + clean_operation_id)[:191],
        payload={
            "operationId": clean_operation_id,
            "phase": str(phase or "unknown")[:64],
            "status": str(status or "unknown")[:64],
            "database": str(database or "")[:191],
            "details": dict(details or {}),
        },
        correlation_id=("rulebox-deployment:" + clean_operation_id)[:191],
    )


def hypothesis_proposed_event(payload: Dict[str, object]) -> DomainEvent:
    return DomainEvent(
        name=HYPOTHESIS_PROPOSED,
        aggregate_id=str(payload.get("proposalId") or "hypothesis-proposal"),
        payload=dict(payload or {}),
    )


def hypothesis_reviewed_event(payload: Dict[str, object]) -> DomainEvent:
    return DomainEvent(
        name=HYPOTHESIS_REVIEWED,
        aggregate_id=str(payload.get("proposalId") or "hypothesis-proposal"),
        payload=dict(payload or {}),
    )


def hypothesis_development_event(payload: Dict[str, object], name: str = HYPOTHESIS_DEVELOPMENT_TRANSITIONED) -> DomainEvent:
    case_id = str(payload.get("caseId") or payload.get("case_id") or "hypothesis-development")
    return DomainEvent(
        name=str(name or HYPOTHESIS_DEVELOPMENT_TRANSITIONED),
        aggregate_id=case_id,
        payload={
            "caseId": case_id,
            "status": str(payload.get("status") or ""),
            "stage": str(payload.get("stage") or ""),
            "symbol": str(payload.get("symbol") or "").upper(),
            "accountId": str(payload.get("accountId") or payload.get("account_id") or ""),
            "sourceProposalIds": list(payload.get("sourceProposalIds") or [])[:100],
            "candidateId": str(payload.get("candidateId") or ""),
            "experimentId": str(payload.get("experimentId") or ""),
            "validationSummary": dict(payload.get("validationSummary") or {}),
            "decisionImpact": dict(payload.get("decisionImpact") or {}),
            "reason": str(payload.get("blockedReason") or payload.get("reason") or "")[:1000],
        },
    )


def hypothesis_lifecycle_transitioned_event(payload: Dict[str, object]) -> DomainEvent:
    """Publish audit-only lifecycle changes without creating an alert signal."""

    lifecycle_key = str(payload.get("lifecycleKey") or payload.get("lifecycle_key") or "unknown")
    return DomainEvent(
        name=HYPOTHESIS_LIFECYCLE_TRANSITIONED,
        aggregate_id="hypothesis-lifecycle:" + lifecycle_key[:160],
        payload={
            "lifecycleKey": lifecycle_key,
            "lifecycleId": str(payload.get("lifecycleId") or payload.get("lifecycle_id") or ""),
            "scope": str(payload.get("scope") or ""),
            "symbol": str(payload.get("symbol") or "").upper(),
            "accountId": str(payload.get("accountId") or payload.get("account_id") or ""),
            "previousState": str(payload.get("previousState") or payload.get("previous_state") or ""),
            "currentState": str(payload.get("currentState") or payload.get("current_state") or ""),
            "inferenceGenerationId": str(payload.get("inferenceGenerationId") or payload.get("inference_generation_id") or ""),
            "previousGenerationId": str(payload.get("previousGenerationId") or payload.get("previous_generation_id") or ""),
            "occurredAt": str(payload.get("occurredAt") or payload.get("occurred_at") or ""),
            "reason": str(payload.get("reason") or ""),
            "materialChange": bool(payload.get("materialChange") if "materialChange" in payload else payload.get("material_change")),
            "evidenceDelta": dict(payload.get("evidenceDelta") or payload.get("evidence_delta") or {}),
            "source": "typedb-hypothesis-lifecycle",
        },
    )


def investment_strategy_proposed_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_PROPOSED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={"proposal": payload},
    )


def investment_strategy_validated_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_VALIDATED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "validation": dict(payload.get("validation") or {}),
        },
    )


def investment_strategy_approved_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    lifecycle = dict(payload.get("lifecycle") or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_APPROVED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "approvedAt": str(payload.get("approvedAt") or ""),
            "approvedBy": str(lifecycle.get("approvedBy") or ""),
            "approvalReason": str(lifecycle.get("approvalReason") or ""),
        },
    )


def investment_strategy_deployed_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_DEPLOYED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "deployedAt": str(payload.get("deployedAt") or ""),
            "ruleIds": list(payload.get("ruleIds") or []),
        },
    )


def investment_strategy_performance_recorded_event(proposal, sample: Dict[str, object]) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    performance = dict(payload.get("performance") or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_PERFORMANCE_RECORDED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "sample": dict(sample or {}),
            "summary": dict(performance.get("summary") or {}),
        },
    )
