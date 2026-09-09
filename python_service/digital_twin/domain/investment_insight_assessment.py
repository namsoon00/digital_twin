"""Evidence-bound investment insight, separate from trade execution authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Dict, Iterable, Mapping, Tuple


INVESTMENT_INSIGHT_ASSESSMENT_VERSION = "investment-insight-assessment-v1"
INVESTMENT_INSIGHT_TRANSITION_VERSION = "investment-insight-transition-v1"

VALID_DIRECTIONS = {"positive", "balanced", "negative"}
VALID_HORIZONS = {
    "intraday", "short-term", "medium-term", "long-term", "multi-horizon",
}
VALID_CONVICTIONS = {"tentative", "moderate", "strong"}

DIRECTION_LABELS = {
    "positive": "상승 요인 우세",
    "balanced": "상승·하락 요인 균형",
    "negative": "하락 위험 우세",
}
HORIZON_LABELS = {
    "intraday": "장중",
    "short-term": "단기",
    "medium-term": "중기",
    "long-term": "장기",
    "multi-horizon": "복합 기간",
}
CONVICTION_LABELS = {
    "tentative": "근거 강도 낮음",
    "moderate": "근거 강도 보통",
    "strong": "근거 강도 높음",
}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object, limit: int = 420) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max(1, int(limit or 1))]


def _items(value: object) -> list:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _unique(values: Iterable[object], limit: int = 16) -> list:
    rows = []
    seen = set()
    for value in values or []:
        text = _text(value)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        rows.append(text)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _fingerprint(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _direction_from_hypothesis(hypothesis: Mapping[str, object]) -> str:
    values = _mapping(hypothesis)
    action = _text(values.get("candidateAction")).upper()
    if action in {"BUY", "ADD"}:
        return "positive"
    if action in {"TRIM", "SELL", "AVOID"}:
        return "negative"
    expected = _text(values.get("expectedDirection")).casefold()
    if any(token in expected for token in (
        "positive", "up", "increase", "recover", "improve", "상승", "회복", "개선",
    )):
        return "positive"
    if any(token in expected for token in (
        "negative", "down", "decrease", "weaken", "deterior", "하락", "약화", "악화",
    )):
        return "negative"
    stance = _text(values.get("stance")).casefold()
    if stance == "support":
        return "positive"
    if stance == "risk":
        return "negative"
    return "balanced"


def _normalized_horizon(value: object, hypothesis: Mapping[str, object]) -> str:
    raw = _text(value or _mapping(hypothesis).get("horizon")).casefold()
    normalized = raw.replace("_", "-").replace(" ", "-")
    if normalized in VALID_HORIZONS:
        return normalized
    if any(token in normalized for token in ("intraday", "session", "장중")):
        return "intraday"
    if any(token in normalized for token in ("short", "단기", "day", "week")):
        return "short-term"
    if any(token in normalized for token in ("medium", "mid", "중기", "month", "quarter")):
        return "medium-term"
    if any(token in normalized for token in ("long", "장기", "year")):
        return "long-term"
    return "multi-horizon"


def _claim_rows(
    narrative_claims: Iterable[Mapping[str, object]],
) -> Dict[str, list]:
    rows: Dict[str, list] = {}
    for item in narrative_claims or []:
        claim = _mapping(item)
        section = _text(claim.get("section"), 60).casefold()
        text = _text(claim.get("text"))
        evidence_ids = _unique(claim.get("evidenceIds") or [], 12)
        if not section or not text or not evidence_ids:
            continue
        rows.setdefault(section, []).append({
            "claimId": _text(claim.get("claimId"), 160),
            "text": text,
            "evidenceIds": evidence_ids,
        })
    return rows


def _first_claim(rows: Mapping[str, list], section: str) -> Dict[str, object]:
    values = rows.get(section) or []
    return _mapping(values[0]) if values else {}


def _selected_hypothesis(
    hypotheses: Iterable[Mapping[str, object]],
    selected_hypothesis_id: str,
    research_lead_hypothesis_id: str,
) -> Dict[str, object]:
    selected_id = _text(selected_hypothesis_id or research_lead_hypothesis_id, 200)
    return next((
        _mapping(item)
        for item in hypotheses or []
        if _text(_mapping(item).get("hypothesisId"), 200) == selected_id
    ), {})


def _thesis_key(hypothesis: Mapping[str, object]) -> str:
    values = _mapping(hypothesis)
    return _text(
        values.get("familyId")
        or values.get("causalSignature")
        or values.get("templateId")
        or values.get("hypothesisId"),
        240,
    )


@dataclass(frozen=True)
class InvestmentInsightAssessment:
    """A publishable interpretation can exist without an executable action."""

    direction: str = "balanced"
    direction_label: str = DIRECTION_LABELS["balanced"]
    horizon: str = "multi-horizon"
    horizon_label: str = HORIZON_LABELS["multi-horizon"]
    conviction: str = "tentative"
    conviction_label: str = CONVICTION_LABELS["tentative"]
    dominant_thesis: str = ""
    causal_mechanism: str = ""
    investment_implication: str = ""
    catalysts: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    invalidation_condition: str = ""
    thesis_key: str = ""
    selected_hypothesis_id: str = ""
    evidence_ids: Tuple[str, ...] = ()
    claim_ids: Tuple[str, ...] = ()
    execution_eligible: bool = False
    publishable: bool = False
    status: str = "invalid"
    source: str = "ai-structured"
    validation_reasons: Tuple[str, ...] = ()
    material_fingerprint: str = ""
    content_fingerprint: str = ""
    version: str = INVESTMENT_INSIGHT_ASSESSMENT_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        result = {
            "version": payload.pop("version"),
            "direction": payload.pop("direction"),
            "directionLabel": payload.pop("direction_label"),
            "horizon": payload.pop("horizon"),
            "horizonLabel": payload.pop("horizon_label"),
            "conviction": payload.pop("conviction"),
            "convictionLabel": payload.pop("conviction_label"),
            "dominantThesis": payload.pop("dominant_thesis"),
            "causalMechanism": payload.pop("causal_mechanism"),
            "investmentImplication": payload.pop("investment_implication"),
            "catalysts": list(payload.pop("catalysts")),
            "risks": list(payload.pop("risks")),
            "invalidationCondition": payload.pop("invalidation_condition"),
            "thesisKey": payload.pop("thesis_key"),
            "selectedHypothesisId": payload.pop("selected_hypothesis_id"),
            "evidenceIds": list(payload.pop("evidence_ids")),
            "claimIds": list(payload.pop("claim_ids")),
            "executionEligible": payload.pop("execution_eligible"),
            "publishable": payload.pop("publishable"),
            "status": payload.pop("status"),
            "source": payload.pop("source"),
            "validationReasons": list(payload.pop("validation_reasons")),
            "materialFingerprint": payload.pop("material_fingerprint"),
            "contentFingerprint": payload.pop("content_fingerprint"),
        }
        return result


def investment_insight_assessment(
    payload: Mapping[str, object],
    *,
    hypotheses: Iterable[Mapping[str, object]] = (),
    selected_hypothesis_id: str = "",
    research_lead_hypothesis_id: str = "",
    narrative_claims: Iterable[Mapping[str, object]] = (),
    causal_chain: Iterable[Mapping[str, object]] = (),
    comparison_state: str = "",
    validation_state: str = "conditional",
    data_state: str = "partial",
    decision_readiness: str = "conditional",
    counter_evidence_status: str = "not-checked",
    invalidation_condition: str = "",
) -> Dict[str, object]:
    """Normalize and verify one AI interpretation against its evidence claims."""

    root = _mapping(payload)
    raw = _mapping(root.get("insightAssessment") or root.get("insight_assessment"))
    claims = _claim_rows(narrative_claims)
    selected_id = _text(
        selected_hypothesis_id
        or research_lead_hypothesis_id
        or raw.get("selectedHypothesisId")
        or raw.get("selected_hypothesis_id"),
        200,
    )
    selected = _selected_hypothesis(
        hypotheses,
        selected_id,
        research_lead_hypothesis_id,
    )
    thesis_key = _thesis_key(selected)

    direction = _text(raw.get("direction"), 40).casefold()
    source = "ai-structured" if raw else "derived-from-verified-response"
    if direction not in VALID_DIRECTIONS:
        direction = _direction_from_hypothesis(selected)
    horizon = _normalized_horizon(raw.get("horizon"), selected)

    thesis_claim = _first_claim(claims, "view")
    mechanism_claim = _first_claim(claims, "mechanism")
    implication_claim = _first_claim(claims, "implication")
    dominant_thesis = _text(thesis_claim.get("text"))
    causal_mechanism = _text(mechanism_claim.get("text"))
    investment_implication = _text(implication_claim.get("text"))
    catalyst_claims = [
        *_items(claims.get("catalyst")),
        *_items(claims.get("next-condition")),
    ]
    catalysts = _unique(
        (_mapping(item).get("text") for item in catalyst_claims),
        3,
    )
    risks = _unique(
        (_mapping(item).get("text") for item in claims.get("counter") or []),
        2,
    )

    causal_rows = [
        _mapping(item)
        for item in causal_chain or []
        if _mapping(item).get("evidenceIds")
        and _text(_mapping(item).get("status")).casefold() in {"supported", "contested"}
    ]
    # A packet-verified mechanism claim already proves an observed causal path
    # for publishing research insight.  Explicit causalChain evidence remains
    # mandatory for executable BUY/ADD/TRIM/SELL decisions elsewhere.
    causal_proof_available = bool(
        causal_rows or mechanism_claim.get("evidenceIds")
    )
    selected_verdict = _text(selected.get("verdict")).casefold()
    evidence_ids = _unique([
        *thesis_claim.get("evidenceIds", []),
        *mechanism_claim.get("evidenceIds", []),
        *implication_claim.get("evidenceIds", []),
        *[
            evidence_id
            for item in catalyst_claims
            for evidence_id in _mapping(item).get("evidenceIds") or []
        ],
        *[
            evidence_id
            for item in claims.get("counter") or []
            for evidence_id in _mapping(item).get("evidenceIds") or []
        ],
    ], 32)
    claim_ids = _unique([
        _mapping(item).get("claimId")
        for section in ("view", "mechanism", "implication", "catalyst", "counter", "next-condition")
        for item in claims.get(section) or []
    ], 24)

    requested_conviction = _text(raw.get("conviction"), 40).casefold()
    conviction = requested_conviction if requested_conviction in VALID_CONVICTIONS else "moderate"
    comparison_complete = _text(comparison_state).casefold() in {
        "completed", "research-reviewed",
    }
    normalized_validation_state = _text(validation_state).casefold()
    evidence_quality_limited = (
        normalized_validation_state in {"invalid", "failed", "error"}
        or _text(data_state).casefold() in {"unavailable", "insufficient"}
    )
    execution_validation_blocked = normalized_validation_state == "blocked"
    if (
        not comparison_complete
        or selected_verdict in {"unresolved", "rejected", "unreviewed", ""}
        or len(evidence_ids) < 2
        or not causal_proof_available
        or execution_validation_blocked
    ):
        conviction = "tentative"
    elif conviction == "strong" and (
        _text(decision_readiness).casefold() != "ready"
        or _text(counter_evidence_status).casefold() not in {"confirmed", "none-found"}
        or not any(_text(item.get("status")).casefold() == "supported" for item in causal_rows)
    ):
        conviction = "moderate"

    validation_reasons = []
    required_claims = {
        "view": dominant_thesis,
        "mechanism": causal_mechanism,
        "implication": investment_implication,
    }
    for section, value in required_claims.items():
        if not value:
            validation_reasons.append("missing-verified-" + section + "-claim")
    if not selected or not thesis_key:
        validation_reasons.append("missing-selected-hypothesis-lineage")
    if not comparison_complete:
        validation_reasons.append("hypothesis-comparison-incomplete")
    if not causal_proof_available:
        validation_reasons.append("missing-supported-causal-chain")
    if len(evidence_ids) < 2:
        validation_reasons.append("insufficient-distinct-evidence")
    if evidence_quality_limited:
        validation_reasons.append("evidence-quality-blocked")

    publishable = not validation_reasons
    status = "ready" if publishable and conviction == "strong" else "conditional" if publishable else "invalid"
    material = {
        "direction": direction,
        "horizon": horizon,
        "conviction": conviction,
        "thesisKey": thesis_key,
    }
    content = {
        **material,
        "dominantThesis": dominant_thesis,
        "causalMechanism": causal_mechanism,
        "investmentImplication": investment_implication,
        "catalysts": catalysts,
        "risks": risks,
        "evidenceIds": evidence_ids,
    }
    assessment = InvestmentInsightAssessment(
        direction=direction,
        direction_label=DIRECTION_LABELS[direction],
        horizon=horizon,
        horizon_label=HORIZON_LABELS[horizon],
        conviction=conviction,
        conviction_label=CONVICTION_LABELS[conviction],
        dominant_thesis=dominant_thesis,
        causal_mechanism=causal_mechanism,
        investment_implication=investment_implication,
        catalysts=tuple(catalysts),
        risks=tuple(risks),
        invalidation_condition=_text(
            invalidation_condition
            or raw.get("invalidationCondition")
            or raw.get("invalidation_condition")
        ),
        thesis_key=thesis_key,
        selected_hypothesis_id=selected_id,
        evidence_ids=tuple(evidence_ids),
        claim_ids=tuple(claim_ids),
        execution_eligible=bool(selected.get("executionEligible")),
        publishable=publishable,
        status=status,
        source=source,
        validation_reasons=tuple(validation_reasons),
        material_fingerprint=_fingerprint(material),
        content_fingerprint=_fingerprint(content),
    )
    return assessment.to_dict()


def compact_previous_investment_insight_episode(value: object) -> Dict[str, object]:
    payload = _mapping(value)
    insight = _mapping(payload.get("insight"))
    assessment = _mapping(
        insight.get("insightAssessment")
        or payload.get("insightAssessment")
    )
    if not assessment or assessment.get("publishable") is not True:
        return {}
    return {
        "episodeId": _text(payload.get("episodeId"), 200),
        "subjectCaseId": _text(payload.get("subjectCaseId"), 200),
        "inferenceGenerationId": _text(payload.get("inferenceGenerationId"), 200),
        "createdAt": _text(payload.get("createdAt"), 100),
        "insightAssessment": {
            key: assessment.get(key)
            for key in (
                "direction", "directionLabel", "horizon", "horizonLabel",
                "conviction", "convictionLabel", "dominantThesis",
                "causalMechanism", "investmentImplication", "thesisKey",
                "selectedHypothesisId", "materialFingerprint", "contentFingerprint",
                "publishable", "status",
            )
            if assessment.get(key) not in (None, "")
        },
    }


def investment_insight_transition(
    previous_episode: object,
    current_assessment: object,
) -> Dict[str, object]:
    """Compare stable insight meaning instead of model wording."""

    previous = compact_previous_investment_insight_episode(previous_episode)
    before = _mapping(previous.get("insightAssessment"))
    current = _mapping(current_assessment)
    base = {
        "version": INVESTMENT_INSIGHT_TRANSITION_VERSION,
        "previousEpisodeId": _text(previous.get("episodeId"), 200),
        "previousDirection": _text(before.get("direction"), 40),
        "currentDirection": _text(current.get("direction"), 40),
        "previousConviction": _text(before.get("conviction"), 40),
        "currentConviction": _text(current.get("conviction"), 40),
        "previousThesisKey": _text(before.get("thesisKey"), 240),
        "currentThesisKey": _text(current.get("thesisKey"), 240),
        "changes": [],
        "material": False,
    }
    if current.get("publishable") is not True:
        return {
            **base,
            "kind": "invalid-current-insight",
            "reason": "근거 계약을 통과한 현재 투자 인사이트가 없습니다.",
        }
    if not before:
        return {
            **base,
            "kind": "initial-insight",
            "material": True,
            "changes": ["first-publishable-insight"],
            "reason": "이 종목의 첫 근거 기반 투자 인사이트가 완성됐습니다.",
        }

    changes = []
    for key, label in (
        ("direction", "direction-changed"),
        ("horizon", "horizon-changed"),
        ("conviction", "conviction-changed"),
        ("thesisKey", "dominant-thesis-changed"),
    ):
        if _text(before.get(key), 240) != _text(current.get(key), 240):
            changes.append(label)
    material = bool(changes)
    return {
        **base,
        "kind": "material-insight-change" if material else "unchanged-insight",
        "material": material,
        "changes": changes,
        "reason": (
            "투자 방향, 관측 기간, 근거 강도 또는 지배 가설이 달라졌습니다."
            if material
            else "투자 인사이트의 핵심 의미가 이전과 같습니다."
        ),
    }
