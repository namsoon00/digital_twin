"""Model output grammar is frozen with the evidence and prompt."""
from digital_twin.modules.ai_orchestration.domain.insight_contract import INSIGHT_VERSION, SECTIONS
from digital_twin.modules.ai_orchestration.domain.insight_quality import REVIEW_VERSION


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def choice(values):
    return {"type": "string", "enum": list(values)}


def array(item):
    return {"type": "array", "items": item}


def scalar_paths(value, prefix=""):
    result = []
    for key, child in value.items():
        path = prefix + str(key)
        if isinstance(child, dict):
            result.extend(scalar_paths(child, path + "."))
        elif child is not None and not isinstance(child, list) and child != "":
            result.append(path)
    return result


def planning_schema(packet):
    current = packet.get("facts", [])
    baseline = (packet.get("lastDeliveredNotification") or {}).get("facts", [])
    ids = sorted({row["id"] for row in current + baseline})
    paths = sorted({path for row in current + baseline for path in scalar_paths(row)})
    ref = obj({"factId": choice(ids), "field": choice(paths), "period": choice(["current", "baseline"] if baseline else ["current"])})
    comparison = {"left": ref, "operator": choice(["gt", "lt", "gte", "lte"]), "right": ref}
    string = {"type": "string"}
    return obj({"insightVersion": choice([INSIGHT_VERSION]),
        **{key: string for key in SECTIONS if key != "notificationReason"},
        "notification": obj({"send": {"type": "boolean"}, "reason": string}),
        "claimEvidence": obj({key: array(ref) for key in SECTIONS}),
        "observations": array(obj(comparison)),
        "followUpConditions": array(obj({"description": string, **comparison, "effect": choice(["supports", "weakens", "invalidates"]), "horizonMinutes": {"type": "integer"}})),
        "evidenceIds": array(choice([row["id"] for row in current])),
        "questions": array(obj({"question": string, "capability": choice(["observe", "research"])})),
        "nextCheckMinutes": {"type": "integer"}})


def review_schema():
    return obj({"version": choice([REVIEW_VERSION]),
        "sections": obj({key: obj({"supported": {"type": "boolean"}, "reason": {"type": "string"}}) for key in SECTIONS}),
        "novelty": choice(["new-meaning", "repetition", "insufficient"]),
        "usefulness": choice(["decision-context", "generic"]), "reason": {"type": "string"}})
