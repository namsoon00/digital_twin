import unittest

from digital_twin.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
from digital_twin.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
from digital_twin.domain.events import hypothesis_research_completed_event, ontology_reasoning_requested_event
from digital_twin.domain.hypothesis_research_planning import apply_ai_research_guidance
from digital_twin.domain.investment_brain import InvestmentQuestion, utc_now_iso
from digital_twin.domain.investment_evidence_governance import ResearchRun, hypothesis_research_brief_from_brain
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence


def brain_context():
    return {
        "reasoningGeneration": {
            "inferenceGenerationId": "inference:current",
            "sourceAboxSnapshotId": "abox:current",
            "worldId": "portfolio-world:account-1",
            "generationAligned": True,
            "observedAt": "2026-07-22T01:00:00Z",
        },
        "hypothesisSet": {
            "hypothesisSetId": "hypothesis-set:current",
            "hypotheses": [
                {
                    "hypothesisId": "hypothesis:risk",
                    "templateId": "hypothesis-template:risk",
                    "templateLabel": "하락 위험 경로",
                    "claim": "가격 약화와 매도 흐름이 함께 이어질 수 있다.",
                    "stance": "risk",
                    "evidenceState": "contested",
                    "verificationStatus": "requires-research",
                    "supportingEvidenceIds": ["relation:risk"],
                    "counterEvidenceIds": ["relation:support"],
                    "requiredEvidenceTypes": ["official-filing", "news-full-text"],
                    "invalidationConditions": ["공식 공시와 가격 반응이 위험 경로를 부정"],
                },
                {
                    "hypothesisId": "hypothesis:support",
                    "templateId": "hypothesis-template:support",
                    "templateLabel": "회복 지지 경로",
                    "claim": "공식 근거와 가격 회복이 함께 확인될 수 있다.",
                    "stance": "support",
                    "evidenceState": "contested",
                    "verificationStatus": "counterfactual-challenge",
                    "supportingEvidenceIds": ["relation:support"],
                    "counterEvidenceIds": ["relation:risk"],
                    "requiredEvidenceTypes": ["official-filing", "market-data"],
                    "invalidationConditions": ["거래량을 동반한 하락이 이어짐"],
                },
            ],
        },
        "researchPlan": {
            "planId": "research-plan:current",
            "tasks": [
                {
                    "taskId": "task:risk",
                    "question": "위험 경로를 확인할 최신 공식 근거는 무엇인가?",
                    "purpose": "위험 가설 검증",
                    "relatedHypothesisIds": ["hypothesis:risk"],
                    "requiredEvidenceTypes": ["official-filing", "news-full-text"],
                    "sourceTypes": ["official-filing", "news-full-text"],
                    "maxAgeMinutes": 180,
                    "decisionRelevance": "direct",
                    "status": "ready",
                },
                {
                    "taskId": "task:support",
                    "question": "회복 경로를 확인할 최신 가격·공시 근거는 무엇인가?",
                    "purpose": "지지 가설 검증",
                    "relatedHypothesisIds": ["hypothesis:support"],
                    "requiredEvidenceTypes": ["official-filing", "market-data"],
                    "sourceTypes": ["official-filing", "market-data"],
                    "maxAgeMinutes": 180,
                    "decisionRelevance": "direct",
                    "status": "ready",
                },
            ],
            "unresolvedQuestions": ["상반된 두 경로 중 어느 쪽이 더 직접적인가?"],
        },
        "epistemicState": {"status": "contested"},
        "missingData": ["공시 본문"],
    }


class FixedPlanner:
    def __init__(self, guidance):
        self.guidance = guidance
        self.contexts = []

    def plan(self, context):
        self.contexts.append(context)
        return self.guidance


class MemoryEvidenceStore:
    def __init__(self):
        self.saved = []

    def latest(self, symbol="", limit=50):
        return []

    def upsert_many(self, items):
        self.saved.extend(items)
        self.last_changed_items = list(items)
        return len(items)


class RecordingGateway:
    def __init__(self, evidence):
        self.evidence = evidence
        self.source_types = []

    def collect_for_target(self, target, source_types=None):
        self.source_types = list(source_types or [])
        return [self.evidence], [{"provider": "test", "status": "ok"}]


class HypothesisResearchPlanningTests(unittest.TestCase):
    def valid_guidance(self):
        return {
            "focusHypothesisIds": ["hypothesis:risk"],
            "tasks": [{
                "hypothesisId": "hypothesis:risk",
                "counterHypothesisIds": ["hypothesis:support"],
                "question": "공식 공시에서 위험 경로를 직접 부정하거나 확인하는 사실이 있는가?",
                "purpose": "위험 가설과 회복 가설을 같은 원문 기준으로 비교합니다.",
                "requiredEvidenceTypes": ["official-filing"],
                "sourceTypes": ["official-filing"],
                "maxAgeMinutes": 9999,
                "decisionRelevance": "direct",
            }],
            "unresolvedQuestions": ["공시 후 실제 가격 반응이 위험 경로와 일치하는가?"],
        }

    def test_brief_preserves_counter_hypotheses_and_generation(self):
        brief = hypothesis_research_brief_from_brain(brain_context())

        risk = next(item for item in brief.candidate_hypotheses if item["hypothesisId"] == "hypothesis:risk")
        self.assertEqual("hypothesis:support", risk["counterHypothesisIds"][0])
        self.assertTrue(brief.reasoning_generation.complete())
        self.assertIn("공시 본문", brief.evidence_gaps)

        run = ResearchRun(
            run_id="run-1",
            question_id="question-1",
            account_id="account-1",
            symbol="005930",
            status="queued",
            task_ids=[],
            source_types=[],
            hypothesis_research_brief=brief,
        )
        restored = ResearchRun.from_dict(run.to_dict())
        self.assertEqual("hypothesis-set:current", restored.hypothesis_research_brief.hypothesis_set_id)
        self.assertEqual("inference:current", restored.hypothesis_research_brief.reasoning_generation.inference_generation_id)

    def test_ai_guidance_can_only_add_policy_bounded_tasks(self):
        context = brain_context()
        brief = hypothesis_research_brief_from_brain(context)

        planned, audit = apply_ai_research_guidance(context["researchPlan"], brief, self.valid_guidance())

        self.assertEqual("ai-augmented", audit["status"])
        self.assertEqual(3, len(planned["tasks"]))
        ai_task = planned["tasks"][-1]
        self.assertEqual(["official-filing"], ai_task["sourceTypes"])
        self.assertEqual(["official-filing"], ai_task["requiredEvidenceTypes"])
        self.assertEqual(180, ai_task["maxAgeMinutes"])
        self.assertEqual("ai-hypothesis-research-planner", ai_task["planningSource"])
        self.assertEqual(["hypothesis:support"], ai_task["counterHypothesisIds"])
        self.assertIn("공시 후 실제 가격 반응", planned["unresolvedQuestions"][-1])

    def test_ai_guidance_rejects_unknown_hypotheses_and_unapproved_sources(self):
        context = brain_context()
        brief = hypothesis_research_brief_from_brain(context)
        guidance = {
            "focusHypothesisIds": ["hypothesis:invented"],
            "tasks": [
                {
                    "hypothesisId": "hypothesis:invented",
                    "question": "없는 가설을 조사한다.",
                    "sourceTypes": ["unapproved-source"],
                    "requiredEvidenceTypes": ["invented-evidence"],
                },
                {
                    "hypothesisId": "hypothesis:risk",
                    "question": "정책 밖 출처를 조사한다.",
                    "sourceTypes": ["unapproved-source"],
                    "requiredEvidenceTypes": ["official-filing"],
                },
            ],
        }

        planned, audit = apply_ai_research_guidance(context["researchPlan"], brief, guidance)

        self.assertEqual("no-valid-guidance", audit["status"])
        self.assertEqual(2, len(planned["tasks"]))
        self.assertTrue(audit["rejectedGuidance"])

    def test_orchestrator_records_ai_planning_audit_without_granting_decision_authority(self):
        planner = HypothesisResearchPlanningService(FixedPlanner(self.valid_guidance()), settings={})
        observed_at = utc_now_iso()
        evidence = ResearchEvidence(
            evidence_id="evidence:official",
            symbol="005930",
            kind="disclosure",
            source="OpenDART",
            title="주요사항보고서",
            observed_at=observed_at,
            published_at=observed_at,
            raw_payload={
                "relationScope": "direct",
                "sourceTrustState": "trusted",
                "dataState": "sufficient",
                "validationState": "ready",
            },
        )
        store = MemoryEvidenceStore()
        gateway = RecordingGateway(evidence)
        service = InvestmentResearchOrchestrationService(
            evidence_repository=store,
            research_gateway=gateway,
            hypothesis_research_planner=planner,
            settings={
                "investmentBrainResearchCooldownMinutes": 0,
                "investmentBrainResearchMinimumVerifiedCount": 1,
            },
        )
        question = InvestmentQuestion.create("삼성전자 위험 가설을 검증해줘", "005930", "삼성전자", "account-1")
        target = NewsCollectionTarget("005930", "삼성전자", "KR", "KRW", "반도체")

        run = service.run(question, target, brain_context(), account_id="account-1", force=True)

        self.assertEqual("evidence-collected", run.status)
        self.assertEqual("ai-augmented", run.hypothesis_research_brief.planning_status)
        self.assertEqual("research-only", run.hypothesis_research_brief.planning_audit["decisionEligibility"])
        self.assertIn("official-filing", gateway.source_types)
        self.assertNotIn("unapproved-source", gateway.source_types)
        self.assertEqual(1, len(store.saved))

    def test_cached_research_does_not_invoke_ai_planner_or_change_plan(self):
        observed_at = utc_now_iso()
        evidence = ResearchEvidence(
            evidence_id="evidence:cached",
            symbol="005930",
            kind="disclosure",
            source="OpenDART",
            title="기존 공시",
            observed_at=observed_at,
            published_at=observed_at,
            raw_payload={
                "relationScope": "direct",
                "sourceTrustState": "trusted",
                "dataState": "sufficient",
                "validationState": "ready",
            },
        )
        advisor = FixedPlanner(self.valid_guidance())
        planner = HypothesisResearchPlanningService(advisor, settings={})
        store = MemoryEvidenceStore()
        store.latest = lambda symbol="", limit=50: [evidence]
        service = InvestmentResearchOrchestrationService(
            evidence_repository=store,
            research_gateway=RecordingGateway(evidence),
            hypothesis_research_planner=planner,
            settings={"investmentBrainResearchMinimumVerifiedCount": 1},
        )
        question = InvestmentQuestion.create("삼성전자 위험 가설을 검증해줘", "005930", "삼성전자", "account-1")
        target = NewsCollectionTarget("005930", "삼성전자", "KR", "KRW", "반도체")

        run = service.run(question, target, brain_context(), account_id="account-1")

        self.assertEqual("cache-satisfied", run.status)
        self.assertEqual([], advisor.contexts)
        self.assertEqual("rule-derived", run.hypothesis_research_brief.planning_status)

    def test_planning_audit_is_carried_to_reasoning_event(self):
        brief = hypothesis_research_brief_from_brain(brain_context())
        completed = hypothesis_research_completed_event({
            "runId": "run-1",
            "questionId": "question-1",
            "accountId": "account-1",
            "symbol": "005930",
            "changedEvidenceCount": 1,
            "changedEvidenceIds": ["evidence:1"],
            "hypothesisResearchBrief": brief.to_dict(),
        })
        requested = ontology_reasoning_requested_event(completed, "hypothesis-research-update", symbols=["005930"])

        self.assertEqual("hypothesis-set:current", requested.payload["hypothesisResearchBrief"]["hypothesisSetId"])
        self.assertEqual("research-only", requested.payload["hypothesisResearchBrief"]["decisionEligibility"])


if __name__ == "__main__":
    unittest.main()
