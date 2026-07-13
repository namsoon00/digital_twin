import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.ontology_rulebox_governance import (
    build_rule_change_candidate_prompt,
    rule_change_candidates_from_text,
)
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository


class FakeOntologyRepository:
    def __init__(self):
        self.saved_candidates = []

    def rulebox_snapshot(self):
        return {
            "status": "ok",
            "ruleCount": 1,
            "relationTypes": ["HAS_EXTERNAL_SIGNAL", "REQUIRES_NEXT_CHECK"],
            "rules": [
                {
                    "rule_id": "graph.loss_guard.breakdown.v1",
                    "label": "손실 방어",
                    "enabled": True,
                    "conditions": [],
                    "derivations": [],
                }
            ],
            "changeCandidates": [],
        }

    def inferencebox_snapshot(self, symbols=None, limit=80):
        return {
            "status": "ok",
            "relationCount": 1,
            "relations": [
                {
                    "type": "HAS_EXTERNAL_SIGNAL",
                    "ruleId": "graph.news.direct_material_risk.v1",
                    "polarity": "risk",
                    "decisionStage": "NEWS_RISK",
                }
            ],
        }

    def save_rule_change_candidates(self, candidates, context=None):
        self.saved_candidates.extend(candidates)
        return {"status": "ok", "candidateCount": len(candidates), "savedCount": len(candidates)}


class FakeAdvisor:
    def propose(self, context):
        return rule_change_candidates_from_text(ai_candidate_json(), context)


class FakeEventReader:
    def latest_events(self, limit=20):
        return [
            DomainEvent(
                name="research_evidence.collected",
                aggregate_id="news:AAPL",
                payload={"symbols": ["AAPL"], "changedCount": 1, "materialChangedCount": 1},
            )
        ]


class FakeCursor:
    def __init__(self):
        self.payload = {"processedEventIds": []}

    def processed_event_ids(self):
        return list(self.payload.get("processedEventIds") or [])

    def load(self):
        return dict(self.payload)

    def save(self, payload):
        self.payload = dict(payload)

    def mark_processed(self, event_ids):
        self.payload["processedEventIds"] = list(event_ids or [])


class FakeMonitorRunner:
    accounts = []

    def run_once(self, force=True, symbol_filter=None):
        return []


class OntologyRuleCandidateAITests(unittest.TestCase):
    def test_ai_candidate_json_is_validated_as_disabled_rule(self):
        context = {"ruleBox": FakeOntologyRepository().rulebox_snapshot()}
        candidates = rule_change_candidates_from_text(ai_candidate_json(), context)

        self.assertEqual(1, len(candidates))
        self.assertEqual("append-disabled-rule", candidates[0]["action"])
        self.assertFalse(candidates[0]["proposedRule"]["enabled"])
        self.assertEqual("graph.ai.peer.news.context.v1", candidates[0]["proposedRule"]["rule_id"])

    def test_prompt_includes_rulebox_and_inferencebox_context(self):
        service = RuleChangeCandidateProposalService(FakeOntologyRepository(), FakeAdvisor(), FakeEventReader())
        context = service.build_context(["AAPL"], "manual")
        prompt = build_rule_change_candidate_prompt(context)

        self.assertIn("RuleChangeCandidate", prompt)
        self.assertIn("HAS_EXTERNAL_SIGNAL", prompt)
        self.assertIn("graph.loss_guard.breakdown.v1", prompt)

    def test_proposal_service_saves_ai_candidates(self):
        repository = FakeOntologyRepository()
        service = RuleChangeCandidateProposalService(repository, FakeAdvisor(), FakeEventReader())

        result = service.propose(["AAPL"], trigger="manual")

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["savedCount"])
        self.assertEqual(1, len(repository.saved_candidates))

    def test_typedb_candidate_save_persists_governance_node(self):
        class CapturingTypeDBRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")
                self.saved_graph = None

            def save_graph(self, graph):
                self.saved_graph = graph
                return {"configured": True, "saved": True, "status": "ok", "graphStore": "typedb"}

        candidates = rule_change_candidates_from_text(ai_candidate_json(), {"ruleBox": FakeOntologyRepository().rulebox_snapshot()})
        repository = CapturingTypeDBRepository()

        result = repository.save_rule_change_candidates(candidates, {"symbols": ["AAPL"]})

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["savedCount"])
        self.assertIsNotNone(repository.saved_graph)
        self.assertTrue(any(
            (entity.properties or {}).get("ontologyBox") == "RuleBoxGovernance"
            and entity.kind == "rule-change-candidate"
            and (entity.properties or {}).get("properties", {}).get("proposedRule")
            for entity in repository.saved_graph.entities
        ))

    def test_reasoning_runner_invokes_candidate_service_when_due(self):
        event = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="ontology:AAPL",
            payload={"symbols": ["AAPL"], "changedCount": 1},
        )

        class EventReader:
            def events(self, name="", aggregate_id="", limit=0):
                return [event]

        repository = FakeOntologyRepository()
        service = RuleChangeCandidateProposalService(repository, FakeAdvisor(), FakeEventReader())
        runner = OntologyReasoningRunner(
            event_reader=EventReader(),
            cursor_store=FakeCursor(),
            monitor_runner_factory=lambda: FakeMonitorRunner(),
            settings={"ontologyRuleCandidateAiEnabled": "1", "ontologyRuleCandidateAiIntervalMinutes": "5"},
            rule_candidate_service=service,
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["ruleCandidateResult"]["savedCount"])


def ai_candidate_json():
    return """
    {
      "candidates": [
        {
          "title": "피어 뉴스 관계 후보",
          "rationale": "피어 뉴스가 반복적으로 직접 종목 리스크와 함께 움직입니다.",
          "expectedEffect": "직접 뉴스가 없어도 섹터 컨텍스트를 AI가 확인합니다.",
          "risk": "섹터 뉴스 과잉 반영 가능",
          "requiresData": ["HAS_EXTERNAL_SIGNAL", "relationScope=peer"],
          "priority": 77,
          "proposedRule": {
            "rule_id": "graph.ai.peer.news.context.v1",
            "label": "AI 피어 뉴스 컨텍스트",
            "version": "ai-candidate-v1",
            "source_kind": "stock",
            "enabled": true,
            "action_group": "alertReview",
            "action_level": "watch",
            "prompt_hint": "피어 뉴스와 직접 종목 논리 연결성을 설명합니다.",
            "conditions": [
              {
                "condition_id": "peer-news",
                "kind": "relation",
                "description": "피어 범위 중요 뉴스입니다.",
                "relation_type": "HAS_EXTERNAL_SIGNAL",
                "target_kind": "research-evidence",
                "target_property_filters": {"relationScope": ["peer"], "materialityPassed": true},
                "min_weight": 0.25
              }
            ],
            "derivations": [
              {
                "relation_type": "REQUIRES_NEXT_CHECK",
                "target_kind": "next-check",
                "target_key": "{symbol}:ai-peer-news-review",
                "target_label": "{displayName} AI 피어 뉴스 점검",
                "tbox_class": "NextCheck",
                "tbox_classes": ["NextCheck", "NewsEvent"],
                "polarity": "context",
                "weight": 0.66,
                "decision_stage": "SECTOR_NEWS",
                "stage_priority": 29
              }
            ]
          }
        }
      ]
    }
    """


if __name__ == "__main__":
    unittest.main()
