"""Generation-to-generation lifecycle audit for TypeDB-derived hypotheses.

The lifecycle is deliberately not an investment decision engine. TypeDB native
rules continue to decide which causal paths are active. This module only keeps
an auditable history of the already materialized paths, their evidence deltas,
and their declared RuleBox validity policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .ontology_rulebox_contracts import HypothesisLifecyclePolicy


HYPOTHESIS_LIFECYCLE_VERSION = "typedb-hypothesis-lifecycle-v1"
HYPOTHESIS_LIFECYCLE_STATES = (
    "observed",
    "maintained",
    "strengthened",
    "weakened",
    "invalidated",
    "expired",
)
TERMINAL_HYPOTHESIS_LIFECYCLE_STATES = {"invalidated", "expired"}
HYPOTHESIS_LIFECYCLE_STATE_LABELS = {
    "observed": "처음 관찰됨",
    "maintained": "근거 유지",
    "strengthened": "근거 강화",
    "weakened": "근거 약화",
    "invalidated": "가설 무효화",
    "expired": "근거 유효기간 만료",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _upper(value: object) -> str:
    return _text(value).upper()


def _as_values(values: object) -> Iterable[object]:
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        return values
    return [values]


def _unique(values: Iterable[object], limit: int = 128) -> List[str]:
    result: List[str] = []
    for value in _as_values(values):
        text = _text(value)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _camelize_key(value: str) -> str:
    parts = str(value or "").split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camelize(value: object):
    if isinstance(value, dict):
        return {_camelize_key(str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_timestamp(value: object, fallback: str = "") -> str:
    text = _text(value)
    if not text:
        return fallback
    parsed = parse_timestamp(text)
    if not parsed:
        return text
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stable_id(prefix: str, *parts: object) -> str:
    seed = "|".join(_text(part) for part in parts)
    digest = hashlib.sha256((prefix + "|" + seed).encode("utf-8")).hexdigest()[:24]
    return prefix + ":" + digest


def stable_fingerprint(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HypothesisLifecycleSnapshot:
    """One active TypeDB hypothesis path at a concrete inference generation."""

    lifecycle_key: str
    lifecycle_id: str
    scope: str
    account_id: str = ""
    portfolio_world_id: str = ""
    market_world_id: str = ""
    market_id: str = ""
    symbol: str = ""
    family_id: str = ""
    hypothesis_ids: List[str] = field(default_factory=list)
    source_rule_ids: List[str] = field(default_factory=list)
    supporting_evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    causal_path_ids: List[str] = field(default_factory=list)
    formation_condition_ids: List[str] = field(default_factory=list)
    matched_condition_ids: List[str] = field(default_factory=list)
    policy: Dict[str, object] = field(default_factory=dict)
    observation_profiles: Dict[str, Dict[str, object]] = field(default_factory=dict)
    trace_freshness_statuses: List[str] = field(default_factory=list)
    inference_generation_id: str = ""
    inference_generation_at: str = ""
    observed_at: str = ""
    semantic_fingerprint: str = ""

    def to_dict(self) -> Dict[str, object]:
        return _camelize(asdict(self))

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        payload = dict(payload or {})
        policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
        profiles = payload.get("observationProfiles") or payload.get("observation_profiles") or {}
        return cls(
            lifecycle_key=_text(payload.get("lifecycleKey") or payload.get("lifecycle_key")),
            lifecycle_id=_text(payload.get("lifecycleId") or payload.get("lifecycle_id")),
            scope=_text(payload.get("scope")),
            account_id=_text(payload.get("accountId") or payload.get("account_id")),
            portfolio_world_id=_text(payload.get("portfolioWorldId") or payload.get("portfolio_world_id")),
            market_world_id=_text(payload.get("marketWorldId") or payload.get("market_world_id")),
            market_id=_text(payload.get("marketId") or payload.get("market_id")),
            symbol=_upper(payload.get("symbol")),
            family_id=_text(payload.get("familyId") or payload.get("family_id")),
            hypothesis_ids=_unique(payload.get("hypothesisIds") or payload.get("hypothesis_ids") or []),
            source_rule_ids=_unique(payload.get("sourceRuleIds") or payload.get("source_rule_ids") or []),
            supporting_evidence_ids=_unique(payload.get("supportingEvidenceIds") or payload.get("supporting_evidence_ids") or []),
            counter_evidence_ids=_unique(payload.get("counterEvidenceIds") or payload.get("counter_evidence_ids") or []),
            causal_path_ids=_unique(payload.get("causalPathIds") or payload.get("causal_path_ids") or []),
            formation_condition_ids=_unique(payload.get("formationConditionIds") or payload.get("formation_condition_ids") or []),
            matched_condition_ids=_unique(payload.get("matchedConditionIds") or payload.get("matched_condition_ids") or []),
            policy=HypothesisLifecyclePolicy.from_dict(policy).to_dict(),
            observation_profiles={
                _text(key): dict(value)
                for key, value in dict(profiles or {}).items()
                if _text(key) and isinstance(value, dict)
            },
            trace_freshness_statuses=_unique(payload.get("traceFreshnessStatuses") or payload.get("trace_freshness_statuses") or []),
            inference_generation_id=_text(payload.get("inferenceGenerationId") or payload.get("inference_generation_id")),
            inference_generation_at=canonical_timestamp(payload.get("inferenceGenerationAt") or payload.get("inference_generation_at")),
            observed_at=canonical_timestamp(payload.get("observedAt") or payload.get("observed_at")),
            semantic_fingerprint=_text(payload.get("semanticFingerprint") or payload.get("semantic_fingerprint")),
        )


@dataclass(frozen=True)
class HypothesisLifecycleRecord:
    lifecycle_key: str
    lifecycle_id: str
    scope: str
    state: str
    account_id: str = ""
    portfolio_world_id: str = ""
    market_world_id: str = ""
    market_id: str = ""
    symbol: str = ""
    family_id: str = ""
    first_observed_at: str = ""
    last_observed_at: str = ""
    last_transition_at: str = ""
    inference_generation_id: str = ""
    inference_generation_at: str = ""
    previous_generation_id: str = ""
    semantic_fingerprint: str = ""
    transition_reason: str = ""
    material_change: bool = False
    evidence_delta: Dict[str, List[str]] = field(default_factory=dict)
    snapshot: Dict[str, object] = field(default_factory=dict)
    version: str = HYPOTHESIS_LIFECYCLE_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = _camelize(asdict(self))
        payload["stateLabel"] = HYPOTHESIS_LIFECYCLE_STATE_LABELS.get(self.state, self.state)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        payload = dict(payload or {})
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        delta = payload.get("evidenceDelta") or payload.get("evidence_delta") or {}
        return cls(
            lifecycle_key=_text(payload.get("lifecycleKey") or payload.get("lifecycle_key")),
            lifecycle_id=_text(payload.get("lifecycleId") or payload.get("lifecycle_id")),
            scope=_text(payload.get("scope")),
            state=_text(payload.get("state")) or "observed",
            account_id=_text(payload.get("accountId") or payload.get("account_id")),
            portfolio_world_id=_text(payload.get("portfolioWorldId") or payload.get("portfolio_world_id")),
            market_world_id=_text(payload.get("marketWorldId") or payload.get("market_world_id")),
            market_id=_text(payload.get("marketId") or payload.get("market_id")),
            symbol=_upper(payload.get("symbol")),
            family_id=_text(payload.get("familyId") or payload.get("family_id")),
            first_observed_at=canonical_timestamp(payload.get("firstObservedAt") or payload.get("first_observed_at")),
            last_observed_at=canonical_timestamp(payload.get("lastObservedAt") or payload.get("last_observed_at")),
            last_transition_at=canonical_timestamp(payload.get("lastTransitionAt") or payload.get("last_transition_at")),
            inference_generation_id=_text(payload.get("inferenceGenerationId") or payload.get("inference_generation_id")),
            inference_generation_at=canonical_timestamp(payload.get("inferenceGenerationAt") or payload.get("inference_generation_at")),
            previous_generation_id=_text(payload.get("previousGenerationId") or payload.get("previous_generation_id")),
            semantic_fingerprint=_text(payload.get("semanticFingerprint") or payload.get("semantic_fingerprint")),
            transition_reason=_text(payload.get("transitionReason") or payload.get("transition_reason")),
            material_change=bool(payload.get("materialChange") if "materialChange" in payload else payload.get("material_change")),
            evidence_delta={
                _text(key): _unique(value if isinstance(value, (list, tuple, set)) else [value])
                for key, value in dict(delta or {}).items()
                if _text(key)
            },
            snapshot=dict(snapshot),
            version=_text(payload.get("version")) or HYPOTHESIS_LIFECYCLE_VERSION,
        )


@dataclass(frozen=True)
class HypothesisLifecycleTransition:
    transition_id: str
    lifecycle_key: str
    lifecycle_id: str
    scope: str
    previous_state: str
    current_state: str
    occurred_at: str
    inference_generation_id: str = ""
    previous_generation_id: str = ""
    reason: str = ""
    material_change: bool = False
    evidence_delta: Dict[str, List[str]] = field(default_factory=dict)
    record: Dict[str, object] = field(default_factory=dict)
    version: str = HYPOTHESIS_LIFECYCLE_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = _camelize(asdict(self))
        payload["previousStateLabel"] = HYPOTHESIS_LIFECYCLE_STATE_LABELS.get(self.previous_state, self.previous_state)
        payload["currentStateLabel"] = HYPOTHESIS_LIFECYCLE_STATE_LABELS.get(self.current_state, self.current_state)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        payload = dict(payload or {})
        delta = payload.get("evidenceDelta") or payload.get("evidence_delta") or {}
        record = payload.get("record") if isinstance(payload.get("record"), dict) else {}
        return cls(
            transition_id=_text(payload.get("transitionId") or payload.get("transition_id")),
            lifecycle_key=_text(payload.get("lifecycleKey") or payload.get("lifecycle_key")),
            lifecycle_id=_text(payload.get("lifecycleId") or payload.get("lifecycle_id")),
            scope=_text(payload.get("scope")),
            previous_state=_text(payload.get("previousState") or payload.get("previous_state")),
            current_state=_text(payload.get("currentState") or payload.get("current_state")),
            occurred_at=canonical_timestamp(payload.get("occurredAt") or payload.get("occurred_at")),
            inference_generation_id=_text(payload.get("inferenceGenerationId") or payload.get("inference_generation_id")),
            previous_generation_id=_text(payload.get("previousGenerationId") or payload.get("previous_generation_id")),
            reason=_text(payload.get("reason")),
            material_change=bool(payload.get("materialChange") if "materialChange" in payload else payload.get("material_change")),
            evidence_delta={
                _text(key): _unique(value)
                for key, value in dict(delta or {}).items()
                if _text(key)
            },
            record=dict(record),
            version=_text(payload.get("version")) or HYPOTHESIS_LIFECYCLE_VERSION,
        )


def lifecycle_policy_from_rows(rows: Iterable[Mapping[str, object]], fallback_condition_ids: Iterable[object] = None) -> Dict[str, object]:
    policies = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        raw = row.get("hypothesisLifecycle") or row.get("hypothesis_lifecycle") or {}
        if isinstance(raw, Mapping):
            policies.append(HypothesisLifecyclePolicy.from_dict(dict(raw)))
    formation = _unique(
        [value for policy in policies for value in policy.formation_condition_ids]
        or list(fallback_condition_ids or [])
    )
    invalidation = _unique([value for policy in policies for value in policy.invalidation_condition_ids])
    freshness = _unique([value for policy in policies for value in policy.required_freshness_domains])
    next_data = _unique([value for policy in policies for value in policy.next_data_requirements])
    validity_candidates = [policy.validity_minutes for policy in policies if int(policy.validity_minutes or 0) > 0]
    modes = _unique([policy.invalidation_mode for policy in policies])
    return HypothesisLifecyclePolicy(
        formation_condition_ids=formation,
        invalidation_condition_ids=invalidation,
        validity_minutes=min(validity_candidates) if validity_candidates else 0,
        required_freshness_domains=freshness,
        next_data_requirements=next_data,
        invalidation_mode=modes[0] if modes else "typedb-rule-not-materialized",
    ).to_dict()


def lifecycle_snapshots_from_relation_context(
    context: Mapping[str, object],
    observed_at: str = "",
) -> List[HypothesisLifecycleSnapshot]:
    """Build auditable lifecycle snapshots from the active InferenceBox context."""

    context = dict(context or {})
    hypothesis_set = context.get("hypothesisSet") if isinstance(context.get("hypothesisSet"), dict) else {}
    if not hypothesis_set:
        investment_brain = context.get("investmentBrain") if isinstance(context.get("investmentBrain"), dict) else {}
        hypothesis_set = investment_brain.get("hypothesisSet") if isinstance(investment_brain.get("hypothesisSet"), dict) else {}
    hypotheses = [item for item in hypothesis_set.get("hypotheses") or [] if isinstance(item, dict)]
    if not hypotheses:
        return []
    graph_inference = context.get("graphStoreInference") if isinstance(context.get("graphStoreInference"), dict) else {}
    traces = [item for item in graph_inference.get("traces") or [] if isinstance(item, dict)]
    traces_by_rule: Dict[str, List[Dict[str, object]]] = {}
    for trace in traces:
        rule_id = _text(trace.get("ruleId"))
        if rule_id:
            traces_by_rule.setdefault(rule_id, []).append(trace)
    subject = context.get("subject") if isinstance(context.get("subject"), dict) else {}
    symbol = _upper(subject.get("symbol"))
    account_id = _text(context.get("accountId"))
    portfolio_world_id = _text(context.get("portfolioWorldId"))
    market_world_id = _text(context.get("marketWorldId"))
    market_id = _text(subject.get("market"))
    profiles = context.get("observationProfiles") if isinstance(context.get("observationProfiles"), dict) else {}
    stamp = canonical_timestamp(
        observed_at
        or context.get("inferenceGenerationAt")
        or context.get("generatedAt")
        or utc_now_iso(),
        utc_now_iso(),
    )
    generation_id = _text(context.get("inferenceGenerationId"))
    generation_at = canonical_timestamp(context.get("inferenceGenerationAt") or stamp, stamp)
    result: List[HypothesisLifecycleSnapshot] = []

    def build_snapshot(
        lifecycle_key: str,
        lifecycle_id: str,
        scope: str,
        members: Sequence[Mapping[str, object]],
        family_id: str = "",
        market_scope_id: str = "",
        account_scope_id: str = "",
    ) -> HypothesisLifecycleSnapshot:
        member_rows = [dict(item) for item in members or [] if isinstance(item, Mapping)]
        rule_ids = _unique([rule for item in member_rows for rule in item.get("supportingRuleIds") or []])
        member_traces = [trace for rule_id in rule_ids for trace in traces_by_rule.get(rule_id, [])]
        supporting = _unique([
            value
            for item in member_rows
            for value in item.get("supportingEvidenceIds") or []
        ] + [
            value
            for trace in member_traces
            for value in trace.get("evidenceRelationIds") or []
        ])
        counter = _unique([
            value
            for item in member_rows
            for value in item.get("counterEvidenceIds") or []
        ])
        paths = _unique([
            value
            for item in member_rows
            for value in item.get("causalPathIds") or []
        ] + [trace.get("id") for trace in member_traces])
        matched_conditions = _unique([
            value
            for trace in member_traces
            for value in trace.get("matchedConditionIds") or []
        ])
        policy = lifecycle_policy_from_rows(member_traces, matched_conditions)
        formation = _unique(policy.get("formationConditionIds") or matched_conditions)
        policy = HypothesisLifecyclePolicy.from_dict(
            policy,
            default_formation_condition_ids=formation,
        ).to_dict()
        payload = {
            "scope": scope,
            "lifecycleId": lifecycle_id,
            "symbol": symbol,
            "familyId": family_id,
            "marketWorldId": market_scope_id or market_world_id,
            "portfolioWorldId": account_scope_id or portfolio_world_id,
            "sourceRuleIds": sorted(rule_ids),
            "supportingEvidenceIds": sorted(supporting),
            "counterEvidenceIds": sorted(counter),
            "causalPathIds": sorted(paths),
            "formationConditionIds": sorted(formation),
            "matchedConditionIds": sorted(matched_conditions),
            "policy": policy,
        }
        return HypothesisLifecycleSnapshot(
            lifecycle_key=lifecycle_key,
            lifecycle_id=lifecycle_id,
            scope=scope,
            account_id=account_id if scope == "account" else "",
            portfolio_world_id=(account_scope_id or portfolio_world_id) if scope == "account" else "",
            market_world_id=market_scope_id or market_world_id,
            market_id=market_id,
            symbol=symbol,
            family_id=family_id,
            hypothesis_ids=_unique([item.get("hypothesisId") for item in member_rows]),
            source_rule_ids=sorted(rule_ids),
            supporting_evidence_ids=sorted(supporting),
            counter_evidence_ids=sorted(counter),
            causal_path_ids=sorted(paths),
            formation_condition_ids=sorted(formation),
            matched_condition_ids=sorted(matched_conditions),
            policy=policy,
            observation_profiles={
                _text(key): dict(value)
                for key, value in dict(profiles or {}).items()
                if _text(key) and isinstance(value, dict)
            },
            trace_freshness_statuses=_unique([trace.get("freshnessStatus") for trace in member_traces]),
            inference_generation_id=generation_id,
            inference_generation_at=generation_at,
            observed_at=stamp,
            semantic_fingerprint=stable_fingerprint(payload),
        )

    market_hypotheses = [item for item in hypothesis_set.get("marketHypotheses") or [] if isinstance(item, dict)]
    market_hypothesis_member_ids = set()
    for market_hypothesis in market_hypotheses:
        lifecycle_id = _text(market_hypothesis.get("marketHypothesisId"))
        if not lifecycle_id:
            continue
        members = [
            item
            for item in hypotheses
            if _text(item.get("marketHypothesisId")) == lifecycle_id
        ]
        if not members:
            continue
        market_hypothesis_member_ids.update(_text(item.get("hypothesisId")) for item in members)
        result.append(build_snapshot(
            "market:" + lifecycle_id,
            lifecycle_id,
            "market",
            members,
            family_id=_text(members[0].get("familyId")),
            market_scope_id=_text(market_hypothesis.get("marketWorldId")),
        ))

    overlays = [item for item in hypothesis_set.get("accountOverlays") or [] if isinstance(item, dict)]
    accounted_hypothesis_ids = set()
    for overlay in overlays:
        lifecycle_id = _text(overlay.get("accountOverlayId"))
        if not lifecycle_id:
            continue
        members = [
            item
            for item in hypotheses
            if _text(item.get("accountHypothesisOverlayId")) == lifecycle_id
        ]
        if not members:
            continue
        accounted_hypothesis_ids.update(_text(item.get("hypothesisId")) for item in members)
        result.append(build_snapshot(
            "account:" + (account_id or "unknown") + ":" + lifecycle_id,
            lifecycle_id,
            "account",
            members,
            family_id=_text(overlay.get("familyId")) or _text(members[0].get("familyId")),
            market_scope_id=_text(context.get("marketWorldId")),
            account_scope_id=_text(overlay.get("portfolioWorldId")),
        ))

    # An unscoped candidate still belongs to the current account. Keeping it
    # private is safer than accidentally promoting account fields into a
    # market-shared lifecycle record.
    for hypothesis in hypotheses:
        hypothesis_id = _text(hypothesis.get("hypothesisId"))
        if (
            not hypothesis_id
            or hypothesis_id in accounted_hypothesis_ids
            or hypothesis_id in market_hypothesis_member_ids
        ):
            continue
        lifecycle_id = "hypothesis:" + hypothesis_id
        result.append(build_snapshot(
            "account:" + (account_id or "unknown") + ":" + lifecycle_id,
            lifecycle_id,
            "account",
            [hypothesis],
            family_id=_text(hypothesis.get("familyId")),
            market_scope_id=_text(context.get("marketWorldId")),
        ))
    return sorted(result, key=lambda item: (item.scope, item.lifecycle_key))


def snapshot_expiry_reason(snapshot: HypothesisLifecycleSnapshot, now: str = "") -> str:
    policy = HypothesisLifecyclePolicy.from_dict(snapshot.policy)
    observed = parse_timestamp(snapshot.observed_at)
    checked = parse_timestamp(now) or observed
    if policy.validity_minutes and observed and checked and checked > observed + timedelta(minutes=policy.validity_minutes):
        return "RuleBox 유효기간 " + str(policy.validity_minutes) + "분을 지났습니다."
    profiles = snapshot.observation_profiles or {}
    for domain in policy.required_freshness_domains:
        profile = profiles.get(domain) if isinstance(profiles.get(domain), dict) else {}
        status = _text(profile.get("freshnessStatus")).lower()
        if status in {"stale", "unavailable"}:
            return str(profile.get("freshnessGateReason") or (domain + " 데이터 신선도 기준을 통과하지 못했습니다."))
    if not policy.required_freshness_domains:
        statuses = {_text(item).lower() for item in snapshot.trace_freshness_statuses if _text(item)}
        if statuses and statuses <= {"stale", "unavailable"}:
            return "TypeDB 추론 경로의 시간 민감 근거가 신선도 기준을 통과하지 못했습니다."
    return ""


def snapshot_invalidation_reason(snapshot: HypothesisLifecycleSnapshot) -> str:
    policy = HypothesisLifecyclePolicy.from_dict(snapshot.policy)
    matched = set(snapshot.matched_condition_ids or [])
    invalidation = [item for item in policy.invalidation_condition_ids if item in matched]
    if not invalidation:
        return ""
    return "RuleBox 무효화 조건이 TypeDB 세대에서 성립했습니다: " + ", ".join(invalidation[:8])


def evidence_delta(previous: HypothesisLifecycleRecord, snapshot: HypothesisLifecycleSnapshot) -> Dict[str, List[str]]:
    before = HypothesisLifecycleSnapshot.from_dict(previous.snapshot)

    def delta(name: str, before_values: Iterable[object], current_values: Iterable[object]) -> Tuple[str, List[str], str, List[str]]:
        old = set(_unique(before_values))
        current = set(_unique(current_values))
        return (
            "added" + name,
            sorted(current - old),
            "removed" + name,
            sorted(old - current),
        )

    values: Dict[str, List[str]] = {}
    for name, before_values, current_values in [
        ("SupportingEvidenceIds", before.supporting_evidence_ids, snapshot.supporting_evidence_ids),
        ("CounterEvidenceIds", before.counter_evidence_ids, snapshot.counter_evidence_ids),
        ("CausalPathIds", before.causal_path_ids, snapshot.causal_path_ids),
        ("FormationConditionIds", before.formation_condition_ids, snapshot.formation_condition_ids),
        ("RuleIds", before.source_rule_ids, snapshot.source_rule_ids),
    ]:
        added_key, added, removed_key, removed = delta(name, before_values, current_values)
        values[added_key] = added
        values[removed_key] = removed
    return values


def has_material_delta(delta: Mapping[str, Sequence[str]]) -> bool:
    return any(bool(list(values or [])) for values in (delta or {}).values())


def state_for_delta(previous: HypothesisLifecycleRecord, delta: Mapping[str, Sequence[str]]) -> Tuple[str, str]:
    added_support = bool(delta.get("addedSupportingEvidenceIds")) or bool(delta.get("addedCausalPathIds"))
    removed_support = bool(delta.get("removedSupportingEvidenceIds")) or bool(delta.get("removedCausalPathIds"))
    added_counter = bool(delta.get("addedCounterEvidenceIds"))
    removed_counter = bool(delta.get("removedCounterEvidenceIds"))
    removed_conditions = bool(delta.get("removedFormationConditionIds"))
    if (added_support or removed_counter) and not (removed_support or added_counter or removed_conditions):
        return "strengthened", "새 지지 근거 또는 인과 경로가 추가되었습니다."
    if removed_support or added_counter or removed_conditions:
        return "weakened", "지지 근거가 줄었거나 반대 근거가 추가되었습니다."
    return "maintained", "동일한 TypeDB 인과 경로가 다음 세대에서도 유지되었습니다."


def record_for_snapshot(
    previous: Optional[HypothesisLifecycleRecord],
    snapshot: HypothesisLifecycleSnapshot,
    now: str = "",
) -> Tuple[HypothesisLifecycleRecord, Optional[HypothesisLifecycleTransition]]:
    """Return the next durable state and only emit an event for a real change."""

    stamp = canonical_timestamp(now or snapshot.observed_at or snapshot.inference_generation_at, utc_now_iso())
    expiry = snapshot_expiry_reason(snapshot, stamp)
    invalidation = snapshot_invalidation_reason(snapshot)
    if previous and previous.inference_generation_id == snapshot.inference_generation_id and previous.semantic_fingerprint == snapshot.semantic_fingerprint:
        if previous.state not in TERMINAL_HYPOTHESIS_LIFECYCLE_STATES and not expiry and not invalidation:
            return previous, None
    delta = evidence_delta(previous, snapshot) if previous else {}
    material_change = has_material_delta(delta)
    if invalidation:
        state, reason = "invalidated", invalidation
    elif expiry:
        state, reason = "expired", expiry
    elif not previous or previous.state in TERMINAL_HYPOTHESIS_LIFECYCLE_STATES:
        state = "observed"
        reason = "TypeDB 가설 경로가 정상 추론 세대에서 관찰되었습니다."
    else:
        state, reason = state_for_delta(previous, delta)
    record = HypothesisLifecycleRecord(
        lifecycle_key=snapshot.lifecycle_key,
        lifecycle_id=snapshot.lifecycle_id,
        scope=snapshot.scope,
        state=state,
        account_id=snapshot.account_id,
        portfolio_world_id=snapshot.portfolio_world_id,
        market_world_id=snapshot.market_world_id,
        market_id=snapshot.market_id,
        symbol=snapshot.symbol,
        family_id=snapshot.family_id,
        first_observed_at=(
            previous.first_observed_at
            if previous and previous.state not in TERMINAL_HYPOTHESIS_LIFECYCLE_STATES
            else stamp
        ),
        last_observed_at=stamp,
        last_transition_at=(
            stamp
            if not previous or previous.state != state or material_change
            else previous.last_transition_at
        ),
        inference_generation_id=snapshot.inference_generation_id,
        inference_generation_at=snapshot.inference_generation_at,
        previous_generation_id=previous.inference_generation_id if previous else "",
        semantic_fingerprint=snapshot.semantic_fingerprint,
        transition_reason=reason,
        material_change=material_change,
        evidence_delta=delta,
        snapshot=snapshot.to_dict(),
    )
    previous_state = previous.state if previous else ""
    changed = not previous or previous.state != record.state or record.material_change
    if not changed:
        return record, None
    transition = HypothesisLifecycleTransition(
        transition_id=stable_id(
            "hypothesis-lifecycle-transition",
            record.lifecycle_key,
            record.inference_generation_id,
            record.state,
            record.semantic_fingerprint,
        ),
        lifecycle_key=record.lifecycle_key,
        lifecycle_id=record.lifecycle_id,
        scope=record.scope,
        previous_state=previous_state,
        current_state=record.state,
        occurred_at=stamp,
        inference_generation_id=record.inference_generation_id,
        previous_generation_id=record.previous_generation_id,
        reason=record.transition_reason,
        material_change=record.material_change,
        evidence_delta=record.evidence_delta,
        record=record.to_dict(),
    )
    return record, transition


def record_for_absent_snapshot(
    previous: HypothesisLifecycleRecord,
    observed_at: str,
    expiry_reason: str = "",
) -> Tuple[HypothesisLifecycleRecord, Optional[HypothesisLifecycleTransition]]:
    """Resolve disappearance only after the caller proves the generation is healthy."""

    if previous.state in TERMINAL_HYPOTHESIS_LIFECYCLE_STATES:
        return previous, None
    stamp = canonical_timestamp(observed_at, utc_now_iso())
    state = "expired" if expiry_reason else "invalidated"
    reason = expiry_reason or "정상·정렬된 TypeDB 추론 세대에서 이전 인과 경로가 더 이상 물질화되지 않았습니다."
    record = HypothesisLifecycleRecord(
        **{
            **asdict(previous),
            "state": state,
            "last_observed_at": stamp,
            "last_transition_at": stamp,
            "previous_generation_id": previous.inference_generation_id,
            "inference_generation_id": "",
            "inference_generation_at": stamp,
            "transition_reason": reason,
            "material_change": True,
            "evidence_delta": {"removedActivePath": list(previous.snapshot.get("causalPathIds") or [])},
        }
    )
    transition = HypothesisLifecycleTransition(
        transition_id=stable_id("hypothesis-lifecycle-transition", record.lifecycle_key, stamp, state),
        lifecycle_key=record.lifecycle_key,
        lifecycle_id=record.lifecycle_id,
        scope=record.scope,
        previous_state=previous.state,
        current_state=state,
        occurred_at=stamp,
        inference_generation_id="",
        previous_generation_id=previous.inference_generation_id,
        reason=reason,
        material_change=True,
        evidence_delta=record.evidence_delta,
        record=record.to_dict(),
    )
    return record, transition


def lifecycle_context_summary(records: Iterable[HypothesisLifecycleRecord]) -> Dict[str, object]:
    rows = [item for item in records or [] if isinstance(item, HypothesisLifecycleRecord)]
    compact = []
    for record in sorted(rows, key=lambda item: (item.scope, item.lifecycle_key))[:12]:
        snapshot = HypothesisLifecycleSnapshot.from_dict(record.snapshot)
        policy = HypothesisLifecyclePolicy.from_dict(snapshot.policy)
        compact.append({
            "lifecycleKey": record.lifecycle_key,
            "scope": record.scope,
            "state": record.state,
            "stateLabel": HYPOTHESIS_LIFECYCLE_STATE_LABELS.get(record.state, record.state),
            "transitionReason": record.transition_reason,
            "inferenceGenerationId": record.inference_generation_id,
            "previousGenerationId": record.previous_generation_id,
            "materialChange": record.material_change,
            "evidenceDelta": dict(record.evidence_delta or {}),
            "nextDataRequirements": list(policy.next_data_requirements or []),
            "requiredFreshnessDomains": list(policy.required_freshness_domains or []),
        })
    return {
        "version": HYPOTHESIS_LIFECYCLE_VERSION,
        "records": compact,
        "activeCount": sum(1 for item in rows if item.state not in TERMINAL_HYPOTHESIS_LIFECYCLE_STATES),
        "terminalCount": sum(1 for item in rows if item.state in TERMINAL_HYPOTHESIS_LIFECYCLE_STATES),
    }
