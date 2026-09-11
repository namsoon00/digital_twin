"""Web notification storage boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.web.common import operational_read_settings
from typing import Dict


def notification_store():
    return stores.notification_template_store()


def notification_queue_store(settings: Dict[str, object] = None):
    read_settings = dict(settings or operational_read_settings())
    # The list API is a read model. Rule defaults and operational schema are
    # owned by service startup and write-side stores, not the first inbox read.
    read_settings["_skipNotificationRuleDefaultsSeed"] = "1"
    read_settings["_skipOperationalSchemaBootstrap"] = "1"
    return stores.notification_job_store(read_settings)


def notification_rule_store():
    return stores.notification_rule_store()
