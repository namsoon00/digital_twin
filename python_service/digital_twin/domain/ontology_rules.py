"""Compatibility facade for ontology relation-rule evaluation.

Production code should import from ``ontology_relation_rules``. This module is
kept only for older integrations that still use
``digital_twin.domain.ontology_rules``.
"""

from .ontology_relation_rules import *  # noqa: F401,F403 - legacy import surface

__all__ = [name for name in globals() if not name.startswith("_")]
