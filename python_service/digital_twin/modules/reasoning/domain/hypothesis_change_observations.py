"""Bounded input receipts for successful, aligned TypeDB generations."""

from copy import deepcopy


def attach_model_observations(result, graph, symbols):
    inference = result.get("inferenceBox") or {}
    if (inference.get("status") != "ok" or inference.get("generationAligned") is not True
            or not inference.get("nativeTypeDbReasoningUsed") or not inference.get("sourceAboxSnapshotId")):
        return
    allowed = set(symbols or []) & set(inference.get("targetSymbols") or symbols or [])
    rows = {symbol: {} for symbol in allowed}
    for entity in graph.entities:
        props = entity.properties or {}
        symbol = props.get("symbol")
        if symbol not in allowed:
            continue
        if entity.kind == "model-hypothesis-assessment":
            for field, status in (("notSupportedContractIds", "not-supported"), ("untestedContractIds", "untested")):
                for rule in props.get(field) or []:
                    rows[symbol][rule] = {"hypothesisContractId": rule, "status": status,
                                          "observedAt": props.get("observedAt"),
                                          "sourceFeatureSnapshotId": props.get("sourceFeatureSnapshotId")}
    for entity in graph.entities:
        props = entity.properties or {}
        symbol, rule = props.get("symbol"), props.get("hypothesisContractId")
        if entity.kind != "statistical-model-hypothesis-evidence" or symbol not in allowed or not rule:
            continue
        rows[symbol][rule] = {key: deepcopy(props[key]) for key in (
            "hypothesisContractId", "signalType", "releaseId", "score", "strengthBand", "contractMatched",
            "freshnessCompatible", "eligibilityStatus", "observedAt", "sourceFeatureSnapshotId",
            "sourceObservation", "modelInputWindows",
        ) if key in props}
    inference["modelEvidenceObservations"] = rows
