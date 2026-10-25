"""Bounded scheduling, honest closure and graph portfolio evidence regressions."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain.work_attention import attention, select, subject_context
from digital_twin.modules.ai_orchestration.domain.question_progress import question_progress, task_wait
from digital_twin.modules.ai_orchestration.domain.question_closure import summarize
from digital_twin.modules.reasoning.domain.observation_evidence import validate_evidence_packet
from digital_twin.modules.reasoning.application.observation_evidence import ObservationEvidenceReader
from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
from digital_twin.modules.reasoning.infrastructure.observation_inventory import scoped_candidates
from test_ai_control import SUBJECT

NOW = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)


def subject(symbol="TEST", held=True, weight=40):
    position = SimpleNamespace(account_value_krw=weight, market_value_krw=weight,
                               source="holding" if held else "watchlist", quantity=2 if held else 0)
    return {**SUBJECT, "symbol": symbol, "workContext": subject_context(position, 100, NOW.isoformat())}


class CentralWorkAttentionTests(unittest.TestCase):
    def test_fair_turns_and_important_work_preserve_scoping_and_ignore_stale_context(self):
        watch, held = subject("WATCH", False), subject()
        jobs = [{**s, "taskId": s["symbol"]} for s in (watch, held)]
        i, why = select(jobs, [watch, held], {}, {}, "research", NOW)
        self.assertEqual(0, i)
        cursor = {"laneDispatchCounts": {"research": 1}}
        i, why = select(jobs, [watch, held], {}, cursor, "research", NOW)
        self.assertEqual(1, i); self.assertEqual("work-order-only", why["authority"])
        for patch in ({"accountId": "other"}, {"worldId": "other"},
                      {"workContext": {**held["workContext"], "observedAt": "2026-01-01T00:00:00Z"}},
                      {"workContext": {**held["workContext"], "observedAt": "2099-01-01T00:00:00Z"}}):
            self.assertFalse(attention(jobs[1], {**held, **patch}, {}, NOW)["held"])

    def test_partial_answer_and_counterevidence_rank_only_the_owned_question(self):
        job = {**SUBJECT, "taskId": "work", "brainCaseId": "case"}
        case = {**SUBJECT, "caseId": "case", "status": "waiting", "latestSourceAnswer": {"status": "partial"},
                "questionAssessment": {"researchReviewInputId": "reviewed-input", "judgmentImpact": {"effect": "weakens"}}}
        value = attention(job, {}, case, NOW)
        self.assertTrue(value["partialAnswer"]); self.assertTrue(value["counterEvidenceReview"])
        self.assertFalse(attention(job, {}, {**case, "worldId": "other"}, NOW)["partialAnswer"])
        self.assertFalse(attention(job, {}, {**case, "status": "answered"}, NOW)["counterEvidenceReview"])

    def test_partial_closure_does_not_become_a_completed_answer_or_delivery(self):
        case = {**SUBJECT, "caseId": "case", "status": "waiting", "capability": "research",
                "latestSourceAnswer": {"status": "partial", "text": "확인된 사업 내용", "sources": [{}],
                                       "missingRequirements": ["같은 기간 현금흐름"]},
                "researchPlan": {"resumeWhen": "다음 공식 보고서가 발표되면"}}
        original = deepcopy(case)
        case["workflow"] = question_progress(case, {}, {"lastFailureCategory": "quota", "blockedUntil": "2026-10-26T00:00:00Z"}, NOW)
        self.assertFalse(case["workflow"]["answerComplete"])
        self.assertEqual(["같은 기간 현금흐름"], case["workflow"]["closure"]["missingRequirements"])
        measured = summarize([case])
        self.assertEqual(1, measured["partialAnswersRetained"])
        self.assertEqual(1, measured["providerWaiting"])
        self.assertIsNone(measured["investmentAccuracy"])
        workflow = case["workflow"]
        workflow["publication"] = {"jobId": "notice", "status": "done"}
        self.assertEqual(0, summarize([case])["receiptVerifiedNotifications"])
        workflow["publication"]["receipt"] = {"source": "transport-receipt", "deliveredAt": NOW.isoformat()}
        self.assertEqual(1, summarize([case, deepcopy(case)])["receiptVerifiedNotifications"])
        del case["workflow"]; self.assertEqual(original, case)
        limited = {**case, "researchAttempts": 6, "researchRepeat": {"version": "research-repeat-v1"}}
        closed = question_progress(limited, {}, {}, NOW)["closure"]
        self.assertIn("한도 도달", closed["nextStep"])
        self.assertIn("다시 실행되지는 않습니다", closed["resumeWhen"])
        self.assertEqual(case["latestSourceAnswer"], limited["latestSourceAnswer"])
        resumed = task_wait({"status": "pending", "lastError": "ai-execution:recovery-wait"},
            {"lastSuccessAt": "2026-10-25T09:00:00Z", "lastFailureAt": "2026-10-25T08:00:00Z"}, NOW)
        self.assertEqual("resuming", resumed["status"])
        self.assertEqual("task-queue", resumed["scope"])

    def test_native_portfolio_inventory_is_scoped_and_frozen_without_inventing_a_trade(self):
        repo = Mock()
        repo.active_abox_metadata.return_value = {"status": "ok", "aboxSnapshotId": "snapshot", "accountId": SUBJECT["accountId"]}
        repo.active_abox_snapshot_id.return_value = "snapshot"
        repo.active_abox_members_clause.return_value = ""
        quote = {"id": "quote", "kind": "stock", "symbol": "TEST", "currentPrice": 100}
        risk = {"id": "risk", "kind": "portfolio-risk-snapshot", "sampleCount": 20, "dataState": "partial"}
        def read(query, columns, label):
            values = {"subject": [quote], "portfolio": [risk]}.get(label.rsplit(".", 1)[-1], [])
            if label.endswith("stock-storage"):
                return [{"id": "quote", "storageId": "quote-storage"}]
            return [{"id": x["id"], "kind": x["kind"], "label": x["id"], "json": json.dumps(x)} for x in values]
        repo.read_rows.side_effect = read
        session = ObservationEvidenceReader(TypeDBObservationEvidenceSource(repo), clock=lambda: NOW).capture_session(SUBJECT)
        packet = session.packet(); validate_evidence_packet(packet)
        fact = next(x for x in packet["facts"] if x["kind"] == "portfolio-risk-snapshot")
        self.assertEqual("partial", fact["dataState"])
        self.assertEqual("snapshot", fact["sourceSnapshotId"])
        self.assertNotIn("action", fact)
        self.assertEqual(1, packet["coverage"]["company"]["included"])
        risk["accountId"] = "other-account"
        with self.assertRaisesRegex(ValueError, "account mismatch"):
            ObservationEvidenceReader(TypeDBObservationEvidenceSource(repo)).capture_session(SUBJECT)

    def test_scoped_portfolio_query_stays_in_native_manifest_and_bounds(self):
        repo = Mock(); repo.read_rows.return_value = []
        scoped_candidates(repo, SUBJECT["worldId"], "TEST", {"scopeGenerationIds": {}})
        query = next(c.args[0] for c in repo.read_rows.call_args_list if c.kwargs["label"].endswith("portfolio"))
        self.assertIn(SUBJECT["worldId"], query); self.assertIn("limit 2001", query)
        repo.reset_mock()
        scoped_candidates(repo, "market:shared:global", "TEST", {"scopeGenerationIds": {}})
        self.assertFalse(any(c.kwargs["label"].endswith("portfolio") for c in repo.read_rows.call_args_list))


if __name__ == "__main__":
    unittest.main()
