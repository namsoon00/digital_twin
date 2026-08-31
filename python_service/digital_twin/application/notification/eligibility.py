"""Dispatch-time notification eligibility gates."""

from datetime import datetime
from typing import Callable, Dict, Mapping
from zoneinfo import ZoneInfo

from ...domain.data_freshness import (
    bool_setting,
    evaluate_notification_data_freshness,
    sanitize_notification_context_for_freshness,
)
from ...domain.message_types import INVESTMENT_INSIGHT, ONTOLOGY_REASONING_QUEUE
from ...domain.market_hours import (
    default_market_hours_markets,
    evaluate_market_hours,
    normalize_off_hours_delivery_mode,
)
from ...domain.notification_ai_delivery import first_holding_review_delivery_is_authorized
from ...domain.notification_ai_context import is_graph_backed_relation_context
from ...domain.notifications import NotificationJob


class NotificationDispatchEligibilityService:
    def __init__(
        self,
        queue,
        settings: Mapping[str, object] = None,
        now_provider: Callable = None,
        operational_state_resolver: Callable = None,
        operational_delivery_recorder: Callable = None,
        fresh_data_recheck_requester: Callable = None,
        ai_defer_predicate: Callable = None,
        outcome_recorder: Callable = None,
        freshness_enabled: bool = True,
    ):
        self.queue = queue
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(ZoneInfo("UTC")))
        self.operational_state_resolver = operational_state_resolver
        self.operational_delivery_recorder = operational_delivery_recorder
        self.fresh_data_recheck_requester = fresh_data_recheck_requester
        self.ai_defer_predicate = ai_defer_predicate or (lambda _job: False)
        self.outcome_recorder = outcome_recorder
        self.freshness_enabled = bool(freshness_enabled)

    def record_outcome(self, job: NotificationJob, status: str, reason: str = "") -> None:
        if callable(self.outcome_recorder):
            self.outcome_recorder(job, status, reason)

    def suppress(self, job: NotificationJob, reason: str) -> None:
        if hasattr(self.queue, "mark_suppressed"):
            self.queue.mark_suppressed(job, reason)
        else:
            self.queue.mark_failed(job, reason)
        self.record_operational_delivery(job, "suppressed", reason)
        self.record_outcome(job, "suppressed", reason[:160])

    def apply_operational_state_gate(self, job: NotificationJob, stage: str) -> bool:
        if str(job.message_type or "") != ONTOLOGY_REASONING_QUEUE:
            return True
        if not callable(self.operational_state_resolver):
            return True
        context = dict(job.context or {})
        expected = context.get("queueDelayHealth")
        expected = expected if isinstance(expected, dict) else {}
        expected_state = str(expected.get("state") or "").strip().lower()
        if not expected_state:
            return True
        try:
            current = self.operational_state_resolver()
        except Exception as error:
            context["operationalDispatchState"] = {
                "status": "unavailable",
                "stage": stage,
                "reason": str(error)[:180],
            }
            job.context = context
            return True
        current = dict(current or {}) if isinstance(current, dict) else {}
        current_state = str(current.get("state") or "").strip().lower()
        context["operationalDispatchState"] = {
            "status": "checked" if current_state else "unknown",
            "stage": stage,
            "expectedState": expected_state,
            "currentState": current_state,
            "checkedAt": str(current.get("checkedAt") or ""),
        }
        job.context = context
        if not current_state or current_state == expected_state:
            return True
        expected_active = expected_state in {"delayed", "critical"}
        current_incident = current_state in {"delayed", "critical", "draining"}
        obsolete = (expected_active and not current_incident) or (
            expected_state == "healthy" and current_incident
        )
        if not obsolete:
            return True
        reason = (
            stage + " 운영 상태 재검증: 추론 대기열이 "
            + expected_state + "에서 " + current_state
            + "로 변경되어 이전 상태 알림을 발송하지 않았습니다."
        )
        context["deliverySuppressionReason"] = "obsolete_queue_health_at_dispatch"
        context["operationalDispatchState"]["status"] = "suppressed-obsolete"
        job.context = context
        self.suppress(job, reason)
        return False

    def apply_inference_change_gate(self, job: NotificationJob) -> bool:
        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return True
        context = dict(job.context or {})
        relation = context.get("ontologyRelationContext")
        relation = relation if isinstance(relation, dict) else {}
        relation_diff = context.get("ontologyRelationDiff")
        relation_diff = relation_diff if isinstance(relation_diff, dict) else {}
        if not is_graph_backed_relation_context(relation) or "material" not in relation_diff:
            return True
        material = bool(relation_diff.get("material"))
        first_holding_review = (
            not material
            and first_holding_review_delivery_is_authorized(context)
        )
        context["inferenceChangeGate"] = {
            "version": "dispatch-inference-change-v2",
            "decision": "send" if material or first_holding_review else "suppress",
            "material": material,
            "reason": (
                "보유 종목에서 조건 확인이 필요한 첫 TypeDB 판단"
                if first_holding_review
                else str(relation_diff.get("reason") or "")
            ),
        }
        job.context = context
        if material or first_holding_review:
            return True
        reason = "TypeDB 관계 판단과 사용자 행동 범위가 이전 추론과 같아 AI와 알림을 다시 실행하지 않습니다."
        context["deliverySuppressionReason"] = "unchanged_graph_inference"
        job.context = context
        self.suppress(job, reason)
        return False

    def apply_market_hours_gate(self, job: NotificationJob, stage: str) -> bool:
        context = dict(job.context or {})
        message_type = str(job.message_type or "")
        enabled_value = context.get("marketHoursEnabled")
        if enabled_value is None:
            context["dispatchMarketHoursGate"] = {
                "version": "dispatch-market-hours-v1",
                "stage": stage,
                "status": "bypass-missing-admission-policy",
                "decision": "send",
                "reason": "입장 단계의 장 시간 정책 정보가 없는 직접 큐 작업이라 재검증하지 않습니다.",
            }
            job.context = context
            return True
        enabled = str(enabled_value).strip().lower() not in {"0", "false", "no", "off", "disabled"}
        markets = context.get("marketHoursMarkets")
        markets = markets if isinstance(markets, list) else default_market_hours_markets(message_type)
        off_hours_mode = normalize_off_hours_delivery_mode(
            context.get("offHoursDeliveryMode"),
            message_type,
        )
        decision = evaluate_market_hours(
            message_type,
            context,
            enabled,
            markets,
            now=self.now_provider(),
            off_hours_mode=off_hours_mode,
        )
        context.update(decision.to_context())
        context["dispatchMarketHoursGate"] = {
            "version": "dispatch-market-hours-v2",
            "stage": stage,
            "status": decision.status,
            "decision": "send",
            "blockingDisabled": True,
            "reason": decision.reason,
            "checkedAt": decision.local_time,
            "offHoursDeliveryMode": decision.off_hours_mode,
        }
        context["marketHoursDecision"] = "advisory" if decision.status == "closed" else "send"
        if decision.status in {"closed", "closed_exception"}:
            context["marketHoursPriceBasis"] = {
                "version": "market-hours-price-basis-v1",
                "status": "closed-market-reference",
                "reason": "장 마감 상태의 표시 가격은 실시간 체결가격이 아닐 수 있어 주문 전에 다시 확인해야 합니다.",
            }
        if str(context.get("deliverySuppressionReason") or "") in {
            "market_closed",
            "market_hours",
            "market_closed_at_dispatch",
        }:
            context.pop("deliverySuppressionReason", None)
        context.pop("marketHoursDeferral", None)
        job.context = context
        if str(job.message_type or "") == INVESTMENT_INSIGHT and stage == "AI 판단 전":
            context["preAiMarketHoursAssessment"] = {
                "version": "pre-ai-market-hours-assessment-v1",
                "status": decision.status,
                "wouldSend": bool(decision.should_send),
                "reason": decision.reason,
                "offHoursDeliveryMode": decision.off_hours_mode,
                "blocking": False,
            }
            job.context = context
            return True
        return True

    def apply_dispatch_freshness_gate(self, job: NotificationJob, stage: str) -> bool:
        if not self.freshness_enabled:
            return True
        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(ZoneInfo("UTC"))
        decision = evaluate_notification_data_freshness(job.context or {}, self.settings, now=now)
        context = dict(job.context or {})
        context.update(decision.to_context())
        job.context = sanitize_notification_context_for_freshness(context, decision, now=now)
        if decision.should_send:
            return True
        reason = stage + " 데이터 신선도 기준 미통과: " + str(decision.reason or decision.status)
        recheck = self.request_fresh_data_recheck(job, stage, reason)
        context = dict(job.context or {})
        context["dataFreshnessDecision"] = "advisory"
        context["notificationFreshnessAdvisory"] = {
            "version": "notification-freshness-advisory-v1",
            "blockingDisabled": True,
            "stage": stage,
            "status": str(decision.status or ""),
            "reason": str(decision.reason or ""),
            "ageMinutes": decision.age_minutes,
            "maxAgeMinutes": decision.max_age_minutes,
            "staleSources": list(decision.stale_sources or []),
            "recheckRequested": bool(recheck.get("requested")),
        }
        if str(context.get("deliverySuppressionReason") or "") in {
            "stale_data",
            "stale_data_at_dispatch",
            "stale_data_recheck_requested",
        }:
            context.pop("deliverySuppressionReason", None)
        job.context = context
        return True

    def apply_ai_freshness_headroom_gate(self, job: NotificationJob) -> bool:
        if not self.freshness_enabled or not bool_setting(
            self.settings,
            "dataFreshnessEnabled",
            True,
        ):
            context = dict(job.context or {})
            context["aiFreshnessHeadroomGate"] = {
                "version": "ai-freshness-headroom-v1",
                "decision": "bypass",
                "blockingDisabled": True,
                "reason": "데이터 신선도 검증이 비활성화되어 AI 처리시간 여유를 차단 조건으로 사용하지 않습니다.",
            }
            if str(context.get("deliverySuppressionReason") or "") == "ai_freshness_headroom_recheck":
                context.pop("deliverySuppressionReason", None)
            job.context = context
            return True
        if not self.ai_defer_predicate(job):
            return True
        context = dict(job.context or {})
        try:
            age = float(context.get("dataFreshnessAgeMinutes"))
            maximum = float(context.get("dataFreshnessMaxAgeMinutes"))
            reserve = float(self.settings.get("notificationAiFreshnessReserveMinutes") or 4)
        except (TypeError, ValueError):
            return True
        if maximum < 5 or maximum - age >= max(1.0, reserve):
            return True
        reason = (
            "AI 판단 전 데이터 유효시간 여유 부족: 남은 "
            + str(round(maximum - age, 1))
            + "분, 필요한 여유 " + str(round(reserve, 1)) + "분"
        )
        recheck = self.request_fresh_data_recheck(job, "AI 큐 등록 전", reason)
        context = dict(job.context or {})
        context["aiFreshnessHeadroomGate"] = {
            "version": "ai-freshness-headroom-v2",
            "decision": "advisory",
            "blockingDisabled": True,
            "ageMinutes": age,
            "maxAgeMinutes": maximum,
            "reserveMinutes": reserve,
            "recheckRequested": bool(recheck.get("requested")),
            "reason": reason,
        }
        if str(context.get("deliverySuppressionReason") or "") == "ai_freshness_headroom_recheck":
            context.pop("deliverySuppressionReason", None)
        job.context = context
        return True

    def request_fresh_data_recheck(self, job: NotificationJob, stage: str, reason: str) -> Dict[str, object]:
        if str(job.message_type or "") != INVESTMENT_INSIGHT or not callable(self.fresh_data_recheck_requester):
            return {"requested": False}
        context = dict(job.context or {})
        if isinstance(context.get("freshDataRecheck"), dict) and context["freshDataRecheck"].get("requested"):
            return dict(context["freshDataRecheck"])
        symbol = self.symbol_from_job(job)
        audit = {
            "version": "fresh-data-recheck-v1",
            "requested": False,
            "stage": stage,
            "accountId": str(job.account_id or ""),
            "symbol": symbol,
            "reason": reason,
        }
        try:
            outcome = self.fresh_data_recheck_requester(job.account_id, symbol, job.job_id)
            if isinstance(outcome, dict):
                audit.update(outcome)
            audit["requested"] = bool(audit.get("requested", True))
        except Exception as error:
            audit["error"] = str(error)[:180]
        context["freshDataRecheck"] = audit
        job.context = context
        return audit

    @staticmethod
    def symbol_from_job(job: NotificationJob) -> str:
        context = job.context if isinstance(job.context, dict) else {}
        relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
        subject = relation.get("subject") if isinstance(relation.get("subject"), dict) else {}
        return str(
            context.get("symbol")
            or context.get("rawSymbol")
            or subject.get("symbol")
            or ""
        ).strip().upper()

    def record_operational_delivery(self, job: NotificationJob, outcome: str, reason: str = "") -> None:
        if not callable(self.operational_delivery_recorder):
            return
        try:
            self.operational_delivery_recorder(job, outcome, reason)
        except Exception:
            return
