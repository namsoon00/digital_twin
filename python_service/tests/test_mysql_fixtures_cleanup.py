import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mysql_fixtures
from digital_twin.infrastructure.mysql_connection_pool import MySQLConnectionPool


class MySQLFixturesCleanupTests(unittest.TestCase):
    def setUp(self):
        self.created = dict(mysql_fixtures._CREATED_TEST_DATABASES)
        self.locks = dict(mysql_fixtures._HELD_TEST_DATABASE_LOCKS)
        mysql_fixtures._CREATED_TEST_DATABASES.clear()

    def tearDown(self):
        for identity, handle in list(mysql_fixtures._HELD_TEST_DATABASE_LOCKS.items()):
            if identity not in self.locks:
                handle.close()
        mysql_fixtures._HELD_TEST_DATABASE_LOCKS.clear()
        mysql_fixtures._HELD_TEST_DATABASE_LOCKS.update(self.locks)
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

    def test_default_database_lock_is_reused_for_the_process(self):
        config = {
            "host": "127.0.0.1",
            "port": 43306,
            "database": "orbit_alpha_test",
            "unix_socket": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            mysql_fixtures.tempfile,
            "gettempdir",
            return_value=temp_dir,
        ):
            mysql_fixtures.acquire_mysql_test_database_lock(config)
            first_handles = dict(mysql_fixtures._HELD_TEST_DATABASE_LOCKS)
            mysql_fixtures.acquire_mysql_test_database_lock(config)

        self.assertEqual(1, len(first_handles))
        self.assertEqual(first_handles, mysql_fixtures._HELD_TEST_DATABASE_LOCKS)

    def test_does_not_register_non_test_database(self):
        with patch.dict(os.environ, {"MYSQL_TEST_DATABASE": "orbit_alpha"}, clear=False):
            mysql_fixtures.mysql_test_settings()

        self.assertNotIn("orbit_alpha", mysql_fixtures._CREATED_TEST_DATABASES)

    def test_process_pool_reuses_a_healthy_connection(self):
        class FakeConnection:
            def __init__(self):
                self.autocommit_values = []
                self.rollback_count = 0

            def ping(self, reconnect=False):
                self.assertions = reconnect

            def autocommit(self, value):
                self.autocommit_values.append(value)

            def rollback(self):
                self.rollback_count += 1

            def close(self):
                raise AssertionError("healthy pooled connection must remain open")

        created = []

        def factory(_autocommit):
            connection = FakeConnection()
            created.append(connection)
            return connection

        pool = MySQLConnectionPool(factory, size=1)
        first = pool.acquire(autocommit=True)
        pool.release(first)
        second = pool.acquire(autocommit=False)

        self.assertIs(first, second)
        self.assertEqual(1, len(created))
        self.assertEqual([True, False], second.autocommit_values)
        self.assertEqual(1, second.rollback_count)
