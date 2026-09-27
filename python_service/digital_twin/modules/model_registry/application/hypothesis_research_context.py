"""Bounded current graph research, explicitly separate from proposal-time proof."""
import json
from datetime import datetime, timezone

from digital_twin.modules.model_registry.domain.hypothesis_compilation import compilation_fingerprint
from digital_twin.modules.reasoning.contracts import entity_id


def research_context(repository, proposal, *, account_id, symbol, world_id):
    base = {"contract": "hypothesis-research-context-v1", "accountId": account_id,
            "symbol": symbol, "worldId": world_id, "asOf": datetime.now(timezone.utc).isoformat(),
            "purpose": "current-research-not-historical-replay", "facts": [], "counterEvidence": [],
            "gaps": [], "maximumFacts": 40, "maximumBytes": 18000,
            "proposalEvidenceIds": list(proposal.get("supportingEvidenceIds") or [])[:40],
            "proposalCreatedAt": proposal.get("createdAt")}
    methods = ("inferencebox_recovery_metadata", "active_abox_snapshot_id", "read_entity_rows_by_ids", "active_tbox_metadata")
    if not account_id or not symbol or not all(callable(getattr(repository, name, None)) for name in methods):
        return {**base, "status": "not-queried", "gaps": ["scoped-graph-reader-unavailable"]}
    try:
        marker = repository.inferencebox_recovery_metadata(world_id=world_id)
        snapshot = marker.get("sourceAboxSnapshotId")
        if marker.get("status") != "ok" or not snapshot or symbol not in (marker.get("targetSymbols") or []):
            return {**base, "status": "not-evaluated", "gaps": ["published-subject-generation-required"]}
        if repository.active_abox_snapshot_id(world_id=world_id) != snapshot:
            return {**base, "status": "not-aligned", "gaps": ["active-generation-changed"]}
        base.update({"sourceAboxSnapshotId": snapshot, "inferenceGenerationId": marker.get("inferenceGenerationId"),
                     "inferenceGenerationAt": marker.get("inferenceGenerationAt"),
                     "ruleboxRulesHash": marker.get("ruleboxRulesHash"),
                     "tbox": repository.active_tbox_metadata()})
        support = set(base["proposalEvidenceIds"])
        counter = set(list(proposal.get("counterEvidenceIds") or [])[:40])
        requested = sorted(support | counter)
        inference_reader = getattr(repository, "inferencebox_snapshot", None)
        if callable(inference_reader):
            inference = inference_reader([symbol], limit=20, world_id=world_id,
                inference_generation_id=marker["inferenceGenerationId"], source_abox_snapshot_id=snapshot)
            if inference.get("status") not in {"ok", "empty"}:
                base["gaps"].append("scoped-inference-unavailable")
            else:
                base["inferenceTraces"] = []
                for trace in (inference.get("traces") or [])[:20]:
                    compact = {key: trace[key] for key in ("id", "ruleId", "sourceId", "evidenceIds", "sourceFactIds", "counterEvidenceIds", "matchedConditionIds") if key in trace}
                    base["inferenceTraces"].append(compact)
                    for key in ("counterEvidenceIds", "sourceFactIds", "evidenceIds"):
                        values = list(trace.get(key) or [])[:20]
                        if key == "counterEvidenceIds":
                            counter.update(values)
                        requested += [value for value in values if isinstance(value, str) and value not in requested]
                base["inferenceTraceOmitted"] = max(0, int(inference.get("traceCount") or 0) - len(base["inferenceTraces"]))
        rows = repository.read_entity_rows_by_ids(requested[:40], boxes=["ABox"], world_id=world_id) if requested else []
        found, used = set(), 0
        for raw in rows:
            props = raw.get("properties") or json.loads(raw.get("propertiesJson") or "{}")
            row = {**raw, **props}
            identity = row.get("id") or row.get("entityId")
            if identity not in requested:
                continue
            if str(row.get("symbol") or row.get("subjectSymbol") or "").upper() != symbol:
                base["gaps"].append("subject-not-verifiable:" + str(identity))
                continue
            if row.get("accountId") and row["accountId"] != account_id:
                raise ValueError("cross-account-research-fact")
            clock_values = [row.get(key) for key in ("observedAt", "publishedAt", "sourceAsOf") if row.get(key)]
            if any(datetime.fromisoformat(str(value).replace("Z", "+00:00")) > datetime.fromisoformat(base["asOf"]) for value in clock_values):
                base["gaps"].append("future-research-fact:" + str(identity))
                continue
            fact = {key: row[key] for key in ("id", "kind", "label", "tboxClass", "symbol", "subjectSymbol", "accountId",
                    "source", "sourceUrl", "sourceReferences", "evidenceIds", "observedAt", "publishedAt", "sourceAsOf",
                    "value", "unit", "description", "statement", "status", "metric", "period", "provenance") if key in row}
            size = len(json.dumps(fact, ensure_ascii=False).encode())
            if used + size > base["maximumBytes"]:
                continue
            used += size
            found.add(identity)
            base["counterEvidence" if identity in counter else "facts"].append(fact)
        classes = sorted({fact.get("tboxClass") for fact in base["facts"] + base["counterEvidence"] if fact.get("tboxClass")})[:12]
        meanings = repository.read_entity_rows_by_ids([entity_id("tbox-class", name) for name in classes], boxes=["TBox"]) if classes else []
        base["tboxMeanings"] = []
        for raw in meanings:
            meaning = {**raw, **(raw.get("properties") or json.loads(raw.get("propertiesJson") or "{}"))}
            base["tboxMeanings"].append({key: meaning[key] for key in ("className", "label", "description", "parentClass", "boundedContext", "tboxFingerprint") if key in meaning})
        if repository.active_abox_snapshot_id(world_id=world_id) != snapshot:
            return {**base, "facts": [], "counterEvidence": [], "status": "not-aligned", "gaps": ["active-generation-changed"]}
        base["missingEvidenceIds"] = [item for item in requested[:40] if item not in found]
        base["omittedEvidenceCount"] = max(0, len(requested) - 40)
        base["counterEvidenceState"] = "observed" if base["counterEvidence"] else "not-requested" if not counter else "not-found"
        base["gaps"] += ["evidence-not-resolved:" + item for item in base["missingEvidenceIds"]]
        base["status"] = "partial" if base["gaps"] or base["omittedEvidenceCount"] else "available" if found else "empty"
        base["fingerprint"] = compilation_fingerprint(base)
        return base
    except Exception as error:
        return {**base, "facts": [], "counterEvidence": [], "status": "query-failed", "gaps": [str(error)[:240]]}
