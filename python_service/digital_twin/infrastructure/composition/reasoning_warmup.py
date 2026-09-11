"""Explicit V2 reasoning warmup composition phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass(frozen=True)
class ReasoningWarmup:
    runtime_rulebox_catalog: Dict[str, Any]
    compiled_ontology_release: Dict[str, Any]
    runtime_world_partition: Dict[str, Any]


def warm_v2_release(projection_recorder, candidate_rulebox) -> ReasoningWarmup:
    from digital_twin.domain.ontology_compiler import compile_ontology_release
    from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_from_payload

    runtime_rulebox_catalog = projection_recorder.ensure_rulebox_ready()
    compiled_ontology_release = compile_ontology_release(
        rulebox_rules_from_payload(
            {
                # ``ensure_rulebox_ready`` may return compact warmed metadata.
                # The immutable candidate release is the authoritative source of
                # executable rule bodies for static compilation.
                "rules": list(candidate_rulebox.get("rules") or [])
            }
        )
    )
    runtime_world_partition = projection_recorder.world_rule_partition(runtime_rulebox_catalog)
    if (
        str(runtime_rulebox_catalog.get("status") or "") != "ready"
        or not bool(compiled_ontology_release.get("valid"))
        or str(runtime_world_partition.get("status") or "") != "ready"
    ):
        raise RuntimeError(
            "The independent V2 frozen ontology release could not be warmed: "
            + str(
                runtime_rulebox_catalog.get("reason")
                or (compiled_ontology_release.get("failures") or [""])[0]
                or (runtime_world_partition.get("failures") or [{}])[0].get("reason")
                or "unknown"
            )[:220]
        )
    projection_recorder.catalog_for_rules(
        runtime_rulebox_catalog,
        runtime_world_partition.get("sharedRules") or [],
    )
    projection_recorder.catalog_for_rules(
        runtime_rulebox_catalog,
        runtime_world_partition.get("overlayRules") or [],
    )
    return ReasoningWarmup(
        runtime_rulebox_catalog, compiled_ontology_release, runtime_world_partition
    )
