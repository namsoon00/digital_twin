import unittest

from digital_twin.domain.sent_article_filter import (
    article_identity_keys,
    collect_article_identity_keys_from_context,
    filter_sent_articles_from_context,
)


class SentArticleFilterTests(unittest.TestCase):
    def test_article_keys_match_source_suffix_and_tracking_variants(self):
        first = {
            "kind": "news",
            "title": "Strategy sells $466M stock, keeps Bitcoin reserves - Yahoo Finance",
            "url": "https://finance.example/story/123?utm_source=rss&utm_medium=feed&id=123",
        }
        second = {
            "kind": "news",
            "title": "Strategy sells $466M stock, keeps Bitcoin reserves",
            "url": "https://finance.example/story/123?id=123&utm_campaign=morning",
        }

        self.assertTrue(article_identity_keys(first).intersection(article_identity_keys(second)))

    def test_filters_sent_research_evidence_but_keeps_other_context(self):
        sent_context = {
            "messageType": "investmentInsight",
            "ontologyRelationContext": {
                "facts": {
                    "researchEvidence": [
                        {
                            "kind": "news",
                            "title": "SK hynix ADR listing raises liquidity questions",
                            "url": "https://news.example/hynix-adr",
                        }
                    ]
                }
            },
        }
        current_context = {
            "messageType": "investmentInsight",
            "symbol": "000660",
            "ontologyRelationContext": {
                "facts": {
                    "currentPrice": 1843000,
                    "researchEvidence": [
                        {
                            "kind": "news",
                            "title": "SK hynix ADR listing raises liquidity questions - Reuters",
                            "url": "https://different.example/hynix-adr-copy",
                        },
                        {
                            "kind": "news",
                            "title": "SK hynix signs new HBM supply agreement",
                            "url": "https://news.example/hynix-hbm",
                        },
                    ],
                }
            },
        }
        sent_keys = collect_article_identity_keys_from_context(sent_context)

        result = filter_sent_articles_from_context(current_context, sent_keys)

        rows = result.context["ontologyRelationContext"]["facts"]["researchEvidence"]
        self.assertEqual(1, result.removed_count)
        self.assertEqual(1, len(rows))
        self.assertIn("HBM supply", rows[0]["title"])
        self.assertEqual(2, result.before_count)
        self.assertEqual(1, result.after_count)


if __name__ == "__main__":
    unittest.main()
