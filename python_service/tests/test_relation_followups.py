"""Customer follow-ups require specific source facts and a delivered parent."""
from copy import deepcopy
import unittest

from test_typedb_independent_observation import readable_fixture
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.investment_conversation import relation_parent_job_id


def context_fixture():
    context = readable_fixture()
    relation = context["ontologyRelationContext"]
    row = relation["hypothesisLifecycle"]["transitions"][0]
    row.update(transitionId="first", lifecycleKey="recovery", hypothesisIds=["h1"], sourceRuleIds=["rule:linked"])
    relation["observationProfiles"] = {"flow": {
        "sourceAsOf": "2026-10-13T01:20:00Z", "freshnessAgeMinutes": 20, "maxAgeMinutes": 5,
        "observationSource": "Fixture", "judgementEvidenceUsable": False,
        "freshnessStatus": "stale", "marketSessionStatus": "open"}}
    row["record"] = {"snapshot": {"policy": {"requiredFreshnessDomains": ["flow"]}}}
    return context


def receipt(packet, job_id="a1234567-root"):
    return {**delivery_snapshot(packet, job_id, "main"), "receiptJobId": job_id, "deliveredAt": "2026-10-13T01:00:00Z"}


def change(context, kind="data-unavailable", identity="interruption"):
    row = context["ontologyRelationContext"]["hypothesisLifecycle"]["transitions"][0]
    row.update(transitionId=identity, currentState="maintained", previousState="maintained", materialChange=False,
               occurredAt="2026-10-13T01:40:00Z", dataAvailabilityChange={
                   "kind": kind, "previousState": "unavailable" if kind == "data-restored" else "usable",
                   "currentState": "usable" if kind == "data-restored" else "unavailable"})
    if kind == "data-restored":
        context["ontologyRelationContext"]["observationProfiles"]["flow"].update(
            judgementEvidenceUsable=True, freshnessStatus="fresh", freshnessAgeMinutes=1, sourceAsOf="2026-10-13T01:39:00Z")


class RelationFollowUpTests(unittest.TestCase):
    def setUp(self):
        self.context = context_fixture()
        self.original = receipt(relation_change_evidence(self.context))

    def test_processing_expiry_is_a_refresh_request_not_a_source_interruption(self):
        from digital_twin.modules.model_registry.domain.hypothesis_data_readiness import issue, VERSION
        from digital_twin.modules.notifications.domain.context_observation_notifications import context_observation_delivery_decision
        change(self.context)
        relation = self.context["ontologyRelationContext"]
        row = relation["hypothesisLifecycle"]["transitions"][0]
        profile = {**relation["observationProfiles"]["flow"], "sourceAsOf": "2026-10-13T01:39:29Z",
                   "maxAgeMinutes": 10, "marketSessionLocalTime": "2026-10-13T10:50:23+09:00"}
        problem = issue("rule:linked", "flow", "source-stale", "원천 기준시각이 허용 시간 10분을 초과했습니다.", profile)
        row["record"]["snapshot"]["dataAvailability"] = {"version": VERSION, "issues": [problem]}
        frozen = deepcopy(self.context)
        packet = relation_change_evidence(self.context, self.original)
        self.assertFalse(packet["eligible"])
        suppressed = packet["suppressedTransitions"][0]
        self.assertTrue(suppressed["sourceRecheckRequired"])
        detail = suppressed["dataIssues"][0]
        self.assertEqual("processing-expiry", detail["notificationSourceState"])
        self.assertAlmostEqual(31 / 60, detail["sourceAgeAtEventMinutes"])
        self.assertAlmostEqual(10.9, detail["ageMinutes"])
        self.assertEqual(frozen, self.context, "notification policy must not rewrite frozen inference facts")
        self.assertTrue(context_observation_delivery_decision(self.context)["sourceRecheckRequired"])
        # An unrelated processing delay must not hide a proven source outage.
        real = {**problem, "sourceAsOf": "2026-10-13T01:20:00Z"}
        row["record"]["snapshot"]["dataAvailability"]["issues"].append(real)
        mixed = relation_change_evidence(self.context, self.original)
        self.assertTrue(mixed["eligible"])
        self.assertEqual(1, len(mixed["transitions"][0]["dataIssues"]))
        row["record"]["snapshot"]["dataAvailability"]["issues"].pop()
        problem["marketSessionStatus"] = "closed_exception"
        closed = relation_change_evidence(self.context, self.original)
        self.assertFalse(any(s.get("sourceRecheckRequired") for s in closed["suppressedTransitions"]))
        problem["marketSessionStatus"] = "open"
        # Legacy jobs without the evaluation clock also cannot claim a stale
        # source at an event cutoff only 31 seconds after that source.
        problem.pop("checkedAt")
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"])
        # A suppressed interruption must not authorize a later recovery reply.
        change(self.context, "data-restored")
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"])

    def test_real_source_delay_uses_one_clock_and_preserves_receipt_policy(self):
        from digital_twin.modules.model_registry.domain.hypothesis_data_readiness import issue, VERSION
        change(self.context)
        relation = self.context["ontologyRelationContext"]
        row = relation["hypothesisLifecycle"]["transitions"][0]
        profile = {**relation["observationProfiles"]["flow"], "marketSessionLocalTime": "2026-10-13T10:45:00+09:00"}
        problem = issue("rule:linked", "flow", "source-stale", "자료가 허용 시간 5분을 초과했습니다.", profile)
        row["record"]["snapshot"]["dataAvailability"] = {"version": VERSION, "issues": [problem]}
        packet = relation_change_evidence(self.context, self.original)
        self.assertTrue(packet["eligible"])
        self.context["relationChangeEvidence"] = packet
        text = typedb_observation_telegram_message(self.context)
        self.assertIn("유효성 검사 10/13 10:45 KST", text)
        self.assertIn("25분", text)
        self.assertNotIn("20분 전 기준", text)
        self.assertFalse(relation_change_evidence(self.context, receipt(packet))["eligible"])
        problem["marketSessionStatus"] = "closed"
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"])

    def test_delayed_queued_notice_requests_refresh_once_without_delivery(self):
        from unittest.mock import Mock
        from digital_twin.modules.notifications.application.notification.eligibility import NotificationDispatchEligibilityService
        change(self.context)
        relation = self.context["ontologyRelationContext"]
        relation["observationProfiles"]["flow"].update(sourceAsOf="2026-10-13T01:39:00Z")
        self.context["relationChangeEvidence"] = {"previous": self.original}
        job = NotificationJob.create("old queued notice", account_id="main", message_type="investmentInsight", context=self.context)
        queue, request = Mock(), Mock(return_value={"requested": True})
        service = NotificationDispatchEligibilityService(queue, fresh_data_recheck_requester=request)
        self.assertFalse(service.apply_dispatch_freshness_gate(job, "발송 직전"))
        self.assertTrue(job.context["freshDataRecheck"]["requested"])
        self.assertEqual("source_status_recheck_required", job.context["deliverySuppressionReason"])
        service.apply_dispatch_freshness_gate(job, "발송 재시도")
        request.assert_called_once()
        queue.mark_suppressed.assert_called()
        from test_investment_insight_dispatch_service import (
            subject_case, alert, FakeOrchestrator, FakeIngress, FakeNotificationQueue,
        )
        from digital_twin.modules.decisions.application.investment_insight_dispatch_service import InvestmentInsightDispatchService
        case = subject_case("subject:readable")
        outbound = FakeNotificationQueue()
        publication_request = Mock(return_value={"requested": True})
        publisher = InvestmentInsightDispatchService(FakeIngress(), outbound, None, FakeOrchestrator([case]),
                                                     source_recheck_requester=publication_request)
        publisher.dispatch([alert(case, self.context, "processing-delay")])
        publication_request.assert_called_once_with("main")
        self.assertFalse(outbound.jobs, "publication must archive the false interruption before queueing")
        dispatch = InvestmentInsightDispatchService(None, None, None, None, source_recheck_requester=request)
        audit = {"sourceRecheckRequired": True}
        dispatch._request_source_recheck(audit, job)
        self.assertTrue(audit["sourceRecheck"]["requested"])
        request.side_effect = RuntimeError("fixture")
        dispatch._request_source_recheck(audit, job)
        self.assertFalse(audit["sourceRecheck"]["requested"])
        self.assertEqual("RuntimeError", audit["sourceRecheck"]["error"])
        from unittest.mock import patch
        from digital_twin.infrastructure.composition.reasoning_delivery import request_source_recheck
        with patch('digital_twin.infrastructure.composition.market_data.monitor_account_job_store_from_settings') as factory:
            self.assertFalse(request_source_recheck({}, 'main', lambda: False)['requested'])
            factory.assert_not_called()
            factory.return_value = None
            direct = request_source_recheck({'monitorAccountIntervalSeconds': 45}, 'main', lambda: True)
            self.assertTrue(direct['requested'])
            self.assertEqual('next-monitor-cycle', direct['scheduleMode'])
            self.assertEqual(45, direct['maxDelaySeconds'])
            store = factory.return_value = Mock()
            store.request_refresh.return_value = {'requested': True, 'scheduledAt': 'fixture'}
            self.assertEqual('fixture', request_source_recheck({}, 'main', lambda: True)['scheduledAt'])
            store.request_refresh.assert_called_once_with('main', priority=5)

    def test_frozen_rule_issue_explains_its_own_interruption_not_unrelated_live_profiles(self):
        from digital_twin.modules.model_registry.domain.hypothesis_data_readiness import issue, VERSION
        change(self.context)
        relation = self.context["ontologyRelationContext"]
        row = relation["hypothesisLifecycle"]["transitions"][0]
        problem = issue("rule:linked", "flow", "source-stale", "체결·수급 자료가 허용 시간 5분을 지났습니다.",
                        relation["observationProfiles"]["flow"], rule_label="수급 회복 확인")
        unrelated = issue("unrelated", "quote", "source-stale", "다른 규칙의 시세 지연")
        row["record"]["snapshot"]["dataAvailability"] = {"version": VERSION, "state": "unavailable", "issues": [problem, unrelated]}
        # A later profile must not rewrite the source time of this frozen event.
        relation["observationProfiles"]["flow"].update(sourceAsOf="2026-10-13T01:39:00Z", freshnessStatus="fresh")
        packet = relation_change_evidence(self.context, self.original)
        self.assertEqual(1, len(packet["transitions"][0]["dataIssues"]))
        self.assertEqual("a1234567-root", packet["followUp"]["parentJobId"])
        self.context["relationChangeEvidence"] = packet
        text = typedb_observation_telegram_message(self.context)
        self.assertIn("매수·매도 흐름 회복 확인", text)
        self.assertIn("허용 시간 5분", text)
        self.assertIn("재확인:", text)
        self.assertIn("10/13 10:20 KST", text)
        self.assertNotIn("다른 규칙의 시세 지연", text)
        self.assertFalse(relation_change_evidence(self.context)["eligible"], "source failure still needs a delivered parent")

    def test_model_input_failure_keeps_precise_web_reason_without_feed_alert(self):
        from digital_twin.modules.model_registry.domain.hypothesis_data_readiness import issue, VERSION
        change(self.context)
        row = self.context["ontologyRelationContext"]["hypothesisLifecycle"]["transitions"][0]
        reason = "수급 회복 확인: 이 규칙의 현재 모델 평가 결과가 없습니다."
        row["record"]["snapshot"].update(dataAvailability={"version": VERSION, "state": "unavailable", "issues": [
            issue("rule:linked", "flow", "model-missing", reason)]}, judgementRefresh={"reason": reason})
        packet = relation_change_evidence(self.context, self.original)
        self.assertFalse(packet["eligible"])
        self.assertEqual("relation_followup_not_deliverable", packet["nonDeliveryReasonCode"])
        self.assertIn(reason, packet["reason"])
        self.assertNotIn("새 의미 변화가 없어", packet["reason"])

    def test_admission_does_not_replace_data_hold_reason_with_delivery_cooldown(self):
        from digital_twin.modules.notifications.application.notification.admission import NotificationAdmissionPolicy
        from digital_twin.modules.notifications.domain.notification_rules import default_notification_rule
        change(self.context)
        row = self.context["ontologyRelationContext"]["hypothesisLifecycle"]["transitions"][0]
        row["record"]["snapshot"].update(
            dataAvailability={"version": "hypothesis-data-readiness-v1", "state": "unavailable", "issues": []},
            judgementRefresh={"reason": "가격 회복 확인: price-window 조건의 관측값이 없습니다."})
        policy = NotificationAdmissionPolicy()
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create("관계 확인", account_id="main", message_type="investmentInsight", context=self.context)
        decision = policy.evaluate(job, rule, policy.prepare(job, rule))
        self.assertEqual("relation_followup_not_deliverable", decision.suppression_reason)
        decision.state_suppressed, decision.state_decision = True, "cooldown"
        decision.state_reason = "60분 발송 간격 대기"
        outcome = policy.apply_result(job, decision)
        self.assertFalse(outcome.accepted)
        self.assertIn("price-window", job.last_error)
        self.assertEqual("relation_followup_not_deliverable", job.context["deliverySuppressionReason"])
        from contextlib import nullcontext
        from types import SimpleNamespace
        from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
        store = SimpleNamespace(connect=lambda: nullcontext(None),
                                relation_delivery_snapshot_with_connection=lambda *_: self.original)
        final_gate = MySQLNotificationJobStore.recheck_delivery_cadence(store, job)
        self.assertFalse(final_gate["allowed"])
        self.assertEqual("relation_followup_not_deliverable", final_gate["suppressionReason"])
        self.assertIn("price-window", final_gate["reason"])

    def test_repeated_hold_without_transition_retains_its_reason(self):
        relation = self.context["ontologyRelationContext"]
        relation["hypothesisLifecycle"].update(transitions=[], records=[{
            "lifecycleKey": "recovery", "dataAvailability": {"state": "unavailable", "reasons": ["수급 원천이 10분 전입니다."]}}])
        packet = relation_change_evidence(self.context, self.original)
        self.assertFalse(packet["eligible"])
        self.assertEqual("rule_evidence_unavailable", packet["nonDeliveryReasonCode"])
        self.assertIn("수급 원천이 10분 전", packet["reason"])

    def test_interruption_replies_once_and_recovery_requires_actual_interruption_delivery(self):
        change(self.context)
        packet = relation_change_evidence(self.context, self.original)
        self.assertTrue(packet["eligible"])
        self.assertEqual("a1234567-root", packet["followUp"]["parentJobId"])
        self.assertFalse(relation_change_evidence(self.context)["eligible"])
        sent = receipt(packet, "b1234567-interruption")
        change(self.context, identity="another-generation")
        self.assertFalse(relation_change_evidence(self.context, sent)["eligible"])
        # Failed or suppressed work does not replace the successful baseline.
        self.assertTrue(relation_change_evidence(self.context, self.original)["eligible"])
        change(self.context, "data-restored", "restored")
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"])
        restored = relation_change_evidence(self.context, sent)
        self.assertTrue(restored["eligible"])
        self.assertEqual("a1234567-root", restored["followUp"]["parentJobId"])
        resumed = receipt(restored, "c1234567-resumed")
        change(self.context, "data-restored", "restored-again")
        self.assertFalse(relation_change_evidence(self.context, resumed)["eligible"])

    def test_routine_closure_unidentified_source_and_timer_expiry_are_quiet(self):
        change(self.context)
        relation = self.context["ontologyRelationContext"]
        profile = relation["observationProfiles"]["flow"]
        profile["marketSessionStatus"] = "closed"
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"])
        profile["marketSessionStatus"] = "open"
        profile.update(judgementEvidenceUsable=True, freshnessStatus="fresh")
        relation["hypothesisLifecycle"]["transitions"][0]["record"]["snapshot"]["judgementRefresh"] = {
            "rules": [{"domain": "flow", "status": "unavailable"}]}
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"], "missing model input is not a failed feed")
        relation["observationProfiles"] = {}
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"])
        row = relation["hypothesisLifecycle"]["transitions"][0]
        row.pop("dataAvailabilityChange")
        row.update(currentState="expired", reason="RuleBox 유효기간 30분을 지났습니다.")
        self.assertFalse(relation_change_evidence(self.context, self.original)["eligible"])
        row.update(currentState="invalidated", reason="새 관측에서 무효화 조건이 성립했습니다.")
        self.assertTrue(relation_change_evidence(self.context, self.original)["eligible"])

    def test_scope_and_success_receipt_cannot_be_replaced_by_symbol_similarity(self):
        change(self.context)
        for field, value in (("accountId", "other"), ("symbol", "OTHER"), ("worldId", "other"),
                             ("worldId", ""), ("deliveredAt", ""), ("receiptJobId", "")):
            with self.subTest(field=field):
                prior = {**deepcopy(self.original), field: value}
                self.assertFalse(relation_change_evidence(self.context, prior)["eligible"])
        prior = deepcopy(self.original)
        prior["hypothesisContinuity"] = {"unrelated-hypothesis": prior["hypothesisContinuity"].pop("recovery")}
        self.assertFalse(relation_change_evidence(self.context, prior)["eligible"])

    def test_unrelated_subject_notice_keeps_original_and_only_delivered_changes_advance_state(self):
        other = deepcopy(self.context)
        raw = other["ontologyRelationContext"]["hypothesisLifecycle"]["transitions"][0]
        raw.update(lifecycleKey="other", transitionId="other-first", hypothesisIds=["other"], sourceRuleIds=["other-rule"])
        intervening = receipt(relation_change_evidence(other, self.original), "other-original")
        change(self.context)
        packet = relation_change_evidence(self.context, intervening)
        self.assertEqual("a1234567-root", packet["followUp"]["parentJobId"])
        self.assertNotEqual(intervening["receiptJobId"], packet["followUp"]["parentJobId"])
        # Raw availability in another receipt was never an interruption notice.
        intervening["dataAvailability"] = {"recovery": {"state": "unavailable"}}
        change(self.context, "data-restored", "restored")
        self.assertFalse(relation_change_evidence(self.context, intervening)["eligible"])

    def test_multiple_originals_are_not_attached_to_an_arbitrary_parent(self):
        change(self.context)
        relation = self.context["ontologyRelationContext"]
        other = deepcopy(relation["hypothesisLifecycle"]["transitions"][0])
        other.update(lifecycleKey="other", transitionId="other-interruption")
        relation["hypothesisLifecycle"]["transitions"].append(other)
        original = deepcopy(self.original)
        original["hypothesisContinuity"]["other"] = {**deepcopy(original["hypothesisContinuity"]["recovery"]), "rootJobId": "other-original"}
        packet = relation_change_evidence(self.context, original)
        self.assertFalse(packet["eligible"])
        self.assertFalse(packet["followUp"]["parentJobId"])

    def test_final_message_names_source_impact_and_clocks_without_reprinting_full_analysis(self):
        change(self.context)
        self.context["relationChangeEvidence"] = relation_change_evidence(self.context, self.original)
        job = NotificationJob.create("preview", account_id="main", message_type="investmentInsight", context=self.context)
        text = NotificationRenderingService().render(job)
        for value in ("체결·수급 자료 지연", "N-A1234567", "지금도 유지되는지 확인할 수 없습니다",
                      "10/13 10:20 KST", "유효성 검사 10/13 10:40 KST", "20분", "5분"):
            self.assertIn(value, text)
        for omitted in ("현재 전체 데이터", "평가 수익률", "재무 참고", "자료 상태", "model-signal"):
            self.assertNotIn(omitted, text)
        self.assertEqual("a1234567-root", relation_parent_job_id(job))
        self.assertEqual("passed", job.context["customerInvestmentDocumentQuality"]["status"])
        from unittest.mock import Mock
        from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
        from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
        notifier = Mock(supports_research_replies=False)
        with self.assertRaises(NotificationDeliverySuppressed):
            NotificationDispatchService(Mock(), lambda _: notifier).deliver(job, {"main": object()}, text)
        notifier.send.assert_not_called()

    def test_recovery_message_does_not_claim_a_new_market_signal(self):
        change(self.context)
        interrupted = receipt(relation_change_evidence(self.context, self.original), "interruption")
        change(self.context, "data-restored", "restored")
        coverage = self.context["ontologyRelationContext"]["facts"]["marketSignalCoverage"]
        coverage["ccnl"]["sourceAsOf"] = "2026-10-13T01:39:00Z"
        self.context["relationChangeEvidence"] = relation_change_evidence(self.context, interrupted)
        text = typedb_observation_telegram_message(self.context)
        self.assertIn("체결·매수·매도 흐름 확인 재개", text)
        self.assertIn("자료를 다시 사용할 수 있어", text)
        self.assertIn("새 자료에서 다시 확인한 수치", text)
        self.assertIn("체결강도 120", text)
        self.assertIn("새로운 강화·약화 판정은 없습니다", text)
        self.assertNotIn("근거가 이전 분석보다 강해졌습니다", text)
        coverage["ccnl"]["tradeStrengthDecisionUsable"] = False
        self.context["relationChangeEvidence"] = relation_change_evidence(self.context, interrupted)
        self.assertNotIn("체결강도 120", typedb_observation_telegram_message(self.context))
        coverage["ccnl"]["tradeStrengthDecisionUsable"] = True
        coverage["ccnl"]["sourceAsOf"] = "2026-10-13T01:00:00Z"
        self.context["relationChangeEvidence"] = relation_change_evidence(self.context, interrupted)
        self.assertNotIn("체결강도 120", typedb_observation_telegram_message(self.context), "a different source clock cannot stand in for restored data")

    def test_measurement_cause_is_first_and_keeps_its_own_source_clock(self):
        relation = self.context["ontologyRelationContext"]
        raw = relation["hypothesisLifecycle"]["transitions"][0]
        raw.update(transitionId="weakened", currentState="invalidated", previousState="maintained")
        relation["graphStoreInference"]["traces"][0]["matchedConditions"].append({
            "conditionId": "trade-condition", "field": "tradeStrength", "observedValue": 92,
            "expectedValue": 110, "operator": ">=", "observedAt": "2026-10-13T01:25:00Z"})
        self.context["relationChangeEvidence"] = relation_change_evidence(self.context, self.original)
        body = typedb_observation_telegram_message(self.context)
        text = self.context['customerInvestmentDetailText']
        self.assertIn("체결강도: 이전 알림 120 → 이번 92", text)
        self.assertIn("판정 기준 체결강도 110 이상", text)
        self.assertIn("10/13 10:25 KST", text)
        self.assertLess(text.index("체결강도: 이전"), text.index("현재 전체 데이터"))
        self.assertTrue(self.context["customerInvestmentDetailDocument"]["lead"].startswith("‘"))
        self.assertNotIn('120 →', body, 'a missing previous rule condition cannot become a native condition comparison')

    def test_model_input_deltas_explain_score_changes_without_invented_thresholds(self):
        from digital_twin.modules.notifications.domain.relation_change_presentation import model_input_change_rows
        comparison = {"comparison": "verified", "previous": {"modelInputWindows": {
            "3D": {"sampleCount": 3, "recentReturn": .03, "velocityChange": .005}}},
            "current": {"modelInputWindows": {"3D": {"sampleCount": 4, "recentReturn": -.02, "velocityChange": -.012}}}}
        rows = model_input_change_rows(comparison)
        self.assertIn("3일 구간 후반 등락률: 직전 분석 +3% → 이번 -2%", rows[0])
        self.assertIn("+0.5%p → 이번 -1.2%p", rows[1])
        self.assertTrue(all("기준" not in row for row in rows))
        comparison["current"]["modelInputWindows"]["3D"]["sampleCount"] = 0
        self.assertEqual([], model_input_change_rows(comparison))

    def test_source_age_is_computed_from_frozen_clocks_not_fetch_time_or_default_age(self):
        change(self.context)
        profile = self.context["ontologyRelationContext"]["observationProfiles"]["flow"]
        profile.update(sourceFetchedAt="2026-10-13T01:40:00Z", freshnessAgeMinutes=0)
        packet = relation_change_evidence(self.context, self.original)
        self.assertEqual(20, packet["transitions"][0]["dataIssues"][0]["ageMinutes"])
        profile["sourceAsOf"] = "2026-10-14T01:20:00Z"
        packet = relation_change_evidence(self.context, self.original)
        self.assertFalse(packet["eligible"], "future source clocks cannot establish a source interruption")
        self.assertIsNone(packet["suppressedTransitions"][0]["dataIssues"][0]["ageMinutes"])
