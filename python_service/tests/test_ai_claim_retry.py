"""Production claim retry/rollback boundaries, without a database or model call."""

import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.infrastructure.mysql_operational_connection import MySQLDeadlockRetryExhausted
from digital_twin.infrastructure.schedulers import AIInferenceQueueScheduler
from digital_twin.infrastructure.transactions.ai_publication import MySQLAIInferenceQueueStore
from digital_twin.modules.decisions.application.ai_inference_queue_service import AIInferenceQueueRunner


class ClaimConnection:
    def __init__(self, rows, fault):
        self.rows = deepcopy(rows)
        self.fault = fault
        self.updates = 0

    def fail_at(self, stage):
        if self.fault and self.fault[0] == stage:
            raise RuntimeError(self.fault[1], "injected database fault")

    def execute(self, sql, params):
        statement = " ".join(sql.split())
        if "last_error LIKE" in statement:
            self.fail_at("cleanup")
            return SimpleNamespace(rowcount=0)
        if statement.startswith("SELECT request_id, subject_key"):
            return SimpleNamespace(fetchall=lambda: [])
        if statement.startswith("SELECT request.*, notification.payload_json"):
            self.fail_at("select")
            rows = [row for row in self.rows if row["status"] in ("pending", "retry")]
            return SimpleNamespace(fetchall=lambda: deepcopy(rows[:params[3]]))
        if "attempts = attempts + 1" in statement:
            row = next(row for row in self.rows if row["request_id"] == params[6])
            row.update(status=params[0], attempts=row["attempts"] + 1,
                       lease_owner=params[1], lease_expires_at=params[2], heartbeat_at=params[3])
            self.updates += 1
            self.fail_at("update-" + str(self.updates))
            return SimpleNamespace(rowcount=1)
        raise AssertionError("Unexpected claim SQL")


class ClaimTransactions:
    def __init__(self, count=1, faults=()):
        self.rows = [dict(request_id="request-" + str(i), subject_key="subject-" + str(i),
                          status="pending", attempts=0, started_at="") for i in range(count)]
        self.faults = list(faults)
        self.connections = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        index = len(self.connections)
        fault = self.faults[index] if index < len(self.faults) else None
        connection = ClaimConnection(self.rows, fault)
        self.connections.append(connection)
        try:
            yield connection
            connection.fail_at("commit")
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.rows = connection.rows
            self.commits += 1

    def store(self, retries=3):
        store = object.__new__(MySQLAIInferenceQueueStore)
        store.runtime_settings = {"mysqlDeadlockRetryCount": str(retries)}
        store.transaction = self.transaction
        store.request_from_row = lambda row: SimpleNamespace(**row)
        store.last_transaction_retry = {}
        return store


class AIClaimRetryTests(unittest.TestCase):
    def setUp(self):
        self.sleep = self.enter_patch("digital_twin.infrastructure.mysql_operational_connection.time.sleep")
        self.enter_patch("digital_twin.infrastructure.mysql_operational_connection.random.random", return_value=0.5)

    def enter_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def runner(self, store):
        return SimpleNamespace(queue=store, worker_id="worker", lease_seconds=60,
                               process_request=Mock(return_value="processed"), recover_request=Mock())

    def test_deadlock_before_selection_retries_in_a_fresh_transaction(self):
        transactions = ClaimTransactions(faults=[("select", 1213)])
        store = transactions.store()
        result = store.claim("worker", 1, 60)
        self.assertEqual(["request-0"], [row.request_id for row in result])
        self.assertEqual((2, 1, 1), (len(transactions.connections), transactions.commits, transactions.rollbacks))
        self.assertEqual(1, transactions.rows[0]["attempts"])
        self.assertEqual({"operation": "ai-inference-claim", "attempts": 2, "retryCount": 1,
                          "recovered": True, "delaysMs": [30]}, store.last_transaction_retry)
        self.sleep.assert_called_once_with(0.03)

    def test_partial_batch_discards_rolled_back_requests_and_refreshes_lease_clock(self):
        transactions = ClaimTransactions(count=2, faults=[("update-2", 1213)])
        store = transactions.store()
        with patch("digital_twin.infrastructure.transactions.ai_publication.utc_now", side_effect=["before", "after"]), \
             patch("digital_twin.infrastructure.transactions.ai_publication._timestamp_after", side_effect=["old-lease", "new-lease"]):
            result = store.claim("worker", 2, 60)
        self.assertEqual(["request-0", "request-1"], [row.request_id for row in result])
        self.assertEqual([1, 1], [row.attempts for row in result])
        self.assertEqual([1, 1], [row["attempts"] for row in transactions.rows])
        self.assertTrue(all(row.heartbeat_at == "after" and row.lease_expires_at == "new-lease" for row in result))
        self.assertEqual((1, 1), (transactions.commits, transactions.rollbacks))

    def test_commit_deadlock_does_not_return_uncommitted_claims(self):
        transactions = ClaimTransactions(faults=[("commit", 1213)])
        store = transactions.store()
        result = store.claim("worker")
        self.assertEqual(1, len(result))
        self.assertEqual(1, result[0].attempts)
        self.assertEqual((1, 1), (transactions.commits, transactions.rollbacks))

    def test_retry_exhaustion_records_budget_without_starting_inference(self):
        transactions = ClaimTransactions(faults=[("update-1", 1213)] * 3)
        store = transactions.store(retries=2)
        runner = self.runner(store)
        with self.assertRaises(MySQLDeadlockRetryExhausted) as raised:
            AIInferenceQueueRunner.run_once(runner)
        self.assertEqual(3, raised.exception.receipt.attempts)
        self.assertEqual((0, 3), (transactions.commits, transactions.rollbacks))
        self.assertEqual("pending", transactions.rows[0]["status"])
        self.assertEqual(0, transactions.rows[0]["attempts"])
        self.assertEqual(2, runner.last_claim_retry["retryCount"])
        runner.process_request.assert_not_called()
        runner.recover_request.assert_not_called()

    def test_connection_loss_timeout_and_sql_errors_are_not_retried(self):
        for stage, code in (("commit", 2013), ("select", 2006), ("update-1", 1205), ("cleanup", 1064)):
            with self.subTest(stage=stage, code=code):
                transactions = ClaimTransactions(faults=[(stage, code)])
                store = transactions.store()
                with self.assertRaises(RuntimeError) as raised:
                    store.claim("worker")
                self.assertEqual(code, raised.exception.args[0])
                self.assertEqual((1, 0, 1), (len(transactions.connections), transactions.commits, transactions.rollbacks))
                self.assertEqual(1, store.last_transaction_retry["attempts"])
                self.assertEqual(0, store.last_transaction_retry["retryCount"])
                self.assertFalse(store.last_transaction_retry["recovered"])
        self.sleep.assert_not_called()

    def test_deadlock_then_connection_loss_retains_full_receipt_without_retrying_loss(self):
        transactions = ClaimTransactions(faults=[("update-1", 1213), ("commit", 2013)])
        store = transactions.store()
        runner = self.runner(store)
        with self.assertRaises(RuntimeError) as raised:
            AIInferenceQueueRunner.run_once(runner)
        self.assertEqual(2013, raised.exception.args[0])
        self.assertEqual((2, 0, 2), (len(transactions.connections), transactions.commits, transactions.rollbacks))
        self.assertEqual({"operation": "ai-inference-claim", "attempts": 2, "retryCount": 1,
                          "recovered": False, "delaysMs": [30]}, runner.last_claim_retry)
        self.assertEqual(2, raised.exception.orbit_mysql_retry_receipt.attempts)
        runner.process_request.assert_not_called()
        self.sleep.assert_called_once_with(0.03)

    def test_committed_claim_with_lost_ack_is_not_retried_or_sent_to_model(self):
        transactions = ClaimTransactions()
        store = transactions.store()

        @contextmanager
        def committed_without_ack():
            with transactions.transaction() as connection:
                yield connection
            raise RuntimeError(2013, "injected acknowledgement loss after server commit")

        store.transaction = committed_without_ack
        runner = self.runner(store)
        with self.assertRaises(RuntimeError):
            AIInferenceQueueRunner.run_once(runner)
        self.assertEqual((1, 1), (len(transactions.connections), transactions.commits))
        self.assertEqual("processing", transactions.rows[0]["status"])
        self.assertEqual(1, transactions.rows[0]["attempts"])
        self.assertEqual(0, runner.last_claim_retry["retryCount"])
        runner.process_request.assert_not_called()
        store.transaction = transactions.transaction
        self.assertEqual([], store.claim("another-worker"))
        self.sleep.assert_not_called()

    def test_disabled_retry_attempts_transaction_only_once(self):
        transactions = ClaimTransactions(faults=[("cleanup", 1213)])
        store = transactions.store(retries=0)
        with self.assertRaises(MySQLDeadlockRetryExhausted):
            store.claim("worker")
        self.assertEqual(1, len(transactions.connections))
        self.assertEqual(0, store.last_transaction_retry["retryCount"])
        self.sleep.assert_not_called()

    def test_model_processing_is_outside_the_retried_claim_transaction(self):
        transactions = ClaimTransactions(count=2, faults=[("commit", 1213)])
        runner = self.runner(transactions.store())
        self.assertEqual(2, AIInferenceQueueRunner.run_once(runner, limit=2))
        self.assertEqual(["request-0", "request-1"], [call.args[0].request_id for call in runner.process_request.call_args_list])
        runner.recover_request.assert_not_called()
        self.assertEqual(1, runner.last_claim_retry["retryCount"])

    def test_previous_recovery_receipt_does_not_leak_into_a_failed_new_claim(self):
        transactions = ClaimTransactions(faults=[("select", 1213), None, ("cleanup", 2013)])
        store = transactions.store()
        store.claim("worker")
        self.assertTrue(store.last_transaction_retry["recovered"])
        with self.assertRaises(RuntimeError):
            store.claim("worker")
        self.assertEqual(1, store.last_transaction_retry["attempts"])
        self.assertEqual(0, store.last_transaction_retry["retryCount"])
        self.assertFalse(store.last_transaction_retry["recovered"])

    def test_scheduler_reports_recovered_claim_even_when_no_job_is_available(self):
        for recovered in (True, False):
            with self.subTest(recovered=recovered):
                runner = SimpleNamespace(last_claim_retry={"recovered": recovered, "attempts": 2,
                                                          "retryCount": 1, "delaysMs": [30]})
                scheduler = AIInferenceQueueScheduler(runner, 2, error_reporter=Mock())

                def run_once(**_kwargs):
                    scheduler.running = False
                    return 0

                runner.run_once = run_once
                with patch("digital_twin.infrastructure.schedulers.install_stop_handlers"), \
                     patch("digital_twin.infrastructure.schedulers.wait_until_running"), patch("builtins.print") as output:
                    scheduler.run_forever()
                messages = [call.args[0] for call in output.call_args_list]
                recovery = [line for line in messages if line.startswith("AI queue claim transaction recovered.")]
                self.assertEqual(int(recovered), len(recovery))
                if recovered:
                    self.assertIn("attempts=2 retries=1 delayMs=30", recovery[0])

    def test_retry_wrapper_preserves_original_claim_sql_and_row_guards(self):
        source = ast.parse(Path(inspect.getsourcefile(MySQLAIInferenceQueueStore)).read_text())
        owner = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "MySQLAIInferenceQueueStore")
        method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "claim")
        self.assertEqual(6, len(method.body))
        callback = method.body[3]
        self.assertEqual("claim_transaction", callback.name)
        self.assertEqual("transaction_with_deadlock_retry", method.body[-1].value.func.attr)
        self.assertEqual("ai-inference-claim", method.body[-1].value.args[0].value)
        transaction = ast.parse("with self.transaction() as connection:\n    pass").body[0]
        transaction.body = callback.body[3:-1]
        method.body = method.body[:3] + callback.body[:3] + [transaction, callback.body[-1]]
        fixture = Path(__file__).parent / "fixtures/stabilization_storage_members_v1.json"
        expected = json.loads(fixture.read_text())["MySQLAIInferenceQueueStore"]["claim"]
        self.assertEqual(expected, hashlib.sha256(ast.dump(method, include_attributes=False).encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
