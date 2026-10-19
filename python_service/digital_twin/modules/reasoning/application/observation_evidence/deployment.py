"""Resolve the active graph anew per capture, and reject a concurrent cutover."""
from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError
from .reads import read_evidence_stage


class DeploymentObservationEvidenceReader:
    def __init__(self, binding_reader, reader_factory):
        self.binding_reader, self.reader_factory = binding_reader, reader_factory

    def capture_session(self, request):
        before = read_evidence_stage("deployment-binding-before", self.binding_reader)
        session = self.reader_factory(before, request).capture_session(request)
        if read_evidence_stage("deployment-binding-after", self.binding_reader) != before:
            raise EvidenceContractError("observation graph deployment changed during capture")
        return session.with_reasoning_binding(before)
