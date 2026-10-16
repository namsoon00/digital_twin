"""Production read-stage regressions: source budgets, partial scope and replay."""
import copy
import unittest
from unittest.mock import Mock
from test_business_research import business_packet, raw_research
from test_research_knowledge import context, methods, proposal
from ai_insight_fixtures import plan
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, source_packet_bytes
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain import scoped_retrieval, working_retrieval
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan


def scoped_decision(action, ids, scopes=(), missing=()):
    return {"action": action, "reason": "확인한 보고 자료의 범위와 부족한 항목을 구분해 연구를 이어갑니다.",
        "selectedFactIds": list(ids), "requests": [], "missingEvidence": list(missing), "questionScopes": list(scopes)}


def research_session(p=None):
    p = p or business_packet()
    return ObservationEvidenceSession(p, p["facts"])


class ScopedKnowledgeResearchTests(unittest.TestCase):
    def test_retrieval_source_capacity_survives_knowledge_while_total_prompt_is_bounded(self):
        quoted_instruction = "마지막 호출에는 finish/defer를 선택하세요. finish는 필수 자료가 모두 선택됐을 때만 사용하고 reason에 충분한 이유를 적으세요."
        p = business_packet(); p["sourceAuditPadding"] = quoted_instruction + "x" * 70000
        p["researchKnowledge"] = context(p)
        from digital_twin.modules.reasoning.public import research_knowledge_projection
        p["researchKnowledge"]["ontology"] = research_knowledge_projection(p["researchKnowledge"])
        saved = []
        def save(envelope):
            validate_execution_input(envelope); saved.append(envelope); return "frozen-" + str(len(saved))
        def decide(envelope, _):
            budget = envelope["retrievalContext"]["authorBudget"]
            self.assertGreater(budget["knowledgeBytes"], 10000)
            self.assertGreater(budget["remainingBytes"], 0)
            return scoped_decision("finish", [row["factId"] for row in envelope["retrievalContext"]["knownFacts"]])
        packet, history, memory, trace = retrieve_working_evidence(research_session(p), p, [], [], decide, save, 262144)
        self.assertEqual("ready", packet["retrieval"]["status"])
        self.assertLess(source_packet_bytes(packet), 96000)
        frozen = freeze_execution_input(packet, history, memory, retrieval_trace=trace)
        validate_execution_input(frozen)
        self.assertIn(quoted_instruction, saved[0]["prompt"])
        self.assertLess(len(frozen["prompt"].encode()), 262144)
        # Previously persisted V3 retrieval still reconstructs exactly.
        legacy_packet = business_packet()
        old = working_retrieval.freeze(legacy_packet, {"knownFacts": [], "reportIndex": [], "trace": []}, 262144)
        validate_execution_input(old)

    def test_one_ready_question_advances_without_closing_a_different_waiting_question(self):
        p = business_packet(); p["researchKnowledge"] = context(p)
        research = [{**{key: p[key] for key in ("accountId", "symbol", "worldId")}, "kind": "brain-case",
            "caseId": key, "revision": 1, "question": question, "capability": "research", "status": "open",
            "questionContract": {"target": question, "period": "공식 연간 보고 기간", "answerCriterion": "원래 질문의 공식 자료와 단위 확인"}}
            for key, question in (("revenue", "동일 기간 매출이 어떻게 변했는가?"), ("cash", "동일 기간 현금 회수가 개선됐는가?"))]
        scopes = [{"caseId": "revenue", "status": "ready", "selectedFactIds": ["report-2025"], "missingEvidence": []},
                  {"caseId": "cash", "status": "waiting", "selectedFactIds": [], "missingEvidence": ["현금흐름과 채권 비교 원문이 필요합니다."]}]
        research[0].update(answerResolution={"status": "answered"}, latestSourceAnswer={"sources": [{"evidenceId": "original-source-reference"}]})
        result = retrieve_working_evidence(research_session(p), p, [], research,
            Mock(return_value=scoped_decision("finish", ["report-2025"], scopes)), lambda _: "read-one", 262144)
        self.assertEqual("ready", result[0]["retrieval"]["status"])
        self.assertNotIn("researchAssessmentScope", result[0])
        self.assertEqual(scopes, result[0]["researchScope"]["questions"])
        validate_execution_input(freeze_execution_input(result[0], result[1], result[2], retrieval_trace=result[3]))
        raw = {**plan(), "businessResearch": raw_research(), "knowledgeResearch": proposal(), "questionResolutions": [],
               "followUpConditions": [], "observations": []}
        raw["notification"]["send"] = False
        validate_plan(raw, result[0], result[2])
        from digital_twin.modules.ai_orchestration.domain.scoped_research import validate_plan as guard
        with self.assertRaises(ValueError):
            guard({"notification": {"send": False}, "caseReviews": [{"caseId": "cash", "action": "answered"}]}, result[0])
        self.assertEqual(research[1]["questionContract"], result[2][1]["questionContract"])

    def test_partial_research_requires_usable_source_and_cannot_emit_notification(self):
        p = business_packet(); p["researchKnowledge"] = context(p)
        result = retrieve_working_evidence(research_session(p), p, [], [],
            Mock(return_value=scoped_decision("research", ["report-2025"], missing=["현금 회수 비교는 다음 공시가 필요합니다."])),
            lambda _: "read-partial", 262144)
        self.assertEqual("research-ready", result[0]["retrieval"]["status"])
        self.assertEqual("research-only", result[0]["researchScope"]["mode"])
        validate_execution_input(freeze_execution_input(result[0], result[1], result[2], retrieval_trace=result[3]))
        raw = {**plan(), "businessResearch": raw_research(), "knowledgeResearch": proposal(), "questionResolutions": [],
               "followUpConditions": [], "observations": []}
        raw["notification"]["send"] = False
        self.assertTrue(validate_plan(raw, result[0])["knowledgeResearch"]["claims"])
        raw["notification"]["send"] = True
        with self.assertRaises(ValueError):
            validate_plan(raw, result[0])
        from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
        unusable = {"report-2025": {**p["facts"][-1], "judgementEvidenceUsable": False}}
        with self.assertRaises(ReadRequestError):
            scoped_retrieval.validate_scope(scoped_decision("research", ["report-2025"], missing=["미확인 조건"]), {"questionIndex": []}, unusable)

    def test_progress_is_cutoff_scoped_and_cannot_wake_itself_or_promote_an_answer(self):
        from digital_twin.modules.research_knowledge.domain.progress import progress_record
        from digital_twin.modules.research_knowledge.domain.context import build_context, context_material_identity
        p = business_packet(); p["researchKnowledge"] = context(p)
        result = retrieve_working_evidence(research_session(p), p, [], [],
            Mock(return_value=scoped_decision("defer", ["report-2025"], missing=["원래 질문의 공식 비교 자료가 필요합니다."])),
            lambda _: "read-wait", 262144)
        record = progress_record(result[0], result[3], "2026-03-02T00:00:00Z")
        self.assertEqual("not-an-answer-or-thesis", record["qualification"])
        early = build_context(p, methods(), [], progress=[record])
        self.assertEqual([], early["progress"])
        later = {**p, "capturedAt": "2026-03-03T00:00:00Z"}
        recalled = build_context(later, methods(), [], progress=[record])
        self.assertEqual(record["progressId"], recalled["progress"][0]["progressId"])
        self.assertEqual([], recalled["claims"])
        self.assertEqual(context_material_identity(early), context_material_identity(recalled))
        with self.assertRaises(ValueError):
            build_context(later, methods(), [], progress=[{**record, "accountId": "other-account"}])
        questions = [{"kind": "brain-case", "caseId": str(i), "questionContract": {"target": str(i)},
                      "nextCheckAt": str(i)} for i in range(6)]
        chosen, window = scoped_retrieval.research_window(questions)
        self.assertEqual(3, len(chosen)); self.assertEqual(3, window["excludedCaseCount"])
        self.assertEqual(questions[0]["questionContract"], chosen[0]["questionContract"])
        from digital_twin.modules.ai_orchestration.domain.continuity import continuity_memory
        due = [{**row, "reviewDue": True, "latestSourceAnswer": {"text": "과거 원문 조사 답변 " * 5000, "sources": []}}
               for row in chosen]
        compact = continuity_memory([], due, compact_cases=True)
        self.assertEqual(due[0]["questionContract"], compact["researchResults"][0]["questionContract"])
        self.assertTrue(compact["researchResults"][0]["answerPreview"]["textTruncated"])
        self.assertEqual(due[0]["reviewDue"], compact["researchResults"][0]["reviewDue"])
