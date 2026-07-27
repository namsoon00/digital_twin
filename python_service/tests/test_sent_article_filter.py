import unittest

from digital_twin.domain.message_types import INVESTMENT_INSIGHT, NEWS_DIGEST
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.sent_article_filter import (
    article_identity_keys,
    collect_article_identity_keys_from_context,
    filter_sent_articles_from_context,
    news_story_changes_decision,
    news_story_impact_from_context,
)
from digital_twin.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore


class SentArticleFilterTests(unittest.TestCase):
    def notification_store(self):
        store = MySQLNotificationJobStore.__new__(MySQLNotificationJobStore)
        return store

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

    def test_collects_precomputed_identity_keys_without_deep_context_scan(self):
        context = {
            "newsDigest": {
                "articleKeys": ["url:alreadycomputed"],
                "items": [{"identityKeys": ["title:itemcomputed"]}],
            },
            "veryDeep": {
                "level1": {
                    "level2": {
                        "level3": {
                            "level4": {
                                "kind": "news",
                                "title": "This should be skipped by the shallow scan",
                                "url": "https://example.test/deep",
                            }
                        }
                    }
                }
            },
        }

        keys = collect_article_identity_keys_from_context(context, max_depth=2, max_nodes=20)

        self.assertIn("url:alreadycomputed", keys)
        self.assertIn("title:itemcomputed", keys)
        self.assertFalse(any(key.startswith("evidence:") for key in keys))

    def test_collect_context_respects_node_budget(self):
        context = {
            "researchEvidence": [
                {
                    "kind": "news",
                    "title": "Apple services revenue expands faster than expected " + str(index),
                    "url": "https://example.test/apple-" + str(index),
                }
                for index in range(50)
            ]
        }

        keys = collect_article_identity_keys_from_context(context, max_nodes=8, max_keys=20)

        self.assertLessEqual(len(keys), 20)

    def test_holding_insight_is_not_article_driven_by_quality_rule_name(self):
        store = self.notification_store()
        job = NotificationJob.create(
            text="holding alert",
            message_type=INVESTMENT_INSIGHT,
            context={
                "sourceSignalTypes": ["holdingTiming"],
                "ontologyInsight": {
                    "sourceSignalTypes": ["holdingTiming"],
                    "semanticSignature": (
                        "subject=strc|sourceSignalTypes=holdingTiming|"
                        "relationRuleIds=graph.factor.position_crowding.v1+"
                        "graph.news.quality.confidence_limit.v1"
                    ),
                },
            },
        )

        self.assertFalse(store.article_driven_job(job))

    def test_research_insight_is_article_driven(self):
        store = self.notification_store()
        job = NotificationJob.create(
            text="news alert",
            message_type=INVESTMENT_INSIGHT,
            context={
                "sourceSignalTypes": ["researchEvidence"],
                "ontologyInsight": {"sourceSignalTypes": ["researchEvidence"]},
            },
        )

        self.assertTrue(store.article_driven_job(job))

    def test_news_digest_is_article_driven(self):
        store = self.notification_store()
        job = NotificationJob.create(text="news digest", message_type=NEWS_DIGEST)

        self.assertTrue(store.article_driven_job(job))

    def test_syndicated_story_is_filtered_by_claim_root_but_new_fact_is_kept(self):
        original = {
            "kind": "news",
            "title": "NVIDIA announces a new AI partnership",
            "url": "https://first.example/nvda-partnership",
            "claimId": "claim:nvda-partnership-origin",
            "readStatus": "body",
            "eventTakeaway": "NVIDIA signed a multi-year AI infrastructure partnership.",
        }
        syndicated_copy = {
            "kind": "news",
            "title": "엔비디아, 인공지능 인프라 협력 발표",
            "url": "https://second.example/nvda-partnership-copy",
            "duplicateOfClaimId": "claim:nvda-partnership-origin",
            "readStatus": "body",
            "eventTakeaway": "엔비디아가 인공지능 인프라 협력 계약을 발표했습니다.",
        }
        sent = article_identity_keys(original)

        duplicate_result = filter_sent_articles_from_context({"researchEvidence": [syndicated_copy]}, sent)
        self.assertEqual(1, duplicate_result.removed_count)
        self.assertEqual([], duplicate_result.context["researchEvidence"])

        followup = dict(syndicated_copy, factId="fact:nvda-partnership-contract-value")
        followup_result = filter_sent_articles_from_context({"researchEvidence": [followup]}, sent)
        self.assertEqual(0, followup_result.removed_count)
        self.assertEqual([followup], followup_result.context["researchEvidence"])

    def test_compact_news_impact_requires_body_read_material_directional_story(self):
        context = {
            "researchEvidence": [
                {
                    "kind": "news",
                    "title": "NVIDIA wins a data-center supply contract",
                    "source": "Reuters",
                    "readStatus": "body",
                    "materialityState": "material",
                    "relevanceState": "direct",
                    "sourceTrustState": "trusted",
                    "dataState": "sufficient",
                    "validationState": "ready",
                    "stockImpactPolarity": "support",
                    "decisionInlineEligible": True,
                    "decisionInlineReasonKo": "공개된 신규 공급 계약이 엔비디아의 수요 근거를 직접 강화합니다.",
                    "eventTakeaway": "NVIDIA won a new data-center supply contract with disclosed demand support.",
                },
                {
                    "kind": "news",
                    "title": "NVIDIA commentary feed",
                    "readStatus": "feed-summary",
                    "materialityState": "material",
                    "relevanceState": "direct",
                    "stockImpactPolarity": "risk",
                },
            ]
        }

        impact = news_story_impact_from_context(context)

        self.assertEqual("Reuters", impact["source"])
        self.assertIn("data-center supply contract", impact["headline"])
        self.assertTrue(impact["verified"])
        self.assertTrue(impact["decisionInlineEligible"])

    def test_compact_news_impact_rejects_partner_story_without_explicit_inline_contract(self):
        context = {
            "researchEvidence": [{
                "kind": "news",
                "title": "Partner company reports a quantum milestone with NVIDIA support",
                "source": "Yahoo Finance",
                "readStatus": "body",
                "materialityState": "material",
                "relevanceState": "direct",
                "sourceTrustState": "standard",
                "dataState": "sufficient",
                "validationState": "ready",
                "stockImpactPolarity": "support",
                "decisionInlineEligible": False,
                "decisionInlineReasonKo": "파트너사 자체 결과라 엔비디아의 신규 실적·계약 사실은 아닙니다.",
                "eventTakeaway": "파트너사의 독자적 성과를 설명한 기사입니다.",
            }]
        }

        self.assertEqual({}, news_story_impact_from_context(context))

    def test_compact_news_requires_new_relation_evidence_for_this_decision(self):
        impact = {
            "decisionInlineEligible": True,
            "evidenceKeys": ["research:nvda:news:contract"],
            "identityKeys": ["url:other"],
        }
        relation_diff = {
            "materialComponents": ["evidenceKeys", "actionEnvelope"],
            "addedEvidenceKeys": ["research:nvda:news:contract"],
            "decisionTransition": {"material": True, "kind": "action-changed"},
        }

        self.assertTrue(news_story_changes_decision(impact, relation_diff))
        relation_diff["addedEvidenceKeys"] = ["research:nvda:news:other"]
        self.assertFalse(news_story_changes_decision(impact, relation_diff))


if __name__ == "__main__":
    unittest.main()
