"""manifest: indexes through explicit injected capabilities."""

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import merge_native_rule_evidence_read_index
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import native_rule_evidence_read_index_from_rows
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import native_rule_manifest_index_required
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import normalize_native_rule_evidence_read_index
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import clean_symbols_from_payload
from typing import Dict
from typing import Iterable
from typing import Tuple
from .indexes_ports import ManifestIndexesStore


def prepare_scoped_manifest_native_rule_indexes(_store: ManifestIndexesStore, graph: PortfolioOntology, active_metadata: Dict[str, object]=None, persistence_rows: Tuple[Iterable[Dict[str, object]], Iterable[Dict[str, object]]]=None) -> Dict[str, object]:
    """Bind a staged target graph to the complete candidate Manifest index.

    This is control-plane persistence only. It never evaluates RuleBox
    conditions. ``persistence_rows`` must be the exact, reconciled candidate
    image (selected incoming scopes plus every reused active scope). When it
    is supplied, validate that complete image against the merged candidate
    topology directly. Treating those rows as a target-only delta makes a
    valid multi-symbol candidate fail against the incoming target topology.

    A failed merge leaves the marker without an index so the runtime falls
    back to active-membership reads for correctness.
    """
    worldview = dict(getattr(graph, "worldview", {}) or {})
    if not native_rule_manifest_index_required(worldview):
        graph.worldview.pop("nativeRuleEvidenceReadIndex", None)
        graph.worldview["nativeRuleEvidenceReadIndexRequired"] = False
        graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
            "status": "not-required-source-world",
            "worldType": str(worldview.get("worldType") or ""),
            "reason": "Source worlds persist facts but do not execute native investment rules.",
        }
        return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
    graph.worldview["nativeRuleEvidenceReadIndexRequired"] = True
    topology = dict(worldview.get("nativeRulePlannerTopology") or {})
    incoming_topology = dict(
        worldview.get("nativeRulePlannerTopologyIncoming")
        or topology
    )
    patch = dict(worldview.get("targetScopedManifestPatch") or {})
    target_symbols = clean_symbols_from_payload(patch.get("targetSymbols") or [])
    replacement_symbols = clean_symbols_from_payload(
        patch.get("replacementSymbols")
        if "replacementSymbols" in patch
        else target_symbols
    )
    if persistence_rows is None:
        node_rows, relation_rows = _store.graph_persistence_rows(graph)
    else:
        raw_node_rows, raw_relation_rows = persistence_rows
        node_rows = [dict(row or {}) for row in raw_node_rows or []]
        relation_rows = [dict(row or {}) for row in raw_relation_rows or []]
    incoming_index = native_rule_evidence_read_index_from_rows(node_rows, relation_rows)
    target_scoped = (
        str(patch.get("status") or "") == "applied"
        and bool(target_symbols)
        and bool(worldview.get("nativeRulePlannerTopologyIncoming"))
    )
    if persistence_rows is not None:
        complete_candidate = normalize_native_rule_evidence_read_index(
            incoming_index,
            planner_topology=topology,
        )
        if str(complete_candidate.get("status") or "") == "ok":
            graph.worldview["nativeRuleEvidenceReadIndex"] = incoming_index
            graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
                "status": "local-complete",
                "mode": "exact-candidate-rows",
                "replacedSymbols": replacement_symbols if target_scoped else [],
                "mergedSymbolCount": len(
                    incoming_index.get("sourceIdsBySymbol") or {}
                ),
            }
            return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
        if target_scoped:
            if "replacementSymbols" in patch and not replacement_symbols:
                active_index = dict(
                    (active_metadata or {}).get("nativeRuleEvidenceReadIndex")
                    or {}
                )
                active_reuse = normalize_native_rule_evidence_read_index(
                    active_index,
                    planner_topology=topology,
                )
                if str(active_reuse.get("status") or "") == "ok":
                    graph.worldview["nativeRuleEvidenceReadIndex"] = active_index
                    graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
                        "status": "merged",
                        "mode": "semantic-noop-active-reuse",
                        "replacedSymbols": [],
                        "mergedSymbolCount": len(
                            active_index.get("sourceIdsBySymbol") or {}
                        ),
                    }
                    return dict(
                        graph.worldview["nativeRuleEvidenceReadIndexMerge"]
                    )
            merged_candidate = merge_native_rule_evidence_read_index(
                dict((active_metadata or {}).get("nativeRuleEvidenceReadIndex") or {}),
                dict((active_metadata or {}).get("nativeRulePlannerTopology") or {}),
                incoming_index,
                incoming_topology,
                topology,
                replacement_symbols,
                incoming_index_is_candidate_subset=True,
            )
            if str(merged_candidate.get("status") or "") == "ok":
                graph.worldview["nativeRuleEvidenceReadIndex"] = dict(
                    merged_candidate.get("index") or {}
                )
                graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
                    "status": "merged",
                    "mode": "exact-candidate-subset",
                    "replacedSymbols": list(
                        merged_candidate.get("replacedSymbols") or []
                    ),
                    "mergedSymbolCount": int(
                        merged_candidate.get("mergedSymbolCount") or 0
                    ),
                }
                return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
            graph.worldview.pop("nativeRuleEvidenceReadIndex", None)
            graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
                "status": str(
                    merged_candidate.get("status")
                    or "candidate-subset-index-merge-failed"
                ),
                "mode": "exact-candidate-subset",
                "reason": str(merged_candidate.get("reason") or "")[:220],
                "replacedSymbols": list(
                    merged_candidate.get("replacedSymbols") or []
                ),
                "missingSymbols": list(
                    merged_candidate.get("missingSymbols") or []
                ),
                "mergedSymbolCount": 0,
            }
            return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
        graph.worldview.pop("nativeRuleEvidenceReadIndex", None)
        graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
            "status": "complete-candidate-index-topology-mismatch",
            "reason": str(complete_candidate.get("reason") or "")[:220],
            "replacedSymbols": replacement_symbols if target_scoped else [],
            "mergedSymbolCount": 0,
        }
        return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
    if not target_scoped:
        local = normalize_native_rule_evidence_read_index(
            incoming_index,
            planner_topology=topology,
        )
        if str(local.get("status") or "") == "ok":
            graph.worldview["nativeRuleEvidenceReadIndex"] = incoming_index
            graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
                "status": "local-complete",
                "mergedSymbolCount": len(incoming_index.get("sourceIdsBySymbol") or {}),
            }
            return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
        graph.worldview.pop("nativeRuleEvidenceReadIndex", None)
        return {
            "status": "local-index-topology-mismatch",
            "reason": str(local.get("reason") or ""),
            "mergedSymbolCount": 0,
        }

    if "replacementSymbols" in patch and not replacement_symbols:
        active_index = dict(
            (active_metadata or {}).get("nativeRuleEvidenceReadIndex") or {}
        )
        active_reuse = normalize_native_rule_evidence_read_index(
            active_index,
            planner_topology=topology,
        )
        if str(active_reuse.get("status") or "") == "ok":
            graph.worldview["nativeRuleEvidenceReadIndex"] = active_index
            graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
                "status": "merged",
                "mode": "semantic-noop-active-reuse",
                "replacedSymbols": [],
                "mergedSymbolCount": len(
                    active_index.get("sourceIdsBySymbol") or {}
                ),
            }
            return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])

    merged = merge_native_rule_evidence_read_index(
        dict((active_metadata or {}).get("nativeRuleEvidenceReadIndex") or {}),
        dict((active_metadata or {}).get("nativeRulePlannerTopology") or {}),
        incoming_index,
        incoming_topology,
        topology,
        replacement_symbols,
    )
    if str(merged.get("status") or "") != "ok":
        graph.worldview.pop("nativeRuleEvidenceReadIndex", None)
        graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
            "status": str(merged.get("status") or "failed"),
            "reason": str(merged.get("reason") or "")[:220],
            "replacedSymbols": list(merged.get("replacedSymbols") or []),
        }
        return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
    graph.worldview["nativeRuleEvidenceReadIndex"] = dict(merged.get("index") or {})
    graph.worldview["nativeRuleEvidenceReadIndexMerge"] = {
        "status": "merged",
        "replacedSymbols": list(merged.get("replacedSymbols") or []),
        "mergedSymbolCount": int(merged.get("mergedSymbolCount") or 0),
    }
    return dict(graph.worldview["nativeRuleEvidenceReadIndexMerge"])
