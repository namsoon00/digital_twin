"""Registration receipts and observation noise controls, never trade decisions."""

import hashlib
import math
from datetime import datetime, timedelta, timezone

from digital_twin.modules.market_data.contracts import market_signal_transition_policies, observable_follow_up_fields


FOLLOW_UP_REGISTRATION_VERSION = "follow-up-registration-v1"
FOLLOW_UP_OBSERVATION_POLICY_VERSION = "follow-up-observation-policy-v1"
FOLLOW_UP_ADMISSION_VERSION = "ai-follow-up-admission-v1"
LIVE_FOLLOW_UP_FIELDS = frozenset({
    "currentPrice", "priceChangeRate", "ma5Distance", "ma20Distance", "ma60Distance",
    "volume", "volumeRatio", "timeAdjustedVolumeRatio", "tradeStrength", "buyVolume", "sellVolume",
    "bidAskImbalance", "orderbookBidVolume", "orderbookAskVolume",
    "foreignNetVolume", "institutionNetVolume", "individualNetVolume", "smartMoneyNetVolume",
})


def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def observation_time(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def ai_follow_up_registration_admission(episode):
    reconciliation = episode.reconciliation or {}
    valid = bool(episode.ai_authored and episode.publication_contract_passed
                 and not episode.contract_failure_code and reconciliation.get("status") == "reconciled")
    duplicate = bool(
        reconciliation.get("reasonCode") == "unchanged_investment_insight"
        and reconciliation.get("notificationDecision") == "suppress"
        and (reconciliation.get("deliveryOutcome") or {}).get("status") == "web-only"
    )
    accepted = reconciliation.get("notificationDecision") == "send"
    eligible = valid and (accepted or duplicate)
    return {"version": FOLLOW_UP_ADMISSION_VERSION, "eligible": eligible,
            "reason": "unchanged-valid-insight" if eligible and duplicate else
                      "accepted-insight" if eligible else "unaccepted-insight",
            "preserveExisting": bool(eligible and duplicate)}


def follow_up_semantic_key(condition):
    return (str(condition.get("field") or ""), str(condition.get("operator") or ""),
            finite_number(condition.get("threshold")), str(condition.get("purpose") or "switch"))


def follow_up_thesis_key(insight):
    return str((insight.get("insightAssessment") or {}).get("thesisKey")
               or (insight.get("insightTransition") or {}).get("currentThesisKey") or "")


def follow_up_is_registered(condition):
    row = condition if isinstance(condition, dict) else {}
    receipt = row.get("registration") if isinstance(row.get("registration"), dict) else {}
    return bool(
        receipt.get("version") == FOLLOW_UP_REGISTRATION_VERSION
        and receipt.get("state") == "registered"
        and receipt.get("conditionId") == row.get("conditionId")
        and receipt.get("episodeId") == row.get("episodeId")
        and receipt.get("accountId") == row.get("accountId")
        and receipt.get("symbol") == row.get("symbol")
        and all(receipt.get(key) for key in ("registrationId", "episodeId", "accountId", "symbol", "registeredAt"))
    )


def follow_up_observation_policy(condition, settings=None):
    policies = market_signal_transition_policies(settings)
    field = str(condition.get("field") or "")
    family = {
        "currentPrice": "price", "priceChangeRate": "price",
        "ma5Distance": "trend-cross", "ma20Distance": "trend-cross", "ma60Distance": "trend-cross",
        "bidAskImbalance": "orderbook", "tradeStrength": "trade-strength",
        "volumeRatio": "volume", "timeAdjustedVolumeRatio": "volume",
    }.get(field)
    policy = policies.get(family, policies["price"])
    baseline = finite_number(condition.get("baselineValue"))
    threshold = finite_number(condition.get("threshold"))
    minimum = 0.0
    unit = ""
    if family and baseline is not None and threshold is not None:
        if field == "currentPrice":
            band = abs(baseline) * policy.enter_value / 100
            unit = "price"
        elif family in {"orderbook", "trade-strength", "volume"}:
            band = abs(policy.enter_value - policy.exit_value)
            unit = policy.unit
        else:
            band = policy.enter_value
            unit = "percentage-points"
        # Near-current thresholds must not turn rounding noise into a new event.
        minimum = band if abs(threshold - baseline) < band else 0.0
    return {
        "version": FOLLOW_UP_OBSERVATION_POLICY_VERSION,
        "sourcePolicy": policy.to_dict(),
        "requiredConfirmations": max(2, policy.confirmations),
        "minimumBaselineChange": minimum,
        "unit": unit,
        "distinctSourceObservations": True,
        "rebaseOnFirstObservation": True,
        "investmentActionAuthority": False,
    }


def registered_follow_up(condition, *, episode_id, account_id, symbol, registered_at,
                         owner_kind="decision", settings=None):
    row = dict(condition)
    if owner_kind == "ai-insight":
        source_id = str(row.get("sourceConditionId") or row.get("conditionId") or "")
        identity = "|".join((account_id, symbol, episode_id, source_id))
        row["sourceConditionId"] = source_id
        row["conditionId"] = "ai-insight-follow-up:" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        row["observationPolicy"] = follow_up_observation_policy(row, settings)
        row["trackingBaselineCaptured"] = False
        created = observation_time(registered_at)
        expiry = observation_time(row.get("expiresAt"))
        if created:
            maximum = created + timedelta(days=7)
            row["expiresAt"] = min(expiry, maximum).isoformat().replace("+00:00", "Z") if expiry else maximum.isoformat().replace("+00:00", "Z")
    row.update({"episodeId": episode_id, "accountId": account_id, "symbol": symbol, "ownerKind": owner_kind})
    receipt_id = hashlib.sha256((episode_id + "|" + str(row.get("conditionId"))).encode()).hexdigest()[:32]
    row["registration"] = {
        "version": FOLLOW_UP_REGISTRATION_VERSION, "registrationId": "follow-up-registration:" + receipt_id,
        "state": "registered", "conditionId": row.get("conditionId"), "episodeId": episode_id,
        "accountId": account_id, "symbol": symbol, "ownerKind": owner_kind, "registeredAt": registered_at,
    }
    row.update({"trackingStatus": "active", "trackingOwner": "system", "notificationOnTransition": True})
    return row


def follow_up_source_time(condition, facts):
    """Use the field's supplier clock, not the monitor's polling clock."""
    profile = facts.get("marketEvidenceProfile") or {}
    field = str(condition.get("field") or "")
    if field not in observable_follow_up_fields(profile):
        return ""
    capability = {
        "currentPrice": "pricePath", "priceChangeRate": "pricePath",
        "ma5Distance": "pricePath", "ma20Distance": "pricePath", "ma60Distance": "pricePath",
        "volume": "volume", "volumeRatio": "volume", "timeAdjustedVolumeRatio": "volume",
        "tradeStrength": "tradeFlow", "buyVolume": "tradeFlow", "sellVolume": "tradeFlow",
        "bidAskImbalance": "orderBook", "orderbookBidVolume": "orderBook", "orderbookAskVolume": "orderBook",
        "foreignNetVolume": "investorFlow", "institutionNetVolume": "investorFlow",
        "individualNetVolume": "investorFlow", "smartMoneyNetVolume": "investorFlow",
        "adrPremiumPct": "crossListingQuote", "adrPriceUsd": "crossListingQuote", "localEquivalentKrw": "crossListingQuote",
    }.get(field)
    return str(((profile.get("capabilities") or {}).get(capability) or {}).get("sourceAsOf") or facts.get("sourceAsOf") or "")


def confirm_follow_up_observation(row, facts, value, matched, stamp):
    """Advance an AI watch, leaving its authored threshold and investment meaning intact."""
    source_at = follow_up_source_time(row, facts)
    if not source_at:
        row.update({"confirmationCount": 0, "observationStatus": "waiting-fresh-source"})
        return False
    source_time = observation_time(source_at)
    registered_at = observation_time((row.get("registration") or {}).get("registeredAt"))
    previous_time = observation_time(row.get("lastSourceAsOf"))
    poll_time = observation_time(stamp)
    if (not source_time or not registered_at or source_time < registered_at
            or (previous_time and source_time <= previous_time)
            or not poll_time or source_time > poll_time):
        return False
    row.update({"lastSourceAsOf": source_at, "observedAt": stamp, "transitionVerified": False,
                "observationStatus": "observed"})
    if not row.get("trackingBaselineCaptured"):
        row.update({
            "trackingBaselineCaptured": True, "trackingBaselineValue": value,
            "previousValue": value, "currentValue": value, "previousMatched": matched,
            "currentMatched": matched, "armed": not matched, "confirmationCount": 0,
        })
        return False
    previous_value = row.get("currentValue")
    row.update({"previousValue": previous_value, "previousMatched": row.get("currentMatched"),
                "currentValue": value, "currentMatched": matched})
    if not matched:
        row.update({"armed": True, "confirmationCount": 0})
        return False
    policy = row.get("observationPolicy") or {}
    minimum = finite_number(policy.get("minimumBaselineChange")) or 0.0
    baseline = finite_number(row.get("trackingBaselineValue"))
    if not row.get("armed") or baseline is None or abs(value - baseline) + 1e-9 < minimum:
        row["confirmationCount"] = 0
        return False
    row["confirmationCount"] = int(row.get("confirmationCount") or 0) + 1
    return row["confirmationCount"] >= max(2, int(policy.get("requiredConfirmations") or 2))


def registered_conditions_for_message(context, conditions):
    context = context or {}
    packet = context.get("followUpRegistration") or {}
    persisted = packet.get("conditions") or (context.get("investmentDecisionEpisode") or {}).get("followUpConditions") or []
    by_meaning = {follow_up_semantic_key(row): row
                  for row in persisted if isinstance(row, dict) and follow_up_is_registered(row)}
    result = []
    account_id = str(context.get("accountId") or "")
    symbol = str(context.get("rawSymbol") or context.get("symbol") or "").upper()
    for raw in conditions or []:
        if not isinstance(raw, dict):
            continue
        row = dict(by_meaning.get(follow_up_semantic_key(raw)) or raw)
        if follow_up_is_registered(row) and (not account_id or row.get("accountId") == account_id) and (not symbol or row.get("symbol") == symbol):
            row["sourceConditionId"] = str(raw.get("sourceConditionId") or raw.get("conditionId") or "")
            result.append(row)
    seen = {row["conditionId"] for row in result}
    for row in persisted:
        if (isinstance(row, dict) and follow_up_is_registered(row) and row["conditionId"] not in seen
                and (not account_id or row.get("accountId") == account_id)
                and (not symbol or row.get("symbol") == symbol)):
            result.append(dict(row))
            seen.add(row["conditionId"])
    return result
