"""Synthetic source packets for projection extraction and replay contracts."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.ontology_worlds import world_from_snapshot


AS_OF = "2026-07-20T00:01:00Z"
TBOX = {"version": "fixture-tbox", "fingerprint": "fixture-tbox-fingerprint"}
RULES = {
    "ruleboxRulesHash": "fixture-release-rules",
    "inputRelationTypes": ["HAS_PRICE"],
    "rules": [
        {
            "rule_id": "fixture.price.observation",
            "source_kind": "stock",
            "enabled": True,
            "conditions": [{"kind": "relation", "relation_type": "HAS_PRICE"}],
        }
    ],
}


def source_snapshot(account_id="fixture-account", as_of=AS_OF):
    return AccountSnapshot(
        account_id,
        "Fixture",
        "fixture",
        "live",
        "ok",
        as_of,
        PortfolioSummary(
            total=1000,
            invested=800,
            cash=200,
            markets=[],
            sectors=[],
            concentration=0.5,
        ),
        positions=[
            Position(
                "AAA",
                "Alpha",
                market="US",
                currency="USD",
                quantity=2,
                current_price=100,
                market_value=200,
                source_as_of=as_of,
            )
        ],
        watchlist=[
            Position("BBB", "Beta", market="US", currency="USD", current_price=50)
        ],
        external_signals={
            "equityQuotes": {
                "AAA": {"price": 100, "source": "fixture", "asOf": as_of},
                "BBB": {"price": 50, "source": "fixture", "asOf": as_of},
            }
        },
        metadata={
            "sourceEventId": "source:fixture",
            "dataPipelineHealth": {"status": "ok"},
        },
    )


class PersistentCache:
    def __init__(self, fail_read=False, fail_write=False):
        self.rows = {}
        self.calls = []
        self.fail_read = fail_read
        self.fail_write = fail_write

    def get(self, key, ttl):
        self.calls.append("get")
        if self.fail_read:
            raise RuntimeError("fixture cache read unavailable")
        return deepcopy(self.rows.get(key, {"status": "miss"}))

    def put(self, key, graph, persistence_graph, ttl, maximum, byte_limit, packet):
        self.calls.append("put")
        if self.fail_write:
            raise RuntimeError("fixture cache write unavailable")
        self.rows[key] = deepcopy(
            {
                "status": "hit",
                "graph": graph,
                "persistenceGraph": persistence_graph,
                "runtimeContextPacket": packet,
                "ageMs": 0,
            }
        )
        return {"status": "stored"}


def recorder(
    api, snapshot=None, *, cache=False, persistent=None, scorer=None, overrides=True
):
    snapshot = snapshot or source_snapshot()
    settings = {
        "_reasoningEngineDeploymentId": "fixture-deployment",
        "_reasoningEngineReleaseFingerprint": "fixture-release",
        "ontologyProjectionGraphCacheEnabled": "1" if cache else "0",
        "ontologyProjectionGraphPersistentCacheEnabled": "1" if persistent else "0",
        "ontologyProjectionRuntimeContextCacheEnabled": "1" if cache else "0",
    }
    context = {
        "asOf": snapshot.generated_at,
        "snapshotId": "abox-snapshot:fixture-source",
        "settings": settings,
        "account": {"accountId": snapshot.account_id, "provider": "fixture"},
        "metadata": deepcopy(snapshot.metadata),
        "activeTBox": deepcopy(TBOX),
        "decisionItems": [],
        "decisionEpisodes": [],
        "hypothesisProposals": [],
        "temporalObservationWindows": {},
    }
    return api.PortfolioOntologyProjectionRecorder(
        SimpleNamespace(
            store_key="fixture", address="fixture.invalid", database="isolated-fixture"
        ),
        settings=settings,
        frozen_rulebox_catalog=RULES,
        frozen_tbox_metadata=TBOX,
        runtime_context_overrides={snapshot.account_id: context} if overrides else {},
        graph_assembly_cache_store=persistent,
        statistical_signal_service=scorer,
        outcome_observation_service=SimpleNamespace(
            observe_snapshot=lambda source: {"status": "fixture"}
        ),
    )


def clear_caches(api):
    for cache in [
        api.SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE,
        api.SHARED_PROJECTION_RUNTIME_CONTEXT_CACHE,
    ]:
        with cache.lock:
            cache.entries.clear()


def stable_payload(value):
    if isinstance(value, dict):
        return {
            str(key): stable_payload(item)
            for key, item in value.items()
            if not str(key).endswith("Ms")
        }
    if isinstance(value, (list, tuple)):
        return [stable_payload(item) for item in value]
    return value


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            stable_payload(value),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def assembly_scenario(api, scenario):
    clear_caches(api)
    snapshot = source_snapshot()
    persistent = (
        PersistentCache(fail_read=True, fail_write=True)
        if scenario == "cache-errors"
        else (PersistentCache() if scenario == "persistent-hit" else None)
    )
    cache = scenario in {"memory-hit", "persistent-hit", "cache-errors"}
    scorer_calls = []

    def score(**kwargs):
        scorer_calls.append(
            {
                key: value
                for key, value in kwargs.items()
                if key not in {"graph", "rules"}
            }
        )
        raise RuntimeError("fixture model input unavailable")

    instance = recorder(
        api,
        snapshot,
        cache=cache,
        persistent=persistent,
        scorer=SimpleNamespace(run=score) if scenario == "scoring-failure" else None,
        overrides=scenario != "live-context",
    )
    rules = {} if scenario == "scoring-failure" else RULES
    events = []
    callback = lambda stage, **details: events.append([stage, details])
    request = (
        {
            "sourceFacts": [{"symbol": "AAA", "family": "price", "observedAt": AS_OF}],
            "semanticChangeSet": {"fingerprint": "change:fixture"},
        }
        if scenario == "source-facts"
        else {}
    )
    kwargs = {
        "target_symbols": ["AAA"] if scenario == "target" else None,
        "target_scoped_input": scenario == "target",
        "progress_callback": callback,
        "reasoning_context": request,
    }
    with patch.object(api.time, "perf_counter", return_value=100.0), patch.object(
        api.time, "monotonic", return_value=100.0
    ):
        if scenario in {"memory-hit", "persistent-hit"}:
            instance.build_graph_assembly(snapshot, rules, **kwargs)
            if scenario == "persistent-hit":
                clear_caches(api)
            events.clear()
        graph, persisted, telemetry = instance.build_graph_assembly(
            snapshot, rules, **kwargs
        )
    return {
        "graph": asdict(graph),
        "persistenceGraph": asdict(persisted),
        "telemetry": telemetry,
        "events": events,
        "contexts": instance.last_runtime_contexts,
        "source": asdict(snapshot),
        "scorerCalls": scorer_calls,
        "cacheCalls": persistent.calls if persistent else [],
    }


def contract_scenarios(api):
    scenarios = {
        name: fingerprint(assembly_scenario(api, name))
        for name in (
            "full",
            "target",
            "source-facts",
            "live-context",
            "memory-hit",
            "persistent-hit",
            "cache-errors",
            "scoring-failure",
        )
    }
    clear_caches(api)
    snapshot = source_snapshot()
    instance = recorder(api, snapshot)
    with patch.object(api.time, "perf_counter", return_value=100.0):
        result = instance.build_projection_graph(
            snapshot, RULES, world_from_snapshot(snapshot, instance.settings)
        )
    result["graph"] = asdict(result["graph"])
    result["persistenceGraph"] = asdict(result["persistenceGraph"])
    scenarios["world-identity"] = fingerprint(result)
    return scenarios
