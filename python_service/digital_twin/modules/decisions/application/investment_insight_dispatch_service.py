"""Route persisted TypeDB subject results to independent downstream modules."""

from __future__ import annotations

import hashlib
from typing import Dict, Iterable, Mapping

from digital_twin.domain.events import investment_inference_episode_completed_event
from digital_twin.domain.investment_reasoning import ARCHIVE, HANDOFF_AI, INVALID, PUBLISH_TYPEDB, inference_dispatch_decision
from digital_twin.domain.portfolio import AlertEvent


AI_QUEUED_STATES = frozenset({
    "awaiting-ai-insight",
    "pending",
    "processing",
    "retry",
})


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


class InvestmentInsightDispatchService:
    """Fan out TypeDB output without coupling TypeDB execution to AI or transport."""

    def __init__(
        self,
        notification_ingress,
        notification_queue,
        ai_handoff_service,
        reasoning_orchestrator,
        account_repository=None,
    ):
        self.notification_ingress = notification_ingress
        self.notification_queue = notification_queue
        self.ai_handoff_service = ai_handoff_service
        self.reasoning_orchestrator = reasoning_orchestrator
        self.account_repository = account_repository

    def enqueue(self, events: Iterable[AlertEvent]) -> Dict[str, object]:
        """Compatibility entry point used by the versioned reasoning engine."""

        return self.dispatch(events)

    def dispatch(self, events: Iterable[AlertEvent]) -> Dict[str, object]:
        candidates = list(events or [])
        account_contexts = self._account_contexts()
        ai_events = []
        typedb_queued_events = []
        outcomes = []
        route_counts = {
            PUBLISH_TYPEDB: 0,
            HANDOFF_AI: 0,
            ARCHIVE: 0,
            INVALID: 0,
        }

        for event in candidates:
            metadata = _mapping(getattr(event, "metadata", {}) or {})
            subject_payload = _mapping(metadata.get("investmentSubjectDecisionCase"))
            subject_case_id = str(
                metadata.get("investmentSubjectDecisionCaseId")
                or subject_payload.get("subjectCaseId")
                or ""
            ).strip()
            if not subject_case_id:
                outcomes.append({
                    "status": "invalid-missing-subject-case",
                    "route": INVALID,
                    "eventKey": str(getattr(event, "key", "") or ""),
                    "symbol": str(getattr(event, "symbol", "") or "").upper(),
                    "reason": "TypeDB 결과에 종목 판단 식별자가 없습니다.",
                })
                route_counts[INVALID] += 1
                continue

            subject_case = self.reasoning_orchestrator.required_subject(subject_case_id)
            if subject_case.publication:
                metadata["decisionPublication"] = subject_case.publication.to_dict()
            metadata["investmentSubjectDecisionCase"] = (
                self.reasoning_orchestrator.compact_subject_context(subject_case)
            )
            metadata["investmentSubjectDecisionCaseId"] = subject_case_id
            metadata.setdefault("messageType", str(getattr(event, "rule", "") or ""))
            source_event = investment_inference_episode_completed_event(subject_case)
            decision = inference_dispatch_decision(
                metadata,
                subject_case,
                source_event_id=source_event.event_id,
            )
            route_counts[decision.route] += 1
            metadata["inferenceDispatchDecision"] = decision.to_dict()
            event.metadata = metadata

            if decision.route == PUBLISH_TYPEDB:
                outcome = self._publish_typedb(
                    event,
                    source_event,
                    decision,
                    account_contexts.get(str(getattr(event, "account_id", "") or ""), {}),
                )
                if outcome.get("queued"):
                    typedb_queued_events.append(event)
                outcomes.append(outcome)
                continue

            if decision.route == HANDOFF_AI:
                self.reasoning_orchestrator.record_inference_dispatch(
                    subject_case_id,
                    decision,
                    delivery_state="handoff-ai",
                )
                event.metadata["investmentSubjectDecisionCase"] = (
                    self.reasoning_orchestrator.compact_subject_context(
                        self.reasoning_orchestrator.required_subject(subject_case_id)
                    )
                )
                ai_events.append(event)
                continue

            state = "failed" if decision.route == INVALID else "archived"
            self.reasoning_orchestrator.record_inference_dispatch(
                subject_case_id,
                decision,
                delivery_state=state,
            )
            outcomes.append({
                "status": "invalid" if decision.route == INVALID else "web-only",
                "route": decision.route,
                "eventKey": str(getattr(event, "key", "") or ""),
                "symbol": str(getattr(event, "symbol", "") or "").upper(),
                "subjectCaseId": subject_case_id,
                "reasonCode": decision.reason_code,
                "reason": decision.reason,
                "queued": False,
            })

        ai_result = (
            dict(self.ai_handoff_service.enqueue(ai_events) or {})
            if ai_events and self.ai_handoff_service is not None
            else {
                "status": "not-requested",
                "candidateCount": len(ai_events),
                "queuedCount": 0,
                "webOnlyCount": len(ai_events),
                "queuedEvents": [],
                "outcomes": [],
            }
        )
        ai_queued_events = list(ai_result.get("queuedEvents") or [])
        ai_outcomes = [dict(item or {}) for item in ai_result.get("outcomes") or []]
        for event, outcome in zip(ai_events, ai_outcomes):
            outcome.setdefault("route", HANDOFF_AI)
            outcome.setdefault("eventKey", str(getattr(event, "key", "") or ""))
            outcome.setdefault("symbol", str(getattr(event, "symbol", "") or "").upper())
        outcomes.extend(ai_outcomes)

        typedb_count = len(typedb_queued_events)
        ai_count = len(ai_queued_events)
        queued_events = [*typedb_queued_events, *ai_queued_events]
        if ai_count and typedb_count:
            status = "typedb-and-ai-queued"
        elif ai_count:
            status = "ai-queued"
        elif typedb_count:
            status = "typedb-queued"
        else:
            status = "web-only"
        return {
            "status": status,
            "candidateCount": len(candidates),
            "queuedCount": len(queued_events),
            "typedbPublishedCount": typedb_count,
            "aiQueuedCount": ai_count,
            "webOnlyCount": max(0, len(candidates) - len(queued_events)),
            "routeCounts": route_counts,
            "queuedEvents": queued_events,
            "typedbQueuedEvents": typedb_queued_events,
            "aiQueuedEvents": ai_queued_events,
            "outcomes": outcomes,
        }

    def _publish_typedb(
        self,
        event: AlertEvent,
        source_event,
        decision,
        account_context: Mapping[str, object],
    ) -> Dict[str, object]:
        subject_case_id = decision.subject_case_id
        self.reasoning_orchestrator.record_inference_dispatch(subject_case_id, decision)
        context = _mapping(getattr(event, "metadata", {}) or {})
        context["investmentSubjectDecisionCase"] = (
            self.reasoning_orchestrator.compact_subject_context(
                self.reasoning_orchestrator.required_subject(subject_case_id)
            )
        )
        semantic_delivery = _mapping(decision.details.get("semanticDeliveryDecision"))
        context.update({
            "inferenceDispatchDecision": decision.to_dict(),
            "contextObservationDeliveryDecision": semantic_delivery,
            "notificationDecisionOwner": "typedb",
            "notificationAiBypass": {
                "status": "typedb-direct",
                "reasonCode": decision.reason_code,
                "reason": decision.reason,
            },
            "notificationWriterProvenance": {
                "writerKind": "deterministic",
                "decisionOwner": "typedb",
                "narrativeOwner": "typedb",
                "aiAuthored": False,
            },
        })
        event.metadata = context
        job = self.notification_ingress.job_from_alert(
            event,
            source_event=source_event,
            account_context=account_context,
        )
        job.job_id = hashlib.sha256(decision.decision_id.encode("utf-8")).hexdigest()[:32]
        job.context["jobId"] = job.job_id
        accepted = bool(self.notification_queue.enqueue(job))
        existing = None
        if not accepted:
            getter = getattr(self.notification_queue, "get", None)
            existing = getter(job.job_id) if callable(getter) else None
        existing_status = str(getattr(existing, "status", "") or "").lower()
        already_queued = existing_status in {
            "pending", "processing", "awaiting_ai", "done", "sent",
        }
        queued = accepted or already_queued
        outcome = {
            "status": (
                "typedb-notification-queued"
                if accepted
                else "typedb-notification-already-recorded"
                if already_queued
                else "typedb-notification-suppressed"
            ),
            "route": PUBLISH_TYPEDB,
            "eventKey": str(getattr(event, "key", "") or ""),
            "symbol": str(getattr(event, "symbol", "") or "").upper(),
            "subjectCaseId": subject_case_id,
            "notificationJobId": job.job_id if queued else "",
            "queued": queued,
            "reasonCode": decision.reason_code,
            "reason": (
                decision.reason
                if queued
                else str(getattr(job, "last_error", "") or "알림 발송 정책이 TypeDB 관찰을 억제했습니다.")
            ),
        }
        self.reasoning_orchestrator.decision_delivery_reconciled(job.context, outcome)
        return outcome

    def _account_contexts(self) -> Dict[str, object]:
        if self.account_repository is None:
            return {}
        loader = getattr(self.account_repository, "load_all", None)
        if not callable(loader):
            loader = getattr(self.account_repository, "load", None)
        try:
            accounts = loader() if callable(loader) else []
        except Exception:  # noqa: BLE001 - account delivery preferences are optional here.
            accounts = []
        return {
            str(getattr(account, "account_id", "") or ""): (
                account.message_delivery_context()
                if callable(getattr(account, "message_delivery_context", None)) else {}
            )
            for account in accounts or []
            if str(getattr(account, "account_id", "") or "")
        }
