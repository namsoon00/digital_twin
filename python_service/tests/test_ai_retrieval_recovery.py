"""Scoped cursors, bounded local correction and durable failed-response audit."""
import copy
import gzip
import hashlib
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.application.read_tools import ObservationReadTools
from digital_twin.modules.ai_orchestration.application.retrieval import retrieve_evidence, RetrievalLeaseLost, RetrievalContractFailure
from digital_twin.modules.ai_orchestration.domain.retrieval import (
    freeze_retrieval_input, validate_read_decision, response_audit, validate_trace, legacy,
)
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
from digital_twin.modules.reasoning.contracts import EvidenceContractError
from digital_twin.modules.reasoning.public import ObservationEvidenceReader
from test_ai_directed_retrieval import session, request, read, FINISH
from test_ai_control import SUBJECT
import test_ai_control as control_helpers


class RetrievalRecoveryTests(unittest.TestCase):
    def test_pages_advance_only_through_issued_query_bound_cursors_and_known_fact_ids(self):
        _, source = session()
        source.candidates.return_value = [source.candidates.return_value[0], *[
            {"id": "flow-" + str(i), "kind": "flow-metric", "symbol": "TEST", "flow": i} for i in range(9)]]
        captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
        tools = ObservationReadTools(captured, [{"summary": str(i)} for i in range(6)], [])
        first = tools.read(tools.resolve(request("flow")))
        self.assertEqual(4, len(first["facts"]))
        self.assertNotIn("nextOffset", first)
        cursor = first["nextCursor"]
        second = tools.read(tools.resolve(request("flow", cursor=cursor)))
        self.assertFalse({row["id"] for row in first["facts"]} & {row["id"] for row in second["facts"]})
        third = tools.read(tools.resolve(request("flow", cursor=second["nextCursor"])))
        self.assertEqual(1, len(third["facts"]))
        self.assertIsNone(third["nextCursor"])
        for bad in (request("company", cursor=cursor), request("flow", kind="flow-metric", cursor=cursor),
                    request("flow", cursor="invented"), request("flow", kind="unavailable-kind"),
                    request("flow", "read_fact", factId=first["facts"][0]["id"])):
            with self.subTest(bad=bad), self.assertRaises(EvidenceContractError):
                tools.resolve(bad)
        with self.assertRaises(EvidenceContractError):
            ObservationReadTools(captured, [], []).resolve(request("flow", cursor=cursor))
        tools.admit(first)
        fact_request = request("flow", "read_fact", factId=first["facts"][0]["id"])
        self.assertEqual([first["facts"][0]], tools.read(tools.resolve(fact_request))["facts"])
        with self.assertRaises(EvidenceContractError):
            tools.resolve({**fact_request, "category": "company"})
        memory = tools.read(tools.resolve(request("analyses", "recall_memory")))
        self.assertEqual(4, len(memory["records"]))
        rest = tools.read(tools.resolve(request("analyses", "recall_memory", cursor=memory["nextCursor"])))
        self.assertEqual([{"summary": "4"}, {"summary": "5"}], rest["records"])
        frozen = freeze_retrieval_input(captured.packet(), tools.context(), 65536)
        validate_execution_input(frozen)
        definitions = frozen["outputSchema"]["properties"]["requests"]["items"]["anyOf"]
        self.assertIn(cursor, definitions[0]["properties"]["cursor"]["enum"])
        self.assertNotIn(cursor, definitions[2]["properties"]["cursor"]["enum"])
        for bad in (request(limit=99), request(offset=-1), request("flow", "recall_memory"),
                    request(tool="execute_sql"), {**fact_request, "accountId": "other"}):
            with self.subTest(bad=bad), self.assertRaises(EvidenceContractError):
                validate_read_decision(read(bad))

    def test_one_correction_keeps_prior_facts_memory_and_snapshot_within_original_call_budget(self):
        captured, source = session()
        captured.read = Mock(wraps=captured.read)
        envelopes, audit = [], []
        def save(value):
            validate_execution_input(value)
            envelopes.append(copy.deepcopy(value))
            return "input-" + str(len(envelopes))
        def record(step):
            audit.append(step)
            return True
        wrong = read(request(limit=100))
        model = Mock(side_effect=[read(request("flow"), request("analyses", "recall_memory")), wrong, read(request())])
        packet, history, memories, trace = retrieve_evidence(captured, captured.packet(), [{"summary": "prior"}], [],
            model, save, 256 * 1024, record_round=record)
        self.assertEqual(3, model.call_count)
        self.assertEqual(4, captured.read.call_count)  # Two local minimum previews and two admitted pages.
        self.assertEqual(1, source.candidates.call_count)
        self.assertEqual("prior", history[0]["summary"])
        self.assertEqual("required-continuity", history[0]["memoryRole"])
        self.assertEqual({"summary": "prior"}, history[1])
        self.assertEqual("round-limit", packet["retrieval"]["status"])
        self.assertEqual(1, packet["retrieval"]["corrections"])
        self.assertEqual({"stock", "flow-metric", "evidence:financial-fact"}, {r["kind"] for r in packet["facts"]})
        self.assertEqual(wrong, json.loads(audit[1]["response"]["text"]))
        self.assertEqual(audit[1]["errors"], envelopes[2]["retrievalContext"]["correction"]["errors"])
        self.assertEqual(0, envelopes[2]["retrievalContext"]["callsRemainingAfterThis"])
        self.assertEqual(trace, audit)
        final = freeze_execution_input(packet, history, memories, retrieval_trace=trace)
        self.assertEqual(1, final["memoryCoverage"]["analysesIncluded"])
        self.assertEqual(1, final["memoryCoverage"]["analysesAvailable"])
        validate_execution_input(final)
        changed = copy.deepcopy(final)
        changed["retrievalTrace"][1]["response"]["text"] += " "
        with self.assertRaises(EvidenceContractError):
            validate_execution_input(changed)

    def test_invalid_second_request_rejects_batch_without_partial_read(self):
        captured, _ = session()
        captured.read = Mock(wraps=captured.read)
        model = Mock(side_effect=[read(request("flow"), request(cursor="forged")), FINISH])
        saved = []
        packet, _, _, trace = retrieve_evidence(captured, captured.packet(), [], [], model, lambda _: "input", 65536,
            record_round=lambda step: saved.append(step) or True)
        captured.read.assert_not_called()
        self.assertEqual("invalid-request", trace[0]["status"])
        self.assertEqual([], trace[0]["reads"])
        self.assertEqual("deferred", packet["retrieval"]["status"])
        self.assertTrue(trace[1]["correction"])

    def test_unrepaired_requests_are_failures_and_never_generate_or_publish_a_judgment(self):
        for responses, budget, stop in (([read(request(limit=10))] * 2, 3, "correction-exhausted"),
                                        ([read(request(limit=10))], 1, "call-budget")):
            captured, _ = session()
            service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(return_value=captured),
                read_planner=Mock(side_effect=responses), read_round_budget=lambda: budget)
            store.fail.return_value = {"status": "failed"}
            self.assertEqual("failed", service.run_once()["status"])
            self.assertEqual(len(responses), service.read_planner.call_count)
            self.assertEqual(len(responses), store.save_retrieval_round.call_count)
            failure = store.fail.call_args.kwargs["result"]["failure"]
            self.assertEqual(stop, failure["stopReason"])
            self.assertEqual("invalid-request", failure["retrieval"]["status"])
            self.assertTrue(store.fail.call_args.kwargs["terminal"])
            planner.assert_not_called(); store.complete.assert_not_called()
        for actual, reported in (("failed", "failed"), ("pending", "deferred"), ("lease-lost", "lease-lost")):
            service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(side_effect=TimeoutError))
            store.fail.return_value = {"status": actual}
            self.assertEqual(reported, service.run_once()["status"])
            self.assertEqual("ai-execution:timeout", store.fail.call_args.args[1])
            planner.assert_not_called()

    def test_lost_audit_lease_or_input_lease_stops_before_correction_call(self):
        for lost_at in ("audit", "input"):
            captured, _ = session()
            model = Mock(return_value=read(request(limit=100)))
            save = Mock(side_effect=["input-1", ""])
            audit = Mock(return_value=lost_at != "audit")
            with self.assertRaises(RetrievalLeaseLost):
                retrieve_evidence(captured, captured.packet(), [], [], model, save, 65536, record_round=audit)
            model.assert_called_once()
            self.assertEqual(1 if lost_at == "audit" else 2, save.call_count)

    def test_malformed_output_can_be_corrected_but_transport_failures_remain_operational(self):
        from digital_twin.modules.ai_orchestration.infrastructure.observation_model import StructuredObservationModel
        from digital_twin.infrastructure.news_ai_analyzer import first_json_object
        captured, _ = session()
        envelope = freeze_retrieval_input(captured.packet(), {}, 65536)
        runner = Mock()
        adapter = StructuredObservationModel(lambda _: ["model"], runner, first_json_object)
        for raw in ("{bad json}", "no json", "", "[]"):
            runner.return_value = SimpleNamespace(stdout=raw)
            response = adapter(envelope, {})
            self.assertEqual(raw, response["unparseableResponse"])
            self.assertEqual(response, json.loads(response_audit(response)["text"]))
            with self.assertRaises(EvidenceContractError):
                validate_read_decision(response)
        runner.side_effect = TimeoutError("provider private details")
        with self.assertRaises(TimeoutError):
            adapter(envelope, {})
        huge = response_audit({"unparseableResponse": "x" * 100000})
        self.assertTrue(huge["omitted"])
        self.assertNotIn("text", huge)
        self.assertGreater(huge["bytes"], 100000)
        self.assertEqual(64, len(huge["hash"]))
        model = Mock(side_effect=[{"unparseableResponse": "{bad json}"}, read(request("flow")), FINISH])
        packet, history, memory, trace = retrieve_evidence(captured, captured.packet(), [], [], model, lambda _: "input", 65536)
        self.assertEqual("ready", packet["retrieval"]["status"])
        validate_execution_input(freeze_execution_input(packet, history, memory, retrieval_trace=trace))

    def test_legacy_numeric_page_prompts_and_final_traces_remain_exactly_replayable(self):
        captured, _ = session()
        decision = read({"tool": "query_facts", "category": "flow", "kind": "", "factId": "", "offset": 0, "limit": 1})
        result = captured.read("flow", limit=1)
        trace = [{"inputId": "legacy-input", "decision": decision, "reads": [{"request": decision["requests"][0], "result": result}]}]
        old_input = legacy.freeze_retrieval_input(captured.packet(), {"trace": trace}, 65536)
        validate_execution_input(old_input)
        packet = captured.select([row["id"] for row in result["facts"]])
        packet["retrieval"] = legacy.trace_summary(trace, "round-limit")
        author = freeze_execution_input(packet, [], [], retrieval_trace=trace)
        from digital_twin.modules.ai_orchestration.domain.execution_input import DIRECTED_PROMPT_VERSION
        from digital_twin.modules.ai_orchestration.domain.observation_clock import citable_management_schema
        author.update(promptVersion=DIRECTED_PROMPT_VERSION, prompt=legacy.directed_planning_prompt(packet, [], []),
                      outputSchema=citable_management_schema(packet, []))
        author["promptHash"] = hashlib.sha256(author["prompt"].encode()).hexdigest()
        validate_execution_input(author)
        self.assertEqual(legacy.directed_planning_prompt(packet, [], []), author["prompt"])
        self.assertEqual(hashlib.sha256(old_input["prompt"].encode()).hexdigest(), old_input["promptHash"])
        old_input["outputSchema"]["properties"]["requests"]["maxItems"] = 2
        with self.assertRaises(EvidenceContractError):
            validate_execution_input(old_input)


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class RetrievalAuditStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import load_local_env, runtime_settings
        from mysql_fixtures import mysql_test_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        load_local_env()
        isolated = mysql_test_settings()
        self.settings = {**runtime_settings(), **isolated, "aiControlBudgetEnabled": "false"}
        self.store = MySQLAIControlStore(self.settings)
        self.clean()
        self.store.seed(SUBJECT)
        self.job = self.store.claim()

    def clean(self):
        with self.store.transaction() as c:
            for table in ("ai_control_input_calls", "ai_control_inputs", "ai_control_tasks", "ai_control_budget", "ai_control_calls"):
                c.execute("DELETE FROM " + table)

    def tearDown(self):
        self.clean()

    def record_failure(self):
        captured, _ = session()
        packet = {**captured.packet(), "taskId": self.job["taskId"]}
        with self.assertRaises(RetrievalContractFailure) as failure:
            retrieve_evidence(captured, packet, [], [], Mock(return_value=read(request(limit=100))),
                lambda value: self.store.save_execution_input(self.job, value), 65536,
                record_round=lambda step: self.store.save_retrieval_round(self.job, step))
        with self.store.connect() as c:
            rows = c.execute("SELECT artifact_gzip FROM ai_control_retrieval_rounds ORDER BY created_at").fetchall()
        return failure.exception.failure, [json.loads(gzip.decompress(row["artifact_gzip"])) for row in rows]

    def test_audit_survives_reload_rejects_overwrite_and_tracks_input_retirement(self):
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        failure, rounds = self.record_failure()
        self.assertEqual(2, len(rounds))
        self.assertFalse(rounds[0]["correction"])
        self.assertTrue(rounds[1]["correction"])
        validate_trace(rounds)
        reloaded = MySQLAIControlStore(self.settings)
        self.assertTrue(reloaded.save_retrieval_round(self.job, rounds[0]))
        changed = copy.deepcopy(rounds[0]); changed["errors"][0]["expected"] = "different"
        with self.assertRaisesRegex(ValueError, "immutable"):
            reloaded.save_retrieval_round(self.job, changed)
        with self.store.transaction() as c:
            c.execute("DELETE FROM ai_control_inputs WHERE input_id=%s", (rounds[0]["inputId"],))
            remaining = c.execute("SELECT COUNT(*) AS n FROM ai_control_retrieval_rounds").fetchone()["n"]
        self.assertEqual(1, remaining)

    def test_stale_attempt_lease_and_foreign_input_cannot_write_an_audit(self):
        _, rounds = self.record_failure()
        for changed in ({"leaseToken": "old"}, {"attempts": 99}):
            self.assertFalse(self.store.save_retrieval_round({**self.job, **changed}, rounds[0]))
        with self.assertRaisesRegex(ValueError, "owned frozen input"):
            self.store.save_retrieval_round(self.job, {**rounds[0], "inputId": "other-task-input"})
        with self.store.connect() as c:
            c.execute("UPDATE ai_control_tasks SET lease_until='2000' WHERE task_id=%s", (self.job["taskId"],))
        self.assertFalse(self.store.save_retrieval_round(self.job, rounds[0]))
        self.assertEqual({"status": "lease-lost"}, self.store.fail(self.job, "ai-retrieval-invalid-request", terminal=True))
        with self.store.connect() as c:
            self.assertEqual(1, c.execute("SELECT COUNT(*) AS n FROM ai_control_tasks").fetchone()["n"])

    def test_terminal_failure_and_delayed_recovery_commit_together_without_publication(self):
        failure, rounds = self.record_failure()
        self.store.outbox_writer = Mock()
        self.store.agenda_failure = Mock(side_effect=RuntimeError("rollback"))
        with self.assertRaises(RuntimeError):
            self.store.fail(self.job, "ai-retrieval-invalid-request", terminal=True, result={"failure": failure})
        with self.store.connect() as c:
            self.assertEqual(1, c.execute("SELECT COUNT(*) AS n FROM ai_control_tasks").fetchone()["n"])
            self.assertEqual("processing", c.execute("SELECT status FROM ai_control_tasks").fetchone()["status"])
        self.store.agenda_failure.side_effect = None
        self.assertEqual({"status": "failed"}, self.store.fail(self.job, "ai-retrieval-invalid-request", terminal=True, result={"failure": failure}))
        self.assertEqual({"status": "lease-lost"}, self.store.fail(self.job, "ai-retrieval-invalid-request", terminal=True))
        with self.store.connect() as c:
            rows = c.execute("SELECT status,result_json,available_at,created_at FROM ai_control_tasks ORDER BY created_at").fetchall()
        self.assertEqual(["failed", "pending"], [row["status"] for row in rows])
        self.assertEqual(failure, json.loads(rows[0]["result_json"])["failure"])
        self.assertGreater(rows[1]["available_at"], rows[1]["created_at"])
        self.store.outbox_writer.assert_not_called()
