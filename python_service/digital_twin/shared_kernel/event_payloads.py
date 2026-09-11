from typing import Dict, Mapping
from .events import _event_text, _event_text_list


def compact_fact_revisions_for_event(values: object, limit: int = 200) -> Dict[str, str]:
    if not isinstance(values, Mapping):
        return {}
    compact = {}
    for key, value in values.items():
        symbol = _event_text(key, 64).upper()
        revision = _event_text(value, 191)
        if symbol and revision:
            compact[symbol] = revision
        if len(compact) >= max(0, int(limit or 0)):
            break
    return compact
