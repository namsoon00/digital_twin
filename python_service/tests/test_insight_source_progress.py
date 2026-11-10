"""Unchanged source polls must not buy another ungrounded market explanation."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from test_observation_abstention import held
from digital_twin.modules.ai_orchestration.domain import insight_progress as progress
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession
from digital_twin.modules.reasoning.domain.price_investigation_sources import build


def missing_sources():
    previous, _ = held()
    p = previous["input"]
    p["insightProgressPolicy"] = progress.VERSION
    next(f for f in p["facts"] if f["id"] == "actual-news").pop("articleText")
    next(f for f in p["facts"] if f["id"] == "market-index").pop("changeRate")
    p["sourceDelivery"] = progress.source_delivery(p)
    return previous


class InsightSourceProgressTests(unittest.TestCase):
    def test_company_wait_inventory_ignores_only_read_hash_churn_and_keeps_all_sources(self):
        p = missing_sources()['input']
        p['facts'][-1]['evidenceView'] = {'sourceFactHash': 'old-snapshot-hash', 'metrics': ['value']}
        before = deepcopy(p)
        def key(packet, extra=()):
            return ObservationEvidenceSession(packet, packet['facts'] + list(extra)).evidence_wait_identity()
        original = key(p)
        p['facts'][-1]['evidenceView']['sourceFactHash'] = 'new-snapshot-hash'
        p['coverage']['research']['inventoryHash'] = 'polling-only'
        self.assertEqual(original, key(p))
        self.assertNotEqual(before['facts'][-1]['evidenceView'], p['facts'][-1]['evidenceView'])
        self.assertEqual('new-snapshot-hash', p['facts'][-1]['evidenceView']['sourceFactHash'])
        for change in (lambda q: q['facts'][0].update(currentPrice=101),
                       lambda q: q['facts'][0].update(positionWeight=99),
                       lambda q: q['facts'][-1].update(articleText='정정된 공식 원문입니다.'),
                       lambda q: q['facts'][-1].update(judgementEvidenceUsable=False),
                       lambda q: q['facts'][-1]['evidenceView'].update(metrics=['different-field'])):
            changed = deepcopy(p); change(changed)
            self.assertNotEqual(original, key(changed))
        self.assertNotEqual(original, key(p, [{'id': 'unread-new-report', 'kind': 'company-financial-state', 'periodEnd': '2026-09-30'}]))

    def test_company_data_gap_handoff_only_replans_due_existing_cases(self):
        from digital_twin.modules.ai_orchestration.domain import company_evidence_wait as wait, research_work
        from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
        from test_research_work import case, plan, SUBJECT, CONTRACT
        value = case(); value['nextCheckAt'] = '2000-01-01T00:00:00Z'
        value['questionContract'] = deepcopy(CONTRACT)
        now = '2026-11-10T07:00:00Z'
        r = {'processingOutcome': {'companyEvidenceWait': {'version': wait.VERSION, 'key': 'captured-key'},
             'requiredEvidence': [{'question': '확인한 현금흐름의 부족한 주석을 확인합니다.', 'source': '동일 기간 공식 원문 주석'}]}}
        job = wait.handoff(SUBJECT, r, [{'kind': 'research-context', 'body': value}], now)
        self.assertTrue(wait.requested(job))
        changed = deepcopy(r); changed['processingOutcome']['requiredEvidence'][0]['question'] = '같은 원문의 부족 항목을 다시 확인합니다.'
        self.assertEqual(job['taskId'], wait.handoff(SUBJECT, changed, [value], now)['taskId'])
        new_source = deepcopy(value); new_source['researchSourceState'] = {'status': 'ready', 'fingerprints': ['new-original']}
        self.assertNotEqual(job['taskId'], wait.handoff(SUBJECT, r, [new_source], now)['taskId'])
        self.assertFalse(wait.requested({**job, 'accountId': 'foreign'}))
        self.assertIsNone(wait.handoff(SUBJECT, r, [{**value, 'nextCheckAt': '2099-01-01T00:00:00Z'}], now))
        self.assertIsNone(wait.handoff(SUBJECT, r, [{**value, 'researchAttempts': 6}], now))
        self.assertIsNone(wait.handoff(SUBJECT, r, [{**value, 'symbol': 'foreign'}], now))
        envelope = research_work.freeze(job, [value], job['researchGaps'])
        validate_execution_input(envelope)
        self.assertEqual(research_work.EXISTING_PROMPT_VERSION, envelope['promptVersion'])
        self.assertEqual(0, envelope['outputSchema']['properties']['questions']['maxItems'])
        research_work.validate_plan(plan(value), envelope)
        bad = plan(value); bad['questions'] = [{'question': '같은 질문을 새로 생성합니다.'}]
        with self.assertRaises(ValueError): research_work.validate_plan(bad, envelope)
        tampered = deepcopy(envelope); tampered['researchCases'][0]['caseId'] = 'other-question'
        with self.assertRaises(ValueError): validate_execution_input(tampered)

    def test_polling_the_same_macro_print_does_not_wake_paid_work_but_real_changes_do(self):
        p = missing_sources()["input"]
        p["facts"].append({"id": "monthly-print", "kind": "macro-print", "value": 3.0,
            "observationDate": "2026-09", "sourceAsOf": "2026-09", "sourceTimestampPresent": True,
            "asOf": "2026-11-01T09:00:00Z", "judgementEvidenceUsable": True})
        original = deepcopy(p)
        def key(packet, extra=()):
            return ObservationEvidenceSession(packet, packet["facts"] + list(extra)).progress_identity()
        before = key(p)
        p["facts"][-1]["asOf"] = "2026-11-01T10:00:00Z"
        self.assertEqual(before, key(p))
        p["coverage"]["macro"]["inventoryHash"] = "new-polling-hash"
        self.assertEqual(before, key(p))
        for delta in ({"value": 3.1}, {"sourceAsOf": "2026-10", "observationDate": "2026-10"},
                      {"judgementEvidenceUsable": False}, {"revision": "corrected"},
                      {"sourceTimestampPresent": False}):
            changed = deepcopy(p); changed["facts"][-1].update(delta)
            self.assertNotEqual(before, key(changed), delta)
        self.assertNotEqual(before, key(p, [{"id": "unread-new-original", "kind": "news-article", "articleText": "새 원문"}]))
        changed = deepcopy(p); changed["accountId"] = "other"
        self.assertNotEqual(before, key(changed))
        self.assertEqual("2026-11-01T09:00:00Z", original["facts"][-1]["asOf"])
        self.assertEqual("2026-11-01T10:00:00Z", p["facts"][-1]["asOf"])

    def test_only_identical_held_explanations_are_skipped_and_useful_changes_reopen(self):
        previous = missing_sources(); p = deepcopy(previous["input"])
        self.assertTrue(progress.assessment(p, previous)["skipAuthor"])
        for mutate in (
            lambda q: q["facts"][0].update(currentPrice=101),
            lambda q: q["facts"][0].update(positionWeight=31),
            lambda q: q["deliveredNotificationContext"].update(fingerprint="new-receipt"),
            lambda q: q.update(reviewedQuestionContext={"answer": "new reviewed answer"}),
            lambda q: q.update(followUpEvaluations=[{"transitionVerified": True}]),
            lambda q: next(f for f in q["facts"] if f["id"] == "actual-news").update(articleText="확인한 새 원문입니다."),
            lambda q: next(f for f in q["facts"] if f["id"] == "market-index").update(changeRate=-1.0),
        ):
            changed = deepcopy(p); mutate(changed)
            self.assertFalse(progress.assessment(changed, previous)["skipAuthor"])
        self.assertFalse(progress.assessment(p, {})["skipAuthor"])
        rejected = deepcopy(previous); rejected["quality"]["status"] = "rejected"
        self.assertFalse(progress.assessment(p, rejected)["skipAuthor"])
        self.assertFalse(progress.assessment(p, previous, [{"kind": "business-thesis", "reviewDue": True}])["skipAuthor"])
        # A persisted compact memory retains the same scoped comparison.
        compact = {k: v for k, v in previous.items() if k != "input"}
        compact["marketProgressMemory"] = progress.memory(previous["input"])
        self.assertTrue(progress.assessment(p, compact)["skipAuthor"])

    def test_gap_handoff_is_stable_scoped_and_does_not_send_numeric_gaps_to_document_research(self):
        previous = missing_sources(); p = previous["input"]
        state = progress.assessment(p, previous)
        self.assertEqual({"event-original", "market-comparison"}, {n["kind"] for n in state["needs"]})
        subject = {k: p[k] for k in ("accountId", "symbol", "worldId", "name")}
        job = progress.research_job(subject, state, "first")
        self.assertTrue(progress.source_gap_request(job))
        self.assertIn("최근 사업 변동의 원문", job["researchGaps"][0])
        self.assertEqual(job["taskId"], progress.research_job(subject, state, "later")["taskId"])
        state["needs"][-1]["factIds"] = ["new-benchmark"]
        self.assertEqual(job["taskId"], progress.research_job(subject, state, "later")["taskId"])
        self.assertFalse(progress.source_gap_request({**job, "accountId": "other"}))
        self.assertFalse(progress.source_gap_request({**job, "researchGaps": ["unrelated"]}))
        next(f for f in p["facts"] if f["id"] == "actual-news")["articleText"] = "실제 원문"
        market_only = progress.assessment(p, previous)
        self.assertIsNone(progress.research_job(subject, market_only, "later"))
        market = next(f for f in p["facts"] if f["id"] == "market-index")
        for value in (float("nan"), True, "-1%"):
            market["changeRate"] = value
            self.assertEqual("market-comparison", progress.source_needs(p)[0]["kind"])
        market.update(changeRate=-1.0, judgementEvidenceUsable=False)
        self.assertTrue(progress.source_needs(p))

    def test_original_text_wins_over_richer_metadata_but_historical_selection_replays(self):
        p = missing_sources()["input"]
        common = {"kind": "news-article", "evidenceCategory": "research", "publishedAt": p["capturedAt"], "url": "https://issuer.example/news"}
        facts = [{**common, "id": "metadata", "bodyRead": True, "summary": "generated " * 100},
                 {**common, "id": "original", "articleText": "원래 기사 본문"}]
        self.assertEqual(["metadata"], build(facts, p)[2]["candidateGroups"]["events"])
        self.assertEqual(["original"], build(facts, p, prefer_original=True)[2]["candidateGroups"]["events"])
        facts[0]["articleSourceReference"] = {"text": "unverified", "passageTextHash": "wrong"}
        self.assertEqual(["original"], build(facts, p, prefer_original=True)[2]["candidateGroups"]["events"])
        self.assertEqual(["metadata"], build(facts, p)[2]["candidateGroups"]["events"])

    def test_new_gap_planning_runs_once_even_when_unrelated_cases_are_parked(self):
        from test_ai_control import AIControlTests
        from test_research_work import case
        from digital_twin.modules.ai_orchestration.domain import research_repeat
        old = case(); research_repeat.initialize(old); research_repeat.remember(old, "planned")
        previous = missing_sources(); p = previous["input"]
        subject = {k: p[k] for k in ("accountId", "symbol", "worldId", "name")}
        old.update(subject)
        job = progress.research_job(subject, progress.assessment(p, previous), "now")
        service, store, planner = AIControlTests().runner(subjects=lambda: [subject], research_plan_memory=lambda *_: [deepcopy(old)])
        store.claim.return_value = {**job, "attempts": 1}; planner.return_value = {"reviews": [], "questions": []}
        self.assertEqual("completed", service.run_once()["status"])
        store.fail.assert_not_called(); self.assertEqual(1, planner.call_count)
        frozen = store.save_execution_input.call_args.args[1]
        self.assertFalse(frozen["researchCases"][0]["planDue"])
        self.assertEqual(job["researchGaps"], frozen["gaps"])
        self.assertTrue(research_repeat.admission(old, "planned"))

    def test_runtime_keeps_retrieval_audit_and_parks_before_author_then_before_the_next_read(self):
        from test_ai_control import AIControlTests
        from digital_twin.modules.ai_orchestration.domain.planning import stamp
        previous = missing_sources(); p = deepcopy(previous["input"])
        p["capturedAt"] = stamp()
        from digital_twin.modules.reasoning.contracts import quote_clock_assessment
        p["quoteAssessment"] = quote_clock_assessment(p["facts"], p["capturedAt"])
        subject = {k: p[k] for k in ("accountId", "symbol", "worldId", "name")}
        p["taskId"] = "source-wait"
        service, store, author = AIControlTests().runner(
            subjects=lambda: [subject], evidence=Mock(return_value=ObservationEvidenceSession(p, p["facts"])), read_planner=Mock())
        store.claim.return_value = {**subject, "taskId": p["taskId"], "capability": "market-observe", "attempts": 1}
        store.memory.return_value = [previous]; store.last_processing_outcome.return_value = {}
        service.research_memory = lambda *_: []
        def retrieved(session, packet, history, research, *args, **kwargs):
            selected = {**packet, "facts": deepcopy(previous["input"]["facts"]),
                "deliveredNotificationContext": previous["input"]["deliveredNotificationContext"],
                "reviewedQuestionContext": previous["input"].get("reviewedQuestionContext", {}),
                "priceInvestigationScope": previous["input"]["priceInvestigationScope"],
                "retrieval": {"status": "ready"}}
            return selected, history, research, [{"inputId": "read-audit"}]
        target = "digital_twin.modules.ai_orchestration.application.control.retrieve_working_evidence"
        with patch(target, side_effect=retrieved) as read:
            service.run_once()
            store.fail.assert_not_called(); author.assert_not_called()
            self.assertEqual(1, read.call_count)
            result, children = store.complete.call_args.args[1:]
            self.assertNotIn("summary", result)
            self.assertEqual(["read-audit"], result["retrievalInputIds"])
            state = result["processingOutcome"]
            self.assertEqual("insight-source-gaps", state["reasonCode"])
            self.assertEqual(1, sum(c["capability"] == "research-plan" for c in children))
            store.last_processing_outcome.return_value = state
            service.run_once()
            store.fail.assert_not_called(); author.assert_not_called()
            self.assertEqual(1, read.call_count)
            self.assertEqual("unchanged-wait", store.complete.call_args.args[1]["status"])
            service.market_brain_memory = lambda *_: [{"kind": "business-thesis", "caseId": "due-thesis", "reviewDue": True}]
            def due_read(session, packet, history, research, *args, **kwargs):
                return {**packet, "retrieval": {"status": "deferred", "missingEvidence": ["사업 가설의 새 원문 확인"]}}, history, research, []
            read.side_effect = due_read
            service.run_once()
            store.fail.assert_not_called(); self.assertEqual(2, read.call_count)


if __name__ == "__main__": unittest.main()
