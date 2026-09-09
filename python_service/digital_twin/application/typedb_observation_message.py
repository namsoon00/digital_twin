"""Customer message owned by deterministic TypeDB observation publication."""

from __future__ import annotations

import html
import re
from typing import Dict, Iterable, List, Mapping

from ..domain.alert_formatting import compact_multiple, compact_number, price_money, signed_pct, trade_strength_label
from ..domain.context_observation_notifications import (
    context_observation_evidence_presentation,
    typedb_context_observation_contract,
)
from ..domain.customer_evidence_explanation import customer_evidence_rows, customer_safe_text
from ..domain.notification_ai import relation_context_value
from ..domain.notification_ai_context import relation_facts
from ..domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ..domain.notification_ai_gate_text import reference_date
from ..domain.notification_delivery_explanation import customer_delivery_explanation_lines


FIELD_LABELS = {
    "currentPrice": "현재가",
    "priceChangeRate": "가격 변화율",
    "ma5Distance": "5일선 차이",
    "ma20Distance": "20일선 차이",
    "ma60Distance": "60일선 차이",
    "volumeRatio": "평균 대비 거래량",
    "timeAdjustedVolumeRatio": "장 진행률 보정 거래량",
    "tradeStrength": "체결강도",
    "foreignNetVolume": "외국인 순매수",
    "institutionNetVolume": "기관 순매수",
    "usdKrw": "원·달러 환율",
    "us10yYield": "미국 10년 금리",
    "krBaseRate": "한국 기준금리",
    "beta": "시장 민감도(베타)",
    "correlation": "시장 상관계수",
}

CRYPTO_DISPLAY_NAMES = {
    "BTC": "비트코인",
    "ETH": "이더리움",
}


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


def _text(value: object) -> str:
    return customer_safe_text(value)


def _key(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _text(value).casefold())


def _topic(value: object) -> str:
    text = _text(value)
    for character in reversed(text):
        if "가" <= character <= "힣":
            return text + ("은" if (ord(character) - 0xAC00) % 28 else "는")
    return text + "는"


def _unique(values: Iterable[object], limit: int) -> List[str]:
    rows: List[str] = []
    keys: List[str] = []
    for value in values or []:
        text = _text(value)
        key = _key(text)
        if not text or not key:
            continue
        if any(key == prior or (len(key) >= 24 and (key in prior or prior in key)) for prior in keys):
            continue
        rows.append(text)
        keys.append(key)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _bullet(value: object) -> str:
    text = _text(value)
    return "• " + html.escape(text, quote=False) if text else ""


def _target_name(value: object) -> str:
    text = str(value or "").strip()
    for separator in ("/", "|"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    return text[:24].rstrip()


def _relation_rows(context: Dict[str, object], observation: Dict[str, object]) -> List[str]:
    presentation = context_observation_evidence_presentation(context)
    lifecycle = _mapping(observation.get("relationLifecycleTransition"))
    title = _text(presentation.get("title"))
    summary = _text(presentation.get("summary"))
    rows: List[str] = []
    if title:
        rows.append("공시 원문 ‘" + title + "’이 종목 관계에 새로 연결됐습니다.")
    if summary:
        rows.append(summary)
    change_label = _text(lifecycle.get("changeLabel") or observation.get("selectedRuleLabel"))
    if change_label and not title:
        if not re.search(r"(?:습니다|됩니다|됐습니다|확인)$", change_label):
            change_label += " 관계가 새로 확인됐습니다."
        rows.append(change_label)
    reason = _text(lifecycle.get("reason"))
    if reason and "추론 세대" not in reason:
        rows.append(reason)
    return _unique(rows, 2)


def _trigger_value(field: str, value: object, context: Dict[str, object]) -> str:
    number = _number(value)
    if number is None:
        return _text(value)
    if field == "currentPrice":
        return _threshold_text(field, number, context)
    if field in {"priceChangeRate", "ma5Distance", "ma20Distance", "ma60Distance"}:
        return signed_pct(number)
    if field in {"volumeRatio", "timeAdjustedVolumeRatio"}:
        return _decimal(number, 2) + "배"
    if field == "tradeStrength":
        return _decimal(number, 1)
    if field in {"foreignNetVolume", "institutionNetVolume"}:
        return ("순매수 " if number >= 0 else "순매도 ") + compact_number(abs(number)) + "주"
    return _threshold_text(field, number, context)


def _trigger_rows(context: Dict[str, object]) -> List[str]:
    trigger = _mapping(context.get("reasoningDeliveryTrigger")) or _mapping(
        _mapping(context.get("metadata")).get("reasoningDeliveryTrigger")
    )
    facts = _mapping(trigger.get("facts"))
    rows: List[str] = []
    crypto_transitions = facts.get("cryptoTransitions")
    has_structured_crypto_transition = False
    if isinstance(crypto_transitions, list):
        transition_labels = {
            "threshold-crossed": "처음 기준에 진입했습니다.",
            "direction-changed": "직전 관찰과 상승·하락 방향이 바뀌었습니다.",
            "severity-escalated": "직전보다 큰 변동 구간으로 확대됐습니다.",
        }
        for raw in crypto_transitions:
            item = _mapping(raw)
            symbol = str(item.get("symbol") or "").strip().upper()
            change = _number(item.get("changePct"))
            threshold = _number(item.get("thresholdPct"))
            horizon = {
                "24h": "24시간",
                "7d": "7일",
            }.get(str(item.get("horizon") or "").strip(), _text(item.get("horizon")))
            direction = "상승" if str(item.get("direction") or "").lower() == "up" else "하락"
            if symbol not in CRYPTO_DISPLAY_NAMES or change is None or threshold is None or not horizon:
                continue
            has_structured_crypto_transition = True
            rows.append(
                CRYPTO_DISPLAY_NAMES[symbol]
                + " " + horizon + " 변동률이 " + signed_pct(change)
                + "로 " + direction + " 알림 기준 " + signed_pct(abs(threshold))
                + "에 도달해 "
                + transition_labels.get(
                    str(item.get("transition") or ""),
                    "관찰 구간이 바뀌었습니다.",
                )
            )
    if not rows:
        rows.extend(trigger.get("reasons") or [])
    if not has_structured_crypto_transition:
        rows.extend(customer_delivery_explanation_lines(context))
    changed_fields = [
        str(item or "").strip()
        for item in trigger.get("changedFields") or []
        if str(item or "").strip() in FIELD_LABELS
    ]
    changed = []
    for field in changed_fields:
        value = facts.get(field)
        if value in (None, ""):
            value = relation_facts(context).get(field)
        if value in (None, ""):
            continue
        changed.append(FIELD_LABELS[field] + " " + _trigger_value(field, value, context))
    if changed:
        rows.insert(
            0,
            "새 관측값은 " + " · ".join(changed[:3])
            + "입니다. 이 변화로 관계를 다시 계산했습니다.",
        )

    matched_conditions = (
        []
        if has_structured_crypto_transition
        else trigger.get("matchedConditions") or []
    )
    for raw in matched_conditions:
        if isinstance(raw, Mapping):
            field = str(raw.get("field") or "").strip()
            observed = raw.get("observedValue")
            if field in FIELD_LABELS and observed not in (None, ""):
                rows.append(
                    "성립 조건: " + FIELD_LABELS[field] + " "
                    + _trigger_value(field, observed, context)
                )
            continue
        condition = _text(raw)
        if condition and not re.search(r"[_=:{}\[\]]", condition):
            rows.append("성립 조건: " + condition)
    return _unique(rows, 2)


def _selected_rule_condition_rows(
    context: Dict[str, object],
    observation: Dict[str, object],
) -> List[str]:
    relation = relation_context_value(context)
    graph = _mapping(relation.get("graphStoreInference"))
    selected_rule_id = str(observation.get("selectedRuleId") or "").strip()
    candidates = [
        *list(relation.get("activeRules") or []),
        *list(relation.get("matchedRules") or []),
        *list(graph.get("traces") or []),
        *list(graph.get("relations") or []),
    ]
    facts = relation_facts(context)
    rows: List[str] = []
    seen_fields = set()
    for raw in candidates:
        item = _mapping(raw)
        rule_id = str(
            item.get("ruleId") or item.get("rule_id") or item.get("sourceRuleId") or ""
        ).strip()
        if selected_rule_id and rule_id != selected_rule_id:
            continue
        conditions = [
            *list(item.get("matchedConditions") or []),
            *list(item.get("conditionMatches") or []),
        ]
        for raw_condition in conditions:
            condition = _mapping(raw_condition)
            shape = _mapping(condition.get("ruleConditionShape"))
            field = str(condition.get("field") or shape.get("field") or "").strip()
            observed = condition.get("observedValue")
            if observed in (None, "") and field:
                observed = facts.get(field)
            target_properties = _mapping(
                condition.get("matchedTargetProperties") or condition.get("targetProperties")
            )
            if field in FIELD_LABELS and observed not in (None, ""):
                field_values = [(field, observed)]
            else:
                field_values = [
                    (key, target_properties.get(key))
                    for key in ("beta", "correlation")
                    if target_properties.get(key) not in (None, "")
                ]
            for current_field, current_value in field_values:
                if current_field in seen_fields:
                    continue
                seen_fields.add(current_field)
                row = _topic(FIELD_LABELS[current_field]) + " " + _trigger_value(
                    current_field, current_value, context
                )
                operator = str(
                    condition.get("operator") or shape.get("operator") or ""
                ).strip()
                expected = condition.get("expectedValue")
                if expected in (None, ""):
                    expected = shape.get("value")
                target_filters = _mapping(shape.get("targetPropertyFilters"))
                filter_rule = _mapping(target_filters.get(current_field))
                if filter_rule:
                    operator = str(filter_rule.get("operator") or operator).strip()
                    expected = filter_rule.get("value")
                if operator in {">", ">=", "<", "<=", "==", "!="} and expected not in (None, ""):
                    comparison = {
                        ">": "초과", ">=": "이상", "<": "미만", "<=": "이하",
                        "==": "일치", "!=": "불일치",
                    }[operator]
                    row += "이며, 성립 기준은 " + _trigger_value(
                        current_field, expected, context
                    ) + " " + comparison + "입니다."
                else:
                    row += "입니다."
                rows.append(row)
                if len(rows) >= 3:
                    return rows
    return rows


def _trend_row(facts: Dict[str, object]) -> str:
    values = []
    for field, label in (
        ("ma5Distance", "5일선"),
        ("ma20Distance", "20일선"),
        ("ma60Distance", "60일선"),
    ):
        distance = _number(facts.get(field))
        if distance is None:
            continue
        values.append(
            label + "보다 " + _decimal(abs(distance), 1) + "% "
            + ("높음" if distance >= 0 else "낮음")
        )
    return "가격 흐름: " + ", ".join(values) if values else ""


def _investor_row(facts: Dict[str, object]) -> str:
    rows = []
    for field, name in (
        ("foreignNetVolume", "외국인"),
        ("institutionNetVolume", "기관"),
    ):
        value = _number(facts.get(field))
        if value is None:
            continue
        rows.append(
            name + (" 순매수 " if value >= 0 else " 순매도 ")
            + compact_number(abs(value)) + "주"
        )
    return "투자자 수급: " + " · ".join(rows) if rows else ""


def _flow_rows(context: Dict[str, object], limit: int) -> List[str]:
    facts = relation_facts(context)
    relation = relation_context_value(context)
    subject = _mapping(relation.get("subject"))
    market = str(facts.get("market") or subject.get("market") or context.get("market") or "").upper()
    currency = str(facts.get("currency") or ("USD" if market == "US" else "KRW"))
    rows: List[str] = []
    current_price = _number(facts.get("currentPrice"))
    if current_price and current_price > 0:
        rows.append("현재가 " + price_money(current_price, currency))
    if market == "CRYPTO":
        trigger = _mapping(context.get("reasoningDeliveryTrigger")) or _mapping(
            _mapping(context.get("metadata")).get("reasoningDeliveryTrigger")
        )
        transition_facts = _mapping(trigger.get("facts"))
        for raw in transition_facts.get("cryptoTransitions") or []:
            item = _mapping(raw)
            change = _number(item.get("changePct"))
            threshold = _number(item.get("thresholdPct"))
            horizon = {
                "24h": "24시간",
                "7d": "7일",
            }.get(str(item.get("horizon") or "").strip(), "")
            if change is None or not horizon:
                continue
            row = horizon + " 변동 " + signed_pct(change)
            if threshold is not None:
                row += " · 알림 기준 " + signed_pct(abs(threshold))
            rows.append(row)
        return _unique(rows, limit)
    pnl = _number(facts.get("profitLossRate"))
    if pnl is not None:
        rows.append("수익률 " + signed_pct(pnl))
    rows.append(_trend_row(facts))
    volume = _number(facts.get("volume"))
    volume_ratio = _number(facts.get("volumeRatio"))
    if volume and volume > 0:
        volume_text = "거래량 " + compact_number(volume)
        if volume_ratio and volume_ratio > 0:
            volume_text += " · 평균 대비 " + compact_multiple(volume_ratio)
        rows.append(volume_text)
    rows.append(_investor_row(facts))
    strength = _number(facts.get("tradeStrength"))
    if strength and strength > 0:
        label = trade_strength_label(strength)
        rows.append(
            "체결 흐름: 체결강도 " + _decimal(strength, 1)
            + ((" (" + label + ")") if label else "")
        )
    return _unique(rows, limit)


def _threshold_text(field: str, value: object, context: Dict[str, object]) -> str:
    number = _number(value)
    if number is None:
        return _text(value)
    facts = relation_facts(context)
    if field == "currentPrice":
        relation = relation_context_value(context)
        subject = _mapping(relation.get("subject"))
        market = str(facts.get("market") or subject.get("market") or "").upper()
        return price_money(number, str(facts.get("currency") or ("USD" if market == "US" else "KRW")))
    if field in {"priceChangeRate", "ma5Distance", "ma20Distance", "ma60Distance", "us10yYield", "krBaseRate"}:
        return _decimal(number, 2) + "%"
    if field in {"volumeRatio", "timeAdjustedVolumeRatio"}:
        return _decimal(number, 2) + "배"
    if field in {"foreignNetVolume", "institutionNetVolume"}:
        return compact_number(number) + "주"
    if field == "usdKrw":
        return format(round(number, 2), ",").rstrip("0").rstrip(".") + "원"
    return _decimal(number, 2)


def _follow_up_rows(context: Dict[str, object]) -> List[str]:
    continuity = _mapping(context.get("decisionContinuityPacket"))
    rows = []
    for item in continuity.get("followUpConditions") or []:
        condition = _mapping(item)
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        if not field or operator not in {">", ">=", "<", "<=", "==", "!="}:
            continue
        threshold = condition.get("threshold")
        if threshold in (None, ""):
            continue
        value = _threshold_text(field, threshold, context)
        comparison = {
            ">": value + " 초과", ">=": value + " 이상",
            "<": value + " 미만", "<=": value + " 이하",
            "==": value + "일 때", "!=": value + "이 아닐 때",
        }[operator]
        purpose = {
            "strengthen": "관계 강화", "weaken": "관계 약화",
            "invalidate": "관계 해제", "switch": "관계 전환",
        }.get(str(condition.get("purpose") or "").lower(), "재확인")
        label = _text(condition.get("label")) or FIELD_LABELS.get(field, "확인 지표")
        outcome = _text(condition.get("onSatisfied"))
        row = purpose + ": " + label + " " + comparison
        if outcome:
            row += " → " + outcome
        rows.append(row)
    return _unique(rows, 2)


def typedb_observation_telegram_message(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    detail_level: str = "concise",
) -> str:
    """Render relation facts only; AI investment judgement uses another module."""

    observation = typedb_context_observation_contract(context)
    target = str(context.get("displayTarget") or context.get("target") or "").strip()
    label = _text(observation.get("selectedRuleLabel") or "관계 변화")
    headline = "🧩 TypeDB 추론"
    symbol = str(observation.get("symbol") or context.get("symbol") or "").strip().upper()
    target_name = CRYPTO_DISPLAY_NAMES.get(symbol) or _target_name(target)
    if target_name:
        headline += " · " + target_name
    if label and label not in headline:
        headline += " · " + label
    presentation = context_observation_evidence_presentation(context)
    evidence_rows = _unique([
        *_selected_rule_condition_rows(context, observation),
        *customer_evidence_rows(context, include_limitations=False, limit=4),
        *list(presentation.get("confirmedFacts") or []),
    ], 3)
    flow_rows = _flow_rows(context, 3 if detail_level == "concise" else 5)
    follow_up_rows = _follow_up_rows(context)
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
    ]
    for title, rows in (
        ("이번 추론 계기", _trigger_rows(context)),
        ("새로 확인한 관계", _relation_rows(context, observation)),
        ("성립 근거", evidence_rows),
        ("관측값", flow_rows),
        ("다음 관찰 조건", follow_up_rows),
    ):
        if rows:
            parts.extend(["", "<b>" + title + "</b>", *[_bullet(row) for row in rows]])
    detail_url = str(context.get("notificationDetailUrl") or "").strip()
    if detail_url:
        parts.extend([
            "",
            "• <a href=\"" + html.escape(detail_url, quote=True) + "\">웹에서 전체 근거 보기</a>",
        ])
    reference = response.reference_date or reference_date(context)
    sent = str(context.get("sentTime") or "").strip()
    footer = " · ".join(part for part in [
        "기준 " + str(reference) if reference else "",
        "발송 " + sent if sent else "",
        "번호 " + str(context.get("notificationNumber")) if context.get("notificationNumber") else "",
    ] if part)
    if footer:
        parts.extend(["", "<i>" + html.escape(footer, quote=False) + "</i>"])
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()
