"""Reject malformed model collections before any partial proposal is saved."""
from copy import deepcopy

LIST_LIMITS = {"causalPath": 12, "supportingEvidenceIds": 20, "counterEvidenceIds": 20,
               "requiredEvidenceTypes": 12, "invalidationConditions": 8}


def proposal_rows(rows):
    if not isinstance(rows, list) or len(rows) > 3:
        raise ValueError("hypothesis proposals must be a bounded array")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("hypothesis proposal must be an object")
        item = deepcopy(row)
        for key, limit in LIST_LIMITS.items():
            values = item.get(key, [])
            if (not isinstance(values, list) or len(values) > limit
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 1000 for value in values)):
                raise ValueError("hypothesis proposal requires string array: " + key)
            if key == "causalPath" and len(values) >= 4 and all(len(value.strip()) == 1 for value in values):
                raise ValueError("hypothesis causal path contains characters instead of stages")
            item[key] = [value.strip() for value in values]
        result.append(item)
    return result
