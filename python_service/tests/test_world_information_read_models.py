import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from digital_twin.modules.news_intelligence.domain.investment_research import ResearchEvidence
from digital_twin.modules.news_intelligence.public import build_event_history
from digital_twin.modules.news_intelligence.infrastructure.mysql_research_evidence import MySQLResearchEvidenceStore
from digital_twin.modules.read_models.domain.world_diagnostics import diagnostics_observation, world_inventory
from digital_twin.modules.read_models.application.ontology_diagnostics_service import OntologyDiagnosticsService
from digital_twin.infrastructure.web.adapters import ontology_diagnostics as api


NOW = datetime(2026, 10, 24, 8, tzinfo=timezone.utc)


def evidence(eid="old", **changes):
    values = dict(evidence_id=eid, symbol="EXAMPLE", kind="news", source="Source", title="Event",
                  published_at="2026-10-23T00:00:00Z", observed_at="2026-10-23T01:00:00Z",
                  raw_payload={"eventEpisodeId": "episode:2026-Q3", "storyClusterId": "story", "eventFingerprint": {"reportingPeriod": "2026-Q3"}})
    values.update(changes)
    return ResearchEvidence(**values)


class EventHistoryTests(unittest.TestCase):
    def test_episode_crosses_story_clusters_but_never_subject_or_period(self):
        item = evidence()
        peer = evidence("new", raw_payload={"eventEpisodeId": "episode:2026-Q3", "storyClusterId": "other", "eventFingerprint": {"reportingPeriod": "2026-Q3"}})
        excluded = [evidence("wrong-symbol", symbol="OTHER"), evidence("wrong-kind", kind="filing"),
                    evidence("wrong-period", raw_payload={"eventEpisodeId": "episode:2026-Q4", "storyClusterId": "story"})]
        result = build_event_history(item, [peer, *excluded])
        self.assertEqual({e["evidenceId"] for e in result["entries"]}, {"old", "new"})
        self.assertFalse(result["historyComplete"])
        self.assertFalse(result["decisionAuthority"])

    def test_no_identity_never_merges_similar_titles(self):
        item = evidence(raw_payload={})
        result = build_event_history(item, [evidence("other", raw_payload={})])
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["identityKind"], "evidenceId")
        incomplete = {"eventEpisodeId": "quarter-only", "storyClusterId": "quarter-only", "eventFingerprint": {"reportingPeriod": "Q3"}}
        bounded = build_event_history(evidence(raw_payload=incomplete), [evidence("other-year", raw_payload=incomplete)])
        self.assertEqual(len(bounded["entries"]), 1)

    def test_clocks_and_recorded_corrections_are_preserved_without_mutation(self):
        item = evidence(lifecycle_state="superseded")
        item.raw_payload.update(evidenceGovernance={"claimState": "superseded", "supersededByEvidenceId": "new", "conflictingEvidenceIds": ["counter"]},
                                companyEventContract={"effectiveAt": "2026-11-01", "announcedAt": "2026-10-22", "correctsSourceDocumentId": "filing-1"})
        before = copy.deepcopy(item.raw_payload)
        row = build_event_history(item)["entries"][0]
        self.assertEqual(row["publishedAt"], item.published_at)
        self.assertEqual(row["observedAt"], item.observed_at)
        self.assertEqual(row["effectiveAt"], "2026-11-01")
        self.assertEqual(row["supersededByEvidenceId"], "new")
        self.assertEqual(row["conflictingEvidenceIds"], ["counter"])
        self.assertEqual(item.raw_payload, before)

    def test_deterministic_order_unknown_clock_and_selected_source_survive_limit(self):
        item = evidence()
        latest = evidence("latest", published_at="2026-10-24T00:00:00Z")
        unknown = evidence("unknown", published_at="", url="javascript:alert(1)")
        result = build_event_history(item, [unknown, latest, item], limit=3)
        self.assertEqual([x["evidenceId"] for x in result["entries"]], ["old", "latest", "unknown"])
        self.assertEqual(result["entries"][-1]["url"], "")
        self.assertEqual(result["entries"][-1]["publishedAt"], "")
        bounded = build_event_history(item, [unknown, latest], limit=1)
        self.assertEqual(bounded["entries"][0]["evidenceId"], "old")
        self.assertEqual(bounded["omittedFromSample"], 2)

    def test_repository_reads_explicit_episode_with_bounded_same_subject_query(self):
        store = MySQLResearchEvidenceStore.__new__(MySQLResearchEvidenceStore)
        store.connect = MagicMock()
        connection = store.connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchall.return_value = []
        store.story_history(evidence(), 100)
        sql, values = connection.execute.call_args.args
        self.assertIn("$.eventEpisodeId", sql)
        self.assertIn("symbol = %s AND kind = %s", sql)
        self.assertEqual(values, ("EXAMPLE", "news", "episode:2026-Q3", 20))


class WorldObservationTests(unittest.TestCase):
    def test_recent_cache_cannot_make_old_diagnostics_current(self):
        result = diagnostics_observation({"generatedAt": "2026-09-02T00:00:00Z"}, {"stale": False}, now=NOW)
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["snapshotRecent"])

    def test_unknown_future_and_recent_clocks_do_not_claim_coverage(self):
        for stamp in ("", "invalid", "2026-10-24T08:00:01Z", "2026-10-24T08:00:00"):
            self.assertEqual(diagnostics_observation({"generatedAt": stamp}, {}, now=NOW)["status"], "unknown")
        recent = diagnostics_observation({"generatedAt": NOW.isoformat()}, {}, now=NOW)
        self.assertEqual(recent["status"], "recent")
        self.assertFalse(recent["coverageVerified"])

    def test_cache_staleness_and_failure_are_not_hidden(self):
        result = diagnostics_observation({"generatedAt": NOW.isoformat()}, {"stale": True, "lastError": "failed", "refreshing": True}, now=NOW)
        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["refreshFailed"])

    def test_missing_world_metadata_is_unknown_not_zero_or_healthy(self):
        result = world_inventory([{"worldId": "market:shared:global", "worldType": "market"}], {}, incremental=True)
        row = result["worlds"][0]
        self.assertEqual(row["status"], "unknown")
        self.assertIsNone(row["activeScopeCount"])
        self.assertEqual(result["sharedPremiseRole"], "offline-reconciliation")
        self.assertEqual(world_inventory([], {}, incremental=True)["status"], "unknown")
        repository = MagicMock()
        repository.active_abox_metadata.side_effect = TypeError("unexpected keyword world_id")
        observed = OntologyDiagnosticsService(repository).world_inventory_boundary([{"worldId": "portfolio:local:example"}])
        self.assertEqual(observed["worlds"][0]["status"], "unknown")
        repository.active_abox_metadata.assert_called_once_with(world_id="portfolio:local:example")

    def test_cache_key_changes_with_deployment_and_database(self):
        before = api.ontology_diagnostics_cache_key([], 5, "", {"deploymentId": "old", "selectedGraphStoreBinding": "one"})
        after = api.ontology_diagnostics_cache_key([], 5, "", {"deploymentId": "new", "selectedGraphStoreBinding": "two"})
        self.assertNotEqual(before, after)

    @patch.object(api, "ONTOLOGY_DIAGNOSTICS_READ_MODEL")
    @patch.object(api, "runtime_settings", side_effect=RuntimeError("offline"))
    def test_missing_binding_never_serves_old_cache(self, settings, cache):
        result = api.ontology_diagnostics_payload({})
        self.assertEqual(result["status"], "unavailable")
        cache.snapshot.assert_not_called()

    @patch.object(api, "MySQLOperationalConnection")
    @patch.object(api, "runtime_settings", return_value={})
    @patch.object(api, "active_observation_binding")
    @patch.object(api, "ONTOLOGY_DIAGNOSTICS_READ_MODEL")
    def test_shared_world_uses_shared_binding_and_marks_old_payload(self, cache, binding, settings, connection):
        binding.return_value = {"deploymentId": "active", "graphStoreBinding": "account-db", "sharedGraphStoreBinding": "shared-db"}
        cache.snapshot.return_value = {"hasData": True, "payload": {"generatedAt": "2020-01-01T00:00:00Z"}, "stale": True}
        result = api.ontology_diagnostics_payload({"worldId": ["market:shared:global"]})
        self.assertEqual(result["observation"]["status"], "stale")
        self.assertIn('"selectedGraphStoreBinding":"shared-db"', cache.snapshot.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
