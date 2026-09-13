import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.news_intelligence.domain.investment_research import ResearchEvidence
from digital_twin.modules.news_intelligence.domain.information_brief import build_information_brief, source_url
from digital_twin.modules.market_data.domain.official_release import RELEASE_URLS, latest_fomc_statement_url, parse_official_release
from digital_twin.modules.market_data.application.external_data.fact_transition_service import ExternalFactTransitionService
from digital_twin.modules.market_data.application.external_data.read_model_service import ExternalSignalsReadModelService
from digital_twin.modules.investment_calendar.domain.release_information import calendar_release_information
from digital_twin.infrastructure.external_api.adapters.official_release import OfficialReleaseAdapter
from digital_twin.modules.market_data.public import CollectionJob, ExternalDatasetRegistry, ExternalDataCollectionService
from digital_twin.modules.news_intelligence.infrastructure.mysql_research_evidence import MySQLResearchEvidenceStore
from digital_twin.modules.read_models.application.console_read_model_service import ConsoleReadModelService
from digital_twin.modules.news_intelligence.application.news_digest_service import NewsDigestEnqueuer
from digital_twin.shared_kernel.events import DomainEvent


NOW = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)
CPI = """<script>malicious text 99 percent</script><pre>
Transmission of material in this release is embargoed until
8:30 a.m. (ET) Friday, September 11, 2026 USDL-26-0000
CONSUMER PRICE INDEX - AUGUST 2026
The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.4 percent on a seasonally adjusted basis in August after rising 0.1 percent in July.
Over the last 12 months, the all items index increased 3.4 percent before seasonal adjustment.
Table A. unrelated numbers 200 300 400
</pre>"""
JOBS = """<pre>Transmission of material in this release is embargoed until
8:30 a.m. (ET) Friday, September 4, 2026 USDL-26-0000
THE EMPLOYMENT SITUATION - AUGUST 2026
Total nonfarm payroll employment increased by 162,000 in August, and the unemployment rate was unchanged at 4.1 percent.
Household Survey Data 9.9 percent</pre>"""
FED = """<p>July 29, 2026</p><h1>Federal Reserve issues FOMC statement</h1>
<p>For release at 2:00 p.m. EDT</p><p>The Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent.</p>
<p>For media inquiries</p>"""


def evidence(**raw):
    statement = "Example Company reported higher revenue for the quarter."
    return ResearchEvidence("e1", "EXAMPLE", "news", "Publisher", "Example Company revenue report",
        published_at="2026-09-12T00:00:00Z", observed_at="2026-09-12T01:00:00Z", url="https://example.org/report",
        raw_payload={"articleText": statement, "claimLedger": {"claims": [{"claimId": "c1", "statement": statement, "excerpt": statement, "sourceEvidenceId": "e1", "symbol": "EXAMPLE", "state": "reported"}]}, **raw})


def cpi_fact():
    result = parse_official_release("cpi", CPI, RELEASE_URLS["cpi"], NOW)
    return {"datasetId": "official.bls-release", "subjectKey": "release:cpi", "payload": {"officialRelease": result}, "fetchedAt": "2026-09-12T00:00:00Z"}


def cpi_event():
    return {"startsAt": "2026-09-11T12:30:00Z", "payload": {"officialSource": True, "scheduleState": "confirmed", "timeState": "official", "sourceProvider": "BLS", "country": "US", "indicator": "cpi"}}


class InformationBriefTests(unittest.TestCase):
    def test_bound_fact_and_read_only_contract(self):
        item = evidence()
        before = copy.deepcopy(item.raw_payload)
        brief = build_information_brief(item, NOW)
        self.assertEqual(brief["state"], "source-linked")
        self.assertEqual(len(brief["facts"]), 1)
        self.assertEqual(brief["facts"][0]["label"], "기사에 기재")
        self.assertEqual(brief["independentSourceCount"], 0)
        self.assertFalse(brief["decisionAuthority"])
        self.assertEqual(item.raw_payload, before)
        store = MySQLResearchEvidenceStore.__new__(MySQLResearchEvidenceStore)
        store.connect = MagicMock()
        connection = store.connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchall.return_value = []
        item.raw_payload["storyClusterId"] = "story-1"
        self.assertEqual(store.story_history(item, 100), [])
        sql, params = connection.execute.call_args.args
        self.assertIn("symbol = %s AND kind = %s", sql)
        self.assertIn("$.storyClusterId", sql)
        self.assertEqual(params, ("EXAMPLE", "news", "story-1", 20))

    def test_fabricated_and_wrong_subject_claims_are_not_facts(self):
        for change in [{"excerpt": "This company bought bitcoin despite no source support."}, {"symbol": "OTHER"}, {"sourceEvidenceId": "other"}, {"state": "conflicted"}]:
            item = evidence()
            item.raw_payload["claimLedger"]["claims"][0].update(change)
            self.assertEqual(build_information_brief(item, NOW)["facts"], [])

    def test_latest_eligibility_overrides_old_stored_approval(self):
        brief = build_information_brief(evidence(), NOW, eligibility={"reviewState": "content-invalid"})
        self.assertEqual(brief["facts"], [])
        self.assertEqual(brief["state"], "content-invalid")

    def test_retracted_and_future_evidence_never_promoted(self):
        item = evidence()
        item.lifecycle_state = "retracted"
        self.assertEqual(build_information_brief(item, NOW)["facts"], [])
        item.lifecycle_state = "active"
        item.published_at = "2026-10-01T00:00:00Z"
        self.assertEqual(build_information_brief(item, NOW)["facts"], [])

    def test_stale_analysis_is_not_displayed_as_current(self):
        brief = build_information_brief(evidence(aiAnalysis={"sourceTextHash": "outdated", "summary": {"oneLineKo": "old summary"}}), NOW)
        self.assertEqual(brief["summary"], "")
        self.assertEqual(len(brief["facts"]), 1)
        self.assertTrue(any("변경" in warning for warning in brief["warnings"]))

    def test_no_automatic_monitoring_is_invented(self):
        analysis = {"summary": {"briefKo": "Revenue increased in the latest quarter.", "whyItMatters": "Higher revenue may support earnings.", "watchPoints": ["Check next earnings release."]}}
        brief = build_information_brief(evidence(aiAnalysis=analysis), NOW)
        self.assertEqual(brief["followUps"][0]["status"], "not-registered")
        self.assertEqual(brief["followUps"][0]["text"], "Check next earnings release.")
        self.assertEqual(brief["interpretation"], analysis["summary"]["whyItMatters"])
        self.assertEqual(brief["summary"], analysis["summary"]["briefKo"])
        legacy = build_information_brief(evidence(aiAnalysis={"summary": {"checkPoints": ["Legacy follow-up."]}}), NOW)
        self.assertEqual(legacy["followUps"][0]["text"], "Legacy follow-up.")
        item = evidence(articleFacts={"eventTakeaway": "Unsupported bitcoin claim", "numbers": ["999"]})
        repository = MagicMock()
        repository.get.return_value = item
        enqueuer = NewsDigestEnqueuer(None, None, None, evidence_repository=repository)
        hydrated = enqueuer.hydrate_canonical_items([{"evidenceId": item.evidence_id}])
        event = DomainEvent("news-test", "EXAMPLE", occurred_at="2026-09-13T00:00:00Z")
        message = enqueuer.message_text(None, hydrated, event)
        self.assertIn("원문과 대조한 내용", message)
        self.assertIn("Example Company reported", message)
        self.assertNotIn("Unsupported bitcoin", message)
        self.assertNotIn("본문 수치: 999", message)
        self.assertNotIn("AI 해석 · 조건부", message)
        item.raw_payload["aiAnalysis"] = analysis
        hydrated = enqueuer.hydrate_canonical_items([{"evidenceId": item.evidence_id}])
        message = enqueuer.message_text(None, hydrated, event)
        self.assertIn("Check next earnings release.", message)
        self.assertIn("자동 관찰 미등록", message)

    def test_metadata_only_disclosure_does_not_claim_document_verified(self):
        item = evidence(disclosureAnalysis={"confirmedFacts": ["Receipt date 20260912"]})
        item.kind = "filing"
        brief = build_information_brief(item, NOW)
        self.assertEqual(brief["facts"], [])
        self.assertEqual(brief["summaryRole"], "metadata")
        self.assertEqual(brief["interpretation"], "")

    def test_official_document_sections_must_match_retained_text(self):
        item = evidence(officialDocumentText="<p>The board approved a new capital issuance on September 12.</p>", documentVerified=True,
            disclosureAnalysis={"sourceSections": [{"text": "The board approved a new capital issuance on September 12."}, {"text": "An unrelated merger was also approved."}]})
        item.kind = "filing"
        brief = build_information_brief(item, NOW)
        self.assertEqual(len(brief["facts"]), 1)
        self.assertEqual(brief["facts"][0]["label"], "공식 문서 기재")
        item.raw_payload["documentHash"] = "new-document"
        item.raw_payload["disclosureAnalysis"].update(sourceTextHash="old-document", summary="Old summary")
        self.assertEqual(build_information_brief(item, NOW)["summary"], "")

    def test_unsafe_urls_and_malformed_counts(self):
        for url in ["javascript:alert(1)", "file:///etc/passwd", "https://u:p@example.org", "https://example.org/\nx"]:
            self.assertEqual(source_url(url), "")
        self.assertEqual(build_information_brief(evidence(evidenceGovernance={"independentSourceCount": "unknown"}), NOW)["independentSourceCount"], 0)

    def test_market_feed_keeps_source_bound_brief(self):
        brief = build_information_brief(evidence(), NOW)
        result = ConsoleReadModelService().market_evidence({"items": [{"evidenceId": "e1", "symbol": "EXAMPLE", "kind": "news", "informationBrief": brief}]})
        self.assertEqual(result["items"][0]["informationBrief"], brief)


class OfficialReleaseTests(unittest.TestCase):
    def test_cpi_units_previous_and_actual_are_distinct(self):
        result = parse_official_release("cpi", CPI, RELEASE_URLS["cpi"], NOW)
        self.assertEqual(result["referencePeriod"], "2026-08")
        self.assertEqual(result["releasedAt"], "2026-09-11T12:30:00Z")
        self.assertEqual([metric["actual"] for metric in result["metrics"]], [0.4, 3.4])
        self.assertEqual(result["metrics"][0]["previous"], 0.1)
        self.assertIsNone(result["metrics"][0]["consensus"])
        self.assertNotIn("malicious", result["sourceText"])
        self.assertNotIn("unrelated", result["sourceText"])

    def test_zero_and_negative_are_preserved(self):
        zero = parse_official_release("cpi", CPI.replace("increased 0.4", "increased 0.0"), RELEASE_URLS["cpi"], NOW)
        negative = parse_official_release("cpi", CPI.replace("increased 0.4", "declined 0.4"), RELEASE_URLS["cpi"], NOW)
        self.assertEqual(zero["metrics"][0]["actual"], 0)
        self.assertEqual(negative["metrics"][0]["actual"], -0.4)

    def test_employment_counts_and_rate(self):
        result = parse_official_release("employment", JOBS, RELEASE_URLS["employment"], NOW)
        self.assertEqual([metric["actual"] for metric in result["metrics"]], [162000, 4.1])

    def test_fomc_fractional_target_range(self):
        result = parse_official_release("fomc", FED, "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm", NOW)
        self.assertEqual(result["metrics"][0]["actual"], [3.5, 3.75])
        self.assertEqual(result["releasedAt"], "2026-07-29T18:00:00Z")

    def test_changed_format_wrong_source_and_future_are_errors(self):
        for markup, url in [("<html>Access denied 403</html>", RELEASE_URLS["cpi"]), (CPI, "https://fake.example/cpi"), (CPI.replace("September 11", "September 30"), RELEASE_URLS["cpi"])]:
            with self.assertRaises(ValueError):
                parse_official_release("cpi", markup, url, NOW)

    def test_fomc_links_must_be_official_published_statements(self):
        markup = '<a href="/newsevents/pressreleases/monetary20260729a.htm">Statement</a><a href="https://evil.test/newsevents/pressreleases/monetary20260830a.htm">Bad</a><a href="/newsevents/pressreleases/monetary20260916a.htm">Future</a>'
        self.assertTrue(latest_fomc_statement_url(markup, NOW).endswith("20260729a.htm"))

    def test_release_updates_have_no_investment_authority(self):
        for dataset in ["official.bls-release", "official.fomc-release"]:
            transition = ExternalFactTransitionService().assess(dataset, {"sourceRevision": "1", "payload": {}}, {"officialRelease": {"x": 2}}, "2")
            self.assertTrue(transition.changed)
            self.assertFalse(transition.material)

    def test_adapters_have_independent_circuits_and_existing_worker_contract(self):
        bls, fed = OfficialReleaseAdapter("bls"), OfficialReleaseAdapter("fomc")
        self.assertNotEqual(bls.descriptor.provider_id, fed.descriptor.provider_id)
        self.assertEqual(len(bls.partitions([], {})), 2)
        self.assertFalse(bls.descriptor.enabled({"externalOfficialReleaseEnabled": "0"}))
        disabled = {"externalOfficialReleaseEnabled": "0"}
        registry = ExternalDatasetRegistry([bls, fed])
        self.assertEqual(registry.desired_partitions([], disabled), [])
        self.assertIn(bls.descriptor.dataset_id, registry.static_dataset_ids(disabled))
        partition = bls.partitions([], {})[0]
        job = CollectionJob(partition.dataset_id, partition.partition_key, bls.descriptor.provider_id, 45, partition.subject)
        store = MagicMock()
        result = ExternalDataCollectionService(disabled, registry, store)._process_job(job)
        self.assertEqual(result["status"], "disabled")
        store.reserve_provider_call.assert_not_called()
        store.defer_job.assert_called_once()

    def test_calendar_data_and_failures_do_not_enter_investment_snapshot(self):
        class Store:
            def list_current(self, _keys):
                return [cpi_fact()]

            def provider_statuses(self):
                return [{"datasetId": "official.bls-release", "state": "failed", "lastError": "403"}]

        snapshot = ExternalSignalsReadModelService(Store()).signals_for_subjects([])
        self.assertNotIn("officialRelease", snapshot)
        self.assertEqual(snapshot["statuses"], [])
        self.assertEqual(snapshot["fetchedAt"], "")


class CalendarReleaseProjectionTests(unittest.TestCase):
    def test_exact_date_join_and_no_mutation(self):
        event, fact = cpi_event(), cpi_fact()
        before = copy.deepcopy((event, fact))
        result = calendar_release_information(event, {"facts": [fact]}, NOW)
        self.assertEqual(result["status"], "released")
        self.assertFalse(result["decisionAuthority"])
        self.assertEqual((event, fact), before)
        self.assertNotIn("sourceText", result["release"])

    def test_old_result_never_attached_to_next_event(self):
        event = cpi_event()
        event["startsAt"] = "2026-10-14T12:30:00Z"
        result = calendar_release_information(event, {"facts": [cpi_fact()]}, NOW)
        self.assertEqual(result["status"], "scheduled")
        self.assertIsNone(result["release"])

    def test_wrong_country_unverified_schedule_and_tampered_source(self):
        for mutate in [lambda event, fact: event["payload"].update(country="KR"), lambda event, fact: event["payload"].update(officialSource=False), lambda event, fact: fact["payload"]["officialRelease"].update(sourceText="tampered")]:
            event, fact = cpi_event(), cpi_fact()
            mutate(event, fact)
            self.assertIsNone(calendar_release_information(event, {"facts": [fact]}, NOW)["release"])

    def test_source_error_does_not_erase_a_verified_historical_result(self):
        result = calendar_release_information(cpi_event(), {"facts": [cpi_fact()], "collection": {"cpi": {"active": True, "error": "HTTP 403"}}}, NOW)
        self.assertEqual(result["status"], "released")
        self.assertEqual(result["collection"]["state"], "error")

    def test_revision_history_and_first_known_time_are_preserved(self):
        old = cpi_fact()
        new = copy.deepcopy(old)
        new["payload"]["officialRelease"] = parse_official_release("cpi", CPI.replace("increased 0.4", "increased 0.5"), RELEASE_URLS["cpi"], NOW)
        new["fetchedAt"] = "2026-09-12T02:00:00Z"
        result = calendar_release_information(cpi_event(), {"facts": [old, new, old]}, NOW)["release"]
        self.assertEqual(result["revisionCount"], 2)
        self.assertEqual(result["firstCollectedAt"], old["fetchedAt"])
        self.assertEqual(result["metrics"][0]["actual"], 0.5)

    def test_operational_time_is_not_official_time(self):
        event = cpi_event()
        event["payload"]["timeState"] = "operationalDefault"
        self.assertEqual(calendar_release_information(event, {}, NOW)["timeRole"], "reminder-default")


if __name__ == "__main__":
    unittest.main()
