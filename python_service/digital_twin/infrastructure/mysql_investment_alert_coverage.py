"""Compatibility module for digital_twin.modules.notifications.infrastructure.mysql_investment_alert_coverage."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.notifications.infrastructure.mysql_investment_alert_coverage')
