"""Evidence-bound narrative contract for investment notifications.

TypeDB owns investment meaning and the AI may choose wording inside that
meaning.  This module is the boundary between those two concerns: it gives
every customer-facing claim an evidence role and provenance, then removes only
the unsupported claim instead of suppressing the complete notification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Dict, Iterable, List, Mapping, Tuple

from .context_observation_notifications import typedb_context_observation_contract
from .notification_ai_context import relation_context_value


NOTIFICATION_NARRATIVE_VERSION = "investment-notification-narrative-v1"
NOTIFICATION_CLAIM_VALIDATION_VERSION = "investment-notification-claim-validation-v1"
NARRATIVE_CLAIM_CONTRACT_VERSION = "investment-narrative-claim-contract-v1"

CLAIM_SECTIONS = {
    "view", "change", "support", "counter", "next-condition", "limitation",
}

FACT_LABELS = {
    "currentPrice": "현재가",
    "averagePrice": "평균매입가",
    "profitLossRate": "보유 수익률",
    "profitLossRateDeltaPct": "직전 판단 대비 수익률 변화",
    "quantity": "보유 수량",
    "sellableQuantity": "매도 가능 수량",
    "positionWeight": "종목 비중",
    "volume": "거래량",
    "volumeRatio": "평균 대비 거래량",
    "timeAdjustedVolumeRatio": "장 진행률 보정 거래량",
    "tradeStrength": "체결강도",
    "buyVolume": "매수 체결량",
    "sellVolume": "매도 체결량",
    "bidAskImbalance": "호가 불균형",
    "foreignNetVolume": "외국인 순매수",
    "institutionNetVolume": "기관 순매수",
    "individualNetVolume": "개인 순매수",
    "ma5": "5일 평균 가격",
    "ma20": "20일 평균 가격",
    "ma60": "60일 평균 가격",
    "ma5Distance": "5일 평균 괴리",
    "ma20Distance": "20일 평균 괴리",
    "ma60Distance": "60일 평균 괴리",
    "ma20Slope": "20일 평균 방향",
    "ma60Slope": "60일 평균 방향",
    "priceChangeRate": "가격 변화율",
    "usdKrw": "원·달러 환율",
    "us10yYield": "미국 10년 금리",
    "us2yYield": "미국 2년 금리",
    "krBaseRate": "한국 기준금리",
    "btcChange24h": "비트코인 24시간 변화",
    "btcChange7d": "비트코인 7일 변화",
}

DERIVED_FACT_MARKERS = (
    "ratio", "distance", "slope", "rate", "delta", "weight", "imbalance",
)

LIMITATION_MARKERS = (
    "부족", "없어", "없음", "누락", "미제공", "미확인", "확인할 수 없",
    "수집 안", "지원하지 않", "자료가 없", "데이터가 없",
)

ACTION_ONLY_PATTERN = re.compile(
    r"^(?:현재\s*)?(?:추가매수|매수|매도|분할축소|보유|신규\s*진입)"
    r"(?:를|는|은|이|가)?\s*(?:보류|유지|검토|우선|회피|권장|차단)?[.!]?$"
)
NUMBER_PATTERN = re.compile(r"(?<![0-9A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?")


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max(1, int(limit or 1))]


def _unique(values: Iterable[object], limit: int = 64) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        rows.append(text)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _stable_id(prefix: str, *parts: object) -> str:
    material = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return prefix + ":" + digest


def _number_tokens(value: object) -> set:
    tokens = set()
    for raw in NUMBER_PATTERN.findall(str(value or "")):
        normalized = raw.replace(",", "").lstrip("+")
        try:
            number = float(normalized)
        except (TypeError, ValueError):
            continue
        tokens.add(("%.8f" % number).rstrip("0").rstrip("."))
    return tokens


def _role(value: object, *, reference_only: bool = False) -> str:
    if reference_only:
        return "context"
    text = str(value or "").strip().casefold()
    if text in {"counter", "oppose", "opposes", "contradict", "contradiction"}:
        return "counter"
    if text in {"missing", "limitation", "limit", "data-limit", "guardrail"}:
        return "limitation"
    if text in {"support", "risk", "block", "blocking", "constraint", "constrain"}:
        return "support"
    return "context"


def _fact_kind(key: object) -> str:
    normalized = str(key or "").casefold()
    return "derived" if any(marker in normalized for marker in DERIVED_FACT_MARKERS) else "fact"


def _rule_reference_only(item: Mapping[str, object]) -> bool:
    basis = _mapping(item.get("knowledgeBasis") or item.get("knowledge_basis"))
    eligibility = str(
        basis.get("decisionEligibility")
        or basis.get("decision_eligibility")
        or item.get("decisionEligibility")
        or item.get("decision_eligibility")
        or ""
    ).casefold()
    return bool(
        item.get("referenceOnly")
        or item.get("reference_only")
        or eligibility == "reference-only"
    )


def _rule_narrative_role(item: Mapping[str, object], *, reference_only: bool) -> str:
    basis = _mapping(item.get("knowledgeBasis") or item.get("knowledge_basis"))
    rule_kind = str(basis.get("ruleKind") or basis.get("rule_kind") or "").casefold()
    eligibility = str(
        basis.get("decisionEligibility")
        or basis.get("decision_eligibility")
        or ""
    ).casefold()
    stage = str(item.get("decisionStage") or item.get("decision_stage") or "").casefold()
    rule_id = str(item.get("ruleId") or item.get("rule_id") or item.get("sourceRuleId") or "").casefold()
    if rule_kind == "data-quality-gate" or stage == "data_conflict" or "coverage_gap" in rule_id:
        return "limitation"
    if reference_only or eligibility == "guardrail-only":
        return "context"
    return _role(item.get("evidenceRole"), reference_only=False)


@dataclass(frozen=True)
class NarrativeEvidence:
    evidence_id: str
    role: str
    kind: str
    label: str
    value: object = None
    source: str = ""
    source_as_of: str = ""
    freshness: str = ""
    rule_ids: Tuple[str, ...] = ()
    hypothesis_ids: Tuple[str, ...] = ()
    judgement_eligible: bool = True
    detail: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "evidenceId": payload.pop("evidence_id"),
            "role": payload.pop("role"),
            "kind": payload.pop("kind"),
            "label": payload.pop("label"),
            "value": payload.pop("value"),
            "source": payload.pop("source"),
            "sourceAsOf": payload.pop("source_as_of"),
            "freshness": payload.pop("freshness"),
            "ruleIds": list(payload.pop("rule_ids")),
            "hypothesisIds": list(payload.pop("hypothesis_ids")),
            "judgementEligible": payload.pop("judgement_eligible"),
            "detail": payload.pop("detail"),
        }


@dataclass(frozen=True)
class NarrativeClaimValidation:
    claim_id: str
    section: str
    text: str
    evidence_ids: Tuple[str, ...]
    status: str
    reasons: Tuple[str, ...] = ()
    writer_kind: str = "deterministic"

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "claimId": payload.pop("claim_id"),
            "section": payload.pop("section"),
            "text": payload.pop("text"),
            "evidenceIds": list(payload.pop("evidence_ids")),
            "status": payload.pop("status"),
            "reasons": list(payload.pop("reasons")),
            "writerKind": payload.pop("writer_kind"),
        }


@dataclass(frozen=True)
class InvestmentNarrativeBrief:
    intent: str
    writer_provenance: Dict[str, object]
    evidence_ledger: Tuple[Dict[str, object], ...]
    claims: Tuple[Dict[str, object], ...]
    validations: Tuple[Dict[str, object], ...]
    hard_blocked: bool = False
    hard_block_reasons: Tuple[str, ...] = ()
    metrics: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": NOTIFICATION_NARRATIVE_VERSION,
            "claimValidationVersion": NOTIFICATION_CLAIM_VALIDATION_VERSION,
            "intent": self.intent,
            "writerProvenance": dict(self.writer_provenance),
            "evidenceLedger": [dict(item) for item in self.evidence_ledger],
            "claims": [dict(item) for item in self.claims],
            "validations": [dict(item) for item in self.validations],
            "hardBlocked": self.hard_blocked,
            "hardBlockReasons": list(self.hard_block_reasons),
            "metrics": dict(self.metrics),
        }


def response_writer_provenance(response: object, context: Mapping[str, object] = None) -> Dict[str, object]:
    source = _text(getattr(response, "source", ""), 180)
    raw_response = str(getattr(response, "raw_response", "") or "").strip()
    lowered = source.casefold()
    execution = _mapping(_mapping(context).get("notificationAiExecutionAudit"))
    fallback = _mapping(execution.get("fallback"))
    explicit_fallback = bool(fallback.get("used")) or str(execution.get("status") or "").casefold() == "typedb-fallback"
    local_source = lowered.startswith(("local", "typedb", "system", "관계"))
    ai_authored = bool(raw_response and not local_source and not explicit_fallback)
    narrative_only = bool(typedb_context_observation_contract(context or {}))
    if ai_authored:
        writer_kind = "ai"
        label = "AI 판단"
    elif lowered.startswith("typedb") or "typedb" in lowered:
        writer_kind = "typedb"
        label = "TypeDB 관계 해석"
    else:
        writer_kind = "deterministic"
        label = "시스템 근거 요약"
    return {
        "version": "notification-writer-provenance-v1",
        "writerKind": writer_kind,
        "label": label,
        "source": source or writer_kind,
        "model": _text(execution.get("model"), 120),
        "promptVersion": _text(execution.get("promptVersion"), 120),
        "requestId": _text(execution.get("requestId"), 180),
        "aiAuthored": ai_authored,
        "fallbackUsed": bool(explicit_fallback or local_source),
        "writerRole": "narrative-only" if narrative_only else "decision-and-narrative",
        "decisionOwner": "typedb" if narrative_only or not ai_authored else "ai",
    }


def build_decision_core_evidence_ledger(
    *,
    facts: Mapping[str, object],
    rules: Iterable[Mapping[str, object]],
    hypotheses: Iterable[Mapping[str, object]],
    temporal: Mapping[str, object] = None,
    external_evidence: Iterable[Mapping[str, object]] = (),
    data_limits: Iterable[Mapping[str, object]] = (),
    reference_date: object = "",
) -> List[Dict[str, object]]:
    """Build compact evidence IDs shared by the prompt and claim verifier."""

    rows: Dict[str, NarrativeEvidence] = {}
    facts = _mapping(facts)
    rules = [dict(item) for item in rules or [] if isinstance(item, Mapping)]
    hypotheses = [dict(item) for item in hypotheses or [] if isinstance(item, Mapping)]
    reference = _text(reference_date, 100)
    source = _text(facts.get("source") or facts.get("quoteSource") or "ontology-facts", 120)
    source_as_of = _text(
        facts.get("sourceAsOf") or facts.get("priceSourceAsOf") or reference,
        100,
    )
    freshness = _text(facts.get("freshnessStatus") or facts.get("dataQuality"), 80)

    for key, value in facts.items():
        if value in (None, "", [], {}) or isinstance(value, (dict, list, tuple, set)):
            continue
        evidence_id = "fact:" + str(key)
        rows[evidence_id] = NarrativeEvidence(
            evidence_id=evidence_id,
            role="context",
            kind=_fact_kind(key),
            label=FACT_LABELS.get(str(key), str(key)),
            value=value,
            source=source,
            source_as_of=source_as_of,
            freshness=freshness,
        )

    rule_fact_links: Dict[str, List[str]] = {}
    for item in rules:
        rule_id = _text(item.get("ruleId") or item.get("rule_id") or item.get("sourceRuleId"), 180)
        if not rule_id:
            continue
        reference_only = _rule_reference_only(item)
        narrative_role = _rule_narrative_role(item, reference_only=reference_only)
        evidence_state = _mapping(item.get("evidenceState") or item.get("evidence_state"))
        applied_fields = _unique(
            item.get("appliedFactFields")
            or item.get("applied_fact_fields")
            or evidence_state.get("appliedFactFields")
            or [],
            16,
        )
        evidence_id = "rule:" + rule_id
        rows[evidence_id] = NarrativeEvidence(
            evidence_id=evidence_id,
            role=narrative_role,
            kind="inference",
            label=_text(item.get("label") or rule_id, 220),
            source="TypeDB",
            source_as_of=reference,
            rule_ids=(rule_id,),
            judgement_eligible=not reference_only and narrative_role != "limitation",
            detail=_text(" / ".join(str(value) for value in item.get("evidence") or []), 320),
        )
        rule_fact_links[rule_id] = ["fact:" + value for value in applied_fields if "fact:" + value in rows]

    for item in hypotheses:
        hypothesis_id = _text(item.get("hypothesisId"), 220)
        if not hypothesis_id:
            continue
        for role, key in (("support", "supportingEvidenceIds"), ("counter", "counterEvidenceIds")):
            for raw_id in item.get(key) or []:
                evidence_id = str(raw_id or "").strip()
                if not evidence_id:
                    continue
                existing = rows.get(evidence_id)
                if existing:
                    payload = existing.to_dict()
                    hypotheses_for_evidence = tuple(_unique([*payload.get("hypothesisIds", []), hypothesis_id], 16))
                    rows[evidence_id] = NarrativeEvidence(
                        evidence_id=evidence_id,
                        role=role if existing.role == "context" else existing.role,
                        kind=existing.kind,
                        label=existing.label,
                        value=existing.value,
                        source=existing.source,
                        source_as_of=existing.source_as_of,
                        freshness=existing.freshness,
                        rule_ids=existing.rule_ids,
                        hypothesis_ids=hypotheses_for_evidence,
                        judgement_eligible=existing.judgement_eligible,
                        detail=existing.detail,
                    )
                else:
                    rows[evidence_id] = NarrativeEvidence(
                        evidence_id=evidence_id,
                        role=role,
                        kind="graph-evidence",
                        label=_text(item.get("claim") or evidence_id, 220),
                        source="TypeDB",
                        source_as_of=reference,
                        hypothesis_ids=(hypothesis_id,),
                    )
        for rule_id in item.get("supportingRuleIds") or []:
            rule_evidence_id = "rule:" + str(rule_id or "").strip()
            existing = rows.get(rule_evidence_id)
            if not existing:
                continue
            rows[rule_evidence_id] = NarrativeEvidence(
                evidence_id=existing.evidence_id,
                role="support",
                kind=existing.kind,
                label=existing.label,
                value=existing.value,
                source=existing.source,
                source_as_of=existing.source_as_of,
                freshness=existing.freshness,
                rule_ids=existing.rule_ids,
                hypothesis_ids=tuple(_unique([*existing.hypothesis_ids, hypothesis_id], 16)),
                judgement_eligible=existing.judgement_eligible,
                detail=existing.detail,
            )
            for fact_id in rule_fact_links.get(str(rule_id), []):
                fact = rows.get(fact_id)
                if fact:
                    rows[fact_id] = NarrativeEvidence(
                        evidence_id=fact.evidence_id,
                        role="support",
                        kind=fact.kind,
                        label=fact.label,
                        value=fact.value,
                        source=fact.source,
                        source_as_of=fact.source_as_of,
                        freshness=fact.freshness,
                        rule_ids=tuple(_unique([*fact.rule_ids, str(rule_id)], 16)),
                        hypothesis_ids=tuple(_unique([*fact.hypothesis_ids, hypothesis_id], 16)),
                        judgement_eligible=fact.judgement_eligible,
                        detail=fact.detail,
                    )

    temporal = _mapping(temporal)
    for item in temporal.get("windows") or []:
        if not isinstance(item, Mapping):
            continue
        key = _text(item.get("windowKey"), 40).upper()
        if not key:
            continue
        rows["temporal:" + key] = NarrativeEvidence(
            evidence_id="temporal:" + key,
            role="support",
            kind="derived",
            label=key + " 기간 경로",
            value={
                field: item.get(field)
                for field in (
                    "priceChangePct", "drawdownFromPeakPct", "reboundFromTroughPct",
                    "priceVelocityChangePct", "volumeRatioEnd",
                )
                if item.get(field) not in (None, "")
            },
            source="time-series",
            source_as_of=reference,
        )

    for index, item in enumerate(external_evidence or []):
        if not isinstance(item, Mapping):
            continue
        evidence_id = _text(item.get("evidenceId"), 220) or _stable_id(
            "external", item.get("url"), item.get("title"), index,
        )
        rows[evidence_id] = NarrativeEvidence(
            evidence_id=evidence_id,
            role=_role(item.get("evidenceRole") or item.get("polarity")),
            kind="external-evidence",
            label=_text(item.get("title") or item.get("summary") or item.get("reportName") or evidence_id, 220),
            source=_text(item.get("source") or item.get("domain") or item.get("provider"), 120),
            source_as_of=_text(item.get("publishedAt") or item.get("observedAt") or item.get("receiptDate"), 100),
            judgement_eligible=str(item.get("evidenceUse") or "").casefold() == "action",
        )

    for index, item in enumerate(data_limits or []):
        if not isinstance(item, Mapping):
            continue
        key = _text(item.get("key") or item.get("label") or index, 160)
        evidence_id = "limit:" + re.sub(r"[^0-9A-Za-z가-힣_.:-]+", "-", key)
        rows[evidence_id] = NarrativeEvidence(
            evidence_id=evidence_id,
            role="limitation",
            kind="data-limit",
            label=_text(item.get("label") or item.get("key") or "자료 한계", 180),
            value=_text(item.get("effect") or item.get("reason"), 260),
            source="data-coverage",
            source_as_of=reference,
            judgement_eligible=False,
        )

    role_priority = {"support": 0, "counter": 1, "limitation": 3, "context": 4}
    kind_priority = {"inference": 0, "external-evidence": 1, "derived": 2, "fact": 3}
    prioritized = sorted(
        rows.values(),
        key=lambda row: (
            role_priority.get(row.role, 5),
            kind_priority.get(row.kind, 4),
            row.evidence_id,
        ),
    )
    return [row.to_dict() for row in prioritized[:64]]


def context_evidence_ledger(context: Mapping[str, object], response: object = None) -> List[Dict[str, object]]:
    relation = relation_context_value(dict(context or {}))
    facts = _mapping(relation.get("facts"))
    active_rules = [dict(item) for item in relation.get("activeRules") or relation.get("matchedRules") or [] if isinstance(item, Mapping)]
    decision = _mapping(relation.get("decision"))
    envelope = _mapping(relation.get("actionEnvelope")) or _mapping(decision.get("actionEnvelope"))
    selected_rule_id = _text(decision.get("selectedRuleId") or envelope.get("selectedRuleId"), 180)
    if selected_rule_id:
        for item in active_rules:
            if (
                _text(item.get("ruleId") or item.get("rule_id") or item.get("sourceRuleId"), 180) == selected_rule_id
                and not _rule_reference_only(item)
            ):
                item.setdefault("evidenceRole", "support")
    hypotheses = list(getattr(response, "hypotheses", []) or [])
    missing = relation.get("missingData") or facts.get("missingData") or []
    missing_rows = []
    for item in missing:
        if isinstance(item, Mapping):
            missing_rows.append(dict(item))
        elif str(item or "").strip():
            missing_rows.append({"key": str(item), "label": str(item)})
    ledger = build_decision_core_evidence_ledger(
        facts=facts,
        rules=active_rules,
        hypotheses=hypotheses,
        temporal={"windows": facts.get("temporalWindows") or []},
        data_limits=missing_rows,
        reference_date=relation.get("inferenceGenerationAt") or _mapping(context).get("referenceDate"),
    )
    transition = _mapping(_mapping(context).get("aiDecisionTransition"))
    if transition:
        transition_value = {
            key: transition.get(key)
            for key in (
                "kind", "historyAvailable", "previousAction", "currentAction",
                "material", "previousEpisodeId", "previousDecidedAt",
            )
            if transition.get(key) not in (None, "")
        }
        ledger.append(NarrativeEvidence(
            evidence_id="transition:decision",
            role="context",
            kind="decision-transition",
            label="저장된 이전 판단과 현재 판단의 비교",
            value=transition_value,
            source="decision-history",
            source_as_of=_text(_mapping(context).get("referenceDate"), 100),
        ).to_dict())
    return ledger


def is_limitation_text(value: object) -> bool:
    text = _text(value, 500)
    return bool(text and any(marker in text for marker in LIMITATION_MARKERS))


def is_action_only_text(value: object) -> bool:
    text = _text(value, 180)
    return bool(text and len(text) <= 32 and ACTION_ONLY_PATTERN.match(text))


def narrative_claim_evidence_contract(
    evidence_ledger: Iterable[Mapping[str, object]],
) -> Dict[str, object]:
    """Declare exactly which routed evidence IDs each message section may cite."""

    rows = [dict(item) for item in evidence_ledger or [] if isinstance(item, Mapping)]
    eligible = [
        item for item in rows
        if bool(item.get("judgementEligible", True)) and str(item.get("evidenceId") or "")
    ]

    def ids(*roles: str, include_ineligible: bool = False) -> List[str]:
        source = rows if include_ineligible else eligible
        accepted = set(roles)
        return _unique(
            item.get("evidenceId")
            for item in source
            if str(item.get("role") or "context") in accepted
        )

    context_ids = ids("context", include_ineligible=True)
    support_ids = ids("support")
    counter_ids = ids("counter")
    limitation_ids = ids("limitation", include_ineligible=True)
    change_ids = _unique(
        item.get("evidenceId")
        for item in eligible
        if str(item.get("kind") or "") == "decision-transition"
    )
    all_decision_ids = _unique([*support_ids, *counter_ids, *context_ids])
    return {
        "version": NARRATIVE_CLAIM_CONTRACT_VERSION,
        "allowedEvidenceIdsBySection": {
            "view": all_decision_ids,
            "change": change_ids,
            "support": support_ids,
            "counter": counter_ids,
            "next-condition": _unique([*all_decision_ids, *limitation_ids]),
            "limitation": limitation_ids,
        },
        "requirements": {
            "allClaimsNeedEvidence": True,
            "actionClaimsNeedJudgementEligibleEvidence": True,
            "viewNeedsObservedState": True,
            "nextConditionNeedsObservableEvidence": True,
            "unverifiedClaimsAreNotPublished": True,
        },
    }


def normalize_narrative_claims(
    context: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    writer_kind: str,
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    prepared = _mapping(_mapping(context).get("_notificationAiPreparedDecisionCore"))
    ledger = list(prepared.get("evidenceLedger") or [])
    if not ledger:
        ledger = context_evidence_ledger(context)
    evidence_by_id = {
        str(item.get("evidenceId") or ""): dict(item)
        for item in ledger
        if isinstance(item, Mapping) and str(item.get("evidenceId") or "")
    }
    claim_contract = _mapping(prepared.get("narrativeClaimContract"))
    allowed_by_section = _mapping(claim_contract.get("allowedEvidenceIdsBySection"))
    requested = payload.get("narrativeClaims") or payload.get("narrative_claims") or []
    narrative_only = bool(typedb_context_observation_contract(context or {}))
    claims: List[Dict[str, object]] = []
    validations: List[Dict[str, object]] = []
    for index, item in enumerate(requested if isinstance(requested, list) else []):
        if not isinstance(item, Mapping):
            continue
        section = str(item.get("section") or "").strip().casefold()
        text = _text(item.get("text"), 420)
        claim_id = _text(item.get("claimId") or item.get("claim_id"), 160) or "claim:" + str(index + 1)
        evidence_ids = _unique(item.get("evidenceIds") or item.get("evidence_ids") or [], 12)
        reasons: List[str] = []
        if section not in CLAIM_SECTIONS:
            reasons.append("unsupported-section")
        if narrative_only and section in {"support", "counter"}:
            reasons.append("context-observation-cannot-assert-action-evidence")
        if not text:
            reasons.append("empty-text")
        unknown_ids = [value for value in evidence_ids if value not in evidence_by_id]
        if unknown_ids:
            reasons.append("unknown-evidence-id")
        allowed_ids = {
            str(value or "")
            for value in allowed_by_section.get(section) or []
            if str(value or "")
        }
        if allowed_by_section and any(value not in allowed_ids for value in evidence_ids):
            reasons.append("evidence-not-allowed-for-section")
        known_rows = [evidence_by_id[value] for value in evidence_ids if value in evidence_by_id]
        if section in CLAIM_SECTIONS and not known_rows:
            reasons.append("evidence-required")
        if section in {"support", "counter"} and any(
            not bool(row.get("judgementEligible", True)) for row in known_rows
        ):
            reasons.append("judgement-ineligible-evidence")
        if section == "support" and any(row.get("role") == "limitation" for row in known_rows):
            reasons.append("limitation-used-as-support")
        if section == "support" and any(row.get("role") == "counter" for row in known_rows):
            reasons.append("counter-used-as-support")
        if section == "support" and any(row.get("role") != "support" for row in known_rows):
            reasons.append("support-role-mismatch")
        if section == "counter" and any(row.get("role") == "limitation" for row in known_rows):
            reasons.append("limitation-used-as-counter")
        if section == "counter" and any(row.get("role") == "support" for row in known_rows):
            reasons.append("support-used-as-counter")
        if section == "counter" and any(row.get("role") != "counter" for row in known_rows):
            reasons.append("counter-role-mismatch")
        if section == "limitation" and known_rows and any(row.get("role") != "limitation" for row in known_rows):
            reasons.append("non-limitation-used-as-limitation")
        if section == "view" and not narrative_only and known_rows and not any(
            str(row.get("kind") or "") != "inference" for row in known_rows
        ):
            reasons.append("view-needs-observed-state")
        if section == "next-condition" and known_rows and not any(
            str(row.get("kind") or "") != "inference" for row in known_rows
        ):
            reasons.append("next-condition-needs-observable-evidence")
        if section == "support" and is_action_only_text(text):
            reasons.append("action-used-as-evidence")
        claim_numbers = _number_tokens(text)
        evidence_numbers = _number_tokens(
            json.dumps(known_rows, ensure_ascii=False, sort_keys=True, default=str)
        )
        if claim_numbers and not claim_numbers.issubset(evidence_numbers):
            reasons.append("ungrounded-number")
        status = "verified" if not reasons else "rejected"
        validation = NarrativeClaimValidation(
            claim_id=claim_id,
            section=section or "unknown",
            text=text,
            evidence_ids=tuple(evidence_ids),
            status=status,
            reasons=tuple(reasons),
            writer_kind=writer_kind,
        ).to_dict()
        validations.append(validation)
        if status == "verified":
            claims.append({
                "claimId": claim_id,
                "section": section,
                "text": text,
                "evidenceIds": evidence_ids,
                "writerKind": writer_kind,
            })
    return claims, {
        "version": NOTIFICATION_CLAIM_VALIDATION_VERSION,
        "status": "verified" if requested and len(claims) == len(requested) else ("partial" if claims else "unavailable"),
        "requestedClaimCount": len(requested) if isinstance(requested, list) else 0,
        "verifiedClaimCount": len(claims),
        "rejectedClaimCount": len([item for item in validations if item.get("status") == "rejected"]),
        "validations": validations,
        "evidenceLedger": ledger,
        "claimContract": claim_contract or narrative_claim_evidence_contract(ledger),
    }


def _narrative_intent(context: Mapping[str, object]) -> str:
    values = _mapping(context)
    relation = relation_context_value(values)
    observation = _mapping(values.get("contextObservationDecision")) or _mapping(
        values.get("typedbContextObservation")
    )
    if str(observation.get("decisionMode") or "").casefold() == "typedb-context-observation":
        return "context-observation"
    transition = _mapping(values.get("aiDecisionTransition"))
    if not transition:
        transition = _mapping(values.get("decisionTransition"))
    kind = str(transition.get("kind") or "").casefold()
    if kind == "action-changed":
        return "action-changed"
    if kind in {"readiness-changed", "validation-changed", "data-state-changed"}:
        return "readiness-changed"
    if bool(transition.get("material")) or kind in {"evidence-changed", "new-condition"}:
        return "action-maintained-evidence-changed"
    if relation:
        return "action-maintained"
    return "data-quality"


def _fallback_claims(
    response: object,
    ledger: List[Dict[str, object]],
    writer_kind: str,
    *,
    narrative_only: bool = False,
) -> List[Dict[str, object]]:
    claims: List[Dict[str, object]] = []

    def append(section: str, text: object, evidence_ids: List[str]) -> None:
        clean = _text(text, 420)
        if not clean or (section == "support" and is_action_only_text(clean)):
            return
        if section == "counter" and is_limitation_text(clean):
            return
        claim_id = _stable_id("claim", section, clean)
        claims.append({
            "claimId": claim_id,
            "section": section,
            "text": clean,
            "evidenceIds": list(evidence_ids[:4]),
            "writerKind": writer_kind,
        })

    limits = {"support": 3, "counter": 2, "limitation": 3}
    counts = {key: 0 for key in limits}
    inference_rows = [
        item for item in ledger
        if item.get("kind") == "inference" and item.get("evidenceId")
    ]
    if narrative_only:
        inference_rows.sort(key=lambda item: bool(item.get("judgementEligible", True)))
    inference_ids = [
        str(item.get("evidenceId") or "")
        for item in inference_rows
    ]
    if inference_ids:
        append(
            "view",
            getattr(response, "investment_view", "") or getattr(response, "summary", ""),
            inference_ids[:2],
        )
        for value in list(getattr(response, "next_checks", []) or [])[:2]:
            append("next-condition", value, inference_ids[:2])
    for item in ledger:
        role = str(item.get("role") or "context")
        if narrative_only and role in {"support", "counter"}:
            continue
        if role not in limits or counts[role] >= limits[role]:
            continue
        if role != "limitation" and not bool(item.get("judgementEligible", True)):
            continue
        label = _text(item.get("label"), 220)
        value = item.get("value")
        detail = _text(item.get("detail"), 260)
        text = label
        if value not in (None, "", [], {}):
            rendered_value = _text(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list)) else value,
                220,
            )
            text += ": " + rendered_value
        elif detail:
            text += ": " + detail
        append(
            "limitation" if role == "limitation" else role,
            text,
            [str(item.get("evidenceId") or "")],
        )
        counts[role] += 1
    return claims


def build_investment_narrative_brief(
    context: Mapping[str, object],
    response: object,
) -> InvestmentNarrativeBrief:
    provenance = response_writer_provenance(response, context)
    writer_kind = str(provenance.get("writerKind") or "deterministic")
    response_validation = _mapping(getattr(response, "claim_validation", {}))
    validation_packet_metadata = {
        key: response_validation.get(key)
        for key in ("inferencePacketId", "evidenceFingerprint")
        if response_validation.get(key)
    }
    ledger = [
        dict(item)
        for item in response_validation.get("evidenceLedger") or []
        if isinstance(item, Mapping)
    ]
    ledger_by_id = {
        str(item.get("evidenceId") or ""): item
        for item in ledger
        if str(item.get("evidenceId") or "")
    }
    for item in context_evidence_ledger(context, response):
        evidence_id = str(item.get("evidenceId") or "")
        if evidence_id and evidence_id not in ledger_by_id:
            ledger.append(item)
            ledger_by_id[evidence_id] = item
    response_claims = [
        dict(item)
        for item in getattr(response, "narrative_claims", []) or []
        if isinstance(item, Mapping)
    ]
    if response_claims:
        validation_context = dict(context or {})
        validation_context["_notificationAiPreparedDecisionCore"] = {
            "evidenceLedger": ledger,
            "narrativeClaimContract": (
                response_validation.get("claimContract")
                or narrative_claim_evidence_contract(ledger)
            ),
        }
        response_claims, response_validation = normalize_narrative_claims(
            validation_context,
            {"narrativeClaims": response_claims},
            writer_kind=writer_kind,
        )
        response_validation.update(validation_packet_metadata)
    if not response_claims and writer_kind == "ai":
        provenance = {
            **provenance,
            "writerKind": "typedb",
            "label": "TypeDB 근거 요약",
            "aiAuthored": False,
            "fallbackUsed": True,
            "fallbackReason": "ai-narrative-claim-contract-missing",
            "decisionOwner": "typedb",
        }
        writer_kind = "typedb"
    if response_claims:
        claims = response_claims
        validations = list(response_validation.get("validations") or [])
    else:
        generated_claims = _fallback_claims(
            response,
            ledger,
            writer_kind,
            narrative_only=str(provenance.get("writerRole") or "") == "narrative-only",
        )
        generated_context = dict(context or {})
        generated_context["_notificationAiPreparedDecisionCore"] = {
            "evidenceLedger": ledger,
            "narrativeClaimContract": narrative_claim_evidence_contract(ledger),
        }
        claims, generated_validation = normalize_narrative_claims(
            generated_context,
            {"narrativeClaims": generated_claims},
            writer_kind=writer_kind,
        )
        validations = list(generated_validation.get("validations") or [])
    verified_claims = [
        item for item in claims
        if next((row.get("status") for row in validations if row.get("claimId") == item.get("claimId")), "verified") != "rejected"
    ]
    transition_evidence = ledger_by_id.get("transition:decision")
    canonical_change = _text(getattr(response, "change_analysis", ""), 420)
    if transition_evidence and canonical_change:
        verified_claims = [item for item in verified_claims if item.get("section") != "change"]
        validations = [item for item in validations if item.get("section") != "change"]
        claim_id = _stable_id("claim", "change", canonical_change)
        verified_claims.append({
            "claimId": claim_id,
            "section": "change",
            "text": canonical_change,
            "evidenceIds": ["transition:decision"],
            "writerKind": "deterministic",
        })
        validations.append(NarrativeClaimValidation(
            claim_id=claim_id,
            section="change",
            text=canonical_change,
            evidence_ids=("transition:decision",),
            status="verified",
            writer_kind="deterministic",
        ).to_dict())
    rejected = len([item for item in validations if item.get("status") == "rejected"])
    metrics = {
        "evidenceCount": len(ledger),
        "claimCount": len(claims),
        "verifiedClaimCount": len(verified_claims),
        "rejectedClaimCount": rejected,
        "supportClaimCount": len([item for item in verified_claims if item.get("section") == "support"]),
        "counterClaimCount": len([item for item in verified_claims if item.get("section") == "counter"]),
        "limitationClaimCount": len([item for item in verified_claims if item.get("section") == "limitation"]),
        "inferencePacketId": str(response_validation.get("inferencePacketId") or ""),
        "evidenceFingerprint": str(response_validation.get("evidenceFingerprint") or ""),
    }
    return InvestmentNarrativeBrief(
        intent=_narrative_intent(context),
        writer_provenance=provenance,
        evidence_ledger=tuple(ledger),
        claims=tuple(verified_claims),
        validations=tuple(validations),
        metrics=metrics,
    )


def apply_narrative_brief_to_response(brief: InvestmentNarrativeBrief, response: object) -> None:
    claims = list(brief.claims)
    views = _unique([item.get("text") for item in claims if item.get("section") == "view"], 1)
    changes = _unique([item.get("text") for item in claims if item.get("section") == "change"], 1)
    support = _unique([item.get("text") for item in claims if item.get("section") == "support"], 3)
    counter = _unique([item.get("text") for item in claims if item.get("section") == "counter"], 2)
    limitations = _unique([item.get("text") for item in claims if item.get("section") == "limitation"], 3)
    next_conditions = _unique([item.get("text") for item in claims if item.get("section") == "next-condition"], 3)
    response.evidence = support
    response.counter_evidence = counter
    response.missing_data_impact = limitations
    response.next_checks = next_conditions
    response.investment_view = views[0] if views else ""
    response.summary = views[0] if views else ""
    response.opinion = views[0] if views else ""
    response.change_analysis = changes[0] if changes else ""
    response.current_action_plan = ""
    response.execution_decision = ""
    response.next_action_plan = next_conditions[0] if next_conditions else ""
    response.writer_provenance = dict(brief.writer_provenance)
    response.claim_validation = {
        "version": NOTIFICATION_CLAIM_VALIDATION_VERSION,
        "status": "verified" if not brief.metrics.get("rejectedClaimCount") else "partial",
        "validations": list(brief.validations),
        **dict(brief.metrics),
    }


def compact_narrative_evidence_ledger(value: object, limit: int = 48) -> List[Dict[str, object]]:
    rows = []
    for item in value or []:
        if not isinstance(item, Mapping):
            continue
        row = {
            key: item.get(key)
            for key in (
                "evidenceId", "role", "kind", "label", "value", "source",
                "sourceAsOf", "freshness", "ruleIds", "hypothesisIds",
                "judgementEligible",
            )
            if item.get(key) not in (None, "", [], {})
        }
        if row:
            rows.append(row)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def narrative_fingerprint(brief: Mapping[str, object]) -> str:
    material = {
        "intent": brief.get("intent"),
        "writerProvenance": brief.get("writerProvenance"),
        "claims": brief.get("claims"),
        "validations": brief.get("validations"),
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
