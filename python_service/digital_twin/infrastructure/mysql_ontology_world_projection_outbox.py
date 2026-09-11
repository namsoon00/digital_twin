"""Compatibility module for digital_twin.modules.reasoning.infrastructure.mysql_ontology_world_projection_outbox."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.reasoning.infrastructure.mysql_ontology_world_projection_outbox')
