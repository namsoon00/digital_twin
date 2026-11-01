"""Portable, bounded copies of verified graph reads; never new graph authority."""
from copy import deepcopy
from datetime import datetime, timezone

from .observation_evidence import EvidenceContractError, EvidenceNotReady, canonical_json, content_hash, evidence_change_identity, quote_clock_assessment
from .observation_session import ObservationEvidenceSession

VERSION = "verified-observation-checkpoint-v1"
READY_EVENT = "ontology.observation_checkpoint_ready"
MAX_BYTES = 12 * 1024 * 1024
REFRESH_SECONDS = 120


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo:
            return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass
    raise EvidenceContractError("checkpoint clock invalid")


def scope(request, binding):
    from .observation_binding import VERSION as binding_version
    if binding.get("version") != binding_version:
        raise EvidenceContractError("checkpoint binding invalid")
    if any(not request.get(k) for k in ("accountId", "symbol", "worldId")):
        raise EvidenceContractError("checkpoint subject invalid")
    return content_hash({"version": VERSION, "binding": binding,
        **{k: request.get(k, "") for k in ("accountId", "symbol", "worldId", "marketWorldId")}})


class CheckpointNotReady(EvidenceNotReady):
    def __init__(self, reason, producer=None):
        super().__init__("checkpoint", {"status": "incomplete"})
        self.code = "evidence-contract:observation-checkpoint-not-ready"
        self.conditions = [reason]
        self.producer = producer or {}


def freeze(session, capture_started_at=None):
    artifact = {"version": VERSION, "packet": session.packet(), "inventory": session.checkpoint_inventory()}
    if capture_started_at:
        artifact["captureStartedAt"] = capture_started_at
    if len(canonical_json(artifact).encode()) > MAX_BYTES:
        raise EvidenceContractError("checkpoint exceeds bounded inventory")
    # Reconstruct before persistence so malformed/incomplete inventories cannot
    # replace the last usable checkpoint.
    validate(artifact)
    return artifact


def validate(artifact):
    if artifact.get("version") != VERSION:
        raise EvidenceContractError("checkpoint version invalid")
    packet, inventory = artifact["packet"], artifact["inventory"]
    if artifact.get("captureStartedAt") and instant(artifact["captureStartedAt"]) > instant(packet["capturedAt"]):
        raise EvidenceContractError("checkpoint capture clocks reversed")
    session = ObservationEvidenceSession(packet, inventory)
    counts = {}
    for row in session.checkpoint_inventory():
        counts[row["evidenceCategory"]] = counts.get(row["evidenceCategory"], 0) + 1
    for category, coverage in packet["coverage"].items():
        if category != "unclassified" and counts.get(category, 0) != coverage["available"]:
            raise EvidenceContractError("checkpoint inventory incomplete")
    indexed = {row["id"]: row for row in session.checkpoint_inventory()}
    for fact in packet["facts"]:
        if fact["id"] not in indexed or any(indexed[fact["id"]].get(k) != v for k, v in fact.items()):
            raise EvidenceContractError("checkpoint selected evidence mismatch")
    return session


def newer_evidence_pending(artifact, request):
    """Do not ignore a committed change already known to this observation."""
    wake, packet = request.get("evidenceWake") or {}, artifact["packet"]
    target = wake.get("sourceSnapshotId")
    if not target or not wake.get("occurredAt"):
        return False
    if target in {packet["sourceSnapshotId"], "checkpoint:" + evidence_change_identity(packet, [])}:
        return False
    return instant(artifact.get("captureStartedAt", packet["capturedAt"])) < instant(wake["occurredAt"])


def reopen(artifact, request, binding, now, producer=None):
    session = validate(artifact)
    packet = session.packet()
    if scope(packet | {"marketWorldId": artifact.get("marketWorldId", "")}, packet.get("reasoningBinding", {})) != scope(request, binding):
        raise EvidenceContractError("checkpoint scope mismatch")
    age = (now - instant(packet["capturedAt"])).total_seconds()
    # This is a capture-age bound, not a renewal of any underlying source.
    maximum = 900 if request.get("capability") == "market-observe" else 86400
    if age < 0 or age > maximum:
        raise CheckpointNotReady("checkpoint-expired", producer)
    original_time = packet["capturedAt"]
    packet["capturedAt"] = now.isoformat().replace("+00:00", "Z")
    packet["quoteAssessment"] = quote_clock_assessment(packet["facts"], packet["capturedAt"])
    packet["evidenceCheckpoint"] = {"version": VERSION, "mode": "verified-graph-copy",
        "graphCapturedAt": original_time, "readAt": packet["capturedAt"], "ageSeconds": round(age, 3),
        "graphCaptureStartedAt": artifact.get("captureStartedAt", original_time),
        "sourceClocksPreserved": True, "requiresLiveInference": False,
        "use": "원천 시각을 유지한 근거입니다. 현재 사용 가능 여부는 자료별로 다시 확인합니다.",
        "producer": deepcopy(producer or {})}
    return ObservationEvidenceSession(packet, session.checkpoint_inventory())
