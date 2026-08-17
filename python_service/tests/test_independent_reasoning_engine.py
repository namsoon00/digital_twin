import unittest
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
    reasoning_event_scope,
    shard_reasoning_event,
)
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
)
from digital_twin.domain.repositories import MonitoringCycleRecordResult


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
                "reasoningEngineV2BatchSize": "6",
                "reasoningEngineV2RealtimeBatchSize": "2",
            },
        )

        result = runner.run_once()

        self.assertEqual(2, result["processedCount"])
        self.assertEqual(1, len(engine.calls))
        self.assertEqual(2, len(engine.calls[0]))
        self.assertEqual(2, len(queue.completed))

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
