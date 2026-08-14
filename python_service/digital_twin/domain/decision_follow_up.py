"""Structured, observable follow-up conditions for a decision episode."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from .market_data import number
from .market_evidence_profiles import observable_follow_up_fields


FOLLOW_UP_CONDITION_VERSION = "decision-follow-up-condition-v1"
FOLLOW_UP_STATUSES = {"pending", "satisfied", "invalidated", "expired", "unobservable"}
FOLLOW_UP_PURPOSES = {"strengthen", "weaken", "invalidate", "switch"}
FOLLOW_UP_OPERATORS = {">", ">=", "<", "<=", "==", "!="}


def _stable_id(*values: object) -> str:
    raw = "|".join(str(value or "") for value in values)
    return "follow-up:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _fact_value(facts: Dict[str, object], field: str):
    if field in facts:
        return facts.get(field)
    trend = facts.get("trendDynamics") if isinstance(facts.get("trendDynamics"), dict) else {}
    if field in trend:
        return trend.get(field)
    temporal = facts.get("temporalDynamics") if isinstance(facts.get("temporalDynamics"), dict) else {}
    return temporal.get(field)


def condition_matches(value: object, operator: str, threshold: object) -> bool:
    if value in (None, "") or threshold in (None, ""):
        return False
    left = number(value)
    right = number(threshold)
    return {
        ">": left > right,
        ">=": left >= right,
        "<": left < right,
        "<=": left <= right,
        "==": left == right,
        "!=": left != right,
    }.get(operator, False)


def normalize_follow_up_conditions(
    raw_conditions: Iterable[Dict[str, object]],
    facts: Dict[str, object],
    symbol: str = "",
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Validate AI-authored conditions against the current market profile.

    Unsupported fields are retained for audit as ``unobservable`` but are not
    scheduled. This prevents a US/ADR decision from waiting forever for KIS
    investor-flow fields that the configured provider cannot supply.
    """

    facts = dict(facts or {})
    profile = facts.get("marketEvidenceProfile") if isinstance(facts.get("marketEvidenceProfile"), dict) else {}
    observable = observable_follow_up_fields(profile)
    tracked: List[Dict[str, object]] = []
    unsupported: List[Dict[str, object]] = []
    seen = set()
    for raw in raw_conditions or []:
        if not isinstance(raw, dict):
            continue
        field = str(raw.get("field") or "").strip()
        operator = str(raw.get("operator") or "").strip()
        purpose = str(raw.get("purpose") or "switch").strip().lower()
        threshold = raw.get("threshold")
        if not field or operator not in FOLLOW_UP_OPERATORS or threshold in (None, ""):
            continue
        if purpose not in FOLLOW_UP_PURPOSES:
            purpose = "switch"
        key = (field, operator, str(threshold), purpose)
        if key in seen:
            continue
        seen.add(key)
        current_value = _fact_value(facts, field)
        is_observable = field in observable
        condition_id = str(raw.get("conditionId") or "").strip() or _stable_id(
            symbol,
            field,
            operator,
            threshold,
            purpose,
        )
        row = {
            "version": FOLLOW_UP_CONDITION_VERSION,
            "conditionId": condition_id,
            "symbol": str(symbol or facts.get("symbol") or "").upper().strip(),
            "field": field,
            "operator": operator,
            "threshold": number(threshold),
            "purpose": purpose,
            "label": " ".join(str(raw.get("label") or "").split())[:240],
            "onSatisfied": " ".join(str(raw.get("onSatisfied") or "").split())[:240],
            "currentValue": current_value,
            "status": "pending" if is_observable else "unobservable",
            "observable": is_observable,
            "observedAt": str(facts.get("updatedAt") or facts.get("sourceAsOf") or ""),
            "expiresAt": str(raw.get("expiresAt") or ""),
        }
        if is_observable and condition_matches(current_value, operator, threshold):
            row["status"] = "satisfied"
        if is_observable:
            tracked.append(row)
        else:
            profile_key = str(profile.get("profileKey") or "")
            row["reason"] = profile_key + " 시장의 현재 공급자가 " + field + " 항목을 제공하지 않습니다."
            unsupported.append(row)
    return tracked[:8], unsupported[:8]


def evaluate_follow_up_conditions(
    conditions: Iterable[Dict[str, object]],
    facts: Dict[str, object],
    observed_at: str = "",
) -> Tuple[List[Dict[str, object]], bool]:
    updated: List[Dict[str, object]] = []
    material = False
    stamp = str(observed_at or facts.get("updatedAt") or facts.get("sourceAsOf") or "")
    now = datetime.now(timezone.utc)
    for raw in conditions or []:
        row = dict(raw or {})
        previous = str(row.get("status") or "pending")
        if previous not in {"pending"}:
            updated.append(row)
            continue
        expires_at = str(row.get("expiresAt") or "").replace("Z", "+00:00")
        try:
            parsed_expiry = datetime.fromisoformat(expires_at) if expires_at else None
            if parsed_expiry and parsed_expiry.tzinfo is None:
                parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
            expired = bool(parsed_expiry and parsed_expiry.astimezone(timezone.utc) <= now)
        except ValueError:
            expired = False
        if expired:
            row["status"] = "expired"
        else:
            value = _fact_value(facts, str(row.get("field") or ""))
            row["currentValue"] = value
            row["observedAt"] = stamp
            if condition_matches(value, str(row.get("operator") or ""), row.get("threshold")):
                row["status"] = "invalidated" if str(row.get("purpose") or "") == "invalidate" else "satisfied"
        if row.get("status") != previous:
            row["transitionAt"] = stamp
            material = True
        updated.append(row)
    return updated, material


def follow_up_fingerprint(conditions: Iterable[Dict[str, object]]) -> str:
    canonical = json.dumps(list(conditions or []), ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
