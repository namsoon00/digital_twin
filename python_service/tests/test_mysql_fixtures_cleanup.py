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
            lock_identity = "127.0.0.1|43306||orbit_alpha_test"
            first_handle = mysql_fixtures._HELD_TEST_DATABASE_LOCKS[lock_identity]
            mysql_fixtures.acquire_mysql_test_database_lock(config)

        self.assertIs(first_handle, mysql_fixtures._HELD_TEST_DATABASE_LOCKS[lock_identity])

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

    def test_pool_waiter_replaces_a_discarded_connection(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from unittest.mock import Mock
        waiting = Event()
        factory = Mock(side_effect=lambda _: Mock())
        pool = MySQLConnectionPool(factory, size=1, acquire_timeout=1)
        first = pool.acquire()
        wait = pool.available.wait
        def entered(timeout):
            waiting.set()
            return wait(timeout)
        with patch.object(pool.available, "wait", side_effect=entered), ThreadPoolExecutor(1) as executor:
            pending = executor.submit(pool.acquire)
            self.assertTrue(waiting.wait(1))
            # Nothing enters the idle queue. The newly free slot must wake it.
            pool.discard(first)
            replacement = pending.result(timeout=0.5)
        self.assertIsNot(first, replacement)
        self.assertEqual(2, factory.call_count)
        self.assertEqual(1, pool.created)
        pool.release(replacement)

    def test_pool_factory_failure_releases_slot_to_waiter(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from unittest.mock import Mock
        creating, waiting, fail = Event(), Event(), Event()
        healthy = Mock()
        def factory(_):
            if not creating.is_set():
                creating.set()
                if not fail.wait(2):
                    raise AssertionError("test factory was not released")
                raise ConnectionError("factory unavailable")
            return healthy
        pool = MySQLConnectionPool(factory, size=1, acquire_timeout=1)
        wait = pool.available.wait
        def entered(timeout):
            waiting.set()
            return wait(timeout)
        with patch.object(pool.available, "wait", side_effect=entered), ThreadPoolExecutor(2) as executor:
            creator = executor.submit(pool.acquire)
            self.assertTrue(creating.wait(1))
            pending = executor.submit(pool.acquire)
            self.assertTrue(waiting.wait(1))
            fail.set()
            with self.assertRaises(ConnectionError):
                creator.result(timeout=0.5)
            self.assertIs(healthy, pending.result(timeout=0.5))
        self.assertEqual(1, pool.created)
        pool.release(healthy)

    def test_pool_limits_broken_replacements_and_reports_capacity_timeout(self):
        from unittest.mock import Mock
        from digital_twin.infrastructure.mysql_connection_pool import MySQLPoolTimeout, MySQLPoolUnavailable
        factory = Mock(side_effect=lambda _: Mock(ping=Mock(side_effect=ConnectionError("broken"))))
        pool = MySQLConnectionPool(factory, size=1)
        with self.assertRaises(MySQLPoolUnavailable):
            pool.acquire()
        self.assertEqual(3, factory.call_count)
        self.assertEqual(0, pool.created)
        pool.factory = lambda _: Mock()
        pool.acquire_timeout = 0.01
        healthy = pool.acquire()
        with self.assertRaises(MySQLPoolTimeout) as caught:
            pool.acquire()
        self.assertEqual(1, caught.exception.pool_size)
        self.assertEqual(1, caught.exception.created_connections)
        pool.release(healthy)
        self.assertIs(healthy, pool.acquire())
        from digital_twin.infrastructure.mysql_connection_pool import mysql_pool_size
        self.assertEqual(2, mysql_pool_size({"mysqlConnectionPoolSize": "inf"}))
