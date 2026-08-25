"""Use cases and contracts for independently scheduled external datasets."""

from .contracts import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
    SourceObservation,
)
from .collection_service import ExternalDataCollectionService
from .read_model_service import ExternalSignalsReadModelService
from .registry import ExternalDatasetRegistry
from .research_evidence_projection_service import (
    ExternalFactResearchEvidenceReconciler,
    ExternalOfficialEvidenceProjectionService,
)

__all__ = [
    "CollectionJob",
    "CollectionPartition",
    "DatasetDescriptor",
    "ExternalDataCollectionService",
    "ExternalDatasetRegistry",
    "ExternalFactResearchEvidenceReconciler",
    "ExternalOfficialEvidenceProjectionService",
    "ExternalSignalsReadModelService",
    "ExternalSubject",
    "SourceObservation",
]
