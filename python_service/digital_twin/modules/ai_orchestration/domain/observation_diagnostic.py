"""A rejected send candidate is inspectable, never a delivered investment insight."""
from copy import deepcopy

from digital_twin.modules.ai_orchestration.domain.publication import quote_from


def observation_diagnostic(result, task_id, reason):
    if not reason or result.get("notification", {}).get("send") is not True:
        return {}
    packet = result.get("input") or {}
    quality = result.get("quality") or {}
    review = quality.get("review") if isinstance(quality.get("review"), dict) else {}
    review_sections = review.get("sections") if isinstance(review.get("sections"), dict) else {}
    repair = result.get("repair") or {}
    quote = quote_from(packet)
    quote_fields = ("currentPrice", "currency", "changeRate", "ma5", "ma20", "ma60",
                    "sourceAsOf", "asOf", "observationSource", "sourceName", "provider", "source",
                    "dataState", "freshnessStatus")
    return {
        "version": "ai-observation-diagnostic-v1", "taskId": task_id,
        "accountId": packet.get("accountId", ""), "symbol": packet.get("symbol", ""),
        "name": packet.get("name", ""), "capturedAt": packet.get("capturedAt", ""),
        "observedAt": result.get("observedAt", ""), "executionInputId": result.get("executionInputId", ""),
        "sourceSnapshotId": packet.get("sourceSnapshotId", ""),
        "reasons": list(dict.fromkeys([*quality.get("errors", []), reason])),
        "review": {"reason": review.get("reason", ""), "sections": {
            key: item.get("reason", "") for key, item in review_sections.items()
            if isinstance(item, dict) and item.get("supported") is not True}},
        "repair": {key: deepcopy(repair[key]) for key in ("status", "initialErrors", "errorKind") if key in repair},
        "draft": {key: result.get(key, "") for key in
                  ("summary", "comparison", "hypothesis", "portfolioImpact", "counterEvidence")},
        "notificationReason": result["notification"].get("reason", ""),
        "quote": {key: deepcopy(quote[key]) for key in quote_fields if key in quote},
    }
