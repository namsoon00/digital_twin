"""Exact metric projections of captured reports; never generated summaries."""
from copy import deepcopy
from .observation_evidence import EvidenceContractError, content_hash

MAPS = ("reportedValues", "metricUnits", "metricProvenance", "comparisonEvidence", "derivedMetricEvidence")


def report_metrics(fact):
    return sorted({key for name in MAPS for key in fact.get(name, {})})


def report_view(fact, metrics):
    available = report_metrics(fact)
    if (fact.get("kind") != "company-financial-state" or not isinstance(metrics, list)
            or not 1 <= len(metrics) <= 12 or any(not isinstance(x, str) or x not in available for x in metrics)
            or len(set(metrics)) != len(metrics)):
        raise EvidenceContractError("invalid report metric selection")
    chosen = set(metrics)
    result = deepcopy(fact)
    for key in set(available) - chosen:
        result.pop(key, None)
    for name in MAPS:
        if name in result:
            result[name] = {k: v for k, v in result[name].items() if k in chosen}
    result["evidenceView"] = {"version": "exact-report-metrics-v1", "sourceFactHash": content_hash(fact),
        "metrics": sorted(chosen), "omittedMetrics": sorted(set(available) - chosen),
        "authority": "exact-field-projection", "omissionMeaning": "not-selected-not-missing"}
    return result
