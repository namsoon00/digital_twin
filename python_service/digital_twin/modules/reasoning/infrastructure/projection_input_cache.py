"""Copy-isolated process caches and best-effort durable assembly cache access."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from digital_twin.domain.ontology_contracts import PortfolioOntology
from threading import Lock
from typing import Dict
import time
from digital_twin.modules.reasoning.application.projection_input.ports import (
    PersistentCacheInputs,
)


class SharedProjectionRuntimeContextCache:
    """Reuse immutable runtime context for one exact source boundary briefly."""

    def __init__(self):
        self.lock = Lock()
        self.entries: "OrderedDict[str, Dict[str, object]]" = OrderedDict()

    def get(self, key: str, ttl_seconds: float) -> Dict[str, object]:
        if not key or ttl_seconds <= 0:
            return {"status": "disabled"}
        now = time.monotonic()
        with self.lock:
            expired = [
                entry_key
                for entry_key, entry in self.entries.items()
                if now - float(entry.get("createdMonotonic") or 0) > ttl_seconds
            ]
            for entry_key in expired:
                self.entries.pop(entry_key, None)
            entry = self.entries.pop(key, None)
            if not isinstance(entry, dict):
                return {"status": "miss"}
            self.entries[key] = entry
            return {
                "status": "hit",
                "ageMs": int(
                    (now - float(entry.get("createdMonotonic") or now)) * 1000
                ),
                "context": deepcopy(entry.get("context") or {}),
            }

    def put(self, key: str, context: Dict[str, object], max_entries: int) -> None:
        if not key or max_entries <= 0:
            return
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = {
                "createdMonotonic": time.monotonic(),
                "context": deepcopy(context or {}),
            }
            while len(self.entries) > max_entries:
                self.entries.popitem(last=False)


class SharedPortfolioGraphAssemblyCache:
    """Reuse one immutable source snapshot's pure ABox assembly briefly.

    Target-scoped TypeDB inference runs can arrive one after another for the
    exact same account snapshot.  Rebuilding the complete ABox for every
    target adds several seconds without changing the facts TypeDB receives.
    The cache keeps only the pre-identity graph pair in process memory; every
    caller gets a deep copy before manifest/scoped-generation fields are
    applied.  A cache key includes the complete source snapshot, runtime
    settings, rule catalog hash, and graph-store namespace, so a fresh source
    observation or configuration change cannot reuse an old graph.
    """

    def __init__(self):
        self.lock = Lock()
        self.entries: "OrderedDict[str, Dict[str, object]]" = OrderedDict()

    def get(self, key: str, ttl_seconds: float) -> Dict[str, object]:
        if not key or ttl_seconds <= 0:
            return {"status": "disabled"}
        now = time.monotonic()
        with self.lock:
            expired = [
                entry_key
                for entry_key, entry in self.entries.items()
                if now - float(entry.get("createdMonotonic") or 0) > ttl_seconds
            ]
            for entry_key in expired:
                self.entries.pop(entry_key, None)
            entry = self.entries.pop(key, None)
            if not isinstance(entry, dict):
                return {"status": "miss"}
            self.entries[key] = entry
            return {
                "status": "hit",
                "ageMs": int(
                    (now - float(entry.get("createdMonotonic") or now)) * 1000
                ),
                "graph": deepcopy(entry["graph"]),
                "persistenceGraph": deepcopy(entry["persistenceGraph"]),
                "runtimeContextPacket": deepcopy(
                    entry.get("runtimeContextPacket") or {}
                ),
            }

    def put(
        self,
        key: str,
        graph: PortfolioOntology,
        persistence_graph: PortfolioOntology,
        max_entries: int,
        runtime_context_packet: Dict[str, object] = None,
    ) -> None:
        if not key or max_entries <= 0:
            return
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = {
                "createdMonotonic": time.monotonic(),
                "graph": deepcopy(graph),
                "persistenceGraph": deepcopy(persistence_graph),
                "runtimeContextPacket": deepcopy(runtime_context_packet or {}),
            }
            while len(self.entries) > max_entries:
                self.entries.popitem(last=False)


SHARED_PROJECTION_RUNTIME_CONTEXT_CACHE = SharedProjectionRuntimeContextCache()


SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE = SharedPortfolioGraphAssemblyCache()


def persistent_graph_assembly_cache_get(
    _inputs: PersistentCacheInputs, cache_key: str
) -> Dict[str, object]:
    if not _inputs.graph_assembly_persistent_cache_enabled():
        return {"status": "disabled"}
    getter = getattr(_inputs.graph_assembly_cache_store, "get", None)
    if not callable(getter):
        return {"status": "unsupported"}
    try:
        result = getter(
            cache_key, _inputs.graph_assembly_persistent_cache_ttl_seconds()
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - exact-cache loss must not block TypeDB reasoning.
        return {"status": "miss", "reason": str(error)[:180]}
    values = dict(result or {}) if isinstance(result, dict) else {}
    if (
        str(values.get("status") or "") == "hit"
        and isinstance(values.get("graph"), PortfolioOntology)
        and isinstance(values.get("persistenceGraph"), PortfolioOntology)
    ):
        return values
    return {
        "status": "miss",
        **(
            {"reason": str(values.get("reason") or "")[:180]}
            if values.get("reason")
            else {}
        ),
    }


def persistent_graph_assembly_cache_put(
    _inputs: PersistentCacheInputs,
    cache_key: str,
    graph: PortfolioOntology,
    persistence_graph: PortfolioOntology,
    runtime_context_packet: Dict[str, object] = None,
) -> Dict[str, object]:
    if not _inputs.graph_assembly_persistent_cache_enabled():
        return {"status": "disabled"}
    saver = getattr(_inputs.graph_assembly_cache_store, "put", None)
    if not callable(saver):
        return {"status": "unsupported"}
    try:
        result = saver(
            cache_key,
            graph,
            persistence_graph,
            _inputs.graph_assembly_persistent_cache_ttl_seconds(),
            _inputs.graph_assembly_persistent_cache_max_entries(),
            _inputs.graph_assembly_persistent_cache_max_payload_bytes(),
            runtime_context_packet,
        )
    except Exception as error:  # noqa: BLE001 - durable cache writes are best effort.
        return {"status": "error", "reason": str(error)[:180]}
    return dict(result or {}) if isinstance(result, dict) else {"status": "invalid"}
