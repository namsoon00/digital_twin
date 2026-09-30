"""Question-level research must earn completion with dated, cited evidence."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from digital_twin.modules.decisions.contracts import InvestmentQuestion, utc_now_iso
from digital_twin.modules.decisions.application.investment_brain_service import InvestmentBrainService
from digital_twin.modules.portfolio.contracts import Position
from digital_twin.modules.decisions.domain.notification_ai_gate_validation import compact_research_cycle_for_ai
from digital_twin.modules.decisions.domain.notification_ai_context_router import fit_notification_ai_decision_core
from digital_twin.modules.decisions.domain.notification_ai_inference_packet import build_notification_ai_inference_packet
from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import ResearchRun, governed_evidence
from digital_twin.modules.news_intelligence.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.modules.news_intelligence.domain.financial_reporting import FINANCIAL_REPORTING_VERSION, bind_financial_report_contract
from digital_twin.modules.news_intelligence.domain.financial_research_evidence import financial_research_evidence
from digital_twin.modules.news_intelligence.domain.research_progress import assess_tasks, audit_retain_until, evidence_packets
from digital_twin.infrastructure.investment_research_gateway import ExistingApiResearchGateway, CompositeInvestmentResearchGateway
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.portfolio_ontology_cognitive_concepts import add_investment_brain_concepts
from digital_twin.modules.reasoning.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts


TARGET = NewsCollectionTarget("AAPL", "Apple", "US", "USD", "technology")


def task(task_id="cash-flow", types=None, sources=None):
    return {
        "taskId": task_id, "question": "Did operating cash flow improve?", "purpose": "Check earnings quality",
        "requiredEvidenceTypes": types or ["financial-fact"], "sourceTypes": sources or ["financial-data"],
        "maxAgeMinutes": 360, "status": "blocked-by-data", "relatedHypothesisIds": [],
    }


def evidence(evidence_id="financial-1", kind="financial-fact", **kwargs):
    stamp = utc_now_iso()
    return ResearchEvidence(
        evidence_id=evidence_id, symbol="AAPL", kind=kind, source="Reuters",
        title="Apple operating cash flow report", summary="Apple operating cash flow increased in the reported quarter.",
        url="https://www.reuters.com/markets/" + evidence_id,
        observed_at=kwargs.pop("observed_at", stamp), published_at=kwargs.pop("published_at", stamp),
        raw_payload={"relationScope": "direct", "sourceTrustState": "trusted", "dataState": "sufficient",
                     "validationState": "ready", "articleReadStatus": "body", **kwargs},
    )


def packets_for(rows):
    accepted, claims, _ = governed_evidence(rows, TARGET, 1000000, "standard")
    return evidence_packets(accepted, claims)


class EvidenceStore:
    def __init__(self, cached=()):
        self.cached = list(cached)
        self.saved = []

    def latest(self, **_kwargs):
        return deepcopy(self.cached)

    def upsert_many(self, items):
        self.saved = deepcopy(items)
        return len(items)


class RunStore:
    def __init__(self):
        self.rows = []

    def save_run(self, run):
        self.rows.append(ResearchRun.from_dict(run.to_dict()))
        return run


class Gateway:
    def __init__(self, rounds=()):
        self.rounds = list(rounds)
        self.requests = []

    def collect_for_target(self, target, source_types=None, research_tasks=None):
        self.requests.append({"terms": target.research_query_terms, "tasks": deepcopy(research_tasks)})
        return self.rounds.pop(0) if self.rounds else [], [{"provider": "fixture", "status": "ok"}]


class Advisor:
    def __init__(self, discover=False, expand=False):
        self.contexts = []
        self.discover = discover
        self.expand = expand

    def plan(self, context):
        self.contexts.append(deepcopy(context))
        reviews = []
        for row in context["researchProgress"].get("tasks") or []:
            if row["candidateEvidenceIds"]:
                reviews.append({"taskId": row["taskId"], "assessmentFingerprint": row["assessmentFingerprint"],
                                "status": "addressed", "evidenceIds": row["candidateEvidenceIds"],
                                "counterEvidenceIds": [], "reason": "The cited report answers this question."})
        additions = []
        if (self.discover and not context["baselinePlan"]["taskCount"]) or (self.expand and len(self.contexts) == 2):
            additions.append({"discoveryKind": "causal-gap", "question": "Does the report explain cash conversion?",
                              "decisionChangingRationale": "Reported profit may not convert to cash", "expectedDecisionImpact": ["REVIEW_LEVEL"],
                              "sourceTypes": ["financial-data"], "requiredEvidenceTypes": ["financial-fact"],
                              "queryTerms": ["cash conversion"], "maxAgeMinutes": 360})
        return {"tasks": additions, "taskReviews": reviews}


class QuestionResearchProgressTests(unittest.TestCase):
    def test_dated_source_and_citation_contract(self):
        self._assert_existing_report_values_flow_with_periods_and_comparable_units()
        self._assert_period_coverage_is_checked_on_cited_evidence_not_collection_date()
        self._assert_future_publication_or_observation_cannot_enter_governed_facts()
        self._assert_wrong_source_clock_subject_and_revised_packet_reject_old_review()
        self._assert_source_specific_freshness_does_not_globally_discard_longer_lived_data()

    def test_adaptive_question_workflow_contract(self):
        self._assert_user_question_collects_without_graph_but_cannot_publish_action()
        self._assert_unrelated_verified_articles_do_not_satisfy_financial_question()
        self._assert_cache_completion_requires_task_bound_semantic_review()
        self._assert_same_question_can_discover_without_existing_hypothesis()
        self._assert_results_can_add_bounded_followup_and_only_pending_work_is_collected()
        self._assert_no_progress_and_budget_are_distinct_and_do_not_repeat_identical_queries()
        self._assert_collector_type_error_is_a_single_failed_attempt()

    def _assert_user_question_collects_without_graph_but_cannot_publish_action(self):
        gateway = Gateway([[evidence()]])
        orchestrator = InvestmentResearchOrchestrationService(
            EvidenceStore(), gateway,
            hypothesis_research_planner=HypothesisResearchPlanningService(Advisor(discover=True)),
        )
        service = InvestmentBrainService(None, None, None, None, research_orchestrator=orchestrator)
        service.resolve_subject = lambda *_args: ({"accountId": "account-test"}, Position(symbol="AAPL", name="Apple", market="US", currency="USD"), "fixture")
        service.load_relation_context = lambda *_args: {}
        result = service.ask("장기 현금흐름을 확인해줘", "account-test", "AAPL")
        self.assertEqual("blocked", result["status"])
        self.assertEqual("requirements-met", result["researchRun"]["stopReason"])
        self.assertEqual(1, len(gateway.requests))
        self.assertNotIn("answer", result)
        self.assertEqual(["typedb-inference-relations"], result["missing"])

    def test_durable_research_audit_contract(self):
        self._assert_collected_revision_wins_over_cached_duplicate_and_receipts_round_trip()
        self._assert_queued_checkpoints_keep_original_context_and_legacy_runs_load()
        self._assert_audit_survives_prompt_and_existing_research_graph_projection()

    def _assert_existing_report_values_flow_with_periods_and_comparable_units(self):
        stamp = utc_now_iso()

        def report(period, value):
            return bind_financial_report_contract({
                "period": period, "periodEnd": period, "frequency": "quarterly", "provider": "SEC EDGAR",
                "financialReportingVersion": FINANCIAL_REPORTING_VERSION, "operatingCashFlow": value,
                "metricProvenance": {"operatingCashFlow": {
                    "provider": "SEC EDGAR", "period": period, "currency": "USD", "scope": "CFS",
                    "durationBasis": "quarterly", "filed": "2026-08-01", "official": True,
                }},
            }, [{"datasetId": "sec.company_facts", "revisionId": "source-revision", "subjectKey": "AAPL", "fetchedAt": stamp}])

        company = {"name": "Apple", "financials": {"quarterly": [report("2026-03-31", 0), report("2026-06-30", 120)]}}

        class Provider:
            def signals_for_positions(self, positions):
                return {"companyKnowledge": {"AAPL": deepcopy(company)}}

        gateway = ExistingApiResearchGateway(provider=Provider())
        first, _ = gateway.collect_for_target(TARGET, source_types=["financial-data"])
        composite = CompositeInvestmentResearchGateway([gateway, Gateway([first])])
        rows, _ = composite.collect_for_target(TARGET, source_types=["financial-data"])
        self.assertEqual(2, len(rows))
        self.assertEqual({"2026-03-31", "2026-06-30"}, {row.raw_payload["periodEnd"] for row in rows})
        self.assertTrue(all(row.published_at == "2026-08-01" and row.observed_at == stamp for row in rows))
        accepted, claims, rejected = governed_evidence(rows, TARGET, 360, "standard")
        self.assertEqual(2, len(accepted), [row.reasons for row in rejected])
        self.assertTrue(all("syndicated-duplicate" not in row.reasons for row in claims))
        graph = PortfolioOntology("account-test")
        add_research_evidence_concepts(graph, "stock:AAPL", "", "", "AAPL", {},
                                      {"researchEvidence": {"AAPL": [row.to_dict() for row in accepted]}})
        financial_nodes = [node for node in graph.entities if node.properties.get("reportObservationId")]
        self.assertEqual(2, len(financial_nodes))
        self.assertTrue(all(node.properties["historicalReport"] for node in financial_nodes))
        self.assertTrue(all("operatingCashFlow" in node.properties["reportedValues"] for node in financial_nodes))
        packets = evidence_packets(accepted, claims)
        self.assertEqual(0, packets[0]["reportedValues"]["operatingCashFlow"] if packets[0]["periodEnd"] == "2026-03-31" else packets[1]["reportedValues"]["operatingCashFlow"])
        t = {**task(), "requiredPeriodEnds": ["2026-03-31", "2026-06-30"], "requiredMetrics": ["operatingCashFlow"]}
        self.assertEqual([], assess_tasks([t], packets, "AAPL", utc_now_iso())[0]["missingRequirements"])
        packets[1]["metricProvenance"]["operatingCashFlow"]["currency"] = "KRW"
        self.assertIn("incomparable-metric:operatingCashFlow", assess_tasks([t], packets, "AAPL", utc_now_iso())[0]["missingRequirements"])
        packets[1]["reportedValues"].pop("operatingCashFlow")
        self.assertTrue(any(reason.startswith("metric:operatingCashFlow:") for reason in assess_tasks([t], packets, "AAPL", utc_now_iso())[0]["missingRequirements"]))
        missing_publication = deepcopy(company)
        missing_publication["financials"]["quarterly"][0]["reportContract"]["publishedAt"] = ""
        self.assertEqual("", financial_research_evidence("AAPL", missing_publication)[0].published_at)
        missing_observation = deepcopy(company)
        for row in missing_observation["financials"]["quarterly"]:
            row["reportContract"]["sourceReferences"][0].pop("fetchedAt")
        self.assertEqual([], financial_research_evidence("AAPL", missing_observation))
        wrong_subject = deepcopy(company)
        for row in wrong_subject["financials"]["quarterly"]:
            row["reportContract"]["sourceReferences"][0]["subjectKey"] = "NVDA"
        self.assertEqual([], financial_research_evidence("AAPL", wrong_subject))

    def run_research(self, tasks, cached=(), rounds=(), advisor=None, settings=None, force=False):
        store, gateway, runs = EvidenceStore(cached), Gateway(rounds), RunStore()
        service = InvestmentResearchOrchestrationService(
            store, gateway, research_store=runs,
            hypothesis_research_planner=HypothesisResearchPlanningService(advisor) if advisor else None,
            settings={"investmentBrainResearchCooldownMinutes": 0, **(settings or {})},
        )
        question = InvestmentQuestion.create("Explain Apple's long term earnings quality", "AAPL", "Apple", "account-test")
        brain = {"researchPlan": {"planId": "plan-1", "tasks": tasks}, "missingData": ["cash-conversion"], "hypothesisSet": {"hypotheses": []}}
        before = deepcopy(brain)
        run = service.run(question, TARGET, brain, force=force)
        self.assertEqual(before, brain)
        return run, gateway, store, runs

    def _assert_unrelated_verified_articles_do_not_satisfy_financial_question(self):
        news = [evidence("news-1", "news"), evidence("news-2", "news")]
        run, gateway, _, _ = self.run_research([task()], cached=news, advisor=Advisor())
        self.assertNotEqual("cache-satisfied", run.status)
        self.assertEqual(1, len(gateway.requests))
        self.assertEqual("needs-evidence", run.task_assessments[0]["status"])
        self.assertIn("financial-fact", run.task_assessments[0]["missingRequirements"])

    def _assert_cache_completion_requires_task_bound_semantic_review(self):
        run, gateway, _, _ = self.run_research([task()], cached=[evidence()], advisor=Advisor())
        self.assertEqual("cache-satisfied", run.status)
        self.assertEqual("requirements-met", run.stop_reason)
        self.assertEqual([], gateway.requests)
        self.assertEqual("addressed", run.task_assessments[0]["status"])
        unreviewed, _, _, _ = self.run_research([task()], cached=[evidence()])
        self.assertNotEqual("cache-satisfied", unreviewed.status)
        self.assertEqual("unreviewed", unreviewed.task_assessments[0]["semanticReviewState"])

    def _assert_period_coverage_is_checked_on_cited_evidence_not_collection_date(self):
        t = {**task(), "requiredPeriodEnds": ["2026-03-31", "2026-06-30"]}
        rows = [evidence("q1", periodEnd="2026-03-31"), evidence("q2", periodEnd="2026-06-30")]
        packets = packets_for(rows)
        initial = assess_tasks([t], packets, "AAPL", utc_now_iso())[0]
        review = {"taskId": t["taskId"], "assessmentFingerprint": initial["assessmentFingerprint"],
                  "evidenceIds": ["q2"], "status": "addressed", "reason": "Growth improved"}
        assessed = assess_tasks([t], packets, "AAPL", utc_now_iso(), [review])[0]
        self.assertEqual(["period:2026-03-31"], assessed["missingRequirements"])
        review["evidenceIds"].append("q1")
        self.assertEqual("addressed", assess_tasks([t], packets, "AAPL", utc_now_iso(), [review])[0]["status"])

    def _assert_future_publication_or_observation_cannot_enter_governed_facts(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        for field in ("published_at", "observed_at"):
            with self.subTest(field=field):
                accepted, verified, rejected = governed_evidence([evidence(**{field: future})], TARGET, 360, "standard")
                self.assertEqual([], accepted)
                self.assertEqual([], verified)
                self.assertTrue(any("after-verification-cutoff" in reason for row in rejected for reason in row.reasons))

    def _assert_wrong_source_clock_subject_and_revised_packet_reject_old_review(self):
        t = task()
        packets = packets_for([evidence()])
        cutoff = utc_now_iso()
        initial = assess_tasks([t], packets, "AAPL", cutoff)[0]
        review = {"taskId": t["taskId"], "assessmentFingerprint": initial["assessmentFingerprint"],
                  "evidenceIds": ["financial-1"], "status": "addressed", "reason": "Supported"}
        self.assertEqual("addressed", assess_tasks([t], packets, "AAPL", cutoff, [review])[0]["status"])
        for field, value in (("sourceRevision", "revision-2"), ("observedAt", "2099-01-01T00:00:00Z"), ("symbol", "NVDA")):
            revised = deepcopy(packets)
            revised[0][field] = value
            assessment = assess_tasks([t], revised, "AAPL", cutoff, [review])[0]
            self.assertNotEqual("addressed", assessment["status"])
            self.assertTrue(assessment["reviewRejected"])

        forged = {**review, "evidenceIds": ["invented-report"]}
        self.assertTrue(assess_tasks([t], packets, "AAPL", cutoff, [forged])[0]["reviewRejected"])

    def _assert_same_question_can_discover_without_existing_hypothesis(self):
        advisor = Advisor(discover=True)
        run, gateway, _, _ = self.run_research([], rounds=[[evidence()]], advisor=advisor)
        self.assertEqual([], advisor.contexts[0]["candidateHypotheses"])
        self.assertEqual(1, len(gateway.requests))
        self.assertEqual("requirements-met", run.stop_reason)
        self.assertEqual(1, len(run.task_assessments))
        self.assertEqual("research-only", run.task_assessments[0]["decisionEligibility"])

    def _assert_results_can_add_bounded_followup_and_only_pending_work_is_collected(self):
        advisor = Advisor(expand=True)
        run, gateway, _, _ = self.run_research([task()], rounds=[[evidence("first")], [evidence("second")]], advisor=advisor)
        self.assertEqual(2, run.round_count)
        self.assertEqual(2, len(gateway.requests))
        self.assertEqual(["cash-flow"], [row["taskId"] for row in gateway.requests[0]["tasks"]])
        self.assertNotIn("cash-flow", [row["taskId"] for row in gateway.requests[1]["tasks"]])
        self.assertEqual("requirements-met", run.stop_reason)
        self.assertEqual(3, len(run.round_history))

    def _assert_no_progress_and_budget_are_distinct_and_do_not_repeat_identical_queries(self):
        for maximum, expected in ((1, "round-budget-exhausted"), (3, "no-new-research-path")):
            with self.subTest(maximum=maximum):
                run, gateway, _, _ = self.run_research([task()], advisor=Advisor(), settings={"investmentBrainResearchMaxRounds": maximum})
                self.assertEqual(expected, run.stop_reason)
                self.assertEqual(1, len(gateway.requests))
                self.assertEqual("needs-evidence", run.task_assessments[0]["status"])

    def _assert_collected_revision_wins_over_cached_duplicate_and_receipts_round_trip(self):
        run, _, store, runs = self.run_research([task()], cached=[evidence(sourceRevision="old")],
                                              rounds=[[evidence(sourceRevision="new")]], advisor=Advisor(), force=True)
        self.assertEqual("new", store.saved[0].raw_payload["sourceRevision"])
        restored = ResearchRun.from_dict(run.to_dict())
        self.assertEqual(run.to_dict(), restored.to_dict())
        self.assertEqual("new", restored.round_history[-1]["evidencePackets"][0]["sourceRevision"])
        self.assertTrue(any(row.status == "processing" and row.round_history for row in runs.rows))
        self.assertEqual(730, (datetime.fromisoformat(run.audit_retain_until.replace("Z", "+00:00")) - datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))).days)

    def _assert_queued_checkpoints_keep_original_context_and_legacy_runs_load(self):
        runs = RunStore()
        service = InvestmentResearchOrchestrationService(EvidenceStore(), Gateway([[evidence()]]), research_store=runs)
        question = InvestmentQuestion.create("장기 현금흐름을 확인해줘", "AAPL", "Apple", "account-test")
        brain = {"researchPlan": {"tasks": [task()]}, "missingData": ["cash-flow"]}
        queued = service.enqueue(question, TARGET, brain, account_id="account-test", notification_event_id="event-1")
        completed = service.execute_queued(queued)
        self.assertEqual(queued.request_context, completed.request_context)
        self.assertTrue(all(row.request_context == queued.request_context for row in runs.rows if row.status == "processing"))
        legacy = ResearchRun.from_dict({"runId": "old-run"})
        self.assertEqual([], legacy.task_assessments)
        self.assertEqual("", legacy.audit_retain_until)
        self.assertEqual("2028-09-29T00:00:00Z", audit_retain_until("long-term", "2026-09-30T00:00:00Z"))

    def _assert_collector_type_error_is_a_single_failed_attempt(self):
        class BrokenGateway:
            calls = 0

            def collect_for_target(self, target, source_types=None, research_tasks=None):
                self.calls += 1
                raise TypeError("provider payload malformed")

        gateway = BrokenGateway()
        service = InvestmentResearchOrchestrationService(EvidenceStore(), gateway)
        rows, statuses = service.collect(TARGET, ["financial-data"], [task()])
        self.assertEqual([], rows)
        self.assertEqual(1, gateway.calls)
        self.assertEqual("error", statuses[0]["status"])

    def _assert_source_specific_freshness_does_not_globally_discard_longer_lived_data(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        run, _, _, _ = self.run_research([dict(task("short"), maxAgeMinutes=60), task("long")],
                                         cached=[evidence(published_at=old, observed_at=old)], advisor=Advisor())
        by_id = {row["taskId"]: row for row in run.task_assessments}
        self.assertEqual("needs-evidence", by_id["short"]["status"])
        self.assertEqual("addressed", by_id["long"]["status"])

    def _assert_audit_survives_prompt_and_existing_research_graph_projection(self):
        run, _, _, _ = self.run_research([task()], cached=[evidence()], advisor=Advisor())
        compact = compact_research_cycle_for_ai(run.to_dict())
        self.assertEqual("requirements-met", compact["stopReason"])
        self.assertEqual(0, compact["unresolvedTaskCount"])
        self.assertNotIn("roundHistory", compact)
        graph = PortfolioOntology("account-test")
        add_investment_brain_concepts(graph, "account-test", [{
            "episodeId": "episode-test", "symbol": "AAPL", "researchPlan": run.executed_plan,
            "question": {"questionId": "q1", "text": "Cash flow?"},
        }])
        node = next(row for row in graph.entities if row.properties.get("tboxClass") == "ResearchTask")
        self.assertEqual("research-only", node.properties["decisionEligibility"])
        self.assertEqual("addressed", node.properties["evidenceAssessment"]["status"])

        cycle = run.to_dict()
        cycle["stopReason"] = "round-budget-exhausted"
        cycle["taskAssessments"].extend([
            {**deepcopy(run.task_assessments[0]), "taskId": "unresolved-" + str(index),
             "question": "Is the cash flow comparable across reported periods?",
             "status": "needs-evidence", "coverageState": "incomplete",
             "missingRequirements": ["metric:operatingCashFlow:2026-06-30"],
             "resultEvidenceIds": ["research-only-not-action-evidence"]}
            for index in range(5)
        ])
        packet = build_notification_ai_inference_packet({
            "messageType": "investmentInsight", "rawSymbol": "AAPL",
            "ontologyRelationContext": {
                "subject": {"symbol": "AAPL", "name": "Apple", "market": "US"},
                "source": "typedbInferenceBox", "graphStoreUsed": True,
                "researchCycle": cycle, "researchPlan": run.executed_plan,
            },
        })
        core = packet.decision_core
        progress = core["researchProgress"]
        self.assertEqual("round-budget-exhausted", progress["stopReason"])
        self.assertEqual(5, progress["unresolvedTaskCount"])
        self.assertEqual(2, progress["omittedTaskCount"])
        self.assertTrue(all(row["status"] == "needs-evidence" for row in progress["tasks"]))
        self.assertTrue(all(row["missingRequirements"] for row in progress["tasks"]))
        self.assertNotIn("research-only-not-action-evidence", str(core["evidenceLedger"]))
        core["background"] = {"unused": "x" * 100000}
        core["hypothesisSet"]["comparisonMode"] = "research-only"
        fitted = fit_notification_ai_decision_core(core, 24 * 1024)
        self.assertEqual("minimum-research-review-contract", fitted["routingAudit"]["status"])
        self.assertEqual(progress, fitted["researchProgress"])


if __name__ == "__main__":
    unittest.main()
