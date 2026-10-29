"""Price context discovery, partial evidence, bounded authoring and delivery proof."""
from copy import deepcopy
from datetime import timedelta
import os
import unittest
from unittest.mock import Mock, patch

import test_external_influence_flow as external
from test_scoped_knowledge_research import scoped_decision
from digital_twin.modules.ai_orchestration.domain import market_explanation as policy
from digital_twin.modules.ai_orchestration.domain import market_explanation_retrieval as reader, market_explanation_prompt as author
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_review_input, freeze_repair_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.insight_quality import accept_review, quality_block
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError, source_packet_bytes
from digital_twin.modules.reasoning.domain.observation_event_index import build, instant

ORIGINAL_PACKET = external.packet
ORIGINAL_REVIEWED = external.reviewed_result


def packet():
    return {**ORIGINAL_PACKET(), "marketExplanationPolicy": policy.VERSION}


def reviewed(p, raw=None):
    result = ORIGINAL_REVIEWED(p, raw)
    verdict = result["quality"]["review"]
    verdict["marketExplanation"] = {"supported": True,
        "reason": "가격 시각과 자료의 공개 시점, 기업 영향의 조건부 설명과 미확인 범위를 대조했습니다."}
    result["quality"] = accept_review(result, verdict, "price-review")
    return result


def run_reader(p, decide):
    saved = []
    def save(envelope):
        validate_execution_input(envelope)
        saved.append(deepcopy(envelope))
        return "price-read-" + str(len(saved))
    result = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]), p, [], [], decide, save, 262144)
    return result, saved


class MarketExplanationTests(unittest.TestCase):
    def test_discovery_uses_publication_time_and_never_selects_unread_or_future_news(self):
        p = packet(); at = instant(p["capturedAt"])
        def news(key, published, **values):
            return {"id": key, "kind": "news-article", "evidenceCategory": "research", "publishedAt": published,
                    "sourceAsOf": p["capturedAt"], "title": "발급 목록은 원문 근거가 아닙니다", **values}
        rows = [news("old-action", (at-timedelta(days=600)).isoformat()),
                news("current-event", (at-timedelta(hours=1)).isoformat()),
                news("unknown-time", ""), news("future", (at+timedelta(hours=1)).isoformat()),
                news("rejected", at.isoformat(), judgementEvidenceUsable=False)]
        index = build(rows, p["capturedAt"], (at-timedelta(hours=2)).isoformat())
        self.assertEqual(["current-event", "old-action", "unknown-time", "rejected"], [r["factId"] for r in index["events"]])
        self.assertTrue(index["events"][0]["publishedAfterQuote"])
        self.assertFalse(index["events"][2]["timeKnown"])
        def decide(env, _):
            listed = env["retrievalContext"]["eventIndex"]["context"]
            self.assertIn("rate-event", [r["factId"] for r in listed])
            self.assertNotIn("rate-event", env["retrievalContext"]["selectedFactIds"])
            return {**scoped_decision("finish", [f["id"] for f in env["current"]["facts"]]),
                    "purpose": "source-dated", "blocker": "none", "optionalEvidence": []}
        (_, _, _, _), saved = run_reader(p, decide)
        self.assertIn('"rate-event"', str(saved[0]["outputSchema"]).replace("'", '"'))

    def test_optional_gap_completes_but_required_gap_and_stale_quote_remain_blocked(self):
        def decision(env, _):
            return {**scoped_decision("finish", [f["id"] for f in env["current"]["facts"]]),
                "purpose": "source-dated", "blocker": "none",
                "optionalEvidence": ["같은 기간 업종 수익률 미확인으로 시장 대비 강약 설명은 제외합니다."]}
        (p, h, r, trace), _ = run_reader(packet(), decision)
        self.assertEqual("ready", p["retrieval"]["status"])
        self.assertEqual(1, len(p["retrieval"]["steps"][-1]["optionalEvidence"]))
        frozen = freeze_execution_input(p, h, r, retrieval_trace=trace)
        validate_execution_input(frozen)
        self.assertIn("업종 수익률 미확인", frozen["prompt"])
        raw = {**scoped_decision("finish", ["quote-1"]), "purpose": "source-dated", "blocker": "none",
               "missingEvidence": ["주장하려는 사건의 원문 미확인"], "optionalEvidence": []}
        self.assertEqual("defer", reader.validate_decision(raw)["action"])
        with self.assertRaises(ReadRequestError):
            reader.validate_scope({**raw, "missingEvidence": [], "purpose": "current-state"},
                {"executionLane": "market-observe", "evidenceUses": {"quotes": []}}, {"quote-1": {}})
        with self.assertRaises(ReadRequestError):
            reader.validate_decision({**raw, "optionalEvidence": raw["missingEvidence"]})

    def test_discovery_reserve_preserves_new_reads_and_exact_baseline_sources(self):
        p = packet()
        # Reproduce the live shape: delivery context and report provenance
        # consume the source budget before the first external-evidence read.
        p["lastDeliveredNotification"] = {"summary": "과거에 전달한 기업 분석의 근거와 한계. " * 620,
                                           "facts": deepcopy(p["facts"][:1])}
        p["deliveredNotificationContext"] = {"history": "기존 가격 변화 알림. " * 440}
        before = deepcopy(p)
        def decide(env, _):
            ids = env["retrievalContext"]["selectedFactIds"]
            if not env["retrievalContext"]["trace"]:
                return {**scoped_decision("read", ids), "purpose": "source-dated", "blocker": "none", "optionalEvidence": [],
                    "requests": [{"tool": "read_fact", "category": "macro", "factId": "rate-event"}]}
            return {**scoped_decision("finish", ids+["rate-event"]), "purpose": "source-dated", "blocker": "none", "optionalEvidence": []}
        (final, h, r, trace), saved = run_reader(p, decide)
        self.assertEqual("ready", final["retrieval"]["status"])
        self.assertEqual(2, len(saved))
        self.assertLessEqual(source_packet_bytes(final), 96000)
        self.assertEqual(before, p)
        self.assertEqual(before["lastDeliveredNotification"], final["lastDeliveredNotification"])
        self.assertEqual(next(f for f in p["facts"] if f["id"] == "rate-event"),
                         {k: v for k, v in next(f for f in final["facts"] if f["id"] == "rate-event").items()})
        validate_execution_input(freeze_execution_input(final, h, r, retrieval_trace=trace))

    def test_review_is_required_and_prior_prompt_replay_stays_unchanged(self):
        p = packet(); result = reviewed(p)
        self.assertEqual("accepted", result["quality"]["status"], result["quality"])
        frozen = freeze_execution_input(p, [], [])
        for envelope in [frozen, freeze_repair_input(frozen, external.raw_result(), ["조건부 설명 확인"], "parent"),
                         freeze_review_input(result)]:
            original = deepcopy(envelope); validate_execution_input(envelope); self.assertEqual(original, envelope)
        self.assertEqual(author.PROMPT_VERSION, frozen["promptVersion"])
        altered = deepcopy(result); altered["quality"]["review"].pop("marketExplanation")
        self.assertTrue(quality_block(altered))
        old = freeze_execution_input(ORIGINAL_PACKET(), [], []); original = deepcopy(old)
        validate_execution_input(old); self.assertEqual(original, old)
        self.assertEqual("independent-observation-v31-external-influence", old["promptVersion"])
        broken = deepcopy(frozen); broken["current"].pop("marketExplanationPolicy")
        with self.assertRaises(EvidenceContractError): validate_execution_input(broken)

    @unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
    def test_read_proof_review_outbox_and_delivered_receipt_use_the_new_policy(self):
        case = external.ExternalInfluenceStorageTests("test_exact_read_author_review_outbox_and_receipt_keep_event_and_exposure")
        case.setUp(); self.addCleanup(case.doCleanups)
        def decision(*args, **kwargs):
            return {**scoped_decision(*args, **kwargs), "optionalEvidence": ["같은 기간 지수가 미확인이므로 시장 대비 강약은 설명하지 않습니다."]}
        with patch.object(external, "packet", packet), patch.object(external, "reviewed_result", reviewed), patch.object(external, "scoped_decision", decision):
            case.test_exact_read_author_review_outbox_and_receipt_keep_event_and_exposure()
