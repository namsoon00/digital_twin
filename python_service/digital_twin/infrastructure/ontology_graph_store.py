from typing import Dict

from ..domain.repositories import ensure_ontology_graph_repository_contract
from .settings import runtime_settings


GRAPH_STORE_MODE = "typedb"


def normalized_graph_store_mode(settings: Dict[str, str] = None) -> str:
    return GRAPH_STORE_MODE


def ontology_repository_from_settings(settings: Dict[str, str] = None):
    settings = settings or runtime_settings()
    from .typedb_ontology import typedb_repository_from_settings

    return ensure_ontology_graph_repository_contract(
        typedb_repository_from_settings(settings),
        "TypeDB ontology graph repository",
    )
