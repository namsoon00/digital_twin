import unittest

from digital_twin.application.hypothesis_proposal_service import HypothesisProposalService
from digital_twin.application.investment_brain_service import InvestmentBrainService
from digital_twin.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
from digital_twin.application.notification_service import NotificationHypothesisResearchEnricher
from digital_twin.domain.investment_brain import (
    DecisionEpisode,
    InvestmentQuestion,
    hypothesis_set_from_relation_context,
)
from digital_twin.domain.investment_evidence_governance import ResearchRun, governed_evidence
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_ai_gate_validation import (
    build_notification_ai_gate_prompt,
    validated_response_from_payload,
)
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_tbox import CLASS_DEFS, RELATION_DEFS
from digital_twin.domain.portfolio_ontology_cognitive_concepts import add_investment_brain_concepts
from digital_twin.domain.portfolio_ontology_research_concepts import add_governed_claim_concepts
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.infrastructure.mysql_investment_decision_episodes import due_outcome_horizon_minutes
from digital_twin.infrastructure.investment_research_gateway import (
    CompositeInvestmentResearchGateway,
    ExistingApiResearchGateway,
)


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


class FakeEvidenceStore:
    def __init__(self, cached=None):
        self.cached = list(cached or [])
        self.saved = []

    def latest(self, symbol="", limit=50):
        return list(self.cached)[:limit]

    def upsert_many(self, items):
        self.saved.extend(items)
        return len(items)


class FakeResearchStore:
    def __init__(self):
        self.runs = []
        self.proposals = []

    def save_run(self, run):
        self.runs.append(run)
        return run

    def save_hypothesis_proposal(self, proposal):
        self.proposals.append(proposal)
        return proposal

    def list_hypothesis_proposals(self, status="", symbol="", limit=50):
        rows = [item.to_dict() for item in self.proposals]
        return rows[:limit]


class FakeResearchGateway:
    def __init__(self, items):
        self.items = list(items)
        self.calls = 0
        self.requested_source_types = []

    def collect_for_target(self, target, source_types=None):
        self.calls += 1
        self.requested_source_types = list(source_types or [])
        return list(self.items), [{"provider": "test", "status": "ok"}]


class FakeExternalSignalProvider:
    def signals_for_positions(self, positions):
        symbol = positions[0].symbol
        return {
            "fetchedAt": "2026-07-19T11:00:00Z",
            "dartDisclosures": {
                symbol: {
                    "provider": "OpenDART",
                    "reportName": "주요사항보고서",
                    "receiptNo": "202607190001",
                    "receiptDate": "20260719",
                }
            },
            "statuses": [{"source": "OpenDART", "ok": True}],
        }


class FakeHypothesisAdvisor:
    def propose(self, context):
        return [
            {
                "title": "검증 관계 확장",
                "claim": "공식 근거와 현재 관계가 함께 유지되면 새로운 인과 경로 후보가 된다.",
                "causalPath": ["official-evidence", "price-reaction"],
                "supportingEvidenceIds": ["relation-risk"],
                "requiredEvidenceTypes": ["official-filing"],
                "invalidationConditions": ["다음 추론 세대에서 관계가 사라짐"],
            },
            {
                "title": "근거 조작 시도",
                "claim": "입력에 없는 근거를 사용한다.",
                "supportingEvidenceIds": ["invented-evidence"],
            },
        ]


class FakeResearchOrchestrator:
    def __init__(self, changed_count=1):
        self.changed_count = changed_count
        self.runs = []

    def run(self, question, target, brain, account_id="", force=False):
        run = ResearchRun(
            run_id="research-run-test",
            question_id=question.question_id,
            account_id=account_id,
            symbol=target.normalized_symbol(),
            status="evidence-collected" if self.changed_count else "cache-satisfied",
            task_ids=[],
            source_types=[],
            changed_evidence_count=self.changed_count,
        )
        self.runs.append(run)
        return run

    def mark_reasoning_refreshed(self, run, refreshed):
        return ResearchRun(
            **{
                **run.__dict__,
                "status": "reasoning-refreshed" if refreshed else run.status,
                "reasoning_refreshed": refreshed,
            }
        )


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

    def test_one_sided_typedb_paths_reserve_counterfactual_safety_hypothesis(self):
        context = relation_context()
        context["activeRules"] = [context["activeRules"][0]]
        context["graphStoreInference"]["relations"] = [context["graphStoreInference"]["relations"][0]]
        context["signalConflicts"] = {"hasConflict": False}
        context["missingData"] = []
        context["facts"]["missingData"] = []
        hypotheses = hypothesis_set_from_relation_context(context)["hypothesisSet"]["hypotheses"]
        self.assertGreaterEqual(len(hypotheses), 3)
        self.assertIn("hypothesis-template:system.null-challenge.v1", {item["templateId"] for item in hypotheses})
        self.assertEqual(1, len([item for item in hypotheses if item.get("supportingRuleIds")]))

    def test_rule_relation_polarity_is_not_overwritten_by_global_risk_pressure(self):
        context = relation_context()
        context["activeRules"][1]["scoreBreakdown"] = {"riskPressure": 95, "supportEvidence": 5}
        hypotheses = hypothesis_set_from_relation_context(context)["hypothesisSet"]["hypotheses"]
        support = next(item for item in hypotheses if item["templateId"] == "hypothesis-template:support-rule")
        self.assertEqual("support", support["stance"])

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
            "researchPlan": brain["researchPlan"],
            "researchAudit": {
                "runId": "research-run-1",
                "status": "reasoning-refreshed",
                "verifiedClaims": [{
                    "claimId": "claim-1",
                    "evidenceId": "evidence-1",
                    "statement": "공식 계약이 확인됐습니다.",
                    "source": "OpenDART",
                    "sourceUrl": "https://dart.fss.or.kr/example",
                    "publishedAt": "2026-07-19T01:00:00Z",
                    "observedAt": "2026-07-19T01:05:00Z",
                    "verificationStatus": "verified-primary",
                    "entityResolutionStatus": "resolved-direct",
                    "confidence": 95,
                    "reasons": [],
                }],
                "rejectedClaims": [{"claimId": "rejected-1"}],
            },
        })
        restored = DecisionEpisode.from_dict(episode.to_dict())
        self.assertEqual("episode-1", restored.episode_id)
        self.assertEqual(3, len(restored.hypothesis_set.hypotheses))
        graph = PortfolioOntology("account-1")
        add_investment_brain_concepts(graph, "account-1", [restored.to_dict()], [{
            "proposalId": "proposal-1",
            "accountId": "account-1",
            "symbol": "005930",
            "title": "검토 전 가설",
            "claim": "새 인과 경로 후보",
            "status": "review-required",
            "supportingEvidenceIds": ["evidence-1"],
        }])
        classes = {item.properties.get("tboxClass") for item in graph.entities}
        relation_types = {item.relation_type for item in graph.relations}
        self.assertIn("DecisionEpisode", classes)
        self.assertIn("CompetingHypothesis", classes)
        self.assertIn("SELECTS_HYPOTHESIS", relation_types)
        self.assertIn("COMPETES_WITH_HYPOTHESIS", relation_types)
        self.assertIn("VerifiedClaim", classes)
        self.assertIn("EvidenceAssessment", classes)
        self.assertIn("NovelHypothesisProposal", classes)
        self.assertNotIn("RejectedClaim", classes)
        self.assertIn("PRODUCES_VERIFICATION_RESULT", relation_types)
        self.assertIn("PROPOSES_HYPOTHESIS_FOR", relation_types)

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
        for name in [
            "InvestmentQuestion", "HypothesisSet", "CompetingHypothesis", "ObservedOutcome", "LearningProposal",
            "ResearchSourcePolicy", "VerificationRun", "VerifiedClaim", "NovelHypothesisProposal",
        ]:
            self.assertIn(name, class_names)
        for name in [
            "ASKS_ABOUT", "COMPETES_WITH_HYPOTHESIS", "SELECTS_HYPOTHESIS", "RESULTED_IN_OUTCOME", "LEARNED_FROM",
            "TESTS_HYPOTHESIS", "INSTANTIATES_HYPOTHESIS_TEMPLATE", "PRODUCES_VERIFICATION_RESULT",
            "PROPOSES_HYPOTHESIS_FOR",
        ]:
            self.assertIn(name, relation_names)

    def test_research_governance_accepts_only_fresh_resolved_evidence(self):
        target = NewsCollectionTarget("005930", "삼성전자", "KR", "KRW", "반도체")
        valid = ResearchEvidence(
            evidence_id="evidence-valid",
            symbol="005930",
            kind="news",
            source="Reuters",
            title="삼성전자 공식 사업 업데이트",
            observed_at="2026-07-19T11:00:00Z",
            published_at="2026-07-19T11:00:00Z",
            confidence=0.9,
            raw_payload={"relationScope": "direct", "sourceReliability": 90},
        )
        unresolved = ResearchEvidence(
            evidence_id="evidence-unresolved",
            symbol="005930",
            kind="news",
            source="Unknown blog",
            title="다른 회사 이야기",
            observed_at="2026-07-19T11:00:00Z",
            published_at="2026-07-19T11:00:00Z",
            confidence=0.9,
            raw_payload={"relationScope": "related_product", "sourceReliability": 90},
        )
        accepted, verified, rejected = governed_evidence(
            [valid, unresolved], target, max_age_minutes=10**8, minimum_source_reliability=55
        )
        self.assertEqual(["evidence-valid"], [item.evidence_id for item in accepted])
        self.assertEqual("verified-secondary", verified[0].verification_status)
        self.assertEqual("rejected", rejected[0].verification_status)
        self.assertEqual(verified[0].claim_id, valid.raw_payload["evidenceGovernance"]["claimId"])
        self.assertFalse(unresolved.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])
        graph = PortfolioOntology("account-1")
        stock_id = add_entity(graph, "stock", "005930", "삼성전자", {"tboxClass": "Stock"})
        add_governed_claim_concepts(graph, stock_id, valid, valid.raw_payload)
        classes = {item.properties.get("tboxClass") for item in graph.entities}
        relation_types = {item.relation_type for item in graph.relations}
        self.assertIn("VerifiedClaim", classes)
        self.assertIn("EvidenceAssessment", classes)
        self.assertIn("ASSERTS", relation_types)
        self.assertIn("RESOLVES_TO", relation_types)

    def test_research_orchestrator_persists_only_governed_evidence(self):
        target = NewsCollectionTarget("005930", "삼성전자", "KR", "KRW", "반도체")
        evidence = ResearchEvidence(
            evidence_id="evidence-direct",
            symbol="005930",
            kind="news",
            source="Reuters",
            title="삼성전자 공급 계약 확인",
            observed_at="2026-07-19T11:00:00Z",
            published_at="2026-07-19T11:00:00Z",
            confidence=0.92,
            raw_payload={"relationScope": "direct", "sourceReliability": 92},
        )
        evidence_store = FakeEvidenceStore()
        run_store = FakeResearchStore()
        gateway = FakeResearchGateway([evidence])
        service = InvestmentResearchOrchestrationService(
            evidence_store,
            gateway,
            research_store=run_store,
            settings={"investmentBrainResearchMinimumVerifiedCount": 2},
        )
        question = InvestmentQuestion.create("삼성전자 위험 근거를 확인해줘", "005930", "삼성전자", "account-1")
        brain = hypothesis_set_from_relation_context(relation_context(), question)
        run = service.run(question, target, brain, account_id="account-1", force=True)
        self.assertEqual("evidence-collected", run.status)
        self.assertEqual(1, run.changed_evidence_count)
        self.assertEqual(["evidence-direct"], [item.evidence_id for item in evidence_store.saved])
        self.assertEqual(1, gateway.calls)
        self.assertTrue(gateway.requested_source_types)
        self.assertTrue(evidence_store.saved[0].raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])

        cached_store = FakeEvidenceStore([evidence])
        cached_gateway = FakeResearchGateway([])
        cached_service = InvestmentResearchOrchestrationService(
            cached_store,
            cached_gateway,
            research_store=FakeResearchStore(),
            settings={"investmentBrainResearchMinimumVerifiedCount": 1},
        )
        cached_run = cached_service.run(question, target, brain, account_id="account-1")
        self.assertEqual("cache-satisfied", cached_run.status)
        self.assertEqual(0, cached_gateway.calls)

    def test_research_gateway_uses_existing_apis_and_deduplicates_sources(self):
        target = NewsCollectionTarget("005930", "삼성전자", "KR", "KRW", "반도체")
        existing_api = ExistingApiResearchGateway(provider=FakeExternalSignalProvider())
        duplicate = ResearchEvidence(
            evidence_id="research:005930:dart:202607190001",
            symbol="005930",
            kind="disclosure",
            source="OpenDART",
            title="중복 공시",
        )
        composite = CompositeInvestmentResearchGateway([
            existing_api,
            FakeResearchGateway([duplicate]),
        ])
        rows, statuses = composite.collect_for_target(target, source_types=["official-filing", "news-full-text"])
        self.assertEqual(1, len(rows))
        self.assertEqual("OpenDART", rows[0].source)
        self.assertTrue(any(item.get("source") == "existing-api-bundle" for item in statuses))
        accepted, verified, rejected = governed_evidence(
            rows,
            target,
            max_age_minutes=10**8,
            minimum_source_reliability=55,
        )
        self.assertEqual(1, len(accepted))
        self.assertEqual("verified-primary", verified[0].verification_status)
        self.assertFalse(rejected)

    def test_notification_research_refreshes_only_when_evidence_changed(self):
        refresh_calls = []

        def refresher(account_id, symbol):
            refresh_calls.append((account_id, symbol))
            return {"status": "completed", "refreshed": True}

        research = FakeResearchOrchestrator(changed_count=1)
        service = InvestmentBrainService(
            FakeMonitorStore(),
            FakeOntologyRepository(),
            FakeReviewer(),
            FakeDecisionEpisodeStore(),
            research_orchestrator=research,
            reasoning_refresher=refresher,
            settings={},
        )
        job = NotificationJob.create(
            "테스트",
            account_id="account-1",
            message_type="investmentInsight",
            context={"ontologyRelationContext": relation_context()},
        )
        NotificationHypothesisResearchEnricher(service, {})(job)
        self.assertEqual([("account-1", "005930")], refresh_calls)
        self.assertEqual("reasoning-refreshed", job.context["researchCycle"]["status"])
        self.assertTrue(job.context["ontologyRelationContext"]["hypothesisTemplates"])

        no_change_service = InvestmentBrainService(
            FakeMonitorStore(),
            FakeOntologyRepository(),
            FakeReviewer(),
            FakeDecisionEpisodeStore(),
            research_orchestrator=FakeResearchOrchestrator(changed_count=0),
            reasoning_refresher=refresher,
            settings={},
        )
        second = NotificationJob.create(
            "테스트",
            account_id="account-1",
            message_type="investmentInsight",
            context={"ontologyRelationContext": relation_context()},
        )
        NotificationHypothesisResearchEnricher(no_change_service, {})(second)
        self.assertEqual(1, len(refresh_calls))
        self.assertEqual("cache-satisfied", second.context["researchCycle"]["status"])

    def test_novel_hypothesis_proposal_requires_known_evidence(self):
        store = FakeResearchStore()
        service = HypothesisProposalService(store, FakeHypothesisAdvisor())
        result = service.propose(
            "account-1",
            "005930",
            {"questionId": "question-1"},
            {"hypotheses": []},
            {},
            relation_context(),
        )
        self.assertEqual(1, result["proposalCount"])
        self.assertEqual(["relation-risk"], result["proposals"][0]["supportingEvidenceIds"])
        self.assertEqual("review-required", result["proposals"][0]["status"])

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
