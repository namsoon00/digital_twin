"""Silence is evidence-scoped; a selective critique never bypasses delivery review."""
from copy import deepcopy
from contextlib import nullcontext
import hashlib
import json
import os
import unittest
from unittest.mock import Mock, patch

from test_price_investigation_flow import with_sources, prepared, raw_result, reviewed
from test_notification_contribution import receipt, added_value
from digital_twin.modules.notifications.domain.delivered_insight_context import context
from digital_twin.modules.ai_orchestration.domain import observation_abstention as policy, abstention_prompt, abstention_review as review
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_review_input, freeze_repair_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, quality_block
from digital_twin.modules.ai_orchestration.domain.publication import publication_block
from digital_twin.modules.ai_orchestration.domain.diagnostic_progress import progress_state, progress_fingerprint
from digital_twin.modules.ai_orchestration.application.abstention_review import reconsider


def held():
    p = policy.annotate(prepared(with_sources()))
    p["notificationContributionPolicy"] = "notification-contribution-v1"
    p["deliveredNotificationContext"] = context([receipt(p)], p["accountId"], p["symbol"], p["capturedAt"])
    raw = raw_result()
    raw["notification"]["send"] = False
    raw["notificationContribution"] = added_value(p, "no-added-value")
    raw["judgmentChange"].update(kind="none", impact="unchanged", evidence=[])
    raw["priceExplanation"].update(status="cause-unconfirmed", sourceFactIds=[])
    result = {**validate_plan(raw, p), "input": p, "executionInputId": "author", "observedAt": p["capturedAt"]}
    result["quality"] = local_quality(result)
    return result, raw


def verdict(result, decision="retain"):
    return {"decision": decision, "reason": "이전 알림과 확인 가능한 원문을 비교해 추가 설명의 필요성을 검토했습니다.",
            "comparedReceiptIds": [r["jobId"] for r in result["input"]["deliveredNotificationContext"]["entries"]],
            "evidence": [{"factId": "actual-news", "field": "articleText", "period": "current"}]}


class ObservationAbstentionTests(unittest.TestCase):
    def test_writer_content_proof_distinguishes_metadata_missing_and_actual_passages(self):
        result, _ = held(); p = result["input"]
        self.assertEqual("delivered", p["sourceDelivery"]["groups"]["events"]["status"])
        fact = next(f for f in p["facts"] if f["id"] == "actual-news")
        body = fact.pop("articleText")
        self.assertTrue(fact["bodyRead"])
        self.assertEqual("metadata-only", policy.source_delivery(p)["groups"]["events"]["status"])
        fact["articleSourceReference"] = {"text": body, "passageTextHash": "wrong"}
        self.assertEqual([], policy.original_fields(fact))
        fact["articleSourceReference"]["passageTextHash"] = hashlib.sha256(body.encode()).hexdigest()
        self.assertEqual(["articleSourceReference.text"], policy.original_fields(fact))
        p["facts"].remove(fact)
        self.assertEqual("not-delivered", policy.source_delivery(p)["groups"]["events"]["status"])

    def test_silence_reasons_do_not_turn_missing_sources_or_failures_into_no_change(self):
        r, _ = held()
        self.assertEqual("cause-unconfirmed", policy.assessment(r)["reasonCode"])
        r["priceExplanation"]["status"] = "possible-explanation"
        self.assertEqual("no-material-change", policy.assessment(r)["reasonCode"])
        r["judgmentChange"]["impact"] = "insufficient"
        self.assertEqual("evidence-insufficient", policy.assessment(r)["reasonCode"])
        next(f for f in r["input"]["facts"] if f["id"] == "actual-news").pop("articleText")
        self.assertEqual("source-content-missing", policy.assessment(r)["reasonCode"])
        r["failure"] = {"stage": "query"}
        self.assertEqual("execution-failed", policy.assessment(r)["reasonCode"])
        self.assertNotIn("새로운 해석이 없", publication_block(r))
        legacy = {"notification": {"send": False, "reason": "자료 확인을 완료하지 못했습니다."}}
        self.assertEqual("not-requested", policy.assessment(legacy)["reasonCode"])

    def test_waiting_progress_stays_open_and_ignores_polling_time(self):
        r, _ = held()
        task = {"taskId": "held", "status": "completed", "updatedAt": "first", "result": r}
        state = progress_state(task)
        self.assertFalse(state["terminal"]); self.assertEqual("waiting", state["stage"])
        task["updatedAt"] = "later"
        self.assertEqual(progress_fingerprint(state), progress_fingerprint(progress_state(task)))
        r["priceExplanation"]["status"] = "possible-explanation"
        self.assertTrue(progress_state(task)["terminal"])

    def test_abstention_prompt_replays_author_repair_review_and_rejects_forged_delivery(self):
        result, raw = held(); p = result["input"]
        old = deepcopy(p); old.pop("abstentionPolicy"); old.pop("sourceDelivery")
        legacy = freeze_execution_input(old, [], []); saved = deepcopy(legacy)
        author = freeze_execution_input(p, [], [])
        self.assertEqual(abstention_prompt.PROMPT_VERSION, author["promptVersion"])
        for env in (author, freeze_review_input(result), freeze_repair_input(author, raw, ["보류 사유 확인"], "parent")):
            validate_execution_input(env)
            bad = deepcopy(env); bad["current"]["sourceDelivery"]["groups"]["events"]["status"] = "invented"
            with self.assertRaises(ValueError): validate_execution_input(bad)
        validate_execution_input(legacy); self.assertEqual(saved, legacy)

    def test_selective_candidates_require_original_usable_sources_or_verified_changes(self):
        r, _ = held()
        self.assertTrue(review.candidate(r))
        original = next(f for f in r["input"]["facts"] if f["id"] == "actual-news")
        original["judgementEvidenceUsable"] = False
        self.assertFalse(review.candidate(r))
        original["judgementEvidenceUsable"] = True
        original["publishedAt"] = "2099-01-01T00:00:00Z"
        self.assertFalse(review.candidate(r))
        r["input"]["priceInvestigationScope"]["candidateGroups"]["events"] = []
        self.assertFalse(review.candidate(r))
        r["input"]["followUpEvaluations"] = [{"transitionVerified": False, "effect": "invalidates", "conditionId": "c"}]
        self.assertFalse(review.candidate(r))
        r["input"]["followUpEvaluations"][0]["transitionVerified"] = True
        self.assertTrue(review.candidate(r))
        r["notification"]["send"] = True
        self.assertFalse(review.candidate(r))

    def test_existing_position_limit_crossing_requests_review_without_authorizing_delivery(self):
        r, _ = held(); p = r["input"]
        p["priceInvestigationScope"]["candidateGroups"]["events"] = []
        quote = next(f for f in p["facts"] if f["id"] == "quote-1")
        quote.update(positionWeight=31, strategyMaxPositionWeightPct=30)
        p["lastDeliveredNotification"] = {"facts": [{**quote, "positionWeight": 29}]}
        self.assertIn("existing-position-limit-crossed", [x["kind"] for x in review.candidate(r)["triggers"]])
        self.assertTrue(publication_block(r)); self.assertTrue(quality_block(r))
        quote["positionWeight"] = 29.5
        self.assertFalse(review.candidate(r))
        from ai_insight_fixtures import plan, ref
        raw = plan()
        raw.update(questionPurposes=[], externalInfluences=[],
            priceExplanation={"status": "cause-unconfirmed", "anchorFactId": "quote-1", "sourceFactIds": []},
            judgmentChange={"kind": "market", "impact": "new", "reason": "확인된 가격 위치와 보유 상황의 변화를 설명합니다.",
                            "evidence": [ref("currentPrice")]},
            notificationContribution=added_value(p, "material-risk-change"))
        # Unknown cause is not a universal veto; normal independent review is
        # still mandatory for the grounded risk explanation proposed by AI.
        candidate = {**validate_plan(raw, p), "input": p}
        self.assertEqual("awaiting-review", local_quality(candidate)["status"])
        self.assertTrue(quality_block(candidate))

    def test_compact_review_freezes_exact_sources_and_rejects_unseen_derived_or_foreign_citations(self):
        r, _ = held(); env = review.freeze(r)
        validate_execution_input(env)
        self.assertLessEqual(len(env["prompt"].encode()), review.MAX_BYTES)
        from digital_twin.modules.ai_orchestration.domain.token_budget import execution_pool
        self.assertEqual("completion", execution_pool("independent-observation", env["promptVersion"]))
        self.assertEqual("reconsider", review.accept(verdict(r, "reconsider"), env)["decision"])
        for extra in ({"evidence": []}, {"comparedReceiptIds": []},
                      {"evidence": [{"factId": "rate-event", "field": "value", "period": "current"}]},
                      {"evidence": [{"factId": "actual-news", "field": "title", "period": "current"}]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                review.accept({**verdict(r, "reconsider"), **extra}, env)
        env["draft"]["summary"] = "누락된 중요한 설명으로 임의 변경했습니다."
        with self.assertRaises(ValueError): validate_execution_input(env)

    def test_same_source_review_is_reused_across_ticks_and_new_source_reopens(self):
        r, raw = held(); envelope = freeze_execution_input(r["input"], [], [])
        reviewer, planner = Mock(return_value=verdict(r)), Mock()
        save = Mock(side_effect=lambda env: (validate_execution_input(env), "review")[1])
        reconsider(r, raw, envelope, "author", [], save, reviewer, planner)
        self.assertEqual(1, reviewer.call_count); planner.assert_not_called()
        next_result, next_raw = held()
        next(f for f in next_result["input"]["facts"] if f["id"] == "quote-1")["currentPrice"] += .01
        reconsider(next_result, next_raw, freeze_execution_input(next_result["input"], [], []), "next", [r], save, reviewer, planner)
        self.assertEqual(1, reviewer.call_count); self.assertTrue(next_result["abstentionReview"]["cached"])
        next(f for f in next_result["input"]["facts"] if f["id"] == "actual-news")["articleText"] += " 추가 원문."
        self.assertNotEqual(r["abstentionReview"]["key"], review.candidate(next_result)["key"])

    def test_reconsideration_rewrites_once_and_still_requires_publication_review(self):
        r, raw = held(); before = deepcopy(r["input"])
        author = freeze_execution_input(r["input"], [], [])
        rewritten = deepcopy(raw)  # The author may still retain silence.
        planner = Mock(return_value=rewritten); reviewer = Mock(return_value=verdict(r, "reconsider"))
        saved = []
        def save(env):
            validate_execution_input(env); saved.append(env); return "input-" + str(len(saved))
        _, _, _, changed = reconsider(r, raw, author, "author", [], save, reviewer, planner)
        self.assertTrue(changed); self.assertEqual((1, 1, 2), (reviewer.call_count, planner.call_count, len(saved)))
        self.assertFalse(r["notification"]["send"]); self.assertEqual(before, r["input"])
        self.assertTrue(quality_block(r)); self.assertEqual("reauthored", r["abstentionReview"]["resolution"])

    def test_review_failure_preserves_authored_work_and_caches_the_attempt(self):
        r, raw = held(); author = freeze_execution_input(r["input"], [], [])
        original = deepcopy(raw); reviewer = Mock(side_effect=RuntimeError("private vendor detail"))
        planner = Mock(); save = Mock(return_value="failed-review")
        reconsider(r, raw, author, "author", [], save, reviewer, planner)
        self.assertEqual(original, raw); self.assertEqual("failed", r["abstentionReview"]["status"])
        self.assertEqual("execution-failed", policy.assessment(r)["reasonCode"])
        self.assertNotIn("private vendor", str(r)); planner.assert_not_called()
        nxt, raw = held(); reconsider(nxt, raw, author, "next", [r], save, reviewer, planner)
        self.assertEqual(1, reviewer.call_count)
        self.assertEqual("execution-failed", policy.assessment(nxt)["reasonCode"])

    def test_runtime_wires_source_delivery_and_selective_review_before_completion(self):
        from digital_twin.modules.ai_orchestration.application.control import AIControlService
        from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession
        r, raw = held(); p = r["input"]; p["taskId"] = "runtime-abstention"
        subject = {k: p[k] for k in ("accountId", "symbol", "name", "worldId")}
        job = {**subject, "taskId": p["taskId"], "capability": "market-observe", "attempts": 1}
        store = Mock(); store.claim.return_value = job; store.keep_alive.return_value = nullcontext()
        store.memory.return_value = []; store.last_processing_outcome.return_value = {}; store.complete.return_value = True
        stored_inputs = []
        def save(_, env):
            validate_execution_input(env); stored_inputs.append(env); return "frozen-" + str(len(stored_inputs))
        store.save_execution_input.side_effect = save
        planner = Mock(return_value=raw); reviewer = Mock(return_value=verdict(r))
        service = AIControlService(store, lambda: [subject], lambda _: ObservationEvidenceSession(p, p["facts"]),
            planner, Mock(), lambda *_: [], reviewer=reviewer, read_planner=Mock())
        from test_market_explanation_flow import run_reader
        from test_price_investigation_flow import decision
        def retrieve(_, packet, *args, **kwargs):
            def read(env, __):
                ids = env["retrievalContext"]["selectedFactIds"]
                if not env["retrievalContext"]["trace"]:
                    return decision("read", ids, [{"tool": "read_fact", "category": "research", "factId": "actual-news"},
                                                 {"tool": "read_fact", "category": "macro", "factId": "market-index"}])
                if len(env["retrievalContext"]["trace"]) == 1:
                    return decision("read", ids, [{"tool": "read_fact", "category": "macro", "factId": "rate-event"},
                                                 {"tool": "read_fact", "category": "research", "factId": "company-exposure"}])
                return decision("finish", list(dict.fromkeys(ids + ["actual-news", "market-index", "rate-event", "company-exposure"])))
            return run_reader(packet, read)[0]
        with patch("digital_twin.modules.ai_orchestration.application.control.retrieve_working_evidence", side_effect=retrieve):
            service.run_once()
        store.fail.assert_not_called(); self.assertEqual(1, planner.call_count); self.assertEqual(1, reviewer.call_count)
        result = store.complete.call_args.args[1]
        self.assertEqual("cause-unconfirmed", result["abstention"]["reasonCode"])
        self.assertEqual("completed", result["abstentionReview"]["status"])
        self.assertEqual(abstention_prompt.PROMPT_VERSION, result["executionPromptVersion"])


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class AbstentionStorageTests(unittest.TestCase):
    def setUp(self):
        from test_external_influence_flow import ExternalInfluenceStorageTests
        ExternalInfluenceStorageTests.setUp(self)

    def test_abstention_proof_persists_with_typed_reason_and_cannot_be_delivery_approval(self):
        from test_ai_control import SUBJECT
        store = self.storage.control
        store.runtime_settings.update(aiControlBudgetEnabled="false", aiTokenBudgetEnabled="false")
        with store.transaction() as c:
            store.insert(c, {**SUBJECT, "taskId": "abstention-storage", "capability": "market-observe"})
        job = store.claim()
        result, raw = held(); p = result["input"]
        p.update(SUBJECT, taskId=job["taskId"], priceInvestigationRepeatPolicy="unconfirmed-price-source-wait-v1",
                 priceInvestigationSourceKey="same-source")
        p["sourceSnapshots"] = {p["worldId"]: p["sourceSnapshotId"]}
        for f in p["facts"]:
            f.update(symbol=p["symbol"], sourceWorldId=p["worldId"], sourceSnapshotId=p["sourceSnapshotId"])
        next(f for f in p["facts"] if f["id"] == "quote-1").update(positionWeight=29.99, strategyMaxPositionWeightPct=30)
        p["deliveredNotificationContext"] = context([receipt(p)], p["accountId"], p["symbol"], p["capturedAt"])
        def save(envelope):
            key = store.save_execution_input(job, envelope)
            call = store.begin_call("independent-observation", envelope["promptHash"], job["taskId"], key)
            store.finish_call(call)
            return key
        author = freeze_execution_input(p, [], [])
        result.update(executionInputId=save(author), executionPromptVersion=author["promptVersion"])
        reconsider(result, raw, author, result["executionInputId"], [], save, Mock(return_value=verdict(result)), Mock())
        result["abstention"] = policy.assessment(result)
        self.assertFalse(store.review_proof(result["abstentionReview"]["inputId"]))
        self.assertTrue(store.complete(job, result, []))
        self.assertEqual("cause-unconfirmed", result["publication"]["reasonCode"])
        self.assertEqual("recorded", result["publication"]["status"])
        memory = store.memory(p["accountId"], p["symbol"], "market-observe")
        self.assertEqual(result["abstentionReviewHistory"], memory[0]["abstentionReviewHistory"])
        from digital_twin.modules.ai_orchestration.domain.price_investigation_repeat import waiting
        from datetime import datetime
        self.assertNotIn("input", memory[0])
        self.assertTrue(waiting(p, memory[0], [], datetime.fromisoformat(result["observedAt"].replace("Z", "+00:00"))))
        changed = deepcopy(p); changed["priceInvestigationSourceKey"] = "new-source"
        self.assertFalse(waiting(changed, memory[0], [], datetime.fromisoformat(result["observedAt"].replace("Z", "+00:00"))))
        changed = deepcopy(p)
        next(f for f in changed["facts"] if f["id"] == "quote-1")["positionWeight"] = 30.01
        self.assertFalse(waiting(changed, memory[0], [], datetime.fromisoformat(result["observedAt"].replace("Z", "+00:00"))))
        with store.connect() as c:
            row = c.execute("SELECT result_json FROM ai_control_tasks WHERE task_id=%s", (job["taskId"],)).fetchone()
        self.assertEqual(result["abstention"], json.loads(row["result_json"])["abstention"])
