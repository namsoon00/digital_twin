"""Validate internal declines without inventing customer-delivery requirements."""
VERSION = "purpose-aware-observation-quality-v1"
FIELD = "observationQualityPolicy"


def decline_reason(result, packet=None):
    packet = packet if packet is not None else result.get("input", {})
    notification = result.get("notification")
    if (packet.get(FIELD) != VERSION or not isinstance(notification, dict) or notification.get("send") is not False
            or packet.get("analysisPurpose") not in {"source-dated", "current-state"}):
        return ""
    from . import investment_meaning, notification_contribution
    meaning = result.get("investmentMeaning")
    if investment_meaning.enabled(packet) and isinstance(meaning, dict) and meaning.get("status") == "insufficient":
        return "evidence-insufficient"
    if packet.get("notificationContributionPolicy") == notification_contribution.POLICY:
        contribution = result.get("notificationContribution")
        return {"repetition": "repetition", "no-added-value": "no-material-change"}.get(
            contribution.get("kind") if isinstance(contribution, dict) else "", "")
    return ""


def wait_metadata(result):
    quality = result.get("quality", {})
    reason = decline_reason(result)
    if quality.get("status") != "not-published" or quality.get("reasonCode") != reason or not reason:
        return {}
    meaning = result.get("investmentMeaning") or {}
    check = meaning.get("nextCheck") or {}
    required = []
    if reason == "evidence-insufficient" and all(isinstance(check.get(k), str) and check[k].strip()
                                                for k in ("question", "source")):
        required = [{"question": check["question"], "source": check["source"]}]
    return {"reasonCode": reason, "validationPolicy": VERSION,
            "requiredEvidence": required, "authority": "operational-only"}
