"""Compatibility module for digital_twin.modules.model_registry.infrastructure.mysql_hypothesis_development."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.model_registry.infrastructure.mysql_hypothesis_development')
