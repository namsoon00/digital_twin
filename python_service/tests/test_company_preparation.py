import copy
import json
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.news_intelligence.domain.company_preparation import assess_preparation, relationship_events
from digital_twin.modules.news_intelligence.application.company_preparation_service import CompanyPreparationService
from digital_twin.modules.news_intelligence.application.company_relationship_backfill import CompanyRelationshipBackfill
from digital_twin.modules.news_intelligence.application.company_relationship_research import CompanyRelationshipResearch
from digital_twin.modules.news_intelligence.domain.company_relationships import extract_relationships
from test_business_research import relationship_source, resolver


SUBJECT = {"accountId": "preparation-test", "symbol": "TEST", "name": "Test", "roles": ["holding", "watchlist"], "worldId": "portfolio:local:preparation-test"}
NOW = "2026-10-14T00:00:00Z"


def report():
    metrics = [{"key": key, "official": True, "sourceReferences": [{"revisionId": "r1"}]} for key in ("revenue", "operatingCashFlow", "totalDebt")]
    metrics[0]["comparison"] = {"basis": "year-over-year"}
    return {"companyReportEvidence": {"businessDescription": "사업 설명", "recentFinancials": [{"period": "2026-06-30", "metrics": metrics}],
        "coverage": {"verifiedDocuments": 1}}, "valuation": {"dataReadiness": {"status": "ready"}}}


class States:
    def __init__(self): self.records = {}
    def state(self, account, symbol):
        key = (account, symbol)
        return SimpleNamespace(load=lambda: copy.deepcopy(self.records.get(key, {})),
            replace=lambda value: self.records.__setitem__(key, copy.deepcopy(value)))
    def subject(self, account, symbol):
        return nullcontext(self.state(account, symbol))


class CompanyPreparationTests(unittest.TestCase):
    def test_complete_requires_baseline_sources_relationship_check_and_real_ai_receipt(self):
        relations = {"status": "complete", "documentCount": 1}
        ai = {"status": "complete", "sourceSnapshotId": "snapshot-1", "observedAt": NOW}
        ready = assess_preparation(SUBJECT, report(), relations, ai, {}, NOW)
        self.assertEqual("complete", ready["status"])
        partial = report()
        partial["companyReportEvidence"]["recentFinancials"][0]["metrics"][2]["official"] = False
        state = assess_preparation(SUBJECT, partial, relations, {"status": "waiting"}, {}, NOW)
        self.assertEqual("partial", state["status"])
        self.assertIn("차입금 자료 확인 필요", state["gaps"])
        self.assertEqual(["waiting", "waiting"], [s["status"] for s in state["stages"][-2:]])
        # Queued/completed work without an actual graph evidence receipt never
        # becomes proof of the first AI analysis.
        false_receipt = assess_preparation(SUBJECT, report(), relations, {"status": "complete"}, {}, NOW)
        self.assertEqual({}, false_receipt["firstAnalysis"])
        self.assertNotEqual("complete", false_receipt["status"])

    def test_receipt_survives_recent_task_retention_but_never_crosses_worlds(self):
        state = assess_preparation(SUBJECT, report(), {"status": "complete", "documentCount": 1},
            {"status": "complete", "sourceSnapshotId": "s1", "observedAt": NOW}, {}, NOW)
        next_state = assess_preparation(SUBJECT, report(), {"status": "complete", "documentCount": 1}, {"status": "waiting"}, state, NOW)
        self.assertEqual("s1", next_state["firstAnalysis"]["sourceSnapshotId"])
        changed = assess_preparation({**SUBJECT, "worldId": "portfolio:other"}, report(), {}, {"status": "waiting"}, state, NOW)
        self.assertEqual({}, changed["firstAnalysis"])
        for i in range(15):
            changed = assess_preparation({**SUBJECT, "roles": ["holding"] if i % 2 else ["watchlist"]}, {}, {}, {}, changed, NOW)
        self.assertEqual(8, len(changed["history"]))

    def test_restart_rotation_backoff_and_roles_change(self):
        state, now = States(), [datetime(2026, 10, 14, tzinfo=timezone.utc)]
        subjects = [{**SUBJECT, "symbol": "A", "roles": ["holding"]}, {**SUBJECT, "symbol": "B", "roles": ["watchlist"]}]
        ai = Mock(); ai.progress.return_value = {"status": "waiting"}; ai.request_preparation.return_value = {"status": "pending"}
        reports = Mock(side_effect=RuntimeError("private details"))
        def run():
            return CompanyPreparationService(lambda: subjects, state, reports, lambda _: {}, ai, Mock(),
                {"companyPreparationBatchSize": 1}, clock=lambda: now[0]).run_once()
        self.assertEqual(1, run()["checked"])
        failed = state.records[(SUBJECT["accountId"], "A")]
        self.assertEqual("failed", failed["status"])
        self.assertNotIn("private details", repr(state.records))
        reports.side_effect = None; reports.return_value = report()
        self.assertEqual(1, run()["checked"])
        self.assertIn((SUBJECT["accountId"], "B"), state.records)
        self.assertEqual(0, run()["checked"])
        now[0] += timedelta(minutes=5)
        self.assertEqual(1, run()["checked"])
        self.assertNotEqual("failed", state.records[(SUBJECT["accountId"], "A")]["status"])
        subjects[0]["roles"].append("watchlist")
        self.assertEqual(1, run()["checked"])

    def test_new_watchlist_without_snapshot_wakes_durable_monitor_only(self):
        refresh, ai, reports = Mock(), Mock(), Mock()
        subject = {**SUBJECT, "worldId": "", "roles": ["watchlist"]}
        states = States()
        service = CompanyPreparationService(lambda: [subject], states, reports, Mock(), ai, refresh)
        self.assertEqual(1, service.run_once()["checked"])
        refresh.assert_called_once_with(SUBJECT["accountId"])
        reports.assert_not_called(); ai.request_preparation.assert_not_called()

    def test_subjects_merge_holdings_and_current_watchlist_and_remove_stale_watch(self):
        from digital_twin.infrastructure.composition.company_preparation import preparation_subjects
        account = SimpleNamespace(enabled=True, account_id="a", watchlist_symbols=["B", "C", "NEW"])
        snapshot = SimpleNamespace(positions=[SimpleNamespace(symbol=x, name=x) for x in ("A", "B")],
            watchlist=[SimpleNamespace(symbol=x, name=x) for x in ("B", "C", "REMOVED")], has_live_account_data=lambda: True)
        with patch("digital_twin.infrastructure.operational_store.monitor_store", return_value=SimpleNamespace(load_previous=lambda: {"a": {}})), \
             patch("digital_twin.modules.accounts.infrastructure.mysql_watchlist_account_reader.MySQLWatchlistAccountReader") as reader, \
             patch("digital_twin.modules.portfolio.contracts.account_snapshot_from_monitor_state", return_value=snapshot), \
             patch("digital_twin.modules.reasoning.contracts.world_from_snapshot", return_value=SimpleNamespace(world_id="world-a", market_id="KR")), \
             patch("digital_twin.modules.reasoning.contracts.market_world", return_value=SimpleNamespace(world_id="market-kr")):
            reader.return_value.load_all.return_value = [account]
            rows = {row["symbol"]: row for row in preparation_subjects({})}
        self.assertEqual({"A", "B", "C", "NEW"}, set(rows))
        self.assertEqual(["holding", "watchlist"], rows["B"]["roles"])
        self.assertEqual("", rows["NEW"]["worldId"])

    def test_cached_relationships_preserve_original_clocks_and_stop_rewriting(self):
        item = relationship_source()
        saved = []
        def enrich(items, known):
            results = copy.deepcopy(items)
            for value in results:
                value.raw_payload["companyRelationships"] = extract_relationships(value, resolver, known)
            return results
        def persist(subject, items):
            saved.extend(items)
            item.raw_payload = copy.deepcopy(items[0].raw_payload)
        service = CompanyRelationshipBackfill(lambda _: [copy.deepcopy(item)], enrich, persist, lambda: NOW)
        first = service(SUBJECT)
        self.assertEqual(1, first["resolvedCount"])
        self.assertEqual("2026-02-01", item.published_at)
        service.clock = lambda: "2026-10-15T00:00:00Z"
        self.assertEqual(first, service(SUBJECT))
        self.assertEqual(1, len(saved))
        self.assertEqual(NOW, item.raw_payload["companyRelationships"]["assertions"][0]["firstKnownAt"])
        item.raw_payload.pop("companyRelationships")
        empty = CompanyRelationshipBackfill(lambda _: [item], CompanyRelationshipResearch(SimpleNamespace(search=lambda *_a, **_k: [])), lambda *_: None, lambda: NOW)(SUBJECT)
        self.assertEqual("partial", empty["status"])
        self.assertEqual(["Example Devices"], empty["unresolvedNames"])

    def test_preparation_status_does_not_create_financial_change_alert(self):
        from digital_twin.modules.news_intelligence.domain.company_change_report import build_company_change_report
        from test_company_report_reading import payload as report_payload
        payload = report_payload()
        baseline = build_company_change_report(payload)
        updated = build_company_change_report({**payload, "companyPreparation": {"status": "complete"}}, baseline)
        self.assertEqual(baseline["materialFingerprint"], updated["materialFingerprint"])
        self.assertFalse(updated["deliveryEligible"])


class CompanyPreparationStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mysql_fixtures import TestAccountRegistry, reset_mysql_test_database, test_store_seed
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        reset_mysql_test_database(cls.temp.name)
        cls.registry = TestAccountRegistry(test_store_seed(cls.temp.name))
        cls.settings = {**cls.registry.settings, "aiControlEnabled": "true"}
        cls.ai_store = MySQLAIControlStore(cls.settings)

    def test_reconciliation_runs_with_the_normal_two_connection_pool(self):
        from digital_twin.modules.news_intelligence.infrastructure.mysql_company_preparation import MySQLCompanyPreparationStore
        state = MySQLCompanyPreparationStore({**self.settings, "mysqlConnectionPoolSize": "2"})
        ai = Mock(); ai.progress.return_value = {"status": "waiting"}; ai.request_preparation.return_value = {"status": "pending"}
        subject = {**SUBJECT, "symbol": "POOL"}
        result = CompanyPreparationService(lambda: [subject], state, lambda _: report(),
            lambda _: {"status": "complete", "documentCount": 1}, ai, Mock()).run_once()
        self.assertEqual({"status": "ok", "checked": 1, "universeCount": 1, "errors": []}, result)
        self.assertEqual("partial", state.record(subject["accountId"], "POOL")["status"])

    def test_watchlist_and_monitor_wake_are_atomic_and_preserve_active_lease(self):
        from digital_twin.modules.accounts.domain.accounts import AccountConfig
        from digital_twin.infrastructure import operational_store
        account = AccountConfig.from_dict({"id": "prep-watch", "provider": "toss", "watchlistSymbols": "AAPL"}, {})
        self.registry.upsert(account)
        repo = operational_store.account_watchlist_repository(self.settings)
        repo.mutate_watchlist(account.account_id, "added", ["MSFT"])
        with repo.connect() as c:
            row = c.execute("SELECT * FROM monitor_account_jobs WHERE account_id=%s", (account.account_id,)).fetchone()
        self.assertEqual("pending", row["status"])
        with repo.transaction() as c:
            c.execute("UPDATE monitor_account_jobs SET status='processing',locked_by='worker',locked_until='2099',next_run_at='2099' WHERE account_id=%s", (account.account_id,))
        repo.mutate_watchlist(account.account_id, "added", ["NVDA"])
        with repo.connect() as c:
            row = c.execute("SELECT * FROM monitor_account_jobs WHERE account_id=%s", (account.account_id,)).fetchone()
        self.assertEqual(("processing", "worker", "2099"), (row["status"], row["locked_by"], row["locked_until"]))
        self.assertLess(row["next_run_at"], "2099")
        repo.refresh_recorder = Mock(side_effect=RuntimeError("queue failure"))
        with self.assertRaises(RuntimeError):
            repo.mutate_watchlist(account.account_id, "added", ["TSLA"])
        self.assertNotIn("TSLA", [item["symbol"] for item in repo.watchlist_items(account.account_id)])

    def test_durable_state_and_ai_jobs_deduplicate_without_losing_missing_topics(self):
        from digital_twin.modules.news_intelligence.infrastructure.mysql_company_preparation import MySQLCompanyPreparationStore
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_company_preparation import MySQLPreparationAI
        state = MySQLCompanyPreparationStore(self.settings)
        with state.subject(SUBJECT["accountId"], SUBJECT["symbol"]) as locked:
            locked.replace({"status": "partial", "nextCheckAt": NOW})
            with MySQLCompanyPreparationStore(self.settings).subject(SUBJECT["accountId"], SUBJECT["symbol"]) as busy:
                self.assertIsNone(busy)
        self.assertEqual("partial", MySQLCompanyPreparationStore(self.settings).record(SUBJECT["accountId"], SUBJECT["symbol"])["status"])
        ai = MySQLPreparationAI(self.settings)
        first = ai.request_preparation(SUBJECT, ["사업", "재무", "비교", "현금", "차입", "문서", "관계"], "basis")
        duplicate = MySQLPreparationAI(self.settings).request_preparation(SUBJECT, ["사업"], "basis")
        self.assertEqual(first["taskId"], duplicate["taskId"])
        with ai.connect() as c:
            row = c.execute("SELECT payload_json FROM ai_control_tasks WHERE task_id=%s", (first["taskId"],)).fetchone()
        gaps = json.loads(row["payload_json"])["researchGaps"]
        self.assertEqual(4, len(gaps)); self.assertIn("관계", gaps[-1])
        self.assertEqual("waiting", ai.progress(SUBJECT)["status"])
        with ai.transaction() as c:
            self.ai_store.insert(c, {**SUBJECT, "taskId": "prep-observe-test", "capability": "observe"})
            c.execute("UPDATE ai_control_tasks SET status='completed',result_json=%s WHERE task_id='prep-observe-test'", (json.dumps({"quality": {"status": "accepted"}, "input": {"sourceSnapshotId": None}}),))
        self.assertEqual("waiting", ai.progress(SUBJECT)["status"])
        with ai.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET result_json=%s WHERE task_id='prep-observe-test'", (json.dumps({"quality": {"status": "accepted"}, "input": {"sourceSnapshotId": "s1"}, "observedAt": NOW}),))
        self.assertEqual("s1", ai.progress(SUBJECT)["sourceSnapshotId"])
        self.assertEqual("waiting", ai.progress({**SUBJECT, "worldId": "another-world"})["status"])

    def test_relationship_backfill_commits_source_and_projection_outbox_once(self):
        from digital_twin.infrastructure import operational_store
        evidence = operational_store.research_evidence_store(self.settings)
        item = relationship_source()
        evidence.upsert_many([item])
        item.raw_payload["companyRelationships"] = extract_relationships(item, resolver, NOW)
        saved, events = evidence.upsert_many_with_events([item], relationship_events)
        self.assertEqual(1, saved)
        self.assertEqual(["research_evidence.collected", "ontology.reasoning_requested"], [event.name for event in events])
        self.assertEqual([], events[0].payload["alertEligibleItems"])
        self.assertEqual(["TEST"], events[1].payload["symbols"])
        self.assertEqual(1, len(evidence.relationship_documents("TEST")))
        self.assertEqual((0, []), evidence.upsert_many_with_events([item], relationship_events))


if __name__ == "__main__":
    unittest.main()
