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
        for value in ("체결·수급 확인 중단", "N-A1234567", "지금도 유지되는지 확인할 수 없습니다",
                      "10/13 10:20 KST", "이번 확인 10/13 10:40 KST", "20분", "5분"):
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
        text = typedb_observation_telegram_message(self.context)
        self.assertIn("체결강도: 이전 알림 120 → 이번 92", text)
        self.assertIn("판정 기준 체결강도 110 이상", text)
        self.assertIn("10/13 10:25 KST", text)
        self.assertLess(text.index("체결강도: 이전"), text.index("현재 전체 데이터"))
        self.assertTrue(self.context["customerInvestmentDocument"]["lead"].startswith("‘"))

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
        self.assertIsNone(packet["transitions"][0]["dataIssues"][0]["ageMinutes"])
