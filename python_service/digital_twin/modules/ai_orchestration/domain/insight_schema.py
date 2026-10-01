"""Model output grammar is frozen with the evidence and prompt."""
from digital_twin.modules.ai_orchestration.domain.insight_contract import INSIGHT_VERSION, SECTIONS, METRICS, finite
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


def legacy_planning_schema(packet):
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


def evidence_references(packet, *, usable=False, numeric=False, current_only=False):
    """Bind a field to its captured fact and period, rather than their Cartesian product."""
    groups = {}
    periods = [("current", packet.get("facts", []))]
    if not current_only:
        periods.append(("baseline", (packet.get("lastDeliveredNotification") or {}).get("facts", [])))
    for period, facts in periods:
        for fact in facts:
            if usable and (fact.get("judgementEvidenceUsable") is False or fact.get("valuationDecisionEligible") is False):
                continue
            paths = scalar_paths(fact)
            if numeric:
                paths = [path for path in paths if path in METRICS and finite(fact[path]) is not None
                         and (METRICS[path][1] != "money" or fact.get("currency"))
                         and (path not in {"policyLimitRatio", "strategyMaxPositionWeightPct"} or finite(fact[path]) > 0)]
            if paths:
                groups.setdefault((period, tuple(sorted(paths))), []).append(fact["id"])
    variants = [obj({"factId": choice(sorted(set(ids))), "field": choice(paths), "period": choice([period])})
                for (period, paths), ids in sorted(groups.items())]
    # An empty reference set must not produce an invalid structured-output schema.
    # Local evidence and comparison validation still rejects an unusable packet.
    if not variants:
        if usable or numeric or current_only:
            return evidence_references(packet)
        return obj({"factId": choice([""]), "field": choice(["id"]), "period": choice(["current"])})
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


def planning_schema(packet):
    result = legacy_planning_schema(packet)
    result["$defs"] = {
        "evidence": evidence_references(packet, usable=True),
        "limitation": evidence_references(packet),
        "numeric": evidence_references(packet, usable=True, numeric=True),
        "currentNumeric": evidence_references(packet, usable=True, numeric=True, current_only=True),
    }
    claims = result["properties"]["claimEvidence"]["properties"]
    for section in SECTIONS:
        claims[section] = array({"$ref": "#/$defs/" + ("limitation" if section == "counterEvidence" else "evidence")})
    for key, definition in (("observations", "numeric"), ("followUpConditions", "currentNumeric")):
        for side in ("left", "right"):
            result["properties"][key]["items"]["properties"][side] = {"$ref": "#/$defs/" + definition}
    return result


def review_schema():
    return obj({"version": choice([REVIEW_VERSION]),
        "sections": obj({key: obj({"supported": {"type": "boolean"}, "reason": {"type": "string"}}) for key in SECTIONS}),
        "novelty": choice(["new-meaning", "repetition", "insufficient"]),
        "usefulness": choice(["decision-context", "generic"]), "reason": {"type": "string"}})
