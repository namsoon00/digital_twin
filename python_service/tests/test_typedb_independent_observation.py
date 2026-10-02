"""Independent delivery and readable, source-clocked graph observations."""

from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import Mock

from test_investment_insight_dispatch_service import (
    subject_case, context_observation, alert, FakeOrchestrator, FakeIngress,
    FakeNotificationQueue, FakeAIHandoff,
)
from digital_twin.modules.decisions.application.investment_insight_dispatch_service import InvestmentInsightDispatchService
from digital_twin.modules.decisions.domain.investment_reasoning import PUBLISH_TYPEDB, ARCHIVE
from digital_twin.modules.notifications.domain.typedb_publication import independent_typedb_publication
from digital_twin.modules.notifications.domain.context_observation_notifications import typedb_context_observation_contract
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
from digital_twin.modules.read_models.contracts import customer_investment_document_from_dict, customer_investment_document_quality
from digital_twin.infrastructure.transactions.ai_control_publication import AIControlPublication
from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed


def independent_fixture(queue=None, material=True, account_id="main"):
    case = subject_case("subject:independent", action_authority="originate", eligible=("hypothesis:fixture",), outcome="READY")
    case.account_id = account_id
    case.synthesis = replace(case.synthesis, account_id=account_id)
    case.candidate_set = replace(case.candidate_set, account_id=account_id)
    context = context_observation(case)
    relation = context["ontologyRelationContext"]
    basis = {"owner": "market-hypothesis", "ruleKind": "predictive", "decisionEligibility": "action-candidate", "requiresHypothesis": True}
    for row in [*relation["activeRules"], *relation["matchedRules"], *relation["graphStoreInference"]["relations"], *relation["graphStoreInference"]["traces"]]:
        row["knowledgeBasis"] = dict(basis)
    if not material:
        relation["hypothesisLifecycle"] = {}
    context["requiresAiJudgement"] = True
    queue = queue if queue is not None else FakeNotificationQueue()
    handoff = FakeAIHandoff()
    orchestrator = FakeOrchestrator([case])
    service = InvestmentInsightDispatchService(FakeIngress(), queue, handoff, orchestrator, legacy_ai_handoff_enabled=False)
    event = alert(case, context, "independent")
    event.account_id = account_id
    result = service.dispatch([event])
    return result, queue, handoff, case


def readable_fixture():
    case = subject_case("subject:readable")
    context = context_observation(case)
    context["displayTarget"] = "예시종목 / MSTR"
    relation = context["ontologyRelationContext"]
    relation["subject"]["market"] = "KR"
    facts = relation["facts"]
    facts.update({"currency": "KRW", "market": "KR", "currentPrice": 11000, "priceChangeRate": 2.5,
                  "ma20Distance": -1.25, "volume": 10000, "tradingValue": 110000000,
                  "volumeRatio": .8, "timeAdjustedVolumeRatio": 1.5, "tradeStrength": 120,
                  "buyVolume": 6000, "sellVolume": 4000,
                  "orderbookBidVolume": 500, "orderbookAskVolume": 600,
                  "foreignNetVolume": -500, "institutionNetVolume": 0, "individualNetVolume": 0,
                  "foreignBuyVolume": 0, "foreignSellVolume": 0,
                  "quantity": 3, "positionWeight": 2.5, "profitLossRate": 10,
                  "quoteSource": "Fixture /api/private", "investorFlowIsEstimate": True,
                  "marketSignalCoverage": {
                      "price": {"observedFields": ["currentPrice", "changeRate"], "sourceAsOf": "2026-10-02T01:10:00Z"},
                      "investor": {"observedFields": ["foreignNetVolume", "institutionNetVolume"], "status": "available",
                                   "measurementType": "intraday-estimate", "sourceAsOf": "2026-10-02T01:00:00Z",
                                   "participantStatus": {"foreign": "available", "institution": "available", "individual": "not-yet-published"}},
                      "ccnl": {"observedFields": ["volume", "tradingValue", "tradeStrength", "buyVolume", "sellVolume"], "sourceAsOf": "2026-10-02T01:09:00Z"},
                      "orderbook": {"observedFields": ["orderbookBidVolume", "orderbookAskVolume"], "sourceAsOf": "2026-10-02T01:09:00Z"},
                  }})
    rule = {"ruleId": "rule:linked", "label": "반등 유지 여부", "matched": True}
    relation["activeRules"] = [{"ruleId": "policy:" + str(i), "label": "계좌 정책 " + str(i), "referenceOnly": True} for i in range(3)] + [rule]
    relation["matchedRules"] = []
    relation["graphStoreInference"]["traces"] = [{**rule, "matchedConditions": [{
        "conditionId": "validated-model-signal", "matchedByTypeDB": True,
        "observedValue": {"contractMatched": True},
        "matchedTargetProperties": {"contractMatched": True, "modelEvidenceIds": ["stock:MSTR#ma20Distance", "stock:MSTR#priceChangeRate"]},
    }]}]
    relation["hypothesisSet"] = {"hypotheses": [{
        "hypothesisId": "h1", "claim": "long machine statement", "expectedOutcome": "반등이 이어지지 않을 가능성",
        "supportingRuleIds": ["rule:linked"], "evidenceState": "supported",
        "qualification": {"status": "shadow", "decisiveOutcomeCount": 1, "directionalHitRate": 1.0},
        "falsificationContract": "회복 가격대와 거래가 유지되는지 확인",
        "claimContract": {"outcomeContract": {"outcomeHorizonMinutes": [60, 1440], "criteria": [{
            "role": "result", "metric": "instrumentReturnPct", "operator": "<=", "threshold": -.5, "horizonMinutes": 0,
        }]}},
    }]}
    previous = relation_change_evidence(context)["current"]
    next(item for item in previous["facts"] if item["id"] == "currentPrice")["value"] = 10000
    previous["transitions"] = []
    context["relationChangeEvidence"] = relation_change_evidence(context, previous)
    return context


class IndependentTypeDBTests(unittest.TestCase):
    def test_material_graph_observation_never_calls_retired_ai_and_passes_transport_guard(self):
        result, queue, handoff, case = independent_fixture()
        self.assertEqual(1, result["typedbPublishedCount"])
        self.assertEqual(0, result["aiQueuedCount"])
        self.assertEqual([], handoff.events)
        self.assertEqual(PUBLISH_TYPEDB, case.inference_dispatch_decision.route)
        job = next(iter(queue.jobs.values()))
        self.assertTrue(independent_typedb_publication(job.context, account_id=job.account_id))
        self.assertFalse(typedb_context_observation_contract(job.context)["aiHandoffPending"])
        publication = AIControlPublication({"investmentNotificationRoute": "ai-control"}, Mock(), Mock(), lambda: [])
        with publication.delivery_guard(job, "saved observation"):
            pass
        for key, field in (("investmentSubjectDecisionCase", "sourceAboxSnapshotId"),
                           ("inferenceDispatchDecision", "inferenceGenerationId"),
                           ("inferenceDispatchDecision", "accountId"), ("inferenceDispatchDecision", "symbol")):
            altered = deepcopy(job)
            altered.context[key][field] = "other"
            self.assertFalse(independent_typedb_publication(altered.context))
            with self.assertRaises(NotificationDeliverySuppressed), publication.delivery_guard(altered, "saved observation"):
                self.fail("mismatched proof reached transport")
        altered = deepcopy(job)
        altered.context["ontologyRelationContext"]["graphStoreInference"]["inferenceGenerationId"] = "other"
        self.assertFalse(independent_typedb_publication(altered.context))
        self.assertFalse(independent_typedb_publication(job.context, account_id="other"))
        self.assertFalse(independent_typedb_publication({"notificationDecisionOwner": "typedb", "notificationWriterProvenance": job.context["notificationWriterProvenance"]}))

    def test_unchanged_graph_remains_auditable_without_an_ai_request(self):
        result, queue, handoff, case = independent_fixture(material=False)
        self.assertEqual(0, result["typedbPublishedCount"])
        self.assertEqual({}, queue.jobs)
        self.assertEqual([], handoff.events)
        self.assertEqual(ARCHIVE, case.inference_dispatch_decision.route)
        self.assertEqual("archived", case.delivery_state)

    def test_render_keeps_linked_proof_current_data_and_distinct_clocks(self):
        context = readable_fixture()
        text = typedb_observation_telegram_message(context)
        for value in ("관찰 가설", "반등이 이어지지 않을 가능성", "11,000원", "+2.5%", "10,000원 → 11,000원", "외국인: 순매도 500주",
                      "기관: 순매수·매도 차이 0주", "개인: 아직 집계 전", "장중 누적 추정", "10/02 10:00 KST", "10/02 10:10 KST",
                      "체결강도 120", "매수 체결 6,000주", "매수 대기 500주", "매도 대기 600주", "110,000,000원", "계좌 비중 2.5%",
                      "연결된 분석 신호 조건 확인", "참고 측정값", "검증 자료 축적 중", "독립 결과 1건", "60분 · 1일", "-0.5%"):
            self.assertIn(value, text)
        for value in ("long machine statement", "조건값 미보존", "매수 0주", "매도 0주", "100%", "0분 뒤", "/api/private", "계좌 정책 0 —"):
            self.assertNotIn(value, text)
        self.assertEqual("passed", context["customerInvestmentDocumentQuality"]["status"])
        context["ontologyRelationContext"]["facts"]["currentPrice"] = 999
        self.assertIn("11,000원", typedb_observation_telegram_message(context))
        doc = customer_investment_document_from_dict(context["customerInvestmentDocument"])
        broken = replace(doc, lead="강화 · 강화", sections=tuple(row for row in doc.sections if row.key != "investor-flow"))
        issues = customer_investment_document_quality(broken)["issues"]
        self.assertIn("missing-observation-investor-flow", issues)
        self.assertIn("unexplained-observation-change", issues)
        # Admission may have cached a document before the receipt comparison is
        # refreshed. The send path must bind it to the final frozen packet.
        from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
        from digital_twin.modules.notifications.domain.notifications import NotificationJob
        job = NotificationJob.create("fallback", message_type="investmentInsight", context=context)
        renderer = NotificationRenderingService()
        first = renderer.render(job)
        previous_price = next(row for row in job.context["relationChangeEvidence"]["previous"]["facts"] if row["id"] == "currentPrice")
        previous_price["value"] = 10500
        self.assertIn("10,500원 → 11,000원", renderer.render(job))
        job.context["transportDelivery"] = {"message": first, "relationChangeEvidence": deepcopy(context["relationChangeEvidence"])}
        self.assertEqual(first, renderer.render(job), "partial delivery must keep its exact original body")

    def test_complete_flow_shows_reported_buy_sell_and_zero_but_never_estimates_amount(self):
        from digital_twin.modules.notifications.domain.observation_market_snapshot import investor_rows
        context = readable_fixture()
        relation = context["ontologyRelationContext"]
        facts = relation["facts"]
        source = facts["marketSignalCoverage"]["investor"]
        source["participantStatus"]["individual"] = "available"
        source["observedFields"] += ["foreignBuyVolume", "foreignSellVolume", "individualNetVolume", "institutionNetAmount"]
        facts.update(foreignBuyVolume=200, foreignSellVolume=700, institutionNetAmount=0)
        rows = investor_rows(relation_change_evidence(context)["current"])
        self.assertIn("외국인: 순매도 500주 · 매수 200주 · 매도 700주", rows)
        self.assertTrue(any("개인: 순매수·매도 차이 0주" in row for row in rows))
        self.assertFalse(any("5,500,000" in row for row in rows))
        self.assertTrue(any("순매수 금액 0원" in row for row in rows))
        source["observedFields"] = []
        self.assertTrue(all("500주" not in row for row in investor_rows(relation_change_evidence(context)["current"])))
        facts["marketSignalCoverage"]["price"].pop("sourceAsOf")
        relation["sourceSnapshot"] = {"generatedAt": "2026-10-02T02:00:00Z"}
        snapshot = relation_change_evidence(context)["current"]
        self.assertEqual("", snapshot["observedAt"], "capture time cannot masquerade as the quote's source clock")
        self.assertEqual("2026-10-02T02:00:00Z", snapshot["capturedAt"])

    def test_small_measurements_and_unsupported_markets_do_not_look_like_zero_flow(self):
        from digital_twin.modules.notifications.domain.observation_market_snapshot import market_snapshot_sections, decimal
        self.assertEqual("0.004", decimal(.004))
        self.assertEqual("-0.004", decimal(-.004))
        snapshot = {"market": "CRYPTO", "facts": [{"id": "quantity", "value": .005}, {"id": "currentPrice", "value": 100}]}
        sections = {key: rows for key, _, rows in market_snapshot_sections(snapshot)}
        self.assertEqual(["보유 0.005개"], sections["holding"])
        self.assertIn("미지원", sections["investor-flow"][0])
        snapshot["marketSignalCoverage"] = {"investor": {"status": "unsupported", "observedFields": []}}
        sections = {key: rows for key, _, rows in market_snapshot_sections(snapshot)}
        self.assertIn("미지원", sections["investor-flow"][0])


if __name__ == "__main__":
    unittest.main()
