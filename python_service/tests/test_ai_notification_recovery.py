"""Regression coverage for observed silence, using no live model or notifier."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import os
import unittest

from digital_twin.modules.ai_orchestration.domain import research_repeat, question_gap_retrieval as gaps
from digital_twin.modules.ai_orchestration.domain import price_investigation_repeat as repeat, evidence_purpose
from digital_twin.modules.ai_orchestration.domain.token_budget import usage_view, reserve
from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
from digital_twin.modules.news_intelligence.contracts import article_source_reference, ResearchEvidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence, validate_evidence_packet, EVIDENCE_PROFILE
from digital_twin.modules.reasoning.domain.price_investigation_sources import content_state


def article():
    text = "Issuer announced a new long-term supply contract with annual delivery obligations."
    passage = {"scope": "body", "text": text,
        "id": "article-passage:" + hashlib.sha256(("body\n" + text).encode()).hexdigest()[:20]}
    return ResearchEvidence(kind="news", evidence_id="article-1", symbol="AAPL", url="https://publisher.example/article",
        source="Publisher", title="Apple (AAPL) announces a supply contract", summary="Apple announced a supply contract.", polarity="context",
        published_at="2026-10-29T00:00:00Z", observed_at="2026-10-29T00:02:00Z",
        raw_payload={"articleText": text, "articleReadStatus": "body", "bodyQualityPassed": True,
            "articleSummaryQuality": {"sourceGrounding": {"version": "article-source-contract-v2-preserved-passages",
                "passed": True, "sourcePassages": [passage]}}})


class NotificationRecoveryTests(unittest.TestCase):
    def test_distinct_question_can_use_stalled_source_without_resetting_existing_budget(self):
        from test_research_work import case, REQUEST, CONTRACT
        old = {**case(), "questionContract": CONTRACT, "researchRequest": REQUEST,
            "researchAttempts": 6, "researchRepeat": {"waitReason": "unchanged-research-material"}}
        new = {**REQUEST, "queryTerms": ["새 계약 공시 발표와 당일 가격 변동"]}
        self.assertEqual("", research_repeat.stalled_question([old], new, question="새 계약의 위험은 무엇인가?"))
        self.assertEqual(old["caseId"], research_repeat.stalled_question([old], deepcopy(REQUEST)))
        self.assertEqual(old["caseId"], research_repeat.stalled_question([old], new, CONTRACT))
        self.assertEqual("research-attempt-limit", research_repeat.admission(old, "scheduled"))
        self.assertEqual(6, old["researchAttempts"])

    def test_partial_gap_wording_is_preserved_without_false_ready_or_repair_call(self):
        from test_scoped_knowledge_research import scoped_decision
        scope = {"caseId": "one", "status": "waiting", "selectedFactIds": ["report"], "missingEvidence": ["공식 현금흐름"]}
        raw = {**scoped_decision("assess", ["report"], [scope], ["같은 기간 비교"]),
            "purpose": "original-question", "blocker": "required-source"}
        checked = gaps.validate_decision(raw)
        self.assertEqual(["공식 현금흐름", "같은 기간 비교"], checked["questionScopes"][0]["missingEvidence"])
        self.assertEqual("waiting", checked["questionScopes"][0]["status"])
        raw["questionScopes"][0].update(status="ready", missingEvidence=[])
        self.assertEqual("waiting", gaps.validate_decision(raw)["questionScopes"][0]["status"])
        raw["missingEvidence"] = [str(i) for i in range(5)]
        with self.assertRaises(ReadRequestError): gaps.validate_decision(raw)
        first = ["원래 확인 항목 " + str(i) for i in range(4)]
        second = ["추가 확인 항목 " + str(i) for i in range(4)]
        raw["questionScopes"][0]["missingEvidence"] = first
        raw["missingEvidence"] = second
        checked = gaps.validate_decision(raw)
        self.assertEqual(4, len(checked["missingEvidence"]))
        self.assertEqual(set(first+second), set("\n".join(checked["missingEvidence"]).splitlines()))
        raw["missingEvidence"] = ["x" * 600, "추가 요구"]
        checked = gaps.validate_decision(raw)
        self.assertIn("x" * 600, checked["missingEvidence"])
        self.assertEqual(set(first+raw["missingEvidence"]), set("\n".join(checked["missingEvidence"]).splitlines()))
        raw["missingEvidence"] = ["x" * 600] * 4
        raw["questionScopes"][0]["missingEvidence"] = [str(i) * 600 for i in range(4)]
        with self.assertRaises(ReadRequestError): gaps.validate_decision(raw)

    def test_article_reference_is_original_text_and_keeps_quality_rejection(self):
        item = article(); before = deepcopy(item.raw_payload)
        item.raw_payload["investmentJudgmentEligible"] = False
        ref = article_source_reference(item)
        self.assertEqual(before["articleText"], ref["text"])
        self.assertFalse(item.raw_payload["investmentJudgmentEligible"])
        self.assertEqual("source-text-present", content_state({"kind": "research-evidence", "articleSourceReference": ref}))
        from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact
        self.assertFalse(usable_fact({"articleSourceReference": ref, "judgementEvidenceUsable": False}))
        for key, value in (("articleText", "unrelated text"), ("bodyQualityPassed", False),
                           ("articleReadStatus", "title-only"), ("articleSummaryQuality", "malformed")):
            bad = article(); bad.raw_payload[key] = value
            self.assertEqual({}, article_source_reference(bad))
        bad = article(); bad.raw_payload["articleSummaryQuality"]["sourceGrounding"]["sourcePassages"][0]["id"] = "invented"
        self.assertEqual({}, article_source_reference(bad))

        from unittest.mock import patch
        from digital_twin.modules.reasoning.domain import portfolio_ontology_research_concepts as projection
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_research_evidence_item
        from digital_twin.modules.news_intelligence.contracts import research_evidence_from_payload, article_projection_proof
        item.raw_payload.update(relationScope="direct", investmentJudgmentEligible=False)
        item = research_evidence_from_payload(compact_research_evidence_item(item.to_dict()))
        self.assertEqual(ref, article_source_reference(item))
        self.assertEqual({}, article_projection_proof({**item.raw_payload, "articleText": "x" * 32001}))
        graph = PortfolioOntology("fixture")
        with patch.object(projection, "research_evidence_from_facts", return_value=[item]), \
             patch.object(projection, "research_evidence_from_external_signals", return_value=[]):
            projection.add_research_evidence_concepts(graph, "stock", "thesis", "opinion", item.symbol, {}, {})
        fact = next(row for row in graph.entities if row.kind == "research-evidence")
        self.assertEqual(ref, fact.properties["articleSourceReference"])
        self.assertFalse(fact.properties["investmentJudgmentEligible"])

    def test_question_gap_contract_reaches_author_and_progress_without_changing_old_replay(self):
        from test_efficient_retrieval import packet
        from test_question_evidence_flow import question
        from test_scoped_knowledge_research import scoped_decision
        from digital_twin.modules.ai_orchestration.domain import execution_lanes, question_assessment, efficient_retrieval, question_evidence
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
        from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
        from unittest.mock import Mock
        value = packet()
        value.update(executionLanePolicy=execution_lanes.VERSION, executionLane="question-research",
            questionWorkPolicy=question_assessment.VERSION, questionGapPolicy=gaps.MARKER)
        case, saved = question(value), []
        selected = ["quote-1", "report-2026-06-30", "report-2026-03-31"]
        scope = {"caseId": case["caseId"], "status": "waiting", "selectedFactIds": selected[1:],
                 "missingEvidence": ["비교 기간의 공식 주석"]}
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return "gap-read-" + str(len(saved))
        def decide(env, _):
            self.assertEqual(gaps.VERSION, env["promptVersion"])
            if len(saved) == 1:
                old = efficient_retrieval.freeze(env["current"], env["retrievalContext"], 262144)
                before = deepcopy(old); validate_execution_input(old)
                self.assertEqual(before, old)
                self.assertNotIn(gaps.INSTRUCTIONS, old["prompt"])
                path = next(path for path in env["retrievalContext"]["readableContextPaths"] if path.endswith("/metricProvenance"))
                return {**scoped_decision("read", selected), "purpose": "original-question", "blocker": "none",
                        "requests": [{"tool": "read_context", "path": path}]}
            return {**scoped_decision("assess", selected, [scope], ["연간 보고 범위 일치 확인"]),
                    "purpose": "original-question", "blocker": "required-source"}
        model = Mock(side_effect=decide)
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]),
            value, [], [case], model, save, 262144)
        self.assertEqual(2, model.call_count)
        self.assertEqual("assessment-ready", final["retrieval"]["status"])
        validate_execution_input(freeze_execution_input(final, history, research, retrieval_trace=trace))
        progress = question_evidence.checkpoints(final, trace)[0]["progress"]
        self.assertEqual(["비교 기간의 공식 주석", "연간 보고 범위 일치 확인"], progress["missingRequirements"])
        self.assertEqual("waiting", progress["status"])
        self.assertEqual("read-progress-only", progress["authority"])

    def test_comparison_facts_are_readable_with_exact_clocks_and_quality(self):
        from test_price_investigation_flow import with_sources
        p = with_sources(); p["profile"] = EVIDENCE_PROFILE
        fact = {**p["facts"][0], "id": "comparison", "kind": "relative-performance-observation",
            "symbol": p["symbol"], "proxySymbol": "SPY", "stockChangeRate": -2, "proxyChangeRate": -1,
            "comparisonWindow": "session-change", "timestampsAligned": False,
            "judgementEvidenceUsable": False, "proxySourceAsOf": "2025-01-01T00:00:00Z"}
        p["facts"], p["coverage"] = select_evidence(p["facts"] + [fact])
        session = ObservationEvidenceSession(p, p["facts"])
        _, index, _ = session.price_investigation()
        entry = next(row for row in index["context"] if row["factId"] == "comparison")
        self.assertTrue(entry["explicitlyIneligible"])
        self.assertEqual("technical", entry["category"])
        validate_evidence_packet(p)
        old = deepcopy(p); old["profile"] = "independent-observation-v5-portfolio-context"
        with self.assertRaises(ValueError): validate_evidence_packet(old)

    def test_source_identity_ignores_quote_tick_but_detects_new_article_and_position(self):
        from test_price_investigation_flow import with_sources
        p = with_sources()
        def key(value): return ObservationEvidenceSession(value, value["facts"]).price_investigation_source_key()
        first = key(p)
        q = next(row for row in p["facts"] if row["kind"] == "stock")
        q["currentPrice"] += 1
        self.assertEqual(first, key(p))
        q["quantity"] = 99
        self.assertNotEqual(first, key(p))
        changed = deepcopy(p); first = key(changed)
        next(row for row in changed["facts"] if row["id"] == "actual-news")["articleText"] += " New evidence."
        self.assertNotEqual(first, key(changed))

    def test_inconclusive_wait_is_bounded_and_new_receipts_or_transitions_resume(self):
        from digital_twin.modules.ai_orchestration.domain.observation_wait import disposition, unchanged_wait
        now = datetime(2026, 10, 30, tzinfo=timezone.utc)
        packet = {"priceInvestigationRepeatPolicy": repeat.VERSION, "priceInvestigationSourceKey": "same",
            "facts": [{"id": "quote", "kind": "stock", "currentPrice": 100, "changeRate": 1}],
            "deliveredNotificationContext": {"fingerprint": "receipt"}}
        previous = {"input": deepcopy(packet), "priceExplanation": {"status": "cause-unconfirmed"},
            "quality": {"status": "observation-only"}, "notification": {"send": False},
            "observedAt": (now-timedelta(hours=1)).isoformat()}
        self.assertTrue(repeat.waiting(packet, previous, [], now))
        self.assertFalse(repeat.waiting(packet, previous, [], now+timedelta(hours=3)))
        for extra in ({"currentPrice": 100.5}, {"currentPrice": True}, {"changeRate": -1},
                      {"previousClose": 98}, {"marketSessionStatus": "closed"}):
            moved = deepcopy(packet); moved["facts"][0].update(extra)
            self.assertFalse(repeat.waiting(moved, previous, [], now))
        moved = deepcopy(packet); moved["facts"][0]["currentPrice"] = 100.1
        self.assertTrue(repeat.waiting(moved, previous, [], now))
        outcome = disposition(packet, "key", "data-wait", ["new event needed"], now)
        self.assertTrue(unchanged_wait(outcome, "key"))
        outcome["reasonCode"] = "unchanged-price-explanation-sources"
        self.assertFalse(unchanged_wait(outcome, "key", now+timedelta(hours=3)))
        for extra in ({"priceInvestigationSourceKey": "new"}, {"deliveredNotificationContext": {"fingerprint": "new"}},
                      {"followUpEvaluations": [{"transitionVerified": True}]}):
            self.assertFalse(repeat.waiting({**packet, **extra}, previous, [], now))

    def test_retrieval_borrows_preparation_only_and_keeps_completion_protection(self):
        now = datetime(2026, 10, 29, 20, tzinfo=timezone.utc)
        settings = {"aiDailyTokenBudget": 30_000_000, "aiTokenBudgetPolicy": "paced-completion-v1"}
        state = {"workloads": {"all": {"input_tokens": 17_991_199}},
            "pools": {"retrieval": {"input_tokens": 10_853_484}, "completion": {"input_tokens": 4_267_384},
                      "preparation": {"input_tokens": 2_870_331}}}
        view = usage_view(settings, state, now)
        self.assertGreater(view["allocation"]["retrievalBorrowLimit"], 0)
        reserve(settings, state, "independent-observation", now, "retrieval")
        full = {"workloads": {"all": {"input_tokens": 16_350_000}},
                "pools": {"retrieval": {"input_tokens": 16_350_000}}}
        with self.assertRaises(AIControlBudgetWait): reserve(settings, full, "independent-observation", now, "retrieval")
        reserve(settings, full, "independent-observation", now, "completion")

    def test_quote_expiry_blocks_current_authoring_but_allows_dated_explanation(self):
        from test_price_investigation_flow import packet
        p = packet(); p["analysisPurpose"] = "current-state"
        at = datetime.fromisoformat(p["capturedAt"].replace("Z", "+00:00")) + timedelta(days=2)
        before = deepcopy(p)
        self.assertIsNotNone(evidence_purpose.expired_current_quote(p, at.isoformat()))
        self.assertEqual(before, p)
        p["analysisPurpose"] = "source-dated"
        self.assertIsNone(evidence_purpose.expired_current_quote(p, at.isoformat()))

    def test_expired_quote_wait_skips_author_and_uses_full_source_inventory_for_next_check(self):
        from contextlib import nullcontext
        from unittest.mock import Mock, patch
        from test_price_investigation_flow import with_sources
        from digital_twin.modules.ai_orchestration.application.control import AIControlService
        from digital_twin.modules.reasoning.contracts import quote_clock_assessment
        p = with_sources()
        subject = {key: p[key] for key in ("accountId", "symbol", "name", "worldId")}
        session = ObservationEvidenceSession(p, p["facts"])
        job = {**subject, "taskId": "expiry", "capability": "market-observe", "attempts": 1}
        store = Mock(); store.claim.return_value = job; store.keep_alive.return_value = nullcontext()
        store.memory.return_value = []; store.last_processing_outcome.return_value = {}; store.complete.return_value = True
        author = Mock()
        service = AIControlService(store, lambda: [subject], lambda _: session, author, Mock(), lambda *_: [],
            read_planner=Mock())
        captured = []
        def retrieve(_, current, history, research, *args, **kwargs):
            captured.append(deepcopy(current))
            # Selection excludes optional facts. Their identity must still be
            # present in the wait key used by the next full source capture.
            selected = {**current, **session.select([]), "analysisPurpose": "current-state",
                        "retrieval": {"status": "ready"}}
            return selected, history, research, []
        at = datetime.fromisoformat(p["capturedAt"].replace("Z", "+00:00")) + timedelta(days=2)
        with patch("digital_twin.modules.ai_orchestration.application.control.retrieve_working_evidence", side_effect=retrieve), \
             patch("digital_twin.modules.ai_orchestration.application.control.stamp", return_value=at.isoformat()):
            result = service.run_once()
        store.fail.assert_not_called(); author.assert_not_called()
        self.assertEqual("awaiting-evidence", result["status"])
        _, stored, children = store.complete.call_args.args
        self.assertEqual("pre-author-quote-check", stored["processingOutcome"]["stage"])
        checked = quote_clock_assessment(captured[0]["facts"], at.isoformat())
        expected = evidence_purpose.quote_wait_key({**captured[0], "quoteAssessment": checked}, [])
        self.assertEqual(expected, stored["processingOutcome"]["quoteWaitKey"])
        self.assertEqual(["market-observe"], [child["capability"] for child in children])
        retry = datetime.fromisoformat(children[0]["availableAt"].replace("Z", "+00:00"))
        self.assertTrue(240 < (retry-datetime.now(timezone.utc)).total_seconds() <= 300)


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class QuestionGapStorageTests(unittest.TestCase):
    def setUp(self):
        from test_external_influence_flow import ExternalInfluenceStorageTests
        ExternalInfluenceStorageTests.setUp(self)

    def test_new_gap_round_is_persisted_with_immutable_source_and_author_proof(self):
        from unittest.mock import Mock
        from test_ai_control import SUBJECT
        from test_efficient_retrieval import packet
        from test_question_evidence_flow import question
        from test_scoped_knowledge_research import scoped_decision
        from digital_twin.modules.ai_orchestration.domain import execution_lanes, question_assessment
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
        from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        store = self.storage.control
        store.runtime_settings.update(aiControlBudgetEnabled="false", aiTokenBudgetEnabled="false")
        store.seed(SUBJECT); job = store.claim()
        value = {**packet(), **SUBJECT, "taskId": job["taskId"], "executionLanePolicy": execution_lanes.VERSION,
                 "executionLane": "question-research", "questionWorkPolicy": question_assessment.VERSION,
                 "questionGapPolicy": gaps.MARKER}
        value["sourceSnapshots"] = {value["worldId"]: value["sourceSnapshotId"]}
        for fact in value["facts"]:
            fact.update(symbol=value["symbol"], sourceWorldId=value["worldId"], sourceSnapshotId=value["sourceSnapshotId"])
        case = question(value)
        scope = {"caseId": case["caseId"], "status": "waiting", "selectedFactIds": ["report-2026-06-30"],
                 "missingEvidence": ["연간 공시의 현금흐름 주석"]}
        def save(envelope):
            input_id = store.save_execution_input(job, envelope)
            call = store.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
            store.finish_call(call)
            return input_id
        model = Mock(return_value={**scoped_decision("assess", ["report-2026-06-30"], [scope], ["같은 기간 비교"]),
                                  "purpose": "original-question", "blocker": "required-source"})
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]),
            value, [], [case], model, save, 262144, record_round=lambda step: store.save_retrieval_round(job, step))
        envelope = freeze_execution_input(final, history, research, retrieval_trace=trace)
        result = {"input": final, "executionInputId": save(envelope),
                  "retrievalInputIds": [step["inputId"] for step in trace]}
        with store.connect() as connection:
            self.assertEqual(trace, AIControlResearchKnowledge.read_proof(connection, job, result))
        forged = deepcopy(trace[0]); forged["decision"]["missingEvidence"] = []
        with self.assertRaises(ValueError): store.save_retrieval_round(job, forged)
        self.assertEqual(1, model.call_count)
