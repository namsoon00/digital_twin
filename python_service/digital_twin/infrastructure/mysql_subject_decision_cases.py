"""Compatibility module for digital_twin.modules.decisions.infrastructure.mysql_subject_decision_cases."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.decisions.infrastructure.mysql_subject_decision_cases')
