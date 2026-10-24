"""Question-owned reporting periods constrain discovery, never certify an answer."""
from datetime import date
import re

VERSION = "question-collection-scope-v1"


def required_report_periods(tasks):
    periods = set()
    for task in tasks:
        explicit = task.get("requiredPeriodEnds") or []
        if not isinstance(explicit, list) or len(explicit) > 8:
            raise ValueError("invalid required reporting periods")
        contract = (task.get("sourceRequirements") or {}).get("originalContract") or {}
        text = str(contract.get("period") or "")
        for value in explicit:
            try:
                if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
                    periods.add(value)
            except ValueError:
                continue
        for match in re.finditer(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", text):
            value = match.group()
            try:
                day = date.fromisoformat(value)
            except ValueError:
                continue
            # Skip an explicit range start; fiscal quarters may end on any
            # day, so a standalone ISO period must not require month end.
            if not re.match(r"\s*(?:부터|에서|[~–—]|to\b|-\s*\d{4}-)", text[match.end():], re.I):
                periods.add(value)
    if len(periods) > 8:
        raise ValueError("reporting period scope exceeds collection bounds")
    return sorted(periods)


def collection_scope(tasks, selected_periods, collected_periods=()):
    required = required_report_periods(tasks)
    if not required:
        return {}
    selected, collected = set(selected_periods), set(collected_periods)
    return {"version": VERSION, "requiredPeriodEnds": required,
            "selectedPeriodEnds": sorted(selected.intersection(required)),
            "collectedPeriodEnds": sorted(collected.intersection(required)),
            "unselectedPeriodEnds": sorted(set(required) - selected),
            "uncollectedPeriodEnds": sorted(set(required) - collected),
            "authority": "collection-coverage-only", "answerCompletion": "requires-independent-review"}
