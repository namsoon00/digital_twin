"""Compatibility module for digital_twin.modules.reasoning.infrastructure.mysql_ontology_projection_runs."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.reasoning.infrastructure.mysql_ontology_projection_runs')
