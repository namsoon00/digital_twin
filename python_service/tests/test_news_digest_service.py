import unittest
from types import SimpleNamespace

from digital_twin.application.news_digest_service import NewsDigestEnqueuer
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.events import DomainEvent, RESEARCH_EVIDENCE_COLLECTED
from digital_twin.domain.investment_research import ResearchEvidence
from digital_twin.domain.message_types import NEWS_DIGEST
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.notification_templates import NotificationTemplate, render_notification


class MemoryNotificationQueue:
    def __init__(self, recent_jobs=None):
        self.jobs = []
        self.recent_jobs = list(recent_jobs or [])

    def enqueue(self, job):
        self.jobs.append(job)
        return True

    def recent(self, limit=40, message_type="", status=""):
        rows = []
        for job in self.recent_jobs:
            if message_type and job.message_type != message_type:
                continue
            if status and job.status != status:
                continue
            rows.append(job)
        return rows[:limit]


class NewsDigestEnqueuerTests(unittest.TestCase):
    def account(self):
        return AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "",
            "",
            "",
            ["AAPL"],
        )

    def evidence(self):
        return ResearchEvidence(
            "research:AAPL:news:apple-openai",
            "AAPL",
            "news",
            "Semafor",
            "Apple OpenAI lawsuit highlights broader tensions",
            "애플과 OpenAI 관련 소송 이슈입니다.",
            "https://example.test/apple?utm_source=news&ref=long",
            "2026-07-11T00:10:00Z",
            "risk",
            8.0,
            0.82,
            published_at="2026-07-11T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 97,
                "sourceReliability": 82,
                "materialityScore": 84,
                "stockImpactPolarity": "risk",
                "stockImpactLabel": "위험",
                "stockImpactScore": 81,
                "articleReadStatus": "body",
                "articleAnalysisSource": "article-body",
                "articleSummaryKo": "애플을 직접 다룬 법적 이슈로 다음 장 가격 반응 확인이 필요합니다.",
                "articleFacts": {
                    "readStatus": "body",
                    "readStatusLabel": "전체 본문 읽음",
                    "eventTakeaway": "애플 관련 소송·규제 이슈가 투자심리 부담으로 부각",
                    "numbers": ["12%"],
                    "topics": ["AI"],
                    "keySentences": ["Apple lawsuit claims new AI service used trade secrets."],
                },
            },
        )

    def enqueuer(self, queue):
        monitor_store = SimpleNamespace(previous={
            "main": {
                "positions": {},
                "watchlist": {
                    "AAPL": {"symbol": "AAPL", "name": "Apple", "market": "NASDAQ"},
                },
            }
        })
        return NewsDigestEnqueuer(
            account_repository=SimpleNamespace(load=lambda: [self.account()]),
            monitor_store=monitor_store,
            queue=queue,
            settings={},
        )

    def test_enqueues_news_digest_with_short_source_link(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [self.evidence().to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual(NEWS_DIGEST, job.message_type)
        self.assertEqual("AAPL", job.context["symbol"])
        self.assertEqual("research:AAPL:news:apple-openai", job.context["newsDigest"]["primaryEvidenceId"])
        self.assertIn('href="https://example.test/apple?utm_source=news&amp;ref=long">원문 보기</a>', job.text)
        self.assertTrue(job.text.startswith("🔔 새 알림 · 새 뉴스 1건"))
        self.assertNotIn("• 원문: https://example.test", job.text)
        self.assertIn("기사일: 07/11 09:00 KST", job.text)
        self.assertIn("분석: 기사 본문 읽음", job.text)
        self.assertIn("판단 근거: 핵심 애플 관련 소송", job.text)
        self.assertIn("계정 성향 기준", job.text)
        self.assertIn("계정 성향: 균형형", job.text)
        self.assertEqual("balanced", job.context["investmentStrategyProfile"])
        self.assertIn("investmentStrategyGuidance", job.context)
        self.assertEqual(1, len(job.context["newsDigest"]["items"]))
        self.assertTrue(job.context["newsDigest"]["items"][0]["identityKeys"])
        self.assertTrue(job.context["newsDigest"]["articleKeys"])

    def test_ignores_article_already_sent_with_same_normalized_title(self):
        previous = NotificationJob.create(
            "previous",
            account_id="main",
            message_type=NEWS_DIGEST,
            context={
                "messageType": NEWS_DIGEST,
                "accountId": "main",
                "newsDigest": {
                    "items": [
                        {
                            "kind": "news",
                            "evidenceId": "research:AAPL:news:old",
                            "title": "Apple OpenAI lawsuit highlights broader tensions - Semafor",
                            "url": "https://other.example/apple-openai-lawsuit",
                        }
                    ],
                    "primaryEvidenceId": "research:AAPL:news:old",
                    "primaryTitle": "Apple OpenAI lawsuit highlights broader tensions - Semafor",
                    "primaryUrl": "https://other.example/apple-openai-lawsuit",
                },
            },
        )
        previous.status = "done"
        queue = MemoryNotificationQueue([previous])
        evidence = self.evidence()
        evidence.evidence_id = "research:AAPL:news:new-provider"
        evidence.url = "https://example.test/apple?utm_source=newsletter"
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [evidence.to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)

    def test_news_digest_renders_ai_article_summary_and_signals(self):
        queue = MemoryNotificationQueue()
        evidence = self.evidence()
        evidence.raw_payload["aiAnalysis"] = {
            "version": "news-ai-analysis-v1",
            "impactPolarity": "risk",
            "impactLabelKo": "악재",
            "confidence": 0.82,
            "materialityScore": 88,
            "summary": {
                "briefKo": "애플 관련 법적 이슈가 투자심리 부담으로 작용할 수 있습니다.",
                "watchPoints": ["원문 본문 확보", "다음 장 가격 반응"],
            },
            "riskSignals": ["소송", "규제"],
            "supportSignals": [],
            "contrastSignals": ["however"],
            "impactReasonKo": "소송 이슈가 투자심리 부담으로 작용할 수 있습니다.",
            "portfolioImplicationKo": "Apple 보유·관심 기준으로는 법적 리스크가 가격 변동성으로 이어지는지 확인해야 합니다.",
            "actionBoundaryKo": "자동 매매 판단이 아니라 원문과 다음 장 가격 반응 확인 조건입니다.",
        }
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [evidence.to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        job = queue.jobs[0]
        self.assertIn("이번 뉴스 핵심", job.text)
        self.assertIn("Apple(AAPL): 단기 경계", job.text)
        self.assertIn("판단: 영향 악재", job.text)
        self.assertIn("핵심 내용: 애플 관련 법적 이슈", job.text)
        self.assertIn("투자 영향: Apple 보유·관심 기준", job.text)
        self.assertIn("대응 경계: 자동 매매 판단이 아니라", job.text)
        self.assertNotIn("핵심 근거:", job.text)
        self.assertIn("알림이 온 이유", job.text)
        self.assertIn("새 뉴스가 들어왔습니다: Apple OpenAI lawsuit", job.text)
        self.assertIn("Apple / AAPL 관심 종목과 직접 관련된 악재 뉴스", job.text)
        self.assertIn("관련성·중요도 97점·84점 기준을 통과", job.text)
        self.assertIn("단독 매수·매도 신호가 아니라", job.text)
        self.assertNotIn("실제 영향 요약", job.text)
        self.assertNotIn("먼저 볼 것", job.text)
        self.assertNotIn("영향 해석:", job.text)
        self.assertNotIn("보유/관심 영향:", job.text)
        self.assertNotIn("내용 요약:", job.text)
        self.assertIn("확인할 것: 원문 본문 확보, 다음 장 가격 반응", job.text)

    def test_news_digest_groups_plain_impact_before_article_details(self):
        queue = MemoryNotificationQueue()
        first = self.evidence()
        first.raw_payload["aiAnalysis"] = {
            "version": "news-ai-analysis-v1",
            "impactPolarity": "risk",
            "impactLabelKo": "악재",
            "confidence": 0.76,
            "materialityScore": 86,
            "summary": {"briefKo": "실적 발표 전 주가 하락과 밸류에이션 논쟁이 핵심입니다.", "watchPoints": ["다음 장 가격 반응"]},
            "impactReasonKo": "쿠팡에는 실적 발표 전 주가 하락과 밸류에이션 논쟁 부담이 우세합니다.",
            "portfolioImplicationKo": "쿠팡 보유 기준으로는 추가 하락이나 거래량 확대 여부를 먼저 확인해야 합니다.",
            "actionBoundaryKo": "자동 매도 판단이 아니라 실적과 거래량 확인 조건입니다.",
            "riskSignals": ["slides", "valuation debate"],
        }
        first.symbol = "CPNG"
        first.title = "Coupang (CPNG) Slides Ahead Of Earnings As The Valuation Debate Heats Up"
        first.raw_payload.update({
            "relevanceScore": 97,
            "materialityScore": 86,
            "stockImpactPolarity": "risk",
            "stockImpactLabel": "악재",
            "stockImpactScore": 82,
        })
        monitor_store = SimpleNamespace(previous={
            "main": {
                "positions": {"CPNG": {"symbol": "CPNG", "name": "쿠팡", "market": "NYSE"}},
                "watchlist": {},
            }
        })
        enqueuer = NewsDigestEnqueuer(
            account_repository=SimpleNamespace(load=lambda: [self.account()]),
            monitor_store=monitor_store,
            queue=queue,
            settings={},
        )
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:CPNG",
            payload={"materialChangedItems": [first.to_dict()]},
        )

        enqueuer.handle(event)

        job = queue.jobs[0]
        self.assertLess(job.text.index("이번 뉴스 핵심"), job.text.index("기사 상세"))
        self.assertIn("쿠팡(CPNG): 단기 경계. 쿠팡 보유 기준", job.text)
        self.assertIn("투자 영향: 쿠팡 보유 기준", job.text)

    def test_ignores_feed_only_article_by_default(self):
        queue = MemoryNotificationQueue()
        feed_only = self.evidence()
        feed_only.raw_payload["articleReadStatus"] = "feed-summary"
        feed_only.raw_payload["articleAnalysisSource"] = "feed-summary"
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [feed_only.to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)

    def test_ignores_social_source_blocked_article_by_default(self):
        queue = MemoryNotificationQueue()
        social = self.evidence()
        social.source = "facebook.com"
        social.raw_payload.update({
            "sourceReliability": 0.25,
            "articleReadStatus": "source-blocked",
            "articleAnalysisSource": "source-quality-gate",
        })
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [social.to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)

    def test_ignores_low_quality_news_by_default(self):
        queue = MemoryNotificationQueue()
        weak = self.evidence()
        weak.raw_payload.update({
            "sourceReliability": 58,
            "materialityScore": 66.5,
            "stockImpactPolarity": "context",
            "stockImpactLabel": "중립",
            "stockImpactScore": 50,
            "articleSummaryKo": "본문 요약: SK하이닉스 상장 제목만 확인됐습니다.",
        })
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [weak.to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)

    def test_reclassifies_and_ignores_stale_platform_noise_even_when_quality_gate_is_relaxed(self):
        queue = MemoryNotificationQueue()
        platform_noise = self.evidence()
        platform_noise.source = "Naver Blog"
        platform_noise.title = "카카오게임즈, 자사주 소각 카드 꺼냈다 : 네이버 블로그"
        platform_noise.raw_payload.update({
            "analysisVersion": "news-analysis-v2-domain-ontology",
            "relationScope": "direct",
            "relevanceScore": 95,
            "sourceReliability": 82,
            "materialityScore": 90,
            "stockImpactPolarity": "support",
            "stockImpactLabel": "호재",
            "articleReadStatus": "body",
            "qualityGate": {
                "decision": "accept",
                "reason": "",
            },
        })
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [platform_noise.to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )
        enqueuer = self.enqueuer(queue)
        enqueuer.settings["newsDigestHighQualityOnly"] = "0"

        enqueuer.handle(event)

        self.assertEqual([], queue.jobs)

    def test_rendered_news_digest_does_not_append_generic_ai_sections(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [self.evidence().to_dict()]},
        )
        self.enqueuer(queue).handle(event)
        job = queue.jobs[0]

        rendered = render_notification(NotificationTemplate("newsDigest", "{body}"), job.context)

        self.assertNotIn("AI 의견", rendered)
        self.assertNotIn("모델 판단", rendered)
        self.assertIn("뉴스/피드 새 정보", rendered)

    def test_news_digest_omits_repeated_first_watch_section(self):
        queue = MemoryNotificationQueue()
        first = self.evidence()
        second = self.evidence()
        second.evidence_id = "research:AAPL:news:apple-openai-2"
        second.title = "Apple OpenAI lawsuit follow-up"
        second.url = "https://example.test/apple-2"
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [first.to_dict(), second.to_dict()]},
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual(1, len(queue.jobs))
        self.assertNotIn("먼저 볼 것", queue.jobs[0].text)
        self.assertIn("기사 상세", queue.jobs[0].text)
        self.assertIn("1. Apple / AAPL", queue.jobs[0].text)
        self.assertIn("2. Apple / AAPL", queue.jobs[0].text)

    def test_ignores_collection_event_without_material_items(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [],
                "changedItems": [self.evidence().to_dict()],
                "materialChangedCount": 0,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)


if __name__ == "__main__":
    unittest.main()
