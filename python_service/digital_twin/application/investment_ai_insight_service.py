"""Handoff TypeDB subject decisions to AI before notification admission."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

from ..domain.events import investment_inference_episode_completed_event
from ..domain.portfolio import AlertEvent


QUEUED_AI_INSIGHT_STATES = frozenset({
    "awaiting-ai-insight",
    "pending",
    "processing",
    "retry",
})


class InvestmentAIInsightHandoffService:
    """Create notification drafts while keeping delivery downstream of AI."""

    def __init__(self, notification_ingress, ai_enqueuer, account_repository=None):
        self.notification_ingress = notification_ingress
        self.ai_enqueuer = ai_enqueuer
        self.account_repository = account_repository

    def enqueue(self, events: Iterable[AlertEvent]) -> Dict[str, object]:
        candidates = list(events or [])
        accounts = self.account_contexts()
        outcomes = []
        queued_events = []
        for event in candidates:
            metadata = dict(getattr(event, "metadata", {}) or {})
            subject_case = (
                dict(metadata.get("investmentSubjectDecisionCase") or {})
                if isinstance(metadata.get("investmentSubjectDecisionCase"), Mapping)
                else {}
            )
            if not subject_case:
                raise ValueError(
                    "TypeDB AI handoff event has no subject decision case: "
                    + str(getattr(event, "key", "") or "")
                )
            source_event = investment_inference_episode_completed_event(subject_case)
            account = accounts.get(str(getattr(event, "account_id", "") or ""))
            account_context = (
                account.message_delivery_context()
                if account is not None
                and callable(getattr(account, "message_delivery_context", None))
                else {}
            )
            job = self.notification_ingress.job_from_alert(
                event,
                source_event=source_event,
                account_context=account_context,
            )
            outcome = dict(self.ai_enqueuer.enqueue_subject_decision(job) or {})
            outcome.setdefault("eventKey", str(getattr(event, "key", "") or ""))
            outcome.setdefault("symbol", str(getattr(event, "symbol", "") or "").upper())
            outcomes.append(outcome)
            if str(outcome.get("status") or "") in QUEUED_AI_INSIGHT_STATES:
                queued_events.append(event)
        return {
            "status": "queued" if queued_events else "web-only",
            "candidateCount": len(candidates),
            "queuedCount": len(queued_events),
            "webOnlyCount": len(candidates) - len(queued_events),
            "queuedEvents": queued_events,
            "outcomes": outcomes,
        }

    def account_contexts(self):
        if self.account_repository is None:
            return {}
        loader = getattr(self.account_repository, "load_all", None)
        if not callable(loader):
            loader = getattr(self.account_repository, "load", None)
        try:
            accounts = loader() if callable(loader) else []
        except Exception:  # noqa: BLE001 - strategy context is optional to the decision boundary.
            accounts = []
        return {
            str(getattr(account, "account_id", "") or ""): account
            for account in accounts or []
            if str(getattr(account, "account_id", "") or "")
        }
