"""Route persisted TypeDB subject results to independent downstream modules."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Dict, Iterable, Mapping

from digital_twin.modules.decisions.domain.events import investment_inference_episode_completed_event
from digital_twin.modules.decisions.domain.investment_reasoning import ARCHIVE, HANDOFF_AI, INVALID, PUBLISH_TYPEDB, InferenceDispatchDecision, inference_dispatch_decision
from digital_twin.modules.notifications.contracts import context_observation_delivery_decision, typedb_ai_handoff_relation_set, typedb_ai_handoff_relation_set_fingerprint
from digital_twin.modules.portfolio.contracts import AlertEvent


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
        typedb_companion_outcomes = []
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
                companion = self._publish_ai_handoff_typedb_observation(
                    event,
                    source_event,
                    subject_case,
                    account_contexts.get(str(getattr(event, "account_id", "") or ""), {}),
                )
                if companion:
                    typedb_companion_outcomes.append(companion[0])
                    if companion[0].get("queued") and companion[1] is not None:
                        typedb_queued_events.append(companion[1])
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

        try:
            if ai_events and self.ai_handoff_service is None:
                raise RuntimeError("AI handoff service is unavailable")
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
            if len(ai_events) != len(ai_result.get("outcomes") or []):
                raise ValueError("AI handoff must return one outcome per subject")
        except Exception as error:
            for event in ai_events:
                self.reasoning_orchestrator.record_ai_handoff_outcome(
                    event.metadata["investmentSubjectDecisionCaseId"],
                    {"status": "handoff-error", "errorType": type(error).__name__,
                     "reason": "AI 요청 전달 중 오류가 발생했습니다. 추론 작업 재시도에서 다시 확인합니다."},
                )
            raise
        ai_queued_events = list(ai_result.get("queuedEvents") or [])
        ai_outcomes = [dict(item or {}) for item in ai_result.get("outcomes") or []]
        for event, outcome in zip(ai_events, ai_outcomes):
            outcome.setdefault("route", HANDOFF_AI)
            outcome.setdefault("eventKey", str(getattr(event, "key", "") or ""))
            outcome.setdefault("symbol", str(getattr(event, "symbol", "") or "").upper())
            self.reasoning_orchestrator.record_ai_handoff_outcome(
                event.metadata["investmentSubjectDecisionCaseId"], outcome,
            )
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
            "typedbCompanionOutcomes": typedb_companion_outcomes,
            "outcomes": outcomes,
        }

    def _publish_ai_handoff_typedb_observation(
        self,
        event: AlertEvent,
        source_event,
        subject_case,
        account_context: Mapping[str, object],
    ):
        """Publish the verified TypeDB stage without replacing the AI route."""

        context = deepcopy(_mapping(getattr(event, "metadata", {}) or {}))
        for key in (
            "preDecisionDeliveryCadence",
            "cooldownDecision",
            "cooldownReason",
            "cooldownSuppressed",
        ):
            context.pop(key, None)
        nested_metadata = _mapping(context.get("metadata"))
        if nested_metadata:
            for key in (
                "preDecisionDeliveryCadence",
                "cooldownDecision",
                "cooldownReason",
                "cooldownSuppressed",
            ):
                nested_metadata.pop(key, None)
            context["metadata"] = nested_metadata
        relation = _mapping(context.get("ontologyRelationContext"))
        candidate = getattr(subject_case, "candidate_set", None)
        hypothesis_ids = list(dict.fromkeys([
            *tuple(getattr(candidate, "execution_eligible_hypothesis_ids", ()) or ()),
            *tuple(getattr(candidate, "eligible_hypothesis_ids", ()) or ()),
            *tuple(getattr(candidate, "reference_hypothesis_ids", ()) or ()),
        ]))
        if not hypothesis_ids:
            return self._typedb_companion_suppression(
                event,
                subject_case,
                "typedb-stage-missing-hypotheses",
                "TypeDB 관계에 연결된 가설 후보가 없어 별도 알림을 만들지 않았습니다.",
            ), None
        relation_rows = typedb_ai_handoff_relation_set(relation, hypothesis_ids)
        relation_ids = [
            str(row.get("ruleId") or row.get("rule_id") or row.get("sourceRuleId") or "").strip()
            for row in relation_rows
        ]
        relation_ids = [value for value in relation_ids if value]
        if not relation_ids:
            return self._typedb_companion_suppression(
                event,
                subject_case,
                "typedb-stage-missing-relations",
                "가설 후보에 연결된 검증 관계가 없어 TypeDB 단계 알림을 만들지 않았습니다.",
            ), None
        relation_set_fingerprint = typedb_ai_handoff_relation_set_fingerprint(
            relation_ids,
            hypothesis_ids,
            subject_case.inference_generation_id,
        )

        context["investmentSubjectDecisionCase"] = (
            self.reasoning_orchestrator.compact_subject_context(subject_case)
        )
        context["typedbAiHandoffObservation"] = {
            "status": "eligible",
            "subjectCaseId": subject_case.subject_case_id,
            "inferenceGenerationId": subject_case.inference_generation_id,
            "candidateFingerprint": str(getattr(candidate, "fingerprint", "") or ""),
            "hypothesisIds": hypothesis_ids,
            "relationIds": relation_ids,
            "relationSetFingerprint": relation_set_fingerprint,
        }
        context["typedbObservationPublication"] = {
            "publicationId": "typedb-stage:" + subject_case.subject_case_id,
            "subjectCaseId": subject_case.subject_case_id,
            "outcomeKind": "OBSERVATION",
            "fingerprint": str(getattr(candidate, "fingerprint", "") or ""),
        }
        semantic_delivery = context_observation_delivery_decision(context)
        if str(semantic_delivery.get("decision") or "").strip().lower() != "send":
            return self._typedb_companion_suppression(
                event,
                subject_case,
                str(
                    semantic_delivery.get("suppressionReason")
                    or "typedb-stage-contract-rejected"
                ),
                str(
                    semantic_delivery.get("reason")
                    or "TypeDB 단계 알림 계약을 충족하지 못했습니다."
                ),
                semantic_delivery=semantic_delivery,
            ), None

        companion_decision = InferenceDispatchDecision.create(
            subject_case,
            PUBLISH_TYPEDB,
            "material-typedb-stage-observation",
            str(semantic_delivery.get("reason") or "AI 판단 전 TypeDB 관계 변화가 확인됐습니다."),
            source_event_id=source_event.event_id,
            details={
                "semanticDeliveryDecision": semantic_delivery,
                "parentDispatchDecisionId": subject_case.inference_dispatch_decision.decision_id,
                "parentDispatchRoute": HANDOFF_AI,
            },
        )
        companion_event = deepcopy(event)
        companion_event.key = str(getattr(event, "key", "") or "") + ":typedb-stage"
        companion_event.metadata = context
        outcome = self._publish_typedb(
            companion_event,
            source_event,
            companion_decision,
            account_context,
            record_subject=False,
            reconcile_subject=False,
        )
        outcome["companionOfRoute"] = HANDOFF_AI
        return outcome, companion_event

    @staticmethod
    def _typedb_companion_suppression(
        event: AlertEvent,
        subject_case,
        reason_code: str,
        reason: str,
        *,
        semantic_delivery: Mapping[str, object] = None,
    ) -> Dict[str, object]:
        outcome = {
            "status": "typedb-companion-suppressed",
            "route": PUBLISH_TYPEDB,
            "companionOfRoute": HANDOFF_AI,
            "eventKey": str(getattr(event, "key", "") or ""),
            "symbol": str(getattr(event, "symbol", "") or "").upper(),
            "subjectCaseId": str(getattr(subject_case, "subject_case_id", "") or ""),
            "notificationJobId": "",
            "queued": False,
            "reasonCode": reason_code,
            "reason": reason,
        }
        if semantic_delivery:
            outcome["semanticDeliveryDecision"] = dict(semantic_delivery)
        return outcome

    def _publish_typedb(
        self,
        event: AlertEvent,
        source_event,
        decision,
        account_context: Mapping[str, object],
        *,
        record_subject: bool = True,
        reconcile_subject: bool = True,
    ) -> Dict[str, object]:
        subject_case_id = decision.subject_case_id
        if record_subject:
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
        if reconcile_subject:
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
