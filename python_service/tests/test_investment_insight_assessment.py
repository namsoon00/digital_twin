import unittest

from digital_twin.domain.investment_insight_assessment import (
    compact_previous_investment_insight_episode,
    investment_insight_assessment,
    investment_insight_transition,
)


def grounded_assessment(
    direction="positive",
    thesis="단기 회복 신호가 우세합니다.",
    *,
    include_causal_chain=True,
    validation_state="conditional",
    counter_evidence_status="none-found",
    invalidation_condition="20일선 이탈과 순매도 전환이 함께 나타나면 무효화합니다.",
    follow_up_conditions=(),
):
    return investment_insight_assessment(
        {
            "insightAssessment": {
                "direction": direction,
                "horizon": "short-term",
                "conviction": "strong",
            },
        },
        hypotheses=[{
            "hypothesisId": "hypothesis:price-flow-recovery",
            "familyId": "price-flow-recovery",
            "verdict": "supported",
            "executionEligible": False,
            "candidateAction": "ADD",
        }],
        selected_hypothesis_id="hypothesis:price-flow-recovery",
        narrative_claims=[
            {
                "claimId": "claim:view",
                "section": "view",
                "text": thesis,
                "evidenceIds": ["fact:price", "trace:recovery"],
            },
            {
                "claimId": "claim:mechanism",
                "section": "mechanism",
                "text": "가격 회복과 순매수가 함께 이어져 단기 추세를 강화합니다.",
                "evidenceIds": ["fact:price", "fact:flow"],
            },
            {
                "claimId": "claim:implication",
                "section": "implication",
                "text": "보유자는 회복 지속 여부를 기준으로 비중 확대 시점을 구분할 수 있습니다.",
                "evidenceIds": ["fact:flow", "trace:recovery"],
            },
            {
                "claimId": "claim:next",
                "section": "next-condition",
                "text": "20일선과 순매수가 다음 거래일에도 유지되는지 확인합니다.",
                "evidenceIds": ["fact:price", "fact:flow"],
            },
        ],
        causal_chain=[{
            "driver": "가격 회복",
            "channel": "flow",
            "expectedEffect": "순매수와 함께 단기 추세 강화",
            "evidenceIds": ["fact:price", "fact:flow"],
            "status": "supported",
        }] if include_causal_chain else [],
        comparison_state="research-reviewed",
        validation_state=validation_state,
        data_state="sufficient",
        decision_readiness="conditional",
        counter_evidence_status=counter_evidence_status,
        invalidation_condition=invalidation_condition,
        follow_up_conditions=follow_up_conditions,
    )


class InvestmentInsightAssessmentTests(unittest.TestCase):
    def test_publishable_insight_does_not_require_execution_authority(self):
        assessment = grounded_assessment(
            include_causal_chain=False,
            validation_state="blocked",
        )

        self.assertTrue(assessment["publishable"])
        self.assertFalse(assessment["executionEligible"])
        self.assertEqual("positive", assessment["direction"])
        self.assertEqual("tentative", assessment["conviction"])
        self.assertEqual("price-flow-recovery", assessment["thesisKey"])
        self.assertEqual([], assessment["validationReasons"])

    def test_missing_causal_implication_blocks_publication(self):
        assessment = investment_insight_assessment(
            {"insightAssessment": {"direction": "positive"}},
            hypotheses=[{
                "hypothesisId": "hypothesis:recovery",
                "familyId": "recovery",
                "verdict": "supported",
            }],
            selected_hypothesis_id="hypothesis:recovery",
            narrative_claims=[{
                "claimId": "claim:view",
                "section": "view",
                "text": "회복 가능성이 높아졌습니다.",
                "evidenceIds": ["fact:price", "trace:recovery"],
            }],
            causal_chain=[{
                "status": "supported",
                "evidenceIds": ["fact:price", "trace:recovery"],
            }],
            comparison_state="completed",
        )

        self.assertFalse(assessment["publishable"])
        self.assertIn(
            "missing-verified-implication-claim",
            assessment["validationReasons"],
        )

    def test_counter_evidence_must_be_checked_before_publication(self):
        assessment = grounded_assessment(counter_evidence_status="not-checked")

        self.assertFalse(assessment["publishable"])
        self.assertIn(
            "counter-evidence-not-verified",
            assessment["validationReasons"],
        )

    def test_confirmed_counter_evidence_requires_a_verified_counter_claim(self):
        assessment = grounded_assessment(counter_evidence_status="confirmed")

        self.assertFalse(assessment["publishable"])
        self.assertIn(
            "missing-verified-counter-claim",
            assessment["validationReasons"],
        )

    def test_generic_invalidation_condition_blocks_publication(self):
        assessment = grounded_assessment(
            invalidation_condition=(
                "다음 데이터에서 현재 근거가 사라지거나 반대 근거가 새로 확인되면 다시 봅니다."
            ),
        )

        self.assertFalse(assessment["publishable"])
        self.assertIn(
            "missing-specific-invalidation-condition",
            assessment["validationReasons"],
        )

    def test_observable_follow_up_replaces_generic_invalidation(self):
        assessment = grounded_assessment(
            invalidation_condition="현재 근거가 사라지면 다시 봅니다.",
            follow_up_conditions=[{
                "conditionId": "follow-up:ma20-break",
                "field": "ma20Distance",
                "operator": "<",
                "threshold": 0,
                "purpose": "invalidate",
                "label": "현재가가 20일선 아래로 내려감",
                "onSatisfied": "상방 관점을 무효화합니다.",
                "observable": True,
            }],
        )

        self.assertTrue(assessment["publishable"])
        self.assertIn("20일선 아래", assessment["invalidationCondition"])
        self.assertEqual("follow-up:ma20-break", assessment["invalidationTests"][0]["conditionId"])

    def test_transition_uses_meaning_not_wording(self):
        before = grounded_assessment(thesis="단기 회복 신호가 우세합니다.")
        episode = {
            "episodeId": "episode:1",
            "subjectCaseId": "subject:1",
            "insight": {"insightAssessment": before},
        }
        after = grounded_assessment(thesis="단기 가격 회복 쪽 근거가 더 강합니다.")

        transition = investment_insight_transition(episode, after)

        self.assertFalse(transition["material"])
        self.assertEqual("unchanged-insight", transition["kind"])
        self.assertEqual(
            "episode:1",
            compact_previous_investment_insight_episode(episode)["episodeId"],
        )

    def test_direction_change_is_material(self):
        before = grounded_assessment(direction="positive")
        episode = {"episodeId": "episode:1", "insight": {"insightAssessment": before}}
        after = grounded_assessment(direction="negative")

        transition = investment_insight_transition(episode, after)

        self.assertTrue(transition["material"])
        self.assertIn("direction-changed", transition["changes"])


if __name__ == "__main__":
    unittest.main()
