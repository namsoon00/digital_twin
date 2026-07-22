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

from .ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from .ontology_projection_fingerprint import stable_value


SCOPED_ABOX_MANIFEST_VERSION = "scoped-manifest-v1"
SCOPED_ABOX_PERSISTENCE_MODE = "immutable-scoped-manifest"

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
        return MACRO_SCOPE_ID
    clean_value = _clean(value) or "global"
    return scope_type + ":" + clean_value


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
    # Market-wide instruments can carry a ticker-like identifier (BTC, an FX
    # pair, an index). Their world ownership is still macro, not a portfolio
    # stock scope.
    if kind in _MACRO_KINDS:
        return MACRO_SCOPE_ID
    if kind in _POLICY_KINDS:
        return _scope_id("policy", account_id)
    if kind in {"portfolio", "account", "watchlist", "cash"}:
        return _scope_id("portfolio", account_id)
    symbol = _symbol(properties.get("symbol"))
    if symbol:
        return _scope_id("symbol", symbol)
    if any(token in kind for token in _EPISODE_TOKENS):
        return _scope_id("episode", account_id)
    if any(token in kind for token in _EVIDENCE_TOKENS):
        return _scope_id("evidence", account_id)
    entity_id = _clean(entity.entity_id).lower()
    if entity_id.startswith(_SYMBOL_PREFIXES):
        candidate = _id_symbol(entity.entity_id)
        if candidate:
            return _scope_id("symbol", candidate)
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
                scopes[neighbour]
                for neighbour in neighbours.get(entity_id, set())
                if neighbour in scopes and _scope_type(scopes[neighbour]) == "symbol"
            }
            if len(candidates) == 1:
                scopes[entity_id] = next(iter(candidates))
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
) -> str:
    properties = dict(relation.properties or {})
    explicit = _clean(properties.get("aboxScopeId"))
    if explicit:
        return explicit
    symbol = _symbol(properties.get("symbol"))
    if symbol:
        return _scope_id("symbol", symbol)
    candidates = [
        entity_scopes.get(_clean(relation.source), ""),
        entity_scopes.get(_clean(relation.target), ""),
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
    if subject_scope:
        return subject_scope
    symbol = _symbol(properties.get("symbol"))
    if symbol:
        return _scope_id("symbol", symbol)
    return _scope_id("evidence", account_id)


def _scope_fragment_payload(
    graph: PortfolioOntology,
    scope_id: str,
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


def scoped_manifest_id(account_id: str, scope_generations: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sorted(scope_generations.items())), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((account_id + "|" + payload).encode("utf-8")).hexdigest()[:20]
    return "abox-manifest:" + digest


def apply_scoped_abox_identity(
    graph: PortfolioOntology,
    account_id: str = "",
) -> Dict[str, object]:
    """Annotate one complete ABox graph with independent scope generations.

    The graph remains complete in memory for validation and AI context.  The
    repository uses ``scopePlan`` to persist only scopes whose fingerprints
    changed since the active Manifest.
    """
    clone = graph
    account_key = _clean(account_id) or _account_id(clone)
    entity_scopes = _seed_entity_scopes(clone)
    _propagate_entity_scopes(clone, entity_scopes)

    for entity in clone.entities:
        if _clean((entity.properties or {}).get("ontologyBox")) not in {"", "ABox"}:
            continue
        scope_id = entity_scopes.get(_clean(entity.entity_id), REFERENCE_SCOPE_ID)
        entity.properties["aboxScopeId"] = scope_id
        entity.properties["aboxScopeType"] = _scope_type(scope_id)

    for relation in clone.relations:
        if _clean((relation.properties or {}).get("ontologyBox")) not in {"", "ABox"}:
            continue
        scope_id = scope_id_for_relation(relation, entity_scopes, account_key)
        relation.properties["aboxScopeId"] = scope_id
        relation.properties["aboxScopeType"] = _scope_type(scope_id)

    for evidence in clone.evidence:
        if _clean((evidence.value or {}).get("ontologyBox")) not in {"", "ABox"}:
            continue
        scope_id = scope_id_for_evidence(evidence, entity_scopes, account_key)
        evidence.value["aboxScopeId"] = scope_id
        evidence.value["aboxScopeType"] = _scope_type(scope_id)

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
    })
    scope_ids = [scope_id for scope_id in scope_ids if scope_id]
    # A relation physically links the immutable records of both endpoints.
    # Its owning scope must therefore roll forward whenever *any* reachable
    # endpoint scope changes. Base fingerprints capture local facts; the
    # dependency closure adds endpoint base fingerprints without recursive
    # generation hashes, so cycles remain deterministic.
    payloads = {
        scope_id: _scope_fragment_payload(clone, scope_id)
        for scope_id in scope_ids
    }
    base_fingerprints = {
        scope_id: hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for scope_id, payload in payloads.items()
    }
    dependency_graph: Dict[str, Set[str]] = {scope_id: set() for scope_id in scope_ids}
    for relation in clone.relations:
        properties = dict(relation.properties or {})
        if _clean(properties.get("ontologyBox")) not in {"", "ABox"}:
            continue
        owner_scope = _clean(properties.get("aboxScopeId"))
        if owner_scope not in dependency_graph:
            continue
        for endpoint in (_clean(relation.source), _clean(relation.target)):
            endpoint_scope = entity_scopes.get(endpoint, "")
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

    scope_plan: List[Dict[str, object]] = []
    generations: Dict[str, str] = {}
    for scope_id in scope_ids:
        dependencies = dependency_closure(scope_id)
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
            "fingerprint": fingerprint,
            "baseFingerprint": base_fingerprints[scope_id],
            "dependencyScopeIds": dependencies,
            "generationId": generation_id,
            "entityCount": len(payloads[scope_id]["entities"]),
            "relationCount": len(payloads[scope_id]["relations"]),
            "evidenceCount": len(payloads[scope_id]["evidence"]),
        })

    manifest_id = scoped_manifest_id(account_key, generations)
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

    clone.worldview.update({
        "aboxSnapshotId": manifest_id,
        "snapshotId": manifest_id,
        "worldviewManifestId": manifest_id,
        "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
        "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
        "scopePlan": scope_plan,
        "scopeGenerationIds": generations,
        "scopeFingerprints": {item["scopeId"]: item["fingerprint"] for item in scope_plan},
    })
    return {
        "manifestId": manifest_id,
        "scopePlan": scope_plan,
        "scopeGenerationIds": generations,
        "scopeFingerprints": {item["scopeId"]: item["fingerprint"] for item in scope_plan},
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
