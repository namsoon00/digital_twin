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

    def test_does_not_register_non_test_database(self):
        with patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha"}, clear=False):
            mysql_fixtures.mysql_test_settings()

        self.assertNotIn("orbit_alpha", mysql_fixtures._CREATED_TEST_DATABASES)
