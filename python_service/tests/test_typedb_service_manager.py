import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from digital_twin import service_manager


class TypeDBServiceManagerTests(unittest.TestCase):
    def test_wait_for_typedb_ready_bootstraps_only_pending_fresh_store(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "healthAddress": "127.0.0.1:1729",
                "startupWaitSeconds": "2",
            }
            spec["pid"].write_text("123\n", encoding="utf-8")
            with patch.object(service_manager, "pid_exists", return_value=True), \
                    patch.object(service_manager, "tcp_ready", return_value=True), \
                    patch.object(service_manager, "typedb_driver_ready", side_effect=[False, True]), \
                    patch.object(service_manager, "typedb_credentials_bootstrap_pending", side_effect=[True, False]), \
                    patch.object(service_manager, "bootstrap_typedb_credentials_after_reset", return_value=True) as bootstrap, \
                    patch.object(service_manager, "clear_typedb_credentials_bootstrap_pending") as clear, \
                    patch.object(service_manager.time, "sleep", return_value=None):
                self.assertTrue(service_manager.wait_for_typedb_ready(spec))

        bootstrap.assert_called_once_with(spec)
        clear.assert_called_once_with()

    def test_reset_marker_requires_one_time_credential_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "role": "typedb",
                "dataPath": Path(temp) / "typedb-data",
                "retentionHours": "24",
                "maxSizeMb": "2048",
            }
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=Path(temp) / "marker.json"):
                result = service_manager.run_typedb_data_retention(spec, force=True)
                self.assertEqual("reset", result["status"])
                self.assertTrue(service_manager.typedb_credentials_bootstrap_pending())
                service_manager.clear_typedb_credentials_bootstrap_pending()
                self.assertFalse(service_manager.typedb_credentials_bootstrap_pending())

    def test_mysql_runtime_spec_carries_local_application_provisioning_input(self):
        spec = service_manager.mysql_worker_spec({
            "mysqlHost": "127.0.0.1",
            "mysqlPort": "3306",
            "mysqlDatabase": "orbit_alpha",
            "mysqlUser": "orbit_alpha_app",
            "mysqlPassword": "test-secret",
        })

        self.assertEqual("orbit_alpha", spec["mysqlDatabase"])
        self.assertEqual("orbit_alpha_app", spec["mysqlUser"])
        self.assertEqual("test-secret", spec["mysqlPassword"])
        self.assertEqual("'a\\\\b\\'c'", service_manager.mysql_sql_literal("a\\b'c"))


if __name__ == "__main__":
    unittest.main()
