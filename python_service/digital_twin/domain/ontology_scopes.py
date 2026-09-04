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
    DEPENDENCY_FINGERPRINT_VERSION,
    SYMBOL_SCOPE_FAMILIES,
    family_for_field,
    family_for_entity,
    family_for_relation,
    macro_scope_id,
    pack_semantic_dependency_fingerprints,
    scope_family,
    scope_symbol,
    symbol_scope_id,
    unpack_semantic_dependency_fingerprints,
)
from .ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from .ontology_fact_slots import select_fact_slot_scope_ids
from .ontology_projection_fingerprint import VOLATILE_LIFECYCLE_KEYS, stable_value
from .ontology_worlds import world_scoped_scope_id


SCOPED_ABOX_MANIFEST_VERSION = "scoped-manifest-v1"
SCOPED_ABOX_PERSISTENCE_MODE = "immutable-scoped-manifest"
SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION = "granular-v10-stable-item-ownership"

REFERENCE_SCOPE_ID = "reference:global"
MACRO_SCOPE_ID = "macro:global"

# These buckets are physical persistence boundaries, not investment
# thresholds. They keep immutable generations bounded while preserving every
# fact in the active ABox manifest. Counts are release-versioned through the
# scope topology so changing them requires an explicit topology migration.
BOUNDED_SCOPE_BUCKET_COUNTS = {
    # Evidence objects carry substantially more cross-scope relations than a
    # quote or valuation observation. Sixteen buckets made one new article
    # roll unrelated articles that collided in the same immutable generation,
    # which in turn recreated dozens of endpoint-bound relations. Sixty-four
    # stays bounded while cutting the expected collision set by 75%.
    "evidence": 64,
    "quality": 8,
    "valuation": 8,
    "company-valuation": 8,
}

_SYMBOL_PREFIXES = (
    "stock:",
    "position:",
    "price-",
    "volume-",
    "execution-",
    "key-level:",
    "trend-",
    "temporal-",
    "market-session-phase:",
    "relative-performance-observation:",
    "liquidity-",
    "slippage-estimate:",
    "smart-money-",
    "investor-",
    "valuation-",
    "security-line-",
    "instrument-",
    "data-latency:",
    "fact-change:",
    "technical-metric:",
    "flow-metric:",
    "data-quality:",
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

_PORTFOLIO_ITEM_KINDS = {
    "cash-exposure",
    "currency-exposure",
    "portfolio-reconciliation",
    "portfolio-risk-snapshot",
    "rebalance-proposal",
    "rebalance-state",
    "sector-exposure",
}

_EPISODE_ITEM_KINDS = {
    "inferred-portfolio-activity",
    "portfolio-action-candidate",
    "portfolio-activity-episode",
    "rebalance-scenario",
}

_POLICY_ITEM_KINDS = {
    "external-signal",
    "position-role",
}

_REFERENCE_ITEM_KINDS = {
    "catalog-entry",
    "factor",
}

_GENERATED_SCOPE_PROPERTY_KEYS = {
    "ontologybox",
    "worldid",
    "worldtype",
    "tenantid",
    "accountid",
    "aboxscopeid",
    "aboxscopetype",
    "aboxscopefamily",
    "scopegenerationid",
    "worldviewmanifestid",
    "snapshotid",
    "aboxsnapshotid",
    "materialfingerprint",
    "manifestid",
}

_QUALITY_TIMESTAMP_FIELDS = {
    "asof",
    "observedat",
    "updatedat",
    "fetchedat",
    "sourceasof",
    "sourcefetchedat",
}

_SEMANTIC_METADATA_FIELDS = {
    "tboxclass",
    "tboxclasses",
    "box",
    "boundedcontext",
    "sourcecontext",
    "targetcontext",
    "iscurrent",
    "tboxversion",
    "activetboxversion",
    "activetboxsource",
    "activetboxentitycount",
    "activetboxrelationcount",
    "activetboxfingerprint",
}

# Observation clocks and explanatory provider text are valuable for display
# and freshness diagnostics, but do not describe a new investment fact. If
# they participate in scope identity, an unchanged quote is re-materialized
# on every poll and reopens native RuleBox execution. Freshness *state* and
# data-state fields are intentionally not included here: a usable-to-stale
# transition remains material.
_IMMATERIAL_OBSERVATION_PROPERTY_FIELDS = {
    "freshnessgatereason",
    "freshnessreason",
    "latencyreason",
    "marketsession",
    "marketsessionelapsedpct",
    "marketsessionlabel",
    "marketsessionlocaltime",
    "quotemessage",
    "stalereason",
}

_IMMATERIAL_OBSERVATION_PROPERTY_KEYS = {
    re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    for value in (
        VOLATILE_LIFECYCLE_KEYS
        | _IMMATERIAL_OBSERVATION_PROPERTY_FIELDS
        | _QUALITY_TIMESTAMP_FIELDS
    )
}


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


def entity_item_scope_id(
    scope_type: object,
    owner: object,
    entity_id: object,
) -> str:
    """Return a graph-shape-independent owner for one non-symbol fact.

    A target-scoped graph can contain fewer neighbours than a complete
    portfolio graph. Inferring ownership from those neighbours therefore
    moved the same logical node between ``reference`` and ``symbol`` scopes,
    which produced relation endpoints that did not exist in the active
    physical generation. Item scopes keep ownership stable in both views and
    limit copy-on-write relation rebinding to edges incident to that fact.
    """

    clean_type = _clean(scope_type).lower() or "reference"
    if clean_type not in {"reference", "portfolio", "episode", "policy"}:
        clean_type = "reference"
    digest = hashlib.sha256(_clean(entity_id).encode("utf-8")).hexdigest()[:16]
    if clean_type == "reference":
        return "reference:item:" + digest
    clean_owner = re.sub(r"[^A-Za-z0-9_.-]+", "-", _clean(owner)).strip("-.") or "global"
    return clean_type + ":" + clean_owner + ":item:" + digest


def _scope_slot_token(value: object, fallback: str = "unknown") -> str:
    token = re.sub(r"[^a-z0-9_.-]+", "-", _clean(value).lower()).strip("-.")
    return (token or fallback)[:48]


def _bounded_bucket(value: object, bucket_count: int) -> str:
    count = max(1, int(bucket_count or 1))
    digest = hashlib.sha256(_clean(value).encode("utf-8")).hexdigest()
    width = max(2, len(str(count - 1)))
    return str(int(digest[:12], 16) % count).zfill(width)


def _temporal_window_token(
    identity: object,
    properties: Mapping[str, object] = None,
) -> str:
    values = dict(properties or {})
    for key in (
        "windowId", "window", "horizon", "period", "lookback",
        "timeframe", "interval", "windowLabel",
    ):
        if _clean(values.get(key)):
            return _scope_slot_token(values.get(key), "current")
    text = _clean(identity).lower()
    matches = re.findall(r"(?:^|:|-)(15m|30m|1h|2h|4h|1d|3d|5d|10d|20d|60d|120d|200d)(?:$|:|-)", text)
    return _scope_slot_token(matches[-1] if matches else "current", "current")


def bounded_fact_scope_id(
    base_scope_id: object,
    family: object,
    identity: object,
    properties: Mapping[str, object] = None,
) -> str:
    """Return a stable bounded physical slot for one semantic fact.

    The family prefix remains unchanged, so impact routing and TypeDB rule
    dependencies continue to operate on the same ontology vocabulary. Only
    immutable persistence ownership becomes more granular.
    """

    base = _clean(base_scope_id)
    clean_family = _clean(family).lower()
    if not base:
        return base
    if clean_family == "temporal":
        return base + ":window:" + _temporal_window_token(identity, properties)
    bucket_count = BOUNDED_SCOPE_BUCKET_COUNTS.get(clean_family)
    if bucket_count:
        return base + ":bucket:" + _bounded_bucket(identity, bucket_count)
    return base


def _scope_slot_suffix(scope_id: object) -> str:
    """Read a bounded slot suffix while ignoring the world ownership suffix."""

    parts = [item for item in _clean(scope_id).split(":") if item]
    if "world" in parts:
        parts = parts[:parts.index("world")]
    for marker in ("window", "bucket"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return ":" + marker + ":" + parts[index + 1]
    return ""


def scope_requires_v8_bounded_slot(scope_id: object) -> bool:
    """Whether one active v7 symbol scope still needs online migration."""

    clean_scope = _clean(scope_id)
    scope_type = _scope_type(clean_scope)
    if not scope_symbol(clean_scope) or scope_type not in {"symbol", "link"}:
        return False
    family = scope_family(clean_scope)
    if family == "temporal":
        # Temporal relation-only scopes can legitimately connect a subject's
        # current market and state anchors without owning one temporal window.
        # Treating that aggregate link as a legacy slot forced every event to
        # migrate the subject's complete scope boundary again. Only temporal
        # fact owners require the v8 window suffix; window-owned links already
        # inherit it from their temporal endpoint.
        return scope_type == "symbol" and ":window:" not in clean_scope
    if family in BOUNDED_SCOPE_BUCKET_COUNTS:
        return ":bucket:" not in clean_scope
    return False


def relation_link_scope_id(
    source_scope: object,
    target_scope: object,
    account_id: object = "",
    symbol: object = "",
    relation_family: object = "",
) -> str:
    """Return a fact-family relation-only owner for a cross-scope ABox edge.

    Immutable TypeDB generations give every node a generation-scoped storage
    identity. Storing a cross-scope edge beside either endpoint therefore
    forced that endpoint's whole fact family to roll whenever the other side
    changed. A relation-only link scope owns the edge instead: endpoint facts
    remain independently versioned and only the link is rebound. The factual
    family prevents a market update from recreating the same symbol's flow,
    evidence, valuation, or exposure relationships.
    """

    clean_family = _clean(relation_family).lower()
    if clean_family not in SYMBOL_SCOPE_FAMILIES and not clean_family.startswith("macro-"):
        clean_family = "state"
    clean_account_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", _clean(account_id)).strip("-.") or "global"
    clean_symbol = _symbol(symbol)
    if not clean_symbol:
        clean_symbol = scope_symbol(source_scope) or scope_symbol(target_scope)
    slot_suffix = ""
    for endpoint_scope in (target_scope, source_scope):
        if scope_family(endpoint_scope) == clean_family:
            slot_suffix = _scope_slot_suffix(endpoint_scope)
            if slot_suffix:
                break
    if not slot_suffix and clean_family in BOUNDED_SCOPE_BUCKET_COUNTS:
        slot_suffix = ":bucket:" + _bounded_bucket(
            "|".join(sorted({_clean(source_scope), _clean(target_scope)})),
            BOUNDED_SCOPE_BUCKET_COUNTS[clean_family],
        )
    if clean_symbol:
        return "link:symbol:" + clean_symbol + ":" + clean_family + slot_suffix
    dependency_scopes = sorted({
        _clean(value)
        for value in (source_scope, target_scope)
        if _clean(value)
    })
    dependency_shard = (
        hashlib.sha256("|".join(dependency_scopes).encode("utf-8")).hexdigest()[:12]
        if dependency_scopes
        else "shared"
    )
    return (
        "link:account:" + clean_account_id + ":" + clean_family
        + slot_suffix + ":" + dependency_shard
    )


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
    if kind in {"portfolio", "account", "watchlist", "cash", "sector", "market-exposure"}:
        return _scope_id("portfolio", account_id)
    # A risk without an instrument subject is an account-level exposure
    # aggregate (for example, sector correlation risk). Instrument risks carry
    # ``symbol`` and are routed by the symbol branch below.
    if kind == "risk" and not _symbol(properties.get("symbol")):
        return _scope_id("portfolio", account_id)
    symbol = _symbol(properties.get("symbol"))
    if kind == "position-exposure" and not symbol:
        exposure_key = _symbol(properties.get("exposureKey"))
        if re.fullmatch(r"(?:\d{4,8}|[A-Z]{1,6}(?:[.-][A-Z0-9]{1,5})?)", exposure_key):
            symbol = exposure_key
    if symbol:
        return bounded_fact_scope_id(
            symbol_scope_id(symbol, family),
            family,
            entity.entity_id,
            properties,
        )
    if kind in _PORTFOLIO_ITEM_KINDS:
        return entity_item_scope_id("portfolio", account_id, entity.entity_id)
    if kind in _EPISODE_ITEM_KINDS:
        return entity_item_scope_id("episode", account_id, entity.entity_id)
    if kind in _POLICY_ITEM_KINDS:
        return entity_item_scope_id("policy", account_id, entity.entity_id)
    if kind == "data-quality":
        return entity_item_scope_id("policy", account_id, entity.entity_id)
    if kind in _REFERENCE_ITEM_KINDS:
        return entity_item_scope_id("reference", "", entity.entity_id)
    if any(token in kind for token in _EPISODE_TOKENS):
        return _scope_id("episode", account_id)
    if any(token in kind for token in _EVIDENCE_TOKENS):
        return _scope_id("evidence", account_id)
    entity_id = _clean(entity.entity_id).lower()
    if entity_id.startswith(_SYMBOL_PREFIXES):
        candidate = _id_symbol(entity.entity_id)
        if candidate:
            return bounded_fact_scope_id(
                symbol_scope_id(candidate, family),
                family,
                entity.entity_id,
                properties,
            )
    # Unknown non-symbol facts still need stable ownership. Falling back to
    # one global reference scope makes a partial subject graph look like a
    # destructive replacement of every unrelated reference fact.
    return entity_item_scope_id("reference", "", entity.entity_id)


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
                family = family_for_entity(entity.kind, entity.properties, entity.entity_id)
                scopes[entity_id] = bounded_fact_scope_id(
                    symbol_scope_id(next(iter(candidates)), family),
                    family,
                    entity.entity_id,
                    entity.properties,
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
            return relation_link_scope_id(
                source_scope,
                target_scope,
                account_id,
                relation_family=relation_family,
            )
        return relation_link_scope_id(
            source_scope,
            target_scope,
            account_id,
            symbol,
            relation_family,
        )
    if (
        source_scope
        and source_scope == target_scope
        and scope_family(source_scope) == relation_family
    ):
        return source_scope
    explicit = _clean(properties.get("aboxScopeId"))
    if explicit:
        return explicit
    # Market-proxy relationships carry their observed ticker for provenance.
    # That ticker must not turn a global market sensor into a pseudo holding.
    if symbol and macro_scopes and not source_symbol and not target_symbol:
        return sorted(macro_scopes, key=_scope_rank)[0]
    if symbol:
        return bounded_fact_scope_id(
            symbol_scope_id(symbol, relation_family),
            relation_family,
            support_relation_key(relation.relation_type, source_id, target_id),
            properties,
        )
    if source_symbol and source_symbol == target_symbol:
        return bounded_fact_scope_id(
            symbol_scope_id(source_symbol, relation_family),
            relation_family,
            support_relation_key(relation.relation_type, source_id, target_id),
            properties,
        )
    if source_symbol:
        return bounded_fact_scope_id(
            symbol_scope_id(source_symbol, relation_family),
            relation_family,
            support_relation_key(relation.relation_type, source_id, target_id),
            properties,
        )
    if target_symbol:
        return bounded_fact_scope_id(
            symbol_scope_id(target_symbol, relation_family),
            relation_family,
            support_relation_key(relation.relation_type, source_id, target_id),
            properties,
        )
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
        return bounded_fact_scope_id(
            symbol_scope_id(symbol, "evidence"),
            "evidence",
            evidence.evidence_id,
            properties,
        )
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
        original_source = _clean(evidence.subject)
        target = _clean(evidence.evidence_id)
        original_source_scope = node_scopes.get(original_source, "")
        symbol = _symbol(values.get("symbol")) or scope_symbol(original_source_scope)
        stable_anchor = "security:" + symbol if symbol else ""
        source = stable_anchor if stable_anchor in node_scopes else original_source
        source_scope = node_scopes.get(source, "")
        target_scope = node_scopes.get(target, "")
        if not source or not target or not source_scope or not target_scope:
            continue
        scope_id = relation_link_scope_id(
            source_scope,
            target_scope,
            account_id,
            values.get("symbol"),
            "evidence",
        )
        if world_id:
            scope_id = world_scoped_scope_id(scope_id, world_id)
        rows.append({
            # Keep the source-evidence lookup key stable for callers that
            # receive OntologyEvidence. The persisted endpoints below may use
            # the generation-independent instrument anchor.
            "key": support_relation_key("HAS_EVIDENCE", original_source, target),
            "source": source,
            "target": target,
            "originalSource": original_source,
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


def _scope_fragment_payloads(
    graph: PortfolioOntology,
    scope_ids: Iterable[str],
    support_relations: Iterable[Mapping[str, object]] = (),
) -> Dict[str, Dict[str, object]]:
    """Build every scoped persistence payload in one graph pass.

    ``apply_scoped_abox_identity`` previously called
    :func:`_scope_fragment_payload` once per scope.  A live portfolio can have
    well over one hundred independently versioned scopes, which made one
    target-symbol update repeatedly scan the same complete graph before any
    TypeDB work began.  This helper preserves the exact payload shape and sort
    order while grouping the source rows only once.
    """
    expected = {str(scope_id or "").strip() for scope_id in scope_ids or [] if str(scope_id or "").strip()}
    grouped: Dict[str, Dict[str, List[Dict[str, object]]]] = {
        scope_id: {"entities": [], "relations": [], "evidence": []}
        for scope_id in expected
    }

    for entity in graph.entities:
        scope_id = _clean((entity.properties or {}).get("aboxScopeId"))
        if scope_id not in grouped:
            continue
        grouped[scope_id]["entities"].append({
            "id": entity.entity_id,
            "kind": entity.kind,
            "properties": stable_value(entity.properties),
        })
    for relation in graph.relations:
        scope_id = _clean((relation.properties or {}).get("aboxScopeId"))
        if scope_id not in grouped:
            continue
        grouped[scope_id]["relations"].append({
            "source": relation.source,
            "target": relation.target,
            "type": relation.relation_type,
            "properties": stable_value(relation.properties),
        })
    for evidence in graph.evidence:
        scope_id = _clean((evidence.value or {}).get("aboxScopeId"))
        if scope_id not in grouped:
            continue
        grouped[scope_id]["evidence"].append({
            "id": evidence.evidence_id,
            "subject": evidence.subject,
            "kind": evidence.kind,
            "source": evidence.source,
            "summary": evidence.summary,
            "value": stable_value(evidence.value),
        })
    for relation in support_relations:
        scope_id = _clean(relation.get("scopeId"))
        if scope_id not in grouped:
            continue
        grouped[scope_id]["relations"].append({
            "source": _clean(relation.get("source")),
            "target": _clean(relation.get("target")),
            "type": _clean(relation.get("type")),
            "properties": stable_value(dict(relation.get("properties") or {})),
        })

    return {
        scope_id: {
            "entities": sorted(payload["entities"], key=lambda item: (str(item["kind"]), str(item["id"]))),
            "relations": sorted(payload["relations"], key=lambda item: (str(item["type"]), str(item["source"]), str(item["target"]))),
            "evidence": sorted(payload["evidence"], key=lambda item: (str(item["kind"]), str(item["id"]))),
        }
        for scope_id, payload in grouped.items()
    }


def _semantic_property_family(field: object, fallback_family: object) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", _clean(field).lower())
    if normalized in _QUALITY_TIMESTAMP_FIELDS:
        # Raw observation clocks are intentionally excluded from a scope
        # fingerprint. Freshness/data-state transitions remain material via
        # their own fields, but a newer fetch time with the same usable fact
        # must not re-open native investment rules.
        return ""
    fallback = _clean(fallback_family)
    # TBox/lifecycle decorations are structural metadata. Keep them with the
    # entity's fact family rather than turning every observation into a
    # generic state update when a deployment changes metadata formatting.
    if normalized in _SEMANTIC_METADATA_FIELDS:
        return fallback or "state"
    family = family_for_field(field)
    # A macro observation can contain quote-shaped properties such as
    # ``currentPrice`` and ``volume``. They describe the macro sensor itself,
    # not an individual-stock price/flow fact. Likewise, article metadata can
    # mention a ticker or price without becoming a market-data observation.
    # Keep these factual owners narrow so a macro refresh or a new article
    # schedules only the corresponding TypeDB rule families.
    if fallback.startswith("macro-"):
        return "quality" if family == "quality" else fallback
    if fallback == "evidence":
        return "quality" if family == "quality" else "evidence"
    # ``family_for_field`` deliberately falls back to state for unknown
    # RuleBox condition fields. For an ABox property, however, the owning
    # entity already supplies a precise type (flow, valuation, evidence,
    # etc.). Use that type so unknown presentation/provenance properties do
    # not make a quote refresh look like a global state mutation.
    if family == "state" and fallback and fallback != "state":
        return fallback
    if family != "unknown":
        return family
    return fallback if fallback else "state"


def _semantic_properties(values: Mapping[str, object]) -> Dict[str, object]:
    return {
        str(key): stable_value(value)
        for key, value in dict(values or {}).items()
        if re.sub(r"[^a-z0-9]", "", str(key or "").lower()) not in _GENERATED_SCOPE_PROPERTY_KEYS
        and re.sub(r"[^a-z0-9]", "", str(key or "").lower()) not in _IMMATERIAL_OBSERVATION_PROPERTY_KEYS
        and re.sub(r"[^a-z0-9]", "", str(key or "").lower()) not in _SEMANTIC_METADATA_FIELDS
    }


def _semantic_dependency_token(value: object) -> str:
    """Return a stable key for a RuleBox-readable fact dependency.

    Scope-family hashes answer whether a broad fact category changed. Native
    rule scheduling additionally needs a narrower, value-free identity such
    as ``field:profitlossrate`` or ``relation:has-external-signal``. Labels
    deliberately never appear here: they are display metadata and are not
    persisted as ABox facts.
    """
    return re.sub(r"[^a-z0-9]+", "-", _clean(value).lower()).strip("-")


def _semantic_dependency_field_key(field: object) -> str:
    return "field:" + re.sub(r"[^a-z0-9]", "", _clean(field).lower())


def _semantic_dependency_kind_field_key(kind: object, field: object) -> str:
    kind_token = _semantic_dependency_token(kind)
    field_token = re.sub(r"[^a-z0-9]", "", _clean(field).lower())
    if not kind_token or not field_token:
        return ""
    return "kind:" + kind_token + ":field:" + field_token


def _semantic_dependency_relation_field_key(relation_type: object, field: object) -> str:
    relation_token = _semantic_dependency_token(relation_type)
    field_token = re.sub(r"[^a-z0-9]", "", _clean(field).lower())
    if not relation_token or not field_token:
        return ""
    return "relation:" + relation_token + ":field:" + field_token


def _fingerprint_semantic_groups(groups: Mapping[str, Iterable[Mapping[str, object]]]) -> Dict[str, str]:
    return {
        key: hashlib.sha256(
            json.dumps(
                sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True)),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for key, items in groups.items()
        if key and items
    }


def _scope_semantic_fingerprints(
    graph: PortfolioOntology,
    scope_id: str,
    support_relations: Iterable[Mapping[str, object]] = (),
) -> Dict[str, str]:
    """Fingerprint factual meanings independently from endpoint rebinding.

    A relation-only scope may roll because an endpoint's immutable storage id
    changed. That is a persistence concern, not a new market fact. These
    per-family fingerprints let native rule routing react to the fact that
    changed rather than every relation that had to be rebound.
    """
    groups: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    entities_by_id = {
        _clean(entity.entity_id): entity
        for entity in graph.entities
        if _clean(entity.entity_id)
    }

    for entity in graph.entities:
        properties = dict(entity.properties or {})
        if _clean(properties.get("aboxScopeId")) != scope_id:
            continue
        fallback = family_for_entity(entity.kind, properties, entity.entity_id)
        groups[fallback].append({
            "entity": _clean(entity.entity_id),
            "kind": _clean(entity.kind),
        })
        for field, value in _semantic_properties(properties).items():
            groups[_semantic_property_family(field, fallback)].append({
                "entity": _clean(entity.entity_id),
                "field": field,
                "value": value,
            })

    for relation in graph.relations:
        properties = dict(relation.properties or {})
        if _clean(properties.get("aboxScopeId")) != scope_id:
            continue
        source = entities_by_id.get(_clean(relation.source))
        target = entities_by_id.get(_clean(relation.target))
        family = family_for_relation(
            relation.relation_type,
            properties,
            source_family=scope_family((getattr(source, "properties", {}) or {}).get("aboxScopeId")),
            target_family=scope_family((getattr(target, "properties", {}) or {}).get("aboxScopeId")),
            source_kind=getattr(source, "kind", ""),
            target_kind=getattr(target, "kind", ""),
        )
        groups[family].append({
            "source": _clean(relation.source),
            "target": _clean(relation.target),
            "type": _clean(relation.relation_type),
            "properties": _semantic_properties(properties),
        })

    for evidence in graph.evidence:
        values = dict(evidence.value or {})
        if _clean(values.get("aboxScopeId")) != scope_id:
            continue
        groups["evidence"].append({
            "id": _clean(evidence.evidence_id),
            "subject": _clean(evidence.subject),
            "kind": _clean(evidence.kind),
            "source": _clean(evidence.source),
            "summary": _clean(evidence.summary),
            "value": _semantic_properties(values),
        })

    for relation in support_relations:
        if _clean(relation.get("scopeId")) != scope_id:
            continue
        families = [
            _clean(value)
            for value in relation.get("impactFamilies") or []
            if _clean(value)
        ]
        family = families[0] if families else "evidence"
        groups[family].append({
            "source": _clean(relation.get("source")),
            "target": _clean(relation.get("target")),
            "type": _clean(relation.get("type")),
            "properties": _semantic_properties(dict(relation.get("properties") or {})),
        })

    return _fingerprint_semantic_groups(groups)


def _scope_semantic_fingerprints_by_scope(
    graph: PortfolioOntology,
    scope_ids: Iterable[str],
    support_relations: Iterable[Mapping[str, object]] = (),
) -> Dict[str, Dict[str, str]]:
    """Calculate semantic scope fingerprints without repeated full scans.

    This is deliberately a structural optimization only: each grouped item is
    built with the same fields and family rules as
    :func:`_scope_semantic_fingerprints`.  The resulting hashes therefore stay
    compatible with existing active scoped manifests.
    """
    expected = {str(scope_id or "").strip() for scope_id in scope_ids or [] if str(scope_id or "").strip()}
    groups_by_scope: Dict[str, Dict[str, List[Dict[str, object]]]] = {
        scope_id: defaultdict(list)
        for scope_id in expected
    }
    entities_by_id = {
        _clean(entity.entity_id): entity
        for entity in graph.entities
        if _clean(entity.entity_id)
    }

    for entity in graph.entities:
        properties = dict(entity.properties or {})
        scope_id = _clean(properties.get("aboxScopeId"))
        groups = groups_by_scope.get(scope_id)
        if groups is None:
            continue
        fallback = family_for_entity(entity.kind, properties, entity.entity_id)
        groups[fallback].append({
            "entity": _clean(entity.entity_id),
            "kind": _clean(entity.kind),
        })
        for field, value in _semantic_properties(properties).items():
            groups[_semantic_property_family(field, fallback)].append({
                "entity": _clean(entity.entity_id),
                "field": field,
                "value": value,
            })

    for relation in graph.relations:
        properties = dict(relation.properties or {})
        scope_id = _clean(properties.get("aboxScopeId"))
        groups = groups_by_scope.get(scope_id)
        if groups is None:
            continue
        source = entities_by_id.get(_clean(relation.source))
        target = entities_by_id.get(_clean(relation.target))
        family = family_for_relation(
            relation.relation_type,
            properties,
            source_family=scope_family((getattr(source, "properties", {}) or {}).get("aboxScopeId")),
            target_family=scope_family((getattr(target, "properties", {}) or {}).get("aboxScopeId")),
            source_kind=getattr(source, "kind", ""),
            target_kind=getattr(target, "kind", ""),
        )
        groups[family].append({
            "source": _clean(relation.source),
            "target": _clean(relation.target),
            "type": _clean(relation.relation_type),
            "properties": _semantic_properties(properties),
        })

    for evidence in graph.evidence:
        values = dict(evidence.value or {})
        groups = groups_by_scope.get(_clean(values.get("aboxScopeId")))
        if groups is None:
            continue
        groups["evidence"].append({
            "id": _clean(evidence.evidence_id),
            "subject": _clean(evidence.subject),
            "kind": _clean(evidence.kind),
            "source": _clean(evidence.source),
            "summary": _clean(evidence.summary),
            "value": _semantic_properties(values),
        })

    for relation in support_relations:
        groups = groups_by_scope.get(_clean(relation.get("scopeId")))
        if groups is None:
            continue
        families = [
            _clean(value)
            for value in relation.get("impactFamilies") or []
            if _clean(value)
        ]
        family = families[0] if families else "evidence"
        groups[family].append({
            "source": _clean(relation.get("source")),
            "target": _clean(relation.get("target")),
            "type": _clean(relation.get("type")),
            "properties": _semantic_properties(dict(relation.get("properties") or {})),
        })

    return {
        scope_id: _fingerprint_semantic_groups(groups)
        for scope_id, groups in groups_by_scope.items()
    }


def _scope_fragment_payloads_with_semantic_fingerprints_and_dependencies(
    graph: PortfolioOntology,
    scope_ids: Iterable[str],
    support_relations: Iterable[Mapping[str, object]] = (),
) -> tuple[Dict[str, Dict[str, object]], Dict[str, Dict[str, str]], Dict[str, str]]:
    """Build persistence and semantic scope hashes from one normalized pass.

    A scoped projection needs two views of the same ABox rows: the complete
    fragment that determines storage identity, the fact-family fragment used
    by incremental native-rule routing, and a narrower dependency fragment
    used to select only RuleBox conditions whose actual input changed.
    Building them independently repeated deep ``stable_value`` work for every
    entity, relation, and evidence row before TypeDB was even called.
    """
    expected = {
        str(scope_id or "").strip()
        for scope_id in scope_ids or []
        if str(scope_id or "").strip()
    }
    payloads_by_scope: Dict[str, Dict[str, List[Dict[str, object]]]] = {
        scope_id: {"entities": [], "relations": [], "evidence": []}
        for scope_id in expected
    }
    semantic_groups_by_scope: Dict[str, Dict[str, List[Dict[str, object]]]] = {
        scope_id: defaultdict(list)
        for scope_id in expected
    }
    dependency_groups_by_scope: Dict[str, Dict[str, List[Dict[str, object]]]] = {
        scope_id: defaultdict(list)
        for scope_id in expected
    }
    entities_by_id = {
        _clean(entity.entity_id): entity
        for entity in graph.entities
        if _clean(entity.entity_id)
    }

    def semantic_properties_from_stable(
        raw_values: Mapping[str, object],
        stable_values: Mapping[str, object],
    ) -> Dict[str, object]:
        return {
            str(key): stable_values.get(str(key))
            for key in dict(raw_values or {})
            if re.sub(r"[^a-z0-9]", "", str(key or "").lower()) not in _GENERATED_SCOPE_PROPERTY_KEYS
            and re.sub(r"[^a-z0-9]", "", str(key or "").lower()) not in _IMMATERIAL_OBSERVATION_PROPERTY_KEYS
            and re.sub(r"[^a-z0-9]", "", str(key or "").lower()) not in _SEMANTIC_METADATA_FIELDS
        }

    for entity in graph.entities:
        properties = dict(entity.properties or {})
        scope_id = _clean(properties.get("aboxScopeId"))
        payload = payloads_by_scope.get(scope_id)
        semantic_groups = semantic_groups_by_scope.get(scope_id)
        dependency_groups = dependency_groups_by_scope.get(scope_id)
        if payload is None or semantic_groups is None or dependency_groups is None:
            continue
        stable_properties = stable_value(properties)
        payload["entities"].append({
            "id": entity.entity_id,
            "kind": entity.kind,
            "properties": stable_properties,
        })
        fallback = family_for_entity(entity.kind, properties, entity.entity_id)
        semantic_properties = semantic_properties_from_stable(properties, stable_properties)
        semantic_groups[fallback].append({
            "entity": _clean(entity.entity_id),
            "kind": _clean(entity.kind),
        })
        entity_structure = {
            "entity": _clean(entity.entity_id),
            "kind": _clean(entity.kind),
        }
        entity_values = {
            **entity_structure,
            "properties": semantic_properties,
        }
        kind_token = _semantic_dependency_token(entity.kind)
        if kind_token:
            dependency_groups["kind:" + kind_token].append(entity_structure)
            dependency_groups["kind:" + kind_token + ":values"].append(entity_values)
        for field, value in semantic_properties.items():
            field_key = _semantic_dependency_field_key(field)
            if field_key != "field:":
                field_dependency = {
                    "entity": _clean(entity.entity_id),
                    "field": field,
                    "value": value,
                }
                dependency_groups[field_key].append(field_dependency)
                kind_field_key = _semantic_dependency_kind_field_key(entity.kind, field)
                if kind_field_key:
                    dependency_groups[kind_field_key].append(field_dependency)
        for field, value in semantic_properties.items():
            semantic_groups[_semantic_property_family(field, fallback)].append({
                "entity": _clean(entity.entity_id),
                "field": field,
                "value": value,
            })

    for relation in graph.relations:
        properties = dict(relation.properties or {})
        scope_id = _clean(properties.get("aboxScopeId"))
        payload = payloads_by_scope.get(scope_id)
        semantic_groups = semantic_groups_by_scope.get(scope_id)
        dependency_groups = dependency_groups_by_scope.get(scope_id)
        if payload is None or semantic_groups is None or dependency_groups is None:
            continue
        stable_properties = stable_value(properties)
        payload["relations"].append({
            "source": relation.source,
            "target": relation.target,
            "type": relation.relation_type,
            "properties": stable_properties,
        })
        source = entities_by_id.get(_clean(relation.source))
        target = entities_by_id.get(_clean(relation.target))
        family = family_for_relation(
            relation.relation_type,
            properties,
            source_family=scope_family((getattr(source, "properties", {}) or {}).get("aboxScopeId")),
            target_family=scope_family((getattr(target, "properties", {}) or {}).get("aboxScopeId")),
            source_kind=getattr(source, "kind", ""),
            target_kind=getattr(target, "kind", ""),
        )
        relation_structure = {
            "source": _clean(relation.source),
            "target": _clean(relation.target),
            "type": _clean(relation.relation_type),
        }
        relation_properties = semantic_properties_from_stable(properties, stable_properties)
        semantic_groups[family].append({
            **relation_structure,
            "properties": relation_properties,
        })
        relation_token = _semantic_dependency_token(relation.relation_type)
        if relation_token:
            dependency_groups["relation:" + relation_token].append(relation_structure)
        for field, value in relation_properties.items():
            field_key = _semantic_dependency_field_key(field)
            field_dependency = {
                **relation_structure,
                "field": field,
                "value": value,
            }
            if field_key != "field:":
                dependency_groups[field_key].append(field_dependency)
            relation_field_key = _semantic_dependency_relation_field_key(relation.relation_type, field)
            if relation_field_key:
                dependency_groups[relation_field_key].append(field_dependency)

    for evidence in graph.evidence:
        values = dict(evidence.value or {})
        scope_id = _clean(values.get("aboxScopeId"))
        payload = payloads_by_scope.get(scope_id)
        semantic_groups = semantic_groups_by_scope.get(scope_id)
        dependency_groups = dependency_groups_by_scope.get(scope_id)
        if payload is None or semantic_groups is None or dependency_groups is None:
            continue
        stable_values = stable_value(values)
        payload["evidence"].append({
            "id": evidence.evidence_id,
            "subject": evidence.subject,
            "kind": evidence.kind,
            "source": evidence.source,
            "summary": evidence.summary,
            "value": stable_values,
        })
        evidence_structure = {
            "id": _clean(evidence.evidence_id),
            "subject": _clean(evidence.subject),
            "kind": _clean(evidence.kind),
        }
        evidence_properties = {
            **semantic_properties_from_stable(values, stable_values),
            "source": _clean(evidence.source),
            "summary": _clean(evidence.summary),
        }
        semantic_groups["evidence"].append({
            **evidence_structure,
            "value": semantic_properties_from_stable(values, stable_values),
            "source": evidence_properties["source"],
            "summary": evidence_properties["summary"],
        })
        evidence_token = _semantic_dependency_token(evidence.kind)
        if evidence_token:
            dependency_groups["evidence:" + evidence_token].append(evidence_structure)
        for field, value in evidence_properties.items():
            field_key = _semantic_dependency_field_key(field)
            if field_key == "field:":
                continue
            field_dependency = {
                **evidence_structure,
                "field": field,
                "value": value,
            }
            dependency_groups[field_key].append(field_dependency)
            evidence_field_key = "evidence:" + evidence_token + ":field:" + re.sub(
                r"[^a-z0-9]", "", _clean(field).lower()
            ) if evidence_token else ""
            if evidence_field_key:
                dependency_groups[evidence_field_key].append(field_dependency)

    for relation in support_relations:
        scope_id = _clean(relation.get("scopeId"))
        payload = payloads_by_scope.get(scope_id)
        semantic_groups = semantic_groups_by_scope.get(scope_id)
        dependency_groups = dependency_groups_by_scope.get(scope_id)
        if payload is None or semantic_groups is None or dependency_groups is None:
            continue
        properties = dict(relation.get("properties") or {})
        stable_properties = stable_value(properties)
        payload["relations"].append({
            "source": _clean(relation.get("source")),
            "target": _clean(relation.get("target")),
            "type": _clean(relation.get("type")),
            "properties": stable_properties,
        })
        families = [
            _clean(value)
            for value in relation.get("impactFamilies") or []
            if _clean(value)
        ]
        relation_structure = {
            "source": _clean(relation.get("source")),
            "target": _clean(relation.get("target")),
            "type": _clean(relation.get("type")),
        }
        relation_properties = semantic_properties_from_stable(properties, stable_properties)
        semantic_groups[families[0] if families else "evidence"].append({
            **relation_structure,
            "properties": relation_properties,
        })
        relation_token = _semantic_dependency_token(relation.get("type"))
        if relation_token:
            dependency_groups["relation:" + relation_token].append(relation_structure)
        for field, value in relation_properties.items():
            field_key = _semantic_dependency_field_key(field)
            field_dependency = {
                **relation_structure,
                "field": field,
                "value": value,
            }
            if field_key != "field:":
                dependency_groups[field_key].append(field_dependency)
            relation_field_key = _semantic_dependency_relation_field_key(relation.get("type"), field)
            if relation_field_key:
                dependency_groups[relation_field_key].append(field_dependency)

    payloads = {
        scope_id: {
            "entities": sorted(payload["entities"], key=lambda item: (str(item["kind"]), str(item["id"]))),
            "relations": sorted(payload["relations"], key=lambda item: (str(item["type"]), str(item["source"]), str(item["target"]))),
            "evidence": sorted(payload["evidence"], key=lambda item: (str(item["kind"]), str(item["id"]))),
        }
        for scope_id, payload in payloads_by_scope.items()
    }
    semantic_fingerprints = {
        scope_id: _fingerprint_semantic_groups(groups)
        for scope_id, groups in semantic_groups_by_scope.items()
    }
    semantic_dependency_fingerprints = {
        scope_id: pack_semantic_dependency_fingerprints(
            _fingerprint_semantic_groups(groups)
        )
        for scope_id, groups in dependency_groups_by_scope.items()
    }
    return payloads, semantic_fingerprints, semantic_dependency_fingerprints


def _scope_fragment_payloads_with_semantic_fingerprints(
    graph: PortfolioOntology,
    scope_ids: Iterable[str],
    support_relations: Iterable[Mapping[str, object]] = (),
) -> tuple[Dict[str, Dict[str, object]], Dict[str, Dict[str, str]]]:
    """Preserve the two-value helper contract used by existing callers."""
    payloads, semantic_fingerprints, _dependencies = (
        _scope_fragment_payloads_with_semantic_fingerprints_and_dependencies(
            graph,
            scope_ids,
            support_relations,
        )
    )
    return payloads, semantic_fingerprints


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
    # Build the exact same immutable scope fragments and semantic fingerprints
    # in a single graph pass. A target-scoped cycle normally has many retained
    # scopes, so repeatedly scanning the complete graph once per scope made
    # identity preparation dominate the TypeDB write itself.
    payloads, semantic_fingerprints, semantic_dependency_fingerprints = (
        _scope_fragment_payloads_with_semantic_fingerprints_and_dependencies(
        clone,
        scope_ids,
        support_relations,
        )
    )
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
            "semanticFingerprints": dict(sorted(semantic_fingerprints.get(scope_id, {}).items())),
            "semanticDependencyFingerprintVersion": DEPENDENCY_FINGERPRINT_VERSION,
            "semanticDependencyFingerprintsPacked": str(
                semantic_dependency_fingerprints.get(scope_id) or ""
            ),
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
            "source": _clean(relation.get("source")),
            "target": _clean(relation.get("target")),
            "originalSource": _clean(relation.get("originalSource")),
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


def target_scope_manifest_fingerprint(
    scope_plan: Iterable[object],
    target_symbols: Iterable[object],
) -> Dict[str, object]:
    """Fingerprint target-owned scopes and their transitive dependencies."""

    rows = _scope_plan_by_id(scope_plan)
    targets = {
        _symbol(value)
        for value in target_symbols or []
        if _symbol(value)
    }
    selected = {
        scope_id
        for scope_id in rows
        if scope_symbol(scope_id) in targets
    }
    changed = True
    while changed:
        changed = False
        for scope_id in tuple(selected):
            for dependency_id in rows.get(scope_id, {}).get("dependencyScopeIds") or []:
                if dependency_id in rows and dependency_id not in selected:
                    selected.add(dependency_id)
                    changed = True
    payload = {
        scope_id: _clean(
            rows[scope_id].get("semanticFingerprint")
            or rows[scope_id].get("fingerprint")
        )
        for scope_id in sorted(selected)
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "scopeCount": len(payload),
        "scopeManifest": payload,
        "targetSymbols": sorted(targets),
    }


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


def apply_scoped_abox_repair_epochs(
    graph: PortfolioOntology,
    active_metadata: Mapping[str, object],
    repair_requests_by_symbol: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Create a fresh immutable generation for physically damaged scopes.

    The semantic fingerprint stays separate from the repair epoch. Subsequent
    projections retain that epoch while the source facts are unchanged, so a
    repaired scope cannot roll back to the physically damaged storage ID. A
    genuine source change naturally produces a new semantic generation and
    retires the repair epoch.
    """

    worldview = dict(graph.worldview or {})
    incoming = _scope_plan_by_id(
        worldview.get("scopePlan") or [],
        worldview.get("scopeGenerationIds") or {},
        worldview.get("scopeFingerprints") or {},
    )
    active = _scope_plan_by_id(
        dict(active_metadata or {}).get("scopePlan") or [],
        dict(active_metadata or {}).get("scopeGenerationIds") or {},
        dict(active_metadata or {}).get("scopeFingerprints") or {},
    )
    if not incoming:
        return {"status": "skipped-empty-scope-plan", "applied": False}

    requested_epochs: Dict[str, Set[str]] = defaultdict(set)
    for raw_symbol, raw_request in dict(repair_requests_by_symbol or {}).items():
        symbol = _symbol(raw_symbol)
        request = dict(raw_request or {}) if isinstance(raw_request, Mapping) else {}
        request_id = _clean(request.get("requestId"))
        if not symbol or not request_id:
            continue
        for raw_scope_id in request.get("scopeIds") or []:
            scope_id = _clean(raw_scope_id)
            if scope_id in incoming and scope_symbol(scope_id) == symbol:
                requested_epochs[scope_id].add(request_id)

    # A link scope stores endpoint storage IDs. Reissue every dependent link
    # generation together with a repaired endpoint, but never widen into an
    # unrelated symbol or shared world scope.
    affected = set(requested_epochs)
    changed = True
    while changed:
        changed = False
        for scope_id, item in incoming.items():
            dependencies = set(item.get("dependencyScopeIds") or [])
            matching = dependencies.intersection(affected)
            if not matching or scope_id in affected:
                continue
            source_symbols = {scope_symbol(value) for value in matching if scope_symbol(value)}
            if source_symbols and scope_symbol(scope_id) not in source_symbols:
                continue
            affected.add(scope_id)
            for dependency in matching:
                requested_epochs[scope_id].update(requested_epochs.get(dependency) or set())
            changed = True

    repaired_scope_ids = []
    retained_scope_ids = []
    plan = []
    for scope_id in sorted(incoming):
        item = dict(incoming[scope_id])
        semantic_fingerprint = _clean(item.get("semanticFingerprint")) or _clean(item.get("fingerprint"))
        epoch_values = set(requested_epochs.get(scope_id) or set())
        active_item = dict(active.get(scope_id) or {})
        active_semantic = (
            _clean(active_item.get("semanticFingerprint"))
            or _clean(active_item.get("fingerprint"))
        )
        active_epoch = _clean(active_item.get("repairEpoch"))
        carried_epoch = ""
        if not epoch_values and active_epoch and semantic_fingerprint == active_semantic:
            carried_epoch = active_epoch
            retained_scope_ids.append(scope_id)
        if epoch_values or carried_epoch:
            repair_epoch = carried_epoch or hashlib.sha256(
                "|".join(sorted(epoch_values)).encode("utf-8")
            ).hexdigest()[:24]
            repair_fingerprint = hashlib.sha256(json.dumps({
                "semanticFingerprint": semantic_fingerprint,
                "repairEpoch": repair_epoch,
            }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            item.update({
                "semanticFingerprint": semantic_fingerprint,
                "repairEpoch": repair_epoch,
                "fingerprint": repair_fingerprint,
                "generationId": scoped_generation_id(scope_id, repair_fingerprint),
            })
            repaired_scope_ids.append(scope_id)
        else:
            item.pop("semanticFingerprint", None)
            item.pop("repairEpoch", None)
        plan.append(item)

    if not repaired_scope_ids:
        return {"status": "not-required", "applied": False, "scopePlan": plan}
    identity = apply_scoped_manifest_plan(
        graph,
        plan,
        account_id=worldview.get("accountId"),
        world_id=worldview.get("worldId"),
    )
    graph.worldview["scopeRepair"] = {
        "version": "scoped-abox-repair-v1",
        "requestedScopeIds": sorted(requested_epochs),
        "repairedScopeIds": repaired_scope_ids,
        "retainedRepairScopeIds": retained_scope_ids,
        "automaticFullProjectionUsed": False,
    }
    return {
        "status": "applied",
        "applied": True,
        "requestedScopeIds": sorted(requested_epochs),
        "repairedScopeIds": repaired_scope_ids,
        "retainedRepairScopeIds": retained_scope_ids,
        **identity,
    }


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
    fact_slot_plan: Mapping[str, object] = None,
    source_graph_complete: bool = True,
) -> Dict[str, object]:
    """Select the incoming scopes that may replace an active manifest.

    The complete source graph stays available in memory, but an incremental
    worker should only materialize the triggering symbols and shared facts.
    A selected link may reuse an unchanged active endpoint. When an endpoint
    scope changed, however, the incoming link can reference a node that did
    not exist in the active generation, so that direct endpoint scope must be
    staged with the link.
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
    target_scope_retention_mode = _clean(worldview.get("targetScopeRetentionMode"))
    retain_missing_target_scopes = target_scope_retention_mode in {
        "incremental-target-patch",
        "observation-followup",
    }
    base = {
        "targetSymbols": requested_symbols,
        "incomingScopeCount": len(incoming),
        "activeScopeCount": len(active_by_scope),
        "selectedIncomingScopeIds": [],
        "selectedIncomingScopePlan": [],
        "reusedActiveScopeIds": [],
        "deferredScopeIds": [],
        "retiredScopeIds": [],
        "removedRelevantScopeIds": [],
        "retainsMissingTargetScopes": retain_missing_target_scopes,
        "targetScopeRetentionMode": target_scope_retention_mode,
        "sourceGraphComplete": bool(source_graph_complete),
        "factSlot": dict(fact_slot_plan or {}),
    }
    if not requested_symbols:
        return {
            **base,
            "status": "skipped-no-target-symbols",
            "applied": False,
            "fallbackReason": "no-target-symbols",
        }
    if not incoming:
        return {
            **base,
            "status": "skipped-empty-incoming-plan",
            "applied": False,
            "fallbackReason": "empty-incoming-scope-plan",
        }
    if str(active.get("status") or "").lower() != "ok" or not active_by_scope:
        return {
            **base,
            "status": "skipped-active-manifest-unavailable",
            "applied": False,
            "fallbackReason": "active-scoped-manifest-unavailable",
        }
    if str(active.get("scopedAboxManifestVersion") or "") != SCOPED_ABOX_MANIFEST_VERSION:
        return {
            **base,
            "status": "skipped-active-manifest-legacy",
            "applied": False,
            "fallbackReason": "active-scoped-manifest-version-mismatch",
        }
    active_topology_version = str(active.get("scopeTopologyVersion") or "")
    legacy_target_scope_ids = sorted(
        scope_id
        for scope_id in active_by_scope
        if scope_symbol(scope_id) in requested_symbols
        and scope_requires_v8_bounded_slot(scope_id)
    )
    topology_migration_required = bool(
        active_topology_version != SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION
        or legacy_target_scope_ids
    )
    topology_migration = {
        "applied": topology_migration_required,
        "fromVersion": active_topology_version,
        "toVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
        "legacyTargetScopeIds": legacy_target_scope_ids,
        "subjectScoped": True,
        "fullWorldRewriteUsed": False,
    }
    base.update({
        "scopeTopologyMigration": topology_migration,
        "activeScopeTopologyVersion": active_topology_version,
        "targetScopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
        "legacyTargetScopeIds": legacy_target_scope_ids,
    })

    def is_requested_or_shared(scope_id: str) -> bool:
        symbol = scope_symbol(scope_id)
        return symbol in requested_symbols or not symbol

    def changed_from_active(scope_id: str, item: Mapping[str, object]) -> bool:
        active_item = active_by_scope.get(scope_id)
        if not active_item:
            return True
        return (
            _clean(active_item.get("generationId")) != _clean(item.get("generationId"))
            or _clean(active_item.get("fingerprint")) != _clean(item.get("fingerprint"))
        )

    def changed_mapping_keys(
        active_item: Mapping[str, object],
        incoming_item: Mapping[str, object],
        key: str,
    ) -> List[str]:
        if key == "semanticDependencyFingerprints":
            before = unpack_semantic_dependency_fingerprints(active_item)
            after = unpack_semantic_dependency_fingerprints(incoming_item)
        else:
            before = dict(active_item.get(key) or {})
            after = dict(incoming_item.get(key) or {})
        return sorted({
            _clean(value)
            for value in set(before) | set(after)
            if _clean(value) and _clean(before.get(value)) != _clean(after.get(value))
        })

    selection_reasons: Dict[str, Set[str]] = {}
    semantic_changes_by_scope: Dict[str, List[str]] = {}
    dependency_changes_by_scope: Dict[str, List[str]] = {}

    def record_direct_change(scope_id: str, item: Mapping[str, object]) -> None:
        active_item = active_by_scope.get(scope_id) or {}
        reasons = selection_reasons.setdefault(scope_id, set())
        if not active_item:
            reasons.add("new-scope")
            return
        semantic_changes = changed_mapping_keys(
            active_item,
            item,
            "semanticFingerprints",
        )
        dependency_changes = changed_mapping_keys(
            active_item,
            item,
            "semanticDependencyFingerprints",
        )
        if semantic_changes:
            semantic_changes_by_scope[scope_id] = semantic_changes
            reasons.add("semantic-value-change")
        if dependency_changes:
            dependency_changes_by_scope[scope_id] = dependency_changes
            reasons.add("rule-dependency-change")
        if reasons:
            return
        if _clean(active_item.get("fingerprint")) != _clean(item.get("fingerprint")):
            reasons.add("persistence-dependency-rebind")
        elif _clean(active_item.get("generationId")) != _clean(item.get("generationId")):
            reasons.add("generation-only-change")

    # A target symbol can have many independently versioned fact families.
    # Rewriting all of them for one changed quote defeats scoped persistence;
    # select only changed target/shared scopes and let stable active endpoint
    # generations satisfy the remaining links.
    selected: Set[str] = set()
    for scope_id, item in incoming.items():
        if not is_requested_or_shared(scope_id) or not changed_from_active(scope_id, item):
            continue
        selected.add(scope_id)
        record_direct_change(scope_id, item)

    # An authoritative mailbox event owns a narrow set of fact slots. A
    # symbol-less link whose assertion did not change can still receive a new
    # storage generation only because one of its global endpoints was rebuilt
    # while assembling the complete in-memory graph. Rebinding that unrelated
    # link expands a calendar/news update into reference, profile, exposure,
    # and macro writes. Keep the active link and endpoint generations until
    # their own source event reports a semantic or rule-dependency change.
    if bool((fact_slot_plan or {}).get("eventBoundaryAuthoritative")):
        persistence_only_shared_links = {
            scope_id
            for scope_id in selected
            if _scope_type(scope_id) == "link"
            and not scope_symbol(scope_id)
            and bool(selection_reasons.get(scope_id))
            and set(selection_reasons.get(scope_id) or []).issubset({
                "persistence-dependency-rebind",
                "generation-only-change",
            })
        }
        selected.difference_update(persistence_only_shared_links)
        for scope_id in persistence_only_shared_links:
            selection_reasons.setdefault(scope_id, set()).update({
                "deferred-shared-persistence-rebind",
                "deferred-unrelated-event-fact-slot",
            })
    fact_slot_selection = select_fact_slot_scope_ids(
        incoming,
        selected,
        fact_slot_plan,
    )
    if str(fact_slot_selection.get("status") or "").startswith("blocked-"):
        return {
            **base,
            "status": str(fact_slot_selection.get("status") or "blocked-fact-slot"),
            "applied": False,
            "fallbackReason": str(
                fact_slot_selection.get("fallbackReason") or "authoritative-fact-slot-unresolved"
            ),
            "factSlot": dict(fact_slot_selection),
        }
    selected = set(fact_slot_selection.get("selectedScopeIds") or selected)
    fact_slot_deferred_scope_ids = {
        _clean(scope_id)
        for scope_id in fact_slot_selection.get("deferredScopeIds") or []
        if _clean(scope_id)
    }
    if topology_migration_required:
        # Replace one complete subject boundary so v7 and v8 copies of the
        # same fact never coexist in the active manifest. Other subjects stay
        # on their verified v7 generations until their own durable event.
        for scope_id, item in incoming.items():
            if scope_symbol(scope_id) not in requested_symbols:
                continue
            selected.add(scope_id)
            selection_reasons.setdefault(scope_id, set()).add(
                "bounded-scope-topology-migration"
            )
            if changed_from_active(scope_id, item):
                record_direct_change(scope_id, item)
    repair_scope_ids = {
        _clean(scope_id)
        for scope_id in dict(worldview.get("scopeRepair") or {}).get("repairedScopeIds") or []
        if _clean(scope_id)
    }
    for scope_id in repair_scope_ids:
        item = incoming.get(scope_id) or {}
        if item and is_requested_or_shared(scope_id) and changed_from_active(scope_id, item):
            selected.add(scope_id)
            selection_reasons.setdefault(scope_id, set()).add("scope-integrity-repair")
    if bool(fact_slot_selection.get("enabled")):
        for scope_id in selected:
            selection_reasons.setdefault(scope_id, set()).add("event-fact-slot")
        for scope_id in fact_slot_selection.get("deferredScopeIds") or []:
            selection_reasons.setdefault(scope_id, set()).add(
                "deferred-unrelated-event-fact-slot"
            )
    elif selected:
        for scope_id in selected:
            selection_reasons.setdefault(scope_id, set()).add("conservative-fallback")

    # A target-scoped quote follow-up is a partial current-state input. Its
    # absent scope rows have no deletion meaning; they merely remain on the
    # active verified manifest until an explicit scoped source fact proves removal.
    # Treating those omissions as retirement forced a full manifest rewrite
    # (and frequently a 300s+ TypeDB cycle) for a small price observation.
    if topology_migration_required:
        # A topology migration is deliberately subject-scoped. Shared and
        # unrelated subject scopes may not be present in the partial incoming
        # graph, so retiring them here would silently remove verified facts.
        removed_relevant_scopes = sorted(
            scope_id
            for scope_id in active_by_scope
            if scope_id not in incoming
            and scope_symbol(scope_id) in requested_symbols
        )
    elif retain_missing_target_scopes:
        removed_relevant_scopes = []
    else:
        removed_relevant_scopes = sorted(
            scope_id
            for scope_id in active_by_scope
            if scope_id not in incoming and is_requested_or_shared(scope_id)
        )
    retired_scope_ids = sorted(
        scope_id
        for scope_id in removed_relevant_scopes
        if _symbol(scope_symbol(scope_id)) in requested_symbols
    )
    shared_removed_scope_ids = sorted(
        set(removed_relevant_scopes) - set(retired_scope_ids)
    )
    # A shared scope can affect every active subject, so its absence remains
    # a conservative whole-manifest reconciliation. A target-local scope is
    # different: its pointer can be retired incrementally when no retained
    # scope still declares a dependency on it.
    if shared_removed_scope_ids:
        return {
            **base,
            "status": "skipped-removed-scope-requires-full-refresh",
            "applied": False,
            "fallbackReason": "shared-scope-removed",
            "removedRelevantScopeIds": removed_relevant_scopes,
            "retiredScopeIds": retired_scope_ids,
            "sharedRemovedScopeIds": shared_removed_scope_ids,
        }
    retired_scope_set = set(retired_scope_ids)

    def target_owned_scope(scope_id: str) -> bool:
        direct_symbol = _symbol(scope_symbol(scope_id))
        if direct_symbol in requested_symbols:
            return True
        # Link scope IDs intentionally preserve their owning subject even
        # when ``scope_symbol`` treats the link itself as shared metadata.
        return any(
            ("symbol:" + symbol + ":") in _clean(scope_id)
            for symbol in requested_symbols
        )

    replaced_dependency_scope_ids: Set[str] = set()
    cascaded_retired_scope_ids: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for scope_id, active_item in active_by_scope.items():
            if scope_id in retired_scope_set:
                continue
            active_dependencies = {
                _clean(dependency)
                for dependency in active_item.get("dependencyScopeIds") or []
                if _clean(dependency)
            }
            if not active_dependencies.intersection(retired_scope_set):
                continue
            incoming_item = incoming.get(scope_id)
            incoming_dependencies = {
                _clean(dependency)
                for dependency in (incoming_item or {}).get("dependencyScopeIds") or []
                if _clean(dependency)
            }
            if incoming_item and not incoming_dependencies.intersection(retired_scope_set):
                # Replace the retained link with the current source version,
                # which no longer points at the retired target fact.
                selected.add(scope_id)
                replaced_dependency_scope_ids.add(scope_id)
                selection_reasons.setdefault(scope_id, set()).add(
                    "replace-retired-dependency-reference"
                )
                continue
            if incoming_item is None and target_owned_scope(scope_id):
                # The source removed both a target fact and its target-owned
                # link. Retire them together; unrelated/shared links remain
                # protected by the blocking check below.
                retired_scope_set.add(scope_id)
                cascaded_retired_scope_ids.add(scope_id)
                changed = True

    retired_scope_ids = sorted(retired_scope_set)
    retained_active_by_scope = {
        scope_id: item
        for scope_id, item in active_by_scope.items()
        if scope_id not in retired_scope_set
    }
    retained_dependency_references = sorted(
        scope_id
        for scope_id, item in retained_active_by_scope.items()
        if any(
            _clean(dependency) in retired_scope_set
            for dependency in (
                (incoming.get(scope_id) or {}).get("dependencyScopeIds") or []
                if scope_id in selected and scope_id in incoming
                else item.get("dependencyScopeIds") or []
            )
        )
    )
    selected_dependency_references = sorted(
        scope_id
        for scope_id in selected
        if any(
            _clean(dependency) in retired_scope_set
            for dependency in (incoming.get(scope_id) or {}).get("dependencyScopeIds") or []
        )
    )
    if retained_dependency_references or selected_dependency_references:
        return {
            **base,
            "status": "skipped-retired-scope-still-referenced",
            "applied": False,
            "fallbackReason": "retired-target-scope-still-referenced",
            "removedRelevantScopeIds": removed_relevant_scopes,
            "retiredScopeIds": retired_scope_ids,
            "retainedDependencyScopeIds": retained_dependency_references,
            "selectedDependencyScopeIds": selected_dependency_references,
            "replacedDependencyScopeIds": sorted(replaced_dependency_scope_ids),
            "cascadedRetiredScopeIds": sorted(cascaded_retired_scope_ids),
        }

    # A relation can point to a brand new endpoint inside an existing scope.
    # The active scope marker alone cannot prove that exact endpoint exists.
    # Stage changed direct dependencies with a selected link; unchanged active
    # endpoint generations remain reusable without expanding the patch.
    node_scopes = _graph_node_scopes(graph)
    # Directly selected relation scopes may pull their endpoints into the
    # candidate generation. Those endpoint scopes are integrity companions,
    # not new traversal roots. Treating them as roots walks every other
    # relation owned by the stock anchor and expands one valuation update into
    # market, flow, temporal, evidence, and portfolio persistence.
    relation_owner_traversal_scopes: Set[str] = {
        scope_id
        for scope_id in selected
        if _scope_type(scope_id) == "link"
    }
    # Only scopes selected by the source fact boundary may trigger reverse
    # relation rebinding. Endpoint companion scopes are added for integrity,
    # but must not become traversal roots themselves; otherwise selecting one
    # stock anchor pulls every unrelated relation owned by that anchor.
    relation_rebind_root_scope_ids: Set[str] = set(selected)

    def include_missing_dependency(
        scope_id: str,
        missing: List[str],
        reason: str,
        traverse_owned_relations: bool = False,
    ) -> None:
        if scope_id in selected:
            if reason:
                selection_reasons.setdefault(scope_id, set()).add(reason)
            if traverse_owned_relations and _scope_type(scope_id) == "link":
                relation_owner_traversal_scopes.add(scope_id)
            return
        if scope_id not in incoming:
            missing.append(scope_id)
            return
        selected.add(scope_id)
        if traverse_owned_relations and _scope_type(scope_id) == "link":
            relation_owner_traversal_scopes.add(scope_id)
        selection_reasons.setdefault(scope_id, set()).add(
            reason or "required-missing-dependency"
        )

    def dependency_requires_staging(scope_id: str) -> bool:
        item = incoming.get(scope_id)
        if not item:
            return scope_id not in retained_active_by_scope
        return (
            scope_id not in retained_active_by_scope
            or changed_from_active(scope_id, item)
        )

    def dependency_reason(scope_id: str, changed_reason: str, missing_reason: str) -> str:
        return (
            changed_reason
            if scope_id in retained_active_by_scope
            else missing_reason
        )

    missing_endpoints: List[str] = []
    incomplete_source_endpoint_scopes: List[str] = []
    changed = True
    while changed:
        changed = False
        before = len(selected)
        for scope_id in list(selected):
            row = incoming.get(scope_id) or {}
            for dependency in row.get("dependencyScopeIds") or []:
                dependency_id = _clean(dependency)
                if dependency_id and dependency_requires_staging(dependency_id):
                    include_missing_dependency(
                        dependency_id,
                        missing_endpoints,
                        dependency_reason(
                            dependency_id,
                            "required-changed-dependency",
                            "required-missing-dependency",
                        ),
                        traverse_owned_relations=_scope_type(dependency_id) == "link",
                    )
        # Copy-on-write changes the physical identity of a node. Every
        # relation scope that depends on that node must therefore be rebound
        # even when its own semantic payload did not change. Keep this reverse
        # dependency closure aligned with the repository write planner so a
        # relation cannot appear only after endpoint selection has finished.
        for scope_id, row in incoming.items():
            if scope_id in selected or _scope_type(scope_id) != "link":
                continue
            if (
                bool((fact_slot_plan or {}).get("eventBoundaryAuthoritative"))
                and scope_id in fact_slot_deferred_scope_ids
            ):
                # The repository will physically rebind an existing active
                # link when one of its endpoint generations changes. Do not
                # replace that link's semantic payload with an unrelated
                # relation change that merely happened to share the same
                # in-memory subject graph.
                selection_reasons.setdefault(scope_id, set()).add(
                    "deferred-unrelated-event-relation"
                )
                continue
            dependencies = {
                _clean(value)
                for value in row.get("dependencyScopeIds") or []
                if _clean(value)
            }
            if not dependencies.intersection(relation_rebind_root_scope_ids):
                continue
            include_missing_dependency(
                scope_id,
                missing_endpoints,
                "required-dependent-link-rebind",
                traverse_owned_relations=True,
            )
        for relation in graph.relations:
            properties = dict(relation.properties or {})
            owner_scope = _clean(properties.get("aboxScopeId"))
            if owner_scope not in relation_owner_traversal_scopes:
                continue
            for endpoint in (_clean(relation.source), _clean(relation.target)):
                endpoint_scope = node_scopes.get(endpoint, "")
                if endpoint_scope and dependency_requires_staging(endpoint_scope):
                    if (
                        not source_graph_complete
                        and endpoint_scope in fact_slot_deferred_scope_ids
                        and ":item:" not in endpoint_scope
                    ):
                        incomplete_source_endpoint_scopes.append(endpoint_scope)
                        continue
                    include_missing_dependency(
                        endpoint_scope,
                        missing_endpoints,
                        dependency_reason(
                            endpoint_scope,
                            "required-changed-link-endpoint",
                            "required-link-endpoint",
                        ),
                    )
        support_scopes = dict(worldview.get("supportRelationScopes") or {})
        for evidence in graph.evidence:
            key = support_relation_key("HAS_EVIDENCE", evidence.subject, evidence.evidence_id)
            support_metadata = dict(support_scopes.get(key) or {})
            owner_scope = _clean(support_metadata.get("scopeId"))
            if owner_scope not in relation_owner_traversal_scopes:
                continue
            for endpoint in (
                _clean(support_metadata.get("source")) or _clean(evidence.subject),
                _clean(support_metadata.get("target")) or _clean(evidence.evidence_id),
            ):
                endpoint_scope = node_scopes.get(endpoint, "")
                if endpoint_scope and dependency_requires_staging(endpoint_scope):
                    if (
                        not source_graph_complete
                        and endpoint_scope in fact_slot_deferred_scope_ids
                        and ":item:" not in endpoint_scope
                    ):
                        incomplete_source_endpoint_scopes.append(endpoint_scope)
                        continue
                    include_missing_dependency(
                        endpoint_scope,
                        missing_endpoints,
                        dependency_reason(
                            endpoint_scope,
                            "required-changed-evidence-endpoint",
                            "required-evidence-endpoint",
                        ),
                    )
        changed = len(selected) != before

    if incomplete_source_endpoint_scopes:
        return {
            **base,
            "status": "skipped-incomplete-link-endpoint-source",
            "applied": False,
            "fallbackReason": "changed-link-endpoint-requires-complete-source",
            "missingEndpointScopeIds": sorted(set(incomplete_source_endpoint_scopes)),
            "factSlot": fact_slot_selection,
        }

    if missing_endpoints:
        return {
            **base,
            "status": "skipped-missing-link-endpoint-scope",
            "applied": False,
            "fallbackReason": "new-link-endpoint-has-no-active-scope",
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

    def selection_trace(scope_ids: Iterable[str], disposition: str) -> List[Dict[str, object]]:
        rows = []
        for scope_id in sorted({_clean(value) for value in scope_ids if _clean(value)}):
            item = incoming.get(scope_id) or active_by_scope.get(scope_id) or {}
            reasons = sorted(selection_reasons.get(scope_id) or [])
            if disposition == "deferred" and not reasons:
                reasons = ["unrelated-event-fact-slot"]
            rows.append({
                "scopeId": scope_id,
                "scopeFamily": _clean(item.get("scopeFamily")) or scope_family(scope_id),
                "symbol": scope_symbol(scope_id),
                "disposition": disposition,
                "reasons": reasons,
                "semanticChangedFamilies": list(
                    semantic_changes_by_scope.get(scope_id) or []
                ),
                "changedDependencyKeys": list(
                    dependency_changes_by_scope.get(scope_id) or []
                ),
            })
        return rows

    required_endpoint_rebind_scope_ids = sorted(
        scope_id
        for scope_id in selected
        if _scope_type(scope_id) == "link"
        and "persistence-dependency-rebind" in selection_reasons.get(scope_id, set())
        and not semantic_changes_by_scope.get(scope_id)
        and not dependency_changes_by_scope.get(scope_id)
    )
    deferred_persistence_rebind_scope_ids = sorted(
        scope_id
        for scope_id in deferred
        if "persistence-dependency-rebind" in selection_reasons.get(scope_id, set())
        and not semantic_changes_by_scope.get(scope_id)
        and not dependency_changes_by_scope.get(scope_id)
    )
    deferred_relation_scope_ids = sorted(
        scope_id
        for scope_id in deferred
        if _scope_type(scope_id) == "link"
    )
    reused_active_relation_scope_ids = sorted(
        scope_id
        for scope_id in deferred_relation_scope_ids
        if scope_id in retained_active_by_scope
    )

    return {
        **base,
        "status": "ready",
        "applied": True,
        "selectedIncomingScopeIds": sorted(selected),
        "selectedIncomingScopePlan": selected_plan,
        "reusedActiveScopeIds": sorted(set(retained_active_by_scope) - selected),
        "deferredScopeIds": sorted(deferred),
        "deferredRelationScopeIds": deferred_relation_scope_ids,
        "reusedActiveRelationScopeIds": reused_active_relation_scope_ids,
        "retiredScopeIds": retired_scope_ids,
        "replacedDependencyScopeIds": sorted(replaced_dependency_scope_ids),
        "cascadedRetiredScopeIds": sorted(cascaded_retired_scope_ids),
        "removedRelevantScopeIds": removed_relevant_scopes,
        "factSlot": fact_slot_selection,
        "scopeSelectionTrace": {
            "version": "target-scope-selection-trace-v2",
            "selected": selection_trace(selected, "selected"),
            "deferred": selection_trace(deferred, "deferred"),
            "requiredEndpointRebindScopeIds": required_endpoint_rebind_scope_ids,
            "requiredEndpointRebindScopeCount": len(
                required_endpoint_rebind_scope_ids
            ),
            "deferredPersistenceRebindScopeIds": deferred_persistence_rebind_scope_ids,
            "deferredPersistenceRebindScopeCount": len(
                deferred_persistence_rebind_scope_ids
            ),
            "deferredRelationScopeIds": deferred_relation_scope_ids,
            "reusedActiveRelationScopeIds": reused_active_relation_scope_ids,
            "semanticChangedScopeCount": len(semantic_changes_by_scope),
            "ruleDependencyChangedScopeCount": len(
                dependency_changes_by_scope
            ),
            "relationRebindRootScopeIds": sorted(relation_rebind_root_scope_ids),
            "relationRebindRootScopeCount": len(relation_rebind_root_scope_ids),
        },
        "scopeTopologyMigration": topology_migration,
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
        "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
        "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
        "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
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
    fact_slot_plan: Mapping[str, object] = None,
    source_graph_complete: bool = True,
) -> Dict[str, object]:
    """Replace only target-symbol scopes while retaining active generations."""

    selection = select_target_scoped_manifest_patch(
        graph,
        active_metadata,
        target_symbols,
        fact_slot_plan=fact_slot_plan,
        source_graph_complete=source_graph_complete,
    )
    if not selection.get("applied"):
        return selection
    active = dict(active_metadata or {})
    active_by_scope = _scope_plan_by_id(
        active.get("scopePlan") or [],
        active.get("scopeGenerationIds") or {},
        active.get("scopeFingerprints") or {},
    )
    for scope_id in selection.get("retiredScopeIds") or []:
        active_by_scope.pop(_clean(scope_id), None)
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
        "deferredRelationScopeIds": list(
            selection.get("deferredRelationScopeIds") or []
        ),
        "reusedActiveRelationScopeIds": list(
            selection.get("reusedActiveRelationScopeIds") or []
        ),
        "retiredScopeIds": list(selection.get("retiredScopeIds") or []),
        "factSlot": dict(selection.get("factSlot") or {}),
        "relationRebindRootScopeIds": list(
            (selection.get("scopeSelectionTrace") or {}).get(
                "relationRebindRootScopeIds"
            ) or []
        ),
        "scopeTopologyMigration": dict(
            selection.get("scopeTopologyMigration") or {}
        ) if isinstance(selection.get("scopeTopologyMigration"), Mapping) else {},
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
