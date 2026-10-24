"""One source preparation, bounded execution and independently fenced subjects."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.infrastructure.graph_writer_guard import LocalGraphWriterGuard
from digital_twin.modules.reasoning.domain.event_types import ONTOLOGY_REASONING_REQUESTED
from digital_twin.modules.reasoning.domain.fact_changes import fact_change_contract
from digital_twin.modules.reasoning.domain.independent_reasoning import independent_reasoning_request, shard_reasoning_event
from digital_twin.modules.reasoning.domain.reasoning_batch import (
    SIBLING_EXECUTION_BATCH_VERSION, reasoning_batch_key, reasoning_batch_fits,
)
from digital_twin.modules.reasoning.application.independent_reasoning_engine import (
    IndependentReasoningJobRunner, V2ReasoningEngine,
)
from stabilization_database import StabilizationDatabaseCase
from test_independent_reasoning_engine import descriptor, FakeAssembler, FakeExecutor


def sibling_events(symbols=("AAA", "BBB"), account="fixture", at="", parent="source:batch"):
    at = at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    facts = [{"factId": "fact:" + symbol, "factType": "MarketQuote", "aggregateId": symbol, "subjectIds": [symbol],
              "revision": "revision:" + symbol, "observedAt": at, "ingestedAt": at,
              "payload": {"symbol": symbol, "currentPrice": index + 100}}
             for index, symbol in enumerate(symbols)]
    boundary = {"accountId": account, "snapshotId": "source:" + at, "generatedAt": at,
                "sourceFactCoverageComplete": True, "expectedSourceFactCount": len(facts),
                "capturedSourceFactCount": len(facts), "sourceFactCaptureLimit": 1200}
    event = DomainEvent(name=ONTOLOGY_REASONING_REQUESTED, aggregate_id="source:" + account,
        event_id=parent, occurred_at=at, payload={
            "accountId": account, "symbols": list(symbols), "factTypes": ["MarketQuote"],
            "factTypesBySymbol": {symbol: ["MarketQuote"] for symbol in symbols},
            "factChangeContract": fact_change_contract(["MarketQuote"], {symbol: ["MarketQuote"] for symbol in symbols}),
            "changedFieldsBySymbol": {symbol: ["currentPrice"] for symbol in symbols},
            "factRevisionsBySymbol": {symbol: "revision:" + symbol for symbol in symbols},
            "trigger": "verified-monitor-snapshot", "sourceObservedAt": at,
            "sourceFacts": facts, "verifiedSourceSnapshot": boundary,
            "verifiedSourceSnapshots": [deepcopy(boundary)],
        })
    return shard_reasoning_event(event, 1)


class SiblingExecutionBatchTests(unittest.TestCase):
    def test_only_complete_siblings_share_preparation_and_input_is_unchanged(self):
        events = [event.to_dict() for event in sibling_events()]
        before = deepcopy(events)
        key = reasoning_batch_key(events[0], "REALTIME")
        self.assertEqual(SIBLING_EXECUTION_BATCH_VERSION, key[0])
        self.assertEqual(key, reasoning_batch_key(events[1], "REALTIME"))
        self.assertNotEqual(key, reasoning_batch_key(events[1], "CONTEXT"))
        changes = [
            lambda p: p.update(candidateValidationReplay=True),
            lambda p: p.update(validationPair={"inputMode": "comparison"}),
            lambda p: p.update(fullReconciliation=True),
            lambda p: p.update(importanceGate="non-fungible"),
            lambda p: p.update(workClass="GOVERNANCE"),
            lambda p: p.update(coalescedReasoningChanges={"eventCount": 2}),
            lambda p: p.update(accountId="other-account"),
            lambda p: p.update(sourceObservedAt="different-clock"),
            lambda p: p.update(futureUnrecognizedContract="different"),
            lambda p: p["reasoningShard"].update(parentEventId="different-event"),
            lambda p: p["verifiedSourceSnapshot"].update(snapshotId="different-source"),
            lambda p: p["verifiedSourceSnapshot"].update(sourceFactCoverageComplete=False),
            lambda p: p["verifiedSourceSnapshot"].update(expectedSourceFactCount=2),
            lambda p: p["sourceFacts"].clear(),
            lambda p: p["sourceFacts"][0].update(factId=["invalid"]),
            lambda p: p["factChangeContract"].update(unclassifiedFactTypesBySymbol={"BBB": ["unknown"]}),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                changed = deepcopy(events[1]); change(changed["payload"])
                self.assertNotEqual(key, reasoning_batch_key(changed, "REALTIME"))
        self.assertEqual(before, events)

    def test_coverage_alias_correction_preserves_other_sources_and_explicit_gaps(self):
        events = sibling_events()
        payload = deepcopy(events[0].payload)
        payload.pop("reasoningShard")
        payload["symbols"] = ["AAA", "BBB"]
        payload["sourceFacts"] = [deepcopy(event.payload["sourceFacts"][0]) for event in events]
        boundary = payload["verifiedSourceSnapshot"]
        boundary.update(expectedSourceFactCount=2, capturedSourceFactCount=2)
        incomplete = {**boundary, "sourceFactCoverageComplete": False, "expectedSourceFactCount": 9}
        other = {**boundary, "accountId": "other-account"}
        payload["verifiedSourceSnapshots"] = [deepcopy(boundary), incomplete, other]
        parent = replace(events[0], payload=payload)
        shards = shard_reasoning_event(parent, 1)
        for shard in shards:
            aliases = shard.payload["verifiedSourceSnapshots"]
            self.assertEqual(1, aliases[0]["expectedSourceFactCount"])
            self.assertEqual(incomplete, aliases[1])
            self.assertEqual(other, aliases[2])
        payload.pop("verifiedSourceSnapshots")
        for shard in shard_reasoning_event(replace(parent, payload=payload), 1):
            self.assertNotIn("verifiedSourceSnapshots", shard.payload)

    def test_combined_request_retains_every_subject_revision_and_source_fact(self):
        events = sibling_events()
        for event in events:
            self.assertEqual(event.payload["verifiedSourceSnapshot"], event.payload["verifiedSourceSnapshots"][0])
        request = independent_reasoning_request("fixture-release", events)
        self.assertEqual(("AAA", "BBB"), request.symbols)
        self.assertEqual(tuple(event.event_id for event in events), request.source_event_ids)
        self.assertEqual({"AAA": "revision:AAA", "BBB": "revision:BBB"}, request.context["factRevisionsBySymbol"])
        self.assertEqual({"AAA": ["currentPrice"], "BBB": ["currentPrice"]}, request.context["changedFieldsBySymbol"])
        self.assertEqual(2, request.context["sourceFactCoverage"]["capturedCount"])
        self.assertEqual(2, request.context["sourceFactCoverage"]["expectedCount"])
        self.assertTrue(request.context["sourceFactCoverage"]["complete"])
        self.assertEqual({"fact:AAA", "fact:BBB"}, {row["factId"] for row in request.context["sourceFacts"]})
        individual = [independent_reasoning_request("fixture-release", [event]) for event in events]
        for single in individual:
            symbol = single.symbols[0]
            for field in ("requestedScopeFamiliesBySymbol", "requestedDependencyKeysBySymbol", "revisionVectorsBySymbol"):
                self.assertEqual(single.context[field][symbol], request.context[field][symbol])

    def test_symbol_and_fact_budgets_are_enforced_without_truncating_sources(self):
        events = [event.to_dict() for event in sibling_events(("AAA", "BBB", "CCC"))]
        self.assertTrue(reasoning_batch_fits(events[:2], 2))
        self.assertFalse(reasoning_batch_fits(events, 2))
        duplicate = deepcopy(events[1]); duplicate["payload"]["sourceFacts"][0]["factId"] = "fact:AAA"
        self.assertFalse(reasoning_batch_fits([events[0], duplicate], 2))
        self.assertFalse(reasoning_batch_fits([events[0], events[0]], 2))
        large = deepcopy(events[:2])
        for index, event in enumerate(large):
            p = event["payload"]
            p["sourceFacts"] = [{"factId": f"{index}:{number}"} for number in range(601)]
            for b in [p["verifiedSourceSnapshot"], *p["verifiedSourceSnapshots"]]:
                b.update(expectedSourceFactCount=601, capturedSourceFactCount=601)
        self.assertFalse(reasoning_batch_fits(large, 2))
        self.assertTrue(reasoning_batch_fits([{"payload": {"symbols": "AAA"}}], 1))

    def test_combined_engine_prepares_once_and_keeps_both_source_events(self):
        assembler = FakeAssembler(); assembler.assemble = Mock(wraps=assembler.assemble)
        executor = FakeExecutor(); executor.execute = Mock(wraps=executor.execute)
        builder = SimpleNamespace(build=Mock(return_value={"detected": [], "ready": []}))
        engine = V2ReasoningEngine(descriptor(), assembler, executor, builder)
        events = sibling_events()
        result = engine.consume(events)
        self.assertEqual(1, assembler.assemble.call_count)
        self.assertEqual(1, executor.execute.call_count)
        self.assertEqual(("AAA", "BBB"), assembler.assemble.call_args.args[0].symbols)
        self.assertEqual(2, len(assembler.assemble.call_args.args[0].source_event_ids))
        self.assertEqual(["AAA", "BBB"], result["evaluated_symbols"])
        self.assertEqual([], result["delivery_events"])


class SiblingExecutionClaimTests(StabilizationDatabaseCase):
    def test_claim_groups_only_same_source_siblings_and_leaves_overflow_untouched(self):
        events = sibling_events(("AAA", "BBB", "CCC"), parent=self.deployment)
        other = sibling_events(("OTHER",), parent=self.deployment + ":other")[0]
        old_limit = self.jobs.runtime_settings.get("typedbNativeRuleTargetSymbolLimit")
        self.addCleanup(self.jobs.runtime_settings.__setitem__, "typedbNativeRuleTargetSymbolLimit", old_limit)
        self.jobs.runtime_settings["typedbNativeRuleTargetSymbolLimit"] = 2
        for event in [events[0], other, *events[1:]]:
            self.events.handle(event)
        before = self.sql("SELECT * FROM reasoning_engine_jobs WHERE deployment_id=%s ORDER BY created_at,job_id", (self.deployment,), True)
        claimed = self.jobs.claim(self.deployment, "batch-worker", 3, 60)
        self.assertEqual(2, len(claimed))
        self.assertEqual({events[0].event_id, events[1].event_id}, {row["sourceEventId"] for row in claimed})
        claimed_ids = {row["jobId"] for row in claimed}
        for row in before:
            if row["job_id"] not in claimed_ids:
                self.assertEqual(row, self.sql("SELECT * FROM reasoning_engine_jobs WHERE job_id=%s", (row["job_id"],)))
        for row in claimed:
            lineage = self.sql("SELECT source_event_id FROM reasoning_engine_job_sources WHERE survivor_job_id=%s", (row["jobId"],), True)
            self.assertIn(row["sourceEventId"], {value["source_event_id"] for value in lineage})

    def test_runner_completes_only_covered_subject_and_releases_writer_after_batch(self):
        events = sibling_events(parent=self.deployment)
        for event in events:
            self.events.handle(event)
        consumed = []
        engine = SimpleNamespace(
            descriptor=lambda: replace(descriptor(), deployment_id=self.deployment),
            consume=lambda source_events: consumed.append(source_events) or {
                "request_id": "fixture:request", "status": "ok", "retryable": False,
                "evaluated_symbols": ["AAA"], "not_evaluated_symbols": ["BBB"],
            },
            health=lambda: {"status": "ready"},
        )
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        guard = LocalGraphWriterGuard("batch-worker", "delivery", Path(temporary.name))
        self.addCleanup(guard.release)
        runner = IndependentReasoningJobRunner(self.jobs, engine, self.registry,
            settings={"typedbNativeRuleTargetSymbolLimit": 2}, graph_writer_guard=guard)
        runner.prepare_queue_turn = lambda: {"status": "ready", **{key: {} for key in (
            "candidateValidationWindow", "marketObservationAnchorReconciliation", "reasoningCaseExpiry",
            "routeReconciliation", "engineComparisonReconciliation", "leaseRecovery", "failureRecovery",
            "staleObservationCleanup", "backlogCompaction")}, "repairedIngressCount": 0}
        result = runner.run_once()
        self.assertEqual(2, len(consumed[0]))
        self.assertTrue(result["result"]["execution_batch"]["sharedSourcePreparation"])
        rows = self.sql("SELECT source_event_id,job_status FROM reasoning_engine_jobs WHERE deployment_id=%s", (self.deployment,), True)
        by_source = {row["source_event_id"]: row["job_status"] for row in rows}
        self.assertEqual("completed", by_source[events[0].event_id])
        self.assertEqual("excluded", by_source[events[1].event_id])
        self.assertFalse(guard.status()["acquired"])


if __name__ == "__main__":
    unittest.main()
