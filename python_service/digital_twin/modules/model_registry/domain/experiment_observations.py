"""Point-in-time research inputs, not investment judgement or collector authority."""

from copy import deepcopy
from datetime import timedelta
import math

from .ontology_evolution import fingerprint, timestamp


OBSERVATION_CONTRACT = "experiment-observations-v1"
DATASET_CONTRACT = "experiment-dataset-v1"


def observation_requirements(rule, baseline_rule, *, cadence_seconds=180):
    """Preserve authored needs and derive future measurements from the actual claims."""
    from .ontology_rulebox_contracts import GraphInferenceRule

    inputs = {"source-packet": {"metric": "source-packet", "label": "판단 시점 원천 자료",
                               "lookbackMinutes": 0, "minimumSamples": 1,
                               "cadenceSeconds": cadence_seconds, "maximumDelayMinutes": 10}}
    outcomes = {}
    for source in (rule, baseline_rule):
        extra = (source.get("model_input_contract") or {}).get("observationRequirements") or []
        if not isinstance(extra, list) or len(extra) > 24:
            raise ValueError("observationRequirements must contain at most 24 requirements")
        for raw in extra:
            if not isinstance(raw, dict) or not isinstance(raw.get("metric"), str) or not raw["metric"].strip():
                raise ValueError("Every observation requirement needs an explicit metric")
            row = {"metric": raw["metric"].strip()[:96], "label": str(raw.get("label") or raw["metric"])[:240]}
            for key, default, minimum, maximum in (
                ("lookbackMinutes", 0, 0, 525600), ("minimumSamples", 1, 1, 10000),
                ("cadenceSeconds", cadence_seconds, 1, 86400), ("maximumDelayMinutes", 10, 0, 10080),
            ):
                value = raw.get(key, default)
                if type(value) is not int or not minimum <= value <= maximum:
                    raise ValueError("Invalid observation requirement: " + key)
                row[key] = value
            if row["metric"] == "source-packet":
                raise ValueError("The source-packet identity requirement cannot be overridden")
            # Different windows for the same metric remain separate requirements.
            inputs[fingerprint(row)] = row
        claim = GraphInferenceRule.from_dict(source).resolved_claim_contract.to_dict()
        for criterion in (claim.get("outcomeContract") or {}).get("criteria") or []:
            outcomes[fingerprint(criterion)] = deepcopy(criterion)
    value = {"contract": OBSERVATION_CONTRACT, "inputs": list(inputs.values()),
             "collectorCadenceSeconds": cadence_seconds,
             "outcomes": list(outcomes.values()), "knowledgePolicy": "recorded-before-source-cutoff",
             "replayScope": "source-packets-and-observed-claims"}
    value["fingerprint"] = fingerprint(value)
    return value


def validate_requirements(value):
    if (value.get("contract") != OBSERVATION_CONTRACT or not value.get("inputs")
            or fingerprint({k: v for k, v in value.items() if k != "fingerprint"}) != value.get("fingerprint")):
        raise ValueError("Observation requirement fingerprint mismatch")
    return value


def assess_requirement(requirement, capability, samples, *, as_of, known_at, historical=False):
    """Availability and event clocks must both pass; missing is never numeric zero."""
    end, known = timestamp(as_of), timestamp(known_at)
    if not end or not known or end > known:
        raise ValueError("Observation cutoffs must be timezone aware and causal")
    row = {**requirement, "sampleCount": 0, "state": "future-collection", "samples": []}
    if not capability or not capability.get("supported"):
        return {**row, "state": "unsupported", "reason": "collector-not-supported"}
    if requirement["lookbackMinutes"] > capability.get("retentionMinutes", 0):
        return {**row, "state": "unsupported", "reason": "window-exceeds-retained-history"}
    start = end - timedelta(minutes=requirement["lookbackMinutes"] or requirement["maximumDelayMinutes"])
    selected = {}
    for raw in samples:
        observed, ingested = timestamp(raw.get("observedAt")), timestamp(raw.get("recordedAt"))
        value = raw.get("value")
        if (not observed or not ingested or observed > end or ingested > known or ingested < observed
                or observed < start or value is None or isinstance(value, bool)
                or (isinstance(value, (int, float)) and not math.isfinite(value))
                or raw.get("unit") != capability.get("unit")):
            continue
        # Preserve the last revision actually available at the cutoff, never a later correction.
        prior = selected.get(observed)
        if not prior or timestamp(prior["recordedAt"]) < ingested:
            selected[observed] = deepcopy(raw)
    ordered = [selected[key] for key in sorted(selected)]
    reason = "insufficient-samples"
    ready = len(ordered) >= requirement["minimumSamples"]
    if ready:
        tolerance = timedelta(seconds=requirement["cadenceSeconds"])
        if requirement["lookbackMinutes"] and timestamp(ordered[0]["observedAt"]) > start + tolerance:
            ready, reason = False, "incomplete-window"
        elif end - timestamp(ordered[-1]["observedAt"]) > timedelta(minutes=requirement["maximumDelayMinutes"]):
            ready, reason = False, "stale-observation"
        elif any(timestamp(b["observedAt"]) - timestamp(a["observedAt"]) > tolerance
                 for a, b in zip(ordered, ordered[1:])):
            ready, reason = False, "cadence-gap"
    return {**row, "state": "ready" if ready else "historical-unrecoverable" if historical else "future-collection",
            "reason": "point-in-time-coverage-complete" if ready else reason,
            "sampleCount": len(ordered), "samples": ordered, "unit": capability.get("unit"),
            "collector": capability.get("collector"), "collectorCadenceSeconds": capability.get("cadenceSeconds")}


def freeze_dataset(plan, source, coverage, *, captured_at):
    requirements = validate_requirements(plan["observationRequirements"])
    if source.get("accountId") != plan["accountId"] or plan["symbol"] not in source.get("symbols", []):
        raise ValueError("Experiment source account or symbol mismatch")
    if (not timestamp(captured_at) or not timestamp(source.get("generatedAt")) or not timestamp(source.get("recordedAt"))
            or timestamp(source["generatedAt"]) > timestamp(source["recordedAt"])
            or timestamp(source["recordedAt"]) > timestamp(captured_at)):
        raise ValueError("Experiment source availability is unverified")
    value = {"contract": DATASET_CONTRACT, "accountId": plan["accountId"], "symbol": plan["symbol"],
             "requirementsFingerprint": requirements["fingerprint"], "source": deepcopy(source),
             "coverage": deepcopy(coverage), "asOf": source["generatedAt"], "knownAt": source["recordedAt"],
             "replayScope": requirements["replayScope"]}
    value["fingerprint"] = fingerprint(value)
    value["datasetId"] = "experiment-input:" + value["fingerprint"][:40]
    return value


def validate_dataset(value):
    expected = fingerprint({k: v for k, v in value.items() if k not in {"fingerprint", "datasetId"}})
    if (value.get("contract") != DATASET_CONTRACT or expected != value.get("fingerprint")
            or value.get("datasetId") != "experiment-input:" + expected[:40]):
        raise ValueError("Experiment input payload changed")
    return value
