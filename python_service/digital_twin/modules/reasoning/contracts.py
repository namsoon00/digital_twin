"""Explicit, lazy contracts surface for the reasoning module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {}

_EXPORTS['ONTOLOGY_GRAPH_REPOSITORY_CONTRACT'] = ('digital_twin.modules.reasoning.domain.repositories', 'ONTOLOGY_GRAPH_REPOSITORY_CONTRACT')
_EXPORTS['OntologyGraphRepository'] = ('digital_twin.modules.reasoning.domain.repositories', 'OntologyGraphRepository')
_EXPORTS['ontology_graph_repository_contract_errors'] = ('digital_twin.modules.reasoning.domain.repositories', 'ontology_graph_repository_contract_errors')
_EXPORTS['ensure_ontology_graph_repository_contract'] = ('digital_twin.modules.reasoning.domain.repositories', 'ensure_ontology_graph_repository_contract')
_EXPORTS['OntologyProjectionRecorder'] = ('digital_twin.modules.reasoning.domain.repositories', 'OntologyProjectionRecorder')
_EXPORTS['OntologyProjectionAuditRepository'] = ('digital_twin.modules.reasoning.domain.repositories', 'OntologyProjectionAuditRepository')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
