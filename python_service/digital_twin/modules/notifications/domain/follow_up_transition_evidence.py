"""One verified follow-up view for AI admission and final notification policy."""

from typing import Mapping

def _mapping(value):
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value):
    return " ".join(str(value or "").strip().split())


def follow_up_transition_evidence(context):
    from digital_twin.modules.outcomes.contracts import follow_up_is_registered
    from digital_twin.modules.decisions.contracts import canonical_investment_timestamp
    packet = _mapping(_mapping(context).get("decisionContinuityPacket"))
    conditions = list(packet.get("followUpConditions") or [])
    previous_analysis = _mapping(context.get("previousInvestmentAIInsightEpisode"))
    analyzed_at = canonical_investment_timestamp(previous_analysis.get("createdAt"))
    for previous in (previous_analysis, _mapping(context.get("previousDeliveredInvestmentAIInsightEpisode"))):
        if (previous.get("accountId") != context.get("accountId")
                or previous.get("symbol") != (context.get("rawSymbol") or context.get("symbol"))):
            continue
        for row in previous.get("followUpConditions") or []:
            if not isinstance(row, Mapping):
                continue
            transition_at = canonical_investment_timestamp(row.get("transitionAt"))
            if (follow_up_is_registered(row) and row.get("accountId") == previous.get("accountId")
                    and row.get("symbol") == previous.get("symbol")
                    and transition_at and (not analyzed_at or transition_at > analyzed_at)):
                conditions.append(row)
    verified = [
        dict(item)
        for item in conditions
        if isinstance(item, Mapping)
        and bool(item.get("transitionVerified"))
        and _text(item.get("transitionAt"))
        and _text(item.get("status")).lower() in {"satisfied", "invalidated", "expired"}
        and (
            _text(item.get("status")).lower() == "expired"
            or (item.get("previousMatched") is False and item.get("currentMatched") is True)
            or (item.get("transitionKind") == "confirmed-false-to-true"
                and follow_up_is_registered(item)
                and int(item.get("confirmationCount") or 0) >= max(2, int(_mapping(item.get("observationPolicy")).get("requiredConfirmations") or 2)))
        )
    ]
    return list({str(item.get("conditionId") or ""): item for item in verified}.values())
