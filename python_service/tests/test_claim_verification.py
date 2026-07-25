import unittest

from digital_twin.domain.investment_evidence_governance import (
    claim_quality_summary,
    governed_evidence,
)
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio_ontology_research_concepts import add_governed_claim_concepts


TARGET = NewsCollectionTarget("005930", "Samsung Electronics", "KOSPI", "KRW", "semiconductor")
POLICY = {
    "researchClaimRequireVerifiedForInvestment": "1",
    "researchClaimOfficialVerificationEnabled": "1",
    "researchClaimMinimumIndependentSources": "2",
    "researchClaimCrossSourceWindowHours": "72",
    "researchClaimSimilarityThreshold": "0.32",
}


def evidence(
    evidence_id,
    source,
    title,
    statement,
    *,
    kind="news",
    polarity="support",
    published_at="2026-07-20T10:00:00Z",
    event_type="capital_policy",
    canonical_url="",
    article_publisher="",
    official_document_text=None,
):
    official_text = official_document_text if official_document_text is not None else (
        statement + " This official filing records the board decision and the disclosed transaction terms."
        if kind in {"disclosure", "filing"} else ""
    )
    return ResearchEvidence(
        evidence_id=evidence_id,
        symbol="005930",
        kind=kind,
        source=source,
        title=title,
        summary=statement,
        url=canonical_url or "https://" + source.lower().replace(" ", "-") + ".example.test/" + evidence_id,
        observed_at=published_at,
        polarity=polarity,
        published_at=published_at,
        raw_payload={
            "relationScope": "direct",
            "eventType": event_type,
            "articleReadStatus": "body",
            "bodyQualityPassed": True,
            "articleText": statement,
            "articleCanonicalUrl": canonical_url,
            "articlePublisher": article_publisher,
            "officialDocumentText": official_text,
        },
    )


class ClaimVerificationTests(unittest.TestCase):
    def govern(self, rows, policy=None):
        return governed_evidence(
            rows,
            TARGET,
            max_age_minutes=10**8,
            minimum_source_trust_state="standard",
            policy=POLICY if policy is None else policy,
        )

    def test_official_document_match_makes_news_claim_eligible(self):
        news = evidence(
            "news-buyback",
            "Reuters",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
        )
        filing = evidence(
            "dart-buyback",
            "OpenDART",
            "Samsung share buyback decision",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            kind="disclosure",
        )

        accepted, verified, rejected = self.govern([news, filing])

        self.assertEqual([], rejected)
        self.assertEqual({"news-buyback", "dart-buyback"}, {item.evidence_id for item in accepted})
        news_claim = news.raw_payload["claimLedger"]["claims"][0]
        self.assertEqual("verified-primary", news_claim["state"])
        self.assertIn("dart-buyback", news_claim["officialEvidenceIds"])
        self.assertGreaterEqual(news_claim["excerptStart"], 0)
        self.assertGreater(news_claim["excerptEnd"], news_claim["excerptStart"])
        self.assertTrue(news.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])
        self.assertTrue(any(item.claim_state == "verified-primary" for item in verified))

    def test_strict_policy_keeps_single_secondary_report_out_of_judgment(self):
        news = evidence(
            "news-single",
            "Reuters",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
        )

        accepted, verified, rejected = self.govern([news])

        self.assertEqual([], accepted)
        self.assertEqual([], verified)
        self.assertEqual(1, len(rejected))
        self.assertEqual("reported", rejected[0].claim_state)
        self.assertFalse(news.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])

    def test_same_wire_republication_does_not_count_as_independent_confirmation(self):
        first = evidence(
            "wire-one",
            "Reuters",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
        )
        second = evidence(
            "wire-two",
            "Reuters",
            "Samsung announces share buyback update",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
        )

        self.govern([first, second])

        first_claim = first.raw_payload["claimLedger"]["claims"][0]
        self.assertEqual(1, first_claim["independentSourceCount"])
        self.assertNotEqual("corroborated", first_claim["state"])
        self.assertTrue(first_claim["duplicateOfClaimId"])

    def test_same_canonical_url_from_different_feed_labels_is_not_independent(self):
        canonical_url = "https://finance.yahoo.com/news/samsung-buyback-123.html?utm_source=feed"
        wrapped = evidence(
            "yahoo-wrapper",
            "Yahoo Finance",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            canonical_url=canonical_url,
        )
        relabeled = evidence(
            "partner-label",
            "Insider Monkey",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            canonical_url="https://finance.yahoo.com/news/samsung-buyback-123.html",
        )

        self.govern([wrapped, relabeled])

        claim = wrapped.raw_payload["claimLedger"]["claims"][0]
        self.assertEqual(1, claim["independentSourceCount"])
        self.assertNotEqual("corroborated", claim["state"])
        self.assertIn("syndicated-duplicate", claim["reasons"])

    def test_official_metadata_only_cannot_be_used_as_claim_evidence(self):
        filing = evidence(
            "dart-metadata-only",
            "OpenDART",
            "Samsung share buyback decision",
            "접수일 20260720",
            kind="disclosure",
            official_document_text="",
        )
        filing.raw_payload["articleText"] = ""

        accepted, _verified, rejected = self.govern([filing])

        self.assertEqual([], accepted)
        self.assertEqual(1, len(rejected))
        self.assertFalse(filing.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])
        self.assertIn("official-document-content-missing", filing.raw_payload["evidenceGovernance"]["reasons"])

    def test_two_official_documents_do_not_verify_each_other(self):
        dart = evidence(
            "dart-official",
            "OpenDART",
            "Samsung share buyback decision",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            kind="disclosure",
        )
        sec = evidence(
            "sec-official",
            "SEC EDGAR",
            "Samsung share buyback decision",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            kind="filing",
        )

        self.govern([dart, sec])

        self.assertEqual([], dart.raw_payload["claimLedger"]["claims"][0]["officialEvidenceIds"])
        self.assertEqual([], sec.raw_payload["claimLedger"]["claims"][0]["officialEvidenceIds"])

    def test_conflicting_independent_reports_are_blocked(self):
        positive = evidence(
            "reuters-positive",
            "Reuters",
            "Samsung share buyback plan",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            polarity="support",
        )
        negative = evidence(
            "bloomberg-negative",
            "Bloomberg",
            "Samsung share buyback plan questioned",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            polarity="risk",
        )

        accepted, _verified, rejected = self.govern([positive, negative])

        self.assertEqual([], accepted)
        self.assertEqual({"reuters-positive", "bloomberg-negative"}, {item.evidence_id for item in rejected})
        self.assertEqual("conflicted", positive.raw_payload["claimLedger"]["claims"][0]["state"])
        self.assertFalse(positive.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])

    def test_correction_supersedes_older_same_origin_claim(self):
        original = evidence(
            "reuters-original",
            "Reuters",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
        )
        correction = evidence(
            "reuters-correction",
            "Reuters",
            "Correction: Samsung announces share buyback",
            "Correction: Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            published_at="2026-07-20T12:00:00Z",
        )

        self.govern([original, correction])

        original_claim = original.raw_payload["claimLedger"]["claims"][0]
        correction_claim = correction.raw_payload["claimLedger"]["claims"][0]
        self.assertEqual("superseded", original_claim["state"])
        self.assertEqual("reuters-correction", original_claim["supersededByEvidenceId"])
        self.assertIn(original_claim["claimId"], correction_claim["supersedesClaimIds"])

    def test_source_registry_overrides_source_tier_and_claim_metrics_are_exposed(self):
        item = evidence(
            "custom-wire",
            "Custom Wire",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
        )
        policy = {
            **POLICY,
            "researchClaimRequireVerifiedForInvestment": "0",
            "researchClaimSourceRegistry": "custom wire=trusted,origin=custom-wire",
        }

        accepted, verified, _rejected = self.govern([item], policy)
        summary = claim_quality_summary([item])

        self.assertEqual(1, len(accepted))
        self.assertEqual("trusted", item.raw_payload["sourceTrustState"])
        self.assertEqual("custom-wire", item.raw_payload["sourceOrigin"])
        self.assertEqual(1, summary["claimCount"])
        self.assertEqual("reported", verified[0].claim_state)

    def test_ontology_projects_claim_lifecycle_and_corroboration_relations(self):
        news = evidence(
            "news-graph",
            "Reuters",
            "Samsung announces share buyback",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
        )
        filing = evidence(
            "dart-graph",
            "OpenDART",
            "Samsung share buyback decision",
            "Samsung Electronics announced a 1 trillion won share buyback plan on Tuesday.",
            kind="disclosure",
        )
        self.govern([news, filing])
        graph = PortfolioOntology("claim-graph")
        stock_id = add_entity(graph, "stock", "005930", "Samsung Electronics", {"tboxClass": "Stock"})

        add_governed_claim_concepts(graph, stock_id, news, news.raw_payload)

        claim_entities = [item for item in graph.entities if item.kind == "verified-claim"]
        self.assertTrue(any(item.properties.get("tboxClass") == "VerifiedClaim" for item in claim_entities))
        self.assertTrue(any(item.relation_type == "OFFICIALLY_VERIFIED_BY" for item in graph.relations))


if __name__ == "__main__":
    unittest.main()
