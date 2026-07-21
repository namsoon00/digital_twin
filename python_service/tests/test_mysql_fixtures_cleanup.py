import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mysql_fixtures


class MySQLFixturesCleanupTests(unittest.TestCase):
    def setUp(self):
        self.created = dict(mysql_fixtures._CREATED_TEST_DATABASES)
        mysql_fixtures._CREATED_TEST_DATABASES.clear()

    def tearDown(self):
        mysql_fixtures._CREATED_TEST_DATABASES.clear()
        mysql_fixtures._CREATED_TEST_DATABASES.update(self.created)

    def test_registers_isolated_test_database_before_connection(self):
        with patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha_test_fixture_cleanup"}, clear=False):
            settings = mysql_fixtures.mysql_test_settings()

        self.assertEqual("orbit_alpha_test_fixture_cleanup", settings["mysqlDatabase"])
        self.assertIn("orbit_alpha_test_fixture_cleanup", mysql_fixtures._CREATED_TEST_DATABASES)

    def test_default_test_database_is_reused_across_temporary_seeds(self):
        with patch.dict(os.environ, {
            "MYSQL_TEST_DATABASE": "",
            "DIGITAL_TWIN_TEST_WORKER": "",
            "PYTEST_XDIST_WORKER": "",
        }, clear=False):
            first = mysql_fixtures.test_database_name("/tmp/orbit-alpha-test-one")
            second = mysql_fixtures.test_database_name("/tmp/orbit-alpha-test-two")
            settings = mysql_fixtures.mysql_test_settings("/tmp/orbit-alpha-test-three")

        self.assertEqual("orbit_alpha_test", first)
        self.assertEqual(first, second)
        self.assertEqual(first, settings["mysqlDatabase"])
        self.assertIn(first, mysql_fixtures._CREATED_TEST_DATABASES)

    def test_parallel_worker_uses_stable_bounded_namespace(self):
        with patch.dict(os.environ, {
            "MYSQL_TEST_DATABASE": "",
            "DIGITAL_TWIN_TEST_WORKER": "worker-2",
            "PYTEST_XDIST_WORKER": "",
        }, clear=False):
            first = mysql_fixtures.test_database_name("/tmp/orbit-alpha-test-one")
            second = mysql_fixtures.test_database_name("/tmp/orbit-alpha-test-two")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("orbit_alpha_test_worker_"))
        self.assertEqual(len("orbit_alpha_test_worker_") + 12, len(first))

    def test_does_not_register_non_test_database(self):
        with patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha"}, clear=False):
            mysql_fixtures.mysql_test_settings()

        self.assertNotIn("orbit_alpha", mysql_fixtures._CREATED_TEST_DATABASES)
