"""Real MySQL boundaries: one request, lease fencing and atomic stage handoff."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
import unittest
from unittest.mock import patch

from test_ai_portfolio_analysis import source, response
from digital_twin.modules.ai_orchestration.application.portfolio_analysis import PortfolioAnalysisWorker
from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
from digital_twin.modules.ai_orchestration.infrastructure.mysql_portfolio_analysis import MySQLPortfolioAnalysisStore
from digital_twin.modules.ai_orchestration.domain.portfolio_analysis import task


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class PortfolioAnalysisStorageTests(unittest.TestCase):
    def setUp(self):
        from mysql_fixtures import mysql_test_settings
        self.settings = {**mysql_test_settings(), "aiControlBudgetEnabled": "false"}
        self.control = MySQLAIControlStore(self.settings)
        self.runs = MySQLPortfolioAnalysisStore(self.settings, initialize=True)
        self.control.portfolio_report_writer = self.runs.complete
        self.control.portfolio_report_failure = self.runs.failed
        self.clean()

    def clean(self):
        with self.control.transaction() as db:
            for table in ("ai_control_input_calls", "ai_control_inputs", "ai_control_tasks", "ai_control_budget", "ai_control_execution_state"):
                db.execute("DELETE FROM " + table)
            db.execute("DELETE FROM ai_portfolio_analyses WHERE account_id='analysis-test'")
            db.execute("DELETE FROM ai_portfolio_reports WHERE account_id='analysis-test'")

    def tearDown(self): self.clean()

    def create(self):
        return self.runs.create("analysis-test", "analysis-storage-001", source(), False)

    def worker(self, model):
        return PortfolioAnalysisWorker(self.runs, self.control, lambda scope: None,
            lambda: [{"accountId": "analysis-test", **s} for s in source()["subjects"]], model,
            lambda report: None, lambda: [{"accountId": "analysis-test"}])

    def test_timeout_retry_gets_attempt_owned_input_without_erasing_failure(self):
        self.create(); job = self.control.claim(); seen = []
        def model(env, input_id):
            call = self.control.begin_call("independent-observation", env["promptHash"], task_id=job["taskId"], input_id=input_id)
            seen.append((input_id, call))
            self.control.finish_call(call, "ai-execution:timeout" if len(seen) == 1 else "")
            if len(seen) == 1: raise TimeoutError("injected timeout")
            return response(env, input_id)
        worker = self.worker(model)
        with self.assertRaises(TimeoutError): worker(job)
        self.control.fail(job, "ai-execution:timeout")
        with self.control.transaction() as db:
            db.execute("UPDATE ai_control_tasks SET available_at='2000-01-01T00:00:00Z' WHERE task_id=%s", (job["taskId"],))
        job = self.control.claim(); self.assertEqual(2, job["attempts"])
        result, children = worker(job)
        self.assertNotEqual(seen[0][0], seen[1][0])
        self.assertTrue(self.control.complete(job, result, children))
        with self.control.connect() as db:
            rows = db.execute("SELECT status FROM ai_control_calls WHERE call_id IN (%s,%s) ORDER BY status", (seen[0][1], seen[1][1])).fetchall()
        self.assertEqual(["completed", "failed"], [r["status"] for r in rows])

    def test_explicit_resume_preserves_progress_and_failure_without_duplicate_work(self):
        run = self.create(); worker = self.worker(response)
        job = self.control.claim(); result, children = worker(job)
        self.control.complete(job, result, children)
        job = self.control.claim(); self.control.fail(job, "injected", terminal=True)
        failed = self.runs.get(run["accountId"], run["analysisId"])
        with self.assertRaises(LookupError): self.runs.resume("other", run["analysisId"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            resumed = list(pool.map(lambda _: self.runs.resume(run["accountId"], run["analysisId"]), range(2)))
        self.assertEqual(resumed[0]["taskId"], resumed[1]["taskId"])
        self.assertNotEqual(job["taskId"], resumed[0]["taskId"])
        self.assertEqual(failed["execution"], resumed[0]["execution"])
        self.assertEqual(failed["step"], resumed[0]["step"])
        with self.control.connect() as db:
            self.assertEqual("failed", db.execute("SELECT status FROM ai_control_tasks WHERE task_id=%s", (job["taskId"],)).fetchone()["status"])
        next_job = self.control.claim(); self.assertEqual(resumed[0]["taskId"], next_job["taskId"])
        result, children = worker(next_job); self.assertTrue(self.control.complete(next_job, result, children))

    def test_concurrent_requests_share_one_active_run_and_get_is_scoped(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda i: self.runs.create("analysis-test", "analysis-storage-00" + str(i), source(), False), range(2)))
        self.assertEqual(runs[0]["analysisId"], runs[1]["analysisId"])
        self.assertIsNone(self.runs.get("other", runs[0]["analysisId"]))
        with self.control.connect() as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) n FROM ai_control_tasks WHERE capability='portfolio-report'").fetchone()["n"])

    def test_expired_worker_cannot_checkpoint_and_terminal_failure_releases_request(self):
        run = self.create(); job = self.control.claim()
        with self.control.transaction() as db:
            db.execute("UPDATE ai_control_tasks SET lease_token='replacement' WHERE task_id=%s", (job["taskId"],))
        with self.assertRaisesRegex(RuntimeError, "lease-lost"):
            self.runs.checkpoint(job, run)
        self.assertFalse(self.control.complete(job, {}, []))
        job["leaseToken"] = "replacement"
        self.control.fail(job, "injected-terminal-failure", terminal=True)
        self.assertEqual("failed", self.runs.get(run["accountId"], run["analysisId"])["status"])
        self.assertIsNone(self.runs.existing(run["accountId"]))

    def test_progress_children_final_report_are_atomic_and_resume_uses_same_response(self):
        run = self.create()
        calls = []
        def model(env, input_id):
            calls.append(input_id); return response(env, input_id)
        worker = PortfolioAnalysisWorker(self.runs, self.control, lambda scope: None,
            lambda: [{"accountId": "analysis-test", **s} for s in source()["subjects"]], model, lambda report: None,
            lambda: [{"accountId": "analysis-test"}])
        first = True
        while True:
            job = self.control.claim()
            self.assertIsNotNone(job)
            result, children = worker(job)
            if first:
                call_count = len(calls)
                original = self.control.insert
                with patch.object(self.control, "insert", side_effect=RuntimeError("injected enqueue failure")):
                    with self.assertRaises(RuntimeError): self.control.complete(job, result, children)
                result, children = worker(job)
                self.assertEqual(call_count, len(calls))
                first = False
            self.assertTrue(self.control.complete(job, result, children))
            if not children: break
        saved = self.runs.get("analysis-test", run["analysisId"])
        report = self.runs.reports.get("analysis-test", saved["reportId"])
        self.assertEqual(4, report["coverage"]["subjects"])
        self.assertEqual(len(calls), len(report["execution"]))
        self.assertIsNone(self.runs.reports.get("other", saved["reportId"]))
        self.assertIsNone(self.runs.existing("analysis-test"))
