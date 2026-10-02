"""Source-bound hypothesis explanations through the real customer formatter."""

from copy import deepcopy
import unittest

from test_typedb_independent_observation import readable_fixture
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
from digital_twin.modules.notifications.domain.notification_templates import NotificationTemplate, render_notification
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
from digital_twin.modules.read_models.contracts import customer_safe_text


def temporal_fixture():
    context = readable_fixture()
    relation = context["ontologyRelationContext"]
    relation["subject"]["market"] = "US"
    relation["facts"].update(currency="USD", market="US", currentPrice=156.72, priceChangeRate=-2.36,
                             averagePrice=143.815766, quantity=202, profitLossRate=8.97275, profitLoss=2606.6552)
    trace = relation["graphStoreInference"]["traces"][0]
    trace.update(evidenceUsableForJudgement=False, freshnessGateReason="원천 데이터가 허용 시간보다 오래되었습니다.")
    proof = trace["matchedConditions"][0]["matchedTargetProperties"]
    identity = "stock:MSTR|HAS_TEMPORAL_WINDOW|temporal-window:MSTR:3D"
    proof.update(sourceFeatureSnapshotId="temporal:frozen", knowledgeCutoffAt="2026-10-02T01:10:00Z",
                 modelEvidenceIds=[identity, "stock:MSTR#priceChangeRate", "stock:OTHER#ma60Distance"],
                 sourceTemporalWindows=[{
                     "evidenceId": identity, "sourceFeatureSnapshotId": "temporal:frozen", "symbol": "MSTR",
                     "knowledgeCutoffAt": "2026-10-02T01:10:00Z", "windowKey": "3D", "sampleCount": 3,
                     "startPrice": 153.09, "currentPrice": 156.72, "priceChangePct": 2.37,
                     "drawdownFromPeakPct": -2.36, "reboundFromTroughPct": 2.37,
                     "recentPriceChangePct": -2.36, "priceVelocityChangePct": -7.2,
                     "hasSufficientHistory": True, "tradeStrengthEnd": 0, "unrelatedPrivateField": "do-not-copy",
                 }])
    hypothesis = relation["hypothesisSet"]["hypotheses"][0]
    hypothesis.update(expectedOutcome="반등이 유지되지 못하고 이전 위험 경로로 복귀", evidenceState="blocked",
                      falsificationContract="회복 가격대와 거래 확인이 다음 관측에서도 유지")
    return context, proof


def freeze_and_render(context):
    context["relationChangeEvidence"] = relation_change_evidence(context)
    context["telegramMessage"] = typedb_observation_telegram_message(context)
    return render_notification(NotificationTemplate.default("investmentInsight"), context)


class RelationObservationNarrativeTests(unittest.TestCase):
    def test_native_window_numbers_holding_return_and_next_check_survive_full_transport(self):
        context, proof = temporal_fixture()
        proof["modelEvidenceIds"].extend(["stock:MSTR#foreignNetVolume", "stock:MSTR#institutionNetVolume", "stock:MSTR#individualNetVolume"])
        context["ontologyRelationContext"]["facts"]["marketSignalCoverage"]["investor"]["observedFields"].append("individualNetVolume")
        text = freeze_and_render(context)
        for value in ("종합해서 보면", "$156.72(전일 대비 -2.36%)", "내 보유 평가 수익률은 +8.97%",
                      "평균 매입가 $143.82", "평가손익 +$2,606.66", "반등이 이어지지 못하고 가격이 다시 약해질 가능성",
                      "$153.09 → $156.72", "구간 등락 +2.37%", "구간 고점 대비 -2.36%",
                      "후반 등락률 변화 -7.2%p", "10/02 10:10 KST", "회복한 가격대와 이를 뒷받침하는 거래가 유지되는지",
                      "현재 판단 근거로 사용 보류", "원천 데이터가 허용 시간보다 오래되었습니다"):
            self.assertIn(value, text)
        hypothesis_text = " ".join(next(s for s in context["customerInvestmentDocument"]["sections"] if s["key"] == "hypotheses")["rows"])
        self.assertIn("외국인 순매도 500주", hypothesis_text)
        self.assertIn("기관 순매수·매도 차이 0주", hypothesis_text)
        self.assertIn("10/02 10:00 KST", hypothesis_text)
        self.assertNotIn("개인 순매수 0주", hypothesis_text, "unpublished defaults cannot become hypothesis evidence")
        self.assertNotIn("• 이 가설은", text, "related explanations should read as paragraphs")
        self.assertNotIn("체결강도 0", text, "source defaults are not confirmed zero measurements")
        packet = deepcopy(context["relationChangeEvidence"])
        condition = next(r for r in packet["current"]["rules"] if r["id"] == "rule:linked")["conditions"][0]
        self.assertNotIn("ma60Distance", condition["measuredFactIds"], "other-stock model inputs cannot bind to this stock's facts")
        self.assertNotIn("unrelatedPrivateField", condition["sourceTemporalWindows"][0])
        proof["sourceTemporalWindows"][0]["startPrice"] = 9999
        self.assertEqual(packet, context["relationChangeEvidence"])
        self.assertIn("$153.09 → $156.72", typedb_observation_telegram_message(context))
        self.assertEqual("passed", context["customerInvestmentDocumentQuality"]["status"])

    def test_wrong_subject_version_link_or_future_window_cannot_explain_this_hypothesis(self):
        for field, value, reason in (
            ("symbol", "OTHER", "이 종목의 규칙 근거"),
            ("sourceFeatureSnapshotId", "different", "원본 자료 버전"),
            ("evidenceId", "stock:MSTR|HAS_TEMPORAL_WINDOW|temporal-window:MSTR:5D", "이 종목의 규칙 근거"),
            ("knowledgeCutoffAt", "2026-10-03T01:10:00Z", "분석 기준 시각 이후"),
            ("knowledgeCutoffAt", "", "기준 시각을 확인"),
        ):
            with self.subTest(field=field, value=value):
                context, proof = temporal_fixture()
                proof["sourceTemporalWindows"][0][field] = value
                text = freeze_and_render(context)
                self.assertIn(reason, text)
                self.assertNotIn("$153.09", text)
                self.assertIn("반등이 이어지지 못하고", text, "a proof limitation must not block the entire message")

    def test_missing_comparison_history_and_watchlist_return_are_not_fabricated(self):
        context, proof = temporal_fixture()
        proof["sourceTemporalWindows"][0]["hasSufficientHistory"] = False
        proof["modelEvidenceIds"].append("stock:MSTR|HAS_RELATIVE_PERFORMANCE|relative-performance-observation:MSTR:BTC")
        facts = context["ontologyRelationContext"]["facts"]
        facts.update(quantity=0, averagePrice=0, profitLoss=0, profitLossRate=0)
        text = freeze_and_render(context)
        self.assertIn("필요한 기간", text)
        self.assertIn("부족한 참고 수치", text)
        self.assertIn("비트코인(BTC)", text)
        self.assertIn("비교 강도를 수치로 설명할 수 없습니다", text)
        self.assertNotIn("평가 수익률", text)
        self.assertNotIn("평가손익", text)
        # Older frozen packets cannot borrow newer windows from the raw context.
        packet = context["relationChangeEvidence"]
        for rule in packet["current"]["rules"]:
            for condition in rule["conditions"]:
                condition.pop("sourceTemporalWindows", None)
        old = typedb_observation_telegram_message(context)
        self.assertNotIn("$153.09", old)
        self.assertIn("전일 대비 -2.36%", old)

    def test_relative_outcomes_use_percentage_points_and_repeated_prefix_keeps_real_content(self):
        context, _ = temporal_fixture()
        hypothesis = context["ontologyRelationContext"]["hypothesisSet"]["hypotheses"][0]
        hypothesis["claimContract"]["outcomeContract"] = {
            "outcomeHorizonMinutes": [1440, 10080], "criteria": [
                {"role": "result", "metric": "excessReturnPct", "operator": "<=", "threshold": -.75,
                 "horizonMinutes": 0, "benchmarkSymbol": "$MARKET"},
                {"role": "invalidation", "metric": "excessReturnPct", "operator": ">", "threshold": .75,
                 "horizonMinutes": 0, "benchmarkSymbol": "SPY"},
            ]}
        text = freeze_and_render(context)
        self.assertIn("1일 · 7일", text)
        self.assertIn("수익률이 0.75%p 이상 낮음", text)
        self.assertIn("수익률이 0.75%p 초과 높음", text)
        self.assertIn("사전에 지정한 시장 기준", text)
        self.assertIn("비교 대상: SPY", text)
        self.assertNotIn("0분 뒤", text)
        self.assertIn("에서도", customer_safe_text("다음 확인: 다음 확인에서도 회복한 가격대가 유지되는지"))
        self.assertEqual("다음 확인", customer_safe_text("다음 확인: 다음 확인"))


if __name__ == "__main__":
    unittest.main()
