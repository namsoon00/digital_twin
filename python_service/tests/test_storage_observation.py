import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from digital_twin.infrastructure.storage_observation import (
    attach_disk_metrics, collect_sample, disk_inventory, launch_agent, write_json, summarize_observations,
)


class StorageObservationTests(unittest.TestCase):
    def test_slow_inventory_preserves_old_measurement_and_reports_staleness(self):
        now = datetime.now(timezone.utc)
        previous = {"history-archiveBytes": {"value": 4096,
                    "measuredAt": (now - timedelta(hours=2)).isoformat()}}
        def timed_out(*args, **kwargs):
            raise subprocess.TimeoutExpired("du", 180)
        observed = disk_inventory(Path("/unused"), previous, runner=timed_out)
        result = {}
        attach_disk_metrics(result, observed, now=now)
        item = result["diskMeasurements"]["history-archiveBytes"]
        self.assertEqual(result["history-archiveBytes"], 4096)
        self.assertEqual(item["measuredAt"], previous["history-archiveBytes"]["measuredAt"])
        self.assertTrue(item["stale"])
        self.assertEqual(item["lastError"], "TimeoutExpired")
        self.assertNotIn("value", previous.get("mysqlActualBytes", {}))

    def test_live_reads_do_not_walk_files_and_survive_one_query_failure(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as records:
            connection = MagicMock()
            cursor = connection.cursor.return_value.__enter__.return_value
            statements = []
            def execute(sql):
                statements.append(sql)
                if "FROM mysql_retention_runs" in sql:
                    raise RuntimeError("PRIVATE_ACCOUNT_TOKEN_MUST_NOT_LEAK")
            cursor.execute.side_effect = execute
            cursor.fetchall.return_value = []
            seen = []
            def reader(path):
                seen.append(path)
                return 200, {"revision": "sample", "routes": []}
            with patch.dict(os.environ, {"MYSQL_USER": "test", "MYSQL_DATABASE": "test"}), \
                 patch("digital_twin.infrastructure.storage_observation.socket.create_connection"):
                result = collect_sample(root, records, connect=lambda **_: connection, reader=reader)
            self.assertEqual(seen, ["/api/version", "/api/operations/performance"])
            self.assertIn("START TRANSACTION READ ONLY", statements)
            self.assertIn("SET SESSION MAX_EXECUTION_TIME=1500", statements)
            self.assertEqual(result["queryCount"], 8)
            self.assertIn("marketSample", result)
            self.assertEqual(result["coreCollectorStatus"], "partial")
            self.assertNotIn("PRIVATE_ACCOUNT", json.dumps(result))
            self.assertNotIn("history-archiveBytes", result)
            connection.rollback.assert_called_once()
            connection.close.assert_called_once()

    def test_missing_or_future_measurements_cannot_be_current_or_zero(self):
        result = {}
        attach_disk_metrics(result, {"mysqlActualBytes": {
            "value": 100, "measuredAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}})
        self.assertTrue(result["diskMeasurements"]["mysqlActualBytes"]["stale"])
        self.assertEqual(result["diskMeasurements"]["history-archiveBytes"]["status"], "unavailable")
        self.assertNotIn("history-archiveBytes", result)

    def test_inventory_uses_allocated_blocks_and_keeps_independent_clocks(self):
        def run(command, **kwargs):
            self.assertEqual(command[:2], ["/usr/bin/du", "-sk"])
            self.assertEqual(kwargs["timeout"], 180)
            return subprocess.CompletedProcess(command, 0, "12\t/path\n")
        metrics = disk_inventory(Path("/unused"), runner=run, mysql_directory="/unused/data/mysql-runtime")
        self.assertEqual(metrics["mysqlActualBytes"]["value"], 12288)
        self.assertIn("measuredAt", metrics["history-archiveBytes"])

    def test_sessions_can_share_inventory_without_copying_or_refreshing_its_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("legacy", "repair"):
                (root / name).mkdir()
            measured = (datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
            write_json(root / "shared/component-metrics.json", {"mysqlActualBytes": {
                "value": 1234, "measuredAt": measured}})
            connection = MagicMock()
            connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
            with patch.dict(os.environ, {"MYSQL_USER": "test", "MYSQL_DATABASE": "test"}), \
                 patch("digital_twin.infrastructure.storage_observation.socket.create_connection"):
                for name in ("legacy", "repair"):
                    result = collect_sample(root, root/name, connect=lambda **_: connection,
                        reader=lambda _: (200, {}), metrics_directory=root/"shared")
                    self.assertEqual(result["mysqlActualBytes"], 1234)
                    self.assertEqual(result["diskMeasurements"]["mysqlActualBytes"]["measuredAt"], measured)
                    self.assertFalse((root/name/"component-metrics.json").exists())

    def test_launchd_recovers_crashes_but_not_successful_stop_or_deadline(self):
        config = launch_agent("local.test", "/python", "/watch.py", "/repo", "/state")
        self.assertTrue(config["RunAtLoad"])
        self.assertEqual(config["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(config["ProgramArguments"][-2:], ["--directory", "/state"])

    def test_expired_window_is_preserved_and_exits_without_reading_services(self):
        script = Path(__file__).resolve().parents[2] / "scripts/watch-storage.py"
        with tempfile.TemporaryDirectory() as home:
            root, records = Path(home) / "repo", Path(home) / "records"
            root.mkdir()
            started = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat()
            write_json(records / "observation-window.json", {"startedAt": started, "maximumDays": 10})
            result = subprocess.run([os.sys.executable, str(script), "--root", str(root),
                                     "--directory", str(records)], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads((records / "observation-window.json").read_text())["startedAt"], started)
            self.assertTrue(json.loads((records / "storage-watch-stopped.json").read_text())["deadlineReached"])
            self.assertFalse((records / "storage-samples.jsonl").exists())
            self.assertFalse((records / "storage-watch.pid").exists())

    def test_stop_file_survives_reboot_style_invocation(self):
        script = Path(__file__).resolve().parents[2] / "scripts/watch-storage.py"
        with tempfile.TemporaryDirectory() as home:
            root, records = Path(home) / "repo", Path(home) / "records"
            root.mkdir(); records.mkdir()
            (records / "STOP").touch()
            result = subprocess.run([os.sys.executable, str(script), "--root", str(root),
                                     "--directory", str(records)], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(json.loads((records / "storage-watch-stopped.json").read_text())["stopFile"])
            self.assertFalse((records / "storage-samples.jsonl").exists())

    def test_http_failure_is_service_evidence_not_a_collector_exception(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as records:
            connection = MagicMock()
            connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
            with patch.dict(os.environ, {"MYSQL_USER": "test", "MYSQL_DATABASE": "test"}), \
                 patch("digital_twin.infrastructure.storage_observation.socket.create_connection"):
                sample = collect_sample(root, records, connect=lambda **_: connection,
                                        reader=lambda _: (503, {}))
            self.assertFalse(sample["collectorErrors"])
            self.assertEqual(sample["serviceIssues"][0]["status"], 503)
            self.assertEqual(sample["coreCollectorStatus"], "ok")

    def test_summary_excludes_gaps_partial_reads_and_pid_only_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stamp = datetime(2026, 11, 2, tzinfo=timezone.utc)
            write_json(root / "observation-window.json", {"startedAt": stamp.isoformat()})
            def row(minutes, **extra):
                return {"observedAt": (stamp + timedelta(minutes=minutes)).isoformat(),
                        "queryCount": 8, "http": [{}, {}], "capacity": {"state": "healthy"}, **extra}
            rows = [row(0), row(5), row(10, collectorStatus="partial"),
                    row(15), row(150), row(155), row(160, queryCount=0, returnCode=0)]
            (root / "storage-samples.jsonl").write_text("\n".join(json.dumps(item) for item in rows))
            result = summarize_observations(root)
            self.assertEqual(len(result["gapsOver10Minutes"]), 1)
            self.assertAlmostEqual(result["validAdjacentIntervalHours"], 10/60, places=3)
            self.assertFalse(result["eligibleForFinalReview"])
            self.assertFalse(result["continuousUptimeProven"])

    def test_us_actual_clocks_use_date_specific_dst_and_exclude_reference_and_premarket(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / "observation-window.json", {"startedAt": "2026-10-30T00:00:00Z"})
            rows = []
            for date, hour, quality in (("2026-10-30", 13, "actual"),
                                        ("2026-11-02", 13, "actual"),
                                        ("2026-11-02", 14, "reference"),
                                        ("2026-11-02", 14, "actual")):
                clock = date + "T" + str(hour) + ":30:00Z"
                rows.append({"observedAt": clock, "marketSample": [
                    {"market": "US", "source_as_of": clock, "data_quality": quality}]})
            (root / "storage-samples.jsonl").write_text("\n".join(json.dumps(item) for item in rows))
            clocks = summarize_observations(root)["actualMarketSourceClocks"]
            self.assertEqual(clocks["US 2026-10-30"]["uniqueClocks"], 1)
            self.assertEqual(clocks["US 2026-11-02"]["uniqueClocks"], 1)
            self.assertIn("14:30", clocks["US 2026-11-02"]["first"])
