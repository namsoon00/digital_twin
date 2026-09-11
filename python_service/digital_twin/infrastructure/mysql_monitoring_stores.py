"""Compatibility module for the explicit monitoring transaction coordinator."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.infrastructure.transactions.monitoring')
