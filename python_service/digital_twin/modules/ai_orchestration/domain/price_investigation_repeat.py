"""Bound same-source, inconclusive price explanations without hiding new work."""
from datetime import datetime, timezone
import math

VERSION = "unconfirmed-price-source-wait-v1"


def unchanged_price_context(packet, previous):
    """Fail open on a changed session, direction, base or meaningful price move.

    The half-percent bound limits this cost-saving gate; it is not an alert
    trigger or trading condition. Missing/invalid source values never suppress.
    """
    quotes = [{row["id"]: row for row in value.get("facts", []) if row.get("kind") == "stock"}
              for value in (packet, previous)]
    if not quotes[0] or quotes[0].keys() != quotes[1].keys():
        return False
    for key, current in quotes[0].items():
        old = quotes[1][key]
        if any(current.get(field) != old.get(field) for field in ("currency", "source", "marketSessionStatus",
                "freshnessReferenceState", "judgementEvidenceUsable", "previousClose")):
            return False
        try:
            if any(isinstance(value["currentPrice"], bool) for value in (current, old)):
                return False
            price, base = float(current["currentPrice"]), float(old["currentPrice"])
            if not all(math.isfinite(value) and value > 0 for value in (price, base)) or abs(price - base) >= base * 0.005:
                return False
            for field in ("changeRate", "priceChangeRate"):
                if current.get(field) is not None or old.get(field) is not None:
                    if any(isinstance(value[field], bool) for value in (current, old)):
                        return False
                    change, prior = float(current[field]), float(old[field])
                    if not all(math.isfinite(value) for value in (change, prior)) or (change > 0)-(change < 0) != (prior > 0)-(prior < 0) or abs(change-prior) >= 0.5:
                        return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
    return True


def waiting(packet, previous, research, now=None):
    now = now or datetime.now(timezone.utc)
    old = previous.get("input") or {}
    if (packet.get("priceInvestigationRepeatPolicy") != VERSION
            or old.get("priceInvestigationRepeatPolicy") != VERSION
            or not packet.get("priceInvestigationSourceKey")
            or packet["priceInvestigationSourceKey"] != old.get("priceInvestigationSourceKey")
            or not unchanged_price_context(packet, old)
            or previous.get("priceExplanation", {}).get("status") != "cause-unconfirmed"
            or previous.get("quality", {}).get("status") != "observation-only"
            or previous.get("notification", {}).get("send") is not False
            or packet.get("deliveredNotificationContext", {}).get("fingerprint") != old.get("deliveredNotificationContext", {}).get("fingerprint")
            or any(row.get("transitionVerified") for row in packet.get("followUpEvaluations", []))
            or any(row.get("reviewDue") for row in research if row.get("kind") == "business-thesis")):
        return False
    try:
        observed = datetime.fromisoformat(previous["observedAt"].replace("Z", "+00:00"))
        return 0 <= (now - observed).total_seconds() < 3 * 3600
    except (KeyError, TypeError, ValueError):
        return False
