"""Crypto research uses native facts, scoped memory and real later observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology, OntologyEntity
from digital_twin.modules.reasoning.domain.ontology_external_abox import add_external_signal_concepts
from digital_twin.modules.reasoning.application.observation_evidence import ObservationEvidenceReader
from digital_twin.modules.reasoning.domain.observation_evidence import (
    CRYPTO_EVIDENCE_PROFILE, EvidenceContractError, evidence_change_identity, validate_evidence_packet,
)
from digital_twin.modules.ai_orchestration.domain.publication import quote_from, publication_block, QUOTE_SELECTION_VERSION
from digital_twin.modules.ai_orchestration.domain.insight_contract import comparable_refs
from digital_twin.modules.ai_orchestration.domain.insight_memory import receipt_facts
from digital_twin.modules.ai_orchestration.domain.crypto_observation import configured_crypto_assets
from digital_twin.modules.outcomes.domain.observation_followup import prepare_observation_conditions, evaluate_observation_conditions
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SUBJECT = {"accountId": "crypto-test", "symbol": "ETH", "name": "이더리움", "worldId": "portfolio:local:crypto-test"}


def source_graph(overrides=None):
    stamp = (NOW - timedelta(minutes=2)).isoformat()
    graph = PortfolioOntology(SUBJECT["accountId"], entities=[OntologyEntity("portfolio:crypto-test", "Portfolio", "portfolio", {})])
    signals = {"cryptoMarkets": {"ethereum": {"symbol": "ETH", "name": "이더리움", "price": 2400,
        "change24h": -1.2, "change7d": -4.7, "volume24h": 150000, "marketCap": 3000000,
        "lastUpdated": stamp, "fetchedAt": stamp, "provider": "CoinGecko"}},
        "cryptoFetchedAt": stamp, "cryptoFreshness": {"status": "fresh", "fetchedAt": stamp, "maxAgeMinutes": 25}}
    signals["cryptoMarkets"]["ethereum"].update(overrides or {})
    add_external_signal_concepts(graph, "portfolio:crypto-test", signals, {})
    return graph


class Source:
    def metadata(self, world):
        return {"status": "ok", "aboxSnapshotId": "source-1", "accountId": SUBJECT["accountId"]}
    def snapshot_id(self, world):
        return "source-1"
    def candidates(self, world, symbol):
        return [{**row.properties, "id": row.entity_id, "kind": row.kind, "label": row.label}
                for row in source_graph().entities if row.properties.get("symbol") == symbol]


def capture():
    return ObservationEvidenceReader(Source(), clock=lambda: NOW).capture_session(SUBJECT)


def ref(quote, field="currentPrice"):
    return {"factId": quote["id"], "field": field, "period": "current"}


def condition(packet):
    quote = quote_from(packet)
    return {"description": "분석 당시 가격을 웃돌면 약세 설명을 재검토합니다.",
            "left": ref(quote), "right": ref(quote), "operator": "gt", "effect": "weakens", "horizonMinutes": 120}


class CryptoAIObservationTests(unittest.TestCase):
    def test_changed_quote_refreshes_without_transition_and_ignores_poll_clocks(self):
        from test_verified_snapshot_reasoning import snapshot
        from digital_twin.modules.reasoning.domain.verified_snapshot_reasoning import verified_monitor_snapshot_reasoning_event
        before = snapshot(external_signals={"cryptoMarkets": {
            "ethereum": {"symbol": "ETH", "price": 2400, "change24h": 0.1}}})
        after = deepcopy(before)
        after.external_signals["cryptoMarkets"]["ethereum"].update(price=2401, fetchedAt=NOW.isoformat())
        event = verified_monitor_snapshot_reasoning_event(after, before.to_monitor_state())
        self.assertEqual(["ETH"], event.payload["symbols"])
        self.assertEqual(["CryptoMarket"], event.payload["factTypesBySymbol"]["ETH"])
        self.assertEqual([], event.payload["verifiedSourceSnapshot"]["cryptoTransitions"])
        self.assertEqual(["external.cryptoMarkets"], event.payload["changedFieldsBySymbol"]["ETH"])
        self.assertTrue(event.payload["factChangeContract"]["dependencyKeysComplete"])
        later = deepcopy(after)
        later.external_signals["cryptoMarkets"]["ethereum"]["fetchedAt"] = (NOW+timedelta(minutes=5)).isoformat()
        self.assertIsNone(verified_monitor_snapshot_reasoning_event(later, after.to_monitor_state()))
        # A quote does not require a synthetic account holding.
        before.positions = before.watchlist = []
        after.positions = after.watchlist = []
        self.assertEqual(["ETH"], verified_monitor_snapshot_reasoning_event(after, before.to_monitor_state()).payload["symbols"])

    def test_real_abox_source_scopes_match_versioned_crypto_contract(self):
        from digital_twin.modules.reasoning.domain.fact_changes import fact_change_contract
        from digital_twin.modules.reasoning.domain.ontology_scopes import apply_scoped_abox_identity
        from digital_twin.modules.reasoning.domain.ontology_fact_slots import build_fact_slot_projection_plan, select_fact_slot_scope_ids
        from digital_twin.modules.reasoning.domain.projection_facts import graph_for_graph_store_persistence
        from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import governed_graph_inference_rules
        graph = source_graph({"change7d": 0.1})
        graph.entities.extend([
            OntologyEntity("crypto-asset:BTC", "Bitcoin", "crypto-asset", {"symbol": "BTC", "currentPrice": 90000}),
            OntologyEntity("stock:AAPL", "Apple", "stock", {"symbol": "AAPL", "currentPrice": 200}),
        ])
        rules = [r.to_dict() for r in governed_graph_inference_rules() if r.rule_id.startswith("graph.crypto.market.")]
        graph = graph_for_graph_store_persistence(graph, {"rules": rules,
            "inputRelationTypes": ["HAS_OBSERVATION", "HAS_EXTERNAL_SIGNAL"]})
        scopes = {r["scopeId"]: r for r in apply_scoped_abox_identity(graph, account_id=SUBJECT["accountId"])["scopePlan"]}
        fields = {"ETH": ["external.cryptoMarkets"]}
        contract = fact_change_contract(["CryptoMarket"], {"ETH": ["CryptoMarket"]}, fields)
        plan = build_fact_slot_projection_plan(["ETH"], contract["scopeFamilies"],
            requested_fact_families_by_symbol=contract["scopeFamiliesBySymbol"], changed_fields_by_symbol=fields,
            event_boundary_authoritative=True, requested_dependency_keys=contract["dependencyKeys"],
            requested_dependency_keys_by_symbol=contract["dependencyKeysBySymbol"], dependency_boundary_authoritative=True)
        selected = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)
        self.assertTrue(selected["enabled"])
        self.assertTrue({"macro:crypto", "symbol:ETH:market", "symbol:ETH:flow"} <= set(selected["selectedScopeIds"]))
        self.assertFalse(any("AAPL" in key for key in selected["selectedScopeIds"]))
        self.assertEqual(["BTC", "ETH"], scopes["macro:crypto"]["nativeSourceSymbols"])
        old_keys = ["kind:crypto-market-signal", "kind:crypto-exposure", "kind:market-event", "relation:has-observation"]
        old_plan = {**plan, "requestedDependencyKeys": old_keys, "requestedDependencyKeysBySymbol": {"ETH": old_keys}}
        self.assertEqual("blocked-dependency-key-no-scope-match", select_fact_slot_scope_ids(scopes, scopes.keys(), old_plan)["status"])
        # Exact dependency mismatches must still fail closed.
        plan.update(requestedDependencyKeys=["kind:missing"], requestedDependencyKeysBySymbol={"ETH": ["kind:missing"]})
        self.assertEqual("blocked-dependency-key-no-scope-match", select_fact_slot_scope_ids(scopes, scopes.keys(), plan)["status"])

    def test_crypto_subject_discovery_respects_enabled_accounts_and_collection_scope(self):
        self.assertEqual([("BTC", "비트코인"), ("ETH", "이더리움")], configured_crypto_assets({}))
        self.assertEqual([], configured_crypto_assets({"externalCoinGeckoEnabled": "0"}))
        self.assertEqual([("ETH", "이더리움")], configured_crypto_assets({"externalCryptoIds": "ethereum,ethereum,unknown"}))
        from digital_twin.infrastructure.composition.ai_orchestration import ai_control_subjects
        accounts = [SimpleNamespace(account_id="live", enabled=True, watchlist_symbols=[]),
                    SimpleNamespace(account_id="disabled", enabled=False, watchlist_symbols=[]),
                    SimpleNamespace(account_id="demo", enabled=True, watchlist_symbols=[])]
        snapshots = {"live": SimpleNamespace(positions=[], watchlist=[], has_live_account_data=lambda: True),
                     "disabled": SimpleNamespace(positions=[], watchlist=[], has_live_account_data=lambda: True),
                     "demo": SimpleNamespace(positions=[], watchlist=[], has_live_account_data=lambda: False)}
        with patch("digital_twin.infrastructure.operational_store.monitor_store", return_value=SimpleNamespace(load_previous=lambda: snapshots)), \
             patch("digital_twin.modules.accounts.infrastructure.mysql_watchlist_account_reader.MySQLWatchlistAccountReader", return_value=SimpleNamespace(load_all=lambda: accounts)), \
             patch("digital_twin.modules.portfolio.contracts.account_snapshot_from_monitor_state", side_effect=lambda s: s), \
             patch("digital_twin.modules.reasoning.contracts.world_from_snapshot", return_value=SimpleNamespace(world_id="portfolio:local:live", market_id="global")):
            subjects = ai_control_subjects({})
        self.assertEqual({("live", "BTC"), ("live", "ETH")}, {(s["accountId"], s["symbol"]) for s in subjects})

    def test_crypto_native_evidence_keeps_source_units_versions_and_required_quote(self):
        session = capture()
        packet = session.packet()
        self.assertEqual(CRYPTO_EVIDENCE_PROFILE, packet["profile"])
        quote = quote_from(packet)
        self.assertEqual(("crypto-asset", 2400, "USD", -1.2, -4.7, 150000),
            tuple(quote[k] for k in ("kind", "currentPrice", "currency", "change24h", "change7d", "volume24h")))
        partial = next(row for row in source_graph({"change7d": None, "volume24h": None}).entities if row.kind == "crypto-asset")
        self.assertNotIn("change7d", partial.properties)
        self.assertNotIn("volume24h", partial.properties)
        class OldProjection(Source):
            def candidates(self, world, symbol):
                rows = super().candidates(world, symbol)
                for row in rows:
                    if row.get("kind") == "crypto-asset":
                        row.pop("currentPrice", None)
                return rows
        from digital_twin.modules.reasoning.domain.observation_evidence import CryptoQuoteNotReady
        reader = ObservationEvidenceReader(OldProjection(), clock=lambda: NOW)
        with self.assertRaises(CryptoQuoteNotReady):
            reader.capture_session(SUBJECT)
        from test_ai_control import AIControlTests
        service, store, planner = AIControlTests().runner()
        service.evidence.side_effect = lambda _: reader.capture_session(SUBJECT)
        store.defer_evidence.return_value = True
        self.assertEqual("awaiting-evidence", service.run_once()["status"])
        store.defer_evidence.assert_called_once()
        store.fail.assert_not_called(); planner.assert_not_called()
        missing = deepcopy(packet); quote_from(missing).pop("currentPrice")
        with self.assertRaises(EvidenceContractError):
            validate_evidence_packet(missing)
        self.assertEqual("fresh", packet["quoteAssessment"]["quotes"][0]["status"])
        self.assertEqual(quote, quote_from(session.select([])))
        self.assertTrue(any(row["kind"] == "price-path" for row in packet["facts"]))
        for key, value in (("profile", "independent-observation-v3-business-research"), ("accountId", "other")):
            wrong = deepcopy(packet); wrong[key] = value
            if key == "accountId":
                wrong["facts"][0]["accountId"] = SUBJECT["accountId"]
            with self.assertRaises(EvidenceContractError):
                validate_evidence_packet(wrong)
        with self.assertRaises(ValueError):
            comparable_refs(packet, ref(quote, "change24h"), ref(quote, "change7d"))
        changed = deepcopy(packet)
        for row in changed["facts"]:
            if row["kind"] == "crypto-asset":
                row["sourceAsOf"] = NOW.isoformat()
        # Only change the quote timestamp: source clocks remain audit data.
        from digital_twin.modules.reasoning.domain.observation_evidence import material_fact
        self.assertEqual(material_fact(quote), material_fact(quote_from(changed)))

    def test_crypto_followup_freezes_the_baseline_and_requires_fresh_later_source_data(self):
        packet = capture().packet()
        checks = prepare_observation_conditions([condition(packet)], packet)
        self.assertEqual("frozen-baseline", checks[0]["right"]["valueMode"])
        baseline = {**SUBJECT, "jobId": "delivered-1", "followUpConditions": checks}
        same = evaluate_observation_conditions(packet, baseline)
        self.assertFalse(same[0]["transitionVerified"])
        newer = deepcopy(packet)
        newer["capturedAt"] = (NOW + timedelta(minutes=5)).isoformat()
        quote_from(newer).update(currentPrice=2450, sourceAsOf=(NOW + timedelta(minutes=4)).isoformat())
        crossed = evaluate_observation_conditions(newer, baseline)
        self.assertTrue(crossed[0]["transitionVerified"])
        self.assertEqual({"left": 2450, "right": 2400}, crossed[0]["observedValues"])
        self.assertFalse(evaluate_observation_conditions(newer, baseline, crossed)[0]["transitionVerified"])
        aged = deepcopy(newer); aged["capturedAt"] = (NOW + timedelta(minutes=50)).isoformat()
        self.assertEqual("unavailable", evaluate_observation_conditions(aged, baseline)[0]["status"])
        with self.assertRaises(ValueError):
            evaluate_observation_conditions(newer, {**baseline, "worldId": "other-world"})
        stale = deepcopy(newer); quote_from(stale)["freshnessStatus"] = "stale"
        self.assertEqual("unavailable", evaluate_observation_conditions(stale, baseline)[0]["status"])
        expired = deepcopy(newer); expired["capturedAt"] = (NOW + timedelta(hours=3)).isoformat()
        self.assertEqual("expired", evaluate_observation_conditions(expired, baseline)[0]["status"])
        wrong = condition(packet); wrong["right"]["field"] = "change7d"
        with self.assertRaises(ValueError):
            prepare_observation_conditions([wrong], packet)

    def test_crypto_publication_and_receipt_preserve_the_actual_quote_and_delivery_route(self):
        packet = capture().packet(); quote = quote_from(packet)
        result = {"input": packet, "evidenceIds": [quote["id"]], "quoteSelectionVersion": QUOTE_SELECTION_VERSION,
            "observedAt": NOW.isoformat(), "summary": "최근 가격 경로의 약세를 관찰했습니다.",
            "comparison": "첫 관찰의 기준을 남깁니다.", "hypothesis": "가격 약세가 이어질 가능성을 관찰합니다.",
            "portfolioImpact": "시장 참고 대상이며 실제 보유 비중은 확인되지 않았습니다.",
            "counterEvidence": "원인에 관한 독립 자료가 부족합니다.",
            "notification": {"send": True, "reason": "기간별 가격 경로를 후속 관찰과 연결합니다."},
            "claimEvidence": {}, "observations": [],
            "followUpConditions": prepare_observation_conditions([condition(packet)], packet)}
        self.assertEqual("", publication_block(result, now=NOW))
        self.assertEqual([quote], receipt_facts(result))
        from digital_twin.modules.notifications.domain.observation_price_basis import price_basis_for_result
        self.assertEqual(quote["id"], price_basis_for_result(result, NOW.isoformat())["evidenceId"])
        legacy_quote = {**quote, "kind": "stock", "id": "subject-stock"}
        other_quote = {**legacy_quote, "id": "other-stock", "symbol": "OTHER"}
        legacy_result = {"quoteSelectionVersion": "subject-stock-quote-v1", "input": {
            **packet, "profile": "independent-observation-v3-business-research", "facts": [other_quote, legacy_quote]}}
        self.assertEqual("subject-stock", price_basis_for_result(legacy_result, NOW.isoformat())["evidenceId"])
        text = render_ai_observation(result)
        for value in ("2,400달러", "24시간 변동률: -1.2%", "7일 변동률: -4.7%", "24시간 거래대금: 150,000달러", "분석 시점 기준 2,400달러"):
            self.assertIn(value, text)
        # Fake send only: exercise the real dispatcher with the configured route selection.
        from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
        from digital_twin.modules.notifications.domain.notifications import NotificationJob
        from digital_twin.modules.notifications.infrastructure.notification import transport
        regular = Mock()
        regular.send.return_value = transport.NotificationResult(True, "test")
        account = SimpleNamespace(telegram_chat_id="fixture")
        settings = {"aiResearchTelegramBotToken": "test", "aiResearchTelegramMessageTypes": "researchProgress,aiObservationDiagnostic"}
        with patch.object(transport, "runtime_settings", return_value=settings):
            dispatcher = NotificationDispatchService(SimpleNamespace(), lambda _: regular,
                ai_research_notifier_factory=transport.notifier_for_ai_research)
            job = NotificationJob.create(text, account_id="crypto-test", message_type="aiObservation")
            dispatcher.deliver(job, {"crypto-test": account}, text)
        self.assertEqual("account", job.context["deliveryRoute"])
        regular.send.assert_called_once()

    def test_crypto_research_reuses_scoped_knowledge_and_reproducible_frozen_prompts(self):
        from digital_twin.modules.research_knowledge.domain.frameworks import framework_catalog
        from digital_twin.modules.research_knowledge.domain.context import build_context
        from digital_twin.modules.research_knowledge.domain.knowledge import digest, validate_proposals, revise_claim
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
        from test_research_knowledge import proposal
        packet = capture().packet(); packet["taskId"] = "crypto-task"
        quote = quote_from(packet)
        methods = [{**row, "knownAt": (NOW - timedelta(days=1)).isoformat(), "contentHash": digest(row)} for row in framework_catalog()]
        context = build_context(packet, methods, [], usable_ids=[quote["id"]])
        self.assertEqual(["crypto-observation-continuity-v1"], [row["id"] for row in context["frameworks"]])
        self.assertEqual("missing", context["frameworks"][0]["coverage"][-1]["status"])
        raw = proposal(frameworkId="crypto-observation-continuity-v1", evidenceIds=[quote["id"]],
                       counterEvidenceIds=[], businessThesisIndex=-1)
        validated = validate_proposals(raw, context, packet, {quote["id"]})
        claim = revise_claim(None, validated["claims"][0], packet, "crypto-input", NOW.isoformat())
        later = {**packet, "capturedAt": (NOW + timedelta(minutes=1)).isoformat()}
        recalled = build_context(later, methods, [claim], usable_ids=[quote["id"]])
        self.assertEqual(claim["claimId"], recalled["claims"][0]["claimId"])
        self.assertEqual("historical-context-only", recalled["claims"][0]["authority"])
        with self.assertRaises(ValueError):
            build_context({**later, "accountId": "other"}, methods, [claim])
        packet["researchKnowledge"] = context
        envelope = freeze_execution_input(packet, [], [])
        self.assertIn("크립토 관찰·연구 계약 v1", envelope["prompt"])
        self.assertEqual(0, envelope["outputSchema"]["properties"]["businessResearch"]["properties"]["theses"]["maxItems"])
        validate_execution_input(envelope)

    def test_crypto_stale_source_waits_for_external_collection_without_stock_refresh(self):
        from digital_twin.infrastructure.transactions.ai_observation_source_refresh import AIObservationSourceRefresh
        from digital_twin.modules.ai_orchestration.domain.evidence_readiness import VERSION
        packet = capture().packet()
        packet.update(evidenceReadPolicy=VERSION, capability="market-observe")
        packet["quoteAssessment"]["quotes"][0]["status"] = "stale"
        store = Mock()
        result = AIObservationSourceRefresh(store, account_queue_enabled=True)({**SUBJECT, "taskId": "task"}, packet)
        self.assertEqual("awaiting-external-collection", result["status"])
        self.assertFalse(result["requested"])
        store.transaction.assert_not_called()


if __name__ == "__main__":
    unittest.main()
