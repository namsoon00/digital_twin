import unittest
from unittest.mock import patch

from digital_twin.modules.news_intelligence.domain.disclosure_analysis import build_disclosure_analysis_prompt, local_disclosure_analysis
from digital_twin.modules.news_intelligence.domain.disclosure_taxonomy import classify_disclosure
from digital_twin.modules.news_intelligence.domain.disclosure_quality import assess_disclosure_document, normalize_official_document_text
from digital_twin.modules.news_intelligence.domain.investment_research import disclosure_evidence_payload, research_evidence_from_facts
from digital_twin.infrastructure.disclosure_analyzer import CommandDisclosureAnalyzer


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

    def test_merger_title_outranks_generic_financial_terms_in_document(self):
        classified = classify_disclosure(
            "주요사항보고서(회사합병결정)",
            "회사합병결정",
            "OpenDART",
            "합병 대상 회사의 매출액과 영업이익을 함께 기재했습니다.",
        )
        payload = disclosure_evidence_payload(
            {},
            title="주요사항보고서(회사합병결정)",
            source="OpenDART",
            document_text="회사는 합병을 결정했습니다. 합병 대상 회사의 매출액과 영업이익을 함께 기재했습니다. " * 4,
            document_quality="body",
            metadata_verified=True,
        )

        self.assertEqual("capital_policy", classified["eventType"])
        self.assertIn("회사 구조", payload["disclosureAnalysis"]["summary"])

    def test_title_specific_analysis_ignores_unrelated_template_terms(self):
        ownership = local_disclosure_analysis({
            "reportName": "임원ㆍ주요주주특정증권등소유상황보고서",
            "officialDocumentText": "양식 안내에는 공급계약과 유상증자 기재 항목이 포함됩니다. " * 5,
            "analysisReady": True,
        })
        periodic = local_disclosure_analysis({
            "reportName": "반기보고서",
            "officialDocumentText": "주요 계약과 공급계약 현황, 자기주식 변동을 함께 기재합니다. " * 5,
            "analysisReady": True,
        })
        acquisition = local_disclosure_analysis({
            "reportName": "주요사항보고서(자기주식취득결정)",
            "officialDocumentText": "계약 체결 및 매출액 관련 표준 양식 문구입니다. " * 5,
            "analysisReady": True,
        })

        self.assertIn("보유 주식 변동", ownership.lines[0])
        self.assertIn("희석을 뜻하지는 않습니다", ownership.lines[1])
        self.assertIn("정기적으로 보고", periodic.lines[0])
        self.assertIn("자기주식을 취득", acquisition.lines[0])
        self.assertNotIn("계약 또는 수주", acquisition.lines[0])

    def test_title_taxonomy_outranks_body_template_contamination(self):
        classified = classify_disclosure(
            "임원ㆍ주요주주특정증권등소유상황보고서",
            "",
            "OpenDART",
            "유상증자와 공급계약 관련 표준 양식 문구 " * 8,
        )

        self.assertEqual("ownership-governance", classified["disclosureCategory"])
        self.assertEqual("notable", classified["materialityState"])

    def test_operational_and_governance_titles_keep_their_specific_meaning(self):
        cases = [
            ("[기재정정]장래사업ㆍ경영계획(공정공시)", "business-plan", "앞으로 추진할 사업"),
            ("최대주주등소유주식변동신고서", "ownership-governance", "보유 주식 변동"),
            ("동일인등출자계열회사와의상품ㆍ용역거래변경", "related-party-transaction", "주요 관계자와의 거래"),
            ("[기재정정]생산중단", "operations-contract", "운영 차질"),
            ("생산재개(자율공시)", "operations-contract", "다시 시작"),
            ("소송등의제기ㆍ신청(일정금액이상의청구)", "legal-regulatory", "법적 분쟁"),
        ]
        contaminated_body = "유상증자 공급계약 매출액 자기주식 표준 양식 문구 " * 10
        for title, expected_category, expected_summary in cases:
            classified = classify_disclosure(title, "", "OpenDART", contaminated_body)
            analysis = local_disclosure_analysis({
                "reportName": title,
                "officialDocumentText": contaminated_body,
                "analysisReady": True,
            })
            self.assertEqual(expected_category, classified["disclosureCategory"], title)
            self.assertIn(expected_summary, analysis.lines[0], title)

    def test_structured_facts_downrank_contact_rows_and_filter_noise_numbers(self):
        payload = disclosure_evidence_payload(
            {},
            title="생산중단",
            source="OpenDART",
            document_text=(
                "담당부서명 IR팀 담당자명 홍길동 tel 02-3458-3139 fax 02-3458-3033. "
                "회사는 2026-08-21 전 사업장의 생산을 중단하기로 결정했습니다. "
                "생산중단 분야의 최근 매출액은 42.29%이며 관련 발행주식은 265,390,108주입니다. "
                "노사 교섭이 타결되면 생산을 재개하고 후속 공시를 제출할 예정입니다."
            ),
            document_quality="body",
            metadata_verified=True,
        )
        analysis = payload["disclosureAnalysis"]

        self.assertTrue(all("담당자명" not in item for item in analysis["confirmedFacts"]))
        self.assertIn("42.29%", analysis["materialNumbers"])
        self.assertIn("265,390,108주", analysis["materialNumbers"])
        self.assertNotIn("3458", analysis["materialNumbers"])

    def test_document_quality_strips_css_and_rejects_dart_error_response(self):
        cleaned = normalize_official_document_text(
            ".xforms * { font-family: 돋움체; color: red; } 회사는 자기주식 취득 결정을 공시했다. " * 5
        )
        error = assess_disclosure_document("014 파일이 존재하지 않습니다.", "body")
        configuration = assess_disclosure_document("", "deferred-contact")

        self.assertNotIn("font-family", cleaned)
        self.assertNotIn(".xforms", cleaned)
        self.assertIn("자기주식 취득", cleaned)
        self.assertEqual("document-rejected", error.state)
        self.assertEqual("blocked", error.validation_state)
        self.assertEqual("configuration-required", configuration.state)
        self.assertEqual("conditional", configuration.validation_state)
        self.assertIn(
            "official-document-configuration-required",
            configuration.issues,
        )

    def test_disclosure_prompt_deduplicates_document_preview(self):
        sentence = "회사는 보통주 100만주를 취득하기로 결정했다."
        prompt = build_disclosure_analysis_prompt({
            "reportName": "자기주식취득결정",
            "officialDocumentText": ".xforms * { font-size: 10px; } " + sentence,
            "analysisReady": True,
            "rawLines": ["공시명: 자기주식취득결정", "공시 원문: " + sentence],
        })

        self.assertEqual(1, prompt.count(sentence))
        self.assertNotIn("font-size", prompt)
        self.assertIn("신뢰할 수 없는 입력 데이터", prompt)

    def test_metadata_only_disclosure_skips_external_ai_command(self):
        analyzer = CommandDisclosureAnalyzer("unused-command")
        with patch("digital_twin.infrastructure.disclosure_analyzer.run_background_ai_prompt") as run:
            result = analyzer.analyze({
                "reportName": "주요사항보고서",
                "analysisReady": False,
                "officialDocumentText": "",
            })

        run.assert_not_called()
        self.assertEqual("메타데이터 전용", result.source)


if __name__ == "__main__":
    unittest.main()
