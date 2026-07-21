from typing import Dict, Optional

from .ontology_relation_catalog import DECISION_STAGE_DEFINITIONS
from .ontology_relation_contracts import DecisionStageDefinition


def decision_stage_by_key(stage_key: str) -> Optional[DecisionStageDefinition]:
    """Return bootstrap metadata for rule authoring, never a runtime fallback.

    A missing key used to silently become ``HOLD_KEEP``.  That converted an
    unknown TypeDB decision stage into a user-facing hold recommendation.
    Runtime code must instead read the stage metadata carried by an
    InferenceBox relation through :func:`decision_stage_from_relation`.
    """
    return DECISION_STAGE_DEFINITIONS.get(str(stage_key or "").strip())


def decision_stage_from_relation(relation: Dict[str, object]) -> Optional[DecisionStageDefinition]:
    """Read decision semantics from one TypeDB-materialized relation.

    ``decisionStage`` remains an opaque TypeDB identifier.  The action group,
    action level, label, and tone travel with the inferred relation so Python
    does not keep a parallel stage-policy or stage-priority list.  Older
    generations without an explicit label can still show the persisted rule
    label, but a missing stage/group/level blocks judgement rather than being
    reinterpreted as a hold state.
    """
    relation = relation or {}
    stage_key = str(relation.get("decisionStage") or relation.get("decision_stage") or "").strip()
    action_group = str(relation.get("actionGroup") or relation.get("action_group") or "").strip()
    action_level = str(relation.get("actionLevel") or relation.get("action_level") or "").strip().lower()
    if not stage_key or not action_group or action_level not in {"reference", "watch", "review", "action", "urgent"}:
        return None
    label = str(
        relation.get("decisionLabel")
        or relation.get("decision_label")
        or relation.get("ruleLabel")
        or relation.get("aiInfluenceLabel")
        or stage_key
    ).strip()
    tone = str(relation.get("decisionTone") or relation.get("decision_tone") or "").strip().lower()
    if tone not in {"hold", "watch", "caution", "danger"}:
        tone = {
            "urgent": "danger",
            "action": "caution",
            "review": "caution",
            "watch": "watch",
            "reference": "hold",
        }[action_level]
    return DecisionStageDefinition(stage_key, action_group, action_level, label, tone)
