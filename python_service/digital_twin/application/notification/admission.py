"""Pure notification admission policy separated from MySQL persistence."""

from dataclasses import dataclass
from typing import Dict, Mapping

from ...domain.data_freshness import (
    evaluate_notification_data_freshness,
    sanitize_notification_context_for_freshness,
)
from ...domain.message_types import INVESTMENT_INSIGHT
from ...domain.notification_rules import (
    NotificationRuleConfig,
    apply_market_hours_rule,
    apply_similarity_rule,
    apply_state_cooldown_rule,
    attach_previous_profit_loss_context,
    evaluate_notification_rule,
    ontology_relation_delivery_diff,
    ontology_relation_delivery_metadata,
    notification_state_group_key,
)
from ...domain.notifications import NotificationJob
from ...domain.notification.lifecycle import age_minutes_since
from ...domain.sent_article_filter import (
    news_story_changes_decision,
    news_story_is_decision_driver,
    news_story_impact_from_context,
)


@dataclass(frozen=True)
class NotificationAdmissionOutcome:
    accepted: bool
    persisted: bool
    status: str
    reason: str = ""


class NotificationAdmissionPolicy:
    """Evaluate business policy using history facts supplied by a repository.

    No database connection is accepted here.  The MySQL adapter owns the short
    transaction and supplies immutable history facts to this service.
    """

    def prepare(self, job: NotificationJob, rule: NotificationRuleConfig):
        relation_delivery = ontology_relation_delivery_metadata(job.context or {})
        if relation_delivery:
            context = dict(job.context or {})
            context["ontologyRelationDelivery"] = {
                "version": relation_delivery.get("version"),
                "fingerprint": relation_delivery.get("fingerprint"),
                "signature": relation_delivery.get("signature"),
            }
            context["ontologyRelationFingerprint"] = relation_delivery.get("fingerprint")
            job.context = context
        transition_condition = next((
            condition
            for condition in rule.similarity_bypass_conditions or []
            if condition.condition_id == "insight_inference_state_changed"
        ), None)
        if str(job.message_type or "") == INVESTMENT_INSIGHT:
            context = dict(job.context or {})
            context["investmentStateTransitionNotificationsEnabled"] = bool(
                transition_condition and transition_condition.enabled
            )
            job.context = context
        return evaluate_notification_rule(job, rule)

    def evaluate(
        self,
        job: NotificationJob,
        rule: NotificationRuleConfig,
        decision,
        *,
        recent_count: int = 0,
        previous_context: Mapping[str, object] = None,
        last_sent_at: str = "",
        relation_previous_context: Mapping[str, object] = None,
    ):
        previous_context = dict(previous_context or {})
        relation_previous_context = dict(relation_previous_context or {})
        predecessor_sent_at = str(relation_previous_context.get("_relationPredecessorSentAt") or "")
        baseline_observed_at = str(relation_previous_context.get("_relationBaselineObservedAt") or "")
        if predecessor_sent_at and not last_sent_at:
            last_sent_at = predecessor_sent_at
            recent_count = max(1, int(recent_count or 0))
        cooldown_previous_context = previous_context
        if baseline_observed_at:
            relation_previous_context["_relationBaselineAgeMinutes"] = age_minutes_since(baseline_observed_at)
            cooldown_previous_context = relation_previous_context
            context = dict(job.context or {})
            context["_relationBaselineObservedAt"] = baseline_observed_at
            context["_relationBaselineFingerprint"] = str(
                relation_previous_context.get("_relationBaselineFingerprint") or ""
            )
            job.context = context
        relation_diff = ontology_relation_delivery_diff(job.context or {}, relation_previous_context)
        if ontology_relation_delivery_metadata(job.context or {}):
            context = dict(job.context or {})
            context["ontologyRelationDiff"] = relation_diff
            transition = relation_diff.get("decisionTransition") if isinstance(relation_diff.get("decisionTransition"), dict) else {}
            if transition:
                context["decisionTransition"] = transition
            context.pop("newsImpact", None)
            news_impact = news_story_impact_from_context(context)
            if news_impact:
                decision_driver_confirmed = news_story_is_decision_driver(news_impact, context)
                decision_changing = news_story_changes_decision(news_impact, relation_diff, context)
                news_impact["decisionDriverConfirmed"] = decision_driver_confirmed
                news_impact["decisionChanging"] = decision_changing
                news_impact["deliveryMode"] = "decision-inline" if decision_changing else "event-digest"
                if decision_changing:
                    context["newsImpact"] = news_impact
            job.context = context
        if relation_diff.get("material") and relation_diff.get("changedComponents") not in ([], ["initial"]):
            reason = "관계 그래프 변화: " + str(relation_diff.get("reason") or "의미 있는 관계 변화")
            if reason not in decision.reasons:
                decision.reasons.append(reason)
        decision = apply_state_cooldown_rule(
            decision,
            rule,
            recent_count,
            cooldown_previous_context,
            last_sent_at,
            age_minutes_since(last_sent_at),
            job,
        )
        decision = apply_similarity_rule(decision, rule, recent_count, previous_context, job)
        decision = attach_previous_profit_loss_context(decision, job, previous_context)
        return apply_market_hours_rule(decision, rule, job)

    def apply_result(
        self,
        job: NotificationJob,
        decision,
        settings: Mapping[str, object] = None,
    ) -> NotificationAdmissionOutcome:
        context = dict(job.context or {})
        context.update(decision.to_context())
        state_group_key = notification_state_group_key(job)
        if state_group_key:
            context["deliveryStateGroupKey"] = state_group_key
        freshness = evaluate_notification_data_freshness(context, dict(settings or {}))
        context.update(freshness.to_context())
        job.context = sanitize_notification_context_for_freshness(context, freshness)
        if decision.should_send and not freshness.should_send:
            if str(job.message_type or "") == INVESTMENT_INSIGHT:
                context = dict(job.context or {})
                context["freshnessDeferredToDispatch"] = True
                context["freshnessDeferredReason"] = str(freshness.reason or "")
                job.context = context
                return NotificationAdmissionOutcome(True, True, job.status, str(freshness.reason or ""))
            job.status = "suppressed"
            job.last_error = "데이터 신선도 기준 미통과로 발송하지 않았습니다. " + str(freshness.reason or "")
            job.context["deliverySuppressionReason"] = "stale_data"
            return NotificationAdmissionOutcome(False, True, job.status, job.last_error)
        if not decision.should_send:
            job.status = "suppressed"
            if decision.suppression_reason == "market_closed":
                job.last_error = "장 시간 외라 발송하지 않았습니다. " + str(decision.market_hours_reason or "")
            elif decision.suppression_reason == "state_cooldown":
                job.last_error = decision.state_reason or "같은 임계값 상태가 지속되어 발송하지 않았습니다."
            else:
                job.last_error = decision.gate_reason or "발송 조건을 충족하지 않아 보내지 않았습니다."
            return NotificationAdmissionOutcome(False, True, job.status, job.last_error)
        return NotificationAdmissionOutcome(True, True, job.status, "")
