"""Exact source, release, target and observation-clock cache identities."""

from __future__ import annotations

from digital_twin.domain.ontology_projection_audit import projection_source_snapshot
from digital_twin.domain.ontology_projection_fingerprint import stable_value
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.reasoning_shadow import frozen_projection_runtime_context
from typing import Dict
from typing import Iterable
from typing import List
import hashlib
import json
from dataclasses import dataclass


PORTFOLIO_GRAPH_ASSEMBLY_CACHE_CONTRACT_VERSION = (
    "portfolio-graph-assembly-cache-v16-frozen-rule-subjects"
)

PROJECTION_RUNTIME_CONTEXT_CACHE_CONTRACT_VERSION = (
    "projection-runtime-context-cache-v1"
)


@dataclass(frozen=True)
class ProjectionCacheKeys:
    settings: Dict[str, object]
    namespace: str

    def graph_assembly_cache_namespace(self) -> str:
        return self.namespace

    def runtime_context_cache_key(
        self,
        snapshot: AccountSnapshot,
        active_tbox: Dict[str, object],
        target_symbols: Iterable[object] = None,
    ) -> str:
        source_snapshot = projection_source_snapshot(snapshot)
        metadata = dict(source_snapshot.get("metadata") or {})
        investment_brain = dict(metadata.get("investmentBrain") or {})
        investment_brain.pop("outcomeObservation", None)
        if investment_brain:
            metadata["investmentBrain"] = investment_brain
        else:
            metadata.pop("investmentBrain", None)
        source_snapshot["metadata"] = metadata
        payload = {
            "version": PROJECTION_RUNTIME_CONTEXT_CACHE_CONTRACT_VERSION,
            "namespace": self.graph_assembly_cache_namespace(),
            "sourceSnapshot": stable_value(source_snapshot),
            "settings": stable_value(self.settings),
            "activeTBox": stable_value(active_tbox),
            "targetSymbols": sorted(
                {
                    str(symbol or "").upper().strip()
                    for symbol in target_symbols or []
                    if str(symbol or "").strip()
                }
            ),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def graph_assembly_cache_key(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        active_tbox: Dict[str, object],
        runtime_context: Dict[str, object],
        target_symbols: List[str] = None,
        input_mode: str = "full",
    ) -> str:
        """Hash only source inputs; no graph result or credentials are persisted."""
        source_snapshot = projection_source_snapshot(snapshot)
        metadata = dict(source_snapshot.get("metadata") or {})
        investment_brain = dict(metadata.get("investmentBrain") or {})
        # Outcome observation is attached by this projection's runtime-context
        # reader. It is derived state, not a new source observation, and must
        # not turn an otherwise identical retry into a cache miss.
        investment_brain.pop("outcomeObservation", None)
        if investment_brain:
            metadata["investmentBrain"] = investment_brain
        else:
            metadata.pop("investmentBrain", None)
        source_snapshot["metadata"] = metadata
        frozen_runtime_context = frozen_projection_runtime_context(runtime_context)
        payload = {
            # Bump this contract whenever graph-builder behavior changes. The
            # durable cache can outlive a worker restart, so source equality
            # alone is not enough to prove a cached graph is reusable.
            "version": PORTFOLIO_GRAPH_ASSEMBLY_CACHE_CONTRACT_VERSION,
            "namespace": self.graph_assembly_cache_namespace(),
            # Cache reuse is stricter than material-generation reuse.  The
            # observation clock and provider timestamps can change freshness,
            # session and data-quality facts even when price/volume values are
            # unchanged.  Removing those fields here previously returned a
            # stale flow/quality graph while the replay packet contained the
            # current context.
            "sourceSnapshot": source_snapshot,
            "settings": stable_value(self.settings),
            "activeTBox": stable_value(active_tbox),
            "runtimeContextHash": hashlib.sha256(
                json.dumps(
                    frozen_runtime_context,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            "ruleboxRulesHash": str((rule_catalog or {}).get("ruleboxRulesHash") or ""),
            "targetSymbols": sorted(
                {
                    str(symbol or "").upper().strip()
                    for symbol in target_symbols or []
                    if str(symbol or "").strip()
                }
            ),
            "inputMode": str(input_mode or "full"),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
