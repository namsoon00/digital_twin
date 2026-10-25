"""Review-only comparison of a frozen research range with a later report."""
from copy import deepcopy
from datetime import datetime, timezone
import math

VERSION = "quantitative-business-checkpoint-v1"
TERMINAL = {"within-expected-range", "below-expected-range", "above-expected-range"}


def _clock(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc) if len(str(value)) == 10 else None
    except (ValueError, TypeError):
        return None


def evaluate_research_expectation(check, metrics, registered_at, captured_at, expires_at):
    baseline = check["baseline"]
    result = {"contractVersion": VERSION, "checkpointId": check["checkpointId"], "metric": baseline["metric"],
        "status": "awaiting-report", "qualification": "metric-observation-only",
        "expectedLow": check["expectedLow"], "expectedHigh": check["expectedHigh"],
        "targetPeriodStart": check["targetPeriodStart"], "targetPeriodEnd": check["targetPeriodEnd"],
        "expectationKind": check["expectationKind"], "baseline": deepcopy(baseline)}
    registered, captured, expires = map(_clock, (registered_at, captured_at, expires_at))
    if not all((registered, captured, expires)) or captured < registered:
        return {**result, "status": "invalid-clock"}
    candidates = []
    for metric in metrics:
        if any(metric.get(field) != baseline.get(field) for field in ("symbol", "metric", "frequency", "basis")):
            continue
        if any(metric.get(field) != check["target" + field[0].upper() + field[1:]] for field in ("periodStart", "periodEnd")):
            continue
        published, observed = _clock(metric.get("publishedAt")), _clock(metric.get("observedAt"))
        if not published or not observed or not (registered < published <= min(captured, expires)
                                                and registered < observed <= min(captured, expires)):
            continue
        if metric["periodEnd"] <= registered.date().isoformat() or metric.get("reportObservationId") == baseline.get("reportObservationId"):
            continue
        if isinstance(metric.get("value"), bool) or not isinstance(metric.get("value"), (int, float)) or not math.isfinite(metric["value"]):
            continue
        candidates.append(metric)
    if not candidates:
        return {**result, "status": "expired-unobserved"} if captured >= expires else result
    current = min(candidates, key=lambda item: (max(_clock(item["publishedAt"]), _clock(item["observedAt"])), item["factId"]))
    low, high, actual = check["expectedLow"], check["expectedHigh"], current["value"]
    status = "below-expected-range" if actual < low else "above-expected-range" if actual > high else "within-expected-range"
    return {**result, "status": status, "current": deepcopy(current), "observedAt": captured_at,
        "rangeDeviation": actual-low if actual < low else actual-high if actual > high else 0,
        "changeFromBaseline": actual-baseline["value"],
        "causalValidation": "not-established", "predictionQualification": "not-empirically-qualified"}
