"""Rulebox history implementation; facade-independent dependencies."""

from __future__ import annotations
from .rulebox_history_ports import RuleboxHistoryPort, AppendRuleboxVersionBindings
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
)
from digital_twin.infrastructure.graph_store_rulebox import (
    add_rulebox_version_concept,
    rulebox_rules_from_payload,
)
from typing import Dict, List


def append_rulebox_version(
    _store: RuleboxHistoryPort,
    version: Dict[str, object],
    *,
    _bindings: AppendRuleboxVersionBindings,
) -> Dict[str, object]:
    """Append immutable RuleBox governance history without replacing it.

    A normal static graph save replaces every row in the boxes contained
    in the graph.  Version history must survive a later RuleBox edit, so
    this write deliberately has no delete phase.
    """
    if not _store.address:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return _store.driver_missing_result(
            imported[1], PortfolioOntology("typedb-rulebox-governance")
        )
    graph = PortfolioOntology("typedb-rulebox-governance")
    add_rulebox_version_concept(graph, version)
    if not graph.entities:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-version",
            "graphStore": "typedb",
            "reason": "RuleBox version payload is missing its ID.",
        }
    try:

        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.ensure_schema(driver, imported)
                _store.write_graph(driver, imported, graph, delete_boxes=[])
            finally:
                _store.close_driver(driver)

        _store.with_typedb_retries(operation)
    except (
        Exception
    ) as error:  # noqa: BLE001 - preserve a saved RuleBox even if its audit append failed.
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
            "versionId": str(version.get("id") or ""),
        }
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "graphStore": "typedb",
        "versionId": str(version.get("id") or ""),
    }


def save_rule_change_candidates(
    _store: RuleboxHistoryPort,
    candidates: List[Dict[str, object]],
    context: Dict[str, object] = None,
) -> Dict[str, object]:
    if not _store._last_rules:
        try:
            snapshot = _store.rulebox_snapshot()
            _store._last_rules = rulebox_rules_from_payload(
                {"rules": snapshot.get("rules") or []}
            )
        except ValueError:
            _store._last_rules = []
    normalized = [
        normalize_rule_change_candidate(
            candidate, existing_rule_ids=[rule.rule_id for rule in _store._last_rules]
        )
        for candidate in (candidates or [])
        if isinstance(candidate, dict)
    ]
    normalized = [item for item in normalized if item]
    if not normalized:
        return {
            "configured": bool(_store.address),
            "status": "no-candidates",
            "graphStore": "typedb",
            "candidateCount": 0,
            "savedCount": 0,
        }
    graph = PortfolioOntology("typedb-rule-change-candidates")
    for item in normalized:
        from digital_twin.domain.ontology_contracts import OntologyEntity

        graph.entities.append(
            OntologyEntity(
                "rule-change-candidate:"
                + str(item.get("id") or item.get("title") or len(graph.entities)),
                str(item.get("title") or "Rule change candidate"),
                "rule-change-candidate",
                {
                    "ontologyBox": "RuleBoxGovernance",
                    "boundedContext": "reasoning-insight",
                    "tboxClass": "RuleChangeCandidate",
                    "properties": item,
                },
            )
        )
    save_result = _store.save_graph(graph)
    return {
        "configured": bool(_store.address),
        "status": save_result.get("status"),
        "graphStore": "typedb",
        "candidateCount": len(normalized),
        "savedCount": len(normalized) if save_result.get("saved") else 0,
        "saveResult": save_result,
    }
