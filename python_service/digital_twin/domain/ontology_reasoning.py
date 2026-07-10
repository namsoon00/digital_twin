from typing import Iterable, List

from .ontology_contracts import PortfolioOntology
from .ontology_graph_reasoner import run_graph_reasoner
from .ontology_rulebox_catalog import default_graph_inference_rules
from .ontology_rulebox_contracts import GraphInferenceRule
from .ontology_rulebox_projection import add_relation_rulebox_concepts, add_rulebox_concepts
from .ontology_rules import DEFAULT_RELATION_RULES


def apply_graph_reasoning(
    graph: PortfolioOntology,
    rules: Iterable[GraphInferenceRule] = None,
) -> List[GraphInferenceRule]:
    active_rules = list(default_graph_inference_rules() if rules is None else rules)
    add_rulebox_concepts(graph, active_rules)
    add_relation_rulebox_concepts(graph, DEFAULT_RELATION_RULES)
    run_graph_reasoner(graph, active_rules)
    return active_rules
