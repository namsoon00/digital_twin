"""Read-side and payload parity contracts for the extracted web adapters."""

import unittest
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.infrastructure.share_access import SHARE_ROLE_OWNER, SHARE_ROLE_VIEWER, ShareAccess
from digital_twin.infrastructure.web import common
from digital_twin.infrastructure.web.adapters import (
    accounts,
    brain,
    capital_flow,
    cases,
    configuration,
    console,
    flow_lens,
    notification_inbox,
    notification_presentation,
    notification_storage,
    ontology_catalog,
    portfolio,
    workspace,
)
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.infrastructure.web.composition import WebRoutes, build_api_router
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.read_models.infrastructure.flow_lens_read_model import FlowLensReadResult


class WebRouterPayloadTests(unittest.TestCase):
    def test_stale_cache_relabels_embedded_freshness_without_mutating_snapshot(self):
        for freshness in [{"status": "fresh", "source": "snapshot"}, "fresh", None]:
            source = {
                "payload": {"status": "ok", "dataFreshness": freshness, "rows": [{"id": "row-1"}]},
                "stale": True,
                "ageSeconds": 181,
                "refreshing": True,
                "lastSuccessAt": "2026-09-03T00:00:00Z",
                "lastError": "timeout",
            }
            before = deepcopy(source)
            cache = SimpleNamespace(get_or_refresh=Mock(return_value=source))
            loader = Mock(side_effect=AssertionError("cached response must not call loader itself"))
            result = cached_api_payload(cache, "snapshot-key", loader, force=True, blocking_first_load=False)
            self.assertEqual(before, source)
            self.assertEqual({"stale": True, "ageSeconds": 181, "refreshing": True, "lastSuccessAt": "2026-09-03T00:00:00Z", "lastError": "timeout"}, result["readCache"])
            self.assertEqual("stale", result["effectiveFreshness"]["status"])
            if freshness is not None:
                self.assertEqual("stale", result["dataFreshness"]["status"])
                self.assertEqual("fresh", result["dataFreshness"]["sourceStatus"])
            cache.get_or_refresh.assert_called_once_with("snapshot-key", loader, force=True, blocking_first_load=False)
            loader.assert_not_called()

    def test_empty_cache_warming_and_unavailable_payloads(self):
        for refreshing, status in [(True, "warming"), (False, "unavailable")]:
            cache = SimpleNamespace(get_or_refresh=Mock(return_value={"refreshing": refreshing, "lastError": "not ready"}))
            result = cached_api_payload(cache, "", lambda: {})
            self.assertEqual(status, result["status"])
            self.assertEqual("not ready", result["error"])
            self.assertFalse(result["readCache"]["stale"])
            self.assertNotIn("effectiveFreshness", result)
            self.assertEqual("default", cache.get_or_refresh.call_args.args[0])

    def test_read_settings_copies_source_and_disables_maintenance(self):
        source = {"database": "synthetic"}
        with patch.object(common, "runtime_settings", return_value=source) as settings:
            result = common.operational_read_settings()
        settings.assert_called_once_with(fast_operational_read=True)
        self.assertEqual({"database": "synthetic"}, source)
        self.assertEqual("1", result["_skipOperationalHistoryRetention"])
        self.assertEqual("1", result["_skipOperationalSchemaBootstrap"])
        self.assertEqual("1", result["_skipNotificationRuleDefaultsSeed"])

    def test_cache_first_readers_remain_nonblocking_and_do_not_construct_services(self):
        marker = {"status": "warming"}
        cases_to_check = [
            (console, console.console_portfolio_api_payload, ({"accountId": ["demo"]}, "positions"), console.PORTFOLIO_CONSOLE_READ_MODEL),
            (console, console.console_market_instruments_api_payload, ({},), console.MARKET_INSTRUMENTS_READ_MODEL),
            (console, console.console_market_evidence_api_payload, ({"limit": ["8"]},), console.MARKET_EVIDENCE_READ_MODEL),
            (brain, brain.investment_brain_hypothesis_workspace_api_payload, ({"view": ["summary"]},), brain.HYPOTHESIS_WORKSPACE_READ_MODEL),
            (ontology_catalog, ontology_catalog.ontology_catalog_api_payload, ("summary", {}), ontology_catalog.ONTOLOGY_CATALOG_SUMMARY_READ_MODEL),
        ]
        for module, reader, args, expected_cache in cases_to_check:
            with self.subTest(reader=reader.__name__), patch.object(module, "cached_api_payload", return_value=marker) as cached:
                self.assertIs(marker, reader(*args))
                self.assertIs(expected_cache, cached.call_args.args[0])
                self.assertFalse(cached.call_args.kwargs["blocking_first_load"])

    def test_flow_lens_compaction_preserves_immutable_source(self):
        snapshot = {
            "generatedAt": "2026-09-03T00:00:00Z",
            "toss": {"positions": [{"symbol": "AAPL", "quantity": 2, "privatePacket": {"raw": "demo"}}], "externalSignals": {"researchEvidence": [{"id": "evidence-1"}]}, "metadata": {"ontology": {"id": "graph-1"}}},
            "tossDecision": {"positions": [{"symbol": "AAPL", "decision": "REVIEW_ONLY", "privatePacket": "demo"}], "ontologyStrategy": {"entities": [{"id": "node-1"}], "generationId": "generation-1"}},
            "investmentAnalysis": {"reasoningCards": [{"id": "card-1"}], "generationId": "generation-1"},
        }
        before = deepcopy(snapshot)
        result = flow_lens.compact_flow_lens_payload(snapshot)
        self.assertEqual(before, snapshot)
        self.assertEqual([{"quantity": 2, "symbol": "AAPL"}], result["toss"]["positions"])
        self.assertEqual(1, result["toss"]["externalSignals"]["researchEvidenceCount"])
        self.assertEqual("generation-1", result["tossDecision"]["ontologyStrategy"]["generationId"])
        self.assertEqual(1, result["investmentAnalysis"]["reasoningCardsCount"])
        self.assertEqual("/api/flow-lens?detail=full", result["fullDetailPath"])

    def test_freshness_boundary_stays_strictly_greater_than_max_age(self):
        for age, expected in [(None, "unknown"), (30, "fresh"), (30.01, "stale")]:
            with patch.object(flow_lens, "age_minutes", return_value=age):
                result = flow_lens.flow_lens_data_freshness("source-clock", {"marketDataMaxAgeMinutes": "30"})
            self.assertEqual(expected, result["status"])
            self.assertEqual("source-clock", result["generatedAt"])
            self.assertEqual(30, result["maxAgeMinutes"])

    def test_flow_lens_status_read_keeps_snapshot_clock_and_skips_capital_flow(self):
        snapshot = {"generatedAt": "source-clock", "dataMode": "live", "tossDecision": {"generationId": "g1"}}
        original = deepcopy(snapshot)
        result = FlowLensReadResult(snapshot, "ready", True, "monitor-snapshot", "refresh-clock")
        model = SimpleNamespace(read=Mock(return_value=result))
        with patch.object(flow_lens, "flow_lens_read_model", return_value=model), patch.object(flow_lens, "runtime_settings", return_value={}), patch.object(flow_lens, "age_minutes", return_value=61), patch.object(flow_lens, "capital_flow_api_payload") as capital:
            payload = flow_lens.flow_lens_read_payload({"detail": ["freshness"], "refresh": ["1"], "watchlistSymbols": ["AAPL"]})
        self.assertEqual(original, snapshot)
        self.assertEqual({"generatedAt", "dataMode", "readModel", "dataFreshness"}, set(payload))
        self.assertEqual("source-clock", payload["generatedAt"])
        self.assertEqual("stale", payload["dataFreshness"]["status"])
        model.read.assert_called_once_with(mock=False, watchlist_symbols="AAPL", refresh=True)
        capital.assert_not_called()

    def test_bootstrap_shape_and_masked_account_payloads(self):
        stored = {"profile": {"ownerName": "demo"}, "memories": [], "items": [], "messages": [], "metadata": {"internal": True}}
        with patch.object(workspace, "read_store", return_value=stored):
            self.assertEqual({key: stored[key] for key in ["profile", "memories", "items", "messages"]}, workspace.snapshot_payload())
        masked = {"id": "account-1", "label": "demo", "clientSecret": ""}
        service = SimpleNamespace(list_masked=Mock(return_value=[masked]), save_payload=Mock(return_value=SimpleNamespace(masked=lambda: masked)), remove=Mock(return_value=True))
        with patch.object(accounts, "account_service", return_value=service):
            self.assertEqual({"accounts": [masked]}, accounts.service_accounts_payload())
            self.assertEqual({"account": masked}, accounts.save_account_payload({"label": "demo"}))
            self.assertEqual({"removed": True, "id": "account-1"}, accounts.remove_account_payload("account-1"))

    def test_settings_never_expose_synthetic_credentials_for_either_shared_role(self):
        secrets = {key: "synthetic-private-value" for key in ["tossClientSecret", "tossAccountSeq", "kisAppKey", "telegramBotToken", "fredApiKey", "typedbPassword", "mysqlPassword"]}
        prompt_release = SimpleNamespace(to_public_dict=lambda: {"releaseId": "fixture"})
        with patch.object(configuration, "runtime_settings", return_value={"appTheme": "dark", **secrets}), patch.object(configuration, "share_runtime_status_payload", return_value={}), patch.object(configuration, "runtime_identity", return_value={}), patch("digital_twin.modules.decisions.domain.notification_ai_prompt_release.active_notification_ai_prompt_release", return_value=prompt_release):
            for role, locked in [(SHARE_ROLE_OWNER, False), (SHARE_ROLE_VIEWER, True)]:
                payload = configuration.settings_status_payload(ShareAccess(role))
                self.assertEqual("dark", payload["settings"]["appTheme"])
                self.assertEqual(locked, payload["locked"])
                for key in secrets:
                    self.assertEqual("", payload["settings"][key])
                    self.assertTrue(payload["configured"][key])

    def test_notification_reads_reuse_joined_receipts_and_read_settings_once(self):
        job = NotificationJob(job_id="job-1", account_id="demo", account_label="demo", message_type="workHandoff", text="finished", status="done", context={"notificationReceipt": {"important": True, "readAt": "read-clock", "usefulness": "helpful"}})
        original = deepcopy(job.to_dict())
        store = SimpleNamespace(
            recent_list_page_with_summary=Mock(return_value=([job], 1, {"done": 1})),
            receipt_states=Mock(side_effect=AssertionError("receipt already joined")),
            inbox_summary=Mock(return_value={"total": 1, "unread": 0, "important": 1, "actionRequired": 0}),
        )
        with patch.object(notification_inbox, "operational_read_settings", return_value={"notificationProcessingStaleMinutes": "2"}) as settings, patch.object(notification_inbox, "notification_queue_store", return_value=store):
            result = notification_inbox.notification_jobs_payload({"limit": ["20"], "inbox": ["unexpected"]})
        self.assertEqual(1, settings.call_count)
        store.receipt_states.assert_not_called()
        self.assertEqual(original, job.to_dict())
        self.assertEqual("all", result["inbox"])
        self.assertEqual("helpful", result["jobs"][0]["usefulness"])
        self.assertTrue(result["jobs"][0]["important"])
        self.assertEqual("read-clock", result["jobs"][0]["readAt"])
        self.assertEqual({"done": 1}, result["summary"])

    def test_notification_queue_reader_sets_read_only_flags_without_mutating_settings(self):
        settings = {"notificationProcessingStaleMinutes": "2"}
        with patch.object(notification_storage.stores, "notification_job_store", return_value="store") as build:
            self.assertEqual("store", notification_storage.notification_queue_store(settings))
        self.assertEqual({"notificationProcessingStaleMinutes": "2"}, settings)
        self.assertEqual("1", build.call_args.args[0]["_skipOperationalSchemaBootstrap"])
        self.assertEqual("1", build.call_args.args[0]["_skipNotificationRuleDefaultsSeed"])

    def test_notification_cursor_roundtrip_and_malformed_fallback(self):
        job = SimpleNamespace(job_id="job-1", updated_at="2026-09-03T00:00:00Z", created_at="")
        cursor = notification_presentation.encode_notification_cursor(job)
        self.assertEqual({"jobId": job.job_id, "updatedAt": job.updated_at}, notification_presentation.decode_notification_cursor(cursor))
        self.assertEqual({"jobId": "", "updatedAt": ""}, notification_presentation.decode_notification_cursor("not-json"))

    def test_case_list_uses_public_read_model_and_never_loads_detail_only_stores(self):
        service = SimpleNamespace(list_cases=Mock(return_value={"status": "ok", "cases": []}))
        store_names = ["investment_decision_episode_store", "notification_job_store", "hypothesis_lifecycle_store", "symbol_universe_store", "subject_decision_case_store", "ai_inference_queue_store", "investment_reasoning_case_store"]
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(cases, "operational_read_settings", return_value={"readOnly": "1"}))
            constructor = stack.enter_context(patch.object(cases, "InvestmentCaseQueryService", return_value=service))
            for name in store_names:
                stack.enter_context(patch.object(cases.stores, name, return_value=name))
            for name in ["monitor_store", "research_evidence_store", "investment_domain_store"]:
                stack.enter_context(patch.object(cases.stores, name, side_effect=AssertionError("detail store on list read")))
            result = cases.investment_case_api_payload({"accountId": ["demo"], "symbol": [" aapl "], "limit": ["9999"], "audience": ["operator"]})
        self.assertEqual({"status": "ok", "cases": []}, result)
        service.list_cases.assert_called_once_with(account_id="demo", symbol="AAPL", limit=500, include_operator=True)
        self.assertIsNone(constructor.call_args.kwargs["monitor_store"])

    def test_capital_flow_keeps_position_clock_and_missing_positions_semantics(self):
        service = SimpleNamespace(summary=Mock(return_value={"contract": "capital-flow-summary-v1"}))
        with patch.object(capital_flow, "operational_read_settings", return_value={}), patch.object(capital_flow.stores, "market_time_series_store", return_value="store"), patch.object(capital_flow, "CapitalFlowService", return_value=service):
            snapshot = {"generatedAt": "source-clock", "toss": {"positions": []}}
            result = capital_flow.capital_flow_api_payload({"limit": ["invalid"]}, snapshot=snapshot)
            self.assertTrue(service.summary.call_args.kwargs["positions_available"])
            self.assertEqual("source-clock", service.summary.call_args.kwargs["position_snapshot_as_of"])
            self.assertEqual(10000, service.summary.call_args.kwargs["limit"])
            self.assertTrue(result["readOnly"])
            capital_flow.capital_flow_api_payload({})
            self.assertFalse(service.summary.call_args.kwargs["positions_available"])

    def test_trade_execution_errors_keep_public_payload_contract(self):
        service = SimpleNamespace(review_plan=Mock(side_effect=ValueError("invalid plan")), submit_plan=Mock(side_effect=ValueError("not approved")), record_fills=Mock(return_value={"status": "ok"}))
        with patch.object(portfolio, "build_trade_execution_service", return_value=service):
            self.assertEqual({"status": "error", "error": "invalid plan"}, portfolio.review_action_plan_payload("plan-1", "approved", {}))
            self.assertEqual({"status": "error", "error": "not approved"}, portfolio.execute_action_plan_payload("plan-1"))
            self.assertEqual({"status": "ok"}, portfolio.record_action_plan_fills_payload("plan-1", {"fills": "invalid"}))
        service.record_fills.assert_called_once_with("plan-1", [], "")

    def test_replay_route_retains_bounded_durable_queue_handoff(self):
        routes = WebRoutes()
        job = {"jobId": "replay-1", "status": "pending"}
        service = SimpleNamespace(enqueue=Mock(return_value=job))
        builder = Mock(return_value=service)
        routes = replace(routes, outcomes=replace(routes.outcomes, build_historical_replay_job_service=builder, operational_read_settings=lambda: {"readOnly": "1"}))
        send = Mock()
        request = SimpleNamespace(command="POST", read_json_body=lambda: {"accountId": "demo", "limit": 99999, "caseLimit": -2, "includeCases": "yes"}, ensure_writable=lambda _message: True, send_payload=send)
        build_api_router(routes).dispatch(request, "/api/investment-brain/decision-replay", {})
        builder.assert_called_once_with({"readOnly": "1"}, execution_enabled=False)
        service.enqueue.assert_called_once_with("decision", {"accountId": "demo", "symbol": "", "limit": 2000, "includeCases": True, "caseLimit": 1, "replayMode": "strict-replay"})
        send.assert_called_once_with(202, {"status": "queued", "job": job})


if __name__ == "__main__":
    unittest.main()
