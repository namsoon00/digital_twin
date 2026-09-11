"""Schedule dependent world work and quality observations after publication."""

from __future__ import annotations
from typing import Optional, Union
from .record_ports import RecordSnapshotBindings
from digital_twin.domain.ontology_worlds import world_metadata
from typing import Dict
import time


from .stage_results import CompletedProjection, ScheduleFollowupsResult
from .schedule_followups_ports import ScheduleFollowupsPort
from digital_twin.domain.ontology_worlds import OntologyWorld
from digital_twin.domain.ontology_contracts import PortfolioOntology


def schedule_followups(
    _store: ScheduleFollowupsPort,
    _bindings: RecordSnapshotBindings,
    graph: PortfolioOntology,
    knowledge_world_context: OntologyWorld,
    market_world_context: OntologyWorld,
    portfolio_world_context: OntologyWorld,
    result: Dict[str, object],
    runtime_stages: Dict[str, int],
    world_impact_route: Dict[str, object],
) -> Union[ScheduleFollowupsResult, CompletedProjection]:
    market_projection_started = time.perf_counter()
    result["worldImpactRoute"] = world_impact_route
    if bool(result.get("saved")) and bool(
        world_impact_route.get("market", {}).get("required")
    ):
        # MarketWorld is an account-independent derived mirror.  It
        # is intentionally scheduled only after the portfolio ABox
        # and its decision-critical TypeDB inference are verified.
        # This keeps a slow shared write out of the alert path while
        # never letting an unverified account projection publish
        # facts to the shared world.
        result["marketWorld"] = _store.schedule_market_world_projection(
            graph,
            market_world_context,
            source_world=portfolio_world_context,
        )
    elif bool(result.get("saved")):
        result["marketWorld"] = {
            **world_metadata(market_world_context),
            "status": "skipped-world-impact-route",
            "preservedActiveGeneration": True,
            "reason": str(world_impact_route.get("market", {}).get("reason") or ""),
        }
    if bool(result.get("saved")) and bool(
        world_impact_route.get("knowledge", {}).get("required")
    ):
        result["knowledgeWorld"] = _store.schedule_knowledge_world_projection(
            graph,
            knowledge_world_context,
            source_world=portfolio_world_context,
        )
    elif bool(result.get("saved")):
        result["knowledgeWorld"] = {
            **world_metadata(knowledge_world_context),
            "status": "skipped-world-impact-route",
            "preservedActiveGeneration": True,
            "reason": str(world_impact_route.get("knowledge", {}).get("reason") or ""),
        }
    else:
        result["marketWorld"] = {
            **world_metadata(market_world_context),
            "status": "deferred-portfolio-inference-not-verified",
            "preservedActiveGeneration": True,
            "reason": "계좌 ABox 또는 TypeDB 추론이 확정되지 않아 공용 시장 읽기 모델 갱신을 건너뛰었습니다.",
        }
        result["knowledgeWorld"] = {
            **world_metadata(knowledge_world_context),
            "status": "deferred-portfolio-inference-not-verified",
            "preservedActiveGeneration": True,
            "reason": "계좌 ABox 또는 TypeDB 추론이 확정되지 않아 공용 지식 세계 갱신을 건너뛰었습니다.",
        }
    runtime_stages["marketWorldQueueMs"] = int(
        (time.perf_counter() - market_projection_started) * 1000
    )
    if _store.quality_store:
        quality_started = time.perf_counter()
        if _store.async_quality_record_enabled():
            result["qualityRecord"] = (
                _bindings.SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR.enqueue(
                    _store.quality_store,
                    graph,
                    _store.source,
                )
            )
            runtime_stages["qualityRecordQueueMs"] = int(
                (time.perf_counter() - quality_started) * 1000
            )
        else:
            sample = _store.quality_store.record_graph(graph, source=_store.source)
            runtime_stages["qualityRecordMs"] = int(
                (time.perf_counter() - quality_started) * 1000
            )
            result["qualitySampleId"] = getattr(sample, "sample_id", "")
            result["qualityState"] = getattr(sample, "overall_state", "") or getattr(
                sample, "overall_score", ""
            )

    return ScheduleFollowupsResult()
