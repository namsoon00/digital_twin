"""Customer-facing projection of auditable investment evidence.

Reasoning payloads retain TypeDB relation names, rule IDs and model contracts
for the trace screen. Those identifiers are not a customer explanation. This
module projects the same immutable payload into plain statements and records
when a model signal lacks customer-visible input values.
"""

from __future__ import annotations

import html
import re
from typing import Dict, Iterable, List, Mapping


CUSTOMER_EVIDENCE_VERSION = "customer-evidence-explanation-v2"

FIELD_LABELS = {
    "currentPrice": "현재가",
    "profitLossRate": "수익률",
    "ma5Distance": "5일선 차이",
    "ma20Distance": "20일선 차이",
    "ma60Distance": "60일선 차이",
    "ma5Slope": "5일선 기울기",
    "ma20Slope": "20일선 기울기",
    "ma60Slope": "60일선 기울기",
    "volume": "거래량",
    "volumeRatio": "평균 대비 거래량",
    "timeAdjustedVolumeRatio": "장 진행률 보정 거래량",
    "buyVolume": "매수 체결량",
    "sellVolume": "매도 체결량",
    "tradeStrength": "체결강도",
    "bidAskImbalance": "호가 잔량 불균형",
    "orderbookImbalance": "호가 잔량 불균형",
    "foreignNetVolume": "외국인 순매수",
    "institutionNetVolume": "기관 순매수",
    "macroDgs10": "미국 10년 금리",
    "macroDgs2": "미국 2년 금리",
    "macroDff": "미국 기준금리",
    "usdKrwRate": "원·달러 환율",
    "usdKrwDeltaPct": "원·달러 환율 변화율",
    "expectedEPS": "예상 EPS",
    "fairValue": "적정가",
    "targetPER": "목표 PER",
    "priceChangeRate": "가격 변화율",
    "priceChangePct": "가격 변화율",
    "eligibilityStatus": "검증 상태",
    "actionEnvelope": "허용 행동 범위",
}

_FIELD_TOKEN_PATTERN = "|".join(
    re.escape(field) for field in sorted(FIELD_LABELS, key=len, reverse=True)
)

_FORBIDDEN_PATTERNS = (
    re.compile(r"\b(?:HAS|MATCHES|BLOCKS|MITIGATES)_[A-Z0-9_]+\b"),
    re.compile(r"\bgraph\.[a-z0-9_.-]+\b", re.IGNORECASE),
    re.compile(
        r"\b(?:relation|hypothesis|inference|evidence|trace)[-_:][a-z0-9_.:-]+\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:" + _FIELD_TOKEN_PATTERN + r")(?![A-Za-z0-9_])"
    ),
    re.compile(r"(?<![A-Za-z0-9_-])event-[a-z0-9_.-]+(?![A-Za-z0-9_-])", re.IGNORECASE),
    re.compile(r"(?<![\d.])-?\d+\.\d{5,}(?!\d)"),
)

_GENERIC_FILLER_PATTERNS = (
    re.compile(r"관계 분석 근거는 확인됐지만.*성립값이 부족", re.IGNORECASE),
    re.compile(r"모든 후보 근거를 비교했으며.*반대 사실은 확인되지 않았", re.IGNORECASE),
    re.compile(r"현재 근거가 사라지거나 반대 근거가 새로 확인되면", re.IGNORECASE),
    re.compile(r"다음 데이터 업데이트에서 현재 조건이 유지되는지 확인", re.IGNORECASE),
    re.compile(r"단일 신호로 결론내리기보다.*같은 방향인지 확인", re.IGNORECASE),
    re.compile(r"발송 기준에 걸린 값이 다음 조회에서도 유지되는지", re.IGNORECASE),
)

_STATUS_LABELS = {
    "conditional": "조건부",
    "eligible": "사용 가능",
    "ineligible": "사용 불가",
    "supported": "근거 확인",
    "blocked": "차단",
}

_POLITE_ENDING_REPLACEMENTS = (
    (re.compile(r"앞선다(?=[.!?]|$)"), "앞섭니다"),
    (re.compile(r"됐다(?=[.!?]|$)"), "됐습니다"),
    (re.compile(r"아니다(?=[.!?]|$)"), "아닙니다"),
    (re.compile(r"필요하다(?=[.!?]|$)"), "필요합니다"),
    (re.compile(r"되지 않는다(?=[.!?]|$)"), "되지 않습니다"),
    (re.compile(r"하지 않는다(?=[.!?]|$)"), "하지 않습니다"),
    (re.compile(r"않는다(?=[.!?]|$)"), "않습니다"),
    (re.compile(r"한다(?=[.!?]|$)"), "합니다"),
    (re.compile(r"된다(?=[.!?]|$)"), "됩니다"),
    (re.compile(r"이다(?=[.!?]|$)"), "입니다"),
    (re.compile(r"있다(?=[.!?]|$)"), "있습니다"),
    (re.compile(r"없다(?=[.!?]|$)"), "없습니다"),
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _number(value: object):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: object, digits: int = 1) -> str:
    number = _number(value)
    if number is None:
        return ""
    return (("%." + str(digits) + "f") % number).rstrip("0").rstrip(".")


def _unique(values: Iterable[object], limit: int = 8) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        key = re.sub(r"[^0-9a-z가-힣]+", "", text.casefold())
        if not text or not key or key in seen:
            continue
        seen.add(key)
        rows.append(text)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def publication_outcome_kind(context: Mapping[str, object]) -> str:
    publication = _mapping(_mapping(context).get("decisionPublication"))
    return str(publication.get("outcomeKind") or "").strip().upper()


def is_non_final_publication(context: Mapping[str, object]) -> bool:
    return publication_outcome_kind(context) in {"REVIEW_ONLY", "ABSTAIN", "OBSERVATION"}


def customer_data_limitation_text(value: object) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    text = re.sub(
        r"(?<![A-Za-z0-9_])eligibilityStatus가?\s*conditional이?\s*아니게\s*되면",
        "검증 상태가 조건부에서 벗어나면",
        text,
        flags=re.IGNORECASE,
    )
    missing = [label for field, label in FIELD_LABELS.items() if field in text]
    if any(field in text for field in ("expectedEPS", "fairValue", "targetPER")):
        return (
            "적정가 계산에 필요한 "
            + "·".join(_unique(missing, 4))
            + " 중 일부가 없어 현재 가격이 싼지 비싼지 판단하지 못했습니다."
        )
    for field, label in FIELD_LABELS.items():
        text = re.sub(
            r"(?<![A-Za-z0-9_])" + re.escape(field) + r"(?![A-Za-z0-9_])",
            label,
            text,
        )
    return text


def customer_safe_text(value: object) -> str:
    """Remove implementation identifiers without inventing replacement facts."""

    text = customer_data_limitation_text(value)
    if not text:
        return ""
    for field, label in FIELD_LABELS.items():
        text = re.sub(
            r"(?<![A-Za-z0-9_])" + re.escape(field) + r"(?![A-Za-z0-9_])",
            label,
            text,
        )
    for status, label in _STATUS_LABELS.items():
        text = re.sub(
            r"(?<![A-Za-z0-9_-])" + re.escape(status) + r"(?![A-Za-z0-9_-])",
            label,
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(
        r"(?<![A-Za-z0-9_-])event-abnormal-return-support(?![A-Za-z0-9_-])",
        "사건 후 가격 반응 신호",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9_-])event-[a-z0-9_.-]+(?![A-Za-z0-9_-])",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<![\d.])(-?\d+\.\d{3,})(?!\d)",
        lambda match: (f"{float(match.group(1)):.2f}").rstrip("0").rstrip("."),
        text,
    )
    text = re.sub(
        r"(\d+)일선 차이\s*([-+]?\d+(?:\.\d+)?)(?:%)?(?:과|와)\s*"
        r"기울기\s*([-+]?\d+(?:\.\d+)?)(?:%)?",
        r"\1일선 차이 \2%와 \1일선 기울기 \3%",
        text,
    )
    for operator, suffix in (("<=", "이하"), (">=", "이상"), ("<", "미만"), (">", "초과")):
        text = re.sub(
            r"(현재가|수익률|\d+일선 차이|가격 변화율|평균 대비 거래량|체결강도|"
            r"외국인 순매수|기관 순매수|원·달러 환율|미국 10년 금리|한국 기준금리)"
            r"\s*" + re.escape(operator) + r"\s*(-?[\d,.]+(?:%|배|주|원)?)",
            r"\1 \2 " + suffix,
            text,
        )
    text = re.sub(
        r"(수익률|\d+일선 차이|\d+일선 기울기|가격 변화율|원·달러 환율 변화율|"
        r"미국 10년 금리|미국 2년 금리|미국 기준금리|한국 기준금리)"
        r"([이가은는]?)\s*([-+]?\d+(?:,\d{3})*(?:\.\d+)?)(?![%\d])\s*"
        r"(이상|이하|초과|미만)",
        lambda match: (
            match.group(1) + match.group(2) + " " + match.group(3)
            + "% " + match.group(4)
        ),
        text,
    )
    text = re.sub(
        r"(수익률|\d+일선 차이|\d+일선 기울기|가격 변화율|원·달러 환율 변화율|"
        r"미국 10년 금리|미국 2년 금리|미국 기준금리|한국 기준금리)"
        r"([이가은는]?)\s*([-+]?\d+(?:,\d{3})*(?:\.\d+)?)(?![%\d]|[.,]\d)([과와]?)"
        r"(?=\s|[가-힣]|[,.!?]|$)",
        lambda match: (
            match.group(1) + match.group(2) + " " + match.group(3)
            + "%" + ("와" if match.group(4) else "")
        ),
        text,
    )
    text = re.sub(
        r"(평균 대비 거래량|장 진행률 보정 거래량)([이가은는]?)\s*"
        r"(-?[\d,.]+)(?![배\d])\s*(이상|이하|초과|미만)",
        lambda match: (
            match.group(1) + match.group(2) + " " + match.group(3)
            + "배 " + match.group(4)
        ),
        text,
    )
    text = text.replace("가격 변화율가", "가격 변화율이")
    text = text.replace("평균 대비 거래량가", "평균 대비 거래량이")
    text = text.replace("조건부이 아니게", "조건부에서 벗어나게")
    text = text.replace("5일 평균", "5일선")
    text = text.replace("20일 평균", "20일선")
    text = text.replace("60일 평균", "60일선")
    text = re.sub(
        r"(거래량[이가은는]?)\s*(5|20|60)일선\s*([\d,.]+배)",
        r"\1 \2일 평균 거래량의 \3",
        text,
    )
    text = re.sub(
        r"\s*/\s*(?:HAS|MATCHES|BLOCKS|MITIGATES)_[A-Z0-9_]+(?:\s*/\s*.*)?$",
        "",
        text,
    )
    text = re.sub(r"\bgraph\.[a-z0-9_.-]+\b", "", text, flags=re.IGNORECASE)
    text = text.replace("사건 반응 지지 모델 신호", "사건 후 가격 반응 신호")
    text = text.replace("조건부 모델 신호", "조건부 검증 신호")
    text = text.replace("모델 신호", "검증 신호")
    text = text.replace("조건부 통계 검증 결과", "조건부 검증 신호")
    text = text.replace("통계 검증 결과", "검증 신호")
    text = text.replace("기간 관측 신선도 부족", "최근 기간 관측값 부족")
    text = re.sub(r"\s*·\s*모델 신호\s*$", "", text)
    text = text.replace("->", "→")
    left, separator, right = text.partition(":")
    if separator:
        left_key = re.sub(r"[^0-9a-z가-힣]+", "", left.casefold())
        right_key = re.sub(r"[^0-9a-z가-힣]+", "", right.casefold())
        if left_key and right_key.startswith(left_key):
            text = left
    text = re.sub(r"\s+", " ", text).strip(" ·,;/: ")
    if any(pattern.search(text) for pattern in _GENERIC_FILLER_PATTERNS):
        return ""
    for pattern, replacement in _POLITE_ENDING_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def customer_text_quality_issues(value: object) -> List[str]:
    text = str(value or "")
    issues = [
        "internal-token-" + str(index)
        for index, pattern in enumerate(_FORBIDDEN_PATTERNS, start=1)
        if pattern.search(text)
    ]
    if "모델 신호" in text:
        issues.append("unexplained-model-signal")
    if any(pattern.search(text) for pattern in _GENERIC_FILLER_PATTERNS):
        issues.append("generic-filler")
    return issues


def _visible_line_key(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"^[•\-*]\s*", "", html.unescape(text).strip())
    return re.sub(r"[^0-9a-z가-힣]+", "", text.casefold())


def _same_customer_claim(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 24 and shorter in longer and len(shorter) / len(longer) >= 0.72


def enforce_customer_message_quality(value: object) -> str:
    """Repair customer prose first, then remove only irreparable trace rows."""

    rows: List[str] = []
    seen_claims: List[str] = []
    pending_heading = ""
    for raw in str(value or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            if rows and rows[-1] != "":
                rows.append("")
            continue
        is_heading = bool(re.fullmatch(r"<b>[^<]+</b>", stripped))
        if is_heading:
            pending_heading = raw
            continue
        prefix = "• " if raw.lstrip().startswith("• ") else ""
        body = raw.lstrip()[2:] if prefix else raw
        cleaned = customer_safe_text(body)
        if not cleaned or customer_text_quality_issues(cleaned):
            continue
        key = _visible_line_key(cleaned)
        is_claim = bool(prefix) or bool(re.match(r"^[•\-*]", stripped))
        if is_claim and any(_same_customer_claim(key, prior) for prior in seen_claims):
            continue
        if pending_heading:
            if rows and rows[-1] != "":
                rows.append("")
            rows.append(pending_heading)
            pending_heading = ""
        rows.append(prefix + cleaned)
        if is_claim and key:
            seen_claims.append(key)
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


def _relation_context(context: Mapping[str, object]) -> Dict[str, object]:
    values = _mapping(context)
    relation = _mapping(values.get("ontologyRelationContext"))
    if relation:
        return relation
    return _mapping(_mapping(values.get("metadata")).get("ontologyRelationContext"))


def _trace_by_rule_id(context: Mapping[str, object]) -> Dict[str, Dict[str, object]]:
    graph = _mapping(_relation_context(context).get("graphStoreInference"))
    return {
        str(item.get("ruleId") or item.get("sourceRuleId") or "").strip(): dict(item)
        for item in graph.get("traces") or []
        if isinstance(item, Mapping)
        and str(item.get("ruleId") or item.get("sourceRuleId") or "").strip()
    }


def _trace_role(trace: Mapping[str, object], fallback: object) -> str:
    direction = str(_mapping(trace.get("claimContract")).get("expectedDirection") or "").casefold()
    if direction in {"risk", "support"}:
        return direction
    role = str(fallback or "").casefold()
    return role if role in {"risk", "support", "counter", "limitation"} else "context"


def _trend_statement(facts: Mapping[str, object]) -> str:
    values = []
    for key, label in (
        ("ma5Distance", "5일선"),
        ("ma20Distance", "20일선"),
        ("ma60Distance", "60일선"),
    ):
        number = _number(facts.get(key))
        if number is not None:
            values.append(
                label + "보다 " + _decimal(abs(number), 1) + "% "
                + ("높음" if number >= 0 else "낮음")
            )
    if not values:
        return ""
    text = "가격 회복 쪽에서는 현재가가 " + ", ".join(values) + "으로 확인됐습니다."
    volume_ratio = _number(facts.get("volumeRatio"))
    if volume_ratio is not None and volume_ratio > 0:
        text += " 거래량은 평균의 " + _decimal(volume_ratio, 2) + "배입니다."
    return text


def _customer_trace_explanation(
    trace: Mapping[str, object],
    ledger_item: Mapping[str, object],
    facts: Mapping[str, object],
) -> Dict[str, object]:
    rule_id = str(trace.get("ruleId") or trace.get("sourceRuleId") or "").strip()
    role = _trace_role(trace, ledger_item.get("role"))
    thesis = str(trace.get("thesisFamily") or "").casefold()
    statement = ""
    limitation = ""
    observed_fields: List[str] = []

    if thesis in {"mean-reversion", "momentum", "trend-recovery", "price-recovery"} or any(
        token in rule_id for token in ("pullback", "recovery", "trend")
    ):
        statement = _trend_statement(facts)
        observed_fields = [
            key
            for key in ("currentPrice", "ma5Distance", "ma20Distance", "ma60Distance", "volumeRatio")
            if facts.get(key) not in (None, "")
        ]
        if statement:
            statement += " 이 흐름이 다음 관측에서도 이어지는지 확인해야 합니다."
    elif thesis == "fundamental-deterioration" or "fragile_rally" in rule_id:
        statement = "재무 위험 모델은 이번 반등이 이어지지 못할 가능성을 감지했습니다."
        limitation = (
            "다만 현금흐름과 부채의 실제 수치가 이번 알림 근거에 연결되지 않아 "
            "이 신호만으로 매수·매도 판단을 만들지 않았습니다."
        )
    else:
        label = customer_safe_text(ledger_item.get("label") or trace.get("label") or "")
        statement = (label + "이 확인됐습니다.") if label else "관계 분석에서 검토할 가능성이 확인됐습니다."
        concrete = []
        for condition in trace.get("matchedConditions") or []:
            if not isinstance(condition, Mapping):
                continue
            field = str(condition.get("field") or "").strip()
            observed = condition.get("observedValue")
            if field in FIELD_LABELS and observed not in (None, "", [], {}):
                concrete.append(FIELD_LABELS[field] + " " + str(observed))
                observed_fields.append(field)
        if concrete:
            statement += " 성립값은 " + "·".join(concrete[:3]) + "입니다."
        else:
            limitation = "이 관계의 실제 성립값이 알림 근거에 연결되지 않아 참고 정보로만 사용합니다."

    return {
        "version": CUSTOMER_EVIDENCE_VERSION,
        "role": role,
        "statement": customer_safe_text(statement),
        "limitation": customer_safe_text(limitation),
        "ruleIds": [rule_id] if rule_id else [],
        "observedFields": observed_fields,
        "source": str(ledger_item.get("source") or "TypeDB"),
        "sourceAsOf": str(ledger_item.get("sourceAsOf") or ""),
        "customerSafe": True,
    }


def build_customer_evidence_explanations(
    context: Mapping[str, object],
    evidence_ledger: Iterable[Mapping[str, object]] = None,
    *,
    limit: int = 5,
) -> List[Dict[str, object]]:
    values = _mapping(context)
    if evidence_ledger is None:
        evidence_ledger = _mapping(values.get("notificationNarrativeBrief")).get("evidenceLedger") or []
    traces = _trace_by_rule_id(values)
    facts = _mapping(_relation_context(values).get("facts"))
    rows: List[Dict[str, object]] = []
    seen = set()
    for raw in evidence_ledger or []:
        if not isinstance(raw, Mapping) or str(raw.get("kind") or "") != "inference":
            continue
        rule_ids = [str(item or "").strip() for item in raw.get("ruleIds") or [] if str(item or "").strip()]
        trace = next((traces[item] for item in rule_ids if item in traces), {})
        explanation = _customer_trace_explanation(trace, raw, facts)
        if explanation.get("role") == "context":
            continue
        key = (explanation.get("role"), explanation.get("statement"), explanation.get("limitation"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(explanation)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def customer_evidence_rows(
    context: Mapping[str, object],
    *,
    include_limitations: bool = True,
    limit: int = 5,
) -> List[str]:
    narrative = _mapping(_mapping(context).get("notificationNarrativeBrief"))
    explanations = [
        dict(item) for item in narrative.get("customerEvidence") or [] if isinstance(item, Mapping)
    ] or build_customer_evidence_explanations(context, limit=limit)
    role_labels = {
        "risk": "위험 쪽",
        "support": "회복 쪽",
        "counter": "반대 쪽",
        "context": "참고 조건",
    }
    rows: List[str] = []
    for item in explanations:
        statement = customer_safe_text(item.get("statement"))
        if statement:
            rows.append(role_labels.get(str(item.get("role") or "context"), "참고 조건") + ": " + statement)
        limitation = customer_safe_text(item.get("limitation"))
        if include_limitations and limitation:
            rows.append("확인 한계: " + limitation)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return _unique(rows, limit)


def non_final_publication_summary(context: Mapping[str, object]) -> str:
    kind = publication_outcome_kind(context)
    synthesis = _mapping(_mapping(context).get("v2DecisionSynthesis"))
    subject_case = _mapping(_mapping(context).get("investmentSubjectDecisionCase"))
    reason = str(_mapping(subject_case.get("abstention")).get("reason") or "").casefold()
    conflict = str(synthesis.get("conflict_state") or synthesis.get("conflictState") or "").casefold()
    if kind == "REVIEW_ONLY" and conflict in {"mixed", "conflicted"}:
        summary = "가격 회복 가능성과 위험 가능성이 함께 성립해 어느 한쪽을 최종 투자 판단으로 선택하지 않았습니다."
    elif kind == "REVIEW_ONLY":
        summary = "관계 변화는 확인했지만 최종 투자 판단으로 확정하지 않았습니다."
    elif kind == "ABSTAIN":
        summary = "후보 비교에 필요한 조건이 완전하지 않아 최종 투자 판단을 만들지 않았습니다."
    else:
        summary = "자료 변화를 확인했지만 매수·매도 판단으로 사용하지 않습니다."
    if "selectedhypothesisid" in reason or "hypothesis" in reason:
        summary += " AI 비교 결과가 시스템이 허용한 후보와 일치하지 않아 안전하게 검토 결과로 전환했습니다."
    return summary
