"""Immutable observation evidence may request research, never qualify a rule."""
from copy import deepcopy
from datetime import datetime, timezone

from digital_twin.modules.decisions.contracts import InvestmentQuestion, stable_id
from digital_twin.modules.reasoning.contracts import content_hash, validate_evidence_packet


OBSERVATION_DEVELOPMENT_VERSION = "observation-development-v1"


def observation_development_request(task, result):
    questions = result.get("developmentQuestions", [])
    if not questions:
        return {}
    if (len(questions) != 1 or not isinstance(questions[0], str)
            or not 8 <= len(questions[0].strip()) <= 500):
        raise ValueError("invalid observation development question")
    packet = result["input"]
    if any(not task.get(key) or packet.get(key) != task[key]
           for key in ("accountId", "symbol", "worldId", "taskId")):
        raise ValueError("observation development ownership mismatch")
    context = {
        "version": OBSERVATION_DEVELOPMENT_VERSION,
        "taskId": task["taskId"], "executionInputId": result.get("executionInputId", ""),
        "inputFingerprint": result.get("inputFingerprint", ""),
        "packet": deepcopy(packet), "evidenceIds": list(dict.fromkeys(list(result.get("evidenceIds", [])) + [
            key for row in result.get("questionResolutions", []) if row["disposition"] == "experiment" for key in row["evidenceIds"]])),
        "analysis": {key: deepcopy(result.get(key)) for key in
                     ("hypothesis", "counterEvidence", "comparison", "followUpEvaluations")},
        "sourceQuestions": [deepcopy(row["sourceQuestion"]) for row in result.get("questionResolutions", [])
                            if row["disposition"] == "experiment"],
        "authority": "proposal-only", "empiricalQualification": "unverified",
    }
    context["fingerprint"] = content_hash(context)
    validate_observation_development_context(context, task["accountId"], task["symbol"])
    captured = datetime.fromisoformat(packet["capturedAt"].replace("Z", "+00:00"))
    if captured.tzinfo is None:
        raise ValueError("observation development requires a source timezone")
    # One immutable request per subject/day, even if the AI rephrases a question
    # or later prices change. A retry retains the first input and proposal budget.
    fingerprint = content_hash(["observation-development-scope-v2", task["accountId"],
                                task["symbol"], task["worldId"], captured.astimezone(timezone.utc).date().isoformat()])
    question = InvestmentQuestion.create(questions[0], task["symbol"], task.get("name", ""),
        task["accountId"], asked_at=packet["capturedAt"], source="ai-control-observation")
    return {"requestId": stable_id("observation-development", fingerprint),
        "gapFingerprint": fingerprint, "accountId": task["accountId"], "symbol": task["symbol"],
        "source": "ai-control-observation", "question": question.to_dict(),
        "observationContext": context, "hypothesisSet": {}, "researchRun": {}, "relationContext": {}}


def validate_observation_development_context(context, account_id, symbol):
    if (not isinstance(context, dict) or context.get("version") != OBSERVATION_DEVELOPMENT_VERSION
            or context.get("authority") != "proposal-only" or context.get("empiricalQualification") != "unverified"
            or not all(context.get(key) for key in ("executionInputId", "inputFingerprint", "taskId"))):
        raise ValueError("invalid observation development contract")
    if context.get("fingerprint") != content_hash({key: value for key, value in context.items() if key != "fingerprint"}):
        raise ValueError("observation development evidence changed")
    packet = context["packet"]
    validate_evidence_packet(packet)
    if (packet["accountId"] != account_id or packet["symbol"] != symbol
            or packet.get("taskId") != context["taskId"]):
        raise ValueError("observation development subject mismatch")
    known = {fact["id"] for fact in packet["facts"] if fact.get("id")}
    cited = context.get("evidenceIds")
    if not isinstance(cited, list) or not cited or any(not isinstance(item, str) or item not in known for item in cited):
        raise ValueError("observation development requires captured evidence")
    usable = {fact["id"] for fact in packet["facts"] if fact["id"] in cited
              and fact.get("judgementEvidenceUsable") is not False and fact.get("valuationDecisionEligible") is not False}
    if not usable:
        raise ValueError("observation development requires usable evidence")
    return usable
