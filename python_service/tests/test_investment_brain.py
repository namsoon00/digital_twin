import unittest

from digital_twin.application.investment_brain_service import InvestmentBrainService
from digital_twin.domain.investment_brain import (
    DecisionEpisode,
    InvestmentQuestion,
    hypothesis_set_from_relation_context,
)
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_ai_gate_validation import (
    build_notification_ai_gate_prompt,
    validated_response_from_payload,
)
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_tbox import CLASS_DEFS, RELATION_DEFS
from digital_twin.domain.portfolio_ontology_cognitive_concepts import add_investment_brain_concepts
from digital_twin.infrastructure.mysql_investment_decision_episodes import due_outcome_horizon_minutes


def relation_context():
    return {
        "subject": {"symbol": "005930", "name": "삼성전자", "market": "KR", "sector": "반도체"},
        "facts": {
            "symbol": "005930",
            "name": "삼성전자",
            "source": "holding",
            "isHolding": True,
            "currentPrice": 70000,
            "profitLossRate": -8,
            "observedAt": "2026-07-19T01:00:00Z",
            "missingData": ["공시 본문"],
        },
        "activeRules": [
            {"ruleId": "risk-rule", "strengthScore": 82, "scoreBreakdown": {"riskPressure": 82}},
            {"ruleId": "support-rule", "strengthScore": 68, "scoreBreakdown": {"supportEvidence": 68}},
        ],
        "signalConflicts": {
            "hasConflict": True,
            "riskPressure": 82,
            "supportEvidence": 68,
        },
        "missingData": ["공시 본문"],
        "inferenceGenerationId": "generation-1",
        "inferenceGenerationAt": "2026-07-19T01:00:00Z",
        "graphStore": "typedb",
        "graphStoreInference": {
            "relations": [
                {
                    "id": "relation-risk",
                    "source": "stock:005930",
                    "target": "risk:trend",
                    "type": "HAS_INFERRED_RISK",
                    "ruleId": "risk-rule",
                    "polarity": "risk",
                    "strength": 82,
                },
                {
                    "id": "relation-support",
                    "source": "stock:005930",
                    "target": "support:flow",
                    "type": "HAS_INFERRED_SUPPORT",
                    "ruleId": "support-rule",
                    "polarity": "support",
                    "strength": 68,
                },
            ],
            "traces": [],
        },
    }


class FakeMonitorStore:
    def load_previous(self):
        return {
            "account-1": {
                "accountId": "account-1",
                "accountLabel": "테스트 계좌",
                "generatedAt": "2026-07-19T01:00:00Z",
                "portfolio": {"total": 1000000, "invested": 700000, "cash": 300000, "markets": [], "sectors": [], "concentration": 70},
                "positions": {
                    "005930": {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "market": "KR",
                        "currency": "KRW",
                        "current_price": 70000,
                        "average_price": 76000,
                        "profit_loss_rate": -8,
                        "quantity": 10,
                        "source": "holding",
                    }
                },
                "watchlist": {},
                "decisions": {},
                "externalSignals": {},
            }
        }


class FakeOntologyRepository:
    def inferencebox_snapshot(self, symbols=None, limit=80):
        return {
            "status": "ok",
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "nativeTypeDbReasoningUsed": True,
            "inferenceGenerationId": "generation-1",
            "inferenceGenerationAt": "2026-07-19T01:00:00Z",
            "relations": relation_context()["graphStoreInference"]["relations"],
            "traces": [],
        }


class FakeReviewer:
    def review(self, context):
        hypotheses = context["ontologyRelationContext"]["hypothesisSet"]["hypotheses"]
        return NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            confidence=72,
            summary="위험과 지지 근거가 충돌해 현재 수량을 유지하며 다음 관계를 확인합니다.",
            opinion="다음 TypeDB 추론 세대에서 위험 관계가 유지되는지 확인합니다.",
            hypotheses=[
                {
                    "hypothesisId": item["hypothesisId"],
                    "claim": item["claim"],
                    "stance": item["stance"],
                    "confidence": item["priorConfidence"],
                    "verdict": "unresolved",
                }
                for item in hypotheses
            ],
            selected_hypothesis_id=hypotheses[2]["hypothesisId"],
            unresolved_questions=["다음 추론 세대에서도 위험 관계가 유지되는가?"],
            epistemic_summary="위험과 지지 근거가 모두 남아 있어 잠정 판단입니다.",
            reference_date="2026-07-19T01:00:00Z",
            source="test-ai",
        )


class FakeDecisionEpisodeStore:
    def __init__(self):
        self.saved = []
        self.observations = []

    def record_observation(self, account_id, symbol, facts, observed_at=""):
        self.observations.append((account_id, symbol, facts, observed_at))
        return []

    def save(self, episode):
        self.saved.append(episode)
        return episode

    def list(self, account_id="", symbol="", limit=50):
        return list(self.saved)[:limit]


class InvestmentBrainTest(unittest.TestCase):
    def test_relation_context_builds_three_competing_hypotheses_with_graph_evidence(self):
        payload = hypothesis_set_from_relation_context(relation_context())
        hypotheses = payload["hypothesisSet"]["hypotheses"]
        self.assertEqual(3, len(hypotheses))
        self.assertEqual({"risk", "support", "uncertain"}, {item["stance"] for item in hypotheses})
        risk = next(item for item in hypotheses if item["stance"] == "risk")
        support = next(item for item in hypotheses if item["stance"] == "support")
        self.assertIn("relation-risk", risk["supportingEvidenceIds"])
        self.assertIn("relation-support", support["supportingEvidenceIds"])
        self.assertEqual(82.0, risk["priorConfidence"])
        self.assertEqual(68.0, support["priorConfidence"])
        self.assertTrue(payload["researchPlan"]["tasks"])
        self.assertTrue(payload["selfQuestions"])

    def test_ai_gate_requires_and_preserves_hypothesis_comparison(self):
        context = {
            "messageType": "investmentInsight",
            "displayTarget": "삼성전자",
            "ontologyRelationContext": relation_context(),
        }
        brain = hypothesis_set_from_relation_context(context["ontologyRelationContext"])
        context["ontologyRelationContext"].update({
            "investmentBrain": brain,
            "hypothesisSet": brain["hypothesisSet"],
            "researchPlan": brain["researchPlan"],
        })
        hypotheses = brain["hypothesisSet"]["hypotheses"]
        selected = hypotheses[0]["hypothesisId"]
        payload = {
            "action": "TRIM",
            "confidence": 75,
            "summary": "위험 가설이 우세합니다.",
            "opinion": "일부 축소를 검토합니다.",
            "evidence": ["relation-risk"],
            "counterEvidence": ["relation-support"],
            "hypotheses": [
                {
                    "hypothesisId": item["hypothesisId"],
                    "claim": item["claim"],
                    "stance": item["stance"],
                    "confidence": item["priorConfidence"],
                    "supportingEvidenceIds": item["supportingEvidenceIds"],
                    "counterEvidenceIds": item["counterEvidenceIds"],
                    "verdict": "supported" if item["hypothesisId"] == selected else "weakened",
                    "reasoning": "TypeDB 근거를 비교했습니다.",
                }
                for item in hypotheses
            ],
            "selectedHypothesisId": selected,
            "unresolvedQuestions": ["공시 본문이 결론을 바꾸는가?"],
            "epistemicSummary": "위험 가설이 잠정 우세하지만 공시 본문이 비어 있습니다.",
        }
        response = validated_response_from_payload(context, payload)
        self.assertEqual(3, len(response.hypotheses))
        self.assertEqual(selected, response.selected_hypothesis_id)
        self.assertIn("공시 본문", response.unresolved_questions[0])
        prompt = build_notification_ai_gate_prompt(context)
        self.assertIn("경쟁 가설", prompt)
        self.assertIn("selectedHypothesisId", prompt)
        context["ontologyRelationContext"]["facts"]["allAvailableData"] = "GRAPH_RAG_DUPLICATE_SENTINEL" * 2000
        compact_prompt = build_notification_ai_gate_prompt(context)
        self.assertNotIn("GRAPH_RAG_DUPLICATE_SENTINEL", compact_prompt)
        self.assertLess(len(compact_prompt), 250000)

    def test_decision_episode_round_trip_and_abox_projection(self):
        brain = hypothesis_set_from_relation_context(relation_context())
        question = InvestmentQuestion.create("삼성전자를 보유해야 하나?", "005930", "삼성전자", "account-1")
        episode = DecisionEpisode.from_dict({
            "episodeId": "episode-1",
            "accountId": "account-1",
            "symbol": "005930",
            "subjectName": "삼성전자",
            "question": question.to_dict(),
            "hypothesisSet": brain["hypothesisSet"],
            "action": "HOLD",
            "confidence": 70,
            "selectedHypothesisId": brain["hypothesisSet"]["hypotheses"][2]["hypothesisId"],
            "inferenceGenerationId": "generation-1",
            "unresolvedQuestions": brain["selfQuestions"],
        })
        restored = DecisionEpisode.from_dict(episode.to_dict())
        self.assertEqual("episode-1", restored.episode_id)
        self.assertEqual(3, len(restored.hypothesis_set.hypotheses))
        graph = PortfolioOntology("account-1")
        add_investment_brain_concepts(graph, "account-1", [restored.to_dict()])
        classes = {item.properties.get("tboxClass") for item in graph.entities}
        relation_types = {item.relation_type for item in graph.relations}
        self.assertIn("DecisionEpisode", classes)
        self.assertIn("CompetingHypothesis", classes)
        self.assertIn("SELECTS_HYPOTHESIS", relation_types)
        self.assertIn("COMPETES_WITH_HYPOTHESIS", relation_types)

    def test_question_service_uses_typedb_and_persists_episode(self):
        episode_store = FakeDecisionEpisodeStore()
        service = InvestmentBrainService(
            FakeMonitorStore(),
            FakeOntologyRepository(),
            FakeReviewer(),
            episode_store,
            settings={},
        )
        result = service.ask("삼성전자를 계속 보유해야 할까?", account_id="account-1")
        self.assertEqual("answered", result["status"])
        self.assertEqual("ontology-investment-brain", result["engine"])
        self.assertEqual(3, len(result["hypothesisSet"]["hypotheses"]))
        self.assertEqual(1, len(episode_store.saved))
        self.assertEqual("generation-1", result["inferenceGenerationId"])

    def test_tbox_defines_cognitive_objects_and_relations(self):
        class_names = {item.name for item in CLASS_DEFS}
        relation_names = {item.name for item in RELATION_DEFS}
        for name in ["InvestmentQuestion", "HypothesisSet", "CompetingHypothesis", "ObservedOutcome", "LearningProposal"]:
            self.assertIn(name, class_names)
        for name in ["ASKS_ABOUT", "COMPETES_WITH_HYPOTHESIS", "SELECTS_HYPOTHESIS", "RESULTED_IN_OUTCOME", "LEARNED_FROM"]:
            self.assertIn(name, relation_names)

    def test_outcome_feedback_is_recorded_once_per_configured_horizon(self):
        brain = hypothesis_set_from_relation_context(relation_context())
        episode = DecisionEpisode.from_dict({
            "episodeId": "episode-horizon",
            "accountId": "account-1",
            "symbol": "005930",
            "subjectName": "삼성전자",
            "question": brain["question"],
            "hypothesisSet": brain["hypothesisSet"],
            "action": "HOLD",
            "confidence": 70,
            "decidedAt": "2026-07-19T00:00:00Z",
        })
        self.assertEqual(60, due_outcome_horizon_minutes(episode, "2026-07-19T01:05:00Z", "60,1440"))
        episode.outcomes.append(type("Outcome", (), {"payload": {"horizonMinutes": 60}})())
        self.assertEqual(0, due_outcome_horizon_minutes(episode, "2026-07-19T02:00:00Z", "60,1440"))
        self.assertEqual(1440, due_outcome_horizon_minutes(episode, "2026-07-20T01:00:00Z", "60,1440"))


if __name__ == "__main__":
    unittest.main()
