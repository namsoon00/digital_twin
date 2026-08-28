import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from digital_twin.application.independent_reasoning_engine import (
    IndependentReasoningInputAssembler,
    IndependentReasoningJobRunner,
    ScopedTypeDBInferenceExecutor,
    V2ReasoningEngine,
    compact_projection_result,
)
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.independent_reasoning import (
    independent_reasoning_request,
    merge_reasoning_events,
    reasoning_event_scope,
    reasoning_queue_slot_key,
    shard_reasoning_event,
)
from digital_twin.domain.fact_changes import fact_change_contract
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
)
from digital_twin.domain.repositories import MonitoringCycleRecordResult
from digital_twin.infrastructure.mysql_versioned_runtime import (
    MySQLReasoningEngineJobStore,
    reasoning_worker_process_owner,
)
from digital_twin.infrastructure.mysql_monitoring_stores import (
    MySQLMarketObservationReasoningAnchorStore,
)


def source_event(symbol="NVDA", account_ids=None):
    selected_accounts = ["acct"] if account_ids is None else list(account_ids)
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="market-observation:" + symbol,
        occurred_at="2026-08-16T00:00:00Z",
        event_id="event:" + symbol,
        payload={
            "accountIds": selected_accounts,
            "affectedSymbols": [symbol],
            "factTypes": ["PRICE_OBSERVATION"],
            "sourceObservedAt": "2026-08-16T00:00:00Z",
            "workClass": "MARKET",
        },
    )


def descriptor():
    return ReasoningEngineDescriptor(
        engine_family="ontology-investment-brain",
        engine_version="v2",
        deployment_id="ontology-v2-shadow",
        status="shadow",
        graph_store_binding="typedb-native",
        time_series_backend_id="questdb-shadow",
        release_bundle=EngineReleaseBundle(
            "tbox-v1",
            "rulebox-v1",
            "prompt-v1",
            "features-v1",
        ),
        capabilities={"independentExecution": True},
    )


class FakeAssembler:
    def assemble(self, request):
        del request
        return {
            "status": "ready",
            "preflight": {"ready": True},
            "snapshots": [SimpleNamespace(account_id="acct", metadata={})],
            "previousByAccount": {"acct": {}},
        }


class FakeExecutor:
    def execute(self, request, snapshots):
        del request, snapshots
        return {
            "acct": {
                "status": "ok",
                "inferenceBox": {
                    "nativeTypeDbReasoningCompleted": True,
                    "generationAligned": True,
                    "sourceAboxSnapshotId": "abox:1",
                    "inferenceGenerationId": "inference:1",
                    "relations": [{"id": "relation:1"}],
                    "traces": [{"id": "trace:1"}],
                },
            }
        }


class FakeCandidateBuilder:
    def build(self, request, snapshots, previous_by_account, projection_results, force=False):
        del request, snapshots, previous_by_account, projection_results, force
        event = AlertEvent(
            "acct",
            "Test",
            "observe",
            "portfolioOntologySignal",
            "NVDA",
            "NVIDIA insight",
            ["Graph-backed evidence"],
            symbol="NVDA",
        )
        return {"detected": [event], "ready": [event]}


class FakeCycleRecorder:
    def __init__(self):
        self.calls = 0

    def record_cycle(self, account_ids, snapshots, events, **kwargs):
        del account_ids, snapshots, kwargs
        self.calls += 1
        return MonitoringCycleRecordResult(
            delivered=False,
            queued=len(events),
            reason="queued",
            delivered_events=list(events),
        )


class IndependentReasoningEngineTests(unittest.TestCase):
    def test_semantic_change_set_preserves_bitemporal_source_boundary(self):
        contract = fact_change_contract(
            ["MarketQuote"],
            {"NVDA": ["MarketQuote"]},
            {"NVDA": ["currentPrice", "volume"]},
        )
        event = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="market-observation:NVDA",
            occurred_at="2026-08-16T00:00:02Z",
            event_id="event:semantic-nvda",
            payload={
                "accountIds": ["acct"],
                "affectedSymbols": ["NVDA"],
                "factTypes": ["MarketQuote"],
                "factTypesBySymbol": {"NVDA": ["MarketQuote"]},
                "changedFieldsBySymbol": {"NVDA": ["currentPrice", "volume"]},
                "factRevisionsBySymbol": {"NVDA": "market-revision:42"},
                "sourceObservedAt": "2026-08-16T00:00:00Z",
                "factChangeContract": contract,
            },
        )

        first = independent_reasoning_request("ontology-v2", [event])
        second = independent_reasoning_request("ontology-v2", [event])
        change = first.context["semanticChangeSet"]

        self.assertEqual(change["changeSetId"], second.context["semanticChangeSet"]["changeSetId"])
        self.assertEqual("2026-08-16T00:00:00Z", change["observedAt"])
        self.assertEqual("2026-08-16T00:00:02Z", change["requestedAt"])
        self.assertTrue(change["authoritativeFactBoundary"])
        self.assertTrue(change["authoritativeDependencyBoundary"])
        self.assertEqual("NVDA", change["factSlices"][0]["subjectId"])
        self.assertEqual(
            "market-revision:42",
            change["factSlices"][0]["revisionVector"]["revisions"]["market-observation"],
        )

    def test_incremental_current_state_executor_skips_shared_premise_critical_path(self):
        class Recorder:
            def __init__(self):
                self.prepare_calls = 0
                self.context = {}

            @staticmethod
            def incremental_current_state_reasoning_enabled():
                return True

            @staticmethod
            def world_partitioned_reasoning_enabled():
                return False

            def prepare_shared_premises(self, *_args, **_kwargs):
                self.prepare_calls += 1
                raise AssertionError("SharedPremise must not run in realtime one-pass mode")

            def record_snapshot(self, _snapshot, **kwargs):
                self.context = dict(kwargs.get("reasoning_context") or {})
                return {
                    "status": "ok",
                    "inferenceBox": {
                        "nativeTypeDbReasoningCompleted": True,
                        "generationAligned": True,
                        "sourceAboxSnapshotId": "abox:one-pass",
                        "inferenceGenerationId": "inference:one-pass",
                    },
                }

        recorder = Recorder()
        executor = ScopedTypeDBInferenceExecutor(
            recorder,
            settings={"ontologyStoreLogicalShardCount": "4"},
        )
        request = independent_reasoning_request(
            "ontology-v2-production",
            [source_event("NVDA", ["acct"])],
        )

        result = executor.execute(
            request,
            [SimpleNamespace(account_id="acct", metadata={})],
        )["acct"]

        self.assertEqual(0, recorder.prepare_calls)
        self.assertEqual(
            "incremental-current-state-one-pass-v1",
            recorder.context["reasoningExecutionMode"],
        )
        self.assertFalse(result["reasoningExecution"]["sharedPremiseCriticalPath"])
        self.assertEqual(1, result["reasoningExecution"]["typedbProjectionCount"])
        self.assertEqual(4, result["reasoningExecution"]["storeRoute"]["shard_count"])

    def test_market_anchor_reconciliation_follows_a_completed_coalesced_job(self):
        pending_event_id = "event:old-market"

        class Connection:
            def execute(self, sql, _params=()):
                if "FROM market_observation_reasoning_anchors" in sql:
                    return SimpleNamespace(fetchall=lambda: [{
                        "account_id": "acct",
                        "symbol": "MSTR",
                        "pending_event_id": pending_event_id,
                        "pending_at": "2026-08-27T00:00:00Z",
                    }])
                if "SELECT scope_key, created_at" in sql:
                    return SimpleNamespace(fetchone=lambda: {
                        "scope_key": "reasoning-slot:mstr",
                        "created_at": "2026-08-27T00:00:01Z",
                    })
                return SimpleNamespace(fetchall=lambda: [{
                    "job_id": "job:survivor",
                    "source_event_id": "event:new-market",
                    "request_json": json.dumps({
                        "sourceEvent": {
                            "event_id": "event:new-market",
                            "payload": {
                                "coalescedReasoningChanges": {
                                    "sourceEventIds": [pending_event_id, "event:new-market"],
                                },
                            },
                        },
                    }),
                    "result_json": json.dumps({"evaluated_symbols": ["MSTR"]}),
                }])

        class Store(MySQLMarketObservationReasoningAnchorStore):
            def __init__(self):
                self.completed = []

            @contextmanager
            def connect(self):
                yield Connection()

            def complete(self, event_ids, account_ids=None, symbols=None):
                del account_ids, symbols
                self.completed = list(event_ids)
                return {"status": "completed", "completedCount": len(self.completed)}

        store = Store()

        result = store.reconcile_completed_reasoning_jobs("ontology-v2-production-r75")

        self.assertEqual([pending_event_id], store.completed)
        self.assertEqual(1, result["completedCount"])
        self.assertEqual(["job:survivor"], result["reasoningJobIds"])

    def test_market_anchor_reconciliation_uses_later_verified_snapshot_when_source_job_expired(self):
        pending_event_id = "event:expired-market"

        class Connection:
            def execute(self, sql, _params=()):
                if "FROM market_observation_reasoning_anchors" in sql:
                    return SimpleNamespace(fetchall=lambda: [{
                        "account_id": "acct",
                        "symbol": "MSTR",
                        "pending_event_id": pending_event_id,
                        "pending_at": "2026-08-27T00:00:00Z",
                    }])
                if "SELECT scope_key, created_at" in sql:
                    return SimpleNamespace(fetchone=lambda: {})
                if "JSON_EXTRACT(result_json" in sql:
                    return SimpleNamespace(fetchall=lambda: [{
                        "job_id": "job:later-verified",
                        "source_snapshot_at": "2026-08-27T01:00:00Z",
                        "evaluated_symbols_json": json.dumps(["MSTR"]),
                        "account_ids_json": json.dumps(["acct"]),
                    }])
                if "job_status IN" in sql:
                    return SimpleNamespace(fetchall=lambda: [])
                raise AssertionError(sql)

        class Store(MySQLMarketObservationReasoningAnchorStore):
            def __init__(self):
                self.completed = []

            @contextmanager
            def connect(self):
                yield Connection()

            def complete(self, event_ids, account_ids=None, symbols=None):
                del account_ids, symbols
                self.completed = list(event_ids)
                return {"status": "completed", "completedCount": len(self.completed)}

            def release_pending(self, event_ids):
                return {"status": "released", "releasedCount": 0, "eventIds": list(event_ids)}

        store = Store()

        result = store.reconcile_completed_reasoning_jobs("ontology-v2-production-r75")

        self.assertEqual([pending_event_id], store.completed)
        self.assertEqual(1, result["completedCount"])
        self.assertEqual(["job:later-verified"], result["reasoningJobIds"])
        self.assertEqual(
            ["job:later-verified"],
            result["boundaryVerifiedReasoningJobIds"],
        )

    def test_market_anchor_reconciliation_rejects_older_or_wrong_account_snapshot(self):
        pending_event_id = "event:pending-market"

        class Connection:
            def execute(self, sql, _params=()):
                if "FROM market_observation_reasoning_anchors" in sql:
                    return SimpleNamespace(fetchall=lambda: [{
                        "account_id": "acct",
                        "symbol": "MSTR",
                        "pending_event_id": pending_event_id,
                        "pending_at": "2026-08-27T02:00:00Z",
                    }])
                if "SELECT scope_key, created_at" in sql:
                    return SimpleNamespace(fetchone=lambda: {})
                if "JSON_EXTRACT(result_json" in sql:
                    return SimpleNamespace(fetchall=lambda: [
                        {
                            "job_id": "job:older",
                            "source_snapshot_at": "2026-08-27T01:00:00Z",
                            "evaluated_symbols_json": json.dumps(["MSTR"]),
                            "account_ids_json": json.dumps(["acct"]),
                        },
                        {
                            "job_id": "job:wrong-account",
                            "source_snapshot_at": "2026-08-27T03:00:00Z",
                            "evaluated_symbols_json": json.dumps(["MSTR"]),
                        "account_ids_json": json.dumps(["other"]),
                        },
                    ])
                if "job_status IN" in sql:
                    return SimpleNamespace(fetchall=lambda: [])
                raise AssertionError(sql)

        class Store(MySQLMarketObservationReasoningAnchorStore):
            def __init__(self):
                self.completed = []

            @contextmanager
            def connect(self):
                yield Connection()

            def complete(self, event_ids, account_ids=None, symbols=None):
                del account_ids, symbols
                self.completed = list(event_ids)
                return {"status": "completed", "completedCount": len(self.completed)}

            def release_pending(self, event_ids):
                return {"status": "released", "releasedCount": 0, "eventIds": list(event_ids)}

        store = Store()

        result = store.reconcile_completed_reasoning_jobs("ontology-v2-production-r75")

        self.assertEqual([], store.completed)
        self.assertEqual(0, result["completedCount"])
        self.assertEqual([], result["boundaryVerifiedReasoningJobIds"])

    def test_market_anchor_reconciliation_releases_stale_orphan_for_retry(self):
        pending_event_id = "event:orphan-market"

        class Connection:
            def execute(self, sql, _params=()):
                if "FROM market_observation_reasoning_anchors" in sql:
                    return SimpleNamespace(fetchall=lambda: [{
                        "account_id": "acct",
                        "symbol": "SKHY",
                        "pending_event_id": pending_event_id,
                        "pending_at": "2026-08-20T00:00:00Z",
                    }])
                if "SELECT scope_key, created_at" in sql:
                    return SimpleNamespace(fetchone=lambda: {})
                if "JSON_EXTRACT(result_json" in sql or "job_status IN" in sql:
                    return SimpleNamespace(fetchall=lambda: [])
                raise AssertionError(sql)

        class Store(MySQLMarketObservationReasoningAnchorStore):
            def __init__(self):
                self.runtime_settings = {
                    "marketObservationReasoningPendingTimeoutSeconds": 3600,
                }
                self.released = []

            @contextmanager
            def connect(self):
                yield Connection()

            def complete(self, event_ids, account_ids=None, symbols=None):
                del account_ids, symbols
                return {"status": "completed", "completedCount": len(list(event_ids))}

            def release_pending(self, event_ids):
                self.released = list(event_ids)
                return {"status": "released", "releasedCount": len(self.released)}

        store = Store()

        result = store.reconcile_completed_reasoning_jobs("ontology-v2-production-r75")

        self.assertEqual([pending_event_id], store.released)
        self.assertEqual(1, result["releasedCount"])
        self.assertEqual([pending_event_id], result["releasedEventIds"])

    def test_market_anchor_reconciliation_keeps_stale_orphan_with_active_coalesced_job(self):
        pending_event_id = "event:active-market"

        class Connection:
            def execute(self, sql, _params=()):
                if "FROM market_observation_reasoning_anchors" in sql:
                    return SimpleNamespace(fetchall=lambda: [{
                        "account_id": "acct",
                        "symbol": "SKHY",
                        "pending_event_id": pending_event_id,
                        "pending_at": "2026-08-20T00:00:00Z",
                    }])
                if "SELECT scope_key, created_at" in sql:
                    return SimpleNamespace(fetchone=lambda: {})
                if "JSON_EXTRACT(result_json" in sql:
                    return SimpleNamespace(fetchall=lambda: [])
                if "job_status IN" in sql:
                    return SimpleNamespace(fetchall=lambda: [{
                        "source_event_id": "event:survivor",
                        "request_json": json.dumps({
                            "sourceEvent": {
                                "eventId": "event:survivor",
                                "payload": {
                                    "coalescedReasoningChanges": {
                                        "sourceEventIds": [pending_event_id],
                                    },
                                },
                            },
                        }),
                    }])
                raise AssertionError(sql)

        class Store(MySQLMarketObservationReasoningAnchorStore):
            def __init__(self):
                self.runtime_settings = {
                    "marketObservationReasoningPendingTimeoutSeconds": 3600,
                }
                self.released = []

            @contextmanager
            def connect(self):
                yield Connection()

            def complete(self, event_ids, account_ids=None, symbols=None):
                del account_ids, symbols
                return {"status": "completed", "completedCount": len(list(event_ids))}

            def release_pending(self, event_ids):
                self.released = list(event_ids)
                return {"status": "released", "releasedCount": len(self.released)}

        store = Store()

        result = store.reconcile_completed_reasoning_jobs("ontology-v2-production-r75")

        self.assertEqual([], store.released)
        self.assertEqual(0, result["releasedCount"])

    def test_v2_executor_observes_lifecycle_after_projection_is_attached(self):
        class Recorder:
            @staticmethod
            def world_partitioned_reasoning_enabled():
                return False

            @staticmethod
            def record_snapshot(_snapshot, **_kwargs):
                return {
                    "status": "ok",
                    "inferenceBox": {
                        "status": "ok",
                        "nativeTypeDbReasoningUsed": True,
                        "generationAligned": True,
                        "inferenceGenerationId": "generation:feedback:1",
                    },
                }

        class Observer:
            calls = 0

            def observe_snapshot(self, snapshot):
                self.calls += 1
                projection = snapshot.metadata["ontology"]["projection"]
                return {
                    "status": "ok",
                    "generationId": projection["inferenceBox"]["inferenceGenerationId"],
                }

        observer = Observer()
        executor = ScopedTypeDBInferenceExecutor(
            Recorder(),
            post_inference_observer=observer,
        )
        request = independent_reasoning_request(
            "ontology-v2-shadow",
            [source_event("NVDA", ["acct"])],
        )

        result = executor.execute(
            request,
            [SimpleNamespace(account_id="acct", metadata={})],
        )["acct"]

        self.assertEqual(1, observer.calls)
        self.assertEqual("ok", result["hypothesisLifecycle"]["status"])
        self.assertEqual(
            "generation:feedback:1",
            result["hypothesisLifecycle"]["generationId"],
        )

    def test_partitioned_executor_reports_exact_evaluated_symbol_coverage(self):
        class Recorder:
            @staticmethod
            def world_partitioned_reasoning_enabled():
                return True

            @staticmethod
            def prepare_shared_premises(
                _snapshot,
                target_symbols=None,
                reasoning_context=None,
                progress_callback=None,
            ):
                del reasoning_context, progress_callback
                return {
                    "status": "ready",
                    "ready": True,
                    "worldId": "premise:shared:global",
                    "requestedSymbols": list(target_symbols or []),
                    "evaluatedSymbols": ["NVDA"],
                    "notEvaluatedSymbols": ["TSLA"],
                    "targetCoverageComplete": False,
                    "symbols": {"NVDA": {"snapshotId": "generation:shared"}},
                    "sharedRuleIds": [],
                    "relations": [],
                    "traces": [],
                    "generationVector": {},
                }

            @staticmethod
            def record_snapshot(_snapshot, **_kwargs):
                return {
                    "status": "ok",
                    "inferenceBox": {
                        "nativeTypeDbReasoningCompleted": True,
                        "generationAligned": True,
                        "sourceAboxSnapshotId": "abox:acct",
                        "inferenceGenerationId": "generation:acct",
                        "relations": [],
                        "traces": [],
                    },
                }

        executor = ScopedTypeDBInferenceExecutor(Recorder())
        request = independent_reasoning_request(
            "ontology-v2-shadow",
            [source_event("NVDA", ["acct"]), source_event("TSLA", ["acct"])],
        )

        result = executor.execute(
            request,
            [SimpleNamespace(account_id="acct", metadata={})],
        )["acct"]["sharedInferenceExecution"]

        self.assertEqual(["NVDA", "TSLA"], result["requestedSymbols"])
        self.assertEqual(["NVDA"], result["evaluatedSymbols"])
        self.assertEqual(["TSLA"], result["notEvaluatedSymbols"])
        self.assertFalse(result["targetCoverageComplete"])

    def test_reasoning_worker_process_owner_rejects_ambiguous_lease_ids(self):
        self.assertEqual(("worker-host", 1234), reasoning_worker_process_owner("worker-host:1234:v2-deadbeef"))
        self.assertEqual(("", 0), reasoning_worker_process_owner("worker:test"))
        self.assertEqual(("", 0), reasoning_worker_process_owner("worker-host:not-a-pid:v2"))

    def test_mysql_live_queue_state_counts_only_typedb_writer_jobs(self):
        class Connection:
            def execute(self, _sql, _params=()):
                return SimpleNamespace(fetchall=lambda: [
                    {"job_status": "queued", "row_count": 2, "oldest": "2026-08-18T00:00:00Z"},
                    {"job_status": "processing", "row_count": 1, "oldest": "2026-08-18T00:01:00Z"},
                    {
                        "job_status": "awaiting_world_projection",
                        "row_count": 4,
                        "oldest": "2026-08-17T00:00:00Z",
                    },
                ])

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def connect(self):
                yield Connection()

        state = object.__new__(Store).live_queue_state("v2-production")

        self.assertEqual(3, state["effectivePendingCount"])
        self.assertEqual(2, state["queuedCount"])
        self.assertEqual(1, state["processingCount"])
        self.assertEqual(4, state["awaitingWorldProjectionCount"])
        self.assertEqual("2026-08-18T00:00:00Z", state["oldestRequestAt"])
        self.assertEqual(
            "2026-08-17T00:00:00Z",
            state["oldestAwaitingWorldProjectionAt"],
        )

    def test_mysql_stale_observation_cleanup_uses_nested_fact_contract(self):
        class Connection:
            def __init__(self):
                self.update_params = ()

            def execute(self, sql, params=()):
                if sql.lstrip().startswith("SELECT"):
                    return SimpleNamespace(fetchall=lambda: [
                        {
                            "job_id": "job:market",
                            "request_json": json.dumps({
                                "request": {
                                    "fact_types": ["MarketQuote", "TechnicalIndicator"],
                                    "context": {"workClasses": []},
                                    "trigger": "market-snapshot",
                                },
                                "sourceEvent": {"payload": {}},
                            }),
                        },
                        {
                            "job_id": "job:portfolio",
                            "request_json": json.dumps({
                                "request": {
                                    "context": {
                                        "workClasses": ["PORTFOLIO"],
                                        "factTypes": ["PortfolioPosition"],
                                    },
                                },
                                "sourceEvent": {"payload": {}},
                            }),
                        },
                        {
                            "job_id": "job:calendar",
                            "request_json": json.dumps({
                                "request": {
                                    "fact_types": ["InvestmentCalendarEvent"],
                                    "trigger": "investment-calendar-update",
                                },
                                "sourceEvent": {"payload": {}},
                            }),
                        },
                    ])
                self.update_params = tuple(params)
                return SimpleNamespace(rowcount=2)

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def transaction(self):
                yield connection

        connection = Connection()
        store = object.__new__(Store)
        store.runtime_settings = {}

        result = store.supersede_stale_observation_jobs(
            "ontology-v2-production-r29",
            maximum_age_seconds=900,
        )

        self.assertEqual(2, result["supersededCount"])
        self.assertEqual({"MARKET": 1, "PORTFOLIO": 1}, result["workClassCounts"])
        self.assertIn("job:market", connection.update_params)
        self.assertIn("job:portfolio", connection.update_params)
        self.assertNotIn("job:calendar", connection.update_params)

    def test_mysql_queue_recovers_only_confirmed_dead_local_worker_leases(self):
        class Connection:
            def __init__(self):
                self.update_params = ()

            def execute(self, sql, params=()):
                if sql.lstrip().startswith("SELECT"):
                    return SimpleNamespace(fetchall=lambda: [
                        {"job_id": "job:dead", "lease_owner": "worker-host:111:v2-old"},
                        {"job_id": "job:live", "lease_owner": "worker-host:222:v2-live"},
                        {"job_id": "job:remote", "lease_owner": "remote-host:333:v2-remote"},
                        {"job_id": "job:current", "lease_owner": "worker-host:444:v2-current"},
                    ])
                self.update_params = tuple(params)
                return SimpleNamespace(rowcount=1)

        connection = Connection()

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def transaction(self):
                yield connection

        store = object.__new__(Store)
        result = store.recover_dead_local_leases(
            "ontology-v2-shadow",
            current_worker_id="worker-host:444:v2-current",
            host_name="worker-host",
            process_alive=lambda process_id: process_id != 111,
        )

        self.assertEqual("recovered", result["status"])
        self.assertEqual(["job:dead"], result["recoveredJobIds"])
        self.assertEqual("job:dead", connection.update_params[-1])

    def test_mysql_queue_supersedes_dead_claim_from_obsolete_deployment(self):
        class Connection:
            def __init__(self):
                self.updates = []

            def execute(self, sql, params=()):
                if "FROM reasoning_engine_control" in sql:
                    return SimpleNamespace(fetchone=lambda: {
                        "active_deployment_id": "ontology-v2-current",
                        "delivery_deployment_id": "ontology-v2-current",
                        "candidate_deployment_id": "ontology-v1-active",
                    })
                if sql.lstrip().startswith("SELECT"):
                    return SimpleNamespace(fetchall=lambda: [{
                        "job_id": "job:obsolete",
                        "deployment_id": "ontology-v2-old",
                        "lease_owner": "worker-host:111:v2-old",
                    }])
                self.updates.append((sql, tuple(params)))
                return SimpleNamespace(rowcount=1)

        connection = Connection()

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def transaction(self):
                yield connection

        result = object.__new__(Store).recover_dead_local_leases(
            "ontology-v2-current",
            host_name="worker-host",
            process_alive=lambda _process_id: False,
        )

        self.assertEqual([], result["recoveredJobIds"])
        self.assertEqual(["job:obsolete"], result["supersededJobIds"])
        self.assertIn("job_status = 'superseded'", connection.updates[-1][0])

    def test_mysql_queue_claim_retries_deadlock_without_repeating_inference(self):
        class Connection:
            def execute(self, sql, _params=()):
                if sql.lstrip().startswith("SELECT *"):
                    return SimpleNamespace(fetchall=lambda: [{
                        "job_id": "job:retry-once",
                        "deployment_id": "ontology-v2-production",
                        "request_json": "{}",
                        "source_boundary_json": "[]",
                    }])
                return SimpleNamespace(rowcount=1)

        connection = Connection()

        class Store(MySQLReasoningEngineJobStore):
            def __init__(self):
                self.runtime_settings = {
                    "mysqlDeadlockRetryCount": "2",
                    "mysqlDeadlockRetryBaseMilliseconds": "1",
                    "mysqlDeadlockRetryMaxMilliseconds": "1",
                    "reasoningEngineV2RequireSourceBoundary": "0",
                }
                self.last_transaction_retry = {}
                self.attempts = 0

            @contextmanager
            def transaction(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError(1213, "Deadlock found when trying to get lock")
                yield connection

        store = Store()
        jobs = store.claim(
            "ontology-v2-production",
            "worker-host:111:v2-current",
        )

        self.assertEqual(["job:retry-once"], [job["jobId"] for job in jobs])
        self.assertEqual(2, store.attempts)
        self.assertTrue(store.last_transaction_retry["recovered"])
        self.assertEqual(1, store.last_transaction_retry["retryCount"])

    def test_mysql_queue_releases_exact_worker_claims_during_managed_shutdown(self):
        class Connection:
            def __init__(self):
                self.sql = ""
                self.params = ()

            def execute(self, sql, params=()):
                self.sql = sql
                self.params = tuple(params)
                return SimpleNamespace(rowcount=3)

        connection = Connection()

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def connect(self):
                yield connection

        result = object.__new__(Store).release_worker_leases(
            "ontology-v2-shadow",
            "worker-host:123:v2-current",
        )

        self.assertEqual("released", result["status"])
        self.assertEqual(3, result["releasedCount"])
        self.assertIn("job_status = 'processing'", connection.sql)
        self.assertEqual("ontology-v2-shadow", connection.params[-2])
        self.assertEqual("worker-host:123:v2-current", connection.params[-1])

    def test_mysql_queue_compacts_old_scopes_without_losing_fact_changes(self):
        old = source_event("NVDA")
        old.payload.update({
            "factTypes": ["PriceObservation"],
            "sourceObservedAt": "2026-08-16T00:00:00Z",
            "verifiedSourceSnapshot": {
                "snapshotId": "snapshot:1",
                "generatedAt": "2026-08-16T00:00:01Z",
            },
            "factChangeContract": {
                "status": "ready",
                "scopeFamilies": ["market"],
                "scopeFamiliesBySymbol": {"NVDA": ["market"]},
                "dependencyKeys": [],
                "dependencyKeysComplete": False,
            },
        })
        newest = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="company:NVDA",
            occurred_at="2026-08-16T00:02:00Z",
            event_id="event:newest",
            payload={
                "accountIds": ["acct"],
                "affectedSymbols": ["NVDA"],
                "factTypes": ["ValuationObservation"],
                "sourceObservedAt": "2026-08-16T00:01:00Z",
                "verifiedSourceSnapshot": {
                    "snapshotId": "snapshot:2",
                    "generatedAt": "2026-08-16T00:02:01Z",
                },
                "factChangeContract": {
                    "status": "ready",
                    "scopeFamilies": ["company-valuation"],
                    "scopeFamiliesBySymbol": {"NVDA": ["company-valuation"]},
                    "dependencyKeys": [],
                    "dependencyKeysComplete": False,
                },
            },
        )
        rows = [
            {
                "job_id": "job:old",
                "scope_key": "reasoning-scope:old",
                "source_snapshot_at": "2026-08-16T00:00:01Z",
                "reasoning_lane": "CONTEXT",
                "priority": 60,
                "created_at": "2026-08-16T00:00:02Z",
                "request_json": json.dumps({"sourceEvent": old.to_dict()}),
            },
            {
                "job_id": "job:new",
                "scope_key": "reasoning-scope:new",
                "source_snapshot_at": "2026-08-16T00:02:01Z",
                "reasoning_lane": "CONTEXT",
                "priority": 70,
                "created_at": "2026-08-16T00:02:02Z",
                "request_json": json.dumps({"sourceEvent": newest.to_dict()}),
            },
        ]

        class Connection:
            def __init__(self):
                self.survivor_update = ()

            def execute(self, sql, params=()):
                if sql.lstrip().startswith("SELECT"):
                    return SimpleNamespace(fetchall=lambda: rows)
                if "source_snapshot_id = %s" in sql:
                    self.survivor_update = tuple(params)
                return SimpleNamespace(rowcount=1)

        connection = Connection()

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def transaction(self):
                yield connection

        result = object.__new__(Store).compact_supersedable_backlog("ontology-v2-shadow")
        stored = json.loads(connection.survivor_update[6])

        self.assertEqual("compacted", result["status"])
        self.assertEqual(1, result["compactedCount"])
        self.assertEqual("snapshot:2", connection.survivor_update[0])
        self.assertEqual(
            ["PriceObservation", "ValuationObservation"],
            stored["sourceEvent"]["payload"]["factTypes"],
        )
        self.assertTrue(str(connection.survivor_update[4]).startswith("reasoning-slot:"))

    def test_scoped_executor_skips_typedb_for_exact_private_overlay_replay(self):
        class Recorder:
            def record_snapshot(self, *_args, **_kwargs):
                raise AssertionError("TypeDB projection must not run for an exact replay")

        class SharedInference:
            def reusable_portfolio_projection(self, _context, _symbols, snapshot):
                return {
                    "status": "ready",
                    "reuseEligible": True,
                    "projection": {
                        "status": "reused-shared-account-inference",
                        "inferenceBox": {
                            "nativeTypeDbReasoningCompleted": True,
                            "generationAligned": True,
                            "sourceAboxSnapshotId": "abox:" + snapshot.account_id,
                            "inferenceGenerationId": "generation:" + snapshot.account_id,
                            "relations": [],
                            "traces": [],
                        },
                    },
                }

        executor = ScopedTypeDBInferenceExecutor(Recorder(), SharedInference())
        request = independent_reasoning_request(
            "ontology-v2-shadow",
            [source_event("NVDA", ["a-1"])],
        )

        results = executor.execute(
            request,
            [SimpleNamespace(account_id="a-1", metadata={})],
        )

        self.assertEqual("reused-shared-account-inference", results["a-1"]["status"])
        self.assertTrue(
            results["a-1"]["sharedInferenceExecution"]["portfolioProjectionReused"]
        )

    def test_request_scope_is_deterministic_and_symbol_bounded(self):
        event = source_event()
        first = independent_reasoning_request("ontology-v2-shadow", [event])
        second = independent_reasoning_request("ontology-v2-shadow", [event])

        self.assertEqual(("acct",), first.account_ids)
        self.assertEqual(("NVDA",), first.symbols)
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertTrue(first.supersedable)

    def test_multi_symbol_source_is_sharded_without_losing_fact_boundaries(self):
        event = source_event("NVDA")
        symbols = ["NVDA", "TSLA", "AAPL", "MSFT", "META"]
        event.payload.update({
            "affectedSymbols": symbols,
            "changedFieldsBySymbol": {
                symbol: ["price", "volume"] for symbol in symbols
            },
            "factRevisionsBySymbol": {
                symbol: "revision:" + symbol for symbol in symbols
            },
            "verifiedSourceSnapshot": {
                "snapshotId": "snapshot:fixed",
                "generatedAt": "2026-08-16T00:00:00Z",
            },
            "factChangeContract": {
                "version": "fact-change-contract-v1",
                "status": "ready",
                "scopeFamilies": ["market"],
                "scopeFamiliesBySymbol": {
                    symbol: ["market"] for symbol in symbols
                },
                "unclassifiedFactTypes": [],
                "unclassifiedFactTypesBySymbol": {},
            },
        })

        shards = shard_reasoning_event(event, 2)

        self.assertEqual(3, len(shards))
        self.assertEqual(event.event_id, shards[0].event_id)
        self.assertEqual(
            [["AAPL", "META"], ["MSFT", "NVDA"], ["TSLA"]],
            [list(reasoning_event_scope(shard)["symbols"]) for shard in shards],
        )
        self.assertEqual(3, len({shard.event_id for shard in shards}))
        for shard in shards:
            scoped_symbols = set(reasoning_event_scope(shard)["symbols"])
            self.assertEqual(
                scoped_symbols,
                set(shard.payload["changedFieldsBySymbol"]),
            )
            self.assertEqual(
                scoped_symbols,
                set(shard.payload["factChangeContract"]["scopeFamiliesBySymbol"]),
            )
            self.assertEqual(
                "snapshot:fixed",
                shard.payload["verifiedSourceSnapshot"]["snapshotId"],
            )

    def test_request_preserves_the_authoritative_source_fact_boundary(self):
        event = source_event("NVDA")
        event.payload.update({
            "subjectChangedFields": ["published_at", "headline"],
            "factRevisionsBySymbol": {"NVDA": "news-revision:7"},
            "revisionVectorsBySymbol": {"NVDA": {"evidence": "7"}},
            "factChangeContract": {
                "version": "fact-change-contract-v1",
                "status": "ready",
                "scopeFamilies": ["evidence"],
                "scopeFamiliesBySymbol": {"NVDA": ["evidence"]},
                "unclassifiedFactTypes": [],
                "unclassifiedFactTypesBySymbol": {},
            },
        })

        request = independent_reasoning_request("ontology-v2-shadow", [event])

        self.assertTrue(request.context["eventFactBoundaryAuthoritative"])
        self.assertEqual(["evidence"], request.context["requestedScopeFamilies"])
        self.assertEqual(
            {"NVDA": ["evidence"]},
            request.context["requestedScopeFamiliesBySymbol"],
        )
        self.assertEqual(
            ["headline", "published_at"],
            request.context["changedFieldsBySymbol"]["NVDA"],
        )
        self.assertEqual("news-revision:7", request.context["factRevisionsBySymbol"]["NVDA"])

    def test_symbol_event_selects_only_affected_accounts(self):
        accounts = [
            SimpleNamespace(account_id="nvidia", watchlist_symbols=["NVDA"]),
            SimpleNamespace(account_id="tesla", watchlist_symbols=["TSLA"]),
        ]
        assembler = IndependentReasoningInputAssembler(
            SimpleNamespace(load=lambda: accounts),
            snapshot_source=SimpleNamespace(),
            monitor_store=SimpleNamespace(previous={}),
        )
        request = independent_reasoning_request(
            "ontology-v2-shadow",
            [source_event(account_ids=[])],
        )

        selected = assembler.selected_accounts(request)

        self.assertEqual(["nvidia"], [account.account_id for account in selected])

    def test_shadow_run_produces_trace_but_never_hands_off_delivery(self):
        recorder = FakeCycleRecorder()
        engine = V2ReasoningEngine(
            descriptor(),
            FakeAssembler(),
            FakeExecutor(),
            FakeCandidateBuilder(),
            cycle_recorder=recorder,
            delivery_authorized_provider=lambda: False,
        )

        result = engine.consume([source_event()])

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["trace_complete"])
        self.assertEqual(1, len(result["candidate_events"]))
        self.assertEqual("shadow-delivery-blocked", result["ai_handoff_status"])
        self.assertEqual(0, recorder.calls)
        self.assertFalse(engine.health()["monitorRunnerUsed"])

    def test_native_rule_failure_is_deferred_instead_of_completed_as_blocked(self):
        class RetryableNativeFailureExecutor:
            def execute(self, request, snapshots):
                del request, snapshots
                return {
                    "acct": {
                        "saved": True,
                        "status": "ok",
                        "nativeRuleFailure": {
                            "retryable": True,
                            "recommendedRetryAfterSeconds": 17,
                            "reason": "One native TypeDB subject was incomplete.",
                            "reasonCode": "typedbSubjectFanoutIncomplete",
                            "stage": "native-rule-query",
                            "ruleId": "graph.temporal.stale_observation.block.v1",
                        },
                        "inferenceBox": {
                            "status": "native-rule-failed",
                            "nativeTypeDbReasoningCompleted": False,
                            "generationAligned": False,
                        },
                    }
                }

        engine = V2ReasoningEngine(
            descriptor(),
            FakeAssembler(),
            RetryableNativeFailureExecutor(),
            FakeCandidateBuilder(),
            delivery_authorized_provider=lambda: False,
        )

        result = engine.consume([source_event()])

        self.assertEqual("deferred", result["status"])
        self.assertTrue(result["retryable"])
        self.assertEqual(17, result["retry_after_seconds"])
        self.assertIn("native TypeDB subject", result["reason"])
        projection = result["projection_results"]["acct"]
        self.assertTrue(projection["retryable"])
        self.assertEqual("typedbSubjectFanoutIncomplete", projection["failureReasonCode"])
        self.assertEqual(
            "graph.temporal.stale_observation.block.v1",
            projection["blockingRuleId"],
        )

    def test_persisted_projection_result_keeps_performance_contract_summary(self):
        compact = compact_projection_result({
            "configured": True,
            "saved": True,
            "status": "ok",
            "performanceAssessment": {
                "version": "ontology-performance-contract-v1",
                "status": "degraded",
                "withinBudget": False,
                "bottleneckStage": "nativeInferenceMs",
                "bottleneckRatio": 1.5,
                "violations": [{
                    "stage": "nativeInferenceMs",
                    "durationMs": 67500,
                    "budgetMs": 45000,
                    "ratio": 1.5,
                    "withinBudget": False,
                }],
                "stages": [{"large": "payload"}],
            },
        })

        performance = compact["performanceAssessment"]
        self.assertEqual("degraded", performance["status"])
        self.assertEqual("nativeInferenceMs", performance["bottleneckStage"])
        self.assertEqual(67500, performance["violations"][0]["durationMs"])
        self.assertNotIn("stages", performance)

    def test_job_lease_heartbeat_also_refreshes_worker_liveness(self):
        class Queue:
            def __init__(self):
                self.calls = []

            def heartbeat(self, job_ids, worker_id, lease_seconds, **_kwargs):
                self.calls.append((list(job_ids), worker_id, lease_seconds))
                return True

        class Registry:
            def __init__(self):
                self.health = {}

            def get(self, _deployment_id):
                return {"health": dict(self.health)}

            def update_health(self, _deployment_id, health):
                self.health = dict(health)

        class StopAfterOneInterval:
            def __init__(self):
                self.calls = 0

            def wait(self, _seconds):
                self.calls += 1
                return self.calls > 1

        class LeaseLost:
            def __init__(self):
                self.value = False

            def set(self):
                self.value = True

        queue = Queue()
        registry = Registry()
        lease_lost = LeaseLost()
        runner = IndependentReasoningJobRunner(
            queue,
            object(),
            registry,
            worker_id="delivery-worker",
            deployment_role="delivery",
        )

        runner.heartbeat_loop(
            ["job:1"],
            StopAfterOneInterval(),
            lease_lost,
            "ontology-v2-production",
        )

        self.assertEqual(1, len(queue.calls))
        self.assertFalse(lease_lost.value)
        self.assertEqual(
            "delivery-worker",
            registry.health["workerHeartbeats"]["delivery"]["workerId"],
        )

    def test_runner_does_not_claim_jobs_when_typedb_execution_guard_is_blocked(self):
        class Queue:
            def claim(self, *_args, **_kwargs):
                raise AssertionError("blocked V2 worker must not claim TypeDB work")

            def summary(self, _deployment_id):
                return {"pendingCount": 3}

        class Engine:
            def descriptor(self):
                return descriptor()

        class Registry:
            def __init__(self):
                self.health = {}

            def get(self, _deployment_id):
                return {"health": dict(self.health)}

            def update_health(self, _deployment_id, health):
                self.health = dict(health)

        registry = Registry()
        runner = IndependentReasoningJobRunner(
            Queue(),
            Engine(),
            registry,
            execution_guard=lambda: {
                "ready": False,
                "status": "rotation-required",
                "reason": "TypeDB rotation is active.",
            },
        )

        result = runner.run_once()

        self.assertEqual("deferred", result["status"])
        self.assertEqual(0, result["processedCount"])
        self.assertEqual("rotation-required", result["executionGuard"]["status"])
        self.assertEqual(3, result["queue"]["pendingCount"])
        self.assertEqual("deferred", registry.health["status"])

    def test_runner_never_completes_a_job_omitted_from_evaluated_symbols(self):
        events = [source_event("NVDA", []), source_event("TSLA", [])]
        anchor_completions = []

        class Queue:
            def __init__(self):
                self.completed = []
                self.superseded = []

            def claim(self, *_args, **_kwargs):
                return [
                    {"jobId": "job:" + str(index), "sourceEvent": event.to_dict()}
                    for index, event in enumerate(events)
                ]

            def complete(self, job_id, _result):
                self.completed.append(job_id)

            def supersede(self, job_id, reason):
                self.superseded.append((job_id, reason))

            def defer(self, *_args):
                return None

            @staticmethod
            def summary(_deployment_id):
                return {"pendingCount": 0}

        class Engine:
            @staticmethod
            def descriptor():
                return descriptor()

            @staticmethod
            def consume(_source_events):
                return {
                    "request_id": "request:coverage",
                    "status": "ok",
                    "retryable": False,
                    "evaluated_symbols": ["NVDA"],
                    "not_evaluated_symbols": ["TSLA"],
                }

            @staticmethod
            def health():
                return {"status": "ready", "monitorRunnerUsed": False}

        class Registry:
            @staticmethod
            def get(_deployment_id):
                return {"health": {}}

            @staticmethod
            def update_health(_deployment_id, _health):
                return None

        queue = Queue()
        runner = IndependentReasoningJobRunner(
            queue,
            Engine(),
            Registry(),
            market_observation_completion_recorder=lambda event_ids, **kwargs: (
                anchor_completions.append((list(event_ids), dict(kwargs)))
                or {"status": "completed", "completedCount": 1}
            ),
        )

        result = runner.run_once()

        self.assertEqual(["job:0"], queue.completed)
        self.assertEqual("job:1", queue.superseded[0][0])
        self.assertIn("TSLA", queue.superseded[0][1])
        self.assertEqual(1, result["result"]["completed_job_count"])
        self.assertEqual(1, result["result"]["coverage_excluded_job_count"])
        self.assertEqual(["event:NVDA"], anchor_completions[0][0])
        self.assertEqual(["NVDA"], anchor_completions[0][1]["symbols"])

    def test_runner_shutdown_releases_only_its_owned_jobs_once(self):
        class Queue:
            def __init__(self):
                self.calls = []

            def release_worker_leases(self, deployment_id, worker_id, reason):
                self.calls.append((deployment_id, worker_id, reason))
                return {"status": "released", "releasedCount": 2}

        class Engine:
            def descriptor(self):
                return descriptor()

        queue = Queue()
        runner = IndependentReasoningJobRunner(
            queue,
            Engine(),
            SimpleNamespace(),
            worker_id="worker-host:123:v2-current",
        )

        first = runner.shutdown()
        second = runner.shutdown()

        self.assertEqual(2, first["releasedCount"])
        self.assertEqual(first, second)
        self.assertEqual(1, len(queue.calls))

    def test_runner_applies_configured_stale_observation_age(self):
        class Queue:
            def __init__(self):
                self.maximum_age_seconds = 0

            def supersede_stale_observation_jobs(
                self,
                deployment_id,
                maximum_age_seconds,
            ):
                self.maximum_age_seconds = maximum_age_seconds
                return {
                    "status": "superseded",
                    "deploymentId": deployment_id,
                    "supersededCount": 2,
                }

        class Engine:
            @staticmethod
            def descriptor():
                return descriptor()

        queue = Queue()
        runner = IndependentReasoningJobRunner(
            queue,
            Engine(),
            SimpleNamespace(),
            settings={"reasoningEngineV2StaleObservationMaxAgeSeconds": "1200"},
        )

        result = runner.supersede_stale_observations("ontology-v2-shadow")

        self.assertEqual(2, result["supersededCount"])
        self.assertEqual(1200, queue.maximum_age_seconds)

    def test_runner_parks_shared_world_projection_failure_with_bounded_retry(self):
        class Queue:
            def __init__(self):
                self.waiting = []

            def claim(self, *_args, **_kwargs):
                return [{"jobId": "job:aapl", "sourceEvent": source_event("AAPL", []).to_dict()}]

            def await_world_projection(
                self,
                job_id,
                result,
                reason,
                retry_after_seconds,
                max_attempts,
            ):
                self.waiting.append({
                    "jobId": job_id,
                    "result": result,
                    "reason": reason,
                    "retryAfterSeconds": retry_after_seconds,
                    "maxAttempts": max_attempts,
                })
                return {"jobId": job_id, "terminal": False, "retryAfterSeconds": retry_after_seconds}

            def defer(self, *_args):
                raise AssertionError("SharedPremiseWorld waits must not enter the generic defer loop")

            @staticmethod
            def summary(_deployment_id):
                return {"pendingCount": 0, "awaitingWorldProjectionCount": 1}

        class Engine:
            @staticmethod
            def descriptor():
                return descriptor()

            @staticmethod
            def consume(_events):
                return {
                    "request_id": "request:aapl",
                    "status": "deferred",
                    "retryable": True,
                    "retry_after_seconds": 30,
                    "reason": "SharedPremiseWorld projection failed.",
                    "reason_code": "typedb-shared-world-projection-error",
                    "projection_results": {
                        "acct": {
                            "failureReasonCode": "typedb-shared-world-projection-error",
                            "failureStage": "shared-world-projection",
                        }
                    },
                }

            @staticmethod
            def health():
                return {"status": "degraded", "monitorRunnerUsed": False}

        class Registry:
            def __init__(self):
                self.health = {"lastError": "stale failure"}

            def get(self, _deployment_id):
                return {"health": dict(self.health)}

            def update_health(self, _deployment_id, health):
                self.health = dict(health)

        queue = Queue()
        registry = Registry()
        runner = IndependentReasoningJobRunner(
            queue,
            Engine(),
            registry,
            settings={"reasoningEngineV2WorldProjectionMaxAttempts": "4"},
        )

        result = runner.run_once()

        self.assertEqual("awaiting-world-projection", result["status"])
        self.assertEqual("job:aapl", queue.waiting[0]["jobId"])
        self.assertEqual(4, queue.waiting[0]["maxAttempts"])
        self.assertEqual("deferred", registry.health["status"])
        self.assertEqual("awaiting-world-projection", registry.health["dependencyStatus"])
        self.assertNotIn("lastError", registry.health)

    def test_runner_terminally_fails_non_retryable_projection_integrity_error(self):
        class Queue:
            def __init__(self):
                self.failed = []

            def claim(self, *_args, **_kwargs):
                return [{"jobId": "job:aapl", "sourceEvent": source_event("AAPL", []).to_dict()}]

            def fail(self, job_id, result, reason, reason_code):
                self.failed.append((job_id, result, reason, reason_code))

            def defer(self, *_args):
                raise AssertionError("a deterministic integrity failure must not be deferred")

            @staticmethod
            def summary(_deployment_id):
                return {"pendingCount": 0}

        class Engine:
            @staticmethod
            def descriptor():
                return descriptor()

            @staticmethod
            def consume(_events):
                return {
                    "request_id": "request:aapl",
                    "status": "blocked",
                    "retryable": False,
                    "reason": "Scoped ABox candidate verification failed for AAPL.",
                    "reason_code": "typedbCandidateVerificationError",
                }

            @staticmethod
            def health():
                return {"status": "degraded", "monitorRunnerUsed": False}

        class Registry:
            def __init__(self):
                self.health = {}

            def get(self, _deployment_id):
                return {"health": dict(self.health)}

            def update_health(self, _deployment_id, health):
                self.health = dict(health)

        queue = Queue()
        registry = Registry()
        result = IndependentReasoningJobRunner(queue, Engine(), registry).run_once()

        self.assertEqual("failed", result["status"])
        self.assertEqual("job:aapl", queue.failed[0][0])
        self.assertEqual("typedbCandidateVerificationError", queue.failed[0][3])
        self.assertEqual("blocked", registry.health["status"])


if __name__ == "__main__":
    unittest.main()
