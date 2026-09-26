"""Point-in-time historical multiple evidence from immutable source revisions.

The builder joins only observations that were available at the time of the
price snapshot.  It deliberately samples at most one observation per ISO week
so repeated polling cannot manufacture an apparently large evidence set.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Dict, Iterable, List, Mapping


HISTORICAL_MULTIPLE_EVIDENCE_VERSION = "historical-forward-per-evidence-v1"


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(value: object):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp(value: object):
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _source_reference(row: Mapping[str, object]) -> Dict[str, object]:
    dataset_id = _text(row.get("datasetId"))
    revision_id = _text(row.get("revisionId"))
    if not dataset_id or not revision_id:
        return {}
    return {
        key: row.get(key)
        for key in (
            "datasetId", "providerId", "subjectKey", "revisionId", "sourceRevision",
            "payloadHash", "sourceSchemaVersion", "sourceAsOf", "fetchedAt", "availability",
        )
        if row.get(key) not in (None, "")
    }


def _symbol_payload(row: Mapping[str, object], group: str, symbol: str) -> Dict[str, object]:
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
    values = payload.get(group) if isinstance(payload.get(group), Mapping) else {}
    value = values.get(symbol) if isinstance(values.get(symbol), Mapping) else {}
    return dict(value)


def _analyst_snapshot(row: Mapping[str, object], symbol: str) -> Dict[str, object]:
    overview = _symbol_payload(row, "companyOverviews", symbol)
    yfinance = _symbol_payload(row, "yfinanceData", symbol)
    estimates = [dict(item) for item in overview.get("earningsEstimates") or [] if isinstance(item, Mapping)]
    fy1 = next((item for item in estimates if _text(item.get("period") or item.get("horizon")).lower() == "fy1"), {})
    eps = _number(fy1.get("base") or fy1.get("average") or fy1.get("avg"))
    observed_at = _timestamp(row.get("fetchedAt"))
    return {
        "observedAt": observed_at,
        "eps": eps,
        "currency": _text(fy1.get("currency") or overview.get("currency")).upper(),
        "securityLine": _text(yfinance.get("querySymbol") or overview.get("securityLine") or symbol).upper(),
        "epsBasis": _text(fy1.get("epsBasis") or fy1.get("perShareBasis") or "provider-reported-unspecified").lower(),
        "accountingBasis": _text(fy1.get("accountingBasis") or "provider-reported-unspecified").lower(),
        "targetPeriodEnd": _text(fy1.get("targetPeriodEnd")),
        "reference": _source_reference(row),
    }


def _price_snapshot(row: Mapping[str, object], symbol: str) -> Dict[str, object]:
    overview = _symbol_payload(row, "companyOverviews", symbol)
    yfinance = _symbol_payload(row, "yfinanceData", symbol)
    observed_at = _timestamp(row.get("fetchedAt"))
    return {
        "observedAt": observed_at,
        "price": _number(overview.get("currentPrice")),
        "currency": _text(overview.get("currency")).upper(),
        "securityLine": _text(yfinance.get("querySymbol") or overview.get("securityLine") or symbol).upper(),
        "reference": _source_reference(row),
    }


def normalize_current_consensus_contract(
    overview: Mapping[str, object],
    *,
    security_line: object = "",
) -> Dict[str, object]:
    """Fill explicit provider-unknown dimensions without claiming a basis."""

    result = dict(overview or {})
    currency = _text(result.get("currency")).upper()
    line = _text(security_line or result.get("securityLine") or result.get("symbol")).upper()
    normalized = []
    for raw in result.get("earningsEstimates") or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        row.setdefault("epsBasis", row.get("perShareBasis") or "provider-reported-unspecified")
        row.setdefault("perShareBasis", row.get("epsBasis"))
        row.setdefault("accountingBasis", "provider-reported-unspecified")
        if currency:
            row.setdefault("currency", currency)
        if line:
            row.setdefault("securityLine", line)
        normalized.append(row)
    if normalized:
        result["earningsEstimates"] = normalized
    if currency:
        result.setdefault("currency", currency)
    if line:
        result.setdefault("securityLine", line)
    return result


def build_historical_forward_multiple_observations(
    symbol: object,
    fundamental_revisions: Iterable[Mapping[str, object]],
    analyst_revisions: Iterable[Mapping[str, object]],
    *,
    max_analyst_age_days: int = 14,
    max_samples: int = 12,
) -> List[Dict[str, object]]:
    """Build weekly FY1 P/E observations without look-ahead joins."""

    normalized_symbol = _text(symbol).upper()
    analysts = [
        _analyst_snapshot(row, normalized_symbol)
        for row in analyst_revisions or []
        if isinstance(row, Mapping)
    ]
    analysts = [
        item for item in analysts
        if item.get("observedAt") and item.get("eps") and item.get("reference")
    ]
    analysts.sort(key=lambda item: item["observedAt"])
    candidates = []
    for raw in fundamental_revisions or []:
        if not isinstance(raw, Mapping):
            continue
        price = _price_snapshot(raw, normalized_symbol)
        price_at = price.get("observedAt")
        if not price_at or not price.get("price") or not price.get("reference"):
            continue
        known = [item for item in analysts if item["observedAt"] <= price_at]
        if not known:
            continue
        analyst = known[-1]
        age_days = (price_at - analyst["observedAt"]).total_seconds() / 86400.0
        if age_days < 0 or age_days > max(1, int(max_analyst_age_days or 14)):
            continue
        if analyst.get("currency") and price.get("currency") and analyst["currency"] != price["currency"]:
            continue
        if analyst.get("securityLine") != price.get("securityLine"):
            continue
        multiple = float(price["price"]) / float(analyst["eps"])
        if not 0.5 <= multiple <= 150.0:
            continue
        week = price_at.date().isocalendar()[:2]
        references = [price["reference"], analyst["reference"]]
        material = {
            "symbol": normalized_symbol,
            "week": week,
            "priceRevision": price["reference"].get("revisionId"),
            "analystRevision": analyst["reference"].get("revisionId"),
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()[:24]
        candidates.append({
            "observationId": "historical-forward-per:" + digest,
            "contractVersion": HISTORICAL_MULTIPLE_EVIDENCE_VERSION,
            "metric": "per",
            "multipleMetric": "per",
            "value": round(multiple, 6),
            "basis": "historical",
            "period": str(week[0]) + "-W" + str(week[1]).zfill(2),
            "asOf": price_at.isoformat().replace("+00:00", "Z"),
            "priceAsOf": price_at.isoformat().replace("+00:00", "Z"),
            "earningsAsOf": analyst["observedAt"].isoformat().replace("+00:00", "Z"),
            "earningsHorizon": "fy1",
            "accountingBasis": analyst["accountingBasis"],
            "epsBasis": analyst["epsBasis"],
            "currency": price.get("currency") or analyst.get("currency"),
            "issuer": normalized_symbol,
            "securityLine": price["securityLine"],
            "provider": "yfinance",
            "upstreamOrigin": "yfinance.fundamental+yfinance.analyst",
            "source": "immutable-revision-point-in-time-join",
            "sourceType": "external",
            "freshnessState": "historical-valid",
            "comparabilityState": "verified",
            "comparisonFactors": {
                "price": round(float(price["price"]), 6),
                "fy1EPS": round(float(analyst["eps"]), 6),
                "formula": "price / point-in-time FY1 EPS consensus",
                "clockOrder": "analyst-observed-at-or-before-price",
                "analystAgeDays": round(age_days, 4),
                "sampling": "latest-observation-per-iso-week",
            },
            "sourceReferences": references,
        })

    # Keep the latest complete observation in each week.  Revision IDs, not
    # rounded values, own identity so repeated identical provider values do not
    # become conflicting duplicates.
    by_week = {}
    for row in candidates:
        current = by_week.get(row["period"])
        if current is None or row["priceAsOf"] > current["priceAsOf"]:
            by_week[row["period"]] = row
    ordered = sorted(by_week.values(), key=lambda item: item["priceAsOf"])
    return ordered[-max(1, int(max_samples or 12)):]
