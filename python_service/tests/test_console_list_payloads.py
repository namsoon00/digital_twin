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

    def test_research_list_blocks_corrupt_legacy_summary_before_rendering(self):
        evidence = ResearchEvidence(
            evidence_id="evidence-corrupt-legacy-news",
            symbol="000660",
            kind="news",
            source="Legacy RSS",
            title="SK하이닉스 키옥시아 지분 기사",
            summary="\ufffd\ufffd\ufffd encoded source text",
            raw_payload={
                "articleSummaryKo": "\ufffd\ufffd\ufffd encoded source text",
                "stockImpactPolarity": "context",
                "relationScope": "direct",
            },
        )

        payload = research_evidence_list_payload(evidence)

        self.assertEqual("legacy-projection", payload["analysisSource"])
        self.assertEqual("blocked", payload["summaryQualityState"])
        self.assertIn("text-encoding-corrupt", payload["articleSummaryQuality"]["issues"])
        self.assertEqual("원문 인코딩 점검으로 요약을 보류했습니다.", payload["articleSummaryKo"])

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

    def test_research_list_exposes_official_analysis_audit_without_full_document(self):
        evidence = ResearchEvidence(
            evidence_id="research:005930:dart:official",
            symbol="005930",
            kind="disclosure",
            source="OpenDART",
            title="자기주식 취득 결정",
            summary="공시 요약",
            published_at="2026-08-25T00:00:00Z",
            raw_payload={
                "officialDocumentState": "document-verified",
                "documentVerified": True,
                "analysisReady": True,
                "documentHash": "document-hash",
                "documentCharCount": 2400,
                "externalFactDatasetId": "opendart.disclosures",
                "externalFactSourceRevision": "metadata-revision",
                "officialDocumentDatasetId": "opendart.document",
                "officialDocumentFactRevision": "document-revision",
                "officialDocumentFactPayloadHash": "document-payload-hash",
                "officialDocumentFetchedAt": "2026-08-25T00:02:00Z",
                "sourceRevision": "202608250001",
                "sourceAsOf": "2026-08-25T00:00:00Z",
                "officialDocumentText": "공식 원문 전체는 목록 응답에 포함하지 않습니다.",
                "disclosureAnalysis": {
                    "status": "ready",
                    "version": "disclosure-analysis-v5",
                    "summary": "회사가 자기주식 취득을 결의했습니다.",
                    "confirmedFacts": ["보통주 1,000,000주를 취득합니다."],
                    "sourceSections": [{"text": "근거 문장", "start": 0, "end": 5}],
                },
            },
        )

        payload = research_evidence_list_payload(evidence)

        self.assertTrue(payload["documentVerified"])
        self.assertEqual(2400, payload["documentCharCount"])
        self.assertEqual("202608250001", payload["sourceRevision"])
        self.assertEqual("opendart.disclosures", payload["externalFactDatasetId"])
        self.assertEqual("opendart.document", payload["officialDocumentDatasetId"])
        self.assertEqual("document-revision", payload["officialDocumentFactRevision"])
        self.assertEqual("document-payload-hash", payload["officialDocumentFactPayloadHash"])
        self.assertEqual("2026-08-25T00:02:00Z", payload["officialDocumentFetchedAt"])
        self.assertEqual("회사가 자기주식 취득을 결의했습니다.", payload["disclosureAnalysis"]["summary"])
        self.assertNotIn("officialDocumentText", payload["payload"])
