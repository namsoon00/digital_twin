import unittest

from digital_twin.application.ontology_portfolio_rebuild_service import OntologyPortfolioRebuildRunner
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position


class SnapshotStore:
    def __init__(self, states):
        self.states = states

    def load_previous(self):
        return dict(self.states)


class ProjectionRecorder:
    def __init__(self, status="ok"):
        self.status = status
        self.calls = []

    def record_snapshot(self, snapshot, reasoning_context=None, progress_callback=None):
        self.calls.append((snapshot, dict(reasoning_context or {})))
        if progress_callback:
            progress_callback("ontology_projection.start", {"elapsedMs": 0})
        return {
            "status": self.status,
            "saved": self.status == "ok",
            "reason": "candidate projection failed" if self.status != "ok" else "",
            "runtimeStages": {"graphBuildMs": 12, "aboxPersistenceMs": 34},
            "timing": {"stage": "candidate-staged" if self.status == "ok" else "changed-scope-write"},
            "activeAboxSnapshotId": "abox:" + snapshot.account_id,
            "ontologyWorld": {"worldId": "portfolio:local:" + snapshot.account_id},
            "inferenceBox": {"inferenceGenerationId": "inference:" + snapshot.account_id},
        }


def monitor_state(account_id="main", mode="live", status="ok"):
    snapshot = AccountSnapshot(
        account_id=account_id,
        account_label="Main",
        provider="toss",
        mode=mode,
        status=status,
        generated_at="2026-08-12T01:00:00Z",
        portfolio=PortfolioSummary(100, 100, 0, [], [], 1),
        positions=[Position("005930", "삼성전자", market="KR", currency="KRW", quantity=1, current_price=100)],
    )
    return snapshot.to_monitor_state()


class OntologyPortfolioRebuildRunnerTests(unittest.TestCase):
    def test_rebuilds_latest_live_snapshot_without_touching_live_queue(self):
        recorder = ProjectionRecorder()
        runner = OntologyPortfolioRebuildRunner(
            SnapshotStore({"main": monitor_state()}),
            recorder,
        )

        result = runner.run()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["projectedPortfolioWorldCount"])
        self.assertTrue(result["readOnlySource"])
        self.assertFalse(result["mutatedLiveQueue"])
        self.assertEqual("typedb-blue-green-candidate-rebuild", recorder.calls[0][1]["triggerTypes"][0])
        self.assertTrue(recorder.calls[0][1]["candidateRebuild"])

    def test_skips_non_live_snapshot_and_fails_closed_on_projection_error(self):
        skipped_recorder = ProjectionRecorder()
        skipped = OntologyPortfolioRebuildRunner(
            SnapshotStore({"demo": monitor_state("demo", mode="demo")}),
            skipped_recorder,
        ).run()
        self.assertEqual("empty", skipped["status"])
        self.assertEqual([], skipped_recorder.calls)

        failed = OntologyPortfolioRebuildRunner(
            SnapshotStore({"main": monitor_state()}),
            ProjectionRecorder(status="activation-failed"),
        ).run()
        self.assertEqual("error", failed["status"])
        self.assertEqual(1, failed["failedPortfolioWorldCount"])
        self.assertEqual("candidate projection failed", failed["rows"][0]["reason"])
        self.assertEqual("changed-scope-write", failed["rows"][0]["aboxPersistence"]["stage"])
        self.assertEqual(12, failed["rows"][0]["runtimeStages"]["graphBuildMs"])
        self.assertEqual("ontology_projection.start", failed["rows"][0]["progressTrace"][0]["stage"])

    def test_fails_closed_when_live_worlds_exceed_rebuild_limit(self):
        recorder = ProjectionRecorder()
        result = OntologyPortfolioRebuildRunner(
            SnapshotStore({
                "first": monitor_state("first"),
                "second": monitor_state("second"),
            }),
            recorder,
        ).run(limit=1)

        self.assertEqual("error", result["status"])
        self.assertEqual(1, result["projectedPortfolioWorldCount"])
        self.assertEqual(1, result["failedPortfolioWorldCount"])
        self.assertEqual(1, result["rebuildLimit"])
        self.assertEqual(1, len(recorder.calls))
        self.assertIn(
            "error-rebuild-limit-exceeded",
            {item["status"] for item in result["rows"]},
        )

    def test_fails_closed_when_a_live_monitor_state_cannot_be_rehydrated(self):
        result = OntologyPortfolioRebuildRunner(
            SnapshotStore({"broken": {"mode": "live", "status": "ok"}}),
            ProjectionRecorder(),
        ).run()

        self.assertEqual("error", result["status"])
        self.assertEqual("error-invalid-live-snapshot", result["rows"][0]["status"])


if __name__ == "__main__":
    unittest.main()
