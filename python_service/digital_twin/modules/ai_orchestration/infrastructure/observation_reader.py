"""Compatibility adapter; reasoning owns evidence discovery and coverage."""
from digital_twin.modules.reasoning.public import ObservationEvidenceReader, TypeDBObservationEvidenceSource


class GraphObservationReader(ObservationEvidenceReader):
    def __init__(self, repository, macro_world_id=""):
        super().__init__(TypeDBObservationEvidenceSource(repository), macro_world_id=macro_world_id)
