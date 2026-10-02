"""Only completed, aligned live ABox handoffs can wake a scoped observation."""


def evidence_wake_targets(payload, subjects):
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return []
    targets = {}
    for outcome in payload.get("projectionOutcomes", []):
        if (not isinstance(outcome, dict) or outcome.get("generationAligned") is not True
                or outcome.get("nativeTypeDbReasoningCompleted") is not True
                or not outcome.get("sourceAboxSnapshotId") or not outcome.get("worldId")):
            continue
        pipeline = outcome.get("alertPipeline") or {}
        symbols = pipeline.get("targetSymbols") or pipeline.get("requestedSymbols") or []
        if not isinstance(symbols, list):
            continue
        for subject in subjects:
            if (subject["accountId"] == outcome.get("accountId") and subject["worldId"] == outcome["worldId"]
                    and subject["symbol"] in symbols):
                key = (subject["accountId"], subject["symbol"], subject["worldId"])
                targets[key] = {**subject, "sourceSnapshotId": outcome["sourceAboxSnapshotId"]}
    return list(targets.values())
