"""Independent evidence preparation and read-only consumption of verified copies."""
from datetime import datetime, timezone
from time import monotonic

from ..domain.observation_checkpoint import CheckpointNotReady, freeze, newer_evidence_pending, reopen, scope
from ..domain.observation_evidence import EvidenceContractError, EvidenceReadError


class CheckpointEvidenceReader:
    def __init__(self, store, binding_reader, clock=None):
        self.store, self.binding_reader = store, binding_reader
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def capture_session(self, request):
        binding, now = self.binding_reader(), self.clock()
        key = scope(request, binding)
        self.store.request(key, request, binding, now)
        artifact, producer = self.store.read(key)
        if not artifact:
            raise CheckpointNotReady("checkpoint-preparing", producer)
        if newer_evidence_pending(artifact, request):
            self.store.request(key, request, binding, now, force=True)
            raise CheckpointNotReady("newer-verified-evidence-pending", producer)
        session = reopen(artifact, request, binding, now, producer)
        if self.binding_reader() != binding:
            raise EvidenceContractError("checkpoint deployment changed during read")
        return session


class ObservationCheckpointService:
    def __init__(self, store, reader, binding_reader, subjects, enabled=lambda: True, clock=None):
        self.store, self.reader, self.binding_reader = store, reader, binding_reader
        self.subjects, self.enabled = subjects, enabled
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self):
        if not self.enabled():
            return {"status": "paused"}
        subjects, binding = list(self.subjects()), self.binding_reader()
        self.store.seed(subjects, binding, self.clock())
        job = self.store.claim()
        if not job:
            return {"status": "idle"}
        try:
            current = next((s for s in subjects if all(s.get(k, "") == job["request"].get(k, "")
                for k in ("accountId", "symbol", "worldId", "marketWorldId"))), None)
            if not current or self.binding_reader() != job["binding"]:
                self.store.retire(job)
                return {"status": "retired"}
            started, capture_started = monotonic(), self.clock().isoformat().replace("+00:00", "Z")
            session = self.reader.capture_session({**job["request"], "name": current.get("name", job["request"].get("name", ""))})
            if session.packet().get("reasoningBinding") != job["binding"]:
                raise EvidenceContractError("checkpoint capture binding changed")
            artifact = freeze(session, capture_started)
            artifact["marketWorldId"] = job["request"].get("marketWorldId", "")
            artifact["preparationMetrics"] = {"captureMs": round((monotonic() - started) * 1000, 3),
                "queueWaitMs": job.get("queueWaitMs"), "modelCalls": 0}
            saved = self.store.complete(job, artifact)
            return {"status": "prepared" if saved else "lease-lost"}
        except Exception as error:
            # Never expose vendor SQL, account identifiers or original content.
            reason = error.code if isinstance(error, (EvidenceContractError, EvidenceReadError)) else type(error).__name__
            saved = self.store.fail(job, reason)
            return {"status": "deferred" if saved else "lease-lost", "reason": reason}
