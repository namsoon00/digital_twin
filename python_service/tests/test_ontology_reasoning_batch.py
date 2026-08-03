import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_reasoning_service import (
    OntologyReasoningRunner,
    reasoning_request_provenance,
)
from digital_twin.domain.events import DomainEvent, ontology_reasoning_requested_event
from digital_twin.domain.ontology_projection_audit import compact_reasoning_request_context
from digital_twin.domain.ontology_reasoning_batch import adaptive_reasoning_batch_plan


class MemoryCursor:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def load(self):
        return dict(self.payload)

    def save(self, payload):
        self.payload = dict(payload or {})

    def processed_event_ids(self):
        return []


class AdaptiveOntologyReasoningBatchTests(unittest.TestCase):
    def test_request_provenance_keeps_fact_families_bound_to_each_symbol(self):
        market = DomainEvent(
            name="ontology.reasoning.requested",
            aggregate_id="market:KR",
            payload={
                "symbols": ["066570"],
                "factTypes": ["MarketQuote", "TechnicalIndicator"],
            },
        )
        research = DomainEvent(
            name="ontology.reasoning.requested",
            aggregate_id="research:US",
            payload={
                "symbols": ["AAPL"],
                "factTypes": ["ResearchEvidence"],
            },
        )

        provenance = reasoning_request_provenance(
            [market, research],
            target_symbols=["066570", "AAPL"],
        )

        self.assertEqual(
            {"066570": ["market", "temporal"], "AAPL": ["evidence"]},
            provenance["requestedScopeFamiliesBySymbol"],
        )

    def test_persistent_runner_hot_reloads_only_operational_batch_controls(self):
        propagated = []
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningTypeDbNativeRuleExecutionEnabled": "1",
                "ontologyReasoningMaxSymbolsPerRun": "4",
                "typedbNativeRuleTargetSymbolLimit": "4",
                "typedbAddress": "old-driver-address",
            },
            operational_settings_refresher=lambda settings, changed, removed: propagated.append(
                (settings, changed, removed)
            ),
        )

        refreshed = runner.refresh_operational_settings({
            "ontologyReasoningMaxSymbolsPerRun": "8",
            "typedbNativeRuleTargetSymbolLimit": "8",
            "ontologyReasoningAdaptiveBatchBurstSymbols": "8",
            "typedbAddress": "must-not-replace-warm-driver",
        })

        self.assertEqual("updated", refreshed["status"])
        self.assertEqual(8, runner.effective_max_symbols_per_run())
        self.assertEqual("old-driver-address", runner.settings["typedbAddress"])
        self.assertEqual(
            [
                "ontologyReasoningMaxSymbolsPerRun",
                "typedbNativeRuleTargetSymbolLimit",
                "ontologyReasoningAdaptiveBatchBurstSymbols",
            ],
            refreshed["changedKeys"],
        )
        self.assertEqual(1, len(propagated))
        self.assertEqual("8", propagated[0][0]["typedbNativeRuleTargetSymbolLimit"])

    def test_native_runtime_defaults_to_one_subject_even_when_backlog_is_old(self):
        source = DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={})
        request = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["005930", "000660", "035420"],
            changed_count=3,
            fact_types=["MarketQuote"],
        )
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
            settings={"ontologyReasoningTypeDbNativeRuleExecutionEnabled": "1"},
        )

        plan = runner.reasoning_batch_plan([request])

        self.assertEqual(1, runner.effective_max_symbols_per_run())
        self.assertEqual(1, plan["hardTargetSymbolLimit"])
        self.assertEqual(1, plan["targetSymbolLimit"])

    def test_queue_pressure_expands_to_the_bounded_burst_target_set(self):
        plan = adaptive_reasoning_batch_plan(
            {
                "ontologyReasoningAdaptiveBatchEnabled": "1",
                "ontologyReasoningAdaptiveBatchSteadySymbols": "1",
                "ontologyReasoningAdaptiveBatchBurstSymbols": "3",
                "ontologyReasoningAdaptiveBatchPendingThreshold": "4",
                "ontologyReasoningAdaptiveBatchAgeSeconds": "60",
            },
            native_rule_execution=True,
            hard_target_symbol_limit=3,
            pending_request_count=5,
            pending_symbol_count=5,
            oldest_wait_seconds=10,
            recent_execution={"status": "ok", "durationMs": 40000},
        )

        self.assertEqual("queue-pressure", plan["mode"])
        self.assertEqual(3, plan["targetSymbolLimit"])
        self.assertTrue(plan["pressure"])
        self.assertFalse(plan["runtimeGuard"])

    def test_observed_per_target_cost_limits_a_pressure_batch_before_timeout(self):
        plan = adaptive_reasoning_batch_plan(
            {
                "ontologyReasoningAdaptiveBatchEnabled": "1",
                "ontologyReasoningAdaptiveBatchSteadySymbols": "1",
                "ontologyReasoningAdaptiveBatchBurstSymbols": "3",
                "ontologyReasoningAdaptiveBatchPendingThreshold": "2",
                "ontologyReasoningAdaptiveBatchAgeSeconds": "10",
                "ontologyReasoningAdaptiveBatchBudgetSeconds": "150",
            },
            native_rule_execution=True,
            hard_target_symbol_limit=3,
            pending_request_count=8,
            pending_symbol_count=5,
            oldest_wait_seconds=120,
            recent_execution={
                "status": "ok",
                "stageTiming": {"monitorAndProjectionMs": 120000},
                "projectionRuntime": {"targetSymbolCount": 1},
            },
        )

        self.assertEqual("runtime-budget-limited", plan["mode"])
        self.assertEqual(1, plan["targetSymbolLimit"])
        self.assertEqual(1, plan["budgetTargetSymbolLimit"])
        self.assertEqual(120000, plan["estimatedPerTargetRuntimeMs"])

    def test_coherent_generation_prices_shared_work_once_and_ramps_targets(self):
        plan = adaptive_reasoning_batch_plan(
            {
                "ontologyReasoningAdaptiveBatchEnabled": "1",
                "ontologyReasoningAdaptiveBatchSteadySymbols": "1",
                "ontologyReasoningAdaptiveBatchBurstSymbols": "3",
                "ontologyReasoningAdaptiveBatchPendingThreshold": "2",
                "ontologyReasoningAdaptiveBatchAgeSeconds": "10",
                "ontologyReasoningAdaptiveBatchBudgetSeconds": "150",
                "typedbNativeRuleTargetParallelism": "2",
            },
            native_rule_execution=True,
            hard_target_symbol_limit=3,
            pending_request_count=8,
            pending_symbol_count=5,
            oldest_wait_seconds=120,
            recent_execution={
                "status": "ok",
                "stageTiming": {"monitorAndProjectionMs": 153000},
                "projectionRuntime": {
                    "durationMs": 123000,
                    "nativeInferenceMs": 18000,
                    "targetSymbolCount": 1,
                },
            },
        )

        # A single target contains 105s of generation work and 18s of native
        # target work. Two targets share the same native-parallel wave, so the
        # first pressure turn expands safely to two rather than pricing every
        # target as a 123s full generation.
        self.assertEqual("queue-pressure-ramp", plan["mode"])
        self.assertEqual(2, plan["targetSymbolLimit"])
        self.assertEqual(3, plan["budgetTargetSymbolLimit"])
        self.assertEqual("projection-runtime", plan["runtimeEstimateBasis"])
        self.assertEqual(105000, plan["estimatedFixedRuntimeMs"])
        self.assertEqual(18000, plan["estimatedIncrementalTargetRuntimeMs"])
        self.assertEqual(141000, plan["estimatedBurstRuntimeMs"])

    def test_measured_two_target_batch_can_ramp_to_the_configured_burst(self):
        plan = adaptive_reasoning_batch_plan(
            {
                "ontologyReasoningAdaptiveBatchEnabled": "1",
                "ontologyReasoningAdaptiveBatchSteadySymbols": "1",
                "ontologyReasoningAdaptiveBatchBurstSymbols": "3",
                "ontologyReasoningAdaptiveBatchPendingThreshold": "2",
                "ontologyReasoningAdaptiveBatchAgeSeconds": "10",
                "ontologyReasoningAdaptiveBatchBudgetSeconds": "150",
                "typedbNativeRuleTargetParallelism": "2",
            },
            native_rule_execution=True,
            hard_target_symbol_limit=3,
            pending_request_count=8,
            pending_symbol_count=5,
            oldest_wait_seconds=120,
            recent_execution={
                "status": "ok",
                "stageTiming": {"monitorAndProjectionMs": 150000},
                "projectionRuntime": {
                    "durationMs": 126000,
                    "nativeInferenceMs": 21000,
                    "targetSymbolCount": 2,
                },
            },
        )

        self.assertEqual("queue-pressure", plan["mode"])
        self.assertEqual(3, plan["targetSymbolLimit"])
        self.assertEqual(3, plan["rampTargetSymbolLimit"])

    def test_old_backlog_uses_the_measured_burst_without_one_target_ramp(self):
        plan = adaptive_reasoning_batch_plan(
            {
                "ontologyReasoningAdaptiveBatchEnabled": "1",
                "ontologyReasoningAdaptiveBatchSteadySymbols": "1",
                "ontologyReasoningAdaptiveBatchBurstSymbols": "13",
                "ontologyReasoningAdaptiveBatchPendingThreshold": "2",
                "ontologyReasoningAdaptiveBatchAgeSeconds": "30",
                "ontologyReasoningAdaptiveBatchBudgetSeconds": "240",
                "ontologyReasoningAdaptiveBatchBacklogBurstEnabled": "1",
                "ontologyReasoningAdaptiveBatchBacklogBurstAgeSeconds": "120",
                "typedbNativeRuleTargetParallelism": "4",
            },
            native_rule_execution=True,
            hard_target_symbol_limit=13,
            pending_request_count=24,
            pending_symbol_count=13,
            oldest_wait_seconds=241,
            recent_execution={
                "status": "ok",
                "stageTiming": {"monitorAndProjectionMs": 128000},
                "projectionRuntime": {
                    "durationMs": 108000,
                    "nativeInferenceMs": 35789,
                    "targetSymbolCount": 1,
                },
            },
        )

        # One target paid about 72s of shared generation work and 36s of
        # target work. Four target-read workers keep all thirteen targets
        # inside the 240s budget, so a four-minute-old queue must not spend
        # twelve generations ramping one target at a time.
        self.assertEqual("backlog-escape", plan["mode"])
        self.assertEqual(13, plan["targetSymbolLimit"])
        self.assertTrue(plan["backlogEscape"])
        self.assertEqual(120, plan["backlogBurstAgeSeconds"])
        self.assertIn("backlog-burst-oldest-wait", plan["reasonCodes"])

    def test_slow_or_failed_projection_returns_to_the_steady_target_set(self):
        plan = adaptive_reasoning_batch_plan(
            {
                "ontologyReasoningAdaptiveBatchEnabled": "1",
                "ontologyReasoningAdaptiveBatchSteadySymbols": "1",
                "ontologyReasoningAdaptiveBatchBurstSymbols": "3",
                "ontologyReasoningAdaptiveBatchPendingThreshold": "2",
                "ontologyReasoningAdaptiveBatchAgeSeconds": "10",
                "ontologyReasoningAdaptiveBatchRuntimeGuardSeconds": "180",
            },
            native_rule_execution=True,
            hard_target_symbol_limit=3,
            pending_request_count=10,
            pending_symbol_count=10,
            oldest_wait_seconds=120,
            recent_execution={
                "status": "ok",
                "stageTiming": {"monitorAndProjectionMs": 181000},
            },
        )

        self.assertEqual("runtime-protected", plan["mode"])
        self.assertEqual(1, plan["targetSymbolLimit"])
        self.assertTrue(plan["runtimeGuard"])

    def test_non_native_runtime_keeps_the_existing_static_batch_cap(self):
        plan = adaptive_reasoning_batch_plan(
            {"ontologyReasoningAdaptiveBatchEnabled": "1"},
            native_rule_execution=False,
            hard_target_symbol_limit=3,
            pending_request_count=20,
            pending_symbol_count=20,
            oldest_wait_seconds=600,
        )

        self.assertEqual("static", plan["mode"])
        self.assertEqual(3, plan["targetSymbolLimit"])
        self.assertFalse(plan["enabled"])

    def test_non_native_unbounded_symbol_setting_remains_unbounded(self):
        source = DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={})
        request = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["005930", "000660"],
            changed_count=2,
            fact_types=["MarketQuote"],
        )
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningTypeDbNativeRuleExecutionEnabled": "0",
                "ontologyReasoningMaxSymbolsPerRun": "0",
            },
        )

        plan = runner.reasoning_batch_plan([request])
        _batches, symbols, omitted = runner.request_symbol_batches(
            [request],
            max_symbols_override=plan["targetSymbolLimit"],
        )

        self.assertEqual("static-unbounded", plan["mode"])
        self.assertEqual(0, plan["targetSymbolLimit"])
        self.assertEqual({"005930", "000660"}, set(symbols))
        self.assertEqual(0, omitted)

    def test_native_runner_selects_multiple_symbols_in_one_coherent_queue_turn(self):
        source = DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={})
        request = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["005930", "000660", "035420"],
            changed_count=3,
            fact_types=["MarketQuote"],
        )
        request = DomainEvent(
            name=request.name,
            aggregate_id=request.aggregate_id,
            payload=request.payload,
            event_id="batch-request",
            occurred_at="2026-07-24T00:00:00Z",
        )
        cursor = MemoryCursor({
            "lastReasoningExecution": {
                "status": "ok",
                "stageTiming": {"monitorAndProjectionMs": 40000},
                "projectionRuntime": {"targetSymbolCount": 1},
            },
        })
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=cursor,
            monitor_runner_factory=lambda: None,
            now_provider=lambda: datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc),
            settings={
                "ontologyReasoningTypeDbNativeRuleExecutionEnabled": "1",
                "ontologyReasoningMaxSymbolsPerRun": "3",
                "typedbNativeRuleTargetSymbolLimit": "3",
                "ontologyReasoningAdaptiveBatchEnabled": "1",
                "ontologyReasoningAdaptiveBatchSteadySymbols": "1",
                "ontologyReasoningAdaptiveBatchBurstSymbols": "3",
                "ontologyReasoningAdaptiveBatchPendingThreshold": "4",
                "ontologyReasoningAdaptiveBatchAgeSeconds": "60",
            },
        )

        plan = runner.reasoning_batch_plan([request])
        batches, symbols, omitted = runner.request_symbol_batches(
            [request],
            max_symbols_override=plan["targetSymbolLimit"],
        )

        self.assertEqual("queue-pressure", plan["mode"])
        self.assertEqual(3, plan["targetSymbolLimit"])
        self.assertEqual({"005930", "000660", "035420"}, set(batches[request.event_id]))
        self.assertEqual(set(["005930", "000660", "035420"]), set(symbols))
        self.assertEqual(0, omitted)

    def test_batch_runtime_evidence_ignores_a_later_cooldown_probe(self):
        cursor = MemoryCursor({
            "reasoningExecutionHistory": [{
                "status": "ok",
                "stageTiming": {"monitorAndProjectionMs": 120000},
                "projectionRuntime": {"targetSymbolCount": 1},
            }],
            "lastReasoningExecution": {
                "status": "cooldown",
                "durationMs": 1000,
            },
        })
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=cursor,
            monitor_runner_factory=lambda: None,
            settings={"ontologyReasoningTypeDbNativeRuleExecutionEnabled": "1"},
        )

        evidence = runner.batch_runtime_evidence()

        self.assertEqual("ok", evidence["status"])
        self.assertEqual(120000, evidence["stageTiming"]["monitorAndProjectionMs"])
        self.assertEqual("history", evidence["batchRuntimeEvidenceSource"])

    def test_projection_audit_retains_batch_policy_without_turning_it_into_a_rule(self):
        compact = compact_reasoning_request_context({
            "targetSymbols": ["005930", "000660"],
            "batchPlan": {
                "version": "adaptive-reasoning-batch-v2",
                "enabled": True,
                "mode": "queue-pressure",
                "targetSymbolLimit": 3,
                "hardTargetSymbolLimit": 3,
                "steadyTargetSymbolLimit": 1,
                "burstTargetSymbolLimit": 3,
                "pendingRequestCount": 8,
                "pendingSymbolCount": 5,
                "oldestWaitSeconds": 120,
                "backlogBurstEnabled": True,
                "backlogBurstAgeSeconds": 120,
                "backlogEscape": True,
                "runtimeGuard": False,
                "runtimeEstimateBasis": "projection-runtime",
                "runtimeEstimateBasisMs": 123000,
                "targetParallelism": 2,
                "estimatedFixedRuntimeMs": 105000,
                "estimatedIncrementalTargetRuntimeMs": 18000,
                "rampTargetSymbolLimit": 2,
                "reasonCodes": ["pending-request-threshold"],
            },
        })

        self.assertEqual("queue-pressure", compact["batchPlan"]["mode"])
        self.assertEqual(3, compact["batchPlan"]["targetSymbolLimit"])
        self.assertEqual("projection-runtime", compact["batchPlan"]["runtimeEstimateBasis"])
        self.assertEqual(105000, compact["batchPlan"]["estimatedFixedRuntimeMs"])
        self.assertEqual(18000, compact["batchPlan"]["estimatedIncrementalTargetRuntimeMs"])
        self.assertTrue(compact["batchPlan"]["backlogEscape"])
        self.assertEqual(["pending-request-threshold"], compact["batchPlan"]["reasonCodes"])


if __name__ == "__main__":
    unittest.main()
