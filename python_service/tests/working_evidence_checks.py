"""Shared regression scenarios invoked by the curated retrieval suite."""
import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from digital_twin.modules.reasoning.public import ObservationEvidenceReader
from digital_twin.modules.reasoning.contracts import EvidenceContractError
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence, byte_size
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.observation_wait import retry_key, disposition, unchanged_wait
from test_ai_directed_retrieval import session, SUBJECT, request
from test_ai_control import AIControlTests


def decision(action, ids=(), requests=(), missing=()):
    return {"action": action, "reason": "질문에 필요한 근거와 한계를 확인했습니다.",
            "selectedFactIds": list(ids), "requests": list(requests), "missingEvidence": list(missing)}


def check_working_evidence(test):
    captured, source = session()
    source.candidates.return_value = [source.candidates.return_value[0],
        *[{"id": "large-"+str(i), "kind": "evidence:financial-fact", "symbol": "TEST", "body": str(i)*25000} for i in range(3)]]
    captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
    packet = {**captured.packet(), "priorDeliveryContext": "x"*40000}
    saved = []
    def save(env):
        validate_execution_input(env); saved.append(env); return "working-"+str(len(saved))
    def model(env, _):
        ctx = env["retrievalContext"]
        test.assertIn("remainingBytes", ctx["authorBudget"])
        if not ctx["trace"]:
            return decision("read", requests=[request()])
        if len(ctx["trace"]) == 1:
            cursor = ctx["trace"][0]["reads"][0]["result"]["nextCursor"]
            return decision("read", requests=[request(cursor=cursor)])
        ids = [x["factId"] for x in ctx["knownFacts"] if x["kind"] != "stock"]
        test.assertEqual(2, len(ids))
        return decision("finish", [ids[-1]])
    final, history, memory, trace = retrieve_working_evidence(captured, packet, [], [], model, save, 262144)
    test.assertEqual("ready", final["retrieval"]["status"])
    test.assertEqual(2, sum(len(r["result"].get("facts", [])) for s in trace for r in s["reads"]))
    test.assertEqual(1, len([x for x in final["facts"] if x["kind"] != "stock"]))
    test.assertTrue(final["workingEvidence"]["excludedReadFacts"])
    test.assertLess(byte_size(final), 96000)
    validate_execution_input(freeze_execution_input(final, history, memory, retrieval_trace=trace))
    from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
    from test_ai_control import PLAN
    excluded = final["workingEvidence"]["excludedReadFacts"][0]["id"]
    with test.assertRaises(ValueError):
        validate_plan({**PLAN, "evidenceIds": [excluded]}, final)
    # Unknown IDs never gain citation authority, and the corrected request
    # stays inside the same bounded audit/lease protocol.
    calls = Mock(side_effect=[decision("finish", ["foreign:invented"]), decision("read", requests=[request()]), decision("defer", missing=["필요한 비교 보고서가 없습니다."])])
    result = retrieve_working_evidence(captured, packet, [], [], calls, save, 262144)
    test.assertEqual("deferred", result[0]["retrieval"]["status"])
    test.assertEqual("invalid-request", result[3][0]["status"])
    validate_execution_input(freeze_execution_input(result[0], result[1], result[2], retrieval_trace=result[3]))

    source.candidates.return_value = [source.candidates.return_value[0],
        {"id": "warning", "kind": "data-quality", "symbol": "TEST", "judgementEvidenceUsable": False},
        {"id": "flow", "kind": "flow-metric", "symbol": "TEST", "flow": -3}]
    warning_session = ObservationEvidenceReader(source).capture_session(SUBJECT)
    flow_id = SUBJECT["worldId"]+":flow"
    result = retrieve_working_evidence(warning_session, warning_session.packet(), [], [], Mock(side_effect=[
        decision("read", requests=[request("quality"), request("flow")]), decision("finish", [flow_id])]), save, 262144)
    test.assertTrue(result[0]["workingEvidence"]["requiredQualityFactIds"])
    test.assertTrue(any(x.get("judgementEvidenceUsable") is False for x in result[0]["facts"]))

    # Metric selection preserves exact associated provenance and leaves the
    # captured full record unchanged. A mutated view cannot enter a packet.
    report = {"id": "report", "kind": "company-financial-state", "symbol": "TEST", "periodEnd": "2025-12-31",
        "frequency": "annual", "historicalReport": True, "revenue": 100, "operatingIncome": 20,
        "reportedValues": {"revenue": 100, "operatingIncome": 20},
        "metricUnits": {"revenue": "USD", "operatingIncome": "USD"},
        "metricProvenance": {"revenue": {"source": "filing", "basis": "consolidated", "body": "a"*12000},
                             "operatingIncome": {"source": "filing", "basis": "consolidated", "body": "b"*12000}}}
    source.candidates.return_value = [source.candidates.return_value[0], report]
    captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
    index = captured.report_index()[0]; fid = index["factId"]
    view = captured.read_report(fid, ["revenue"])["facts"][0]
    test.assertEqual(report["metricProvenance"]["revenue"], view["metricProvenance"]["revenue"])
    test.assertNotIn("operatingIncome", view)
    test.assertEqual(["operatingIncome"], view["evidenceView"]["omittedMetrics"])
    test.assertEqual(20, captured.read("company")["facts"][0]["operatingIncome"])
    changed = copy.deepcopy(view); changed["revenue"] = 999
    with test.assertRaises(EvidenceContractError):
        captured.select([fid], views={fid: changed})
    result = retrieve_working_evidence(captured, captured.packet(), [], [], Mock(side_effect=[
        decision("read", requests=[{"tool": "read_report", "factId": fid, "metrics": ["revenue"]}]), decision("finish", [fid])]), save, 262144)
    test.assertEqual("ready", result[0]["retrieval"]["status"])
    test.assertNotIn("operatingIncome", next(x for x in result[0]["facts"] if x["id"] == fid))
    validate_execution_input(freeze_execution_input(result[0], result[1], result[2], retrieval_trace=result[3]))
    service, store, planner = AIControlTests().runner(evidence=Mock(return_value=captured),
        read_planner=Mock(return_value=decision("defer", missing=["공식 비교 기간의 원문이 필요합니다."])))
    test.assertEqual("awaiting-evidence", service.run_once()["status"])
    planner.assert_not_called()
    saved_result = store.complete.call_args.args[1]
    test.assertNotIn("summary", saved_result)
    test.assertEqual("data-wait", saved_result["processingOutcome"]["status"])
    test.assertTrue(saved_result["processingOutcome"]["requirements"])
    service, store, planner = AIControlTests().runner(evidence=Mock(return_value=captured),
        read_planner=Mock(), read_round_budget=lambda: 0)
    test.assertEqual("awaiting-evidence", service.run_once()["status"])
    test.assertEqual("retrieval-blocked", store.complete.call_args.args[1]["processingOutcome"]["status"])
    planner.assert_not_called(); service.read_planner.assert_not_called()
    service, store, planner = AIControlTests().runner(evidence=Mock(return_value=captured))
    key = retry_key(captured.packet(), [])
    state = disposition(captured.packet(), key, "output-invalid", ["조건 누락"])
    store.last_processing_outcome.return_value = state
    test.assertEqual("unchanged-wait", service.run_once()["status"])
    planner.assert_not_called()
    test.assertTrue(unchanged_wait(state, key))
    test.assertFalse(unchanged_wait(state, "changed"))
    test.assertFalse(unchanged_wait(state, key, datetime.now(timezone.utc)+timedelta(hours=4)))
    test.assertFalse(unchanged_wait({**state, "version": "old-policy"}, key))
