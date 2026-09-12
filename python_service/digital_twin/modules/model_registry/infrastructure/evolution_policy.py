"""Load an operator-owned policy; model output cannot override this policy."""

import json
from pathlib import Path

from digital_twin.modules.model_registry.domain.ontology_evolution import validate_policy


def evolution_policy(settings=None):
    raw = (settings or {}).get("ontologyEvolutionPolicy")
    if raw is None:
        raw = Path(__file__).with_name("ontology_evolution_policy.json").read_text(encoding="utf-8")
    return validate_policy(json.loads(raw) if isinstance(raw, str) else raw)
