"""Pure TypeDB payload decoding, including retained historical encodings."""

import json
from typing import Dict

from ..manifest.legacy_control_payload import restore_legacy_manifest_indices


def typedb_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def json_object(value: object) -> Dict[str, object]:
    if isinstance(value, dict):
        return restore_legacy_manifest_indices(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return restore_legacy_manifest_indices(decoded) if isinstance(decoded, dict) else {}
