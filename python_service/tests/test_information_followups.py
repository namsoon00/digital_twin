import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from urllib.error import HTTPError

from digital_twin.infrastructure.external_signal_utils import ExternalApiGuard, ExternalRateLimited
from digital_twin.infrastructure.external_api.adapters.official_release import OfficialReleaseAdapter
from digital_twin.infrastructure.news_sources import extract_article_text
from digital_twin.infrastructure.provider_http_budget import ProviderHttpBudget
from digital_twin.infrastructure.reloading_collection_runner import ReloadingCollectionRunner
from digital_twin.modules.market_data.application.information_followup_service import InformationFollowupService, stamp
from digital_twin.modules.market_data.application.information_observation_service import InformationObservationService
from digital_twin.modules.market_data.domain.information_observation import exact_time
from digital_twin.modules.market_data.public import CollectionJob, ExternalSubject
from digital_twin.modules.notifications.application.notification.intake import NotificationIngressService
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.domain.information_update import information_update_message
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.notification_templates import NotificationTemplate, render_notification
from digital_twin.modules.market_data.contracts import ExternalCallDeferred
from digital_twin.platform.domain.data_pipeline_health import evaluate_news_collection_health


NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


class MemoryFollowups:
    def __init__(self):
        self.rows = {}
        self.phases = []

    def get(self, identity):
        return self.rows.get(identity)

    def register(self, row):
        if row['trackingId'] in self.rows:
            return False
        for old in self.rows.values():
            if old['sourceId'] == row['sourceId'] and old['status'] == 'active':
                old['status'] = 'superseded'
        self.rows[row['trackingId']] = copy.deepcopy(row)
        return True

    def due(self, now, limit):
        return [copy.deepcopy(row) for row in self.rows.values() if row['status'] == 'active' and row['nextCheckAt'] <= now][:limit]

    def complete(self, row, event, phases):
        self.rows[row['trackingId']] = copy.deepcopy(row)
        for phase in phases:
            self.phases.append((phase, copy.deepcopy(row), event))
        return True


def source():
    return {'sourceKind': 'research', 'sourceId': 'example-news', 'sourceHash': 'body-v1', 'verified': True,
        'eventAt': stamp(NOW), 'title': 'Example company result', 'symbols': ['EXAMPLE'], 'sourceUrl': 'https://example.org/story'}


def quote(at, price):
    return {'symbol': 'EXAMPLE', 'sourceAsOf': stamp(at), 'generatedAt': stamp(at), 'currentPrice': price,
        'provider': 'sample', 'currency': 'USD', 'observationGranularity': '3m', 'dataQuality': 'live', 'privateAccount': 'must-not-retain'}


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        self.reader = MagicMock()
        self.reader.load_baseline_observations.return_value = {'EXAMPLE': quote(NOW - timedelta(minutes=1), 100)}
        self.reader.load_outcome_observations.return_value = {
            'EXAMPLE:60': quote(NOW + timedelta(minutes=61), 102),
            'EXAMPLE:1440': quote(NOW + timedelta(minutes=1441), 103)}
        self.items = [source()]
        self.repo = MemoryFollowups()
        self.observer = InformationObservationService(self.reader, now=lambda: self.clock)
        self.service = InformationFollowupService(self.repo, lambda now: self.items, self.observer, {}, now=lambda: self.clock)

    def test_restart_safe_measurements_and_once_per_horizon(self):
        self.assertEqual(self.service.run_once()['registeredCount'], 1)
        row = next(iter(self.repo.rows.values()))
        self.assertNotIn('privateAccount', json.dumps(row))
        self.clock += timedelta(minutes=62)
        self.service.run_once()
        self.service.run_once()
        self.assertEqual([entry[0] for entry in self.repo.phases], ['60'])
        self.reader.load_baseline_observations.return_value = {}
        self.clock = NOW + timedelta(minutes=1442)
        restarted = InformationFollowupService(self.repo, lambda now: self.items, self.observer, {}, now=lambda: self.clock)
        restarted.run_once()
        restarted.run_once()
        self.assertEqual([entry[0] for entry in self.repo.phases], ['60', '1440'])
        row = next(iter(self.repo.rows.values()))
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['marketReaction']['observations'][0]['priceChangePercent'], 2)
        self.assertFalse(self.repo.phases[0][2].payload['decisionAuthority'])

    def test_old_backfill_has_no_retroactive_price_alert(self):
        self.clock = NOW + timedelta(hours=25)
        self.service.run_once()
        self.assertEqual(self.repo.phases, [])
        self.assertEqual(next(iter(self.repo.rows.values()))['status'], 'completed')

    def test_no_synthetic_price_for_missing_or_incompatible_data(self):
        self.reader.load_outcome_observations.return_value = {'EXAMPLE:60': {**quote(NOW + timedelta(minutes=61), 999), 'currency': 'KRW'}}
        self.service.run_once()
        self.clock += timedelta(hours=28)
        self.service.run_once()
        self.assertEqual(self.repo.phases, [])
        self.assertTrue(all(item['status'] == 'missing' for item in next(iter(self.repo.rows.values()))['marketReaction']['observations']))

    def test_withdrawn_source_cancels_without_stale_followup(self):
        self.service.run_once()
        self.service.validate_source = lambda row: False
        self.clock += timedelta(minutes=62)
        self.service.run_once()
        row = next(iter(self.repo.rows.values()))
        self.assertEqual(row['status'], 'canceled')
        self.assertFalse(row['marketReaction']['notificationRegistered'])
        self.assertEqual(self.repo.phases, [])

    def test_changed_source_creates_new_version_and_retires_old(self):
        self.service.run_once()
        self.items[0]['sourceHash'] = 'body-v2'
        self.clock += timedelta(minutes=5)
        self.service.run_once()
        self.assertEqual(sorted(row['status'] for row in self.repo.rows.values()), ['active', 'superseded'])

    def test_failed_read_does_not_complete_or_notify(self):
        self.service.run_once()
        self.reader.load_outcome_observations.side_effect = RuntimeError('unavailable')
        self.clock += timedelta(hours=28)
        self.assertEqual(self.service.run_once()['status'], 'degraded')
        self.assertEqual(next(iter(self.repo.rows.values()))['status'], 'active')
        self.assertEqual(self.repo.phases, [])

    def test_registration_error_is_reported(self):
        self.reader.load_baseline_observations.side_effect = RuntimeError('database unavailable')
        result = self.service.run_once()
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['errors'][0]['reason'], 'baseline-read-error')

    def test_disabled_and_unverified_sources_never_register(self):
        self.items[0]['verified'] = False
        self.assertEqual(self.service.run_once()['registeredCount'], 0)
        self.service.settings['informationFollowupEnabled'] = '0'
        self.assertEqual(self.service.run_once()['status'], 'disabled')

    def test_date_only_release_does_not_invent_midnight_prices(self):
        self.items = [{**source(), 'sourceKind': 'calendar', 'eventAt': '', 'publicationDate': '2026-09-14',
            'releaseMetrics': [{'label': 'Rate', 'actual': 3.0, 'previous': 2.75, 'unit': '%'}]}]
        self.service.run_once()
        self.assertEqual([entry[0] for entry in self.repo.phases], ['release'])
        row = next(iter(self.repo.rows.values()))
        self.assertEqual(row['baselineSnapshots'], {})
        self.assertEqual(row['marketReaction']['observations'], [])
        text = information_update_message(row, 'release')
        self.assertIn('날짜만 확인', text)
        self.assertNotIn('00:00', text)

    def test_rendered_message_preserves_measurements_without_generic_ai(self):
        self.service.run_once()
        self.clock += timedelta(minutes=62)
        self.service.run_once()
        row = self.repo.phases[0][1]
        row['source']['title'] = '<script>not HTML</script>'
        body = information_update_message(row, '60')
        job = NotificationJob.create(body, message_type='informationUpdate', context={'body': body, 'notificationAiSkip': True})
        NotificationIngressService.prepare_job(job)
        rendered = NotificationRenderingService(template_renderer=lambda current: render_notification(NotificationTemplate.default('informationUpdate'), current.context)).render(job)
        self.assertIn('+2.00%', rendered)
        self.assertIn('100.0', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('단일 신호', rendered)
        self.assertNotIn('AI 의견', rendered)


class FreeSourceTests(unittest.TestCase):
    def test_rejected_candidates_do_not_reset_usable_news_freshness(self):
        previous = {'state': 'healthy', 'firstObservedAt': stamp(NOW - timedelta(minutes=10)), 'lastNonZeroAt': stamp(NOW - timedelta(minutes=10))}
        result = {'status': 'ok', 'targetCount': 3, 'fetchedCount': 5, 'admittedCount': 0, 'savedCount': 0,
            'statuses': [{'source': 'google_rss_kr', 'ok': True, 'count': 5}]}
        health = evaluate_news_collection_health(result, previous, now=NOW)
        self.assertEqual(health.reason_code, 'collected-items-not-admitted')
        self.assertEqual(health.last_non_zero_at, previous['lastNonZeroAt'])
        self.assertEqual(health.consecutive_zero_runs, 1)
        self.assertEqual(evaluate_news_collection_health(result, previous, now=NOW + timedelta(hours=4)).state, 'stale')

    def test_retry_after_defers_instead_of_retry_burst(self):
        for header in ['120', 'Mon, 14 Sep 2026 00:02:00 GMT']:
            fetch = MagicMock(side_effect=HTTPError('https://example.org', 429, 'rate limit', {'Retry-After': header}, None))
            sleep = MagicMock()
            guard = ExternalApiGuard({}, now=lambda: NOW, sleep=sleep)
            with self.assertRaises(ExternalRateLimited) as raised:
                guard.call('test', 'test', fetch, 3, 0, 2, 5)
            self.assertEqual(exact_time(raised.exception.retry_at), NOW + timedelta(seconds=120))
            self.assertEqual(fetch.call_count, 1)
            sleep.assert_not_called()
            with self.assertRaises(ExternalCallDeferred):
                guard.call('test', 'test', fetch, 3, 0, 2, 5)
            self.assertEqual(fetch.call_count, 1)

    def test_budget_denial_never_calls_vendor_or_counts_failure(self):
        store = MagicMock()
        store.reserve_provider_call.return_value = {'allowed': False, 'reason': 'daily-budget', 'nextAllowedAt': '2026-09-15T00:00:00Z'}
        fetch = MagicMock()
        with self.assertRaises(ExternalRateLimited):
            ProviderHttpBudget(store, {}).call(fetch)
        fetch.assert_not_called()
        store.record_http_failure.assert_not_called()
        descriptor = store.reserve_provider_call.call_args.args[0]
        self.assertEqual(descriptor.daily_request_budget, 240)
        self.assertEqual(descriptor.rate_limit_seconds, 6)

    def test_gdelt_http_quota_persists_retry_after_without_second_attempt(self):
        store = MagicMock()
        store.reserve_provider_call.return_value = {'allowed': True}
        fetch = MagicMock(side_effect=HTTPError('https://example.org', 429, 'quota', {'Retry-After': '1800'}, None))
        with self.assertRaises(ExternalRateLimited) as raised:
            ProviderHttpBudget(store, {}).call(fetch)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(store.record_http_failure.call_args.args[2], raised.exception.retry_at)
        store.mark_provider_success.assert_not_called()

    def test_bls_403_waits_six_hours_without_retries(self):
        fetch = MagicMock(side_effect=HTTPError('https://www.bls.gov/news.release/cpi.nr0.htm', 403, 'denied', {}, None))
        adapter = OfficialReleaseAdapter('bls', fetch_text=fetch, now=lambda: NOW)
        job = CollectionJob(dataset_id=adapter.descriptor.dataset_id, partition_key='cpi', provider_id=adapter.descriptor.provider_id, priority=45, subject=ExternalSubject('release:cpi'))
        with self.assertRaises(ExternalCallDeferred) as raised:
            adapter.fetch(job, {})
        self.assertEqual(raised.exception.reason, 'access-denied')
        self.assertEqual(exact_time(raised.exception.retry_at), NOW + timedelta(hours=6))
        self.assertEqual(fetch.call_count, 1)

    def test_article_scope_excludes_teaser_navigation_and_duplicate_copy(self):
        body = 'Example Company reported revenue growth of twenty percent and raised its forecast.'
        markup = '<meta name="description" content="Truncated teaser"><nav><p>Unrelated cryptocurrency advertisement navigation</p></nav><article><p>' + body + '</p><aside><p>Unrelated market recommendation for another stock.</p></aside><p>' + body + '</p></article>'
        self.assertEqual(extract_article_text(markup), body)
        structured = '<script type="application/ld+json">' + json.dumps({'@type': 'NewsArticle', 'articleBody': body}) + '</script>'
        self.assertEqual(extract_article_text(markup + structured), body)

    def test_empty_article_does_not_fall_back_to_unrelated_page_copy(self):
        markup = '<article><div class="related"><p>Unrelated article that should not become source text.</p></div></article><p>Another unrelated story outside the article body.</p>'
        self.assertEqual(extract_article_text(markup), '')
        body = 'Company earnings rose after its core business recovered during the quarter.'
        markup = '<article class="story-summary">This automated summary is not the actual article body.</article><div class="story-news"><p>' + body + '</p><figcaption>[Photo] An older caption with irrelevant date and company.</figcaption></div>'
        self.assertEqual(extract_article_text(markup), body)

    def test_advertising_revenue_is_business_content_not_page_ad(self):
        text = 'NAVER reported higher advertisement revenue and a stronger earnings forecast for the coming quarter.'
        self.assertEqual(extract_article_text('<article><p>' + text + '</p></article>'), text)

    def test_worker_reloads_contact_and_rate_policy_at_cycle_boundary(self):
        first, second = MagicMock(), MagicMock()
        factory = MagicMock(return_value=second)
        settings = {'externalSecContactEmail': '', 'newsCollectionGdeltDailyRequestBudget': '240'}
        wrapped = ReloadingCollectionRunner(first, factory, lambda: dict(settings), dict(settings))
        wrapped.run_once()
        factory.assert_not_called()
        settings['externalSecContactEmail'] = 'contact@example.org'
        wrapped.run_once()
        wrapped.run_once()
        factory.assert_called_once_with(settings)
        self.assertEqual(second.run_once.call_count, 2)


if __name__ == '__main__':
    unittest.main()
