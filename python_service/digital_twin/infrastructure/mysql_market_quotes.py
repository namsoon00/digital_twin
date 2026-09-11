"""Compatibility module for digital_twin.modules.market_data.infrastructure.mysql_market_quotes."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.market_data.infrastructure.mysql_market_quotes')
