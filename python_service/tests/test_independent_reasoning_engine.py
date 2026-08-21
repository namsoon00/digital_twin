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
                ])

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def connect(self):
                yield Connection()

        state = object.__new__(Store).live_queue_state("v2-production")

        self.assertEqual(3, state["effectivePendingCount"])
        self.assertEqual(2, state["queuedCount"])
        self.assertEqual(1, state["processingCount"])
        self.assertEqual("2026-08-18T00:00:00Z", state["oldestRequestAt"])

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

    def test_scoped_executor_reuses_verified_market_inference_within_batch(self):
        class Recorder:
            def __init__(self):
                self.contexts = []

            def record_snapshot(self, snapshot, target_symbols=None, reasoning_context=None):
                del target_symbols
                self.contexts.append(dict(reasoning_context or {}))
                return {
                    "status": "ok",
                    "inferenceBox": {
                        "nativeTypeDbReasoningCompleted": True,
                        "generationAligned": True,
                        "sourceAboxSnapshotId": "abox:" + str(snapshot.account_id),
                        "inferenceGenerationId": "generation:" + str(snapshot.account_id),
                        "relations": [],
                        "traces": [],
                    },
                }

        class SharedInference:
            def __init__(self):
                self.published = False

            def execution_reuse_proof(self, _context, _symbols, snapshot=None):
                del snapshot
                return {
                    "status": "ready" if self.published else "missing",
                    "reuseEligible": self.published,
                    "sharedMarketRuleIds": ["graph.market.recovery.v1"],
                }

            def publish_verified_results(self, *_args, **_kwargs):
                self.published = True
                return {"status": "ready", "sharedSymbolCount": 1}

        recorder = Recorder()
        executor = ScopedTypeDBInferenceExecutor(recorder, SharedInference())
        request = independent_reasoning_request(
            "ontology-v2-shadow",
            [source_event("NVDA", ["a-1", "a-2"])],
        )

        results = executor.execute(
            request,
            [
                SimpleNamespace(account_id="a-1", metadata={}),
                SimpleNamespace(account_id="a-2", metadata={}),
            ],
        )

        self.assertNotIn("sharedInferenceReuseProof", recorder.contexts[0])
        self.assertTrue(recorder.contexts[1]["sharedInferenceReuseProof"]["reuseEligible"])
        self.assertEqual("ready", results["a-2"]["sharedInferenceExecution"]["reuseProofStatus"])

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

    def test_company_subject_becomes_a_symbol_scope(self):
        event = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="company:TSLA",
            payload={"subjectKind": "COMPANY", "subjectId": "tsla"},
        )

        self.assertEqual(["TSLA"], reasoning_event_scope(event)["symbols"])

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

    def test_reasoning_event_shards_are_deterministic(self):
        event = source_event("NVDA")
        event.payload["affectedSymbols"] = ["NVDA", "TSLA", "AAPL"]

        first = shard_reasoning_event(event, 1)
        second = shard_reasoning_event(event, 1)

        self.assertEqual(
            [shard.to_dict() for shard in first],
            [shard.to_dict() for shard in second],
        )

    def test_queue_slot_is_stable_across_different_fact_families(self):
        price = source_event("NVDA")
        price.payload["factTypes"] = ["PriceObservation"]
        valuation = source_event("NVDA")
        valuation.payload["factTypes"] = ["ValuationObservation"]
        valuation = DomainEvent(
            **{**valuation.__dict__, "event_id": "event:valuation"}
        )

        self.assertNotEqual(
            independent_reasoning_request("v2", [price]).scope_id,
            independent_reasoning_request("v2", [valuation]).scope_id,
        )
        self.assertEqual(
            reasoning_queue_slot_key(price, "REALTIME"),
            reasoning_queue_slot_key(valuation, "REALTIME"),
        )

    def test_pending_changes_merge_onto_latest_verified_snapshot(self):
        price = source_event("NVDA")
        price.payload.update({
            "factTypes": ["PriceObservation"],
            "changedFieldsBySymbol": {"NVDA": ["price"]},
            "factRevisionsBySymbol": {"NVDA": "price:7"},
            "verifiedSourceSnapshot": {
                "snapshotId": "snapshot:7",
                "generatedAt": "2026-08-16T00:00:01Z",
            },
            "factChangeContract": {
                "version": "fact-change-contract-v3",
                "status": "ready",
                "factTypes": ["PriceObservation"],
                "scopeFamilies": ["market"],
                "scopeFamiliesBySymbol": {"NVDA": ["market"]},
                "dependencyKeys": ["market.price"],
                "dependencyKeysBySymbol": {"NVDA": ["market.price"]},
                "dependencyKeysComplete": True,
                "dependencyKeysCompleteBySymbol": {"NVDA": True},
                "unclassifiedFactTypes": [],
                "unclassifiedFactTypesBySymbol": {},
            },
        })
        valuation = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="company:NVDA",
            occurred_at="2026-08-16T00:02:00Z",
            event_id="event:valuation",
            payload={
                "accountIds": ["acct"],
                "affectedSymbols": ["NVDA"],
                "factTypes": ["ValuationObservation"],
                "sourceObservedAt": "2026-08-16T00:01:00Z",
                "changedFieldsBySymbol": {"NVDA": ["peRatio"]},
                "factRevisionsBySymbol": {"NVDA": "valuation:3"},
                "verifiedSourceSnapshot": {
                    "snapshotId": "snapshot:8",
                    "generatedAt": "2026-08-16T00:02:01Z",
                },
                "factChangeContract": {
                    "version": "fact-change-contract-v3",
                    "status": "ready",
                    "factTypes": ["ValuationObservation"],
                    "scopeFamilies": ["company-valuation"],
                    "scopeFamiliesBySymbol": {"NVDA": ["company-valuation"]},
                    "dependencyKeys": ["company.valuation"],
                    "dependencyKeysBySymbol": {"NVDA": ["company.valuation"]},
                    "dependencyKeysComplete": True,
                    "dependencyKeysCompleteBySymbol": {"NVDA": True},
                    "unclassifiedFactTypes": [],
                    "unclassifiedFactTypesBySymbol": {},
                },
            },
        )

        merged = merge_reasoning_events([price, valuation])
        request = independent_reasoning_request("v2", [merged])

        self.assertEqual("event:valuation", merged.event_id)
        self.assertEqual("snapshot:8", merged.payload["verifiedSourceSnapshot"]["snapshotId"])
        self.assertEqual(
            ["PriceObservation", "ValuationObservation"],
            merged.payload["factTypes"],
        )
        self.assertEqual(["peRatio", "price"], merged.payload["changedFieldsBySymbol"]["NVDA"])
        self.assertEqual(
            ["company-valuation", "market"],
            request.context["requestedScopeFamiliesBySymbol"]["NVDA"],
        )
        self.assertEqual(2, merged.payload["coalescedReasoningChanges"]["eventCount"])

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

    def test_symbol_event_uses_subscription_reverse_index_before_account_scan(self):
        accounts = [
            SimpleNamespace(account_id="nvidia", watchlist_symbols=[]),
            SimpleNamespace(account_id="tesla", watchlist_symbols=[]),
        ]
        reverse_index = SimpleNamespace(
            account_ids_for_symbols=lambda symbols: ["nvidia"] if list(symbols) == ["NVDA"] else []
        )
        assembler = IndependentReasoningInputAssembler(
            SimpleNamespace(load=lambda: accounts),
            snapshot_source=SimpleNamespace(),
            monitor_store=SimpleNamespace(previous={}),
            instrument_subscription_index=reverse_index,
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

    def test_active_delivery_uses_existing_notification_ai_handoff(self):
        recorder = FakeCycleRecorder()
        engine = V2ReasoningEngine(
            descriptor(),
            FakeAssembler(),
            FakeExecutor(),
            FakeCandidateBuilder(),
            cycle_recorder=recorder,
            delivery_authorized_provider=lambda: True,
        )

        result = engine.consume([source_event()])

        self.assertTrue(result["delivery_authorized"])
        self.assertEqual("notification-queue-enqueued", result["ai_handoff_status"])
        self.assertEqual(1, recorder.calls)

    def test_persisted_projection_result_excludes_the_full_scope_plan(self):
        compact = compact_projection_result({
            "configured": True,
            "saved": True,
            "status": "ok",
            "entityCount": 20,
            "relationCount": 30,
            "scopePlan": [{"large": "payload"}],
            "inferenceBox": {
                "nativeTypeDbReasoningCompleted": True,
                "generationAligned": True,
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "inference:1",
                "relations": [{"id": "relation:1"}],
                "traces": [{"id": "trace:1"}],
            },
        })

        self.assertNotIn("scopePlan", compact)
        self.assertEqual("abox:1", compact["sourceAboxSnapshotId"])
        self.assertEqual(1, compact["inferenceTraceCount"])

    def test_runner_batches_compatible_source_events_into_one_engine_turn(self):
        events = [source_event("NVDA", []), source_event("TSLA", [])]

        class Queue:
            def __init__(self):
                self.completed = []

            def claim(self, *_args, **_kwargs):
                return [
                    {"jobId": "job:" + str(index), "sourceEvent": event.to_dict()}
                    for index, event in enumerate(events)
                ]

            def complete(self, job_id, result):
                self.completed.append((job_id, result["request_id"]))

            def defer(self, *_args):
                raise AssertionError("compatible jobs must not be deferred")

            def summary(self, _deployment_id):
                return {"pendingCount": 0}

        class Engine:
            def __init__(self):
                self.calls = []

            def descriptor(self):
                return descriptor()

            def consume(self, source_events):
                self.calls.append(list(source_events))
                return {
                    "request_id": "request:batch",
                    "status": "ok",
                    "retryable": False,
                }

            def health(self):
                return {"status": "ready", "monitorRunnerUsed": False}

        class Registry:
            def __init__(self):
                self.health = {"lastError": "stale delivery error"}

            def get(self, _deployment_id):
                return {"health": dict(self.health)}

            def update_health(self, _deployment_id, health):
                self.health = dict(health)

        queue = Queue()
        engine = Engine()
        registry = Registry()
        runner = IndependentReasoningJobRunner(
            queue,
            engine,
            registry,
            settings={
                "reasoningEngineV2BatchSize": "6",
                "reasoningEngineV2RealtimeBatchSize": "2",
            },
        )

        result = runner.run_once()

        self.assertEqual(2, result["processedCount"])
        self.assertEqual(1, len(engine.calls))
        self.assertEqual(2, len(engine.calls[0]))
        self.assertEqual(2, len(queue.completed))
        self.assertNotIn("lastError", registry.health)

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

    def test_runner_defers_jobs_beyond_native_unique_symbol_limit(self):
        events = [
            source_event("NVDA", []),
            source_event("TSLA", []),
            source_event("AAPL", []),
        ]

        class Queue:
            def __init__(self):
                self.completed = []
                self.deferred = []

            def claim(self, *_args, **_kwargs):
                return [
                    {"jobId": "job:" + str(index), "sourceEvent": event.to_dict()}
                    for index, event in enumerate(events)
                ]

            def complete(self, job_id, result):
                self.completed.append((job_id, result["request_id"]))

            def defer(self, job_id, reason, delay):
                self.deferred.append((job_id, reason, delay))

            def summary(self, _deployment_id):
                return {"pendingCount": len(self.deferred)}

        class Engine:
            def __init__(self):
                self.calls = []

            def descriptor(self):
                return descriptor()

            def consume(self, source_events):
                self.calls.append(list(source_events))
                return {
                    "request_id": "request:native-bounded",
                    "status": "ok",
                    "retryable": False,
                }

            def health(self):
                return {"status": "ready", "monitorRunnerUsed": False}

        class Registry:
            def get(self, _deployment_id):
                return {"health": {}}

            def update_health(self, _deployment_id, _health):
                return None

        queue = Queue()
        engine = Engine()
        runner = IndependentReasoningJobRunner(
            queue,
            engine,
            Registry(),
            settings={
                "reasoningEngineV2RealtimeBatchSize": "3",
                "typedbNativeRuleTargetSymbolLimit": "2",
            },
        )

        result = runner.run_once()

        self.assertEqual(2, result["processedCount"])
        self.assertEqual(2, len(engine.calls[0]))
        self.assertEqual(2, len(queue.completed))
        self.assertEqual("job:2", queue.deferred[0][0])
        self.assertIn("target-symbol limit", queue.deferred[0][1])
        self.assertEqual(1, result["result"]["capacity_deferred_job_count"])

    def test_runner_never_completes_a_job_omitted_from_evaluated_symbols(self):
        events = [source_event("NVDA", []), source_event("TSLA", [])]

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
        runner = IndependentReasoningJobRunner(queue, Engine(), Registry())

        result = runner.run_once()

        self.assertEqual(["job:0"], queue.completed)
        self.assertEqual("job:1", queue.superseded[0][0])
        self.assertIn("TSLA", queue.superseded[0][1])
        self.assertEqual(1, result["result"]["completed_job_count"])
        self.assertEqual(1, result["result"]["coverage_excluded_job_count"])

    def test_runner_reshards_one_oversized_job_before_engine_execution(self):
        event = source_event("NVDA", [])
        event.payload["affectedSymbols"] = ["NVDA", "TSLA", "AAPL", "MSFT", "META"]

        class Queue:
            def __init__(self):
                self.resharded = []

            def claim(self, *_args, **_kwargs):
                return [{"jobId": "job:wide", "sourceEvent": event.to_dict()}]

            def reshard_claimed_job(self, job_id, source, limit, worker_id=""):
                self.resharded.append((job_id, source, limit, worker_id))
                return {"status": "resharded", "shardCount": 3}

            def summary(self, _deployment_id):
                return {"pendingCount": 3}

        class Engine:
            def descriptor(self):
                return descriptor()

            def consume(self, _events):
                raise AssertionError("an oversized job must never reach TypeDB")

        queue = Queue()
        runner = IndependentReasoningJobRunner(
            queue,
            Engine(),
            SimpleNamespace(),
            settings={"typedbNativeRuleTargetSymbolLimit": "2"},
            worker_id="worker:test",
        )

        result = runner.run_once()

        self.assertEqual("resharded", result["status"])
        self.assertEqual(0, result["processedCount"])
        self.assertEqual(1, result["reshardedJobCount"])
        self.assertEqual(2, queue.resharded[0][2])
        self.assertEqual("worker:test", queue.resharded[0][3])

    def test_runner_claims_only_the_realtime_lane_batch_limit(self):
        class Queue:
            def __init__(self):
                self.claim_call = {}
                self.recovery_call = {}

            def recover_dead_local_leases(self, deployment_id, current_worker_id=""):
                self.recovery_call = {
                    "deploymentId": deployment_id,
                    "workerId": current_worker_id,
                }
                return {"status": "recovered", "recoveredCount": 2}

            def next_lane(self, _deployment_id):
                return "REALTIME"

            def claim(self, deployment_id, worker_id, limit, lease_seconds, reasoning_lane=""):
                self.claim_call = {
                    "deploymentId": deployment_id,
                    "workerId": worker_id,
                    "limit": limit,
                    "leaseSeconds": lease_seconds,
                    "reasoningLane": reasoning_lane,
                }
                return []

            def summary(self, _deployment_id):
                return {"pendingCount": 0}

        class Engine:
            def descriptor(self):
                return descriptor()

            def release_identity(self):
                return {}

        queue = Queue()
        runner = IndependentReasoningJobRunner(
            queue,
            Engine(),
            SimpleNamespace(),
            settings={
                "reasoningEngineV2BatchSize": "6",
                "reasoningEngineV2RealtimeBatchSize": "1",
            },
        )

        result = runner.run_once()

        self.assertEqual("idle", result["status"])
        self.assertEqual(2, result["leaseRecovery"]["recoveredCount"])
        self.assertEqual("ontology-v2-shadow", queue.recovery_call["deploymentId"])
        self.assertEqual(1, queue.claim_call["limit"])
        self.assertEqual("REALTIME", queue.claim_call["reasoningLane"])

    def test_runner_supersedes_permanently_unrestorable_source_packet(self):
        class Queue:
            def __init__(self):
                self.superseded = []

            def claim(self, *_args, **_kwargs):
                return [{"jobId": "job:old", "sourceEvent": source_event().to_dict()}]

            def supersede(self, job_id, reason):
                self.superseded.append((job_id, reason))

            def defer(self, *_args):
                raise AssertionError("permanent input loss must not be retried")

            def complete(self, *_args, **_kwargs):
                raise AssertionError("rejected input must not be completed")

            def summary(self, _deployment_id):
                return {"pendingCount": 0}

        class Engine:
            def descriptor(self):
                return descriptor()

            def consume(self, _events):
                return {
                    "request_id": "request:old",
                    "status": "rejected",
                    "retryable": False,
                    "reason": "The immutable source packet is no longer available.",
                }

            def health(self):
                return {"status": "degraded", "monitorRunnerUsed": False}

        class Registry:
            def get(self, _deployment_id):
                return {"health": {}}

            def update_health(self, _deployment_id, _health):
                return None

        queue = Queue()
        runner = IndependentReasoningJobRunner(queue, Engine(), Registry())

        result = runner.run_once()

        self.assertEqual("superseded", result["status"])
        self.assertEqual("job:old", queue.superseded[0][0])
        self.assertIn("immutable source packet", queue.superseded[0][1])


if __name__ == "__main__":
    unittest.main()
