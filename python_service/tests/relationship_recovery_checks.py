"""Source-clock and native-proof regressions within the existing alert suite."""

from copy import deepcopy
from datetime import datetime, timezone
from contextlib import contextmanager, ExitStack
import hashlib
import json
from pathlib import Path
import sys


@contextmanager
def legacy_observation_quality():
    """Replay historical input contracts with their original quality adapter."""
    from unittest.mock import patch
    from digital_twin.modules.reasoning.domain import ontology_observation_quality as quality
    fixture = json.loads((Path(__file__).parent / "fixtures/observation_quality_v1.json").read_text())
    if hashlib.sha256(fixture["source"].encode()).hexdigest() != fixture["sha256"]:
        raise AssertionError("Frozen quote-quality source hash mismatch")
    namespace = dict(vars(quality))
    exec(compile(fixture["source"], "observation_quality_v1", "exec"), namespace)
    originals = {id(getattr(quality, name)): namespace[name] for name in ("position_observation_profiles", "observation_profile")}
    with ExitStack() as stack:
        for name, module in list(sys.modules.items()):
            if not name.startswith("digital_twin.") or module is None:
                continue
            for attribute, value in list(vars(module).items()):
                replacement = originals.get(id(value))
                if replacement is not None:
                    stack.enter_context(patch.object(module, attribute, replacement))
        yield


def assert_live_quote_source_clock(test):
    from digital_twin.modules.market_data.domain.market_data import normalize_position
    from digital_twin.modules.reasoning.domain.ontology_observation_quality import position_observation_profiles, observation_profile

    checked = "2026-10-19T01:11:00Z"
    for provider_status in ("realtime", "live", "fresh", "ok", "available", "current"):
        with test.subTest(provider_status=provider_status):
            position = normalize_position({
                "symbol": "TEST", "market": "KR", "currency": "KRW", "currentPrice": 100,
                "dataQuality": "actual", "quoteSource": "Fixture WebSocket",
                "sourceAsOf": "2026-10-19T01:10:00Z", "sourceFetchedAt": "2026-10-19T01:10:00Z",
                "freshnessStatus": provider_status, "freshnessReason": "신선도 기준 통과",
            })
            original = deepcopy(position.to_dict())
            fresh = position_observation_profiles(position, {"asOf": checked})["quote"]
            test.assertEqual("fresh", fresh["freshnessStatus"])
            test.assertEqual(provider_status, fresh["providerStatus"])
            test.assertTrue(fresh["judgementEvidenceUsable"])
            test.assertEqual(1, fresh["freshnessAgeMinutes"])
            for source, status in (("2026-10-19T00:50:00Z", "stale"),
                                   ("2026-10-19T01:12:00Z", "future"), ("invalid", "unknown"), ("", "unknown")):
                position.source_as_of = source
                position.source_fetched_at = checked
                profile = position_observation_profiles(position, {"asOf": checked})["quote"]
                test.assertEqual(status, profile["freshnessStatus"])
                test.assertFalse(profile["judgementEvidenceUsable"])
                test.assertNotEqual("신선도 기준 통과", profile["freshnessGateReason"])
            position.source_as_of, position.source_fetched_at = original["source_as_of"], original["source_fetched_at"]
            test.assertEqual(original, position.to_dict())
    record = {"status": "fresh", "sourceAsOf": "2026-10-19T01:10:00Z", "maxAgeMinutes": 10,
              "judgementEvidenceUsable": False}
    profile = observation_profile(record, {"status": "open"}, "quote", "KR",
                                  datetime(2026, 10, 19, 1, 11, tzinfo=timezone.utc))
    test.assertFalse(profile["judgementEvidenceUsable"], "source time does not waive an explicit quality denial")


def assert_native_notification_recovery(test, checks):
    from test_rulebox_decision_brief import management_only_fixture
    from digital_twin.modules.notifications.domain.management_observation import management_observation_assessment
    from digital_twin.modules.notifications.domain.relation_follow_up import delivery_snapshot
    from digital_twin.modules.decisions.contracts import decision_synthesis_from_relation_context

    ctx = management_only_fixture()
    rel = ctx["ontologyRelationContext"]
    # This is the actual production projection shape: the native trace keeps
    # the claim, while compact OntologyRuleMatch rows do not have that field.
    for row in [*rel["matchedRules"], *rel["activeRules"], *rel["graphStoreInference"]["relations"]]:
        row.pop("claimContract", None)
    original = deepcopy(ctx)
    result, queue, ai, _ = checks.engine_run(context=ctx)
    test.assertEqual(1, len(result.candidate_events))
    test.assertTrue(queue.admission.accepted, queue.job.last_error)
    test.assertEqual("typedb-queued", result.insight_dispatch_status)
    test.assertEqual(original, ctx)
    ai.enqueue.assert_not_called()
    checks.assert_policy_only_candidate_survives_worker_gates_and_records_transport_receipt(context=ctx)
    baseline = delivery_snapshot(queue.job.context["relationChangeEvidence"], "delivered-original", queue.job.account_id)
    baseline.update(receiptJobId="delivered-original", deliveredAt="2026-10-02T01:11:00Z")
    _, repeated, _, _ = checks.engine_run(context=ctx, baseline=baseline)
    test.assertFalse(repeated.admission.accepted, "repair must retain receipt-backed repeat suppression")

    for variant, reason_code in (("missing", "management-notification-purpose-missing"),
                                 ("invalid", "management-notification-purpose-invalid"),
                                 ("conflict", "management-notification-purpose-conflict"),
                                 ("scope", "management-notification-proof-scope-mismatch")):
        with test.subTest(variant=variant):
            damaged = deepcopy(ctx)
            native = damaged["ontologyRelationContext"]["graphStoreInference"]["traces"][0]
            if variant == "missing":
                native.pop("claimContract")
            elif variant == "invalid":
                native["claimContract"] = {"claimType": "market-hypothesis"}
            elif variant == "scope":
                native["worldId"] = "portfolio:other"
            else:
                projected = damaged["ontologyRelationContext"]["matchedRules"][0]
                projected["claimContract"] = deepcopy(native["claimContract"])
                projected["claimContract"]["statement"] += " different frozen claim"
            synthesis = decision_synthesis_from_relation_context(damaged["accountId"], damaged["ontologyRelationContext"])
            assessment = management_observation_assessment({**damaged, "v2DecisionSynthesis": synthesis.to_dict()})
            test.assertEqual(reason_code, assessment["reasonCode"])
            failed, jobs, ai, orchestrator = checks.engine_run(context=damaged)
            test.assertEqual(0, len(failed.candidate_events))
            test.assertEqual({}, jobs.jobs)
            test.assertEqual("invalid-notification-contract", failed.insight_dispatch_status)
            test.assertEqual("notification-contract-invalid", failed.reason_code)
            test.assertEqual(1, failed.insight_dispatch_routes["INVALID"])
            from digital_twin.modules.reasoning.application.independent_reasoning_engine import V2ReasoningEngine
            from test_independent_reasoning_engine import descriptor
            engine = V2ReasoningEngine(descriptor(), None, None, None)
            engine.last_result = failed
            test.assertEqual("degraded", engine.health()["status"])
            from digital_twin.modules.reasoning.domain.reasoning_delivery_diagnostics import reasoning_delivery_diagnostics
            diagnosis = reasoning_delivery_diagnostics({"queues": {"delivery": {"pendingCount": 0}},
                "deliveryDeployment": {"lastRun": {"status": "ok", "traceComplete": True,
                    "candidateEventCount": 0, "dispatchOutcomes": list(failed.insight_dispatch_outcomes)}}})
            codes = [row["code"] for row in diagnosis["checks"]]
            test.assertIn("last-run-notification-contract-invalid", codes)
            test.assertNotIn("last-run-no-candidate", codes)
            subject = orchestrator.subject_cases.for_batch(failed.reasoning_case_id)[0]
            test.assertEqual("BLOCKED", subject.stage)
            test.assertEqual("INVALID", subject.inference_dispatch_decision.route)
            test.assertEqual(reason_code, subject.delivery_reason_code)
            test.assertEqual("failed", subject.delivery_state)
            test.assertIsNone(subject.publication)
            test.assertIsNone(subject.final_decision)
            test.assertFalse(orchestrator.record_notification_contract_failures(
                failed.reasoning_case_id, [subject.inference_dispatch_decision.details]))
            ai.enqueue.assert_not_called()
    assert_native_match_assembly(test, checks)


def assert_native_match_assembly(test, checks):
    """Exercise the real InferenceBox readers and compact match assembler."""
    from unittest.mock import patch
    from test_rulebox_decision_brief import management_only_fixture
    from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
    from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
    from digital_twin.modules.reasoning.domain.ontology_inference_materializer import materialize_rule_inference
    from digital_twin.modules.reasoning.domain.ontology_inference_context import relation_context_from_inferencebox
    from digital_twin.modules.portfolio.contracts import Position, PortfolioSummary
    from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_relation_payload, inferencebox_trace_payload
    from digital_twin.infrastructure import typedb_ontology as api

    ctx = management_only_fixture()
    prior = ctx["ontologyRelationContext"]
    properties = {**prior["facts"], "source": "holding", "sourceAsOf": "2026-10-02T01:10:00Z",
                  "freshnessStatus": "fresh", "freshnessAgeMinutes": 0, "maxAgeMinutes": 10,
                  "judgementEvidenceUsable": True, "symbol": "MSTR"}
    subject = OntologyEntity("stock:MSTR", "Fixture", "stock", properties)
    graph = PortfolioOntology("fixture:source-clock-policy", entities=[subject])
    rule = next(r for r in default_graph_inference_rules() if r.rule_id == "graph.position.concentration.guard.v1")
    materialize_rule_inference(graph, rule, subject, {"matchedConditions": [{"conditionId": c.condition_id,
        "matchedByTypeDB": True, "operator": c.operator, "value": c.value} for c in rule.conditions]})
    with patch.object(api, "runtime_settings", return_value={}):
        repository = api.TypeDBOntologyGraphRepository("fixture.invalid", retry_count=0)
    box = {**prior["graphStoreInference"], "status": "ok", "graphStore": "typedb",
           "nativeTypeDbReasoningUsed": True, "generationAligned": True,
           **{key: prior[key] for key in ("worldId", "sourceAboxSnapshotId", "inferenceGenerationId")},
           "inferenceGenerationAt": "2026-10-02T01:10:00Z",
           "relations": [inferencebox_relation_payload(r) for r in repository.rows_for_relations(graph)],
           "traces": [inferencebox_trace_payload(r) for r in repository.rows_for_entities(graph) if r.get("kind") == "inference-trace"]}
    position = Position("MSTR", "Fixture", market="US", currency="USD", quantity=203,
                        current_price=139.26, average_price=143.86, market_value_krw=78450,
                        source_as_of="2026-10-02T01:10:00Z", source_fetched_at="2026-10-02T01:10:00Z")
    relation = relation_context_from_inferencebox(position, PortfolioSummary(100000,78450,21550,[],[],78.45), box,
        source="holding", account_id=ctx["accountId"], account_context={"investmentStrategy": {"profile": "aggressive"}})
    test.assertTrue(relation)
    test.assertTrue(any(t.get("claimContract") for t in relation["graphStoreInference"]["traces"]))
    test.assertTrue(all(not m.get("claimContract") for m in relation["matchedRules"]))
    ctx["ontologyRelationContext"] = relation
    result, queue, _, _ = checks.engine_run(context=ctx)
    test.assertEqual(1, len(result.candidate_events))
    test.assertTrue(queue.admission.accepted, queue.job.last_error)
