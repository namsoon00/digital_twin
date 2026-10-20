"""Compatibility adapter; reasoning owns evidence discovery and coverage."""
from digital_twin.modules.reasoning.public import ObservationEvidenceReader, TypeDBObservationEvidenceSource, WorldRoutedObservationEvidenceSource


class GraphObservationReader(ObservationEvidenceReader):
    def __init__(self, repository, macro_world_id="", macro_repository=None):
        source = TypeDBObservationEvidenceSource(repository)
        if macro_world_id and macro_repository is not None and macro_repository is not repository:
            source = WorldRoutedObservationEvidenceSource(source,
                TypeDBObservationEvidenceSource(macro_repository), macro_world_id)
        super().__init__(source, macro_world_id=macro_world_id)
