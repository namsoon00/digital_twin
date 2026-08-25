import unittest
from types import SimpleNamespace

from digital_twin.application.news_digest_service import (
    NewsDigestEnqueuer,
    confirmed_fact_lines,
    item_investment_impact,
    item_summary,
    item_watch_text,
)
from digital_twin.application.notification_service import DisclosureAnalysisNotificationEnricher
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
                "relevanceState": "direct",
                "sourceTrustState": "trusted",
                "materialityState": "material",
                "dataState": "sufficient",
                "validationState": "ready",
                "bodyQualityPassed": True,
                "summaryQualityState": "ready",
                "articleSummaryQuality": {"state": "ready", "issues": []},
                "aiAnalysis": {
                    "status": "ok",
                    "needsReview": False,
                    "sourceLanguage": "en",
                    "originalTitle": "Apple OpenAI lawsuit highlights broader tensions",
                    "translatedTitleKo": "애플과 OpenAI 소송이 더 큰 긴장을 부각",
                    "translationStatus": "complete",
                },
                "articleFacts": {
                    "readStatus": "body",
                    "readStatusLabel": "전체 본문 읽음",
                    "bodyAvailable": True,
                    "bodyQualityPassed": True,
                    "eventTakeaway": "애플 관련 소송·규제 이슈가 투자심리 부담으로 부각",
                    "numbers": ["12%"],
                    "topics": ["AI"],
                    "keySentences": ["Apple lawsuit claims new AI service used trade secrets."],
                },
            },
        )

    def enqueuer(self, queue, evidence_repository=None):
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
            evidence_repository=evidence_repository,
        )

    def test_durable_compact_event_reloads_canonical_quality_state(self):
        queue = MemoryNotificationQueue()
        evidence = self.evidence()
        repository = SimpleNamespace(get=lambda evidence_id: evidence if evidence_id == evidence.evidence_id else None)
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "alertEligibleItems": [{
                    "evidenceId": evidence.evidence_id,
                    "symbol": "AAPL",
                    "kind": "news",
                    "title": evidence.title,
                }],
            },
        )

        self.enqueuer(queue, repository).handle(event)

        self.assertEqual(1, len(queue.jobs))
        self.assertEqual(evidence.evidence_id, queue.jobs[0].context["newsDigest"]["primaryEvidenceId"])

    def test_authoritative_empty_alert_set_never_falls_back_to_material_items(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "alertEligibleCount": 0,
                "materialChangedCount": 1,
                "materialChangedItems": [self.evidence().to_dict()],
            },
        )

        queued = self.enqueuer(queue).handle(event)

        self.assertEqual(0, queued)
        self.assertEqual([], queue.jobs)

    def test_confirmed_facts_use_a_distinct_target_sentence_when_takeaway_matches_summary(self):
        item = {
            "kind": "news",
            "summary": "공정위가 네이버와 두나무 합병 심사를 연내 마무리할 수 있다고 밝혔다.",
            "payload": {
                "aiAnalysis": {
                    "summary": {
                        "briefKo": "공정위가 네이버와 두나무 합병 심사를 연내 마무리할 수 있다고 밝혔다.",
                    },
                },
                "articleFacts": {
                    "eventTakeaway": "공정위가 네이버와 두나무 합병 심사를 연내 마무리할 수 있다고 밝혔다.",
                    "keySentences": [
                        "공정위가 네이버와 두나무 합병 심사를 연내 마무리할 수 있다고 밝혔다.",
                        "네이버에 자료를 13회 요청했고 이해관계자 의견청취도 8월 말까지 진행한다.",
                    ],
                    "numbers": [],
                },
            },
        }

        lines = confirmed_fact_lines(item)

        self.assertEqual(["네이버에 자료를 13회 요청했고 이해관계자 의견청취도 8월 말까지 진행한다."], lines)

    def test_confirmed_facts_keeps_last_direct_fact_when_brief_contains_every_fact(self):
        first = "공정위가 네이버와 두나무 합병 심사를 연내 마무리할 수 있다고 밝혔다."
        second = "네이버에 자료를 13회 요청했고 이해관계자 의견청취도 8월 말까지 진행한다."
        item = {
            "kind": "news",
            "payload": {
                "aiAnalysis": {"summary": {"briefKo": first + " " + second}},
                "articleFacts": {
                    "eventTakeaway": first,
                    "keySentences": [first, second],
                    "numbers": [],
                },
            },
        }

        self.assertEqual([second], confirmed_fact_lines(item))

    def test_disclosure_alert_uses_structured_document_analysis(self):
        item = {
            "kind": "disclosure",
            "publishedAt": "2026-08-25T00:00:00Z",
            "payload": {
                "receiptNo": "202608250001",
                "disclosureAnalysis": {
                    "summary": "이사회가 자기주식 취득을 결의했습니다.",
                    "impactSummary": "유통 주식 수 감소 가능성을 확인할 공시입니다.",
                    "confirmedFacts": ["보통주 1,000,000주를 취득하기로 결정했습니다."],
                    "watchItems": ["실제 취득 체결 내역을 확인합니다."],
                },
            },
        }

        self.assertEqual("이사회가 자기주식 취득을 결의했습니다", item_summary(item))
        self.assertEqual(["보통주 1,000,000주를 취득하기로 결정했습니다."], confirmed_fact_lines(item))
        self.assertEqual("유통 주식 수 감소 가능성을 확인할 공시입니다", item_investment_impact(item))
        self.assertEqual("실제 취득 체결 내역을 확인합니다.", item_watch_text(item))

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
        self.assertTrue(job.text.startswith("📰 뉴스 · Apple / AAPL"))
        self.assertNotIn("• 원문: https://example.test", job.text)
        self.assertIn("07/11 09:00 KST", job.text)
        self.assertIn("확인된 사실", job.text)
        self.assertIn("애플 관련 소송·규제 이슈가 투자심리 부담으로 부각", job.text)
        self.assertIn("본문 수치: 12%", job.text)
        self.assertIn("시장 확인", job.text)
        self.assertIn("다음 장 가격 반응과 거래량 동반 여부", job.text)
        self.assertNotIn("판단 근거:", job.text)
        self.assertNotIn("계정 성향 기준", job.text)
        self.assertEqual("balanced", job.context["investmentStrategyProfile"])
        self.assertIn("investmentStrategyGuidance", job.context)
        self.assertEqual("news", job.context["newsDigest"]["eventKind"])
        self.assertEqual("new-event", job.context["newsDigest"]["deliveryMode"])
        self.assertEqual(1, len(job.context["newsDigest"]["items"]))
        self.assertTrue(job.context["newsDigest"]["items"][0]["identityKeys"])
        self.assertTrue(job.context["newsDigest"]["articleKeys"])
        self.assertEqual("Semafor", job.context["dataFreshness"]["source"])
        self.assertEqual("2026-07-11T00:00:00Z", job.context["dataFreshness"]["sourceAsOf"])

    def test_news_digest_uses_dispatch_clock_for_article_age(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            occurred_at="2026-07-11T06:00:00Z",
            payload={"materialChangedItems": [self.evidence().to_dict()]},
        )

        self.enqueuer(queue).handle(event)

        job = queue.jobs[0]
        self.assertEqual("2026-07-11T06:00:00Z", job.context["referenceDate"])
        self.assertIn("6시간 전 · 07/11 09:00 KST", job.text)

    def test_rejects_body_with_google_result_contamination_before_digest(self):
        queue = MemoryNotificationQueue()
        evidence = self.evidence()
        evidence.symbol = "000660"
        evidence.title = '"적자 땐 임금조정"…SK하이닉스 제안 - 한국경제'
        evidence.url = "https://www.hankyung.com/article/example"
        evidence.raw_payload.update({
            "name": "SK하이닉스",
            "articleText": (
                "적자 나면 임금 깎자고? 성과급 주식 지급과 일정 기간 매도 제한을 두고 "
                "SK하이닉스 노조가 반발하면서 임단협 진통이 예상된다. 회사는 제안을 수정하지 않으면 "
                "갈등이 길어질 수 있다고 설명했다. "
                "Google 검색에서 한국경제 기사를 더 자주 볼 수 있습니다. "
                "최태원 SK그룹 회장이 SK하이닉스 주식 약 48억원어치를 장내매수했다."
            ),
            "articleFacts": {
                "readStatus": "body",
                "bodyAvailable": True,
                "bodyQualityPassed": True,
            },
        })
        monitor_store = SimpleNamespace(previous={
            "main": {
                "positions": {"000660": {"symbol": "000660", "name": "SK하이닉스"}},
                "watchlist": {},
            },
        })
        enqueuer = NewsDigestEnqueuer(
            account_repository=SimpleNamespace(load=lambda: [self.account()]),
            monitor_store=monitor_store,
            queue=queue,
            settings={},
        )
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:000660",
            payload={"materialChangedItems": [evidence.to_dict()]},
        )

        enqueuer.handle(event)

        self.assertEqual([], queue.jobs)

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
            "status": "ok",
            "needsReview": False,
            "sourceLanguage": "en",
            "originalTitle": "Apple OpenAI lawsuit highlights broader tensions",
            "translatedTitleKo": "애플과 OpenAI 소송이 더 큰 긴장을 부각",
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
        self.assertNotIn("이번 뉴스 핵심", job.text)
        self.assertIn("한국어 제목", job.text)
        self.assertIn("애플과 OpenAI 소송이 더 큰 긴장을 부각", job.text)
        self.assertIn("한 줄 요약", job.text)
        self.assertIn("확인된 사실", job.text)
        self.assertIn("AI 해석 · 조건부", job.text)
        self.assertIn("Apple 보유·관심 기준", job.text)
        self.assertNotIn("대응 경계:", job.text)
        self.assertNotIn("핵심 근거:", job.text)
        self.assertIn("알림이 온 이유", job.text)
        self.assertIn("Apple / AAPL 관심 종목의 새 기사", job.text)
        self.assertIn("종목 직접 관련 · 투자 판단에 중요한 사건 조건을 통과", job.text)
        self.assertIn("기사 한 건만으로 매수·매도를 결정하지 않고", job.text)
        self.assertNotIn("실제 영향 요약", job.text)
        self.assertNotIn("먼저 볼 것", job.text)
        self.assertNotIn("투자 영향:", job.text)
        self.assertIn("다음 확인: 원문 본문 확보, 다음 장 가격 반응", job.text)

    def test_news_digest_groups_plain_impact_before_article_details(self):
        queue = MemoryNotificationQueue()
        first = self.evidence()
        first.raw_payload["aiAnalysis"] = {
            "version": "news-ai-analysis-v1",
            "status": "ok",
            "needsReview": False,
            "impactPolarity": "risk",
            "impactLabelKo": "악재",
            "sourceLanguage": "en",
            "originalTitle": "Coupang (CPNG) Slides Ahead Of Earnings As The Valuation Debate Heats Up",
            "translatedTitleKo": "쿠팡, 실적 발표를 앞두고 밸류에이션 논쟁 속 하락",
            "translationStatus": "complete",
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
        first.summary = "쿠팡 실적 발표 전 주가 하락과 밸류에이션 논쟁이 핵심입니다."
        first.raw_payload.update({
            "relevanceScore": 97,
            "materialityScore": 86,
            "stockImpactPolarity": "risk",
            "stockImpactLabel": "악재",
            "stockImpactScore": 82,
            "articleSummaryKo": "쿠팡 실적 발표 전 주가 하락과 밸류에이션 논쟁이 핵심입니다.",
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
        self.assertNotIn("이번 뉴스 핵심", job.text)
        self.assertIn("AI 해석 · 조건부", job.text)
        self.assertIn("쿠팡 보유 기준", job.text)

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
        self.assertIn("뉴스 · Apple / AAPL", rendered)

    def test_news_digest_sends_unrelated_events_separately(self):
        queue = MemoryNotificationQueue()
        first = self.evidence()
        second = self.evidence()
        second.evidence_id = "research:AAPL:news:apple-openai-2"
        second.title = "Apple OpenAI lawsuit follow-up"
        second.url = "https://example.test/apple-2"
        second.raw_payload["articleFacts"]["eventTakeaway"] = "애플의 별도 서비스 가격 정책 변경이 매출 전망에 미치는 영향"
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [first.to_dict(), second.to_dict()]},
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual(2, len(queue.jobs))
        self.assertTrue(all("기사 상세" not in job.text for job in queue.jobs))
        self.assertTrue(all("먼저 볼 것" not in job.text for job in queue.jobs))

    def test_same_event_clusters_sources_and_shows_korean_translation(self):
        queue = MemoryNotificationQueue()
        first = self.evidence()
        first.raw_payload.update({
            "storyClusterId": "apple-openai-lawsuit-20260711",
            "aiAnalysis": {
                "status": "ok",
                "needsReview": False,
                "sourceLanguage": "en",
                "originalTitle": "Apple OpenAI lawsuit highlights broader tensions",
                "translatedTitleKo": "애플과 OpenAI 소송이 더 큰 긴장을 부각",
                "summary": {"briefKo": "애플 관련 법적 이슈의 후속 보도가 추가됐습니다."},
            },
        })
        second = self.evidence()
        second.evidence_id = "research:AAPL:news:apple-openai-reuters"
        second.source = "Reuters"
        second.title = "Apple and OpenAI lawsuit draws regulatory attention"
        second.url = "https://example.test/reuters-apple-openai"
        second.raw_payload.update({"storyClusterId": "apple-openai-lawsuit-20260711"})
        second.raw_payload["aiAnalysis"] = {
            "status": "ok",
            "needsReview": False,
            "sourceLanguage": "en",
            "originalTitle": "Apple and OpenAI lawsuit draws regulatory attention",
            "translatedTitleKo": "애플과 OpenAI 소송이 규제 당국의 관심을 받다",
            "translationStatus": "complete",
        }
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [first.to_dict(), second.to_dict()]},
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual(2, job.context["newsDigest"]["sourceCount"])
        self.assertEqual(["Semafor", "Reuters"], job.context["newsDigest"]["sources"])
        self.assertIn("한국어 제목", job.text)
        self.assertIn("애플과 OpenAI 소송이 더 큰 긴장을 부각", job.text)
        self.assertIn("함께 수집된 출처 2곳: Semafor, Reuters", job.text)

    def test_verified_new_fact_reopens_a_sent_story_as_update(self):
        initial_queue = MemoryNotificationQueue()
        first = self.evidence()
        first.raw_payload.update({
            "storyClusterId": "apple-openai-lawsuit-20260711",
            "keyFacts": ["Apple lawsuit filing alleges trade secret misuse"],
        })
        first_event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [first.to_dict()]},
        )
        self.enqueuer(initial_queue).handle(first_event)
        previous = initial_queue.jobs[0]
        previous.status = "done"

        update_queue = MemoryNotificationQueue([previous])
        follow_up = self.evidence()
        follow_up.evidence_id = "research:AAPL:news:apple-openai-followup"
        follow_up.source = "Reuters"
        follow_up.raw_payload.update({
            "storyClusterId": "apple-openai-lawsuit-20260711",
            "keyFacts": ["Apple lawsuit filing adds a new requested damages amount"],
        })
        update_event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [follow_up.to_dict()]},
        )

        self.enqueuer(update_queue).handle(update_event)

        self.assertEqual(1, len(update_queue.jobs))
        job = update_queue.jobs[0]
        self.assertEqual("story-update", job.context["newsDigest"]["deliveryMode"])
        self.assertEqual("↻", job.context["notificationIcon"])
        self.assertTrue(job.text.startswith("↻ 뉴스"))
        self.assertIn("검증된 새 사실이 추가된 후속 알림", job.text)

    def test_disclosure_is_a_separate_event_and_gets_disclosure_analysis(self):
        queue = MemoryNotificationQueue()
        disclosure = ResearchEvidence(
            "research:AAPL:dart:202607270001",
            "AAPL",
            "disclosure",
            "OpenDART",
            "자기주식 취득 결정",
            "접수일 20260727",
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202607270001",
            "2026-07-27T00:10:00Z",
            "support",
            published_at="2026-07-27T00:10:00Z",
            raw_payload={
                "relationScope": "direct",
                "eventType": "capital_policy",
                "sourceTrustState": "trusted",
                "materialityState": "material",
                "dataState": "sufficient",
                "validationState": "ready",
                "receiptNo": "202607270001",
                "receiptDate": "20260727",
                "officialDocumentText": "회사는 보통주 100만주를 취득하기로 결정했다.",
            },
        )
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="disclosure:AAPL",
            payload={"materialChangedItems": [disclosure.to_dict()]},
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual("disclosure", job.context["newsDigest"]["eventKind"])
        self.assertEqual("📄", job.context["notificationIcon"])
        self.assertTrue(job.text.startswith("📄 공시 · Apple / AAPL"))
        self.assertIn("접수번호 202607270001", job.text)
        DisclosureAnalysisNotificationEnricher(settings={})(job)
        self.assertIn("AI 공시 해석", job.context["body"])
        self.assertLess(job.context["body"].index("AI 공시 해석"), job.context["body"].index("시장 확인"))

    def test_summary_does_not_fall_back_to_the_headline(self):
        self.assertEqual("", item_summary({"title": "Headline must not become a summary"}))

    def test_english_event_waits_for_a_completed_korean_title_translation(self):
        queue = MemoryNotificationQueue()
        pending = self.evidence()
        pending.raw_payload["aiAnalysis"] = {
            "sourceLanguage": "en",
            "originalTitle": "Apple OpenAI lawsuit highlights broader tensions",
            "translationStatus": "pending",
        }
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [pending.to_dict()]},
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)

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
