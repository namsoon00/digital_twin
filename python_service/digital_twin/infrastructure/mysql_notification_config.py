"""Compatibility module for digital_twin.modules.notifications.infrastructure.mysql_notification_config."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.notifications.infrastructure.mysql_notification_config')
