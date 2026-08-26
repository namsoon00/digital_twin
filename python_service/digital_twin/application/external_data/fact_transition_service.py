from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple


def number(value: object):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def flattened_values(payload: object, prefix: str = "", limit: int = 120) -> Dict[str, object]:
    result: Dict[str, object] = {}

    def visit(value: object, path: str) -> None:
        if len(result) >= limit:
            return
        if isinstance(value, dict):
            for key in sorted(value):
                if str(key) in {"fetchedAt", "collectedAt", "checkedAt", "cryptoLastAttemptAt"}:
                    continue
                visit(value[key], path + "." + str(key) if path else str(key))
            return
        if isinstance(value, list):
            for index, item in enumerate(value[:20]):
                visit(item, path + "[" + str(index) + "]")
            return
        result[path] = value

    visit(payload, prefix)
    return result


def changed_fields(previous: Dict[str, object], current: Dict[str, object], limit: int = 40) -> List[str]:
    before = flattened_values(previous)
    after = flattened_values(current)
    return [key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)][:limit]


def numeric_pairs(previous: Dict[str, object], current: Dict[str, object]) -> Iterable[Tuple[str, float, float]]:
    before = flattened_values(previous)
    after = flattened_values(current)
    for key in sorted(set(before).intersection(after)):
        left = number(before.get(key))
        right = number(after.get(key))
        if left is not None and right is not None:
            yield key, left, right


@dataclass(frozen=True)
class FactTransition:
    changed: bool
    material: bool
    change_type: str = "unchanged"
    changed_fields: List[str] = field(default_factory=list)
    reason: str = ""


class ExternalFactTransitionService:
    """Detect source changes without deciding an investment action."""

    DOCUMENT_DATASETS = {
        "sec.document",
        "sec.company_facts",
        "opendart.document",
        "opendart.company_facts",
        "yfinance.fundamental",
        "yfinance.analyst",
    }

    DISCOVERY_DATASETS = {
        "sec.submissions",
        "opendart.disclosures",
    }

    def assess(
        self,
        dataset_id: str,
        previous_fact: Dict[str, object],
        current_payload: Dict[str, object],
        source_revision: str,
    ) -> FactTransition:
        previous = dict((previous_fact or {}).get("payload") or {})
        previous_revision = str((previous_fact or {}).get("sourceRevision") or "")
        if not previous_fact:
            return FactTransition(True, False, "bootstrap", [], "initial source baseline")
        fields = changed_fields(previous, current_payload)
        changed = bool(fields) or previous_revision != str(source_revision or "")
        if not changed:
            return FactTransition(False, False, "unchanged", [], "same source revision")
        if dataset_id in self.DISCOVERY_DATASETS:
            return FactTransition(
                True,
                False,
                "document-discovery",
                fields,
                "filing metadata changed; verified document collection decides materiality",
            )
        if dataset_id in self.DOCUMENT_DATASETS:
            return FactTransition(True, True, "source-revision", fields, "new or revised source document")
        if dataset_id == "fred.macro":
            return FactTransition(True, True, "macro-observation", fields, "published macro observation changed")
        if dataset_id in {"coingecko.market", "yfinance.price", "alpha.quote"}:
            material_fields = []
            for key, before, after in numeric_pairs(previous, current_payload):
                normalized = key.lower()
                absolute = abs(after - before)
                relative_pct = abs((after - before) / before * 100) if before else absolute
                if any(token in normalized for token in ["change24h", "change7d", "changepercent"]):
                    if absolute >= (1.0 if "7d" in normalized else 0.5):
                        material_fields.append(key)
                elif any(token in normalized for token in ["price", "current_price", "close"]):
                    if relative_pct >= 0.5:
                        material_fields.append(key)
            return FactTransition(
                True,
                bool(material_fields),
                "market-observation",
                material_fields or fields,
                "material raw market movement" if material_fields else "sub-threshold market refresh",
            )
        return FactTransition(True, True, "source-revision", fields, "canonical source fact changed")
