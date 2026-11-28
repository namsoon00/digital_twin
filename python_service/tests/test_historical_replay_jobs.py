import unittest
from unittest.mock import patch

from digital_twin.modules.outcomes.application.historical_replay_job_service import HistoricalReplayJobService
from digital_twin.modules.outcomes.application.price_recovery_paper_service import PriceRecoveryPaperService
from digital_twin.modules.outcomes.domain.historical_replay import HistoricalReplayJob
from digital_twin.modules.outcomes.domain.price_recovery_paper import KIND
from stabilization_database import StabilizationDatabaseCase
from test_strategy_scenarios import contract, observation, scenario_rules


class FakeReplayStore:
    def __init__(self):
        self.jobs = {}

    def enqueue(self, job):
        self.jobs[job.job_id] = job
        return True

    def get(self, job_id):
        return self.jobs.get(job_id)

    def list(self, replay_kind="", limit=20):
        rows = list(self.jobs.values())
        if replay_kind:
            rows = [row for row in rows if row.replay_kind == replay_kind]
        return rows[:limit]

    def summary(self):
        summary = {}
        for job in self.jobs.values():
            summary[job.status] = summary.get(job.status, 0) + 1
        return summary

    def claim_pending(self, limit=1):
        selected = [job for job in self.jobs.values() if job.status == "pending"][:limit]
        for job in selected:
            job.status = "processing"
            job.attempts += 1
        return selected

    def mark_completed(self, job, result):
        job.status = "completed"
        job.result = dict(result)

    def mark_failed(self, job, error):
        job.status = "failed"
        job.last_error = str(error)


class ReplayService:
    def __init__(self, result=None):
        self.result = result or {"status": "completed"}
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


class HistoricalReplayJobServiceTests(unittest.TestCase):
    def test_enqueue_returns_durable_pending_contract(self):
        store = FakeReplayStore()
        service = HistoricalReplayJobService(store, ReplayService(), ReplayService())

        payload = service.enqueue("decision", {"symbol": "NVDA", "limit": 500})

        self.assertEqual("pending", payload["status"])
        self.assertEqual("decision", payload["replayKind"])
        self.assertEqual("NVDA", store.get(payload["jobId"]).request["symbol"])

    def test_worker_executes_decision_replay_without_delivery_or_abox_writes(self):
        store = FakeReplayStore()
        decision = ReplayService({"status": "completed", "episodeCount": 12})
        service = HistoricalReplayJobService(store, decision, ReplayService())
        job = HistoricalReplayJob.create("decision", {
            "accountId": "account-1",
            "symbol": "TSLA",
            "limit": 20,
            "includeCases": True,
        })
        store.enqueue(job)

        processed = service.run_once()

        self.assertEqual(1, processed)
        self.assertEqual("completed", job.status)
        self.assertEqual(12, job.result["episodeCount"])
        self.assertTrue(job.result["executionIsolation"]["readOnly"])
        self.assertFalse(job.result["executionIsolation"]["notificationDeliveryEnabled"])
        self.assertFalse(job.result["executionIsolation"]["operationalAboxWriteEnabled"])
        self.assertEqual("TSLA", decision.calls[0]["symbol"])

    def test_worker_records_failure_without_raising_to_scheduler(self):
        class BrokenReplay:
            def run(self, **_kwargs):
                raise RuntimeError("broken archive")

        store = FakeReplayStore()
        service = HistoricalReplayJobService(store, BrokenReplay(), ReplayService())
        job = HistoricalReplayJob.create("decision", {})
        store.enqueue(job)

        self.assertEqual(1, service.run_once())
        self.assertEqual("failed", job.status)
        self.assertIn("broken archive", job.last_error)


class PriceRecoveryPaperQueueTests(StabilizationDatabaseCase):
    def setUp(self):
        super().setUp()
        from digital_twin.modules.decisions.infrastructure.mysql_strategy_scenarios import MySQLStrategyScenarioStore
        from digital_twin.modules.market_data.infrastructure.mysql_market_time_series import MySQLMarketTimeSeriesStore
        from digital_twin.modules.outcomes.infrastructure.mysql_historical_replay_jobs import MySQLHistoricalReplayJobStore

        self.scenarios = MySQLStrategyScenarioStore(self.settings)
        self.quotes = MySQLMarketTimeSeriesStore(self.settings)
        self.jobs = MySQLHistoricalReplayJobStore(self.settings)
        self.registry.save_release_artifact(self.deployment, {
            "tboxFingerprint": "fixture-tbox",
            "ruleboxFingerprint": "fixture-rulebox",
            "releaseBundle": self.registry.get(self.deployment)["releaseBundle"],
            "rules": [rule.to_dict() for rule in scenario_rules()],
        })
        self.now = "2026-10-24T01:00:00Z"
        self.service = PriceRecoveryPaperService(self.registry, self.scenarios, self.quotes, now=lambda: self.now)
        self.replays = HistoricalReplayJobService(self.jobs, None, None, self.service)
        self.request = {"accountId": "paper-" + self.deployment, "symbol": "TEST", "market": "KR", "worldId": "world:test",
                        "startDate": "2026-10-23", "endDate": "2026-10-23", "costBpsPerSide": 25}

    def signal(self, phase, time, recorded=None, **scope):
        stamp = "2026-10-23T" + time + "Z"
        decision = {"accountId": self.request["accountId"], "deploymentId": self.deployment, **scope}
        with patch("digital_twin.modules.decisions.infrastructure.mysql_strategy_scenarios.utc_now_iso",
                   return_value="2026-10-23T" + (recorded or time) + "Z"):
            return self.scenarios.record(contract(), observation([phase], stamp, **decision))

    def quote(self, time, price, source=None, account=None, session="regular"):
        from digital_twin.modules.portfolio.domain.portfolio import Position
        stamp = "2026-10-23T" + time + "Z"
        position = Position("TEST", "Test", market="KR", currency="KRW", current_price=price,
                            source_as_of="2026-10-23T" + (source or time) + "Z", market_session=session)
        self.quotes.record_positions(account or self.request["accountId"], [position], stamp,
                                     provider="test-source", replace=False)

    def test_paper_worker_persists_paired_cost_and_loss_with_exact_signal_and_quote_scope(self):
        setup = self.signal("setup", "01:00:00", recorded="01:01:00")
        entry = self.signal("entry", "01:08:00", recorded="01:09:00")
        ending = self.signal("exit", "01:21:00")
        self.signal("exit", "01:06:00", worldId="wrong-world")
        self.quote("01:00:45", 1)  # This price was known before the setup was recorded.
        self.quote("01:02:00", 1, account="other-account")
        self.quote("01:03:10", 100)
        self.quote("01:06:10", 97)
        self.quote("01:09:10", 104, source="01:08:55")  # Source is before confirmation.
        self.quote("01:12:10", 102)
        self.quote("01:18:10", 108)
        self.quote("01:22:10", 106)
        job = self.replays.enqueue(KIND, self.request)
        self.assertEqual(job, self.replays.enqueue(KIND, self.request))
        self.assertEqual(1, self.replays.run_once())
        result = self.jobs.get(job["jobId"]).result
        self.assertFalse(result["orderAuthorized"])
        self.assertFalse(result["tradeTimingQualified"])
        self.assertEqual(1, result["summary"]["pairedCount"])
        trial = result["ledger"][0]
        self.assertEqual("exit", trial["exitReason"])
        self.assertEqual([setup["observationId"], entry["observationId"], ending["observationId"]],
                         [trial[k]["observationId"] for k in ("setup", "confirmation", "ending")])
        self.assertEqual("fixture-rulebox", result["study"]["ruleboxFingerprint"])
        first, wait = (trial["arms"][name] for name in ("first-setup", "wait-for-entry"))
        self.assertEqual((100, 102, 106), (first["entry"]["price"], wait["entry"]["price"], wait["exit"]["price"]))
        self.assertAlmostEqual((106 * 0.9975 / (100 * 1.0025) - 1) * 100, first["netReturnPct"], places=5)
        self.assertAlmostEqual((97 * 0.9975 / (100 * 1.0025) - 1) * 100, first["worstObservedReturnPct"], places=5)
        self.assertFalse(result["executionIsolation"]["notificationDeliveryEnabled"])
        self.assertFalse(result["executionIsolation"]["operationalAboxWriteEnabled"])

    def test_paper_worker_keeps_missing_quotes_out_of_returns(self):
        self.signal("setup", "01:00:00")
        self.signal("entry", "01:10:00")
        self.quote("01:03:00", 100)
        self.quote("01:31:00", 110, session="after")
        job = self.replays.enqueue(KIND, self.request)
        self.replays.run_once()
        result = self.jobs.get(job["jobId"]).result
        self.assertEqual("no-comparable-results", result["status"])
        self.assertEqual(0, result["summary"]["pairedCount"])
        arms = result["ledger"][0]["arms"]
        self.assertEqual("unmeasurable", arms["first-setup"]["state"])
        self.assertEqual("unfilled", arms["wait-for-entry"]["state"])
        self.assertEqual("cash", arms["cash"]["state"])
        self.assertIsNone(result["summary"]["waitMinusFirstPairedMeanPctPoint"])

    def test_forward_paper_registration_waits_for_cutoff_and_fences_interrupted_claim(self):
        self.now = "2026-10-22T04:00:00Z"
        forward = {**self.request, "mode": "forward"}
        study = self.service.prepare(forward)
        job = HistoricalReplayJob("forward-paper-" + self.deployment, KIND, study, created_at=self.now)
        self.jobs.enqueue_paper_study(job)
        clock_path = "digital_twin.modules.outcomes.infrastructure.mysql_historical_replay_jobs.utc_now"
        with patch(clock_path, return_value="2026-10-23T14:59:59Z"):
            self.assertEqual([], self.jobs.claim_pending())
        self.now = "2026-10-23T15:00:01Z"
        with patch(clock_path, return_value=self.now):
            interrupted = self.jobs.claim_pending()[0]
        with patch(clock_path, return_value="2026-10-23T15:16:00Z"):
            self.assertEqual(1, self.replays.run_once())
        result = self.jobs.get(job.job_id)
        self.assertEqual(2, result.attempts)
        self.assertEqual("completed", result.status)
        self.assertEqual("forward", result.result["study"]["mode"])
        self.jobs.mark_completed(interrupted, {"result": "late-attempt"})
        self.assertEqual(result.to_dict(), self.jobs.get(job.job_id).to_dict())
        with self.assertRaisesRegex(ValueError, "registered before"):
            self.service.prepare(forward)


if __name__ == "__main__":
    unittest.main()
