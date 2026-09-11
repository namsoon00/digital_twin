"""Compatibility module for the explicit decision_history transaction coordinator."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.infrastructure.transactions.decision_history')
