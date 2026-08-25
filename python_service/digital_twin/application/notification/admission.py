"""Pure notification admission policy separated from MySQL persistence."""

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Mapping

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
    material_evidence_present,
)
from ...domain.context_observation_notifications import typedb_context_observation_contract
from ...domain.notification_ai_context import relation_context_value
from ...domain.notifications import NotificationJob
from ...domain.notification.lifecycle import age_minutes_since
from ...domain.sent_article_filter import (
    news_story_changes_decision,
    news_story_is_decision_driver,
    news_story_impact_from_context,
)


POST_DECISION_DELIVERY_REASONS = {
    "market_closed",
    "market_hours",
}


def _unique_texts(values) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in rows:
            rows.append(text)
    return rows


def _relation_trigger_provenance(context: Mapping[str, object]) -> Dict[str, object]:
    values = dict(context or {})
    relation = relation_context_value(values)
    graph = relation.get("graphStoreInference") if isinstance(relation.get("graphStoreInference"), dict) else {}
    subgraph = relation.get("evidenceSubgraph") if isinstance(relation.get("evidenceSubgraph"), dict) else {}
    rule_ids = list(subgraph.get("matchedRuleIds") or [])
    evidence_ids: List[str] = []
    trace_ids: List[str] = []
    for item in list(subgraph.get("nodes") or []) + list(subgraph.get("edges") or []):
        if isinstance(item, dict):
            evidence_ids.append(item.get("id") or item.get("evidenceId") or item.get("relationId"))
    for trace in graph.get("traces") or []:
        if not isinstance(trace, dict):
            continue
        rule_ids.append(trace.get("ruleId"))
        trace_ids.append(trace.get("id") or trace.get("traceId"))
        evidence_ids.extend(trace.get("evidenceRelationIds") or [])
    source = values.get("newsImpact") if isinstance(values.get("newsImpact"), dict) else {}
    if not source:
        candidates = []
        for field in (
            "newsItems",
            "newsHeadlines",
            "researchEvidence",
            "disclosures",
            "dartDisclosures",
        ):
            container = values.get(field)
            if isinstance(container, dict):
                container = container.get("items") or container.get("rows") or container.get("evidence") or []
            if isinstance(container, list):
                candidates.extend(item for item in container if isinstance(item, dict))
        candidates.extend(
            item
            for item in subgraph.get("nodes") or []
            if isinstance(item, dict)
        )
        source = next((
            item for item in candidates
            if item.get("headline") or item.get("title")
        ), {})
    return {
        "ruleIds": _unique_texts(rule_ids),
        "evidenceIds": _unique_texts(evidence_ids),
        "traceIds": _unique_texts(trace_ids),
        "sourceTitle": str(source.get("headline") or source.get("title") or "").strip(),
        "sourceProvider": str(source.get("source") or source.get("provider") or "").strip(),
        "sourceUrl": str(source.get("url") or "").strip(),
        "sourceObservedAt": str(source.get("publishedAt") or source.get("observedAt") or "").strip(),
    }


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
        provenance = _relation_trigger_provenance(job.context or {})
        if relation_diff.get("material") and relation_diff.get("changedComponents") not in ([], ["initial"]):
            reason = "관계 그래프 변화: " + str(relation_diff.get("reason") or "의미 있는 관계 변화")
            if reason not in decision.reasons:
                decision.reasons.append(reason)
            decision.add_trigger(
                "typedb-relation-change",
                "typedb-relation-diff",
                "관계 판단 변화",
                reason,
                status="matched",
                currentValue=list(relation_diff.get("changedComponents") or []),
                source="typedb-relation-delivery-diff",
                triggerCategory="material-change",
                customerVisible=True,
                **provenance,
            )
        source_title = str(provenance.get("sourceTitle") or "").strip()
        if source_title and material_evidence_present(job.context or {}):
            source_provider = str(provenance.get("sourceProvider") or "").strip()
            source_reason = "새 근거: " + source_title
            if source_provider:
                source_reason += " (" + source_provider + ")"
            evidence_ids = provenance.get("evidenceIds") or []
            source_identity = str(evidence_ids[0] if evidence_ids else provenance.get("sourceUrl") or source_title)
            source_key = hashlib.sha256(source_identity.encode("utf-8")).hexdigest()[:20]
            decision.add_trigger(
                "material-evidence:" + source_key,
                "material-evidence",
                "확인된 새 근거",
                source_reason,
                status="matched",
                source="typedb-evidence-provenance",
                triggerCategory="evidence",
                customerVisible=True,
                **provenance,
            )
        observation = typedb_context_observation_contract(job.context or {})
        unresolved_material_evidence = bool(
            observation
            and "news.direct_material_context" in str(observation.get("selectedRuleId") or "")
            and not source_title
        )
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
        decision = apply_market_hours_rule(decision, rule, job)
        if unresolved_material_evidence:
            decision.mark_suppressed(
                "unresolved_material_evidence",
                "참고 관계를 만든 정확한 뉴스 원문을 연결하지 못해 사용자 알림을 보내지 않습니다.",
            )
        return decision

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
        relation_diff = context.get("ontologyRelationDiff")
        relation_diff = relation_diff if isinstance(relation_diff, dict) else {}
        relation_material_known = "material" in relation_diff
        relation_material = bool(relation_diff.get("material"))
        state_reason_codes = {
            "baseline": "initial_graph_baseline",
            "unchanged-inference": "unchanged_graph_inference",
            "in-flight": "in_flight_duplicate",
            "cooldown": "state_cooldown",
        }
        state_reason_code = state_reason_codes.get(str(decision.state_decision or ""), "")
        if decision.state_suppressed and state_reason_code:
            decision.suppression_reason = state_reason_code
            decision.gate_reason = str(decision.state_reason or decision.gate_reason or "")
        elif decision.similarity_suppressed:
            decision.suppression_reason = "similar_repeat"
            decision.gate_reason = str(decision.similarity_reason or decision.gate_reason or "")
        context = dict(job.context or {})
        context.update(decision.to_context())
        job.context = context
        if (
            not decision.should_send
            and str(job.message_type or "") == INVESTMENT_INSIGHT
            and str(decision.suppression_reason or "") in POST_DECISION_DELIVERY_REASONS
            and (not relation_material_known or relation_material)
        ):
            context = dict(job.context or {})
            reason_codes = [str(decision.suppression_reason or "")]
            if decision.state_suppressed and decision.state_decision == "cooldown":
                reason_codes.append("state_cooldown")
            if decision.similarity_suppressed:
                reason_codes.append("similar_repeat")
            reason_codes = list(dict.fromkeys(item for item in reason_codes if item))
            context["preDecisionDeliveryGate"] = {
                "version": "pre-decision-delivery-gate-v1",
                "status": "deferred",
                "reasonCode": str(decision.suppression_reason or ""),
                "reasonCodes": reason_codes,
                "reasonDetails": {
                    "state_cooldown": str(decision.state_reason or ""),
                    "similar_repeat": str(decision.similarity_reason or ""),
                    "market_closed": str(decision.market_hours_reason or ""),
                },
                "reason": str(decision.gate_reason or decision.state_reason or decision.market_hours_reason or ""),
                "evaluatedBeforeAi": True,
            }
            context["deliveryDecision"] = "decision_pending"
            context["deliveryGateState"] = "deferred_until_decision"
            context["deliveryGateReason"] = "투자 판단을 먼저 완료한 뒤 발송 정책을 다시 적용합니다."
            context.pop("deliverySuppressionReason", None)
            job.context = context
            job.status = "pending"
            job.last_error = ""
            return NotificationAdmissionOutcome(
                True,
                True,
                job.status,
                "투자 판단 완료 후 발송 정책 재검사",
            )
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
