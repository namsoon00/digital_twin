import inspect
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.application.investment_reasoning import (
    InvestmentReasoningOrchestrator,
    V2GraphDecisionCandidateBuilder,
)
from digital_twin.application.investment_reasoning.decision_synthesis import (
    V2NotificationCadence,
    build_investment_insight_events_by_snapshot,
)
from digital_twin.application.notification.workflow import (
    NotificationAIOpinionEnricher,
    NotificationAIValidatedGateEnricher,
    NotificationHypothesisResearchEnricher,
    NotificationQueueRunner,
)
from digital_twin.application.notification_ai_decision_context import NotificationAIDecisionContextEnricher
from digital_twin.application.ai_inference_queue_service import NotificationAIRequestEnqueuer
from digital_twin.domain.context_observation_notifications import (
    CONTEXT_OBSERVATION_DECISION_MODE,
    typedb_context_observation_contract,
)
from digital_twin.domain.data_freshness import evaluate_notification_data_freshness
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.independent_reasoning import independent_reasoning_request
from digital_twin.domain.notification_ai_gate_validation import normalized_hypothesis_comparison
from digital_twin.domain.notification_ai_decision_brief import notification_ai_decision_brief
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.investment_reasoning import (
    CASE_BLOCKED,
    CASE_DECISION_SYNTHESIZED,
    CASE_PUBLISHED,
    CASE_SUPPRESSED,
    CASE_SUPERSEDED,
    CASE_VALIDATED,
    FactDelta,
    GraphHypothesisManager,
    DecisionSynthesis,
    ActionAlternative,
    decision_synthesis_from_relation_context,
    reasoning_rule_inventory,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position


class InMemoryReasoningCaseRepository:
    def __init__(self):
        self.cases = {}

    def save(self, reasoning_case):
        self.cases[reasoning_case.case_id] = reasoning_case
        return reasoning_case

    def get(self, case_id):
        return self.cases.get(case_id)

    def get_by_request(self, request_id):
        return next(
            (item for item in self.cases.values() if item.request_id == request_id),
            None,
        )


class ReasoningCaseDispositionTests(unittest.TestCase):
    def test_suppressed_and_superseded_are_terminal_explained_outcomes(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        first = orchestrator.start(reasoning_request())

        suppressed = orchestrator.notification_suppressed(
            {"investmentReasoningCaseId": first.case_id},
            "delivery cooldown",
        )

        self.assertEqual(CASE_SUPPRESSED, suppressed.stage)
        self.assertTrue(suppressed.completed_at)

        second_request = reasoning_request(["FUNDAMENTAL_OBSERVATION"])
        second = orchestrator.start(second_request)
        superseded = orchestrator.case_superseded(second.case_id, "newer subject revision")

        self.assertEqual(CASE_SUPERSEDED, superseded.stage)
        self.assertTrue(superseded.completed_at)


def reasoning_request(fact_types=None):
    event = DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="market-observation:NVDA",
        event_id="event:investment-reasoning",
        occurred_at="2026-08-16T00:00:00Z",
        payload={
            "accountIds": ["account:1"],
            "affectedSymbols": ["NVDA"],
            "factTypes": list(fact_types or ["PRICE_OBSERVATION"]),
            "sourceObservedAt": "2026-08-16T00:00:00Z",
            "workClass": "MARKET",
        },
    )
    return independent_reasoning_request("ontology-v2-shadow", [event])


def hypothesis_candidate(hypothesis_id="hypothesis:recovery"):
    knowledge_basis = {
        "ruleKind": "predictive-hypothesis",
        "theoryFamily": "behavioral-momentum-and-trend",
        "thesisFamily": "trend-recovery",
        "validationStatus": "replay-required",
        "decisionEligibility": "conditional",
        "requiresHypothesis": True,
        "evidenceIndependenceKey": "trend-recovery",
        "plainLanguageBasis": "가격 회복이 이어질 수 있다는 검증 대상 가설입니다.",
    }
    return {
        "metadata": {
            "ontologyRelationContext": {
                "investmentBrain": {
                    "hypothesisSet": {
                        "hypotheses": [{
                            "hypothesisId": hypothesis_id,
                            "familyId": "recovery",
                            "label": "Price recovery is supported",
                            "candidateAction": "BUY",
                            "supportingRuleIds": ["graph.price.recovery.v1"],
                            "supportingEvidenceIds": ["evidence:price-window"],
                            "counterEvidenceIds": ["evidence:weak-volume"],
                            "invalidationConditions": ["price recovery fails"],
                            "theoryFamily": knowledge_basis["theoryFamily"],
                            "thesisFamily": knowledge_basis["thesisFamily"],
                            "knowledgeBasis": knowledge_basis,
                        }],
                    },
                },
            },
        },
    }


def crypto_context_observation_relation():
    rule_id = "graph.crypto.market.24h.up.major.v1"
    knowledge_basis = {
        "ruleKind": "context-observation",
        "decisionEligibility": "reference-only",
        "requiresHypothesis": False,
        "plainLanguageBasis": "현재 코인 시장 문맥을 설명하며 단독 투자 행동 근거로 사용하지 않습니다.",
    }
    rule = {
        "ruleId": rule_id,
        "label": "BTC 24시간 상승 변동 관찰",
        "ruleSourceKind": "crypto-asset",
        "notificationSeverity": "ALERT",
        "candidateAction": "BUY",
        "knowledgeBasis": knowledge_basis,
    }
    return {
        "subject": {"symbol": "BTC", "name": "Bitcoin", "market": "CRYPTO"},
        "facts": {
            "symbol": "BTC",
            "name": "Bitcoin",
            "market": "CRYPTO",
            "source": "watchlist",
            "isWatchlist": True,
            "currentPrice": 65000.0,
            "priceChangeRate": 11.5,
            "quoteSource": "CoinGecko",
        },
        "source": "typedbInferenceBox",
        "graphStore": "typedb",
        "graphStoreUsed": True,
        "fallbackUsed": False,
        "sourceAboxSnapshotId": "abox:crypto:1",
        "inferenceGenerationId": "generation:crypto:1",
        "generationAligned": True,
        "activeRules": [rule],
        "matchedRules": [rule],
        "allowedActions": ["BUY", "HOLD", "AVOID"],
        "blockedActions": ["ADD", "TRIM", "SELL"],
        "decision": {
            "label": "BTC 상승 변동 확대",
            "candidateAction": "BUY",
            "selectedRuleId": rule_id,
            "notificationSeverity": "ALERT",
            "basis": "typedbInferenceBox",
        },
        "executionPlan": {"notificationSeverity": "ALERT"},
        "investmentBrain": {"hypothesisSet": {"hypotheses": []}},
        "graphStoreInference": {
            "graphStore": "typedb",
            "sourceAboxSnapshotId": "abox:crypto:1",
            "inferenceGenerationId": "generation:crypto:1",
            "relations": [rule],
            "traces": [{"id": "trace:crypto:1", **rule}],
        },
    }


class InvestmentReasoningModuleTests(unittest.TestCase):
    def test_v2_candidate_builder_has_no_v1_monitor_dependency(self):
        source = inspect.getsource(V2GraphDecisionCandidateBuilder)

        self.assertNotIn("RealtimeMonitor", source)
        self.assertNotIn("events_for_snapshot", source)

    def test_v2_cooldown_starts_only_after_successful_delivery(self):
        event = SimpleNamespace(
            rule="investmentInsight",
            cadence_key=lambda: "cadence:typedb:NVDA",
        )
        queued_only = SimpleNamespace(sent={
            "cadence:typedb:NVDA": datetime.now(timezone.utc).isoformat(),
        })
        delivery_history = SimpleNamespace(
            delivered_cadence_timestamps=lambda _keys: {},
        )
        cadence = V2NotificationCadence(
            {"notificationCooldownMinutes": "60"},
            queued_only,
            delivery_history_store=delivery_history,
        )

        self.assertEqual([event], cadence.ready([event]))

        delivery_history.delivered_cadence_timestamps = lambda _keys: {
            "cadence:typedb:NVDA": datetime.now(timezone.utc).isoformat(),
        }
        self.assertEqual([], cadence.ready([event]))

    def test_v2_candidate_preserves_source_freshness_for_notification_delivery(self):
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot = AccountSnapshot(
            "account:1",
            "Test",
            "toss",
            "live",
            "ok",
            observed_at,
            PortfolioSummary(1000.0, 1000.0, 0.0, [], [], 100.0),
            positions=[Position(
                symbol="NVDA",
                name="NVIDIA",
                current_price=200.0,
                quote_source="market-feed",
                data_quality="actual",
                source_as_of=observed_at,
                source_fetched_at=observed_at,
            )],
        )
        relation = hypothesis_candidate()["metadata"]["ontologyRelationContext"]
        relation.update({
            "subject": {"symbol": "NVDA", "name": "NVIDIA"},
            "facts": {"source": "watchlist", "currentPrice": 200.0},
            "source": "typedbInferenceBox",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "generationAligned": True,
            "allowedActions": ["WATCH"],
            "decision": {
                "candidateAction": "WATCH",
                "selectedRuleId": "graph.price.recovery.v1",
                "notificationSeverity": "WATCH",
                "basis": "typedbInferenceBox",
            },
            "graphStoreInference": {
                "traces": [{"id": "trace:1"}],
                "relations": [{"ruleId": "graph.price.recovery.v1"}],
            },
        })
        synthesis = decision_synthesis_from_relation_context("account:1", relation)
        builder = V2GraphDecisionCandidateBuilder({}, SimpleNamespace(sent={}))

        event = builder._base_event(snapshot, relation, synthesis)

        self.assertEqual("fresh", event.metadata["dataFreshness"]["status"])
        source = event.metadata["dataFreshness"]["sources"][0]
        self.assertEqual(observed_at, source["sourceAsOf"])
        self.assertEqual(observed_at, event.metadata["reasoningSourceObservedAt"])
        insight = build_investment_insight_events_by_snapshot([snapshot], [event])[0]
        freshness = evaluate_notification_data_freshness(
            {"messageType": insight.rule, **dict(insight.metadata or {})},
            now=datetime.now(timezone.utc),
        )
        self.assertEqual(observed_at, insight.generated_at)
        self.assertTrue(freshness.should_send)

    def test_guardrail_only_relation_does_not_create_investment_notification(self):
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        relation = hypothesis_candidate()["metadata"]["ontologyRelationContext"]
        relation["investmentBrain"]["hypothesisSet"]["hypotheses"] = []
        guardrail = {
            "ruleId": "graph.instrument_profile.strategy_fit.support.v1",
            "label": "종목 성격·투자 성향 적합",
            "candidateAction": "HOLD",
            "notificationSeverity": "WATCH",
            "knowledgeBasis": {
                "ruleKind": "policy-constraint",
                "decisionEligibility": "guardrail-only",
                "requiresHypothesis": False,
            },
        }
        relation.update({
            "subject": {"symbol": "NVDA", "name": "NVIDIA", "market": "US"},
            "facts": {"source": "watchlist", "currentPrice": 200.0},
            "source": "typedbInferenceBox",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "sourceAboxSnapshotId": "abox:guardrail:1",
            "inferenceGenerationId": "generation:guardrail:1",
            "generationAligned": True,
            "activeRules": [guardrail],
            "matchedRules": [guardrail],
            "decision": {
                "candidateAction": "HOLD",
                "selectedRuleId": guardrail["ruleId"],
                "notificationSeverity": "WATCH",
                "basis": "typedbInferenceBox",
            },
            "graphStoreInference": {
                "relations": [guardrail],
                "traces": [{"id": "trace:guardrail:1", **guardrail}],
            },
        })
        snapshot = AccountSnapshot(
            "account:1", "Test", "toss", "live", "ok", observed_at,
            PortfolioSummary(1000.0, 1000.0, 0.0, [], [], 100.0),
        )
        synthesis = decision_synthesis_from_relation_context("account:1", relation)
        builder = V2GraphDecisionCandidateBuilder({}, SimpleNamespace(sent={}))

        self.assertEqual((), synthesis.eligible_hypothesis_ids)
        self.assertIsNone(builder._base_event(snapshot, relation, synthesis))

    def test_reference_only_crypto_relation_becomes_information_notification(self):
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        relation = crypto_context_observation_relation()
        contract = typedb_context_observation_contract(relation)
        synthesis = decision_synthesis_from_relation_context("account:1", relation)
        snapshot = AccountSnapshot(
            "account:1",
            "Test",
            "toss",
            "live",
            "ok",
            observed_at,
            PortfolioSummary(1000.0, 1000.0, 0.0, [], [], 100.0),
            external_signals={
                "cryptoFreshness": {"status": "fresh", "fetchedAt": observed_at},
                "cryptoMarkets": {
                    "bitcoin": {
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "price": 65000.0,
                        "change24h": 11.5,
                        "provider": "CoinGecko",
                        "fetchedAt": observed_at,
                    },
                },
            },
        )
        builder = V2GraphDecisionCandidateBuilder({}, SimpleNamespace(sent={}))

        event = builder._base_event(snapshot, relation, synthesis)

        self.assertEqual(CONTEXT_OBSERVATION_DECISION_MODE, contract["decisionMode"])
        self.assertEqual("NO_ACTION", synthesis.graph_candidate_action)
        self.assertEqual((), synthesis.allowed_actions)
        self.assertEqual((), synthesis.eligible_hypothesis_ids)
        self.assertEqual("cryptoOntologySignal", event.rule)
        self.assertEqual("reference-only", event.metadata["validationState"])
        self.assertFalse(event.metadata["requiresAiJudgement"])
        self.assertTrue(contract["requiresAiNarrative"])
        self.assertNotIn("행동 대안", "\n".join(event.lines))
        insight = build_investment_insight_events_by_snapshot([snapshot], [event])[0]
        self.assertEqual(["cryptoOntologySignal"], insight.metadata["sourceSignalTypes"])
        self.assertEqual(CONTEXT_OBSERVATION_DECISION_MODE, insight.metadata["notificationDecisionMode"])
        self.assertFalse(insight.metadata["requiresAiJudgement"])

    def test_reference_only_context_observation_publishes_without_ai(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        reasoning_case = orchestrator.start(reasoning_request())
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:crypto:1",
                "inferenceGenerationId": "generation:crypto:1",
                "relationCount": 1,
                "traceCount": 1,
            }},
            {},
            10,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [])
        synthesis = decision_synthesis_from_relation_context(
            "account:1",
            crypto_context_observation_relation(),
        )
        orchestrator.decisions_synthesized(reasoning_case.case_id, [synthesis])

        validated = orchestrator.context_observation_validated(reasoning_case.case_id)

        self.assertEqual(CASE_VALIDATED, validated.stage)
        self.assertEqual("NO_ACTION", validated.final_decision.action)
        self.assertEqual("typedb-context-observation", validated.final_decision.source)
        self.assertEqual("reference-only", validated.final_decision.validation_state)
        published = orchestrator.notification_published({
            "investmentReasoningCaseId": reasoning_case.case_id,
        })
        self.assertEqual(CASE_PUBLISHED, published.stage)
        self.assertTrue(published.final_decision.published)

    def test_reference_only_context_observation_queues_ai_narrative_without_inline_judgement(self):
        context = {
            "messageType": "investmentInsight",
            "ontologyRelationContext": crypto_context_observation_relation(),
        }
        job = NotificationJob.create(
            "crypto observation",
            account_id="account:1",
            message_type="investmentInsight",
            context=context,
        )

        class FailReviewer:
            def review(self, _context):
                raise AssertionError("AI reviewer must not run for a reference-only observation")

        class FailResearch:
            def enqueue_notification_research_context(self, *_args, **_kwargs):
                raise AssertionError("Hypothesis research must not run for a reference-only observation")

        NotificationAIOpinionEnricher({})(job)
        NotificationAIValidatedGateEnricher(
            FailReviewer(),
            {"notificationAiGateEnabled": "1"},
        )(job)
        NotificationHypothesisResearchEnricher(FailResearch(), {})(job)
        NotificationAIDecisionContextEnricher(None, {})(job)
        runner = NotificationQueueRunner(
            queue=SimpleNamespace(),
            account_repository=None,
            notifier_factory=lambda _account: None,
            settings={"notificationAiGateEnabled": "1"},
            ai_request_enqueuer=object(),
        )

        self.assertTrue(runner.should_defer_ai_inference(job))
        self.assertTrue(runner.apply_final_ai_delivery_gate(job))
        self.assertNotIn("notificationAiValidatedResponse", job.context)
        self.assertEqual(
            "unavailable",
            job.context["notificationAiInternalData"]["audit"]["status"],
        )

        forged = NotificationJob.create(
            "forged context",
            account_id="account:1",
            message_type="investmentInsight",
            context={
                "requiresAiJudgement": False,
                "notificationDecisionMode": CONTEXT_OBSERVATION_DECISION_MODE,
            },
        )
        self.assertTrue(runner.should_defer_ai_inference(forged))

    def test_context_observation_ai_request_does_not_reopen_typedb_action_lifecycle(self):
        job = NotificationJob.create(
            "crypto observation",
            account_id="account:1",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "ontologyRelationContext": crypto_context_observation_relation(),
                "notificationDecisionMode": CONTEXT_OBSERVATION_DECISION_MODE,
                "investmentReasoningCaseId": "reasoning-case:observation",
            },
        )

        class Queue:
            request = None

            def enqueue(self, _job, request):
                self.request = request
                return {"status": "awaiting-ai", "requestId": request.request_id}

        class NoActionLifecycle:
            def capture_ai_context(self, *_args, **_kwargs):
                raise AssertionError("narrative-only request must not recapture action hypotheses")

            def ai_queued(self, *_args, **_kwargs):
                raise AssertionError("narrative-only request must not reopen the action lifecycle")

        queue = Queue()
        enqueuer = NotificationAIRequestEnqueuer(
            queue,
            settings={},
            reasoning_orchestrator=NoActionLifecycle(),
        )

        outcome = enqueuer.enqueue(job)

        self.assertEqual("awaiting-ai", outcome["status"])
        self.assertIsNotNone(queue.request)
        self.assertEqual(CONTEXT_OBSERVATION_DECISION_MODE, queue.request.context["notificationDecisionMode"])

    def test_typedb_relation_context_becomes_auditable_action_alternatives(self):
        relation = hypothesis_candidate()["metadata"]["ontologyRelationContext"]
        relation.update({
            "subject": {"symbol": "NVDA", "name": "NVIDIA"},
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "generationAligned": True,
            "allowedActions": ["BUY", "WATCH"],
            "blockedActions": ["SELL"],
            "decision": {
                "candidateAction": "BUY",
                "selectedRuleId": "graph.price.recovery.v1",
                "nextChecks": ["volume confirmation"],
            },
            "graphStoreInference": {
                "traces": [{"id": "trace:1"}],
                "relations": [{
                    "ruleId": "graph.price.recovery.v1",
                    "candidateAction": "BUY",
                }],
            },
        })

        synthesis = decision_synthesis_from_relation_context("account:1", relation)

        self.assertEqual("BUY", synthesis.graph_candidate_action)
        self.assertEqual(("hypothesis:recovery",), synthesis.eligible_hypothesis_ids)
        self.assertEqual(["BUY"], [item.action for item in synthesis.alternatives])
        self.assertTrue(synthesis.graph_trace_complete)
        self.assertIn("SELL", synthesis.blocked_actions)

    def test_reasoning_case_persists_decision_synthesis_before_ai(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        reasoning_case = orchestrator.start(reasoning_request())
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
            }},
            {},
            10,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [hypothesis_candidate()])
        synthesis = DecisionSynthesis(
            synthesis_id="synthesis:1",
            account_id="account:1",
            symbol="NVDA",
            source_abox_snapshot_id="abox:1",
            inference_generation_id="generation:1",
            graph_candidate_action="BUY",
            allowed_actions=("BUY", "WATCH"),
            alternatives=(ActionAlternative(
                action="BUY",
                hypothesis_ids=("hypothesis:recovery",),
                decision_eligible=True,
            ),),
            eligible_hypothesis_ids=("hypothesis:recovery",),
        )

        synthesized = orchestrator.decisions_synthesized(reasoning_case.case_id, [synthesis])

        self.assertEqual(CASE_DECISION_SYNTHESIZED, synthesized.stage)
        self.assertEqual("synthesis:1", synthesized.to_dict()["decisionSyntheses"][0]["synthesis_id"])
        self.assertEqual(
            "BUY",
            orchestrator.compact_context(synthesized)["decisionSyntheses"][0]["graphCandidateAction"],
        )

    def test_ai_context_enrichment_cannot_replace_synthesized_hypothesis_ids(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        reasoning_case = orchestrator.start(reasoning_request())
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
            }},
            {},
            10,
        )
        orchestrator.hypotheses_ready(
            reasoning_case.case_id,
            [hypothesis_candidate("hypothesis:canonical")],
        )
        orchestrator.decisions_synthesized(
            reasoning_case.case_id,
            [DecisionSynthesis(
                synthesis_id="synthesis:canonical",
                account_id="account:1",
                symbol="NVDA",
                source_abox_snapshot_id="abox:1",
                inference_generation_id="generation:1",
                graph_candidate_action="BUY",
                alternatives=(ActionAlternative(
                    action="BUY",
                    hypothesis_ids=("hypothesis:canonical",),
                    decision_eligible=True,
                ),),
                eligible_hypothesis_ids=("hypothesis:canonical",),
            )],
        )
        enriched = orchestrator.capture_ai_context(
            reasoning_case.case_id,
            {
                "ontologyRelationContext": hypothesis_candidate(
                    "hypothesis:display-enrichment"
                )["metadata"]["ontologyRelationContext"],
            },
        )

        persisted = repository.get(reasoning_case.case_id)

        self.assertEqual(
            ["hypothesis:canonical"],
            [item.hypothesis_id for item in persisted.hypotheses],
        )
        self.assertEqual(
            ["hypothesis:canonical"],
            enriched["investmentReasoningCase"]["hypothesisIds"],
        )
        self.assertEqual(
            ["hypothesis:canonical"],
            [
                item["hypothesisId"]
                for item in enriched["ontologyRelationContext"]["investmentBrain"]
                ["hypothesisSet"]["hypotheses"]
            ],
        )
        prompt_hypothesis = enriched["ontologyRelationContext"]["investmentBrain"]["hypothesisSet"]["hypotheses"][0]
        self.assertEqual("behavioral-momentum-and-trend", prompt_hypothesis["theoryFamily"])
        self.assertEqual("trend-recovery", prompt_hypothesis["evidenceIndependenceKey"])
        self.assertEqual("predictive-hypothesis", prompt_hypothesis["knowledgeBasis"]["ruleKind"])
        persisted_hypothesis = persisted.to_dict()["hypotheses"][0]
        self.assertEqual("behavioral-momentum-and-trend", persisted_hypothesis["theory_family"])
        self.assertEqual("trend-recovery", persisted_hypothesis["evidence_independence_key"])
        brief_hypothesis = notification_ai_decision_brief(enriched)["inference"]["hypothesisSet"]["hypotheses"][0]
        self.assertEqual("behavioral-momentum-and-trend", brief_hypothesis["theoryFamily"])
        self.assertEqual("trend-recovery", brief_hypothesis["knowledgeBasis"]["evidenceIndependenceKey"])
        comparison = normalized_hypothesis_comparison(enriched, {
            "hypotheses": [{
                "hypothesisId": "hypothesis:canonical",
                "verdict": "supported",
                "reasoning": "TypeDB 근거가 현재 판단을 지지합니다.",
                "supportingEvidenceIds": prompt_hypothesis["supportingEvidenceIds"],
                "counterEvidenceIds": prompt_hypothesis["counterEvidenceIds"],
            }],
            "selectedHypothesisId": "hypothesis:canonical",
        })
        self.assertEqual("completed", comparison["hypothesisComparisonState"])
        self.assertEqual("hypothesis:canonical", comparison["selectedHypothesisId"])

    def test_rule_inventory_exposes_unroutable_rules_before_release(self):
        inventory = reasoning_rule_inventory([{
            "rule_id": "graph.complete.v1",
            "enabled": True,
            "domain_manifest": {
                "module": "decision-intelligence",
                "dependencyContractVersion": "v2",
                "triggerDependencies": [{"conditionId": "price"}],
                "derivedOutputs": [{"relationType": "SUPPORTS"}],
                "invalidationContract": {"mode": "not-materialized"},
                "executionStage": "critical",
                "lifecycleClass": "hot",
                "decisionEffects": ["support"],
            },
        }, {
            "rule_id": "graph.incomplete.v1",
            "enabled": True,
        }])

        self.assertEqual(2, inventory["ruleCount"])
        self.assertEqual(1, inventory["invalidRuleCount"])
        self.assertFalse(inventory["releaseReady"])

    def test_fact_delta_routes_market_price_to_realtime_lane(self):
        delta = FactDelta.from_request(reasoning_request())

        self.assertEqual("REALTIME", delta.lane)
        self.assertEqual(("NVDA",), delta.symbols)
        self.assertIn("PRICE_OBSERVATION", delta.fact_types)

    def test_hypothesis_manager_accepts_only_graph_context(self):
        manager = GraphHypothesisManager()

        graph_hypotheses = manager.from_candidates([hypothesis_candidate()])
        invented_hypotheses = manager.from_candidates([{
            "hypotheses": [{"hypothesisId": "python:invented"}],
        }])

        self.assertEqual(["hypothesis:recovery"], [item.hypothesis_id for item in graph_hypotheses])
        self.assertEqual((), invented_hypotheses)

    def test_case_is_published_only_after_inference_ai_validation_and_delivery(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        request = reasoning_request()
        reasoning_case = orchestrator.start(request, {
            "releaseFingerprint": "release:1",
            "validationCohortId": "cohort:1",
        })
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
                "relationCount": 3,
                "traceCount": 2,
            }},
            {"account:1": {"status": "ok"}},
            125,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [hypothesis_candidate()])
        orchestrator.ai_queued(reasoning_case.case_id, "ai-request:1", "notification:1")
        result = SimpleNamespace(
            request_id="ai-request:1",
            result_id="ai-result:1",
            model="gpt-test",
            reasoning_effort="max",
            latency_ms=80,
            validation_state="verified",
            response={
                "action": "BUY",
                "confidence": 0.81,
                "selectedHypothesisId": "hypothesis:recovery",
                "validationState": "verified",
                "summary": "Recovery hypothesis is supported.",
            },
        )
        request_stub = SimpleNamespace(notification_job_id="notification:1")
        validated = orchestrator.ai_completed(
            request_stub,
            {"investmentReasoningCaseId": reasoning_case.case_id},
            result,
        )

        self.assertEqual(CASE_VALIDATED, validated.stage)
        self.assertFalse(validated.final_decision.published)
        published = orchestrator.notification_published({
            "investmentReasoningCaseId": reasoning_case.case_id,
        })
        self.assertEqual(CASE_PUBLISHED, published.stage)
        self.assertTrue(published.final_decision.published)
        self.assertTrue(published.inference_result.trace_complete)

    def test_ai_cannot_publish_without_a_typedb_hypothesis(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        reasoning_case = orchestrator.start(reasoning_request())
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
            }},
            {},
            10,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [])
        orchestrator.ai_queued(reasoning_case.case_id, "ai-request:2", "notification:2")
        blocked = orchestrator.ai_completed(
            SimpleNamespace(notification_job_id="notification:2"),
            {"investmentReasoningCaseId": reasoning_case.case_id},
            SimpleNamespace(
                request_id="ai-request:2",
                result_id="ai-result:2",
                model="gpt-test",
                reasoning_effort="max",
                latency_ms=20,
                validation_state="verified",
                response={
                    "action": "BUY",
                    "selectedHypothesisId": "python:invented",
                    "validationState": "verified",
                },
            ),
        )

        self.assertEqual(CASE_BLOCKED, blocked.stage)
        self.assertIn("hypothesis set is empty", blocked.errors[-1]["reason"])

    def test_ai_failure_can_publish_the_existing_typedb_inference(self):
        repository = InMemoryReasoningCaseRepository()
        orchestrator = InvestmentReasoningOrchestrator(repository)
        reasoning_case = orchestrator.start(reasoning_request())
        orchestrator.input_ready(reasoning_case.case_id)
        orchestrator.inference_completed(
            reasoning_case.case_id,
            {"account:1": {
                "verified": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
                "relationCount": 3,
                "traceCount": 2,
            }},
            {},
            10,
        )
        orchestrator.hypotheses_ready(reasoning_case.case_id, [hypothesis_candidate()])
        orchestrator.ai_queued(reasoning_case.case_id, "ai-request:fallback", "notification:fallback")

        validated = orchestrator.ai_fallback_completed(
            SimpleNamespace(
                request_id="ai-request:fallback",
                notification_job_id="notification:fallback",
            ),
            {"investmentReasoningCaseId": reasoning_case.case_id},
            SimpleNamespace(response={"action": "WATCH"}),
            "AI request timed out",
        )

        self.assertEqual(CASE_VALIDATED, validated.stage)
        self.assertEqual("WATCH", validated.final_decision.action)
        self.assertEqual("typedb-inference-fallback", validated.final_decision.source)
        self.assertEqual("reference-only", validated.final_decision.validation_state)
        self.assertFalse(validated.final_decision.published)
        published = orchestrator.notification_published({
            "investmentReasoningCaseId": reasoning_case.case_id,
        })
        self.assertEqual(CASE_PUBLISHED, published.stage)
        self.assertTrue(published.final_decision.published)


if __name__ == "__main__":
    unittest.main()
