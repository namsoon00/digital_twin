"""Bounded memoization for deterministic JSON-to-JSON transformations only."""
from collections import OrderedDict
from functools import wraps
from hashlib import sha256
import json
import math
from threading import Lock


def _native(value):
    kind = type(value)
    if kind in (str, int, bool, type(None)):
        return True
    if kind is float:
        return math.isfinite(value)
    if kind is list:
        return all(_native(item) for item in value)
    if kind is dict:
        return all(type(key) is str and _native(item) for key, item in value.items())
    return False


def json_memoized(*, max_bytes, max_entries):
    """Keep serialized results only; each hit returns a fresh mutable value.

    Inputs include their insertion order, types, clocks and scope. Unsupported
    values bypass caching, so JSON conversion never changes a public contract.
    No source values are retained as cache keys or treated as durable authority.
    """
    def decorate(function):
        cache, payloads, lock, used = OrderedDict(), {}, Lock(), 0

        def discard(key):
            nonlocal used
            digest = cache.pop(key)
            entry = payloads[digest]
            entry[1] -= 1
            if not entry[1]:
                used -= len(entry[0])
                del payloads[digest]

        def clear():
            nonlocal used
            with lock:
                cache.clear()
                payloads.clear()
                used = 0

        @wraps(function)
        def call(*args, **kwargs):
            nonlocal used
            try:
                eligible = all(_native(value) for value in args) and _native(kwargs)
            except RecursionError:
                eligible = False
            if not eligible:
                return function(*args, **kwargs)
            # Do not sort dictionary keys: a normalizer may preserve order.
            try:
                encoded = json.dumps([args, kwargs], ensure_ascii=False, separators=(",", ":")).encode()
            except (UnicodeError, RecursionError):
                return function(*args, **kwargs)
            if len(encoded) > max_bytes:
                return function(*args, **kwargs)
            key = sha256(encoded).digest()
            del encoded
            with lock:
                digest = cache.get(key)
                payload = payloads[digest][0] if digest is not None else None
                if digest is not None:
                    cache.move_to_end(key)
            if payload is not None:
                return json.loads(payload)
            result = function(*args, **kwargs)
            try:
                eligible = _native(result)
                payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode() if eligible else b""
            except (UnicodeError, RecursionError):
                eligible = False
            if not eligible:
                return result
            if len(payload) > max_bytes:
                return result
            digest = sha256(payload).digest()
            with lock:
                if key in cache:
                    discard(key)
                if digest not in payloads:
                    payloads[digest] = [payload, 0]
                    used += len(payload)
                payloads[digest][1] += 1
                cache[key] = digest
                while used > max_bytes or len(cache) > max_entries:
                    discard(next(iter(cache)))
            return result

        call.cache_clear = clear
        return call
    return decorate
