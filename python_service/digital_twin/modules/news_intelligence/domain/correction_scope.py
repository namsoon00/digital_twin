"""A similar sentence in a different reporting period is not a corrected fact."""
from .dart_question_filings import period_end


def same_correction_scope(current, previous):
    if not current.symbol or current.symbol != previous.symbol:
        return False
    def scope(item):
        raw = item.raw_payload or {}
        period = raw.get("reportPeriodEnd") or raw.get("periodEnd") or raw.get("reportDate")
        if not period and item.kind == "disclosure":
            period = period_end(raw.get("reportName") or item.title)
        return period or ""
    current_period, previous_period = scope(current), scope(previous)
    # News without reporting metadata retains its existing similarity policy.
    # Once either filing has a period, missing metadata cannot establish parity.
    return current_period == previous_period
