from datetime import datetime, timezone
from typing import Dict, Iterable


EXTERNAL_SIGNAL_MAP_FIELDS = {
    "equityQuotes",
    "cryptoMarkets",
    "fxRates",
    "secFilings",
    "dartDisclosures",
    "newsHeadlines",
    "companyOverviews",
    "earningsReports",
    "yfinanceData",
    "researchEvidence",
    "companyKnowledge",
}


def merge_dict(base: Dict[str, object], incoming: Dict[str, object]) -> Dict[str, object]:
    result = dict(base or {})
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def max_timestamp(*values: str) -> str:
    parsed = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        parsed.append((stamp.astimezone(timezone.utc), text))
    return max(parsed, default=(None, ""), key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))[1]


def merge_external_signal_read_models(
    base: Dict[str, object],
    incoming: Dict[str, object],
) -> Dict[str, object]:
    result = dict(base or {})
    for key, value in (incoming or {}).items():
        if key == "statuses" and isinstance(value, list):
            existing = [dict(item) for item in result.get("statuses") or [] if isinstance(item, dict)]
            replacement_sources = {
                (str(item.get("source") or ""), str(item.get("datasetId") or ""))
                for item in value
                if isinstance(item, dict)
            }
            existing = [
                item for item in existing
                if (str(item.get("source") or ""), str(item.get("datasetId") or "")) not in replacement_sources
            ]
            result["statuses"] = existing + [dict(item) for item in value if isinstance(item, dict)]
        elif key in EXTERNAL_SIGNAL_MAP_FIELDS and isinstance(value, dict):
            result[key] = merge_dict(result.get(key) or {}, value)
        elif key == "macro" and isinstance(value, dict):
            result["macro"] = merge_dict(result.get("macro") or {}, value)
        elif key == "fetchedAt":
            result["fetchedAt"] = max_timestamp(result.get("fetchedAt"), value)
        else:
            result[key] = value
    return result


class ExternalSignalsReadModelService:
    """Build the compact legacy read model from independently stored facts."""

    def __init__(self, fact_store):
        self.fact_store = fact_store

    def signals_for_subjects(self, subject_keys: Iterable[str]) -> Dict[str, object]:
        result: Dict[str, object] = {
            "fetchedAt": "",
            "cryptoFetchedAt": "",
            "cryptoLastAttemptAt": "",
            "equityQuotes": {},
            "cryptoMarkets": {},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "earningsReports": {},
            "yfinanceData": {},
            "researchEvidence": {},
            "statuses": [],
            "externalDataPlatform": {
                "enabled": True,
                "factCount": 0,
                "datasets": [],
                "staleDatasets": [],
            },
        }
        datasets = set()
        stale = set()
        rows = self.fact_store.list_current(subject_keys)
        for row in rows:
            fragment = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            for key, value in fragment.items():
                if key == "statuses" and isinstance(value, list):
                    result["statuses"].extend([dict(item) for item in value if isinstance(item, dict)])
                elif key in EXTERNAL_SIGNAL_MAP_FIELDS and isinstance(value, dict):
                    result[key] = merge_dict(result.get(key) or {}, value)
                elif key == "macro" and isinstance(value, dict):
                    result["macro"] = merge_dict(result.get("macro") or {}, value)
                elif key not in {"fetchedAt", "externalDataPlatform"}:
                    result[key] = value
            dataset_id = str(row.get("datasetId") or "")
            if dataset_id:
                datasets.add(dataset_id)
            if str(row.get("freshnessState") or "") == "stale":
                stale.add(dataset_id)
            result["fetchedAt"] = max_timestamp(result.get("fetchedAt"), row.get("fetchedAt"))
            if dataset_id == "coingecko.market":
                result["cryptoFetchedAt"] = str(row.get("fetchedAt") or "")
                result["cryptoLastAttemptAt"] = str(row.get("updatedAt") or row.get("fetchedAt") or "")
        for status in self.fact_store.provider_statuses():
            state = str(status.get("state") or "unknown")
            if state in {"failed", "circuit_open"}:
                result["statuses"].append({
                    "source": str(status.get("providerId") or "External API"),
                    "datasetId": str(status.get("datasetId") or ""),
                    "ok": False,
                    "message": str(status.get("lastError") or state),
                    "state": state,
                    "lastAttemptAt": str(status.get("lastAttemptAt") or ""),
                    "lastSuccessAt": str(status.get("lastSuccessAt") or ""),
                    "circuitOpenUntil": str(status.get("circuitOpenUntil") or ""),
                })
        for dataset_id in sorted(stale):
            result["statuses"].append({
                "source": dataset_id,
                "datasetId": dataset_id,
                "ok": True,
                "deferred": True,
                "dataUsable": False,
                "message": "source fact is stale; collection refresh is pending",
            })
        result["externalDataPlatform"] = {
            "enabled": True,
            "factCount": len(rows),
            "datasets": sorted(datasets),
            "staleDatasets": sorted(stale),
        }
        return result
