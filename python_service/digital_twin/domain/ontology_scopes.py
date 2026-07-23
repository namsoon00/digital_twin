"""Scoped ABox lifecycle contracts.

The active investment world is a manifest of independently versioned ABox
scopes.  This keeps the atomic/rollback properties of immutable generations
without rewriting unrelated symbols after every market observation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from typing import Dict, Iterable, List, Mapping, MutableMapping, Set, Tuple

from .ontology_change_impact import (
    family_for_entity,
    family_for_relation,
    macro_scope_id,
    scope_family,
    scope_symbol,
    symbol_scope_id,
)
from .ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from .ontology_projection_fingerprint import stable_value
from .ontology_worlds import world_scoped_scope_id


SCOPED_ABOX_MANIFEST_VERSION = "scoped-manifest-v1"
SCOPED_ABOX_PERSISTENCE_MODE = "immutable-scoped-manifest"
SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION = "granular-v3-relation-links"

REFERENCE_SCOPE_ID = "reference:global"
MACRO_SCOPE_ID = "macro:global"

_SYMBOL_PREFIXES = (
    "stock:",
    "position:",
    "price-",
    "volume-",
    "execution-",
    "key-level:",
    "trend-",
    "temporal-",
    "liquidity-",
    "smart-money-",
    "investor-",
    "valuation-",
    "security-line-",
    "instrument-",
    "data-latency:",
    "fact-change:",
)

_MACRO_KINDS = {
    "fx-rate",
    "interest-rate",
    "yield-curve",
    "macro-indicator",
    "macro-regime",
    "crypto-asset",
    "market-proxy",
    "market-index",
    "market-proxy-instrument",
    "market-proxy-observation",
    "market-proxy-theme",
}

_POLICY_KINDS = {
    "collection-policy",
    "collection-schedule",
    "data-pipeline",
    "data-pipeline-health",
    "notification-dispatch",
    "insight-policy",
    "importance-gate",
    "novelty-policy",
    "cooldown-policy",
    "suppression-policy",
    "reasoning-cycle",
    "analysis-job",
    "runtime-setting",
    "runtime-metadata",
    "account-delivery-profile",
    "investment-strategy-profile",
    "risk-budget",
    "profit-policy",
}

_EPISODE_TOKENS = (
    "decision",
    "hypothesis",
    "outcome",
    "learning",
    "research-run",
    "investment-question",
)

_EVIDENCE_TOKENS = (
    "news",
    "disclosure",
    "research",
    "claim",
    "article",
    "document",
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _symbol(value: object) -> str:
    return _clean(value).upper()


def _scope_type(scope_id: str) -> str:
    return _clean(scope_id).split(":", 1)[0] or "reference"


def _account_id(graph: PortfolioOntology) -> str:
    return _clean(graph.portfolio_id) or "portfolio"


def _scope_id(scope_type: str, value: str = "") -> str:
    if scope_type == "reference":
        return REFERENCE_SCOPE_ID
    if scope_type == "macro":
        return macro_scope_id(value)
    if scope_type == "symbol":
        return symbol_scope_id(value, "state")
    clean_value = _clean(value) or "global"
    return scope_type + ":" + clean_value


def relation_link_scope_id(
    source_scope: object,
    target_scope: object,
    account_id: object = "",
    symbol: object = "",
) -> str:
    """Return a relation-only owner scope for a cross-scope ABox edge.

    Immutable TypeDB generations give every node a generation-scoped storage
    identity. Storing a cross-scope edge beside either endpoint therefore
    forced that endpoint's whole fact family to roll whenever the other side
    changed. A relation-only link scope owns the edge instead: endpoint facts
    remain independently versioned and only the link is rebound.
    """

    clean_symbol = _symbol(symbol)
    if not clean_symbol:
        clean_symbol = scope_symbol(source_scope) or scope_symbol(target_scope)
    if clean_symbol:
        return symbol_scope_id(clean_symbol, "link")
    return _scope_id("link", _clean(account_id) or "global")


def _id_symbol(entity_id: object) -> str:
    text = _clean(entity_id)
    if not text:
        return ""
    parts = [part.strip().upper() for part in text.split(":") if part.strip()]
    excluded = {"GLOBAL", "UNKNOWN", "MAIN", "KR", "US", "USD", "KRW"}
    # Korean symbols are unambiguous, and they often occur after a semantic
    # prefix such as ``price-metric`` or ``trend-scenario``.
    for candidate in parts[1:]:
        if re.fullmatch(r"\d{4,8}", candidate):
            return candidate
    # For overseas instruments, the first ticker-shaped segment after the
    # ontology kind is the subject. Later segments are usually field names
    # (for example ``averagePrice``), so do not scan the whole ID greedily.
    for candidate in parts[1:2]:
        if candidate in excluded:
            continue
        if re.fullmatch(r"[A-Z]{1,6}(?:[.-][A-Z0-9]{1,5})?", candidate):
            return candidate
    return ""


def _explicit_entity_scope(entity: OntologyEntity, account_id: str) -> str:
    properties = dict(entity.properties or {})
    explicit = _clean(properties.get("aboxScopeId"))
    if explicit:
        return explicit
    kind = _clean(entity.kind).lower()
    family = family_for_entity(kind, properties, entity.entity_id)
    # Market-wide instruments can carry a ticker-like identifier (BTC, an FX
    # pair, an index). Their world ownership is still macro, not a portfolio
    # stock scope.
    if kind in _MACRO_KINDS or family.startswith("macro-"):
        return macro_scope_id(family)
    if kind in _POLICY_KINDS:
        return _scope_id("policy", account_id)
    if kind in {"portfolio", "account", "watchlist", "cash"}:
        return _scope_id("portfolio", account_id)
    symbol = _symbol(properties.get("symbol"))
    if symbol:
        return symbol_scope_id(symbol, family)
    if any(token in kind for token in _EPISODE_TOKENS):
        return _scope_id("episode", account_id)
    if any(token in kind for token in _EVIDENCE_TOKENS):
        return _scope_id("evidence", account_id)
    entity_id = _clean(entity.entity_id).lower()
    if entity_id.startswith(_SYMBOL_PREFIXES):
        candidate = _id_symbol(entity.entity_id)
        if candidate:
            return symbol_scope_id(candidate, family)
    return ""


def _seed_entity_scopes(graph: PortfolioOntology) -> Dict[str, str]:
    account_id = _account_id(graph)
    scopes: Dict[str, str] = {}
    for entity in graph.entities:
        scope_id = _explicit_entity_scope(entity, account_id)
        if scope_id:
            scopes[_clean(entity.entity_id)] = scope_id
    return scopes


def _scope_rank(scope_id: str) -> Tuple[int, str]:
    # Lower ranks own a cross-scope relation.  Dynamic symbol facts must win
    # over static reference context so an updated price does not rewrite a
    # sector/catalog generation.
    ranks = {
        "symbol": 0,
        "portfolio": 1,
        "evidence": 2,
        "macro": 3,
        "episode": 4,
        "policy": 5,
        "reference": 6,
    }
    scope_type = _scope_type(scope_id)
    return (ranks.get(scope_type, 7), scope_id)


def _propagate_entity_scopes(graph: PortfolioOntology, scopes: MutableMapping[str, str]) -> None:
    # Most fact entities have a direct symbol.  A small number inherit it via
    # their single stock relation, so propagate only unambiguous neighbours.
    neighbours: Dict[str, Set[str]] = defaultdict(set)
    for relation in graph.relations:
        source = _clean(relation.source)
        target = _clean(relation.target)
        if source and target:
            neighbours[source].add(target)
            neighbours[target].add(source)
    for _ in range(3):
        changed = False
        for entity in graph.entities:
            entity_id = _clean(entity.entity_id)
            if not entity_id or entity_id in scopes:
                continue
            candidates = {
                scope_symbol(scopes[neighbour])
                for neighbour in neighbours.get(entity_id, set())
                if neighbour in scopes and scope_symbol(scopes[neighbour])
            }
            if len(candidates) == 1:
                scopes[entity_id] = symbol_scope_id(
                    next(iter(candidates)),
                    family_for_entity(entity.kind, entity.properties, entity.entity_id),
                )
                changed = True
        if not changed:
            break
    for entity in graph.entities:
        entity_id = _clean(entity.entity_id)
        if entity_id and entity_id not in scopes:
            scopes[entity_id] = REFERENCE_SCOPE_ID


def scope_id_for_relation(
    relation: OntologyRelation,
    entity_scopes: Mapping[str, str],
    account_id: str,
    entities_by_id: Mapping[str, OntologyEntity] = None,
) -> str:
    properties = dict(relation.properties or {})
    source_id = _clean(relation.source)
    target_id = _clean(relation.target)
    source_scope = entity_scopes.get(source_id, "")
    target_scope = entity_scopes.get(target_id, "")
    source_symbol = scope_symbol(source_scope)
    target_symbol = scope_symbol(target_scope)
    symbol = _symbol(properties.get("symbol"))
    macro_scopes = [
        scope_id
        for scope_id in [source_scope, target_scope]
        if _scope_type(scope_id) == "macro"
    ]
    # Cross-scope edges must never share an endpoint's entity scope. Otherwise
    # a fresh endpoint generation forces the full owner scope, and then every
    # edge pointing at that owner, to roll forward recursively.
    if source_scope and target_scope and source_scope != target_scope:
        # Market-proxy relations may carry an observed ticker only for
        # provenance. They are still a global cross-scope edge, not a holding
        # or watchlist link for that ticker.
        if symbol and macro_scopes and not source_symbol and not target_symbol:
            return _scope_id("link", account_id)
        return relation_link_scope_id(
            source_scope,
            target_scope,
            account_id,
            symbol,
        )
    explicit = _clean(properties.get("aboxScopeId"))
    if explicit:
        return explicit
    source_entity = (entities_by_id or {}).get(source_id)
    target_entity = (entities_by_id or {}).get(target_id)
    relation_family = family_for_relation(
        relation.relation_type,
        properties,
        scope_family(source_scope),
        scope_family(target_scope),
        getattr(source_entity, "kind", ""),
        getattr(target_entity, "kind", ""),
    )
    # Market-proxy relationships carry their observed ticker for provenance.
    # That ticker must not turn a global market sensor into a pseudo holding.
    if symbol and macro_scopes and not source_symbol and not target_symbol:
        return sorted(macro_scopes, key=_scope_rank)[0]
    if symbol:
        return symbol_scope_id(symbol, relation_family)
    if source_symbol and source_symbol == target_symbol:
        return symbol_scope_id(source_symbol, relation_family)
    if source_symbol:
        return symbol_scope_id(source_symbol, relation_family)
    if target_symbol:
        return symbol_scope_id(target_symbol, relation_family)
    candidates = [
        source_scope,
        target_scope,
    ]
    candidates = [item for item in candidates if item]
    if candidates:
        return sorted(candidates, key=_scope_rank)[0]
    return _scope_id("portfolio", account_id)


def scope_id_for_evidence(evidence: OntologyEvidence, entity_scopes: Mapping[str, str], account_id: str) -> str:
    properties = dict(evidence.value or {})
    explicit = _clean(properties.get("aboxScopeId"))
    if explicit:
        return explicit
    subject_scope = entity_scopes.get(_clean(evidence.subject), "")
    symbol = _symbol(properties.get("symbol"))
    if not symbol:
        symbol = scope_symbol(subject_scope)
    if symbol:
        return symbol_scope_id(symbol, "evidence")
    if subject_scope:
        return subject_scope
    return _scope_id("evidence", account_id)


def support_relation_key(relation_type: object, source: object, target: object) -> str:
    return "|".join([
        _clean(relation_type).upper(),
        _clean(source),
        _clean(target),
    ])


def _support_relation_specs(
    graph: PortfolioOntology,
    node_scopes: Mapping[str, str],
    account_id: str,
    world_id: str,
) -> List[Dict[str, object]]:
    """Describe generated ABox support edges before persistence row mapping.

    Evidence is represented as an ABox node while ``HAS_EVIDENCE`` is created
    by the repository row mapper. Keeping a matching lightweight description
    here lets the scoped manifest own and refresh that edge independently from
    both the subject and the evidence node.
    """

    rows: List[Dict[str, object]] = []
    for evidence in graph.evidence:
        values = dict(evidence.value or {})
        if _clean(values.get("ontologyBox")) not in {"", "ABox"}:
            continue
        source = _clean(evidence.subject)
        target = _clean(evidence.evidence_id)
        source_scope = node_scopes.get(source, "")
        target_scope = node_scopes.get(target, "")
        if not source or not target or not source_scope or not target_scope:
            continue
        scope_id = relation_link_scope_id(
            source_scope,
            target_scope,
            account_id,
            values.get("symbol"),
        )
        if world_id:
            scope_id = world_scoped_scope_id(scope_id, world_id)
        rows.append({
            "key": support_relation_key("HAS_EVIDENCE", source, target),
            "source": source,
            "target": target,
            "type": "HAS_EVIDENCE",
            "scopeId": scope_id,
            "impactFamilies": ["evidence"],
            "properties": {
                "kind": _clean(evidence.kind),
                "source": _clean(evidence.source),
                "evidenceRole": _clean(evidence.evidence_role),
                "dataState": _clean(evidence.data_state),
            },
        })
    return rows


def _scope_fragment_payload(
    graph: PortfolioOntology,
    scope_id: str,
    support_relations: Iterable[Mapping[str, object]] = (),
) -> Dict[str, object]:
    entities = [
        {
            "id": entity.entity_id,
            "kind": entity.kind,
            "properties": stable_value(entity.properties),
        }
        for entity in graph.entities
        if _clean((entity.properties or {}).get("aboxScopeId")) == scope_id
    ]
    relations = [
        {
            "source": relation.source,
            "target": relation.target,
            "type": relation.relation_type,
            "properties": stable_value(relation.properties),
        }
        for relation in graph.relations
        if _clean((relation.properties or {}).get("aboxScopeId")) == scope_id
    ]
    relations.extend({
        "source": _clean(item.get("source")),
        "target": _clean(item.get("target")),
        "type": _clean(item.get("type")),
        "properties": stable_value(dict(item.get("properties") or {})),
    } for item in support_relations if _clean(item.get("scopeId")) == scope_id)
    evidence = [
        {
            "id": item.evidence_id,
            "subject": item.subject,
            "kind": item.kind,
            "source": item.source,
            "summary": item.summary,
            "value": stable_value(item.value),
        }
        for item in graph.evidence
        if _clean((item.value or {}).get("aboxScopeId")) == scope_id
    ]
    return {
        "entities": sorted(entities, key=lambda item: (str(item["kind"]), str(item["id"]))),
        "relations": sorted(relations, key=lambda item: (str(item["type"]), str(item["source"]), str(item["target"]))),
        "evidence": sorted(evidence, key=lambda item: (str(item["kind"]), str(item["id"]))),
    }


def scoped_generation_id(scope_id: str, fingerprint: str) -> str:
    digest = hashlib.sha256((scope_id + "|" + fingerprint).encode("utf-8")).hexdigest()[:20]
    return "abox-scope:" + digest


def scoped_manifest_id(account_id: str, scope_generations: Mapping[str, str], world_id: str = "") -> str:
    payload = json.dumps(dict(sorted(scope_generations.items())), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((str(world_id or account_id) + "|" + account_id + "|" + payload).encode("utf-8")).hexdigest()[:20]
    return "abox-manifest:" + digest


def apply_scoped_abox_identity(
    graph: PortfolioOntology,
    account_id: str = "",
    world_id: str = "",
    tenant_id: str = "",
    world_type: str = "",
    world_account_id: str = None,
) -> Dict[str, object]:
    """Annotate one complete ABox graph with independent scope generations.

    The graph remains complete in memory for validation and AI context.  The
    repository uses ``scopePlan`` to persist only scopes whose fingerprints
    changed since the active Manifest.
    """
    clone = graph
    account_key = _clean(account_id) or _account_id(clone)
    metadata_account_id = account_key if world_account_id is None else _clean(world_account_id)
    clean_world_id = _clean(world_id)
    clean_tenant_id = _clean(tenant_id)
    clean_world_type = _clean(world_type)
    if clean_world_id:
        for entity in clone.entities:
            if _clean((entity.properties or {}).get("ontologyBox")) in {"", "ABox"}:
                entity.properties.update({
                    "worldId": clean_world_id,
                    "worldType": clean_world_type or "portfolio",
                    "tenantId": clean_tenant_id,
                    "accountId": metadata_account_id,
                })
        for relation in clone.relations:
            if _clean((relation.properties or {}).get("ontologyBox")) in {"", "ABox"}:
                relation.properties.update({
                    "worldId": clean_world_id,
                    "worldType": clean_world_type or "portfolio",
                    "tenantId": clean_tenant_id,
                    "accountId": metadata_account_id,
                })
        for evidence in clone.evidence:
            if _clean((evidence.value or {}).get("ontologyBox")) in {"", "ABox"}:
                evidence.value.update({
                    "worldId": clean_world_id,
                    "worldType": clean_world_type or "portfolio",
                    "tenantId": clean_tenant_id,
                    "accountId": metadata_account_id,
                })
    entity_scopes = _seed_entity_scopes(clone)
    _propagate_entity_scopes(clone, entity_scopes)
    if clean_world_id:
        entity_scopes = {
            entity_id: world_scoped_scope_id(scope_id, clean_world_id)
            for entity_id, scope_id in entity_scopes.items()
        }
    entities_by_id = {
        _clean(entity.entity_id): entity
        for entity in clone.entities
        if _clean(entity.entity_id)
    }

    for entity in clone.entities:
        if _clean((entity.properties or {}).get("ontologyBox")) not in {"", "ABox"}:
            continue
        scope_id = entity_scopes.get(_clean(entity.entity_id), REFERENCE_SCOPE_ID)
        entity.properties["aboxScopeId"] = scope_id
        entity.properties["aboxScopeType"] = _scope_type(scope_id)
        entity.properties["aboxScopeFamily"] = scope_family(scope_id)

    for relation in clone.relations:
        if _clean((relation.properties or {}).get("ontologyBox")) not in {"", "ABox"}:
            continue
        scope_id = scope_id_for_relation(relation, entity_scopes, account_key, entities_by_id)
        if clean_world_id:
            scope_id = world_scoped_scope_id(scope_id, clean_world_id)
        relation.properties["aboxScopeId"] = scope_id
        relation.properties["aboxScopeType"] = _scope_type(scope_id)
        relation.properties["aboxScopeFamily"] = scope_family(scope_id)

    for evidence in clone.evidence:
        if _clean((evidence.value or {}).get("ontologyBox")) not in {"", "ABox"}:
            continue
        scope_id = scope_id_for_evidence(evidence, entity_scopes, account_key)
        if clean_world_id:
            scope_id = world_scoped_scope_id(scope_id, clean_world_id)
        evidence.value["aboxScopeId"] = scope_id
        evidence.value["aboxScopeType"] = _scope_type(scope_id)
        evidence.value["aboxScopeFamily"] = scope_family(scope_id)

    # ABox evidence is stored as a node, while HAS_EVIDENCE is generated by
    # the persistence mapper. Include its endpoint identity in the same scope
    # plan so a fresh subject node can rebind the support edge without rolling
    # the evidence node generation itself.
    node_scopes = dict(entity_scopes)
    node_scopes.update({
        _clean(evidence.evidence_id): _clean((evidence.value or {}).get("aboxScopeId"))
        for evidence in clone.evidence
        if _clean((evidence.value or {}).get("ontologyBox")) in {"", "ABox"}
        and _clean(evidence.evidence_id)
        and _clean((evidence.value or {}).get("aboxScopeId"))
    })
    support_relations = _support_relation_specs(
        clone,
        node_scopes,
        account_key,
        clean_world_id,
    )

    scope_ids = sorted({
        _clean((entity.properties or {}).get("aboxScopeId"))
        for entity in clone.entities
        if _clean((entity.properties or {}).get("ontologyBox")) in {"", "ABox"}
    } | {
        _clean((relation.properties or {}).get("aboxScopeId"))
        for relation in clone.relations
        if _clean((relation.properties or {}).get("ontologyBox")) in {"", "ABox"}
    } | {
        _clean((evidence.value or {}).get("aboxScopeId"))
        for evidence in clone.evidence
        if _clean((evidence.value or {}).get("ontologyBox")) in {"", "ABox"}
    } | {
        _clean(item.get("scopeId"))
        for item in support_relations
    })
    scope_ids = [scope_id for scope_id in scope_ids if scope_id]
    # Cross-scope assertions now live in relation-only link scopes. A link
    # rolls when either endpoint's local fact generation changes, but it never
    # rolls an endpoint's entity scope in return. Legacy embedded cross-scope
    # ownership still uses the conservative recursive closure below.
    payloads = {
        scope_id: _scope_fragment_payload(clone, scope_id, support_relations)
        for scope_id in scope_ids
    }
    base_fingerprints = {
        scope_id: hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for scope_id, payload in payloads.items()
    }
    dependency_graph: Dict[str, Set[str]] = {scope_id: set() for scope_id in scope_ids}
    scope_impact_families: Dict[str, Set[str]] = {
        scope_id: {scope_family(scope_id)}
        for scope_id in scope_ids
    }
    for relation in clone.relations:
        properties = dict(relation.properties or {})
        if _clean(properties.get("ontologyBox")) not in {"", "ABox"}:
            continue
        owner_scope = _clean(properties.get("aboxScopeId"))
        if owner_scope not in dependency_graph:
            continue
        source_entity = entities_by_id.get(_clean(relation.source))
        target_entity = entities_by_id.get(_clean(relation.target))
        relation_family = family_for_relation(
            relation.relation_type,
            properties,
            scope_family(node_scopes.get(_clean(relation.source), "")),
            scope_family(node_scopes.get(_clean(relation.target), "")),
            getattr(source_entity, "kind", ""),
            getattr(target_entity, "kind", ""),
        )
        if relation_family:
            scope_impact_families[owner_scope].add(relation_family)
        for endpoint in (_clean(relation.source), _clean(relation.target)):
            endpoint_scope = node_scopes.get(endpoint, "")
            if endpoint_scope and endpoint_scope != owner_scope and endpoint_scope in dependency_graph:
                dependency_graph[owner_scope].add(endpoint_scope)

    for relation in support_relations:
        owner_scope = _clean(relation.get("scopeId"))
        if owner_scope not in dependency_graph:
            continue
        for family in relation.get("impactFamilies") or []:
            clean_family = _clean(family)
            if clean_family:
                scope_impact_families[owner_scope].add(clean_family)
        for endpoint in (_clean(relation.get("source")), _clean(relation.get("target"))):
            endpoint_scope = node_scopes.get(endpoint, "")
            if endpoint_scope and endpoint_scope != owner_scope and endpoint_scope in dependency_graph:
                dependency_graph[owner_scope].add(endpoint_scope)

    def dependency_closure(scope_id: str) -> List[str]:
        visited: Set[str] = set()
        pending = list(dependency_graph.get(scope_id, set()))
        while pending:
            candidate = pending.pop()
            if candidate == scope_id or candidate in visited:
                continue
            visited.add(candidate)
            pending.extend(dependency_graph.get(candidate, set()))
        return sorted(visited)

    def scope_dependencies(scope_id: str) -> List[str]:
        direct = sorted(dependency_graph.get(scope_id, set()))
        # Relation-only scopes have no endpoint nodes of their own, so direct
        # endpoint fingerprints are sufficient and avoid a whole-graph roll.
        # Retain the old closure for an unexpected legacy embedded owner until
        # it is migrated to a link scope, preserving storage-ID correctness.
        if not payloads[scope_id]["entities"] and not payloads[scope_id]["evidence"]:
            return direct
        return dependency_closure(scope_id)

    scope_plan: List[Dict[str, object]] = []
    generations: Dict[str, str] = {}
    for scope_id in scope_ids:
        dependencies = scope_dependencies(scope_id)
        fingerprint_payload = {
            "baseFingerprint": base_fingerprints[scope_id],
            "dependencyBaseFingerprints": [
                {"scopeId": dependency, "fingerprint": base_fingerprints[dependency]}
                for dependency in dependencies
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        generation_id = scoped_generation_id(scope_id, fingerprint)
        generations[scope_id] = generation_id
        scope_plan.append({
            "scopeId": scope_id,
            "scopeType": _scope_type(scope_id),
            "scopeFamily": scope_family(scope_id),
            "impactScopeFamilies": sorted(scope_impact_families.get(scope_id) or {scope_family(scope_id)}),
            "fingerprint": fingerprint,
            "baseFingerprint": base_fingerprints[scope_id],
            "dependencyScopeIds": dependencies,
            "generationId": generation_id,
            "entityCount": len(payloads[scope_id]["entities"]),
            "relationCount": len(payloads[scope_id]["relations"]),
            "evidenceCount": len(payloads[scope_id]["evidence"]),
        })

    manifest_id = scoped_manifest_id(account_key, generations, clean_world_id)
    by_scope = {item["scopeId"]: item for item in scope_plan}
    for entity in clone.entities:
        scope_id = _clean((entity.properties or {}).get("aboxScopeId"))
        if scope_id in by_scope:
            entity.properties.update({
                "scopeGenerationId": by_scope[scope_id]["generationId"],
                "worldviewManifestId": manifest_id,
                "snapshotId": by_scope[scope_id]["generationId"],
                "aboxSnapshotId": by_scope[scope_id]["generationId"],
            })
    for relation in clone.relations:
        scope_id = _clean((relation.properties or {}).get("aboxScopeId"))
        if scope_id in by_scope:
            relation.properties.update({
                "scopeGenerationId": by_scope[scope_id]["generationId"],
                "worldviewManifestId": manifest_id,
                "snapshotId": by_scope[scope_id]["generationId"],
                "aboxSnapshotId": by_scope[scope_id]["generationId"],
            })
    for evidence in clone.evidence:
        scope_id = _clean((evidence.value or {}).get("aboxScopeId"))
        if scope_id in by_scope:
            evidence.value.update({
                "scopeGenerationId": by_scope[scope_id]["generationId"],
                "worldviewManifestId": manifest_id,
                "snapshotId": by_scope[scope_id]["generationId"],
                "aboxSnapshotId": by_scope[scope_id]["generationId"],
            })

    support_relation_scopes = {
        _clean(relation.get("key")): {
            "scopeId": _clean(relation.get("scopeId")),
            "scopeType": _scope_type(_clean(relation.get("scopeId"))),
            "scopeGenerationId": _clean((by_scope.get(_clean(relation.get("scopeId"))) or {}).get("generationId")),
            "snapshotId": _clean((by_scope.get(_clean(relation.get("scopeId"))) or {}).get("generationId")),
            "aboxSnapshotId": _clean((by_scope.get(_clean(relation.get("scopeId"))) or {}).get("generationId")),
            "manifestId": manifest_id,
        }
        for relation in support_relations
        if _clean(relation.get("key"))
        and _clean(relation.get("scopeId")) in by_scope
    }

    scope_family_counts: Dict[str, int] = {}
    for item in scope_plan:
        family = _clean(item.get("scopeFamily")) or "reference"
        scope_family_counts[family] = scope_family_counts.get(family, 0) + 1
    clone.worldview.update({
        "worldId": clean_world_id,
        "worldType": clean_world_type or ("portfolio" if clean_world_id else ""),
        "tenantId": clean_tenant_id,
        "accountId": metadata_account_id,
        "aboxSnapshotId": manifest_id,
        "snapshotId": manifest_id,
        "worldviewManifestId": manifest_id,
        "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
        "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
        "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
        "scopePlan": scope_plan,
        "scopeGenerationIds": generations,
        "scopeFingerprints": {item["scopeId"]: item["fingerprint"] for item in scope_plan},
        "scopeFamilyCounts": dict(sorted(scope_family_counts.items())),
        "supportRelationScopes": support_relation_scopes,
    })
    return {
        "worldId": clean_world_id,
        "worldType": clean_world_type or ("portfolio" if clean_world_id else ""),
        "tenantId": clean_tenant_id,
        "accountId": metadata_account_id,
        "manifestId": manifest_id,
        "scopePlan": scope_plan,
        "scopeGenerationIds": generations,
        "scopeFingerprints": {item["scopeId"]: item["fingerprint"] for item in scope_plan},
        "scopeFamilyCounts": dict(sorted(scope_family_counts.items())),
    }


def scoped_manifest_material_fingerprint(scope_plan: Iterable[object]) -> str:
    """Return the material identity for the active scoped ABox manifest.

    A partial projection deliberately retains generations outside the current
    target symbols.  The persisted manifest, rather than the complete source
    snapshot, is therefore the only honest material identity for that cycle.
    """

    rows = [
        dict(item)
        for item in scope_plan or []
        if isinstance(item, Mapping) and _clean(item.get("scopeId"))
    ]
    payload = {
        "scopeGenerations": {
            _clean(item.get("scopeId")): _clean(item.get("generationId"))
            for item in rows
            if _clean(item.get("generationId"))
        },
        "scopeFingerprints": {
            _clean(item.get("scopeId")): _clean(item.get("fingerprint"))
            for item in rows
            if _clean(item.get("fingerprint"))
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scope_plan_by_id(
    scope_plan: Iterable[object],
    scope_generations: Mapping[str, object] = None,
    scope_fingerprints: Mapping[str, object] = None,
) -> Dict[str, Dict[str, object]]:
    """Normalize manifest rows while accepting older marker metadata."""

    generations = dict(scope_generations or {})
    fingerprints = dict(scope_fingerprints or {})
    rows: Dict[str, Dict[str, object]] = {}
    for raw in scope_plan or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        scope_id = _clean(row.get("scopeId"))
        if not scope_id:
            continue
        row["scopeId"] = scope_id
        row["generationId"] = _clean(row.get("generationId")) or _clean(generations.get(scope_id))
        row["fingerprint"] = _clean(row.get("fingerprint")) or _clean(fingerprints.get(scope_id))
        row["scopeType"] = _clean(row.get("scopeType")) or _scope_type(scope_id)
        row["scopeFamily"] = _clean(row.get("scopeFamily")) or scope_family(scope_id)
        row["dependencyScopeIds"] = sorted({
            _clean(value)
            for value in row.get("dependencyScopeIds") or []
            if _clean(value)
        })
        row["impactScopeFamilies"] = sorted({
            _clean(value)
            for value in row.get("impactScopeFamilies") or []
            if _clean(value)
        } or {row["scopeFamily"]})
        rows[scope_id] = row
    return rows


def _scope_plan_counts(scope_plan: Iterable[object]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in scope_plan or []:
        if not isinstance(item, Mapping):
            continue
        family = _clean(item.get("scopeFamily")) or scope_family(item.get("scopeId"))
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _graph_node_scopes(graph: PortfolioOntology) -> Dict[str, str]:
    scopes: Dict[str, str] = {}
    for entity in graph.entities:
        scope_id = _clean((entity.properties or {}).get("aboxScopeId"))
        if scope_id and _clean(entity.entity_id):
            scopes[_clean(entity.entity_id)] = scope_id
    for evidence in graph.evidence:
        scope_id = _clean((evidence.value or {}).get("aboxScopeId"))
        if scope_id and _clean(evidence.evidence_id):
            scopes[_clean(evidence.evidence_id)] = scope_id
    return scopes


def select_target_scoped_manifest_patch(
    graph: PortfolioOntology,
    active_metadata: Mapping[str, object],
    target_symbols: Iterable[object],
) -> Dict[str, object]:
    """Select the incoming scopes that may replace an active manifest.

    The complete source graph stays available in memory, but an incremental
    worker should only materialize the triggering symbols and shared facts.
    An old active generation is a valid endpoint for a newly written link, so
    dependencies are added only when that endpoint has no active generation.
    """

    worldview = dict(graph.worldview or {})
    active = dict(active_metadata or {})
    incoming = _scope_plan_by_id(
        worldview.get("scopePlan") or [],
        worldview.get("scopeGenerationIds") or {},
        worldview.get("scopeFingerprints") or {},
    )
    active_by_scope = _scope_plan_by_id(
        active.get("scopePlan") or [],
        active.get("scopeGenerationIds") or {},
        active.get("scopeFingerprints") or {},
    )
    requested_symbols = sorted({_symbol(value) for value in target_symbols or [] if _symbol(value)})
    base = {
        "targetSymbols": requested_symbols,
        "incomingScopeCount": len(incoming),
        "activeScopeCount": len(active_by_scope),
        "selectedIncomingScopeIds": [],
        "selectedIncomingScopePlan": [],
        "reusedActiveScopeIds": [],
        "deferredScopeIds": [],
    }
    if not requested_symbols:
        return {**base, "status": "skipped-no-target-symbols", "applied": False}
    if not incoming:
        return {**base, "status": "skipped-empty-incoming-plan", "applied": False}
    if str(active.get("status") or "").lower() != "ok" or not active_by_scope:
        return {**base, "status": "skipped-active-manifest-unavailable", "applied": False}
    if str(active.get("scopedAboxManifestVersion") or "") != SCOPED_ABOX_MANIFEST_VERSION:
        return {**base, "status": "skipped-active-manifest-legacy", "applied": False}
    if str(active.get("scopeTopologyVersion") or "") != SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION:
        return {**base, "status": "skipped-active-topology-migration", "applied": False}

    selected: Set[str] = {
        scope_id
        for scope_id in incoming
        if scope_symbol(scope_id) in requested_symbols or not scope_symbol(scope_id)
    }

    # A relation can point to a brand new endpoint that has never been active.
    # Include that endpoint locally; otherwise retain an active endpoint
    # generation rather than unnecessarily rolling its entire fact family.
    node_scopes = _graph_node_scopes(graph)

    def include_missing_dependency(scope_id: str, missing: List[str]) -> None:
        if scope_id in selected:
            return
        if scope_id not in incoming:
            missing.append(scope_id)
            return
        selected.add(scope_id)

    missing_endpoints: List[str] = []
    changed = True
    while changed:
        changed = False
        before = len(selected)
        for scope_id in list(selected):
            row = incoming.get(scope_id) or {}
            for dependency in row.get("dependencyScopeIds") or []:
                dependency_id = _clean(dependency)
                if dependency_id and dependency_id not in active_by_scope:
                    include_missing_dependency(dependency_id, missing_endpoints)
        for relation in graph.relations:
            properties = dict(relation.properties or {})
            owner_scope = _clean(properties.get("aboxScopeId"))
            if owner_scope not in selected:
                continue
            for endpoint in (_clean(relation.source), _clean(relation.target)):
                endpoint_scope = node_scopes.get(endpoint, "")
                if endpoint_scope and endpoint_scope not in active_by_scope:
                    include_missing_dependency(endpoint_scope, missing_endpoints)
        support_scopes = dict(worldview.get("supportRelationScopes") or {})
        for evidence in graph.evidence:
            key = support_relation_key("HAS_EVIDENCE", evidence.subject, evidence.evidence_id)
            owner_scope = _clean((support_scopes.get(key) or {}).get("scopeId"))
            if owner_scope not in selected:
                continue
            for endpoint in (_clean(evidence.subject), _clean(evidence.evidence_id)):
                endpoint_scope = node_scopes.get(endpoint, "")
                if endpoint_scope and endpoint_scope not in active_by_scope:
                    include_missing_dependency(endpoint_scope, missing_endpoints)
        changed = len(selected) != before

    if missing_endpoints:
        return {
            **base,
            "status": "skipped-missing-link-endpoint-scope",
            "applied": False,
            "missingEndpointScopeIds": sorted(set(missing_endpoints)),
        }

    selected_plan = [incoming[scope_id] for scope_id in sorted(selected)]
    deferred = [
        scope_id
        for scope_id, item in incoming.items()
        if scope_id not in selected
        and (
            scope_id not in active_by_scope
            or _clean((active_by_scope.get(scope_id) or {}).get("generationId")) != _clean(item.get("generationId"))
            or _clean((active_by_scope.get(scope_id) or {}).get("fingerprint")) != _clean(item.get("fingerprint"))
        )
    ]
    return {
        **base,
        "status": "ready",
        "applied": True,
        "selectedIncomingScopeIds": sorted(selected),
        "selectedIncomingScopePlan": selected_plan,
        "reusedActiveScopeIds": sorted(set(active_by_scope) - selected),
        "deferredScopeIds": sorted(deferred),
    }


def apply_scoped_manifest_plan(
    graph: PortfolioOntology,
    scope_plan: Iterable[object],
    account_id: object = "",
    world_id: object = "",
    material_fingerprint: object = "",
) -> Dict[str, object]:
    """Bind the in-memory graph to a complete active scoped manifest plan."""

    worldview = dict(graph.worldview or {})
    by_scope = _scope_plan_by_id(scope_plan)
    rows = [by_scope[scope_id] for scope_id in sorted(by_scope)]
    generations = {
        scope_id: _clean(item.get("generationId"))
        for scope_id, item in by_scope.items()
        if _clean(item.get("generationId"))
    }
    fingerprints = {
        scope_id: _clean(item.get("fingerprint"))
        for scope_id, item in by_scope.items()
        if _clean(item.get("fingerprint"))
    }
    clean_account = _clean(account_id) or _clean(worldview.get("accountId")) or _account_id(graph)
    clean_world = _clean(world_id) or _clean(worldview.get("worldId"))
    manifest_id = scoped_manifest_id(clean_account, generations, clean_world)
    fingerprint = _clean(material_fingerprint) or scoped_manifest_material_fingerprint(rows)

    def bind(values: MutableMapping[str, object]) -> None:
        scope_id = _clean(values.get("aboxScopeId"))
        scope = by_scope.get(scope_id)
        if not scope:
            return
        generation_id = _clean(scope.get("generationId"))
        values.update({
            "scopeGenerationId": generation_id,
            "worldviewManifestId": manifest_id,
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            "materialFingerprint": fingerprint,
        })

    for entity in graph.entities:
        if _clean((entity.properties or {}).get("ontologyBox")) in {"", "ABox"}:
            bind(entity.properties)
    for relation in graph.relations:
        if _clean((relation.properties or {}).get("ontologyBox")) in {"", "ABox"}:
            bind(relation.properties)
    for evidence in graph.evidence:
        if _clean((evidence.value or {}).get("ontologyBox")) in {"", "ABox"}:
            bind(evidence.value)

    support_scopes: Dict[str, Dict[str, object]] = {}
    for key, raw in dict(worldview.get("supportRelationScopes") or {}).items():
        metadata = dict(raw or {}) if isinstance(raw, Mapping) else {}
        scope_id = _clean(metadata.get("scopeId"))
        scope = by_scope.get(scope_id)
        if not scope:
            continue
        generation_id = _clean(scope.get("generationId"))
        support_scopes[_clean(key)] = {
            **metadata,
            "scopeId": scope_id,
            "scopeType": _clean(scope.get("scopeType")) or _scope_type(scope_id),
            "scopeGenerationId": generation_id,
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            "manifestId": manifest_id,
        }

    graph.worldview.update({
        "aboxSnapshotId": manifest_id,
        "snapshotId": manifest_id,
        "worldviewManifestId": manifest_id,
        "materialFingerprint": fingerprint,
        "scopePlan": rows,
        "scopeGenerationIds": generations,
        "scopeFingerprints": fingerprints,
        "scopeFamilyCounts": _scope_plan_counts(rows),
        "supportRelationScopes": support_scopes,
    })
    return {
        "manifestId": manifest_id,
        "materialFingerprint": fingerprint,
        "scopePlan": rows,
        "scopeGenerationIds": generations,
        "scopeFingerprints": fingerprints,
        "scopeFamilyCounts": _scope_plan_counts(rows),
    }


def merge_target_scoped_abox_manifest(
    graph: PortfolioOntology,
    active_metadata: Mapping[str, object],
    target_symbols: Iterable[object],
) -> Dict[str, object]:
    """Replace only target-symbol scopes while retaining active generations."""

    selection = select_target_scoped_manifest_patch(graph, active_metadata, target_symbols)
    if not selection.get("applied"):
        return selection
    active = dict(active_metadata or {})
    active_by_scope = _scope_plan_by_id(
        active.get("scopePlan") or [],
        active.get("scopeGenerationIds") or {},
        active.get("scopeFingerprints") or {},
    )
    for item in selection.get("selectedIncomingScopePlan") or []:
        scope_id = _clean(item.get("scopeId"))
        if scope_id:
            active_by_scope[scope_id] = dict(item)
    merged_plan = [active_by_scope[scope_id] for scope_id in sorted(active_by_scope)]
    material_fingerprint = scoped_manifest_material_fingerprint(merged_plan)
    applied = apply_scoped_manifest_plan(
        graph,
        merged_plan,
        material_fingerprint=material_fingerprint,
    )
    patch_metadata = {
        "mode": "incremental-target-scoped-manifest-patch",
        "targetSymbols": list(selection.get("targetSymbols") or []),
        "selectedIncomingScopeIds": list(selection.get("selectedIncomingScopeIds") or []),
        "reusedActiveScopeIds": list(selection.get("reusedActiveScopeIds") or []),
        "deferredScopeIds": list(selection.get("deferredScopeIds") or []),
    }
    graph.worldview["targetScopedManifestPatch"] = patch_metadata
    return {
        **selection,
        **applied,
        "status": "applied",
        "applied": True,
        "scopeManifestFingerprint": material_fingerprint,
        "targetScopedManifestPatch": patch_metadata,
    }


def scoped_graph_slice(graph: PortfolioOntology, scope_ids: Iterable[str]) -> PortfolioOntology:
    """Return only changed ABox facts while preserving cross-scope endpoints.

    The TypeDB adapter uses the full graph to resolve endpoint storage IDs, so
    this helper is intentionally a presentation/testing slice rather than the
    persistence row builder.
    """
    selected = {_clean(item) for item in scope_ids if _clean(item)}
    clone = deepcopy(graph)
    clone.entities = [
        item for item in clone.entities
        if _clean((item.properties or {}).get("ontologyBox")) != "ABox"
        or _clean((item.properties or {}).get("aboxScopeId")) in selected
    ]
    clone.relations = [
        item for item in clone.relations
        if _clean((item.properties or {}).get("ontologyBox")) != "ABox"
        or _clean((item.properties or {}).get("aboxScopeId")) in selected
    ]
    clone.evidence = [
        item for item in clone.evidence
        if _clean((item.value or {}).get("ontologyBox")) != "ABox"
        or _clean((item.value or {}).get("aboxScopeId")) in selected
    ]
    return clone
