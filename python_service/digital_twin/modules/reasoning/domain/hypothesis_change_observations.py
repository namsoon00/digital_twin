"""Bounded input receipts for successful, aligned TypeDB generations."""

from copy import deepcopy

from .rule_execution_proof import compiled_rule_execution_completed


RELATIVE_INPUT_FIELDS = (
    "proxySymbol", "comparisonWindow", "sourceAsOf", "proxySourceAsOf", "dataState",
    "judgementEvidenceUsable", "timestampsAligned", "stockEvidenceUsable", "proxyEvidenceUsable",
    "relativeReturnPct", "proxyChangeRate", "stockChangeRate",
)


def attach_model_observations(result, graph, symbols):
    inference = result.get("inferenceBox") or {}
    native = inference.get("status") == "ok" and inference.get("nativeTypeDbReasoningUsed")
    if (inference.get("generationAligned") is not True or not inference.get("sourceAboxSnapshotId")
            or not (native or compiled_rule_execution_completed(inference))):
        return
    allowed = set(symbols or []) & set(inference.get("targetSymbols") or symbols or [])
    rows = {symbol: {} for symbol in allowed}
    relative_inputs = {symbol: [] for symbol in allowed}
    for entity in graph.entities:
        props = entity.properties or {}
        symbol = props.get("symbol")
        if symbol not in allowed:
            continue
        if entity.kind == "relative-performance-observation":
            relative_inputs[symbol].append({k: props[k] for k in RELATIVE_INPUT_FIELDS if k in props})
        if entity.kind == "model-hypothesis-assessment":
            for field, status in (("notSupportedContractIds", "not-supported"), ("untestedContractIds", "untested")):
                for rule in props.get(field) or []:
                    rows[symbol][rule] = {"hypothesisContractId": rule, "status": status,
                                          "observedAt": props.get("observedAt"),
                                          "sourceFeatureSnapshotId": props.get("sourceFeatureSnapshotId"),
                                          **deepcopy((props.get("contractObservations") or {}).get(rule) or {})}
    for entity in graph.entities:
        props = entity.properties or {}
        symbol, rule = props.get("symbol"), props.get("hypothesisContractId")
        if entity.kind != "statistical-model-hypothesis-evidence" or symbol not in allowed or not rule:
            continue
        rows[symbol][rule] = {key: deepcopy(props[key]) for key in (
            "hypothesisContractId", "signalType", "releaseId", "score", "strengthBand", "contractMatched",
            "freshnessCompatible", "eligibilityStatus", "observedAt", "sourceFeatureSnapshotId",
            "sourceObservation", "modelInputWindows", "sourceMeasurements",
        ) if key in props}
    for symbol, models in rows.items():
        for model in models.values():
            if str(model.get("signalType") or "").startswith("cross-asset-residual-"):
                model["relativeObservations"] = relative_inputs[symbol]
    inference["modelEvidenceObservations"] = rows
