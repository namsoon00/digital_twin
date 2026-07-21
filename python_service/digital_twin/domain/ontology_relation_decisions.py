from .ontology_relation_catalog import DECISION_STAGE_DEFINITIONS
from .ontology_relation_contracts import DecisionStageDefinition


def decision_stage_by_key(stage_key: str) -> DecisionStageDefinition:
    return DECISION_STAGE_DEFINITIONS.get(stage_key, DECISION_STAGE_DEFINITIONS["HOLD_KEEP"])
