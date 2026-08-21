import unittest
from types import SimpleNamespace

from digital_twin.application.time_series_platform import (
    TemporalFeatureSnapshotService,
    TimeSeriesBackendPlatformService,
    TimeSeriesProjectionRunner,
    VersionedMarketTimeSeriesStore,
)
from digital_twin.domain.time_series_storage import (
    TemporalFeatureSnapshot,
    TimeSeriesWatermark,
    compare_feature_snapshots,
)
from digital_twin.infrastructure.questdb_time_series import QuestDBTimeSeriesAdapter


class Context:
    def __init__(self, connection=None):
        self.connection = connection or object()

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class FakeBaseline:
    backend_id = "mysql-primary"

    def __init__(self):
        self.rows = [{
            "account_id": "main",
            "symbol": "005930",
            "granularity": "3m",
            "bucket_at": "2026-08-15T00:00:00Z",
            "observed_at": "2026-08-15T00:00:00Z",
            "current_price": 100,
        }]

    def transaction(self):
        return Context()

    def record_snapshots_with_connection(self, connection, snapshots):
        return {"savedCount": len(list(snapshots or []))}

    def projectable_rows_with_connection(self, connection, **_kwargs):
        return list(self.rows)

    def load_temporal_windows(self, account_id, symbols, definitions, as_of=""):
        return {symbol: {definition.key: [] for definition in definitions} for symbol in symbols}

    def summary(self, account_id=""):
        return {"accountId": account_id, "granularities": []}

    def watermark(self):
        return TimeSeriesWatermark("mysql-primary", "2026-08-15T00:00:00Z")


class FakeRegistry:
    def __init__(self):
        self.health = {}

    def control(self):
        return {"activeBackendId": "mysql-primary", "shadowBackendId": "questdb-shadow"}

    def update_health(self, backend_id, health):
        self.health[backend_id] = health


class SwitchingRegistry(FakeRegistry):
    def __init__(self):
        super().__init__()
        self.rows = {
            "mysql-primary": {"backendId": "mysql-primary", "status": "active"},
            "questdb-shadow": {"backendId": "questdb-shadow", "status": "candidate"},
        }
        self.control_row = {
            "activeBackendId": "mysql-primary",
            "shadowBackendId": "questdb-shadow",
            "candidateBackendId": "questdb-shadow",
            "version": 1,
        }

    def control(self):
        return dict(self.control_row)

    def get(self, backend_id):
        return dict(self.rows.get(backend_id) or {})

    def transition(self, backend_id, status):
        self.rows[backend_id]["status"] = status
        return dict(self.rows[backend_id])

    def set_control(self, active, shadow="", candidate="", expected_version=None):
        self.asserted_version = expected_version
        self.control_row = {
            "activeBackendId": active,
            "shadowBackendId": shadow,
            "candidateBackendId": candidate,
            "version": self.control_row["version"] + 1,
        }
        return dict(self.control_row)

    def list(self):
        return list(self.rows.values())


class FakeOutbox:
    def __init__(self, jobs=None):
        self.enqueued = []
        self.jobs = list(jobs or [])
        self.completed = []
        self.retried = []

    def transaction(self):
        return Context()

    def enqueue_with_connection(self, connection, **kwargs):
        self.enqueued.append(kwargs)
        return True

    def claim(self, backend_ids, worker_id, limit, lease_seconds):
        del backend_ids, worker_id, limit, lease_seconds
        result, self.jobs = self.jobs, []
        return result

    def complete(self, job_id):
        self.completed.append(job_id)

    def retry(self, job_id, error, max_attempts):
        self.retried.append((job_id, error, max_attempts))

    def summary(self):
        return {"backends": []}


class FakeAdapter:
    def __init__(self, fail=False):
        self.fail = fail
        self.rows = []

    def write_observations(self, rows):
        if self.fail:
            raise RuntimeError("shadow unavailable")
        self.rows.extend(rows)
        return {"writtenCount": len(rows)}

    def health(self):
        return {"status": "unavailable" if self.fail else "ready"}

    def watermark(self):
        return TimeSeriesWatermark("fake", "2026-08-15T00:00:00Z")


class SnapshotStore:
    def __init__(self):
        self.saved = []

    def upsert(self, snapshot):
        self.saved.append(snapshot)
        return True


class SnapshotAdapter:
    def __init__(self, backend_id, price):
        self.backend_id = backend_id
        self.price = price

    def load_temporal_windows(self, account_id, symbols, definitions, as_of=""):
        del account_id, as_of
        return {
            symbol: {definition.key: [{"currentPrice": self.price}] for definition in definitions}
            for symbol in symbols
        }

    def watermark(self):
        return TimeSeriesWatermark(self.backend_id, "2026-08-15T00:00:00Z")


class RecordingQuestDB(QuestDBTimeSeriesAdapter):
    def __init__(self):
        self.settings = {"questDbWriteBatchSize": "10"}
        self.backend_id = "questdb-shadow"
        self.lines = []

    def ensure_schema(self):
        return None

    def write_lines(self, lines):
        self.lines.extend(lines)


class SchemaQuestDB(QuestDBTimeSeriesAdapter):
    def __init__(self, ttl_overrides=None, missing_tables=None, metadata_error=""):
        super().__init__({
            "questDbHttpUrl": "http://schema-test-" + str(id(self)),
            "marketTimeSeriesRawRetentionDays": "2",
            "marketTimeSeries15mRetentionDays": "10",
            "marketTimeSeries1hRetentionDays": "90",
            "marketTimeSeriesDailyRetentionDays": "180",
        })
        self.statements = []
        self.metadata_error = metadata_error
        self.missing_tables = set(missing_tables or [])
        self.ttl_days = {
            "market_observations_3m": 2,
            "market_observations_15m": 10,
            "market_observations_1h": 90,
            "market_observations_1d": 180,
            "portfolio_marks": 2,
            **dict(ttl_overrides or {}),
        }

    def execute(self, sql):
        normalized = " ".join(str(sql).split())
        self.statements.append(normalized)
        if normalized.startswith("SELECT table_name, ttlValue"):
            if self.metadata_error:
                raise RuntimeError(self.metadata_error)
            return {
                "columns": [
                    {"name": "table_name"},
                    {"name": "ttl_value"},
                    {"name": "ttl_unit"},
                ],
                "dataset": [
                    [table_name, days, "DAY"]
                    for table_name, days in self.ttl_days.items()
                    if table_name not in self.missing_tables
                ],
            }
        if normalized.startswith("CREATE TABLE IF NOT EXISTS"):
            table_name = normalized.split()[5]
            self.missing_tables.discard(table_name)
            return {}
        if normalized == "SELECT 1 AS ready":
            return {"columns": [{"name": "ready"}], "dataset": [[1]]}
        return {}


class TimeSeriesPlatformTests(unittest.TestCase):
    def test_questdb_schema_skips_redundant_ttl_metadata_writes(self):
        adapter = SchemaQuestDB()

        adapter.ensure_schema()

        self.assertFalse(any(" SET TTL " in statement for statement in adapter.statements))
        self.assertFalse(any(statement.startswith("CREATE TABLE") for statement in adapter.statements))

    def test_questdb_schema_creates_only_missing_table(self):
        adapter = SchemaQuestDB(missing_tables={"market_observations_1h"})

        adapter.ensure_schema()

        create_statements = [statement for statement in adapter.statements if statement.startswith("CREATE TABLE")]
        self.assertEqual(1, len(create_statements))
        self.assertIn("CREATE TABLE IF NOT EXISTS market_observations_1h", create_statements[0])

    def test_questdb_schema_changes_only_drifted_ttl(self):
        adapter = SchemaQuestDB({"market_observations_1h": 30})

        adapter.ensure_schema()

        ttl_statements = [statement for statement in adapter.statements if " SET TTL " in statement]
        self.assertEqual(["ALTER TABLE market_observations_1h SET TTL 90 DAYS"], ttl_statements)

    def test_questdb_health_checks_schema_readiness_without_ddl(self):
        adapter = SchemaQuestDB(metadata_error="metadata unavailable")

        result = adapter.health()

        self.assertEqual("unavailable", result["status"])
        self.assertIn("metadata unavailable", result["error"])
        self.assertFalse(any(statement.startswith("CREATE TABLE") for statement in adapter.statements))

    def test_active_write_queues_shadow_without_changing_baseline_result(self):
        baseline = FakeBaseline()
        outbox = FakeOutbox()
        store = VersionedMarketTimeSeriesStore(
            baseline,
            {"mysql-primary": baseline, "questdb-shadow": FakeAdapter()},
            FakeRegistry(),
            outbox,
            {"timeSeriesShadowWritesEnabled": "1", "timeSeriesQuestDbEnabled": "1"},
        )
        snapshot = SimpleNamespace(
            account_id="main",
            generated_at="2026-08-15T00:00:00Z",
            positions=[SimpleNamespace(symbol="005930")],
            watchlist=[],
        )

        result = store.record_snapshots_with_connection(object(), [snapshot])

        self.assertEqual(1, result["savedCount"])
        self.assertEqual(1, result["projectionQueuedCount"])
        self.assertEqual("questdb-shadow", outbox.enqueued[0]["backend_id"])
        self.assertEqual(baseline.rows, outbox.enqueued[0]["payload"]["observations"])

    def test_projection_failure_is_retried_and_not_reported_as_completed(self):
        outbox = FakeOutbox([{
            "jobId": "job-1",
            "backendId": "questdb-shadow",
            "operation": "write-observations",
            "payload": {"observations": [{"symbol": "005930"}]},
        }])
        runner = TimeSeriesProjectionRunner(
            {"questdb-shadow": FakeAdapter(fail=True)},
            outbox,
            FakeRegistry(),
            {"timeSeriesProjectionMaxAttempts": "5"},
        )

        result = runner.run_once()

        self.assertEqual(1, result["failedCount"])
        self.assertEqual([], outbox.completed)
        self.assertEqual("job-1", outbox.retried[0][0])

    def test_compatibility_read_persists_feature_snapshot_identity(self):
        baseline = FakeBaseline()
        snapshots = SnapshotStore()
        store = VersionedMarketTimeSeriesStore(
            baseline,
            {"mysql-primary": baseline},
            FakeRegistry(),
            FakeOutbox(),
            {},
            snapshot_store=snapshots,
        )

        store.load_temporal_windows(
            "main", ["005930"], [SimpleNamespace(key="1D")], "2026-08-15T00:00:00Z"
        )

        self.assertEqual(1, len(snapshots.saved))
        self.assertEqual("mysql-primary", snapshots.saved[0].backend_id)
        self.assertEqual(snapshots.saved[0].snapshot_id, store.last_feature_snapshot["snapshotId"])

    def test_feature_snapshot_comparison_is_deterministic(self):
        store = SnapshotStore()
        service = TemporalFeatureSnapshotService(
            {
                "mysql-primary": SnapshotAdapter("mysql-primary", 100),
                "questdb-shadow": SnapshotAdapter("questdb-shadow", 100),
            },
            store,
        )
        definition = SimpleNamespace(key="1D")

        comparison = service.compare(
            "mysql-primary", "questdb-shadow", "main", ["005930"], [definition],
            "2026-08-15T00:00:00Z",
        )

        self.assertEqual("equivalent", comparison["status"])
        self.assertEqual(2, len(store.saved))

    def test_feature_comparison_ignores_backend_label_and_timestamp_formatting(self):
        active = TemporalFeatureSnapshot.create(
            "mysql-primary", "main", "2026-08-15T00:00:00Z",
            {"005930": {"1D": [{
                "bucketAt": "2026-08-15T00:00:00Z",
                "currentPrice": 100.123456789012,
                "observationSource": "mysql-market-time-series",
            }]}},
            TimeSeriesWatermark("mysql-primary", "2026-08-15T00:00:00Z"),
        )
        candidate = TemporalFeatureSnapshot.create(
            "questdb-shadow", "main", "2026-08-15T00:00:00Z",
            {"005930": {"1D": [{
                "bucketAt": "2026-08-15T00:00:00.000000Z",
                "currentPrice": 100.123456789013,
                "observationSource": "questdb-market-time-series",
            }]}},
            TimeSeriesWatermark("questdb-shadow", "2026-08-15T00:00:00Z"),
        )

        self.assertEqual("equivalent", compare_feature_snapshots(active, candidate)["status"])

    def test_empty_snapshot_scope_never_projects_the_full_history(self):
        baseline = FakeBaseline()
        outbox = FakeOutbox()
        store = VersionedMarketTimeSeriesStore(
            baseline,
            {"mysql-primary": baseline, "questdb-shadow": FakeAdapter()},
            FakeRegistry(),
            outbox,
            {"timeSeriesShadowWritesEnabled": "1", "timeSeriesQuestDbEnabled": "1"},
        )

        result = store.record_snapshots_with_connection(object(), [])

        self.assertEqual(0, result["projectionQueuedCount"])
        self.assertEqual([], outbox.enqueued)

    def test_backend_promotion_requires_empty_projection_queue_and_feature_parity(self):
        registry = SwitchingRegistry()
        adapters = {"mysql-primary": FakeAdapter(), "questdb-shadow": FakeAdapter()}
        platform = TimeSeriesBackendPlatformService(
            adapters,
            registry,
            FakeOutbox(),
            snapshot_service=None,
            settings={"timeSeriesPromotionMaxWatermarkLagSeconds": "180"},
        )

        result = platform.promote("questdb-shadow", {"status": "equivalent"})

        self.assertEqual("promoted", result["status"])
        self.assertEqual("questdb-shadow", result["control"]["activeBackendId"])
        self.assertEqual("mysql-primary", result["control"]["candidateBackendId"])

    def test_questdb_routes_each_granularity_and_preserves_account_fields(self):
        adapter = RecordingQuestDB()
        adapter.write_observations([{
            "account_id": "main",
            "symbol": "005930",
            "granularity": "3m",
            "bucket_at": "2026-08-15T00:00:00Z",
            "observed_at": "2026-08-15T00:00:00Z",
            "provider": "test",
            "source_role": "holding",
            "current_price": 100,
            "quantity": 10,
            "average_price": 90,
            "profit_loss_rate": 11.1,
        }])

        market_insert = next(line for line in adapter.lines if line.startswith("market_observations_3m"))
        self.assertIn("account_id=main", market_insert)
        self.assertIn("quantity=10", market_insert)
        self.assertFalse(any(line.startswith("market_observations_1d") for line in adapter.lines))


if __name__ == "__main__":
    unittest.main()
