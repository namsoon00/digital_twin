"""Account-independent capital-flow observations and read-side metrics."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .investor_flow_psychology import (
    INVESTOR_PARTY_FIELDS,
    investor_flow_contract,
    investor_flow_coverage,
    investor_flow_values_reliable,
)


CAPITAL_FLOW_CONTRACT_VERSION = "capital-flow-observation-v1"
CAPITAL_FLOW_FEATURE_VERSION = "capital-flow-features-v1"
CAPITAL_FLOW_WINDOWS = (1, 3, 5, 20)

MEASUREMENT_RANK = {
    "daily-final": 4,
    "intraday-estimate": 3,
    "delayed-reference": 2,
    "unspecified": 1,
}

PROVIDER_RANK = {
    "kis": 4,
    "korea-investment-securities": 4,
    "legacy-market-time-series": 1,
}

PARTY_STORAGE_FIELDS = {
    "foreign": ("foreign_net_volume", "foreign_net_amount", "foreign_buy_volume", "foreign_sell_volume"),
    "institution": (
        "institution_net_volume",
        "institution_net_amount",
        "institution_buy_volume",
        "institution_sell_volume",
    ),
    "individual": ("individual_net_volume", "individual_net_amount", "individual_buy_volume", "individual_sell_volume"),
}


def clean_text(value: object) -> str:
    return str(value or "").strip()


def finite_number(value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def boolean_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return clean_text(value).lower() in {"1", "true", "yes", "y", "on"}


def normalized_timestamp(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def trading_date_from(value: object, fallback: object = "") -> str:
    for candidate in (value, fallback):
        text = clean_text(candidate)
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
                return text[:10]
            continue
        return parsed.date().isoformat()
    return ""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def observation_id_for(payload: Mapping[str, object]) -> str:
    identity = {
        "subjectKind": clean_text(payload.get("subject_kind") or payload.get("subjectKind")),
        "subjectId": clean_text(payload.get("subject_id") or payload.get("subjectId")).upper(),
        "tradingDate": clean_text(payload.get("trading_date") or payload.get("tradingDate")),
        "provider": clean_text(payload.get("provider")),
        "measurementType": clean_text(payload.get("measurement_type") or payload.get("measurementType")),
        "sourceAsOf": normalized_timestamp(payload.get("source_as_of") or payload.get("sourceAsOf")),
    }
    return "capital-flow:" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:32]


def coverage_payload(raw: object) -> Dict[str, object]:
    if isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        try:
            parsed = json.loads(clean_text(raw) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        payload = dict(parsed) if isinstance(parsed, Mapping) else {}
    investor = payload.get("investor") if isinstance(payload.get("investor"), Mapping) else payload
    return dict(investor) if isinstance(investor, Mapping) else {}


def observed_fields_from_coverage(raw: object) -> Tuple[str, ...]:
    coverage = coverage_payload(raw)
    return tuple(sorted({
        clean_text(field)
        for field in (coverage.get("observedFields") or coverage.get("fields") or [])
        if clean_text(field)
    }))


def _observed_position_value(position, observed: Sequence[str], public_field: str, attr_field: str) -> Optional[float]:
    if public_field not in observed:
        return None
    value = finite_number(getattr(position, attr_field, None))
    return 0.0 if value is None else value


@dataclass(frozen=True)
class CapitalFlowObservation:
    observation_id: str
    subject_kind: str
    subject_id: str
    market: str
    currency: str
    sector: str
    trading_date: str
    observed_at: str
    source_as_of: str
    received_at: str
    provider: str
    measurement_type: str
    status: str
    freshness_status: str
    judgement_eligible: bool
    observed_fields_json: str
    coverage_json: str
    current_price: Optional[float] = None
    market_volume: Optional[float] = None
    trading_value: Optional[float] = None
    foreign_net_volume: Optional[float] = None
    foreign_net_amount: Optional[float] = None
    foreign_buy_volume: Optional[float] = None
    foreign_sell_volume: Optional[float] = None
    institution_net_volume: Optional[float] = None
    institution_net_amount: Optional[float] = None
    institution_buy_volume: Optional[float] = None
    institution_sell_volume: Optional[float] = None
    individual_net_volume: Optional[float] = None
    individual_net_amount: Optional[float] = None
    individual_buy_volume: Optional[float] = None
    individual_sell_volume: Optional[float] = None
    data_quality: str = "actual"
    contract_version: str = CAPITAL_FLOW_CONTRACT_VERSION

    @classmethod
    def from_position(cls, position, observed_at: object, provider: object = "") -> Optional["CapitalFlowObservation"]:
        coverage = investor_flow_coverage(position)
        contract = investor_flow_contract(position)
        observed = tuple(contract.get("observedFields") or ())
        status = clean_text(coverage.get("status")).lower() or "missing"
        if not observed or not investor_flow_values_reliable(position):
            return None
        source_as_of = clean_text(coverage.get("sourceAsOf") or getattr(position, "source_as_of", "") or observed_at)
        payload = {
            "subject_kind": "security",
            "subject_id": clean_text(getattr(position, "symbol", "")).upper(),
            "trading_date": trading_date_from(source_as_of, observed_at),
            "provider": clean_text(
                coverage.get("provider")
                or coverage.get("source")
                or ("KIS" if coverage.get("measurementType") else "")
                or provider
                or getattr(position, "quote_source", "")
            ),
            "measurement_type": clean_text(coverage.get("measurementType")) or "unspecified",
            "source_as_of": normalized_timestamp(source_as_of),
        }
        if not payload["subject_id"] or not payload["trading_date"]:
            return None
        row = {
            **payload,
            "observation_id": observation_id_for(payload),
            "market": clean_text(getattr(position, "market", "")),
            "currency": clean_text(getattr(position, "currency", "")),
            "sector": clean_text(getattr(position, "sector", "")) or "기타",
            "observed_at": normalized_timestamp(observed_at or getattr(position, "updated_at", "") or source_as_of),
            "received_at": normalized_timestamp(coverage.get("fetchedAt") or observed_at or source_as_of),
            "status": status,
            "freshness_status": clean_text(coverage.get("freshnessStatus")),
            "judgement_eligible": boolean_value(coverage.get("judgementEvidenceUsable", True)),
            "observed_fields_json": canonical_json(list(observed)),
            "coverage_json": canonical_json(coverage),
            "current_price": finite_number(getattr(position, "current_price", None)),
            "market_volume": finite_number(getattr(position, "volume", None)),
            "trading_value": finite_number(getattr(position, "trading_value", None)),
            "data_quality": clean_text(getattr(position, "data_quality", "")) or "actual",
        }
        for party, fields in INVESTOR_PARTY_FIELDS.items():
            storage_fields = PARTY_STORAGE_FIELDS[party]
            row[storage_fields[0]] = _observed_position_value(position, observed, fields["net"], fields["net_attr"])
            row[storage_fields[1]] = _observed_position_value(position, observed, fields["amount"], fields["amount_attr"])
            row[storage_fields[2]] = _observed_position_value(position, observed, fields["buy"], fields["buy_attr"])
            row[storage_fields[3]] = _observed_position_value(position, observed, fields["sell"], fields["sell_attr"])
        return cls(**row)

    @classmethod
    def from_legacy_row(cls, raw: Mapping[str, object]) -> Optional["CapitalFlowObservation"]:
        row = dict(raw or {})
        coverage = coverage_payload(row.get("investor_coverage_json") or row.get("marketSignalCoverage"))
        reported_observed = observed_fields_from_coverage(coverage)
        legacy_columns = {
            "foreignNetVolume": "foreign_net_volume",
            "institutionNetVolume": "institution_net_volume",
            "individualNetVolume": "individual_net_volume",
        }
        observed = tuple(
            field for field, column in legacy_columns.items()
            if field in reported_observed and row.get(column) not in (None, "")
        )
        dropped_fields = sorted(set(reported_observed) - set(observed))
        coverage = dict(coverage)
        coverage["observedFields"] = list(observed)
        coverage["fields"] = list(observed)
        if dropped_fields:
            coverage["legacyUnpersistedFields"] = dropped_fields
        status = clean_text(coverage.get("status")).lower()
        if status != "available" or not observed or coverage.get("judgementEvidenceUsable") is False:
            return None
        subject_id = clean_text(row.get("symbol")).upper()
        source_as_of = clean_text(coverage.get("sourceAsOf") or row.get("source_as_of") or row.get("observed_at"))
        payload = {
            "subject_kind": "security",
            "subject_id": subject_id,
            "trading_date": trading_date_from(source_as_of, row.get("bucket_at")),
            "provider": clean_text(
                coverage.get("provider")
                or coverage.get("source")
                or ("KIS" if coverage.get("measurementType") else "")
                or row.get("provider")
            ) or "legacy-market-time-series",
            "measurement_type": clean_text(coverage.get("measurementType")) or "unspecified",
            "source_as_of": source_as_of,
        }
        if not subject_id or not payload["trading_date"]:
            return None

        def observed_value(public_field: str, column: str) -> Optional[float]:
            if public_field not in observed:
                return None
            value = finite_number(row.get(column))
            return 0.0 if value is None else value

        return cls(
            observation_id=observation_id_for(payload),
            subject_kind="security",
            subject_id=subject_id,
            market=clean_text(row.get("market")),
            currency=clean_text(row.get("currency")),
            sector=clean_text(row.get("sector")) or "기타",
            trading_date=payload["trading_date"],
            observed_at=normalized_timestamp(row.get("observed_at") or row.get("bucket_at")),
            source_as_of=normalized_timestamp(source_as_of),
            received_at=normalized_timestamp(row.get("observed_at") or source_as_of),
            provider=payload["provider"],
            measurement_type=payload["measurement_type"],
            status=status,
            freshness_status=clean_text(coverage.get("freshnessStatus")),
            judgement_eligible=True,
            observed_fields_json=canonical_json(list(observed)),
            coverage_json=canonical_json(coverage),
            current_price=finite_number(row.get("current_price")),
            market_volume=finite_number(row.get("volume")),
            trading_value=finite_number(row.get("trading_value")),
            foreign_net_volume=observed_value("foreignNetVolume", "foreign_net_volume"),
            institution_net_volume=observed_value("institutionNetVolume", "institution_net_volume"),
            individual_net_volume=observed_value("individualNetVolume", "individual_net_volume"),
            data_quality=clean_text(row.get("data_quality")) or "actual",
        )

    def valid(self) -> bool:
        return bool(
            self.observation_id
            and self.subject_id
            and self.trading_date
            and self.status == "available"
            and self.judgement_eligible
            and self.observed_fields()
        )

    def observed_fields(self) -> Tuple[str, ...]:
        try:
            parsed = json.loads(self.observed_fields_json or "[]")
        except json.JSONDecodeError:
            parsed = []
        return tuple(clean_text(item) for item in parsed if clean_text(item))

    def to_row(self) -> Dict[str, object]:
        return asdict(self)

    def to_payload(self) -> Dict[str, object]:
        row = self.to_row()
        payload = {
            "observationId": row.pop("observation_id"),
            "subjectKind": row.pop("subject_kind"),
            "subjectId": row.pop("subject_id"),
            "tradingDate": row.pop("trading_date"),
            "observedAt": row.pop("observed_at"),
            "sourceAsOf": row.pop("source_as_of"),
            "receivedAt": row.pop("received_at"),
            "measurementType": row.pop("measurement_type"),
            "freshnessStatus": row.pop("freshness_status"),
            "judgementEligible": row.pop("judgement_eligible"),
            "observedFields": list(self.observed_fields()),
            "coverage": coverage_payload(row.pop("coverage_json")),
            "contractVersion": row.pop("contract_version"),
        }
        row.pop("observed_fields_json", None)
        camel = {
            "current_price": "currentPrice",
            "market_volume": "marketVolume",
            "trading_value": "tradingValue",
            "foreign_net_volume": "foreignNetVolume",
            "foreign_net_amount": "foreignNetAmount",
            "foreign_buy_volume": "foreignBuyVolume",
            "foreign_sell_volume": "foreignSellVolume",
            "institution_net_volume": "institutionNetVolume",
            "institution_net_amount": "institutionNetAmount",
            "institution_buy_volume": "institutionBuyVolume",
            "institution_sell_volume": "institutionSellVolume",
            "individual_net_volume": "individualNetVolume",
            "individual_net_amount": "individualNetAmount",
            "individual_buy_volume": "individualBuyVolume",
            "individual_sell_volume": "individualSellVolume",
            "data_quality": "dataQuality",
        }
        for key, value in row.items():
            if value is not None:
                payload[camel.get(key, key)] = value
        return payload


def observation_from_row(raw: Mapping[str, object]) -> Optional[CapitalFlowObservation]:
    row = dict(raw or {})
    aliases = {
        "observationId": "observation_id",
        "subjectKind": "subject_kind",
        "subjectId": "subject_id",
        "tradingDate": "trading_date",
        "observedAt": "observed_at",
        "sourceAsOf": "source_as_of",
        "receivedAt": "received_at",
        "measurementType": "measurement_type",
        "freshnessStatus": "freshness_status",
        "judgementEligible": "judgement_eligible",
        "currentPrice": "current_price",
        "marketVolume": "market_volume",
        "tradingValue": "trading_value",
        "foreignNetVolume": "foreign_net_volume",
        "foreignNetAmount": "foreign_net_amount",
        "foreignBuyVolume": "foreign_buy_volume",
        "foreignSellVolume": "foreign_sell_volume",
        "institutionNetVolume": "institution_net_volume",
        "institutionNetAmount": "institution_net_amount",
        "institutionBuyVolume": "institution_buy_volume",
        "institutionSellVolume": "institution_sell_volume",
        "individualNetVolume": "individual_net_volume",
        "individualNetAmount": "individual_net_amount",
        "individualBuyVolume": "individual_buy_volume",
        "individualSellVolume": "individual_sell_volume",
        "dataQuality": "data_quality",
        "contractVersion": "contract_version",
    }
    for source, target in aliases.items():
        if target not in row and source in row:
            row[target] = row.get(source)
    if "observed_fields_json" not in row:
        row["observed_fields_json"] = canonical_json(row.get("observedFields") or [])
    if "coverage_json" not in row:
        row["coverage_json"] = canonical_json(row.get("coverage") or {})
    allowed = set(CapitalFlowObservation.__dataclass_fields__)
    values = {key: value for key, value in row.items() if key in allowed}
    if not values.get("observation_id"):
        values["observation_id"] = observation_id_for(values)
    for key in allowed:
        if key.endswith(("_volume", "_amount")) or key in {"current_price", "market_volume", "trading_value"}:
            values[key] = finite_number(values.get(key))
    values["judgement_eligible"] = boolean_value(values.get("judgement_eligible"))
    try:
        return CapitalFlowObservation(**values)
    except TypeError:
        return None


def canonical_observations(rows: Iterable[object], as_of: object = "") -> List[CapitalFlowObservation]:
    cutoff = normalized_timestamp(as_of)
    by_subject_date: Dict[Tuple[str, str, str], CapitalFlowObservation] = {}
    for raw in rows or []:
        observation = raw if isinstance(raw, CapitalFlowObservation) else observation_from_row(raw) if isinstance(raw, Mapping) else None
        if not observation or not observation.valid():
            continue
        if cutoff and observation.observed_at and observation.observed_at > cutoff:
            continue
        key = (observation.subject_kind, observation.subject_id, observation.trading_date)
        previous = by_subject_date.get(key)
        rank = (
            MEASUREMENT_RANK.get(observation.measurement_type, 0),
            PROVIDER_RANK.get(observation.provider.lower(), 0),
            observation.source_as_of,
            observation.observed_at,
            observation.observation_id,
        )
        previous_rank = (
            MEASUREMENT_RANK.get(previous.measurement_type, 0),
            PROVIDER_RANK.get(previous.provider.lower(), 0),
            previous.source_as_of,
            previous.observed_at,
            previous.observation_id,
        ) if previous else (-1, -1, "", "", "")
        if not previous or rank > previous_rank:
            by_subject_date[key] = observation
    return sorted(by_subject_date.values(), key=lambda item: (item.subject_id, item.trading_date, item.observed_at))


def _sum_present(rows: Sequence[CapitalFlowObservation], field: str) -> Optional[float]:
    values = [getattr(item, field) for item in rows if getattr(item, field) is not None]
    return round(sum(values), 4) if values else None


def _party_window(rows: Sequence[CapitalFlowObservation], party: str) -> Dict[str, object]:
    net_volume_field, net_amount_field, _buy, _sell = PARTY_STORAGE_FIELDS[party]
    volumes = [getattr(item, net_volume_field) for item in rows if getattr(item, net_volume_field) is not None]
    amounts = [getattr(item, net_amount_field) for item in rows if getattr(item, net_amount_field) is not None]
    positive = len([value for value in amounts or volumes if value > 0])
    negative = len([value for value in amounts or volumes if value < 0])
    observed_count = len(amounts or volumes)
    return {
        "netVolume": round(sum(volumes), 4) if volumes else None,
        "netAmount": round(sum(amounts), 4) if amounts else None,
        "observedCount": observed_count,
        "volumeObservedCount": len(volumes),
        "amountObservedCount": len(amounts),
        "positiveSessionRatio": round(positive / observed_count, 4) if observed_count else None,
        "negativeSessionRatio": round(negative / observed_count, 4) if observed_count else None,
    }


def _direction(value: Optional[float], ratio: Optional[float] = None) -> str:
    if value is None:
        return "unavailable"
    if ratio is not None and abs(ratio) < 0.05:
        return "neutral"
    return "inflow" if value > 0 else "outflow" if value < 0 else "neutral"


def subject_flow_summary(rows: Sequence[CapitalFlowObservation], window_days: int = 5) -> Dict[str, object]:
    canonical = canonical_observations(rows)
    if not canonical:
        return {}
    selected = canonical[-max(1, int(window_days or 5)):]
    foreign = _party_window(selected, "foreign")
    institution = _party_window(selected, "institution")
    individual = _party_window(selected, "individual")
    complete_amount_count = len([
        item for item in selected
        if item.foreign_net_amount is not None and item.institution_net_amount is not None
    ])
    complete_volume_count = len([
        item for item in selected
        if item.foreign_net_volume is not None and item.institution_net_volume is not None
    ])
    smart_amount = None
    if complete_amount_count == len(selected):
        smart_amount = round(foreign["netAmount"] + institution["netAmount"], 4)
    smart_volume = None
    if complete_volume_count == len(selected):
        smart_volume = round(foreign["netVolume"] + institution["netVolume"], 4)
    trading_value = _sum_present(selected, "trading_value")
    normalized_pct = round(smart_amount / trading_value * 100, 6) if smart_amount is not None and trading_value and trading_value > 0 else None
    smart_daily = [
        (item.foreign_net_amount + item.institution_net_amount)
        if item.foreign_net_amount is not None and item.institution_net_amount is not None
        else (item.foreign_net_volume + item.institution_net_volume)
        if item.foreign_net_volume is not None and item.institution_net_volume is not None
        else None
        for item in selected
    ]
    smart_daily = [value for value in smart_daily if value is not None]
    usable_observation_count = complete_amount_count if complete_amount_count else complete_volume_count
    midpoint = max(1, len(smart_daily) // 2) if smart_daily else 0
    prior_mean = sum(smart_daily[:midpoint]) / len(smart_daily[:midpoint]) if midpoint else 0.0
    recent_values = smart_daily[midpoint:]
    recent_mean = sum(recent_values) / len(recent_values) if recent_values else prior_mean
    persistence = (
        max(len([value for value in smart_daily if value > 0]), len([value for value in smart_daily if value < 0]))
        / len(smart_daily)
        if smart_daily else None
    )
    latest = selected[-1]
    return {
        "subjectId": latest.subject_id,
        "subjectKind": latest.subject_kind,
        "market": latest.market,
        "currency": latest.currency,
        "sector": latest.sector,
        "windowDays": int(window_days or 5),
        "fromTradingDate": selected[0].trading_date,
        "throughTradingDate": latest.trading_date,
        "sourceAsOf": latest.source_as_of,
        "observedAt": latest.observed_at,
        "receivedAt": latest.received_at,
        "measurementType": latest.measurement_type,
        "foreign": foreign,
        "institution": institution,
        "individual": individual,
        "smartMoneyNetAmount": smart_amount,
        "smartMoneyNetVolume": smart_volume,
        "normalizedFlowPct": normalized_pct,
        "direction": _direction(smart_amount if smart_amount is not None else smart_volume, normalized_pct),
        "persistenceRatio": round(persistence, 4) if persistence is not None else None,
        "acceleration": round(recent_mean - prior_mean, 4) if smart_daily else None,
        "observationCount": len(selected),
        "usableObservationCount": usable_observation_count,
        "requiredObservationCount": int(window_days or 5),
        "coverageRatio": round(usable_observation_count / max(1, int(window_days or 5)), 4),
        "dataState": "sufficient" if usable_observation_count >= max(1, min(3, int(window_days or 5))) else "partial",
        "featureVersion": CAPITAL_FLOW_FEATURE_VERSION,
    }


def capital_flow_summary(rows: Iterable[object], window_days: int = 5) -> Dict[str, object]:
    source_rows = list(rows or [])
    canonical = canonical_observations(source_rows)
    by_subject: Dict[str, List[CapitalFlowObservation]] = {}
    for item in canonical:
        by_subject.setdefault(item.subject_id, []).append(item)
    subjects = [subject_flow_summary(items, window_days) for items in by_subject.values()]
    subjects = [item for item in subjects if item]

    def aggregate(key_name: str) -> List[Dict[str, object]]:
        grouped: Dict[str, List[Dict[str, object]]] = {}
        for item in subjects:
            key = clean_text(item.get(key_name)) or "기타"
            grouped.setdefault(key, []).append(item)
        result = []
        for key, items in grouped.items():
            amount_values = [finite_number(item.get("smartMoneyNetAmount")) for item in items]
            amount_values = [value for value in amount_values if value is not None]
            volume_values = [finite_number(item.get("smartMoneyNetVolume")) for item in items]
            volume_values = [value for value in volume_values if value is not None]
            normalized_values = [finite_number(item.get("normalizedFlowPct")) for item in items]
            normalized_values = [value for value in normalized_values if value is not None]
            value = sum(amount_values) if amount_values else sum(volume_values) if volume_values else None
            normalized = sum(normalized_values) / len(normalized_values) if normalized_values else None
            result.append({
                "key": key,
                "label": key,
                "smartMoneyNetAmount": round(sum(amount_values), 4) if amount_values else None,
                "smartMoneyNetVolume": round(sum(volume_values), 4) if volume_values else None,
                "normalizedFlowPct": round(normalized, 6) if normalized is not None else None,
                "direction": _direction(value, normalized),
                "subjectCount": len(items),
                "sufficientCount": len([item for item in items if item.get("dataState") == "sufficient"]),
            })
        return sorted(result, key=lambda item: abs(finite_number(item.get("smartMoneyNetAmount")) or finite_number(item.get("smartMoneyNetVolume")) or 0), reverse=True)

    quality = {
        "rawObservationCount": len(source_rows),
        "canonicalObservationCount": len(canonical),
        "subjectCount": len(subjects),
        "sufficientSubjectCount": len([item for item in subjects if item.get("dataState") == "sufficient"]),
        "missingConvertedToZeroCount": 0,
    }
    return {
        "contract": "capital-flow-summary-v1",
        "featureVersion": CAPITAL_FLOW_FEATURE_VERSION,
        "windowDays": int(window_days or 5),
        "asOf": max((item.observed_at for item in canonical), default=""),
        "sourceAsOf": max((item.source_as_of for item in canonical), default=""),
        "status": "ready" if subjects else "empty",
        "markets": aggregate("market"),
        "sectors": aggregate("sector"),
        "subjects": sorted(subjects, key=lambda item: abs(finite_number(item.get("normalizedFlowPct")) or 0), reverse=True),
        "quality": quality,
    }


def capital_flow_overlay_payload(observation: CapitalFlowObservation) -> Dict[str, object]:
    payload = observation.to_payload()
    coverage = dict(payload.get("coverage") or {})
    payload["marketSignalCoverage"] = {"investor": coverage}
    payload["marketSessionDate"] = observation.trading_date
    payload["capitalFlowObservationId"] = observation.observation_id
    payload["capitalFlowMeasurementType"] = observation.measurement_type
    return payload


def merge_capital_flow_rows(
    market_rows: Iterable[Mapping[str, object]],
    flow_rows: Iterable[object],
) -> List[Dict[str, object]]:
    """Overlay point-in-time public flow facts onto price rows by trading date."""

    canonical = canonical_observations(flow_rows)
    by_date = {item.trading_date: item for item in canonical}
    merged = []
    flow_fields = {
        "foreignNetVolume", "foreignNetAmount", "foreignBuyVolume", "foreignSellVolume",
        "institutionNetVolume", "institutionNetAmount", "institutionBuyVolume", "institutionSellVolume",
        "individualNetVolume", "individualNetAmount", "individualBuyVolume", "individualSellVolume",
    }
    for raw in market_rows or []:
        row = dict(raw or {})
        trading_date = clean_text(
            row.get("marketSessionDate")
            or row.get("tradingDate")
            or row.get("bucketAt")
            or row.get("generatedAt")
        )[:10]
        observation = by_date.get(trading_date)
        if observation:
            payload = capital_flow_overlay_payload(observation)
            for field in flow_fields:
                if field in payload:
                    row[field] = payload[field]
            row["marketSignalCoverage"] = payload["marketSignalCoverage"]
            row["capitalFlowObservationId"] = observation.observation_id
            row["capitalFlowMeasurementType"] = observation.measurement_type
            row["capitalFlowSourceAsOf"] = observation.source_as_of
            row["capitalFlowObservedAt"] = observation.observed_at
            row["capitalFlowProvider"] = observation.provider
            row["capitalFlowDataQuality"] = observation.data_quality
        merged.append(row)
    return merged
