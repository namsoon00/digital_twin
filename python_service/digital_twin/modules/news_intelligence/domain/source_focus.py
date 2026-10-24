"""Prioritize the recorded gap without changing the original answer contract."""
import re
from copy import deepcopy
import hashlib
import json

VERSION = "source-research-focus-v1"
REQUIREMENTS_VERSION = "original-source-requirements-v1"


def source_requirements(contract, missing, previous_sources=()):
    """Exact criterion and atomic gap IDs, never a relaxed substitute question."""
    if not isinstance(contract, dict) or set(contract) != {"target", "period", "answerCriterion"}:
        raise ValueError("invalid original source contract")
    if any(not isinstance(value, str) or not 2 <= len(value.strip()) <= 300 for value in contract.values()):
        raise ValueError("invalid original source criterion")
    gaps = source_research_focus(missing)["missingRequirements"] if missing else []
    digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"version": REQUIREMENTS_VERSION, "originalContract": deepcopy(contract),
            "contractHash": digest(contract),
            "contractChecks": [{"requirementId": digest([contract, key]), "field": key,
                                "criterion": contract[key], "status": "unverified"}
                               for key in ("target", "period", "answerCriterion")],
            "items": [{"requirementId": digest([contract, gap]), "requirement": gap,
                       "status": "unverified"} for gap in gaps],
            "previousEvidenceIds": list(dict.fromkeys(row["evidenceId"] for row in previous_sources
                                                     if isinstance(row, dict) and row.get("evidenceId")))[:8],
            "verificationOrder": ["issuer-and-official-source", "publication-and-report-period", "exact-answer-passage"],
            "numericChecksWhenUsed": ["unit-and-currency", "reporting-scope", "quarter-versus-cumulative"],
            "authority": "collection-plan-not-answer"}


def source_research_focus(requirements):
    if (not isinstance(requirements, list) or not 1 <= len(requirements) <= 8
            or any(not isinstance(row, str) or not 1 <= len(row.strip()) <= 1000
                   for row in requirements)):
        raise ValueError("invalid source research focus")
    return {"version": VERSION, "missingRequirements": list(dict.fromkeys(requirements))}


def focused_tasks(tasks):
    selected = []
    for task in tasks:
        focus = task.get("sourceFocus") or {}
        if focus.get("version") == VERSION:
            validated = source_research_focus(focus.get("missingRequirements"))
            selected.append({"question": " ".join(validated["missingRequirements"]),
                             "queryTerms": task.get("queryTerms", [])})
    return selected


def needs_filing_history(tasks):
    selected = focused_tasks(tasks) or tasks
    return any(re.search(r"정정|재작성|제출\s*이력|10-[QK]/A|amend|restat|filing\s*history",
                        str(row.get("question") or "") + " " + " ".join(row.get("queryTerms") or []), re.I)
               for row in selected)
