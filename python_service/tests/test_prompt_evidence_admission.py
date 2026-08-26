import unittest
from datetime import datetime, timezone

from digital_twin.domain.prompt_evidence_admission import assess_prompt_evidence


NOW = datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)


class PromptEvidenceAdmissionTests(unittest.TestCase):
    def news_payload(self, inline=True):
        return {
            "kind": "news",
            "title": "NVIDIA confirms a new supply agreement",
            "publishedAt": "2026-08-21T01:00:00Z",
            "validationState": "ready",
            "dataState": "sufficient",
            "evidenceGovernance": {"investmentJudgmentEligible": True},
            "newsEligibility": {
                "displayEligible": True,
                "alertEligible": True,
                "reasoningEligible": True,
            },
            "aiAnalysis": {"decisionInlineEligible": inline},
        }

    def test_news_requires_inline_eligibility_before_any_decision_prompt_use(self):
        result = assess_prompt_evidence(self.news_payload(inline=False), now=NOW)

        self.assertFalse(result.prompt_eligible)
        self.assertEqual("alert", result.usage)
        self.assertIn("news-decision-inline-not-eligible", result.reason_codes)

    def test_fresh_verified_news_can_be_action_evidence_only_when_linked(self):
        reference = assess_prompt_evidence(self.news_payload(), now=NOW)
        action = assess_prompt_evidence(self.news_payload(), now=NOW, directly_linked=True)

        self.assertTrue(reference.prompt_eligible)
        self.assertEqual("reference", reference.usage)
        self.assertEqual("decision", action.usage)

    def test_metadata_only_disclosure_never_enters_decision_prompt(self):
        result = assess_prompt_evidence({
            "kind": "disclosure",
            "title": "주요사항보고서",
            "publishedAt": "2026-08-21T01:00:00Z",
            "validationState": "conditional",
            "dataState": "partial",
            "officialDocumentState": "metadata-only",
            "documentVerified": False,
            "analysisReady": False,
            "evidenceGovernance": {"investmentJudgmentEligible": False},
        }, now=NOW)

        self.assertFalse(result.prompt_eligible)
        self.assertEqual("alert", result.usage)
        self.assertIn("official-document-not-verified", result.reason_codes)

    def test_stale_verified_disclosure_is_display_only(self):
        result = assess_prompt_evidence({
            "kind": "filing",
            "title": "10-Q",
            "publishedAt": "2026-08-01T01:00:00Z",
            "validationState": "ready",
            "dataState": "sufficient",
            "officialDocumentState": "document-verified",
            "documentVerified": True,
            "documentHash": "sha256:verified-disclosure-document",
            "analysisReady": True,
            "evidenceGovernance": {"investmentJudgmentEligible": True},
        }, now=NOW, directly_linked=True)

        self.assertFalse(result.prompt_eligible)
        self.assertEqual("display", result.usage)
        self.assertEqual("stale", result.freshness_state)
        self.assertIn("evidence-stale", result.reason_codes)

    def test_fresh_verified_disclosure_can_be_decision_evidence(self):
        result = assess_prompt_evidence({
            "kind": "disclosure",
            "title": "단일판매 공급계약",
            "publishedAt": "2026-08-20T01:00:00Z",
            "validationState": "ready",
            "dataState": "sufficient",
            "officialDocumentState": "document-verified",
            "documentVerified": True,
            "documentHash": "sha256:verified-disclosure-document",
            "analysisReady": True,
            "evidenceGovernance": {"investmentJudgmentEligible": True},
        }, now=NOW, directly_linked=True)

        self.assertTrue(result.prompt_eligible)
        self.assertEqual("decision", result.usage)


if __name__ == "__main__":
    unittest.main()
