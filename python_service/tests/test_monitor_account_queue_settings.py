import unittest
from unittest.mock import patch

from digital_twin.infrastructure.service_factory import monitor_account_job_store_from_settings


class MonitorAccountQueueSettingsTests(unittest.TestCase):
    def test_disabled_queue_does_not_construct_a_job_store(self):
        with patch("digital_twin.infrastructure.service_factory.stores.monitor_account_job_store") as factory:
            store = monitor_account_job_store_from_settings({"monitorAccountQueueEnabled": "0"})

        self.assertIsNone(store)
        factory.assert_not_called()

    def test_enabled_queue_constructs_the_job_store(self):
        expected = object()
        settings = {"monitorAccountQueueEnabled": "1"}
        with patch(
            "digital_twin.infrastructure.service_factory.stores.monitor_account_job_store",
            return_value=expected,
        ) as factory:
            store = monitor_account_job_store_from_settings(settings)

        self.assertIs(expected, store)
        factory.assert_called_once_with(settings)


if __name__ == "__main__":
    unittest.main()
