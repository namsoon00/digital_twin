"""Process-scoped readiness with an explicitly shared cache and monotonic TTL."""

from typing import Tuple

from .ports import SchemaCachePort, SchemaReadinessCache, TypeDBRuntime


def process_base_schema_cache_key(store: SchemaCachePort, schema_fingerprint: str) -> Tuple[str, str, bool, str]:
    return (
        str(store.address or "").strip(),
        str(store.database or "").strip(),
        bool(store.tls_enabled),
        str(schema_fingerprint or ""),
    )


def process_base_schema_is_ready(store: SchemaCachePort, schema_fingerprint: str, *, runtime: TypeDBRuntime, cache: SchemaReadinessCache) -> bool:
    key = store.process_base_schema_cache_key(schema_fingerprint)
    if not key[0] or not key[1] or not key[3]:
        return False
    now = runtime.monotonic()
    with cache.lock:
        ready_at = cache.entries.get(key)
        if ready_at is None:
            return False
        if now - ready_at > cache.ttl_seconds:
            cache.entries.pop(key, None)
            return False
        return True


def mark_process_base_schema_ready(store: SchemaCachePort, schema_fingerprint: str, *, runtime: TypeDBRuntime, cache: SchemaReadinessCache) -> None:
    key = store.process_base_schema_cache_key(schema_fingerprint)
    if not key[0] or not key[1] or not key[3]:
        return
    with cache.lock:
        cache.entries[key] = runtime.monotonic()


def invalidate_process_base_schema_readiness(store: SchemaCachePort, *, cache: SchemaReadinessCache) -> None:
    address = str(store.address or "").strip()
    database = str(store.database or "").strip()
    tls_enabled = bool(store.tls_enabled)
    with cache.lock:
        stale_keys = [
            key
            for key in cache.entries
            if key[:3] == (address, database, tls_enabled)
        ]
        for key in stale_keys:
            cache.entries.pop(key, None)
