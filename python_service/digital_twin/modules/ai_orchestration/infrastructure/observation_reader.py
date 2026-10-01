"""Compatibility adapter; reasoning owns evidence discovery and coverage."""
from digital_twin.modules.reasoning.public import ObservationEvidenceReader, TypeDBObservationEvidenceSource


class GraphObservationReader(ObservationEvidenceReader):
    def __init__(self, repository):
        super().__init__(TypeDBObservationEvidenceSource(repository))
