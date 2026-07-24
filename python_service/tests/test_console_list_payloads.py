import unittest

from digital_twin.domain.investment_research import ResearchEvidence
from digital_twin.domain.notifications import NotificationJob
from digital_twin.infrastructure.web_server import (
    notification_job_list_payload,
    research_evidence_list_payload,
)


class ConsoleListPayloadTests(unittest.TestCase):
    def test_notification_list_omits_full_message_and_cooldown_audit(self):
        job = NotificationJob.create(
            "A long message body that belongs in the detail endpoint.",
            message_type="investmentInsight",
            context={
                "symbol": "000660",
                "title": "SK하이닉스 판단",
                "deliveryDecision": "send",
                "deliveryReasons": ["관계 점수가 기준을 넘었습니다."],
                "cooldownReason": "same-state",
            },
        )

        payload = notification_job_list_payload(job, stale_minutes=30)

        self.assertEqual("000660", payload["symbol"])
        self.assertEqual("send", payload["deliveryDecision"])
        self.assertNotIn("fullText", payload)
        self.assertNotIn("cooldownReason", payload)

    def test_research_list_omits_heavy_article_objects(self):
        evidence = ResearchEvidence(
            evidence_id="evidence-1",
            symbol="000660",
            kind="news",
            source="Yahoo Finance",
            title="메모리 업황 기사",
            summary="본문 요약",
            raw_payload={
                "articleSummaryKo": "한글 기사 요약",
                "stockImpactPolarity": "negative",
                "articleFacts": {"revenue": [1, 2, 3]},
                "aiAnalysis": {"long": "detail"},
                "ontologyRelations": [{"id": "r-1"}],
            },
        )

        payload = research_evidence_list_payload(evidence)

        self.assertEqual("한글 기사 요약", payload["articleSummaryKo"])
        self.assertEqual("negative", payload["stockImpactPolarity"])
        self.assertNotIn("articleFacts", payload)
        self.assertNotIn("aiAnalysis", payload)
        self.assertNotIn("ontologyRelations", payload)

    def test_research_list_projects_missing_legacy_news_analysis(self):
        evidence = ResearchEvidence(
            evidence_id="evidence-legacy-news",
            symbol="AAPL",
            kind="news",
            source="Yahoo Finance",
            title="Apple demand outlook remains mixed",
            summary="Apple demand outlook remains mixed after the latest market update.",
            raw_payload={"relationScope": "direct"},
        )

        payload = research_evidence_list_payload(evidence)

        self.assertEqual("legacy-projection", payload["analysisSource"])
        self.assertTrue(payload["articleSummaryKo"])
        self.assertEqual("feed-summary", payload["articleReadStatus"])
        self.assertIn(payload["stockImpactPolarity"], {"support", "risk", "context"})

    def test_research_list_exposes_claim_verification_without_full_claim_text(self):
        evidence = ResearchEvidence(
            evidence_id="evidence-claim-state",
            symbol="005930",
            kind="news",
            source="Reuters",
            title="삼성전자 자사주 매입",
            summary="기사 요약",
            raw_payload={
                "evidenceGovernance": {
                    "claimState": "corroborated",
                    "verificationStatus": "verified-secondary",
                    "investmentJudgmentEligible": True,
                    "sourcePublisher": "Reuters",
                    "sourceOrigin": "reuters",
                    "independentSourceCount": 2,
                    "officialEvidenceIds": ["research:005930:dart:1"],
                },
                "claimLedger": {
                    "claims": [{"statement": "원문 근거 문장"}],
                    "summary": {"claimCount": 1, "eligibleClaimCount": 1},
                },
            },
        )

        payload = research_evidence_list_payload(evidence)

        self.assertEqual("corroborated", payload["claimVerification"]["claimState"])
        self.assertEqual(2, payload["claimVerification"]["independentSourceCount"])
        self.assertEqual(1, len(payload["claimVerification"]["officialEvidenceIds"]))
        self.assertNotIn("claimLedger", payload)
