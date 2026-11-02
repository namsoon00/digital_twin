"""Research memories must reach source-backed meaning, not a price-only rewrite."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import analyst_memory as memory, analyst_prompt, reliable_retrieval
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.application.abstention_review import reconsider
from digital_twin.modules.ai_orchestration.application.opinion_preparation import memory_reads
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession


def assessment(packet):
    return {"memories": [{"memoryId": r["memoryId"], "effect": "unresolved", "reason": "과거 답변과 대조할 같은 기간의 원문이 부족합니다.",
        "evidence": [], "nextEvidence": "원래 질문의 비교 기간에 맞는 실적 원문을 확인합니다."}
        for r in packet.get("analystMemory", {}).get("items", [])], "insightKind": "none",
        "meaning": "사업 가치의 변화를 뒷받침할 원문은 아직 부족합니다.", "counterEvidence": "주가 변화만으로 기존 사업 가설을 반증할 수 없습니다."}


class AnalystMemoryTests(unittest.TestCase):
    def test_frozen_new_contracts_replay_without_changing_legacy_inputs(self):
        from test_reviewed_opinion_memory import fixture, approved
        p, memories, trace, _, raw = fixture()
        p["retrievalReliabilityPolicy"] = reliable_retrieval.MARKER
        old = freeze_execution_input(p, [], memories, retrieval_trace=trace); saved = deepcopy(old)
        p = memory.prepare({**p, "analystMemoryPolicy": memory.VERSION}, memories)
        raw["analystAssessment"] = assessment(p)
        current = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        self.assertEqual(analyst_prompt.PROMPT_VERSION, current["promptVersion"])
        for env in (current, freeze_repair_input(current, raw, ["기억 연결 검토"], "parent"),
                    freeze_review_input(approved(p, memories, raw)), old):
            validate_execution_input(env)
            bad = deepcopy(env); bad["current"]["analystMemoryPolicy"] = "changed"
            if env is not old:
                with self.assertRaises(ValueError): validate_execution_input(bad)
        self.assertEqual(saved, old)

    def test_each_memory_needs_disposition_and_current_usable_original(self):
        from test_observation_abstention import held
        r, _ = held()
        p = memory.prepare({**r["input"], "analystMemoryPolicy": memory.VERSION,
            "reviewedQuestionContext": [{"caseId": "cash-flow", "sources": [{"factId": "actual-news"}]}]}, [])
        value = assessment(p); self.assertEqual(value, memory.validate(value, p))
        invalid = deepcopy(value); invalid["memories"] = []
        with self.assertRaises(ValueError): memory.validate(invalid, p)
        row = value["memories"][0]; row["effect"] = "strengthened"
        with self.assertRaises(ValueError): memory.validate(value, p)
        row["evidence"] = [{"period": "current", "factId": "actual-news", "field": "articleText"}]
        memory.validate(value, p)
        next(f for f in p["facts"] if f["id"] == "actual-news")["judgementEvidenceUsable"] = False
        with self.assertRaises(ValueError): memory.validate(value, p)
        with self.assertRaises(ValueError): memory.prepare(p, [{"kind": "business-thesis", "caseId": "foreign", "accountId": "other"}])

    def test_price_only_or_news_only_in_counterargument_cannot_be_insight(self):
        from test_observation_abstention import held
        from digital_twin.modules.ai_orchestration.domain.insight_repair import correction_warranted
        r, _ = held(); r["input"] = memory.prepare({**r["input"], "analystMemoryPolicy": memory.VERSION}, [])
        r["analystAssessment"] = {**assessment(r["input"]), "insightKind": "correction"}
        r["notification"]["send"] = True
        quote = {"period": "current", "factId": "quote-1", "field": "currentPrice"}
        news = {"period": "current", "factId": "actual-news", "field": "articleText"}
        r["claimEvidence"] = {"summary": [quote], "hypothesis": [quote], "portfolioImpact": [quote], "counterEvidence": [news]}
        self.assertEqual([memory.VALUE_ERROR], memory.quality_errors(r))
        r["quality"] = {"status": "rejected", "errors": [memory.VALUE_ERROR]}
        self.assertFalse(correction_warranted(r))
        r["claimEvidence"]["hypothesis"].append(news)
        self.assertEqual([], memory.quality_errors(r))
        good = {"analystAssessment": {"supported": True, "substantive": True, "memoryIds": [], "reason": "원문이 사업 가정에 미치는 영향을 검토했습니다."}}
        self.assertEqual([], memory.review_errors(r, good))
        good["analystAssessment"]["substantive"] = False
        self.assertTrue(memory.review_errors(r, good))

    def test_new_existing_limit_crossing_is_distinct_from_persistent_overweight(self):
        from test_observation_abstention import held
        r, _ = held(); p = r["input"]
        quote = next(f for f in p["facts"] if f["id"] == "quote-1")
        quote.update(positionWeight=31, strategyMaxPositionWeightPct=30)
        p["lastDeliveredNotification"] = {"facts": [{**quote, "positionWeight": 29}]}
        refs = [{"period": "current", "factId": "quote-1", "field": field} for field in ("positionWeight", "strategyMaxPositionWeightPct")]
        self.assertTrue(memory.new_position_risk(p, refs))
        p["lastDeliveredNotification"]["facts"][0]["positionWeight"] = 31
        self.assertFalse(memory.new_position_risk(p, refs))

    def test_omission_review_cannot_spend_rewrite_call_on_price_only_evidence(self):
        from test_observation_abstention import held, verdict
        r, raw = held(); r["input"]["analystMemoryPolicy"] = memory.VERSION
        answer = verdict(r, "reconsider")
        answer["evidence"] = [{"factId": "quote-1", "field": "currentPrice", "period": "current"}]
        # A real position change admits the quote to the compact reviewer.
        quote = next(f for f in r["input"]["facts"] if f["id"] == "quote-1")
        quote.update(positionWeight=31, strategyMaxPositionWeightPct=30)
        r["input"]["lastDeliveredNotification"] = {"facts": [{**quote, "positionWeight": 29}]}
        reviewer, writer = Mock(return_value=answer), Mock()
        returned = reconsider(r, raw, {}, "author", [], Mock(return_value="review"), reviewer, writer)
        reviewer.assert_called_once(); writer.assert_not_called()
        self.assertFalse(returned[-1]); self.assertFalse(r["notification"]["send"])
        self.assertEqual("retained-insufficient-investment-meaning", r["abstentionReview"]["resolution"])
        from digital_twin.modules.ai_orchestration.domain.observation_abstention import assessment as silence
        self.assertNotEqual("review-needed", silence(r)["reasonCode"])

    def test_memory_original_reads_are_bounded_to_issued_sources(self):
        from question_assessment_checks import fixture
        p, session, case, document = fixture()
        p = memory.prepare({**p, "analystMemoryPolicy": memory.VERSION,
            "reviewedQuestionContext": [{"caseId": case["caseId"], "sources": [{"factId": document["id"]}, {"factId": "unissued"}]}]}, [])
        documents = session.question_document_index({document["historicalDocumentReference"]["evidenceId"]: [case["caseId"]]}, document_views=True)
        views, work = memory_reads(session, p, documents, [], set())
        self.assertEqual({document["id"]}, set(views)); self.assertEqual(1, len(work))
        self.assertEqual("read-needs-assessment", work[0]["status"])
        self.assertEqual(({}, []), memory_reads(session, p, documents, [], set(views)))

    def test_business_baseline_survives_quote_only_planner_selection(self):
        from test_reliable_opinion_completion import packet
        from test_efficient_retrieval import decision
        p = {**packet(), "analystMemoryPolicy": memory.VERSION}
        def decide(env, _):
            self.assertEqual(analyst_prompt.RETRIEVAL_VERSION, env["promptVersion"])
            validate_execution_input(env)
            # V22 inputs containing the new memory marker were already used in
            # production; they must keep their exact historical prompt contract.
            legacy = reliable_retrieval.freeze(env["current"], env["retrievalContext"], env["promptBudgetBytes"])
            validate_execution_input(legacy)
            raw = decision("finish"); raw["selectedFactIds"] = ["quote-1"]; return raw
        final, history, memories, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]),
            p, [], [], decide, lambda env: "input", 262144)
        baseline = final["businessEvidence"]["factIds"]
        self.assertTrue(baseline)
        self.assertTrue(set(baseline[:2]) <= {f["id"] for f in final["facts"]})
        validate_execution_input(freeze_execution_input(final, history, memories, retrieval_trace=trace))
        blocked = {"id": "blocked-report", "kind": "financial-report", "judgementEvidenceUsable": False}
        marked = {**p, "businessEvidence": {"factIds": [blocked["id"]]}, "analystMemory": {"items": [{"sourceFactIds": [blocked["id"]]}]}}
        self.assertEqual(set(), memory.protected_ids(marked, {blocked["id"]: blocked}))

    def test_completed_trace_does_not_reserve_another_round_and_block_fitting_sources(self):
        from test_price_investigation_flow import with_sources, decision
        from test_reliable_opinion_completion import packet as policies
        from digital_twin.modules.reasoning.contracts import source_packet_bytes
        p = with_sources(); p.update({k: v for k, v in policies().items() if k.endswith("Policy")})
        p.update(analystMemoryPolicy=memory.VERSION, frozenAuditMetadata="x" * 70000)
        original = next(f for f in p["facts"] if f["id"] == "actual-news")
        original["articleText"] += " exact dated original " * 250
        def decide(env, _):
            ids = env["retrievalContext"]["selectedFactIds"]
            if not env["retrievalContext"]["trace"]:
                return decision("read", ids, [{"tool": "read_fact", "category": "research", "factId": "actual-news"},
                                             {"tool": "read_fact", "category": "macro", "factId": "market-index"}])
            return decision("finish", list(dict.fromkeys(ids + ["actual-news", "market-index"])))
        model = Mock(side_effect=decide)
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]),
            p, [], [], model, lambda env: "input", 262144, max_rounds=2)
        self.assertEqual("ready", final["retrieval"]["status"]); self.assertEqual(2, model.call_count)
        self.assertEqual(original["articleText"], next(f for f in final["facts"] if f["id"] == "actual-news")["articleText"])
        self.assertLess(source_packet_bytes(final), 96000)
        self.assertGreater(source_packet_bytes(final) + reliable_retrieval.reserve(trace), 96000)
        validate_execution_input(freeze_execution_input(final, history, research, retrieval_trace=trace))
