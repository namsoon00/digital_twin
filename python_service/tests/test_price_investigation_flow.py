"""Fixed price question, bounded event survey, exact source proof and replay."""
from copy import deepcopy
from datetime import timedelta
import os
import unittest
from unittest.mock import Mock, patch

import test_external_influence_flow as external
import test_market_explanation_flow as old
from test_scoped_knowledge_research import scoped_decision
from digital_twin.modules.ai_orchestration.domain import price_investigation as policy
from digital_twin.modules.ai_orchestration.domain import price_investigation_prompt as author
from digital_twin.modules.ai_orchestration.domain import price_investigation_retrieval as reader
from digital_twin.modules.ai_orchestration.domain.evidence_purpose import annotate
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_review_input, freeze_repair_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import accept_review, quality_block
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError, quote_clock_assessment
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
from digital_twin.modules.reasoning.domain.observation_event_index import instant
from digital_twin.modules.reasoning.domain.price_investigation_sources import build, price_move_anchor

ORIGINAL_RAW = external.raw_result


def packet():
    return {**old.packet(), "priceInvestigationPolicy": policy.VERSION}


def prepared(p):
    p["facts"], p["coverage"] = select_evidence(p["facts"])
    anchor, _, scope = ObservationEvidenceSession(p, p["facts"]).price_investigation()
    p = {**p, "priceMoveAnchor": anchor, "priceInvestigationScope": scope}
    ids = {f["id"] for f in p["facts"]}
    p["priceInvestigationCoverage"] = policy.coverage(scope, ids, ids)
    return p


def raw_result():
    result = {**ORIGINAL_RAW(), "priceExplanation": {"status": "possible-explanation",
              "anchorFactId": "quote-1", "sourceFactIds": ["rate-event"]}}
    result["summary"] = "관측한 가격을 기준으로, 금리 변화가 변동금리 차입 비용에 전달될 가능성을 검토합니다."
    result["claimEvidence"]["summary"].append(external.ref("currentPrice"))
    return result


def reviewed(p, raw=None):
    result = old.reviewed(p, raw or raw_result())
    checked = result["quality"]["review"]
    checked["priceInvestigation"] = {"supported": True,
        "reason": "기준 시세 시점과 금리 관측, 기업 노출의 조건부 설명 및 대안을 확인했습니다."}
    result["quality"] = accept_review(result, checked, "price-investigation-review")
    return result


def decision(action, ids, requests=()):
    return {**scoped_decision(action, ids), "purpose": "source-dated", "blocker": "none",
            "optionalEvidence": [], "requests": list(requests)}


def with_sources():
    p = packet()
    common = {k: p["facts"][0][k] for k in ("sourceWorldId", "sourceSnapshotId", "sourceAsOf")}
    p["facts"] += [{**common, "id": "actual-news", "kind": "news-article", "evidenceCategory": "research",
        "publishedAt": p["capturedAt"], "title": "최근 사업 변동의 원문", "bodyRead": True,
        "judgementEvidenceUsable": True, "articleText": "기업의 최근 수요와 원가에 관한 독립적인 원문입니다."},
        {**common, "id": "market-index", "kind": "benchmark-index", "evidenceCategory": "macro",
         "changeRate": -1.0, "judgementEvidenceUsable": True}]
    p["facts"], p["coverage"] = select_evidence(p["facts"])
    return p


class PriceInvestigationTests(unittest.TestCase):
    def test_discovery_preserves_real_events_over_polling_financial_duplicates(self):
        p = packet(); at = instant(p["capturedAt"])
        def event(key, **values):
            return {"id": key, "kind": "research-evidence", "evidenceCategory": "research",
                    "publishedAt": (at-timedelta(hours=1)).isoformat(), "title": "원문", **values}
        facts = [event("news", kind="news-article", url="https://issuer.example/release"),
                 event("original", url="https://issuer.example/release", bodyRead=True),
                 event("old", publishedAt=(at-timedelta(days=300)).isoformat()),
                 event("future", publishedAt=(at+timedelta(days=1)).isoformat()),
                 event("price", tboxClasses=["PriceObservation"]),
                 event("financial", reportedValues={"revenue": 1}, publicationTimeKnown=False),
                 event("aggregate", sourceKind="unofficial-yahoo-finance-wrapper", tboxClasses=["EarningsEvent"]),
                 event("unknown", publicationTimeKnown=False)]
        before = deepcopy(facts)
        _, index, scope = build(facts, p)
        self.assertEqual(["original", "unknown"], [row["factId"] for row in index["events"]])
        self.assertFalse(index["events"][1]["timeKnown"])
        self.assertEqual("metadata-or-derived-summary; original-text-not-present", index["events"][0]["sourceContent"])
        self.assertEqual(1, index["duplicateEvents"])
        self.assertEqual(2, index["excludedEvents"]["financial-snapshot"])
        self.assertFalse(index["discoveryWindowIsReturnPeriod"])
        self.assertEqual(2, len(scope["candidateGroups"]["events"]))
        self.assertEqual(before, facts)

    def test_early_financial_finish_gets_one_bounded_correction_then_real_reads(self):
        p = with_sources()
        def model(env, _):
            context = env["retrievalContext"]; ids = context["selectedFactIds"]
            if not context["trace"]:
                # Neither group has been read; a headline in the index is not proof.
                return decision("finish", ids)
            if len(context["trace"]) == 1:
                return decision("read", ids, [
                    {"tool": "read_fact", "category": "research", "factId": "actual-news"},
                    {"tool": "read_fact", "category": "macro", "factId": "market-index"}])
            return decision("finish", ids + ["actual-news", "market-index"])
        (final, h, r, trace), saved = old.run_reader(p, model)
        self.assertEqual(3, len(saved))
        self.assertEqual("invalid-request", trace[0]["status"])
        self.assertEqual("ready", final["retrieval"]["status"])
        self.assertEqual(reader.VERSION, saved[0]["promptVersion"])
        for group in ("events", "market"):
            self.assertEqual("source-read", final["priceInvestigationCoverage"][group]["status"])
        self.assertEqual({row["id"]: row for row in p["facts"] if row["id"] in {"actual-news", "market-index"}},
                         {row["id"]: row for step in trace for read in step["reads"] for row in read["result"].get("facts", [])})
        validate_execution_input(freeze_execution_input(final, h, r, retrieval_trace=trace))
        from digital_twin.modules.ai_orchestration.domain.question_evidence import checkpoints
        self.assertEqual([], checkpoints(final, trace))

    def test_old_last_close_remains_a_dated_anchor_not_current_state_or_newer_receipt(self):
        p = packet(); quote = next(f for f in p["facts"] if f["id"] == "quote-1")
        quote.update(sourceAsOf=(instant(p["capturedAt"])-timedelta(days=2)).isoformat(),
                     freshnessReferenceState="last-close")
        p["quoteAssessment"] = quote_clock_assessment(p["facts"], p["capturedAt"])
        p = annotate(p)
        p["deliveredNotificationContext"] = {"deliveredAt": p["capturedAt"], "text": "더 늦게 발송한 시세 알림"}
        (final, _, _, _), saved = old.run_reader(p, lambda env, _: decision("finish", env["retrievalContext"]["selectedFactIds"]))
        self.assertEqual("ready", final["retrieval"]["status"])
        anchor = final["priceMoveAnchor"]["quotes"][0]
        self.assertTrue(anchor["datedReferenceEligible"])
        self.assertFalse(anchor["currentStateEligible"])
        self.assertEqual(quote["sourceAsOf"], anchor["sourceAsOf"])
        self.assertEqual("source-dated", final["analysisPurpose"])
        context = saved[0]["retrievalContext"]
        with self.assertRaises(ReadRequestError):
            reader.validate_scope({**decision("finish", context["selectedFactIds"]), "purpose": "current-state"},
                                  context, {f["id"]: f for f in saved[0]["current"]["facts"]})

    def test_absent_index_is_distinct_from_unread_and_does_not_create_fake_proof(self):
        p = packet(); p["facts"] = [f for f in p["facts"] if f["id"] != "company-exposure"]
        p = prepared(p)
        row = p["priceInvestigationCoverage"]["events"]
        self.assertEqual("no-candidate-in-captured-index", row["status"])
        self.assertEqual([], row["readFactIds"])
        scope = {"candidateGroups": {"events": ["unread"], "market": []}}
        self.assertEqual("not-read", policy.coverage(scope, {}, set())["events"]["status"])
        self.assertEqual(["events"], policy.completion_gaps(scope, {}, set()))

    def test_possible_explanation_needs_cited_conditional_sources_and_review(self):
        p = prepared(packet()); raw = raw_result()
        result = reviewed(p, raw)
        self.assertEqual("accepted", result["quality"]["status"], result["quality"])
        broken = deepcopy(raw); broken["externalInfluences"] = []
        with self.assertRaises(ValueError): validate_plan(broken, p)
        detached = deepcopy(raw); detached["claimEvidence"]["summary"].pop()
        with self.assertRaises(ValueError): validate_plan(detached, p)
        with self.assertRaises(ValueError):
            policy.validate(raw["priceExplanation"], p, {**result, "externalInfluences": [{**result["externalInfluences"][0],
                "exposureEvidence": [external.ref("analysisSummary", fact_id="company-exposure")]}]})
        broken["priceExplanation"] = {"status": "cause-unconfirmed", "anchorFactId": "quote-1", "sourceFactIds": []}
        broken["notification"]["send"] = False
        self.assertEqual("cause-unconfirmed", validate_plan(broken, p)["priceExplanation"]["status"])
        altered = deepcopy(result); altered["priceExplanation"]["status"] = "cause-unconfirmed"
        self.assertTrue(quality_block(altered))
        altered = deepcopy(result); altered["quality"]["review"].pop("priceInvestigation")
        self.assertTrue(quality_block(altered))

    def test_new_and_historical_input_replay_and_anchor_tamper_detection(self):
        p = prepared(packet()); result = reviewed(p)
        frozen = freeze_execution_input(p, [], [])
        self.assertEqual(author.PROMPT_VERSION, frozen["promptVersion"])
        for env in (frozen, freeze_repair_input(frozen, raw_result(), ["시세 시점 확인"], "parent"), freeze_review_input(result),
                    freeze_execution_input(old.packet(), [], []), freeze_execution_input(external.packet(), [], [])):
            original = deepcopy(env); validate_execution_input(env); self.assertEqual(original, env)
        broken = deepcopy(frozen); broken["current"]["priceMoveAnchor"]["quotes"][0]["sourceAsOf"] = p["capturedAt"] + "x"
        with self.assertRaises(EvidenceContractError): validate_execution_input(broken)

    def test_unknown_provider_clock_cannot_be_promoted_by_delivery_or_collection(self):
        p = packet(); quote = next(f for f in p["facts"] if f["id"] == "quote-1")
        quote.update(sourceTimestampState="fetched-at-fallback", sourceTimestampPresent=False)
        p = annotate(p); p = prepared(p)
        self.assertFalse(price_move_anchor(p)["quotes"][0]["datedReferenceEligible"])
        context = {"executionLane": "market-observe", "evidenceUses": p["evidenceUses"],
                   "priceMoveAnchor": p["priceMoveAnchor"], "priceInvestigationScope": p["priceInvestigationScope"]}
        with self.assertRaises(ReadRequestError):
            reader.validate_scope(decision("finish", [f["id"] for f in p["facts"]]), context,
                                  {f["id"]: f for f in p["facts"]})

    @unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
    def test_persisted_reads_author_review_and_receipt_keep_investigation_outcome(self):
        case = external.ExternalInfluenceStorageTests("test_exact_read_author_review_outbox_and_receipt_keep_event_and_exposure")
        case.setUp(); self.addCleanup(case.doCleanups)
        def choose(*args, **kwargs):
            return {**scoped_decision(*args, **kwargs), "optionalEvidence": []}
        with patch.object(external, "packet", packet), patch.object(external, "reviewed_result", reviewed), \
                patch.object(external, "raw_result", raw_result), patch.object(external, "scoped_decision", choose):
            case.test_exact_read_author_review_outbox_and_receipt_keep_event_and_exposure()
        from test_ai_control import SUBJECT
        receipt = case.storage.publication.memory(SUBJECT["accountId"], SUBJECT["symbol"])
        self.assertEqual(raw_result()["priceExplanation"], receipt["priceExplanation"])
        self.assertEqual("quote-1", receipt["priceMoveAnchor"]["quotes"][0]["evidenceId"])
