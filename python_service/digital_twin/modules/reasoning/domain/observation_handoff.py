"""Evidence-ready event from a completed live V2 job, independent of rule matches."""
from dataclasses import replace
import hashlib

from .events import ontology_reasoning_completed_event
from .independent_reasoning import VERIFIED_PROJECTION_STATUSES


OBSERVATION_EVIDENCE_READY = "ontology.observation_evidence_ready"


def completed_observation_evidence_event(job, result):
    if (result.get("deployment_id") != job.get("deployment_id") or result.get("delivery_authorized") is not True
            or result.get("status") not in {"ok", "partial"}):
        return None
    evaluated = set(result.get("evaluated_symbols") or [])
    outcomes = []
    for account, projection in (result.get("projection_results") or {}).items():
        if (projection.get("status") not in VERIFIED_PROJECTION_STATUSES
                or projection.get("nativeTypeDbReasoningCompleted") is not True or projection.get("generationAligned") is not True
                or not projection.get("worldId") or not projection.get("sourceAboxSnapshotId")):
            continue
        execution = projection.get("sharedInferenceExecution") or {}
        symbols = sorted(evaluated & set(execution["evaluatedSymbols"])) if "evaluatedSymbols" in execution else sorted(evaluated)
        if not symbols:
            continue
        outcomes.append({"accountId": account, "worldId": projection["worldId"],
            "sourceAboxSnapshotId": projection["sourceAboxSnapshotId"],
            "inferenceGenerationId": projection.get("inferenceGenerationId", ""),
            "nativeTypeDbReasoningCompleted": True, "generationAligned": True,
            "alertPipeline": {"targetSymbols": symbols}})
    if not outcomes:
        return None
    event = ontology_reasoning_completed_event([job["source_event_id"]], [row["accountId"] for row in outcomes],
        sorted({symbol for row in outcomes for symbol in row["alertPipeline"]["targetSymbols"]}), 0,
        reason="운영 V2 추론 작업의 TypeDB 근거 반영이 완료되었습니다.", projection_outcomes=outcomes)
    return replace(event, name=OBSERVATION_EVIDENCE_READY,
        event_id="observation-ready:" + hashlib.sha256(job["job_id"].encode()).hexdigest(),
        payload={**event.payload, "reasoningJobId": job["job_id"], "deploymentId": job["deployment_id"]})
