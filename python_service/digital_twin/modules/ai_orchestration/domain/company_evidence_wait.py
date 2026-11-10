"""Bounded evidence waits and existing-question handoff, never opinion authority."""
from copy import deepcopy
from .planning import identity
from . import research_repeat as repeat

VERSION = "company-evidence-wait-v1"
PLAN_FIELD = "companyEvidencePlanning"


def state(result, key):
    quality, packet = result.get("quality", {}), result.get("input", {})
    if (not key or quality.get("status") != "not-published" or quality.get("reasonCode") != "evidence-insufficient"
            or packet.get("analysisPurpose") != "source-dated" or not packet.get("investmentMeaningPolicy")):
        return {}
    return {"version": VERSION, "key": key, "authority": "source-wait-only"}


def waiting(previous, key):
    wait = previous.get("companyEvidenceWait") or {}
    return (bool(key) and previous.get("status") == "data-wait" and previous.get("reasonCode") == "evidence-insufficient"
            and wait.get("version") == VERSION and wait.get("key") == key)


def handoff(subject, result, research, now):
    """Revisit only captured, due source questions; no future-source guessing."""
    from .research_work import needs_research_plan, PRIORITY
    if not result.get("processingOutcome", {}).get("companyEvidenceWait"):
        return None
    needs = result["processingOutcome"].get("requiredEvidence", [])
    if not needs:
        return None
    cases, material_keys = {}, {}
    for row in research:
        case = row.get("body", {}) if row.get("kind") == "research-context" else row
        if (case.get("kind") != "brain-case" or not needs_research_plan(case)
                or any(case.get(k) != subject[k] for k in ("accountId", "symbol", "worldId"))):
            continue
        contract = case.get("questionContract") or {}
        if not all(isinstance(contract.get(k), str) and contract[k].strip() for k in ("target", "period", "answerCriterion")):
            continue
        case = deepcopy(case); repeat.initialize(case)
        if repeat.planning_due(case, now):
            cases[case["caseId"]] = repeat.planning_memory(case)
            material_keys[case["caseId"]] = identity(repeat.materials(case))
    selected = [cases[k] for k in sorted(cases)[:5]]
    if not selected:
        return None
    # Authored gap paraphrases and quote ticks cannot mint another planning job.
    scope = [{**{k: c.get(k) for k in ("caseId", "questionContract", "sourceFollowUpOpportunity", "sourceRouteOpportunity")},
              "sourceAnswer": c.get("latestSourceAnswer", {}).get("assessmentFingerprint"),
              "sourceMaterialKey": material_keys[c["caseId"]]}
             for c in selected]
    key = identity(VERSION, {k: subject[k] for k in ("accountId", "symbol", "worldId")}, scope)
    return {**subject, "capability": "research-plan", "priority": PRIORITY,
            "taskId": identity(VERSION, "research-plan", key), "availableAt": now,
            PLAN_FIELD: {"version": VERSION, "key": key,
                         "scopeKey": identity(*[subject[k] for k in ("accountId", "symbol", "worldId")]),
                         "caseIds": [c["caseId"] for c in selected]},
            "researchGaps": [(str(n.get("question", "")) + " 필요한 원천: " + str(n.get("source", "")))[:500] for n in needs[:2]]}


def requested(job):
    value = job.get(PLAN_FIELD) or {}
    ids = value.get("caseIds")
    return (value.get("version") == VERSION and isinstance(value.get("key"), str) and len(value["key"]) == 64
            and value.get("scopeKey") == identity(*[job.get(k) for k in ("accountId", "symbol", "worldId")])
            and isinstance(ids, list) and 1 <= len(ids) <= 5
            and all(isinstance(k, str) and k for k in ids) and ids == sorted(set(ids))
            and job.get("taskId") == identity(VERSION, "research-plan", value["key"]))
