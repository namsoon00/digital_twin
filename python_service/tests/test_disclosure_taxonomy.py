import unittest

from digital_twin.domain.disclosure_taxonomy import classify_disclosure
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
        self.assertNotEqual(rows[0].evidence_id, rows[1].evidence_id)


if __name__ == "__main__":
    unittest.main()
