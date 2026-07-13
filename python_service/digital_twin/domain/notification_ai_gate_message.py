import html
import re
from typing import Dict, List

from .notification_ai import criterion_lines, notification_ai_prompt_context, relation_context_value
from .notification_ai_context import relation_facts
from .notification_ai_gate_contracts import ACTION_LABELS, MESSAGE_START_BADGE, NotificationAIValidatedResponse
from .notification_ai_gate_sources import source_detail_text, source_url_rows
from .notification_ai_gate_text import (
    _clamp,
    _line_after_colon,
    _number,
    _raw_lines,
    _text,
    append_unique_text,
    reference_date,
)
from .notification_ai_gate_validation import (
    _driver_rows,
    delivery_level_from_context,
    signed_percent_from_text,
)
from .notification_text_formatting import absolute_beginner_friendly_text


def _friendly_text(value: object) -> str:
    return absolute_beginner_friendly_text(value).strip()


def _html_row(label: str, value: object, beginner: bool = False) -> str:
    text = _text(value, 500)
    if not text:
        return ""
    if beginner:
        text = _friendly_text(text)
    return "• <b>" + html.escape(label, quote=False) + "</b>: <code>" + html.escape(text, quote=False) + "</code>"

def _context_path_value(context: Dict[str, object], path: str):
    current = context or {}
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict) and part in current:
            current = current.get(part)
            continue
        return None
    return current

def _first_number_from_paths(context: Dict[str, object], paths: List[str]):
    for path in paths:
        value = _context_path_value(context, path)
        if value not in (None, ""):
            return _number(value)
    return None

def _signed_decimal_text(value: float) -> str:
    magnitude = ("%.1f" % abs(float(value or 0))).rstrip("0").rstrip(".")
    if value > 0:
        return "+" + magnitude
    if value < 0:
        return "-" + magnitude
    return magnitude

def _signed_point_change_text(delta: float) -> str:
    if abs(delta) < 0.05:
        return ""
    magnitude = ("%.1f" % abs(delta)).rstrip("0").rstrip(".")
    direction = "개선" if delta > 0 else "악화"
    return "이전 알림 대비 " + magnitude + "%p " + direction

def _profit_loss_band_label(rate: float) -> str:
    if rate <= -20:
        return "큰 손실"
    if rate <= -8:
        return "손실 관리"
    if rate < -2:
        return "손실 주의"
    if rate <= 2:
        return "거의 보합"
    if rate < 8:
        return "작은 수익"
    if rate < 20:
        return "수익 구간"
    return "큰 수익"

def _profit_loss_band_text(rate: float) -> str:
    return _profit_loss_band_label(rate) + "(" + _signed_decimal_text(rate) + "%)"

def _profit_loss_pair_from_reason(reason: str):
    match = re.search(
        r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:->|→)\s*([+-]?\d+(?:\.\d+)?)\s*%",
        str(reason or ""),
    )
    if not match:
        return None
    return (_number(match.group(1)), _number(match.group(2)))

def _profit_loss_rate_from_text(value: object):
    text = str(value or "")
    if not text.strip():
        return None
    match = re.search(r"(?:수익률|손익률|손익)\s*(?:[:：]|은|이|약)?\s*([+-]?\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    return _number(match.group(1))

def _profit_loss_current_rate(context: Dict[str, object], reason: str = ""):
    pair = _profit_loss_pair_from_reason(reason)
    if pair:
        return pair[1]
    current = _first_number_from_paths(context, [
        "profitLossRate",
        "profit_loss_rate",
        "pnlRate",
        "pnl_rate",
        "facts.profitLossRate",
        "ontologyInsight.facts.profitLossRate",
        "ontologyInsight.legacyModel.profitLossRate",
        "ontologyInsight.sourceFacts.profitLossRate",
        "ontologyInsight.executionPlan.sourceFacts.profitLossRate",
        "activeInvestmentOpinion.facts.profitLossRate",
        "activeInvestmentOpinion.legacyModel.profitLossRate",
        "activeInvestmentOpinion.sourceFacts.profitLossRate",
        "activeInvestmentOpinion.executionPlan.sourceFacts.profitLossRate",
        "ontologyRelationContext.facts.profitLossRate",
        "relationContext.facts.profitLossRate",
    ])
    if current is not None:
        return current
    for path in ["rawLines", "body", "summary", "currentStatus", "currentSituation"]:
        value = _context_path_value(context, path)
        rate = _profit_loss_rate_from_text(value)
        if rate is not None:
            return rate
    return None

def _profit_loss_delta(context: Dict[str, object], reason: str = ""):
    pair = _profit_loss_pair_from_reason(reason)
    if pair:
        previous, current = pair
        return current - previous
    delta = _first_number_from_paths(context, [
        "profitLossRateDeltaPct",
        "profitLossDeltaPct",
        "pnlDeltaPct",
        "pnlDelta",
        "facts.profitLossRateDeltaPct",
        "ontologyInsight.facts.profitLossRateDeltaPct",
        "activeInvestmentOpinion.facts.profitLossRateDeltaPct",
        "ontologyRelationContext.facts.profitLossRateDeltaPct",
        "relationContext.facts.profitLossRateDeltaPct",
    ])
    if delta is not None:
        return delta
    previous = _first_number_from_paths(context, [
        "previousProfitLossRate",
        "previous_profit_loss_rate",
        "facts.previousProfitLossRate",
        "ontologyInsight.facts.previousProfitLossRate",
        "activeInvestmentOpinion.facts.previousProfitLossRate",
        "ontologyRelationContext.facts.previousProfitLossRate",
        "relationContext.facts.previousProfitLossRate",
    ])
    current = _profit_loss_current_rate(context, reason)
    if previous is None or current is None:
        return None
    return current - previous

def _profit_loss_reason_present(reason: str) -> bool:
    return any(term in str(reason or "") for term in ["손익", "손실률", "수익률", "필수 발송 구간"])

def _profit_loss_change_summary(context: Dict[str, object], reason: str = "") -> str:
    current = _profit_loss_current_rate(context, reason)
    delta = _profit_loss_delta(context, reason)
    delta_text = _signed_point_change_text(delta) if delta is not None else ""
    if current is None and not delta_text:
        return ""
    if current is None and delta_text:
        return "손익 구간: " + delta_text
    if not delta_text and not _profit_loss_reason_present(reason):
        return ""
    parts = [_profit_loss_band_text(current)]
    if delta_text:
        parts.append(delta_text)
    return "손익 구간: " + " · ".join(parts)

def notification_topline_change_summary(context: Dict[str, object]) -> str:
    context = context or {}
    reason = str(context.get("honeyStateReason") or context.get("honeySimilarityBypassReason") or "").strip()
    source_types = " ".join(str(item or "") for item in (context.get("sourceSignalTypes") or []))
    profit_loss_summary = _profit_loss_change_summary(context, reason)
    if profit_loss_summary:
        return profit_loss_summary
    if "손익률 추가 악화" in reason:
        return "손익률 악화"
    if "필수 발송 구간" in reason or "손실률" in reason or "수익률" in reason:
        return "손익 구간"
    if "60일 평균 아래 전환" in reason or "60일선 이탈" in reason:
        return "60일선 이탈"
    if "판단 액션 변경" in reason or "판단 변경" in reason:
        return "판단 변경"
    if "새 근거 신호 추가" in reason:
        if any(term in source_types for term in ["news", "News", "Dart", "Disclosure", "researchEvidence"]):
            return "새 뉴스·공시"
        return "새 근거"
    if "새 뉴스/공시/관계 근거" in reason or "새 관계 이벤트" in reason:
        return "새 뉴스·공시"
    if "관계 강도 변화" in reason:
        return "관계 강도 변화"
    if "신규성 변화" in reason:
        return "신규성 변화"
    if "인사이트 유형 변경" in reason:
        return "유형 변경"
    if "신규 임계값 상태" in reason:
        return "새 기준 진입"
    reason_summary = notification_reason_summary(context)
    if reason_summary:
        if "뉴스" in reason_summary or "공시" in reason_summary:
            return "새 뉴스·공시"
        if "관계 점수" in reason_summary:
            return "관계 점수 상승"
        return _clean_reason_text(reason_summary, 18)
    return ""

def prepend_execution_start_badge(rendered: str, context: Dict[str, object] = None) -> str:
    text = str(rendered or "").strip()
    if not text:
        return text
    summary = notification_topline_change_summary(context or {})
    plain_badge = MESSAGE_START_BADGE
    html_badge = "<b>" + MESSAGE_START_BADGE + "</b>"
    plain_summary_line = summary if summary else ""
    html_summary_line = ("<code>" + html.escape(summary, quote=False) + "</code>") if summary else ""
    if text.startswith("<b>" + MESSAGE_START_BADGE + "</b>"):
        first, rest = (text.split("\n", 1) + [""])[:2]
        second = text.splitlines()[1] if len(text.splitlines()) > 1 else ""
        second_plain = re.sub(r"</?(?:b|code)>", "", second)
        if summary and (first.strip() != html_badge or summary not in second_plain):
            first, rest = (text.split("\n", 1) + [""])[:2]
            first = html_badge
            return first + "\n" + html_summary_line + (("\n" + rest) if rest else "")
        return text
    if text.startswith(MESSAGE_START_BADGE):
        first, rest = (text.split("\n", 1) + [""])[:2]
        second = text.splitlines()[1] if len(text.splitlines()) > 1 else ""
        if summary and (first.strip() != plain_badge or summary not in second):
            first, rest = (text.split("\n", 1) + [""])[:2]
            first = plain_badge
            return first + "\n" + plain_summary_line + (("\n" + rest) if rest else "")
        return text
    if summary:
        return html_badge + "\n" + html_summary_line + "\n\n" + text
    return html_badge + "\n\n" + text

def confidence_text(value: object) -> str:
    score = _clamp(_number(value), 0, 100)
    if score >= 85:
        label = "높음"
    elif score >= 65:
        label = "보통"
    else:
        label = "낮음"
    return label + " (" + str(round(score, 1)) + "%)"

def action_label_for_action(action: object) -> str:
    text = str(action or "").strip().upper()
    return ACTION_LABELS.get(text, str(action or "").strip())

def ai_confidence_display(response: NotificationAIValidatedResponse, level: str) -> str:
    if level in {"absoluteBeginner", "beginner"}:
        return confidence_text(response.confidence)
    return str(round(response.confidence, 1)) + "%"

def ai_judgment_section_title(level: str) -> str:
    if level in {"absoluteBeginner", "beginner"}:
        return "판단 요약"
    return "AI 최종 판단"

def ai_action_row_label(level: str) -> str:
    if level == "absoluteBeginner":
        return "지금 할 일"
    return "대응 방향"

def ai_judgment_rows(response: NotificationAIValidatedResponse, level: str) -> List[str]:
    beginner = level == "absoluteBeginner"
    rows = [
        _html_row(ai_action_row_label(level), response.action_label, beginner),
        _html_row("판단 강도", ai_confidence_display(response, level), beginner),
    ]
    summary_label = "이유" if level == "absoluteBeginner" else "AI 판단 이유"
    if response.summary:
        rows.append(_html_row(summary_label, response.summary, beginner))
    return [row for row in rows if row]

def ai_difference_rows(response: NotificationAIValidatedResponse, level: str) -> List[str]:
    if not response.precomputed_action or response.precomputed_action == response.action:
        return []
    beginner = level == "absoluteBeginner"
    rows = [
        _html_row("계산 후보", action_label_for_action(response.precomputed_action), beginner),
        _html_row("AI 최종", response.action_label, beginner),
    ]
    if response.disagreement_reason:
        rows.append(_html_row("다르게 본 이유" if level == "absoluteBeginner" else "변경 이유", response.disagreement_reason, beginner))
    return [row for row in rows if row]

def target_name_for_headline(target: object) -> str:
    text = str(target or "").strip()
    if not text:
        return ""
    for separator in ["/", "|"]:
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    return text[:24].rstrip()

def title_prefix_from_headline(headline: str) -> str:
    text = str(headline or "").strip()
    match = re.match(r"^(\[[^\]]+\]\s+(?:\S+\s+)?)", text)
    return match.group(1).strip() if match else ""

def action_headline(response: NotificationAIValidatedResponse) -> str:
    action = response.action
    if action == "BUY":
        return "매수 조건 점검"
    if action == "ADD":
        return "추가매수 조건 점검"
    if action == "HOLD":
        return "보유 유지·다음 조건 확인"
    if action == "TRIM":
        return "분할축소 우선 점검"
    if action == "SELL":
        return "매도 우선 점검"
    if action == "AVOID":
        return "신규 진입 회피"
    return response.action_label or "대응 기준 점검"

def execution_headline(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    headline = str(context.get("headline") or context.get("title") or "알림").strip()
    prefix = title_prefix_from_headline(headline)
    target = target_name_for_headline(context.get("displayTarget") or context.get("target") or context.get("title") or "")
    action = action_headline(response)
    if target:
        return " ".join(part for part in [prefix, target + ": " + action] if part)
    return " ".join(part for part in [prefix, action] if part) or headline

def _plain_value(context: Dict[str, object], label: str) -> str:
    if label == "투자자":
        return _investor_text_from_lines(_raw_lines(context))
    return _line_after_colon(_raw_lines(context), label)

def execution_footer(context: Dict[str, object], response: NotificationAIValidatedResponse, reference: str, sent: str) -> List[str]:
    return []

def _split_legacy_investor_rows(text: str) -> List[str]:
    rows = []
    for part in re.split(r",\s*(?=(?:기관|개인)(?:\s|:))", str(text or "")):
        cleaned = part.strip()
        if cleaned:
            rows.append(cleaned)
    return rows

def _investor_text_from_lines(lines: List[str]) -> str:
    for index, line in enumerate(lines):
        if not str(line or "").startswith("투자자"):
            continue
        first = _line_after_colon([line], "투자자")
        rows = _split_legacy_investor_rows(first)
        for next_line in lines[index + 1 :]:
            stripped = str(next_line or "").strip()
            if stripped.startswith(("외국인:", "기관:", "개인:")):
                rows.append(stripped)
                continue
            break
        return "\n".join(rows)
    return ""

def _html_multiline_rows(title: str, value: object) -> List[str]:
    rows = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not rows:
        return []
    result = ["<b>" + html.escape(title, quote=False) + "</b>"]
    result.extend("• " + html.escape(row, quote=False) for row in rows)
    return result

def _point_text(value: object) -> str:
    number = _number(value)
    if not number:
        return ""
    if float(number).is_integer():
        return str(int(number))
    return ("%.1f" % number).rstrip("0").rstrip(".")

def _criterion_value(lines: List[str], label: str) -> str:
    prefix = str(label or "").strip()
    for line in lines:
        text = str(line or "").strip()
        if text.startswith(prefix + ":"):
            return text.split(":", 1)[1].strip()
    return ""

def _clean_reason_text(value: object, limit: int = 100) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(설정|감지|확인 데이터):\s*", "", text)
    text = text.replace(" -> ", " → ")
    return _text(text, limit)

def _rule_score(item: Dict[str, object]) -> float:
    if not isinstance(item, dict):
        return 0.0
    return _number(item.get("strengthScore") or item.get("strength_score") or item.get("score") or item.get("relationScore"))

def _top_relation_rule_reasons(relation_context: Dict[str, object], limit: int = 2) -> List[str]:
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    rows: List[str] = []
    ranked = sorted(
        [item for item in rules if isinstance(item, dict) and not item.get("referenceOnly") and not item.get("reference_only")],
        key=_rule_score,
        reverse=True,
    )
    for item in ranked:
        label = _clean_reason_text(item.get("label") or item.get("ruleId") or item.get("rule_id"), 70)
        score = _point_text(_rule_score(item))
        if label:
            rows.append(label + ((" " + score + "점") if score else ""))
        if len(rows) >= limit:
            break
    return rows

def _relation_score_reason(context: Dict[str, object]) -> str:
    relation_context = relation_context_value(context)
    if not relation_context:
        return ""
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    score = _number(
        decision.get("score")
        or relation_context.get("signalStrength")
        or relation_context.get("score")
    )
    if not score:
        rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
        score = max([_rule_score(item) for item in rules if isinstance(item, dict)], default=0.0)
    if not score:
        return ""
    previous = _number(
        decision.get("previousScore")
        or decision.get("previousRelationScore")
        or relation_context.get("previousScore")
        or relation_context.get("previousRelationScore")
    )
    label = _clean_reason_text(
        decision.get("label")
        or decision.get("actionLabel")
        or decision.get("action")
        or relation_context.get("decisionLabel"),
        64,
    )
    if previous and score > previous:
        score_text = "관계 점수 " + _point_text(previous) + "점→" + _point_text(score) + "점"
    else:
        score_text = "관계 점수 " + _point_text(score) + "점까지 상승"
    stage = "주의 기준" if score >= 85 else "실행 기준" if score >= 70 else "관찰 기준"
    parts = [score_text + "해 " + stage + "을 넘었습니다"]
    if label:
        parts.append(label)
    reasons = _top_relation_rule_reasons(relation_context)
    if reasons:
        parts.append("주요 요인: " + ", ".join(reasons))
    return " · ".join(parts)

def _score_text_reason(context: Dict[str, object]) -> str:
    lines = [*criterion_lines(context), *_raw_lines(context)]
    for line in lines:
        text = _clean_reason_text(line, 130)
        if "점" not in text:
            continue
        if "설정" in str(line or "") and "감지" not in str(line or ""):
            continue
        score_match = re.search(r"(\d+(?:\.\d+)?)점", text)
        if not score_match:
            continue
        if any(token in text for token in ["상태", "판단", "점수", "관계", "강도", "감지"]):
            return text + "까지 올라 알림 기준을 넘었습니다."
    return ""

def _threshold_reason(context: Dict[str, object]) -> str:
    criteria = criterion_lines(context)
    if not criteria:
        return ""
    detected = _criterion_value(criteria, "감지")
    setting = _criterion_value(criteria, "설정")
    if detected and setting:
        return "감지값 " + _clean_reason_text(detected, 90) + "이 기준(" + _clean_reason_text(setting, 70) + ")을 넘었습니다."
    if detected:
        return "감지값 " + _clean_reason_text(detected, 120) + " 때문에 알림이 발생했습니다."
    if setting:
        return _clean_reason_text(setting, 120)
    return _clean_reason_text(criteria[0], 120) if criteria else ""

def notification_reason_summary(context: Dict[str, object]) -> str:
    return (
        _relation_score_reason(context)
        or _score_text_reason(context)
        or _threshold_reason(context)
    )

def _contains_any(value: object, terms: List[str]) -> bool:
    text = str(value or "").lower()
    return any(str(term or "").lower() in text for term in terms)

def _source_event_titles(context: Dict[str, object], limit: int = 3) -> List[str]:
    prompt_context = notification_ai_prompt_context(str((context or {}).get("messageType") or (context or {}).get("rule") or "notification"), context or {})
    facts = prompt_context.get("facts") if isinstance(prompt_context.get("facts"), dict) else {}
    rows: List[str] = []
    for item in (facts.get("newsHeadlines") or []) + (facts.get("researchEvidence") or []):
        if not isinstance(item, dict):
            continue
        title = source_detail_text(item, "title", "summary", "articleSummaryKo")
        source = source_detail_text(item, "domain", "provider", "source")
        impact = source_detail_text(item, "stockImpactLabel", "impactLabel")
        if not title:
            continue
        prefix = (source + " · ") if source else ""
        suffix = (" · " + impact) if impact and impact not in {"중립", "neutral", "Neutral"} else ""
        append_unique_text(rows, prefix + title + suffix, 150)
        if len(rows) >= limit:
            break
    return rows[:limit]

def _price_position_summary(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    current = _plain_value(context, "현재가")
    average = _plain_value(context, "평균매입가") or _plain_value(context, "평단가")
    pnl = _plain_value(context, "수익률") or _plain_value(context, "손익")
    trend = _plain_value(context, "추세")
    if not any([current, average, pnl, trend]):
        return ""
    if response.action in {"TRIM", "SELL"} and signed_percent_from_text(pnl) > 0:
        base = "아직 수익 구간이지만 가격 흐름이 약해져 수익 보호 쪽으로 봅니다."
    elif response.action in {"TRIM", "SELL"}:
        base = "손실 또는 가격 약화가 커져 비중 관리 쪽으로 봅니다."
    elif response.action in {"BUY", "ADD"}:
        base = "가격 회복 조건이 일부 잡혀 진입 조건을 봅니다."
    else:
        base = "바로 실행보다 다음 조건 확인이 먼저입니다."
    details = []
    if pnl:
        details.append("수익률 " + pnl)
    if trend:
        details.append(trend)
    return _text(base + (" " + " / ".join(details[:2]) if details else ""), 210)

def _relation_feature_summary(context: Dict[str, object]) -> str:
    facts = relation_facts(context or {})
    if not facts:
        return ""
    rows: List[str] = []
    ma5 = _number(facts.get("ma5Distance"))
    ma20 = _number(facts.get("ma20Distance"))
    ma60 = _number(facts.get("ma60Distance"))
    if facts.get("ma5"):
        rows.append("5일선 " + ("위" if ma5 >= 0 else "아래") + " " + str(abs(round(ma5, 1))) + "%")
    if facts.get("ma20"):
        rows.append("20일선 " + ("위" if ma20 >= 0 else "아래") + " " + str(abs(round(ma20, 1))) + "%")
    if facts.get("ma60"):
        rows.append("60일선 " + ("위" if ma60 >= 0 else "아래") + " " + str(abs(round(ma60, 1))) + "%")
    btc24 = _number(facts.get("btcChange24h"))
    btc7 = _number(facts.get("btcChange7d"))
    if facts.get("isBtcSensitive") and (btc24 or btc7):
        rows.append("BTC 민감 종목 · 24h " + str(round(btc24, 1)) + "% / 7d " + str(round(btc7, 1)) + "%")
    ten_year = _number(facts.get("us10yYield") or facts.get("us10y") or facts.get("tenYearYield"))
    fx = _number(facts.get("usdKrw") or facts.get("usdkrw") or facts.get("fxRate"))
    if ten_year:
        rows.append("미 10년 금리 " + str(round(ten_year, 2)) + "%")
    if fx:
        rows.append("USD/KRW " + str(round(fx, 1)))
    return " / ".join(rows[:4])

def _news_event_summary(context: Dict[str, object]) -> str:
    titles = _source_event_titles(context, 3)
    if not titles:
        return ""
    joined = " / ".join(titles)
    if _contains_any(joined, ["sell", "sells", "sold", "매도", "처분", "disposal"]):
        return "뉴스·공시에서 보유자산 매각/처분 성격의 이벤트가 보여 원문 확인 우선입니다: " + joined
    if _contains_any(joined, ["rise", "rises", "상승", "defend", "호재", "beat"]):
        return "뉴스에는 반대 방향 신호도 있어 가격 반응 확인이 필요합니다: " + joined
    return "뉴스·공시 확인 대상: " + joined

def context_specific_insight_rows(context: Dict[str, object], response: NotificationAIValidatedResponse, limit: int = 4) -> List[str]:
    rows: List[str] = []
    for item in _driver_rows(context, ["risk", "support", "counter", "neutral"], limit):
        append_unique_text(rows, item, 230)
    append_unique_text(rows, _price_position_summary(context, response), 230)
    append_unique_text(rows, _relation_feature_summary(context), 210)
    append_unique_text(rows, _news_event_summary(context), 230)
    if response.precomputed_action and response.precomputed_action != response.action:
        append_unique_text(
            rows,
            "계산 후보는 " + action_label_for_action(response.precomputed_action) + "였지만 최종 메시지는 " + response.action_label + " 기준으로 완화/조정했습니다.",
            210,
        )
    return rows[:limit]

def execution_telegram_message(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    level = delivery_level_from_context(context)
    if level == "absoluteBeginner":
        return execution_telegram_message_absolute_beginner(context, response)
    headline = execution_headline(context, response)
    target = str(context.get("displayTarget") or context.get("target") or "").strip()
    current = _plain_value(context, "현재가")
    average = _plain_value(context, "평균매입가") or _plain_value(context, "평단가")
    pnl = _plain_value(context, "수익률") or _plain_value(context, "손익")
    quantity = _plain_value(context, "보유 수량")
    sellable = _plain_value(context, "매도가능 수량")
    position_value = _plain_value(context, "종목 평가금액") or _plain_value(context, "평가금액")
    account_value = _plain_value(context, "계좌 평가금액")
    legacy_balance = _plain_value(context, "보유") if not any([quantity, sellable, position_value]) else ""
    trend = _plain_value(context, "추세")
    flow = _plain_value(context, "수급")
    investor = _plain_value(context, "투자자")
    sent = str(context.get("sentTime") or "").strip()
    reference = response.reference_date or reference_date(context)
    current_state_rows = [
        _html_row("현재가", current),
        _html_row("평균매입가", average),
        _html_row("수익률", pnl),
        _html_row("보유 수량", quantity),
        _html_row("매도가능 수량", sellable),
        _html_row("종목 평가금액", position_value),
        _html_row("계좌 평가금액", account_value),
        _html_row("보유", legacy_balance),
        _html_row("추세", trend),
        _html_row("수급", flow),
        *_html_multiline_rows("투자자", investor),
    ]
    current_state_rows = [row for row in current_state_rows if str(row or "").strip()]
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
        "",
        "<b>" + ai_judgment_section_title(level) + "</b>",
        *ai_judgment_rows(response, level),
    ]
    difference_rows = ai_difference_rows(response, level)
    if difference_rows:
        parts.extend(["", "<b>계산 후보와 다른 점</b>", *difference_rows])
    if current_state_rows:
        parts.extend(["", "<b>현재 상태</b>", *current_state_rows])
    context_rows = context_specific_insight_rows(context, response, 4)
    if context_rows:
        parts.extend(["", "<b>이번 알림에서 봐야 할 것</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in context_rows)
    parts.extend(["", "<b>AI가 중요하게 본 근거</b>"])
    evidence_limit = 3 if level == "beginner" else 4
    parts.extend("• " + html.escape(item, quote=False) for item in response.evidence[:evidence_limit])
    if response.counter_evidence:
        parts.extend(["", "<b>" + ("다르게 볼 점" if level == "beginner" else "확인할 반대 신호") + "</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in response.counter_evidence[:3 if level == "beginner" else 4])
    parts.extend(["", "<b>실행 전 확인</b>"])
    if response.opinion:
        parts.append("• " + html.escape(response.opinion, quote=False))
    if response.invalidation_condition:
        parts.append("• 의견이 약해지는 조건: " + html.escape(response.invalidation_condition, quote=False))
    for item in response.next_checks[:2 if level == "beginner" else 3]:
        parts.append("• 다음 확인: " + html.escape(item, quote=False))
    if response.missing_data_impact:
        parts.extend(["", "<b>부족 데이터</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in response.missing_data_impact[:4])
    if response.validation_warnings and level != "beginner":
        parts.extend(["", "<b>검증 메모</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in response.validation_warnings[:3])
    reason = notification_reason_summary(context)
    if reason:
        parts.extend(["", "<b>알림이 온 이유</b>", "• " + html.escape(reason, quote=False)])
    if level == "advanced":
        relation_labels = relation_rule_summary(context, 4)
        if relation_labels:
            parts.extend(["", "<b>관계 규칙 요약</b>"])
            parts.extend("• " + html.escape(item, quote=False) for item in relation_labels)
    if response.source_urls:
        parts.extend(["", "<b>출처</b>"])
        parts.extend(source_url_rows(response.source_urls, context))
    parts.extend(execution_footer(context, response, reference, sent))
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()

def execution_telegram_message_absolute_beginner(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    headline = execution_headline(context, response)
    target = str(context.get("displayTarget") or context.get("target") or "").strip()
    sent = str(context.get("sentTime") or "").strip()
    reference = response.reference_date or reference_date(context)
    current_state_rows = beginner_current_state_rows(context)
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
        "",
        "<b>" + ai_judgment_section_title("absoluteBeginner") + "</b>",
        *ai_judgment_rows(response, "absoluteBeginner"),
        _html_row("안내", "자동 주문이 아니라 실행 전 점검 알림입니다.", True),
    ]
    difference_rows = ai_difference_rows(response, "absoluteBeginner")
    if difference_rows:
        parts.extend(["", "<b>AI가 다르게 본 점</b>", *difference_rows])
    if current_state_rows:
        parts.extend(["", "<b>현재 상황</b>", *current_state_rows])
    context_rows = context_specific_insight_rows(context, response, 3)
    if context_rows:
        parts.extend(["", "<b>이번 알림에서 봐야 할 것</b>"])
        parts.extend("• " + html.escape(_friendly_text(item), quote=False) for item in context_rows)
    if response.evidence:
        parts.extend(["", "<b>AI가 중요하게 본 근거</b>"])
        parts.extend("• " + html.escape(_friendly_text(item), quote=False) for item in response.evidence[:3])
    if response.counter_evidence:
        parts.extend(["", "<b>다르게 볼 점</b>"])
        parts.extend("• " + html.escape(_friendly_text(item), quote=False) for item in response.counter_evidence[:2])
    parts.extend(["", "<b>실행 전 확인</b>"])
    if response.opinion:
        parts.append("• " + html.escape(_friendly_text(response.opinion), quote=False))
    if response.invalidation_condition:
        parts.append("• 의견이 약해지는 조건: " + html.escape(_friendly_text(response.invalidation_condition), quote=False))
    for item in response.next_checks[:2]:
        parts.append("• " + html.escape(_friendly_text(item), quote=False))
    if response.missing_data_impact:
        parts.extend(["", "<b>데이터 빈 곳</b>"])
        parts.extend("• " + html.escape(_friendly_text(item), quote=False) for item in response.missing_data_impact[:2])
    reason = notification_reason_summary(context)
    if reason:
        parts.extend(["", "<b>알림이 온 이유</b>", "• " + html.escape(_friendly_text(reason), quote=False)])
    if response.source_urls:
        parts.extend(["", "<b>원문/출처</b>"])
        parts.extend(source_url_rows(response.source_urls, context))
    parts.extend(execution_footer(context, response, reference, sent))
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()

def beginner_current_state_rows(context: Dict[str, object]) -> List[str]:
    values = [
        ("현재가", _plain_value(context, "현재가")),
        ("평균매입가", _plain_value(context, "평균매입가") or _plain_value(context, "평단가")),
        ("수익률", _plain_value(context, "수익률") or _plain_value(context, "손익")),
        ("보유 수량", _plain_value(context, "보유 수량")),
        ("종목 평가금액", _plain_value(context, "종목 평가금액") or _plain_value(context, "평가금액")),
        ("계좌 평가금액", _plain_value(context, "계좌 평가금액")),
        ("가격 흐름", _plain_value(context, "추세")),
        ("거래 흐름", _plain_value(context, "수급")),
    ]
    rows = [row for row in [_html_row(label, value, True) for label, value in values] if row]
    rows.extend(_html_multiline_rows("투자자", _plain_value(context, "투자자")))
    return rows

def relation_rule_summary(context: Dict[str, object], limit: int = 4) -> List[str]:
    relation_context = relation_context_value(context)
    if not isinstance(relation_context, dict):
        return []
    matches = relation_context.get("matchedRules") or relation_context.get("activeRules") or relation_context.get("rules") or []
    rows = []
    if isinstance(matches, list):
        for item in matches:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or item.get("rule") or item.get("ruleId") or "").strip()
                score = item.get("score") or item.get("strength") or item.get("relationScore")
                if label:
                    rows.append(label + ((" (" + str(score) + "점)") if score not in (None, "") else ""))
            elif str(item or "").strip():
                rows.append(str(item).strip())
            if len(rows) >= limit:
                break
    return rows
