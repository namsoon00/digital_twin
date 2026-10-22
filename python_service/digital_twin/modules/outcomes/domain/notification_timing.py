"""Receipt-clock price observation. This is never a trade or qualification."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite

VERSION = "notification-timing-observation-v1"
MAX_QUOTE_DELAY_MINUTES = 5


def clock(value):
    try:
        value = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (TypeError, ValueError):
        return None


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def timing_contract(packet):
    current = packet.get("current") or {}
    if not current.get("proofIdentityVerified"):
        return {}
    claims = {}
    for event in packet.get("customerEvents") or []:
        if not event.get("eligible") or event.get("purpose") != "hypothesis-evidence" or event.get("outcome"):
            continue
        hypothesis = event.get("hypothesis") or {}
        claim = hypothesis.get("claimContract") or {}
        if not claim.get("claimContractId"):
            continue
        claims[claim["claimContractId"]] = {
            "claimContractId": claim["claimContractId"],
            "claimFingerprint": (event.get("intent") or {}).get("claimFingerprint"),
            "qualification": (hypothesis.get("qualification") or {}).get("status") or "unknown",
            "outcomeContract": deepcopy(claim.get("outcomeContract") or {}),
        }
    if not claims:
        return {}
    facts = {row.get("id"): row.get("value") for row in current.get("facts") or []}
    horizons = sorted({int(h) for c in claims.values() for h in c["outcomeContract"].get("outcomeHorizonMinutes") or []
                       if str(h).isdigit() and 0 < int(h) <= 525600})
    if not horizons:
        return {}
    return {"version": VERSION, "accountId": current.get("accountId"), "symbol": current.get("symbol"),
            "inferenceGenerationId": current.get("inferenceGenerationId"), "sourceAboxSnapshotId": current.get("sourceAboxSnapshotId"),
            "sourceAsOf": current.get("observedAt"), "signalPrice": number(facts.get("currentPrice")),
            "horizons": horizons, "claims": list(claims.values()), "maximumQuoteDelayMinutes": MAX_QUOTE_DELAY_MINUTES,
            "decisionEligibility": "measurement-only", "tradeTimingQualified": False,
            "costBasis": "not-recorded", "netReturnPct": None, "horizonBasis": "elapsed-minutes-after-delivery"}


def advance_timing(saved, observations, now):
    """Freeze the first post-receipt quote and each deadline's first quote."""
    row = deepcopy(saved)
    delivered = clock(row.get("deliveredAt"))
    current = clock(now)
    contract = row.get("contract") or {}
    delay = timedelta(minutes=MAX_QUOTE_DELAY_MINUTES)
    if not delivered or not current:
        return {**row, "state": "unmeasurable", "reason": "invalid-receipt-clock"}
    if current < delivered:
        return row
    entry = row.get("entry") or {}
    outcomes = row.setdefault("outcomes", {})
    targets = [("entry", delivered), *[(str(h), delivered + timedelta(minutes=h)) for h in contract.get("horizons") or []]]
    for key, target in targets:
        if (key == "entry" and entry) or key in outcomes or target > current:
            continue
        observation = observations.get(key) or {}
        source = clock(observation.get("sourceAsOf"))
        price = number(observation.get("currentPrice"))
        quality = observation.get("dataQuality")
        usable = (source and target <= source <= min(current, target + delay) and price is not None and price > 0
                  and quality in {"actual", "live", "fresh"})
        if usable:
            result = {"state": "observed", "price": price, "sourceAsOf": source.isoformat(),
                      "provider": observation.get("provider") or "", "targetAt": target.isoformat(),
                      "delayMinutes": round((source-target).total_seconds()/60, 4)}
            if key == "entry":
                row["entry"] = entry = result
                original = number(contract.get("signalPrice"))
                row["signalToEntryChangePct"] = round((price/original-1)*100, 6) if original and original > 0 else None
            elif entry and source > clock(entry.get("sourceAsOf")):
                result["grossPriceChangePct"] = round((price/entry["price"]-1)*100, 6)
                result["netReturnPct"] = None
                result["executionPriceVerified"] = False
                outcomes[key] = result
        if current > target + delay and not (entry if key == "entry" else outcomes.get(key)):
            if key == "entry":
                return {**row, "state": "unmeasurable", "reason": "post-delivery-quote-unavailable"}
            outcomes[key] = {"state": "unmeasurable", "reason": "horizon-quote-unavailable", "targetAt": target.isoformat()}
    remaining = [target for key, target in targets if not (entry if key == "entry" else outcomes.get(key))]
    row.update(state="awaiting-entry" if not entry else "awaiting-outcomes" if remaining else "complete",
               nextCheckAt=min(remaining).isoformat().replace("+00:00", "Z") if remaining else "",
               tradeTimingQualified=False, costBasis="not-recorded")
    return row


def timing_report(rows):
    rows = list(rows)
    return {"version": VERSION, "scope": "bounded-delivered-notification-sample", "count": len(rows),
            "measuredHorizonCount": sum(o.get("state") == "observed" for r in rows for o in r.get("outcomes", {}).values()),
            "tradeTimingQualified": False, "netReturnAvailable": False,
            "reason": "발송 후 저장된 시세의 변화입니다. 체결 가격·비용·비교 전략 검증이 없어 매매 승률이나 순수익으로 사용하지 않습니다.",
            "observations": rows}
