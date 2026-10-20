"""Prioritize the recorded gap without changing the original answer contract."""
import re

VERSION = "source-research-focus-v1"


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
