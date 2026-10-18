"""Conservative, deterministic admission before expensive source/model work."""
from copy import deepcopy
import re


VERSION = "research-repeat-v1"
MAX_MATERIALS = 256
MAX_RESEARCH_ATTEMPTS = 6
WAIT_MINUTES = 360
WAIT_REASON = "새 원문이나 수치가 없어 같은 조사와 AI 계획 호출을 보류합니다. 확인한 답변은 유지합니다."


def materials(case):
    state = case.get("researchSourceState") or {}
    answer = case.get("latestSourceAnswer") or case.get("lastResearch", {}).get("result", {}).get("documentaryAnswer") or {}
    return sorted(set(state.get("fingerprints", [])) | {
        source["materialFingerprint"] for source in answer.get("sources", []) if source.get("materialFingerprint")})


def initialize(case):
    if (case.get("researchRepeat") or {}).get("version") == VERSION:
        return False
    current = materials(case)
    # Existing plans/repeated investigations are parked on upgrade. Changing
    # policy is not a new fact and must not trigger another paid migration pass.
    legacy = bool(case.get("researchPlan") or case.get("researchAttempts", 0) >= 2)
    case["researchRepeat"] = {"version": VERSION, "planned": legacy,
        "plannedMaterials": current if legacy else [], "scheduled": bool(case.get("researchAttempts")),
        "scheduledMaterials": current if case.get("researchAttempts") else [],
        "authorizedTaskId": "", "legacyBaseline": legacy}
    return True


def admission(case, action):
    guard = case.get("researchRepeat") or {}
    if (case.get("answerResolution") or {}).get("status") == "answered":
        return "answered"
    if case.get("researchAttempts", 0) >= MAX_RESEARCH_ATTEMPTS:
        return "research-attempt-limit"
    if (case.get("researchSourceState") or {}).get("status") == "unavailable":
        return "source-state-unavailable"
    known = set(guard.get(action + "Materials", []))
    current = set(materials(case))
    if len(known | current) > MAX_MATERIALS:
        return "research-material-limit"
    if guard.get(action) and not current - known:
        return "unchanged-research-material"
    return ""


def remember(case, action, task_id=""):
    initialize(case)
    guard = case["researchRepeat"]
    guard[action] = True
    guard[action + "Materials"] = sorted(set(guard.get(action + "Materials", [])) | set(materials(case)))[:MAX_MATERIALS]
    guard.pop("waitReason", None)
    if action == "scheduled":
        guard["authorizedTaskId"] = task_id


def planning_due(case, now):
    from .research_work import needs_research_plan
    return (needs_research_plan(case) and case.get("nextCheckAt", "") <= now
            and not admission(case, "planned"))


def planning_memory(case):
    """No fingerprint ledgers or duplicate historical answers in a paid prompt."""
    result = deepcopy({key: value for key, value in case.items()
                       if key not in {"researchRepeat", "researchSourceState", "answerMemory", "origin"}})
    returned = result.get("lastResearch", {}).get("result", {})
    if returned.get("documentaryAnswer") == result.get("latestSourceAnswer"):
        returned.pop("documentaryAnswer", None)
    result["planningMemoryVersion"] = "research-planning-memory-v1"
    return result


def observation_key(packet, research):
    """Agenda bookkeeping is not a new input to an accepted observation.

    Keep source-owned evidence identity and real question/answer/resolution
    changes. Only this owner's timer, revision and self-authored review fields
    are excluded. The original frozen input fingerprint remains unchanged.
    """
    from digital_twin.modules.reasoning.contracts import evidence_change_identity
    from .planning import identity
    operational = {"revision", "nextCheckAt", "reviewDue", "reason", "lastAssessment",
                   "researchPlan", "researchRepeat", "researchSourceState", "answerMemory", "evidenceProgress"}
    work = [{key: value for key, value in row.items() if key not in operational}
            if row.get("kind") == "brain-case" else row for row in research]
    source = evidence_change_identity(packet, work, packet.get("questionsToCheck", []))
    if packet.get("researchKnowledge"):
        from digital_twin.modules.research_knowledge.contracts import knowledge_context_identity
        if packet.get("researchExecutionPolicy"):
            return identity(VERSION, source, knowledge_context_identity(packet["researchKnowledge"]), packet["researchExecutionPolicy"])
        return identity(VERSION, source, knowledge_context_identity(packet["researchKnowledge"]))
    return identity(VERSION, source)


def preserve_answer(case, answer):
    """A failed later attempt does not erase a previously cited partial answer."""
    previous = case.get("latestSourceAnswer") or {}
    if previous.get("status") in {"partial", "answered"} and answer.get("status") == "unavailable":
        return False
    case["latestSourceAnswer"] = deepcopy(answer)
    return True


def answer_changed(previous, answer):
    if answer.get("status") == "unavailable":
        return False
    if answer.get("status") == "answered" and previous.get("status") != "answered":
        return True
    previous_material = {row.get("materialFingerprint") for row in previous.get("sources", [])}
    return any(row.get("materialFingerprint") and row["materialFingerprint"] not in previous_material
               for row in answer.get("sources", []))


def stalled_question(active, request, contract=None):
    """Defer creation, never merge possibly distinct questions or reset limits."""
    requested_sources = set((request or {}).get("sourceTypes") or ["official-filing", "news"])
    for case in active:
        guard = case.get("researchRepeat") or {}
        stalled = guard.get("waitReason") or (not guard and case.get("researchPlan"))
        if case.get("capability") != "research" or not stalled:
            continue
        if (case.get("answerResolution") or {}).get("status") == "answered":
            continue
        old_period = (case.get("questionContract") or {}).get("period")
        new_period = (contract or {}).get("period")
        old_dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", old_period or ""))
        new_dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", new_period or ""))
        if old_dates and new_dates and old_dates.isdisjoint(new_dates):
            continue
        if requested_sources.intersection((case.get("researchRequest") or {}).get("sourceTypes") or ["official-filing", "news"]):
            return case["caseId"]
    return ""
