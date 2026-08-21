import unittest

from digital_twin.domain.disclosure_taxonomy import classify_disclosure
from digital_twin.domain.disclosure_quality import assess_disclosure_document, normalize_official_document_text
from digital_twin.domain.investment_research import research_evidence_from_facts


class DisclosureTaxonomyTests(unittest.TestCase):
    def test_disclosure_categories_do_not_default_every_filing_to_capital_policy(self):
        earnings = classify_disclosure("분기보고서", "분기보고서", "OpenDART")
        contract = classify_disclosure("단일판매ㆍ공급계약체결", "", "OpenDART")
        ownership = classify_disclosure("임원ㆍ주요주주특정증권등소유상황보고서", "", "OpenDART")

        self.assertEqual("earnings", earnings["eventType"])
        self.assertEqual("supply_chain", contract["eventType"])
        self.assertEqual("capital_policy", ownership["eventType"])
        self.assertEqual("notable", ownership["materialityState"])

    def test_dart_collection_preserves_each_bounded_filing_as_distinct_evidence(self):
        rows = research_evidence_from_facts("005930", {
            "dartDisclosure": {
                "provider": "OpenDART",
                "receiptNo": "20260814000001",
                "documentText": "삼성전자는 2026년 2분기 연결 기준 실적과 주요 사업 현황을 공시했다. " * 4,
                "documentTextQuality": "body",
                "items": [
                    {
                        "provider": "OpenDART",
                        "reportName": "분기보고서",
                        "receiptNo": "20260814000001",
                        "receiptDate": "20260814",
                    },
                    {
                        "provider": "OpenDART",
                        "reportName": "임원ㆍ주요주주특정증권등소유상황보고서",
                        "receiptNo": "20260813000002",
                        "receiptDate": "20260813",
                    },
                ],
            },
        })

        self.assertEqual(2, len(rows))
        by_receipt = {item.raw_payload["receiptNo"]: item for item in rows}
        self.assertEqual("earnings", by_receipt["20260814000001"].raw_payload["eventType"])
        self.assertEqual("body", by_receipt["20260814000001"].raw_payload["officialDocumentQuality"])
        self.assertEqual("capital_policy", by_receipt["20260813000002"].raw_payload["eventType"])
        self.assertEqual("metadata-only", by_receipt["20260813000002"].raw_payload["officialDocumentQuality"])
        self.assertEqual("ready", by_receipt["20260814000001"].raw_payload["validationState"])
        self.assertEqual("conditional", by_receipt["20260813000002"].raw_payload["validationState"])
        self.assertTrue(by_receipt["20260814000001"].raw_payload["documentVerified"])
        self.assertFalse(by_receipt["20260813000002"].raw_payload["analysisReady"])
        self.assertNotEqual(rows[0].evidence_id, rows[1].evidence_id)

    def test_document_body_changes_disclosure_taxonomy(self):
        cancellation = classify_disclosure(
            "주요사항보고서",
            "주요사항보고서",
            "OpenDART",
            "이사회는 40조원 규모의 주식소각결정을 승인했다.",
        )
        ipo_reply = classify_disclosure(
            "조회공시요구(풍문또는보도)에대한답변(미확정)",
            "",
            "OpenDART",
            "카카오모빌리티의 10억 달러 규모 IPO 및 상장 추진을 검토 중이다.",
        )

        self.assertEqual("capital-structure", cancellation["disclosureCategory"])
        self.assertEqual("material", cancellation["materialityState"])
        self.assertEqual("listing-transaction", ipo_reply["disclosureCategory"])
        self.assertEqual("material", ipo_reply["materialityState"])
        self.assertEqual("title-and-document", ipo_reply["classificationBasis"])

    def test_document_quality_strips_css_and_rejects_dart_error_response(self):
        cleaned = normalize_official_document_text(
            ".xforms-label { font-family: Arial; color: red; } 회사는 자기주식 취득 결정을 공시했다. " * 5
        )
        error = assess_disclosure_document("014 파일이 존재하지 않습니다.", "body")

        self.assertNotIn("font-family", cleaned)
        self.assertIn("자기주식 취득", cleaned)
        self.assertEqual("document-rejected", error.state)
        self.assertEqual("blocked", error.validation_state)


if __name__ == "__main__":
    unittest.main()
