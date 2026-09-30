"""Observe a later comparable annual report without interpreting a trading action."""

from digital_twin.modules.news_intelligence.contracts import annual_financial_observations
from .follow_up_tracking import finite_number, observation_time, follow_up_is_registered


FINANCIAL_REPORT_WATCH_VERSION = "annual-report-follow-up-v1"


def financial_report_baseline(facts, field, symbol):
    observations = facts.get("annualFinancialObservations")
    if not isinstance(observations, dict):
        observations = annual_financial_observations(
            facts.get("companyContext") or {}, symbol,
            facts.get("financialObservationCutoffAt") or facts.get("updatedAt") or facts.get("sourceAsOf") or "",
        )
    value = observations.get(field) or {}
    return dict(value) if (isinstance(value, dict) and value.get("symbol") == symbol
                           and field == "financial.annual." + str(value.get("metric") or "")
                           and finite_number(value.get("value")) is not None) else {}


def report_watch_contract(raw, baseline):
    # The observed report value supplies the threshold; AI cannot invent a target.
    if (not baseline or raw.get("operator") not in {"<", ">"}
            or finite_number(raw.get("threshold")) != finite_number(baseline.get("value"))):
        return {}
    return {"version": FINANCIAL_REPORT_WATCH_VERSION, "baseline": baseline,
            "expectedFrequency": "annual", "minimumPeriodDays": 270, "maximumPeriodDays": 430,
            "investmentActionAuthority": False}


def valid_financial_report_watch(condition):
    contract = condition.get("financialReportWatch") or {}
    baseline = contract.get("baseline") or {}
    return bool(contract.get("version") == FINANCIAL_REPORT_WATCH_VERSION
                and str(condition.get("field") or "") == "financial.annual." + str(baseline.get("metric") or "")
                and baseline.get("symbol") == condition.get("symbol")
                and baseline.get("reportObservationId") and baseline.get("sourceReferences")
                and report_watch_contract(condition, baseline))


def observe_financial_report(row, facts, stamp):
    contract = row.get("financialReportWatch") or {}
    baseline = contract.get("baseline") or {}
    if not valid_financial_report_watch(row) or not follow_up_is_registered(row):
        row["observationStatus"] = "unregistered-report-contract"
        return False
    current = financial_report_baseline(facts, str(row.get("field") or ""), str(row.get("symbol") or ""))
    before, after = observation_time(baseline.get("periodEnd")), observation_time(current.get("periodEnd"))
    now, observed = observation_time(stamp), observation_time(current.get("observedAt"))
    published = observation_time(current.get("publishedAt"))
    registered = observation_time((row.get("registration") or {}).get("registeredAt"))
    if not all((before, after, now, observed, published, registered)):
        row["observationStatus"] = "waiting-comparable-report"
        return False
    if (after <= before or current.get("reportObservationId") == baseline.get("reportObservationId")
            or current.get("reportObservationId") == row.get("lastReportObservationId")):
        return False
    if (not 270 <= (after - before).days <= 430 or max(observed, published) > now
            or observed < registered or published.date() < registered.date()):
        row["observationStatus"] = "waiting-new-comparable-report"
        return False
    keys = ("provider", "currency", "scope", "durationBasis")
    if any(not baseline.get("basis", {}).get(key) or baseline["basis"][key] != current.get("basis", {}).get(key) for key in keys):
        row["observationStatus"] = "incompatible-report-basis"
        return False
    baseline_start, current_start = observation_time(baseline.get("periodStart")), observation_time(current.get("periodStart"))
    if (not baseline_start or not current_start
            or abs((after - current_start).days - (before - baseline_start).days) > 8):
        row["observationStatus"] = "incompatible-report-duration"
        return False
    value, threshold = finite_number(current.get("value")), finite_number(row.get("threshold"))
    if value is None or threshold is None:
        return False
    matched = value < threshold if row.get("operator") == "<" else value > threshold
    row.update({
        "previousValue": row.get("currentValue"), "currentValue": value,
        "previousMatched": row.get("currentMatched", False), "currentMatched": matched,
        "lastReportObservationId": current["reportObservationId"], "reportObservation": current,
        "lastSourceAsOf": current["publishedAt"], "observedAt": stamp,
        "observationStatus": "report-condition-reached" if matched else "report-condition-not-reached",
        "confirmationCount": 1, "transitionVerified": False,
    })
    return matched
