import json
from typing import Dict, Iterable, List

from .market_data import number


VOLATILE_FACT_KEYS = {
    "collectedAt",
    "collected_at",
    "collectionSource",
    "collectionPurpose",
    "createdAt",
    "created_at",
    "firstSeenAt",
    "first_seen_at",
    "lastSeenAt",
    "last_seen_at",
    "observedAt",
    "observed_at",
    "updatedAt",
    "updated_at",
}

MARKET_FACT_FIELDS = (
    "currentPrice",
    "changeRate",
    "volume",
    "volumeRatio",
    "tradingValue",
    "tradeStrength",
    "bidAskImbalance",
    "orderbookImbalance",
    "ma5",
    "ma20",
    "ma60",
    "ma120",
    "ma200",
    "ma20Slope",
    "ma60Slope",
    "ma20Distance",
    "ma60Distance",
    "quoteStatus",
    "dataQuality",
)


def canonical_fact_payload(payload: Dict[str, object], ignore_keys: Iterable[str] = None) -> Dict[str, object]:
    ignored = set(VOLATILE_FACT_KEYS)
    ignored.update(str(key) for key in (ignore_keys or []))
    clean: Dict[str, object] = {}
    for key, value in sorted((payload or {}).items()):
        if str(key) in ignored:
            continue
        if isinstance(value, dict):
            clean[key] = canonical_fact_payload(value, ignored)
        elif isinstance(value, list):
            clean[key] = [
                canonical_fact_payload(item, ignored) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            clean[key] = value
    return clean


def fact_signature(payload: Dict[str, object], ignore_keys: Iterable[str] = None) -> str:
    return json.dumps(canonical_fact_payload(payload, ignore_keys), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _values_equal(previous: object, current: object, numeric_tolerance: float = 0.0001) -> bool:
    previous_number = number(previous)
    current_number = number(current)
    if previous_number is not None or current_number is not None:
        if previous_number is None or current_number is None:
            return False
        return abs(float(previous_number) - float(current_number)) <= numeric_tolerance
    return str(previous or "").strip() == str(current or "").strip()


def changed_fields(previous: Dict[str, object], current: Dict[str, object], fields: Iterable[str]) -> List[str]:
    if not previous:
        return [str(field) for field in fields if current.get(field) not in (None, "")]
    changed = []
    for field in fields:
        key = str(field)
        if key not in current and key not in previous:
            continue
        if not _values_equal(previous.get(key), current.get(key)):
            changed.append(key)
    return changed


def market_fact_change(previous: Dict[str, object], current: Dict[str, object]) -> Dict[str, object]:
    fields = changed_fields(previous or {}, current or {}, MARKET_FACT_FIELDS)
    if not previous:
        reason = "new-market-fact"
    elif fields:
        reason = "market-fact-fields-changed"
    else:
        reason = "market-fact-refresh-only"
    return {
        "changed": bool(fields),
        "reason": reason,
        "fields": fields,
        "signature": fact_signature({key: (current or {}).get(key) for key in MARKET_FACT_FIELDS}),
    }


def research_evidence_fact_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return canonical_fact_payload(payload or {})
