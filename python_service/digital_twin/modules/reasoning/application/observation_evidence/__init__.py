"""Public read use case for independent observation evidence."""
from datetime import datetime, timezone
from contextlib import nullcontext
from typing import Protocol

from digital_twin.modules.reasoning.domain.observation_evidence import (
    EvidenceContractError, EVIDENCE_PROFILE, EVIDENCE_PROTOCOL, MACRO_KINDS, select_evidence, validate_evidence_packet,
    quote_clock_assessment,
)
from .reads import read_evidence_stage


class ObservationEvidenceSource(Protocol):
    def metadata(self, world_id): ...
    def snapshot_id(self, world_id): ...
    def candidates(self, world_id, symbol): ...


class ObservationEvidenceReader:
    def __init__(self, source: ObservationEvidenceSource, clock=None):
        self.source = source
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def __call__(self, request):
        return read_evidence_stage("snapshot", lambda: self._read_snapshot(request))

    def _read_snapshot(self, request):
        capture = getattr(type(self.source), "capture", None)
        with capture(self.source) if callable(capture) else nullcontext():
            return self._capture(request)

    def _capture(self, request):
        world = request["worldId"]
        metadata = read_evidence_stage("metadata", lambda: self.source.metadata(world))
        if metadata.get("status") != "ok" or not metadata.get("aboxSnapshotId"):
            raise EvidenceContractError("portfolio graph not ready")
        if metadata.get("accountId") and metadata["accountId"] != request["accountId"]:
            raise EvidenceContractError("graph account mismatch")
        worlds = [world]
        shared = metadata.get("sharedPremiseWorldId")
        if shared:
            if not shared.startswith("premise:"):
                raise EvidenceContractError("invalid shared evidence world")
            worlds.append(shared)
        versions = {key: read_evidence_stage("snapshot-before", lambda: self.source.snapshot_id(key)) for key in worlds}
        if not all(versions.values()) or versions[world] != metadata["aboxSnapshotId"]:
            raise EvidenceContractError("graph changed before capture")
        candidates = []
        for key in worlds:
            for row in read_evidence_stage("candidates", lambda: self.source.candidates(key, request["symbol"])):
                if row.get("accountId") and row["accountId"] != request["accountId"]:
                    raise EvidenceContractError("graph fact account mismatch")
                if row.get("worldId") and row["worldId"] != key:
                    raise EvidenceContractError("graph fact world mismatch")
                if row.get("symbol") and row["symbol"] != request["symbol"] and row.get("kind") not in MACRO_KINDS:
                    raise EvidenceContractError("graph fact subject mismatch")
                candidates.append({**row, "id": key + ":" + row["id"], "sourceEntityId": row["id"],
                                   "sourceWorldId": key, "sourceSnapshotId": versions[key]})
        if any(read_evidence_stage("snapshot-after", lambda: self.source.snapshot_id(key)) != value for key, value in versions.items()):
            raise EvidenceContractError("graph changed during capture")
        facts, coverage = select_evidence(candidates)
        packet = {**{key: request[key] for key in ("accountId", "symbol", "name", "worldId")},
            "protocolVersion": EVIDENCE_PROTOCOL, "profile": EVIDENCE_PROFILE,
            "sourceSnapshotId": versions[world], "sourceSnapshots": versions,
            "capturedAt": self.clock().isoformat().replace("+00:00", "Z"),
            "facts": facts, "coverage": coverage, "availableFactCount": sum(row["available"] for row in coverage.values()),
            "includedFactCount": len(facts), "requiresMatchedRule": False}
        packet["quoteAssessment"] = quote_clock_assessment(facts, packet["capturedAt"])
        validate_evidence_packet(packet)
        return packet
