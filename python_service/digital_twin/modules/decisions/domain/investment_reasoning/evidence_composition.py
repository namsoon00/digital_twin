"""Read native composition conclusions and their exact frozen measurements.

No action selection, probability estimate, threshold evaluation or vote count
is performed here. Missing historical proof remains explicitly unavailable.
"""

from copy import deepcopy
from itertools import combinations

from digital_twin.modules.model_registry.contracts import verified_measurement_packet


VERSION = "native-evidence-composition-v1"


def _rows(value):
    return [r for r in value or [] if isinstance(r, dict)]


def _usable(condition):
    return (condition.get("judgementEvidenceUsable") is not False
            and condition.get("freshnessStatus") not in {"stale", "expired", "missing", "unknown"}
            and (condition.get("matchedByTypeDB") is True or condition.get("matched") is True))


def _windows(packet):
    return sorted({str(c.get("windowKey") or c.get("comparisonWindow"))
                   for row in packet["measurements"] for c in [row.get("context") or {}]
                   if c.get("windowKey") or c.get("comparisonWindow")})


def native_evidence_composition(identity, relation):
    traces = _rows((relation.get("graphStoreInference") or {}).get("traces"))
    rules = {r.get("rule_id") or r.get("ruleId"): r for r in _rows(relation.get("matchedRules"))}
    receipts, missing, native = {}, set(), []
    for trace in traces:
        rid = trace.get("ruleId") or trace.get("rule_id")
        if not rid or trace.get("matched") is False:
            continue
        conditions = _rows(trace.get("matchedConditions"))
        for condition in conditions:
            props = condition.get("matchedTargetProperties") or {}
            contract = props.get("hypothesisContractId")
            if not contract or not _usable(condition):
                continue
            receipt = verified_measurement_packet(props.get("sourceMeasurements"),
                rule_id=contract, symbol=identity["symbol"], release_id=props.get("releaseId"),
                feature_snapshot_id=props.get("sourceFeatureSnapshotId"),
                cutoff=props.get("knowledgeCutoffAt"))
            if not receipt:
                missing.add(contract)
                continue
            key = (contract, props.get("releaseId"), receipt["measurementFingerprint"])
            receipts[key] = {
                "ruleId": contract, "signalType": props.get("signalType"),
                "polarity": props.get("polarity"), "horizons": _windows(receipt),
                "sourceMeasurements": receipt,
                "nativeRuleIds": sorted(set(receipts.get(key, {}).get("nativeRuleIds", [])) | {rid}),
            }
        response = (rules.get(rid) or {}).get("compositionResponse") or {}
        if (response.get("version") != "rule-evidence-composition-v1"
                or response.get("authority") != "typedb-rulebox"):
            continue
        required = set(response.get("conditionIds") or [])
        proved = {c.get("conditionId") for c in conditions if _usable(c)}
        if len(required) < 2 or not required.issubset(proved):
            continue
        ids = sorted({(c.get("matchedTargetProperties") or {}).get("hypothesisContractId")
                      for c in conditions if _usable(c) and
                      (c.get("matchedTargetProperties") or {}).get("hypothesisContractId")})
        # A policy combination cannot manufacture a policy response. The
        # policy must also have its own independent native match.
        policy = response.get("policyRuleId")
        if policy and not (rules.get(policy) or {}).get("managementResponse"):
            continue
        native.append({**deepcopy(response), "ruleId": rid, "sourceContractIds": ids,
                       "nativeConditionIds": sorted(required)})
    evidence = sorted(receipts.values(), key=lambda r: (
        r["ruleId"], r["sourceMeasurements"]["modelReleaseId"], r["sourceMeasurements"]["measurementFingerprint"]))
    for index, row in enumerate(evidence):
        row["id"] = "source-group:" + str(index + 1)
    relationships = []
    for a, b in combinations(evidence, 2):
        pa, pb = a["sourceMeasurements"], b["sourceMeasurements"]
        roots = sorted(set(pa["rootEvidenceIds"]) & set(pb["rootEvidenceIds"]))
        same_snapshot = pa["sourceFeatureSnapshotId"] == pb["sourceFeatureSnapshotId"]
        horizons_differ = bool(a["horizons"] and b["horizons"] and a["horizons"] != b["horizons"])
        opposing = {a.get("polarity"), b.get("polarity")} == {"support", "risk"}
        relationships.append({
            "left": a["id"], "right": b["id"], "sharedEvidenceIds": roots,
            "sharedObservation": bool(roots and same_snapshot),
            "differentHorizons": horizons_differ, "opposingDirections": opposing,
            "independenceEstablished": False,
        })
    return {"version": VERSION, "authority": "typedb-materialized-synthesis", **identity,
            "compositions": sorted(native, key=lambda r: r["code"]),
            "evidenceGroups": evidence, "relationships": relationships,
            "unavailableRuleIds": sorted(missing - {r["ruleId"] for r in evidence}),
            "independenceEstablished": False, "selectedMarketAction": None}
