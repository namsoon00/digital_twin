#!/usr/bin/env python3
"""Print the complete deterministic RuleBox prediction semantic audit."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SERVICE = ROOT / "python_service"
if str(PYTHON_SERVICE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SERVICE))

from digital_twin.modules.model_registry.domain.hypothesis_semantic_audit import audit_active_predictive_rules  # noqa: E402
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules  # noqa: E402


print(json.dumps(
    audit_active_predictive_rules(default_graph_inference_rules()),
    ensure_ascii=False,
    indent=2,
))
