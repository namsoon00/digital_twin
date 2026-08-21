import io
import sys
import unittest
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.research_evidence_governance_service import ResearchEvidenceGovernanceService, payload_signature
from digital_twin.domain.investment_research import ResearchEvidence, research_evidence_from_external_signals
from digital_twin.domain.market_data import normalize_position
from digital_twin.infrastructure.external_signal_provider_market import dart_document_text
from digital_twin.infrastructure.external_signal_provider_sec import sec_document_text
from digital_twin.infrastructure.external_signals import ExternalSignalProvider


class MemoryEvidenceStore:
    def __init__(self, items):
        self.items = list(items)
        self.written = []

    def latest_page(self, symbol="", limit=50, offset=0):
        rows = [item for item in self.items if not symbol or item.symbol == symbol]
        return rows[offset:offset + limit], len(rows)

    def upsert_many(self, items):
        self.written = list(items)
        return len(self.written)


class ResearchEvidenceGovernanceMaintenanceTests(unittest.TestCase):
    def test_payload_signature_ignores_volatile_prompt_age_but_tracks_freshness_state(self):
        first = {
            "promptEvidenceAdmission": {
                "checkedAt": "2026-07-24T00:01:00Z",
                "ageMinutes": 1.0,
                "freshnessState": "fresh",
                "reasonCodes": [],
            },
        }
        later = {
            "promptEvidenceAdmission": {
                "checkedAt": "2026-07-24T00:02:00Z",
                "ageMinutes": 2.0,
                "freshnessState": "fresh",
                "reasonCodes": [],
            },
        }
        stale = {
            "promptEvidenceAdmission": {
                "checkedAt": "2026-07-27T00:02:00Z",
                "ageMinutes": 4322.0,
                "freshnessState": "stale",
                "reasonCodes": ["evidence-stale"],
            },
        }

        self.assertEqual(payload_signature(first), payload_signature(later))
        self.assertNotEqual(payload_signature(first), payload_signature(stale))

    def test_backfill_adds_ledger_and_blocks_metadata_only_official_document(self):
        news = ResearchEvidence(
            "legacy-news", "AAPL", "news", "Reuters", "Apple reports demand update",
            "Apple reported stronger device demand and raised its operating outlook for the next quarter.",
            "https://www.reuters.com/article/apple-demand", "2026-07-24T00:00:00Z", "support",
            published_at="2026-07-24T00:00:00Z",
            raw_payload={"relationScope": "direct", "eventType": "guidance", "articleText": "Apple reported stronger device demand and raised its operating outlook for the next quarter."},
        )
        metadata_only = ResearchEvidence(
            "legacy-filing", "AAPL", "filing", "SEC EDGAR", "10-Q", "Apple, 제출일 20260724",
            "https://www.sec.gov/Archives/edgar/data/1/example.htm", "2026-07-24T00:00:00Z", "context",
            published_at="2026-07-24T00:00:00Z",
            raw_payload={"relationScope": "direct", "eventType": "earnings"},
            data_state="sufficient",
            validation_state="ready",
        )
        store = MemoryEvidenceStore([news, metadata_only])
        service = ResearchEvidenceGovernanceService(store, {
            "newsEvidenceMaxAgeMinutes": "100000000",
            "researchClaimRequireVerifiedForInvestment": "1",
            "researchClaimOfficialVerificationEnabled": "1",
            "researchClaimMinimumIndependentSources": "2",
            "researchClaimSimilarityThreshold": "0.32",
        })

        result = service.revalidate(limit=20)

        self.assertEqual(2, result["writtenCount"])
        self.assertEqual(0, result["claimQuality"]["ungovernedEvidenceCount"])
        self.assertIn("claimLedger", news.raw_payload)
        self.assertFalse(metadata_only.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])
        self.assertIn("official-document-content-missing", metadata_only.raw_payload["evidenceGovernance"]["reasons"])
        self.assertEqual("partial", metadata_only.data_state)
        self.assertEqual("conditional", metadata_only.validation_state)

        second = service.revalidate(limit=20, dry_run=True)

        self.assertEqual(0, second["changedCount"])
        self.assertEqual(0, second["writtenCount"])
        self.assertTrue(second["notificationReplay"] is False)

    def test_sec_and_dart_document_extractors_preserve_readable_body(self):
        sec_text = sec_document_text(
            "<html><body><h1>Quarterly report</h1><p>Revenue increased and the company updated its outlook.</p><script>ignore()</script></body></html>",
            6000,
        )
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("document.xml", "<DOCUMENT><TITLE>주요사항보고서</TITLE><P>회사는 자사주 취득 결정을 공시했습니다.</P></DOCUMENT>")
        dart_text = dart_document_text(archive.getvalue(), 6000)

        self.assertIn("Revenue increased", sec_text)
        self.assertNotIn("ignore", sec_text)
        self.assertIn("자사주 취득", dart_text)

    def test_provider_collects_official_document_body_and_maps_it_to_evidence(self):
        dart_zip = io.BytesIO()
        with zipfile.ZipFile(dart_zip, "w") as bundle:
            bundle.writestr("disclosure.xml", "<DOC><P>삼성전자는 자사주 취득 결정을 공시했고 취득 규모와 기간을 명시했습니다. 이번 공시는 이사회 결의, 취득 방법, 예상 일정과 자금 사용 계획을 함께 설명해 투자자가 공시 사실을 원문으로 확인할 수 있도록 했습니다. 회사는 공시 내용의 변경 여부와 후속 이행 현황도 관련 보고서에서 지속적으로 제공할 예정이라고 밝혔습니다.</P></DOC>")

        def dart_json(url, _headers=None):
            self.assertIn("list.json", url)
            return {"status": "000", "list": [{
                "corp_name": "삼성전자", "report_nm": "주요사항보고서(자기주식취득결정)",
                "rcept_no": "20260724000001", "rcept_dt": "20260724",
            }]}

        provider = ExternalSignalProvider(
            settings={
                "externalAlphaEnabled": "0", "externalCoinGeckoEnabled": "0", "externalFredEnabled": "0",
                "externalSecEnabled": "0", "externalNewsEnabled": "0", "externalFxRateEnabled": "0", "externalYFinanceEnabled": "0",
                "externalDartEnabled": "1", "externalDartCorpCodes": "005930=00126380",
                "opendartApiKey": "test-key",
                "externalDartDocumentTextEnabled": "1", "externalDartDocumentTextMaxChars": "6000",
                "externalApiRateLimitSeconds": "0", "externalApiRetryAttempts": "1",
            },
            cache=object(), evidence_store=object(), fetch_json=dart_json,
            fetch_bytes=lambda _url, _headers=None: dart_zip.getvalue(), sleep=lambda _seconds: None,
        )
        signals = provider.fetch_signals([normalize_position({"symbol": "005930", "name": "삼성전자", "market": "KR", "currency": "KRW"})])
        evidence = research_evidence_from_external_signals("005930", signals)
        disclosure = next(item for item in evidence if item.kind == "disclosure")

        self.assertEqual("body", signals["dartDisclosures"]["005930"]["documentTextQuality"])
        self.assertIn("자사주 취득", disclosure.raw_payload["officialDocumentText"])
        self.assertEqual("sufficient", disclosure.raw_payload["dataState"])


if __name__ == "__main__":
    unittest.main()
