import re
from typing import Dict, List, Optional

from .message_types import MESSAGE_TYPE_LABELS, OPERATOR_REASONING_REPORT
from .accounts import message_delivery_profile
from .disclosure_analysis import local_disclosure_analysis
from .notification_ai_constants import KST
from .notification_ai_context import (
    active_investment_opinion_value,
    active_rule_evidence,
    active_rule_items,
    context_raw_lines,
    criterion_lines,
    disclosure_context,
    execution_plan_value,
    first_line_with,
    has_graph_backed_relation_context,
    line_value,
    missing_data_labels,
    normalized_text,
    relation_context_value,
    relation_labels,
    relation_trend_dynamics,
    source_alert_event_items,
    split_label_value_text,
)
from .notification_ai_news import (
    news_summary_candidate,
    rank_news_items,
    research_evidence_items,
    selected_news_headline_items,
    selected_research_evidence_items,
)
from .ontology_relation_reasoning import (
    AI_PROMPT_REGISTRY_VERSION,
    default_ai_prompt_policy_text,
    prompt_template_for_message_type,
)


SKIP_AI_OPINION_TYPES = {"workHandoff", "modelReview", OPERATOR_REASONING_REPORT}
AI_OPINION_ENGINE_VERSION = "notification-ai-opinion-v3"
AI_OPINION_MAX_LINES = 5
AI_OPINION_MAX_CHARS = 135
SENSITIVE_PROMPT_KEY_TERMS = ("secret", "token", "password", "clientid", "client_id", "appsecret", "app_key", "apikey", "api_key", "accountseq", "account_seq", "chatid", "chat_id")
NOISY_AI_LABELS = {"가격 위치", "추세 동역학", "투자자", "분석출처"}
REPEATED_VALUE_LABELS = {"이유", "상황", "해석", "수급·추세", "근거", "현재가", "가격", "추세", "수급", "상태"}
AI_LABEL_PRIORITY = {
    "판단": 10,
    "상황": 20,
    "해석": 30,
    "이유": 40,
    "투자 의견 근거": 45,
    "반대 근거": 50,
    "피할 일": 51,
    "근거": 52,
    "수급·추세": 60,
    "뉴스·공시": 65,
    "뉴스": 66,
    "공시": 67,
    "공시 의미": 68,
    "공시 영향": 69,
    "주의": 70,
    "다음 확인": 75,
    "의견": 80,
    "부족 데이터": 95,
}


def compact_text(value: object, max_len: int = 96) -> str:
    text = " ".join(str(value or "").split())
    if max_len > 3 and len(text) > max_len:
        return text[: max_len - 3].rstrip() + "..."
    return text


def active_opinion_evidence_text(opinion: Dict[str, object], key: str, limit: int = 3) -> str:
    rows = opinion.get(key) if isinstance(opinion.get(key), list) else []
    titles: List[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        title = str(
            item.get("articleSummaryKo")
            or payload.get("articleSummaryKo")
            or item.get("summary")
            or item.get("title")
            or item.get("source")
            or ""
        ).strip()
        impact = str(item.get("stockImpactLabel") or payload.get("stockImpactLabel") or "").strip()
        if impact and str(item.get("kind") or "").strip() == "news" and not title.startswith(impact):
            title = impact + ": " + title
        if title and title not in titles:
            titles.append(compact_text(title, 72))
        if len(titles) >= limit:
            break
    return " / ".join(titles)


def execution_plan_text(plan: Dict[str, object], key: str, limit: int = 2) -> str:
    rows = plan.get(key) if isinstance(plan.get(key), list) else []
    values: List[str] = []
    for item in rows:
        text = compact_text(item, 90)
        if text and text not in values:
            values.append(text)
        if len(values) >= limit:
            break
    return " / ".join(values)


def join_unique_segments(*values: str) -> str:
    rows: List[str] = []
    for value in values:
        for item in str(value or "").split(" / "):
            text = compact_text(item, 90)
            if text and text not in rows:
                rows.append(text)
    return " / ".join(rows)


def user_facing_investment_text(value: object, max_len: int = 110) -> str:
    text = compact_text(value, max_len)
    replacements = [
        ("관심종목 관계 신호 관계가 새로 감지되었습니다", "관심종목 조건 변화가 새로 잡혔습니다"),
        ("관심종목 관계 신호", "관심종목 조건"),
        ("관계 분석 관계 신호", "관계 신호"),
        ("관계 신호 관계", "조건 변화"),
        ("관계 변화", "조건 변화"),
        ("온톨로지 인사이트", "투자 인사이트"),
        ("추가매수 보류", "추가매수는 보류"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    return " ".join(text.split())


def volume_context_summary(flow: str) -> str:
    match = re.search(r"\(([0-9]+(?:\.[0-9]+)?)x\)", str(flow or ""))
    if not match:
        return ""
    try:
        ratio = float(match.group(1))
    except ValueError:
        return ""
    if ratio < 0.7:
        return "거래량 낮음(" + match.group(1) + "x)"
    if ratio >= 1.5:
        return "거래량 증가(" + match.group(1) + "x)"
    return "거래량 보통(" + match.group(1) + "x)"


def trend_position_summary(trend: str) -> str:
    text = str(trend or "")
    rows: List[str] = []
    for period in ["5", "20", "60"]:
        segment_match = re.search(period + r"일선[^,/]*(높음|낮음)", text)
        if not segment_match:
            continue
        label = period + "일선 " + ("위" if segment_match.group(1) == "높음" else "아래")
        if label not in rows:
            rows.append(label)
    return ", ".join(rows[:3])


def compact_trend_dynamics_summary(context: Dict[str, object]) -> str:
    dynamics = relation_trend_dynamics(context)
    if not dynamics:
        return ""
    rows: List[str] = []
    state = str(dynamics.get("state") or "").strip()
    if state:
        rows.append(user_facing_investment_text(state, 36))
    scenario_parts = []
    if dynamics.get("supportRetest"):
        scenario_parts.append("지지 재확인")
    if dynamics.get("recoveryAttempt"):
        scenario_parts.append("회복 시도")
    if dynamics.get("breakdownAcceleration"):
        scenario_parts.append("하락 가속")
    if scenario_parts:
        rows.append("/".join(scenario_parts[:2]))
    review_label = str(dynamics.get("reviewLabel") or dynamics.get("reviewLevelLabel") or "").strip()
    if review_label:
        rows.append(review_label)
    return ", ".join(rows[:3])


def compact_market_context(flow: str, trend: str, context: Dict[str, object]) -> str:
    rows = [
        volume_context_summary(flow),
        trend_position_summary(trend),
        compact_trend_dynamics_summary(context),
    ]
    unique: List[str] = []
    for row in rows:
        text = compact_text(row, 70)
        if text and text not in unique:
            unique.append(text)
    return " / ".join(unique[:3])


def compact_news_summary_text(context: Dict[str, object]) -> str:
    disclosure = disclosure_context(context)
    if disclosure:
        report = str(disclosure.get("reportName") or disclosure.get("report_name") or "").strip()
        receipt_date = str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or "").strip()
        if report:
            return compact_text("공시: " + report + (", " + receipt_date if receipt_date else ""), 115)
    news_items = selected_news_headline_items(context, 4)
    if news_items:
        first = news_items[0]
        payload = first.get("payload") if isinstance(first.get("payload"), dict) else {}
        source = str(first.get("domain") or first.get("provider") or "뉴스").strip()
        impact = str(first.get("stockImpactLabel") or payload.get("stockImpactLabel") or "").strip()
        summary = news_summary_candidate(first)
        prefix = source + ((" " + impact) if impact and impact not in {"중립", "neutral", "Neutral"} else "")
        suffix = " 외 " + str(len(news_items) - 1) + "건" if len(news_items) > 1 else ""
        return compact_text(prefix + ": " + compact_text(summary, 58) + suffix, 125)
    evidence_items = selected_research_evidence_items(context, 1)
    if evidence_items:
        item = evidence_items[0]
        source = str(item.get("source") or "리서치").strip()
        title = str(item.get("title") or item.get("summary") or "").strip()
        if title:
            return compact_text(source + ": " + title, 115)
    return ""


def investment_reason_text(
    target: str,
    insight_label: str,
    thesis: str,
    flow_line: str,
    trend_line: str,
    context: Dict[str, object],
) -> str:
    base = user_facing_investment_text(thesis, 110)
    if not base:
        label = user_facing_investment_text(insight_label, 48)
        base = (target + "에서 " + label + "이 감지됐습니다.").strip()
    market = compact_market_context(flow_line, trend_line, context)
    if market:
        return compact_text(base.rstrip(".") + ". " + market, 170)
    return compact_text(base, 150)


def trend_dynamics_summary(context: Dict[str, object]) -> str:
    dynamics = relation_trend_dynamics(context)
    if not dynamics:
        return ""
    parts: List[str] = []
    state = str(dynamics.get("state") or "").strip()
    momentum = str(dynamics.get("priceMomentum") or "").strip()
    slope = str(dynamics.get("slope") or "").strip()
    curve = str(dynamics.get("curve") or "").strip()
    if state:
        parts.append("상태 " + state)
    if momentum:
        parts.append("가격 " + momentum + " " + str(dynamics.get("priceChangeRate") or 0) + "%")
    if slope:
        parts.append("기울기 " + slope)
    if curve:
        parts.append("커브 " + curve + " " + str(dynamics.get("trendCurve") or 0))
    scenario_parts = []
    if dynamics.get("supportRetest"):
        scenario_parts.append("60일선 지지 재확인")
    if dynamics.get("recoveryAttempt"):
        scenario_parts.append("회복 시도")
    if dynamics.get("breakdownAcceleration"):
        scenario_parts.append("하락 가속")
    if scenario_parts:
        parts.append("시나리오 " + ", ".join(scenario_parts))
    review_label = str(dynamics.get("reviewLabel") or dynamics.get("reviewLevelLabel") or "").strip()
    if review_label:
        parts.append("확인 단계 " + review_label)
    return " / ".join(parts[:6])


def sanitized_prompt_data(value: object, depth: int = 0, max_items: int = 40) -> object:
    if depth > 5:
        return "[omitted-depth]"
    if isinstance(value, dict):
        result: Dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["_truncated"] = True
                break
            text_key = str(key or "")
            normalized = text_key.lower().replace("-", "_")
            if any(term in normalized for term in SENSITIVE_PROMPT_KEY_TERMS):
                result[text_key] = "[redacted]"
                continue
            result[text_key] = sanitized_prompt_data(item, depth + 1, max_items)
        return result
    if isinstance(value, list):
        rows = [sanitized_prompt_data(item, depth + 1, max_items) for item in value[:max_items]]
        if len(value) > max_items:
            rows.append({"_truncated": True, "omitted": len(value) - max_items})
        return rows
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return compact_text(value, 300)


def news_summary_text(context: Dict[str, object]) -> str:
    disclosure = disclosure_context(context)
    news_items = selected_news_headline_items(context, 2)
    evidence_items = selected_research_evidence_items(context, 4)
    parts: List[str] = []
    if disclosure:
        report = str(disclosure.get("reportName") or disclosure.get("report_name") or "").strip()
        receipt_date = str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or "").strip()
        if report:
            parts.append("OpenDART " + report + (", 접수일 " + receipt_date if receipt_date else ""))
    for item in news_items[:2]:
        domain = str(item.get("domain") or item.get("provider") or "뉴스").strip()
        seen_date = str(item.get("seenDate") or item.get("seendate") or "").strip()
        suffix = (" · " + seen_date) if seen_date else ""
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        summary = news_summary_candidate(item)
        impact = str(item.get("stockImpactLabel") or payload.get("stockImpactLabel") or "").strip()
        prefix = (impact + " · ") if impact else ""
        parts.append(domain + ": " + prefix + compact_text(summary, 110) + suffix)
    for item in evidence_items:
        kind = str(item.get("kind") or "").strip()
        if kind == "news":
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            source = str(item.get("source") or "뉴스").strip()
            summary = news_summary_candidate(item)
            impact = str(item.get("stockImpactLabel") or payload.get("stockImpactLabel") or "").strip()
            observed = str(item.get("publishedAt") or item.get("observedAt") or "").strip()
            suffix = (" · " + observed) if observed else ""
            if summary:
                parts.append(source + ": " + ((impact + " · ") if impact else "") + compact_text(summary, 110) + suffix)
            continue
        if kind == "disclosure":
            continue
        source = str(item.get("source") or "리서치").strip()
        title = compact_text(item.get("title") or item.get("summary"), 84)
        observed = str(item.get("observedAt") or "").strip()
        suffix = (" · " + observed) if observed else ""
        if title:
            parts.append(source + ": " + title + suffix)
    unique: List[str] = []
    for item in parts:
        if item and item not in unique:
            unique.append(item)
    return " / ".join(unique[:4])


def disclosure_analysis_opinion_lines(context: Dict[str, object]) -> List[str]:
    disclosure = disclosure_context(context)
    if not disclosure:
        return []
    report = str(disclosure.get("reportName") or disclosure.get("report_name") or "").strip()
    receipt_date = str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or "").strip()
    provider = str(disclosure.get("provider") or "OpenDART").strip()
    analysis_context = {
        "title": target_label(context),
        "symbol": str(context.get("symbol") or "").strip(),
        "metadata": disclosure,
        "rawLines": "\n".join(line for line in [
            "신규 공시 감지",
            report,
            "접수일 " + receipt_date if receipt_date else "",
            "출처 " + provider if provider else "",
        ] if line),
    }
    result = local_disclosure_analysis(analysis_context)
    lines: List[str] = []
    for line in result.lines[:4]:
        label, _, value = str(line or "").partition(": ")
        if label and value:
            lines.append("공시 " + label + ": " + value)
        elif str(line or "").strip():
            lines.append("공시 해석: " + str(line).strip())
    return lines


def target_label(context: Dict[str, object]) -> str:
    return str(
        context.get("displayTarget")
        or context.get("target")
        or context.get("title")
        or context.get("symbol")
        or "이 알림"
    ).strip()


def notification_ai_prompt_context(
    message_type: str,
    context: Dict[str, object],
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    settings = settings or {}
    template = prompt_template_for_message_type(message_type, settings)
    policy = str(settings.get("aiPromptPolicy") or default_ai_prompt_policy_text()).strip()
    relation_context = relation_context_value(context)
    all_data = sanitized_prompt_data(context)
    active_opinion = active_investment_opinion_value(context)
    execution_plan = execution_plan_value(context)
    delivery_profile = context.get("messageDeliveryProfile") if isinstance(context.get("messageDeliveryProfile"), dict) else {}
    if not delivery_profile:
        delivery_profile = message_delivery_profile(context.get("messageDeliveryLevel") if "messageDeliveryLevel" in context else "intermediate")
    return {
        "promptVersion": template.version,
        "promptRegistryVersion": AI_PROMPT_REGISTRY_VERSION,
        "promptId": template.prompt_id,
        "promptTemplate": template.to_dict(),
        "promptPolicy": policy,
        "guardrails": list(template.guardrails),
        "facts": {
            "messageType": str(message_type or ""),
            "target": target_label(context),
            "severity": str(context.get("severityLabel") or context.get("severity") or ""),
            "rawLines": context_raw_lines(context),
            "criteria": criterion_lines(context),
            "relationRules": relation_labels(context),
            "missingData": missing_data_labels(context),
            "newsHeadlines": [
                {
                    "title": str(item.get("title") or ""),
                    "domain": str(item.get("domain") or ""),
                    "summary": str(item.get("articleSummaryKo") or item.get("summary") or ""),
                    "stockImpactLabel": str(item.get("stockImpactLabel") or ""),
                    "stockImpactReasonKo": str(item.get("stockImpactReasonKo") or ""),
                    "seenDate": str(item.get("seenDate") or item.get("seendate") or ""),
                }
                for item in selected_news_headline_items(context, 3)
            ],
            "disclosure": disclosure_context(context),
            "researchEvidence": sanitized_prompt_data(selected_research_evidence_items(context, 8)),
            "referenceDate": str(context.get("referenceDate") or ""),
            "activeRules": active_rule_items(context),
            "relationFacts": sanitized_prompt_data(relation_context.get("facts") if isinstance(relation_context, dict) else {}),
            "evidenceSubgraph": sanitized_prompt_data(relation_context.get("evidenceSubgraph") if isinstance(relation_context, dict) else {}),
            "trendDynamics": sanitized_prompt_data(relation_trend_dynamics(context)),
            "activeInvestmentOpinion": sanitized_prompt_data(active_opinion),
            "executionPlan": sanitized_prompt_data(execution_plan),
            "messageDeliveryProfile": sanitized_prompt_data(delivery_profile),
            "sourceAlertEvents": sanitized_prompt_data(source_alert_event_items(context)),
            "allAvailableData": all_data,
        },
    }


def generic_opinion(context: Dict[str, object], lines: List[str]) -> List[str]:
    message_type = str(context.get("messageType") or context.get("rule") or "").strip()
    label = MESSAGE_TYPE_LABELS.get(message_type, message_type or "알림")
    signal = first_line_with(lines, "상태", "신호", "변화", "현재", "가격", "수급", "추세")
    summary = label + " 조건이 감지됐습니다."
    if signal:
        summary += " 핵심 신호는 " + signal + "입니다."
    return [
        "해석: " + summary,
        "의견: 단일 신호로 결론내리기보다 가격, 수급, 추세가 같은 방향인지 확인하는 게 우선입니다.",
        "다음 확인: 발송 기준에 걸린 값이 다음 조회에서도 유지되는지 보고 반복 알림이면 기준값을 조정하세요.",
    ]


def opinion_lines_for_type(message_type: str, context: Dict[str, object]) -> List[str]:
    lines = context_raw_lines(context)
    action = line_value(lines, "권장 액션")
    state = line_value(lines, "상태")
    signal = line_value(lines, "신호")
    trend = line_value(lines, "추세")
    flow = line_value(lines, "수급")
    pnl = line_value(lines, "수익률") or line_value(lines, "손익")
    missing = missing_data_labels(context)
    rules = relation_labels(context)
    target = target_label(context)
    active_opinion = active_investment_opinion_value(context)
    active_label = str(active_opinion.get("actionLabel") or active_opinion.get("action") or "").strip()
    active_review_label = str(active_opinion.get("reviewLevelLabel") or "").strip()
    active_data_label = str(active_opinion.get("dataStateLabel") or "").strip()
    active_thesis = str(active_opinion.get("thesis") or "").strip()
    active_next_check = str(active_opinion.get("nextCheck") or "").strip()
    active_invalidation = str(active_opinion.get("invalidationCondition") or "").strip()
    active_evidence = active_opinion_evidence_text(active_opinion, "evidence") if active_opinion else ""
    active_counter = active_opinion_evidence_text(active_opinion, "counterEvidence") if active_opinion else ""
    execution_plan = execution_plan_value(context)
    primary_action = str(execution_plan.get("primaryActionLabel") or "").strip()
    plan_next = execution_plan_text(execution_plan, "nextChecks", 2)
    plan_blocked = execution_plan_text(execution_plan, "blockedActions", 2)
    plan_counter = execution_plan_text(execution_plan, "counterSignals", 2)

    if message_type == "investmentInsight":
        if not has_graph_backed_relation_context(context or {}):
            return []
        insight = context.get("ontologyInsight") if isinstance(context, dict) else {}
        if not isinstance(insight, dict):
            insight = {}
        insight_label = str(insight.get("insightLabel") or line_value(lines, "인사이트 유형") or "온톨로지 인사이트").strip()
        thesis = str(insight.get("thesis") or line_value(lines, "핵심 결론") or "").strip()
        next_check = str(insight.get("nextCheck") or line_value(lines, "다음 확인") or "").strip()
        action_line = line_value(lines, "권장 액션")
        risk_line = line_value(lines, "주요 리스크")
        trend_line = line_value(lines, "추세")
        flow_line = line_value(lines, "수급")
        investor_line = line_value(lines, "투자자")
        news_text = compact_news_summary_text(context)
        disclosure_lines = disclosure_analysis_opinion_lines(context)
        stance = active_label or "실행보다 관찰 우선"
        if "기회" in insight_label or "매수" in (action_line + thesis):
            stance = "소액 분할매수 검토"
        if any(term in (insight_label + thesis + action_line + risk_line) for term in ["리스크", "손실", "축소", "손절", "보류"]):
            stance = "추가매수 보류, 손실·비중 관리 우선"
        if any(term in (insight_label + thesis + action_line) for term in ["분할매도", "익절", "리밸런싱"]):
            stance = "분할매도·비중 조정 우선"
        if active_label:
            stance = active_label + (" · " + active_review_label if active_review_label else "")
            if primary_action and primary_action not in stance:
                stance += " · " + primary_action
        reason_text = investment_reason_text(
            target,
            insight_label,
            active_thesis or thesis,
            flow_line or investor_line,
            trend_line,
            context,
        )
        result = [
            "판단: " + user_facing_investment_text(stance, 80),
            "이유: " + reason_text,
        ]
        if active_evidence:
            result.append("근거: " + user_facing_investment_text(active_evidence, 110))
        counter_text = join_unique_segments(active_counter, plan_counter)
        if counter_text:
            result.append("반대 근거: " + user_facing_investment_text(counter_text, 110))
        elif risk_line:
            result.append("주의: " + user_facing_investment_text(risk_line, 110))
        if news_text:
            if news_text.startswith("공시:"):
                result.append("공시: " + news_text.split(":", 1)[1].strip())
            else:
                result.append("뉴스: " + news_text)
        result.extend(disclosure_lines[:2])
        opinion_text = active_label or action_line or stance
        if primary_action and primary_action not in opinion_text:
            opinion_text += " · " + primary_action
        if active_invalidation:
            opinion_text += ". 무효화 조건: " + active_invalidation
        if plan_blocked:
            result.append("피할 일: " + user_facing_investment_text(plan_blocked, 120))
        elif opinion_text:
            result.append("의견: " + user_facing_investment_text(opinion_text, 120))
        next_text = plan_next or active_next_check or next_check or "다음 조회에서도 같은 조건이 유지되는지 확인하세요."
        if active_invalidation and "무효화 조건" not in next_text:
            next_text += " / 무효화 조건: " + active_invalidation
        result.append("다음 확인: " + user_facing_investment_text(next_text, 140))
        return result
    if message_type == "holdingTiming":
        evidence = active_rule_evidence(context, 5)
        trend_dynamics_text = trend_dynamics_summary(context)
        rule_text = ", ".join(rules[:2]) if rules else (state or "보유 타이밍 조건")
        situation_parts = []
        if state:
            situation_parts.append(state)
        if pnl:
            situation_parts.append("수익률 " + pnl)
        if evidence:
            situation_parts.append("근거 " + ", ".join(evidence[:3]))
        elif trend:
            situation_parts.append("추세 " + trend)
        situation = target + "는 " + " · ".join(situation_parts) if situation_parts else target + "에서 " + rule_text + " 조건이 감지됐습니다."
        news_text = news_summary_text(context)
        next_check = active_next_check or action or "비중 확대 여부보다 손실 기준, 분할 대응 기준, 추세 회복 조건을 먼저 확인하세요."
        result = []
        if active_label:
            result.append("판단: " + active_label + (" · " + active_review_label if active_review_label else ""))
            if active_data_label:
                result.append("자료 상태: " + active_data_label)
        result.append("상황: " + situation)
        if active_thesis:
            result.append("투자 의견 근거: " + active_thesis)
        if active_evidence:
            result.append("근거: " + active_evidence)
        if active_counter:
            result.append("반대 근거: " + active_counter)
        if flow or trend:
            result.append("수급·추세: " + " / ".join(part for part in [flow, trend] if part))
        if trend_dynamics_text:
            result.append("추세 동역학: " + trend_dynamics_text)
        if line_value(lines, "투자자"):
            result.append("투자자: " + line_value(lines, "투자자"))
        if news_text:
            result.append("뉴스·공시: " + news_text)
        result.extend(disclosure_analysis_opinion_lines(context))
        external_phrase = "뉴스/공시" if news_text else "다음 조회 데이터"
        opinion_text = active_label or action or (state or "보유 판단") + " 기준을 우선 확인"
        if active_invalidation:
            opinion_text += ". 무효화 조건: " + active_invalidation
        result.extend([
            "의견: " + opinion_text + ". 가격·수급·추세와 " + external_phrase + "가 같은 방향인지 확인한 뒤 대응 강도를 정하세요.",
            "다음 확인: " + next_check,
        ])
        return result
    if message_type == "ontologyInferenceMissing":
        reason = line_value(lines, "원인")
        inference_status = line_value(lines, "추론 상태")
        failure_stage = line_value(lines, "실패 단계")
        failure_detail = line_value(lines, "실패 상세")
        detail_sentence = ""
        if failure_stage or failure_detail:
            detail_sentence = " 실패 단계: " + (failure_stage or "확인 필요")
            if failure_detail:
                detail_sentence += ". 상세: " + failure_detail
        return [
            "해석: 실계좌 데이터는 있지만 온톨로지 추론 결과가 없어 매수·매도 판단을 만들지 않았습니다.",
            "의견: 투자 신호가 아니라 판단 엔진의 필수 데이터 누락 알림입니다. " + (reason or "InferenceBox 상태를 먼저 확인해야 합니다.") + detail_sentence,
            "다음 확인: " + (inference_status + " 확인 후 " if inference_status else "") + "TypeDB 연결, 네이티브 규칙 저장 상태, 온톨로지 추론 워커를 순서대로 점검하세요.",
        ]
    if message_type == "monitorDecisionChange":
        previous_value = line_value(lines, "이전")
        current_value = line_value(lines, "현재")
        result = []
        if active_label:
            result.append("판단: " + active_label + (" · " + active_review_label if active_review_label else ""))
        result.extend([
            "해석: 판단명이 바뀐 알림입니다. " + " -> ".join(part for part in [previous_value, current_value] if part),
        ])
        if active_thesis:
            result.append("투자 의견 근거: " + active_thesis)
        if active_counter:
            result.append("반대 근거: " + active_counter)
        result.extend([
            "의견: " + (active_label or "표시된 단계만 보지 말고 어떤 규칙과 근거 조합이 새로 성립했는지 먼저 확인해야 합니다.") + (". 무효화 조건: " + active_invalidation if active_invalidation else ""),
            "다음 확인: " + (active_next_check or "같은 판단이 다음 조회에서도 유지되는지, 임계값 근처 흔들림인지 구분하세요."),
        ])
        return result
    if message_type in {"modelBuy", "watchlistBuyCandidate"}:
        return [
            "해석: 매수 후보 기준을 통과했습니다.",
            "의견: 분할매수 후보로 볼 수 있습니다. 첫 진입은 작게 두고 손절 기준과 추가매수 조건을 먼저 정하세요.",
            "다음 확인: " + (flow or trend or "거래량, 수급, 20일선 위치가 매수 방향과 같이 움직이는지 확인하세요."),
        ]
    if message_type == "modelSell":
        return [
            "해석: 매도 압력 기준을 통과했습니다.",
            "의견: 전량 매도 결론보다 분할매도, 손절, 보유 유지 중 어떤 규칙이 실제로 성립했는지 나눠 봐야 합니다.",
            "다음 확인: " + (pnl or trend or "목표 수익률, 손실 기준, 추세 이탈 여부를 함께 확인하세요."),
        ]
    if message_type == "monitorTrendChange":
        return [
            "해석: 이동평균과 현재가의 관계가 바뀌었습니다. " + (signal or trend or ""),
            "의견: 추세 알림은 방향 신호입니다. 거래량과 투자자 수급이 같이 움직이면 근거가 보강되고, 그렇지 않으면 일시적 흔들림일 수 있습니다.",
            "다음 확인: 20일선 회복/이탈이 다음 봉에서도 유지되는지와 거래량 배율을 같이 보세요.",
        ]
    if message_type == "monitorPnlChange":
        return [
            "해석: 손익률 변화폭이 기준을 넘었습니다.",
            "의견: 손익률이 좋아졌다면 수익 보호 기준을, 나빠졌다면 손실 확대 방어 기준을 먼저 점검해야 합니다.",
            "다음 확인: 현재가와 평균매입가 차이, 변화가 가격 때문인지 환율/수량 변화 때문인지 확인하세요.",
        ]
    if message_type == "monitorValueChange":
        return [
            "해석: 평가액 변화폭이 기준을 넘었습니다.",
            "의견: 평가액 알림은 포트폴리오 영향 신호입니다. 가격 변화와 보유 수량 변화가 섞였는지 분리해서 봐야 합니다.",
            "다음 확인: 평가액 변화가 특정 종목 집중 때문인지 시장 전체 움직임 때문인지 확인하세요.",
        ]
    if message_type == "monitorPositionChange":
        return [
            "해석: 보유 수량이 직전 스냅샷과 달라졌습니다.",
            "의견: 의도한 매매가 계좌에 반영됐는지, 평균매입가와 비중이 계획과 맞는지 확인하는 알림입니다.",
            "다음 확인: 주문 체결 내역, 매도 가능 수량, 새 평균매입가를 함께 확인하세요.",
        ]
    if message_type == "monitorCashChange":
        return [
            "해석: 현금 비중이 크게 바뀌었습니다.",
            "의견: 현금 감소는 매수 여력 축소, 현금 증가는 방어력 확대 신호입니다. 시장별 목표 현금 비중과 비교하세요.",
            "다음 확인: 현금 변화가 주문 체결, 환전, 입출금 중 무엇 때문인지 확인하세요.",
        ]
    if message_type == "watchlistQuote":
        return [
            "해석: 관심종목 시세가 수집됐거나 크게 변했습니다.",
            "의견: 아직 보유 종목이 아니면 매수 후보 검토용 신호로만 보고, 추세와 거래량이 붙는지 기다리는 편이 낫습니다.",
            "다음 확인: 관심종목 매수 기준, 20일선 위치, 거래량 배율을 함께 확인하세요.",
        ]
    if message_type == "watchlistQuotePending":
        return [
            "해석: 관심종목 현재가가 아직 수집되지 않았습니다.",
            "의견: 이 종목은 모델 판단 신뢰도가 낮으니 매매 판단 전에 시세 연결부터 복구해야 합니다.",
            "다음 확인: 종목 코드, 토스 candles 응답, 허용 IP와 API 권한을 확인하세요.",
        ]
    if message_type == "monitorConnection":
        return [
            "해석: 데이터 연결 상태가 정상 live 흐름이 아닙니다.",
            "의견: 투자 판단보다 데이터 신뢰도 복구가 우선입니다. 토스 조회 실패는 3회 연속 확인된 뒤 키/권한 재점검 알림으로 보냅니다.",
            "다음 확인: 실패 단계, 재시도 결과, 3회 누적 전 정상 복구되는지 확인하세요.",
        ]
    if message_type == "monitorHeartbeat":
        return [
            "해석: 모니터링 워커 생존 확인 알림입니다.",
            "의견: 매매 판단 신호는 아니고, 데이터 수집과 알림 파이프라인이 살아 있는지 보는 상태 메시지입니다.",
            "다음 확인: 보유 수와 평가 데이터가 최근 기준일로 갱신되는지만 확인하세요.",
        ]
    if message_type == "externalEquityMove":
        return [
            "해석: 미국 주식 가격 또는 거래량 변화가 기준을 넘었습니다.",
            "의견: 단기 급변은 추격보다 보유 수익률, 거래량, 프리/정규장 구간을 나눠 보는 게 좋습니다.",
            "다음 확인: Alpha Vantage 기준일과 실제 장 시간, 보유 종목이면 평균매입가 대비 위치를 확인하세요.",
        ]
    if message_type == "externalCryptoMove":
        return [
            "해석: 크립토 변동이 기준을 넘었습니다.",
            "의견: BTC/ETH 움직임은 민감 종목 점검 신호이지 단독 매매 신호가 아닙니다.",
            "다음 확인: MSTR/STRC 같은 민감 종목의 가격 반응 시차와 BTC 7일 변동 유지 여부를 확인하세요.",
        ]
    if message_type == "externalMacroShift":
        return [
            "해석: 금리 또는 스프레드 변화가 기준을 넘었습니다.",
            "의견: 성장주와 장기 현금흐름 종목은 할인율 변화에 민감하니 가격보다 포트폴리오 노출을 먼저 확인하세요.",
            "다음 확인: 10년물, 2년물, 스프레드 변화가 며칠 지속되는지 확인하세요.",
        ]
    if message_type == "externalDartDisclosure":
        return [
            "해석: 보유 또는 추적 종목에 신규 공시가 감지됐습니다.",
            "의견: 공시는 제목만으로 결론 내리면 위험합니다. 규모, 목적, 대상자, 가격 반응을 원문에서 확인해야 합니다.",
            "다음 확인: 공시 원문과 접수번호, 장중 거래량 변화, 보유 수익률 기준 대응선을 함께 보세요.",
        ]
    if message_type == "externalDataConnection":
        pipeline_health = context.get("pipelineHealth") if isinstance(context.get("pipelineHealth"), dict) else {}
        state = str(context.get("apiStatus") or pipeline_health.get("state") or "").strip().lower()
        previous_state = str(pipeline_health.get("previousState") or "").strip().lower()
        signals = context.get("notificationSignals") if isinstance(context.get("notificationSignals"), list) else []
        recovered = "connectionRecovered" in signals or (
            state in {"healthy", "idle"} and previous_state in {"degraded", "failed", "stale"}
        )
        if recovered:
            target = str(context.get("displayTarget") or context.get("apiSource") or "외부 데이터 수집").strip()
            return [
                "해석: " + target + "이 정상화됐습니다.",
                "의견: 현재 수집과 공급자 상태가 정상이라 해당 데이터 기반 알림 신뢰도는 회복됐습니다.",
                "다음 확인: 다음 수집 주기에도 같은 정상 상태가 유지되는지만 확인하세요.",
            ]
        return [
            "해석: 외부 데이터 API 연결 문제가 감지됐습니다.",
            "의견: 해당 소스에 의존하는 알림은 일시적으로 신뢰도가 낮아질 수 있습니다.",
            "다음 확인: API 키, 호출 제한, 응답 형식, 마지막 성공 시각을 확인하세요.",
        ]

    result = generic_opinion(context, lines)
    if missing:
        result.append("부족 데이터: " + ", ".join(missing[:3]) + "는 판단에서 보수적으로 봐야 합니다.")
    return result


def raw_line_values(context: Dict[str, object]) -> set:
    values = set()
    for line in context_raw_lines(context or {}):
        text = normalized_text(line)
        if text:
            values.add(text)
        _, value = split_label_value_text(line)
        if value:
            values.add(normalized_text(value))
    return values


def compact_opinion_line(line: object) -> str:
    label, value = split_label_value_text(line)
    if not label:
        return compact_text(line, AI_OPINION_MAX_CHARS)
    if label in NOISY_AI_LABELS:
        return ""
    if label == "투자 의견 근거":
        label = "이유"
    if label == "뉴스·공시" and str(value or "").startswith("공시:"):
        label = "공시"
        value = value.split(":", 1)[1].strip()
    value = user_facing_investment_text(value, AI_OPINION_MAX_CHARS - len(label) - 2)
    if not value:
        return ""
    return label + ": " + value


def line_priority(line: str, index: int) -> int:
    label, _ = split_label_value_text(line)
    return AI_LABEL_PRIORITY.get(label, 200) * 100 + index


def remove_repeated_opinion_lines(lines: List[str], context: Dict[str, object]) -> List[str]:
    raw_values = raw_line_values(context or {})
    if not raw_values:
        return lines
    rich_context = has_rich_opinion_context(context or {})
    rows: List[str] = []
    for line in lines:
        label, value = split_label_value_text(line)
        normalized_value = normalized_text(value)
        if label in REPEATED_VALUE_LABELS and normalized_value in raw_values and not (rich_context and label in {"이유", "상황", "해석", "근거"}):
            continue
        if normalized_text(line) in raw_values:
            continue
        rows.append(line)
    return rows


def apply_opinion_budget(lines: List[str]) -> List[str]:
    unique: List[str] = []
    for line in lines:
        text = compact_opinion_line(line)
        if text and text not in unique:
            unique.append(text)
    unique = merge_related_opinion_lines(unique)
    if len(unique) <= AI_OPINION_MAX_LINES:
        return unique
    selected = sorted(enumerate(unique), key=lambda pair: line_priority(pair[1], pair[0]))[:AI_OPINION_MAX_LINES]
    return [line for _, line in sorted(selected, key=lambda pair: pair[0])]


def merge_related_opinion_lines(lines: List[str]) -> List[str]:
    rows: List[str] = []
    pending_disclosure_meaning = ""
    for line in lines:
        label, value = split_label_value_text(line)
        if label == "공시 의미":
            pending_disclosure_meaning = value
            continue
        if label == "공시 영향" and pending_disclosure_meaning:
            merged = "공시 의미: " + compact_text(pending_disclosure_meaning + " / " + value, AI_OPINION_MAX_CHARS - 7)
            rows.append(merged)
            pending_disclosure_meaning = ""
            continue
        if pending_disclosure_meaning:
            rows.append("공시 의미: " + pending_disclosure_meaning)
            pending_disclosure_meaning = ""
        rows.append(line)
    if pending_disclosure_meaning:
        rows.append("공시 의미: " + pending_disclosure_meaning)
    return rows


def has_actionable_opinion(lines: List[str]) -> bool:
    actionable_labels = {"판단", "이유", "상황", "해석", "반대 근거", "주의", "뉴스", "뉴스·공시", "공시", "피할 일", "의견", "다음 확인", "부족 데이터"}
    for line in lines:
        label, value = split_label_value_text(line)
        if label in actionable_labels and str(value or "").strip():
            return True
    return False


def has_rich_opinion_context(context: Dict[str, object]) -> bool:
    if active_investment_opinion_value(context) or execution_plan_value(context):
        return True
    if missing_data_labels(context) or disclosure_context(context):
        return True
    if selected_news_headline_items(context, 1) or selected_research_evidence_items(context, 1):
        return True
    return False


def postprocess_opinion_lines(message_type: str, context: Dict[str, object], lines: List[str]) -> List[str]:
    compacted = apply_opinion_budget(lines or [])
    compacted = remove_repeated_opinion_lines(compacted, context or {})
    compacted = apply_opinion_budget(compacted)
    if not compacted or not has_actionable_opinion(compacted):
        return []
    labels = {split_label_value_text(line)[0] for line in compacted}
    if message_type == "investmentInsight" and not has_rich_opinion_context(context or {}) and labels.issubset({"판단", "의견", "다음 확인"}):
        return []
    if len(compacted) <= 2 and not has_rich_opinion_context(context or {}):
        return []
    return compacted


def build_notification_ai_opinion(
    context: Dict[str, object],
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    message_type = str((context or {}).get("messageType") or (context or {}).get("rule") or "").strip() or "default"
    if message_type in SKIP_AI_OPINION_TYPES:
        return {}
    prompt_context = notification_ai_prompt_context(message_type, context or {}, settings)
    lines = opinion_lines_for_type(message_type, context or {})
    if missing_data_labels(context or {}) and not any(line.startswith("부족 데이터:") for line in lines):
        lines.append("부족 데이터: " + ", ".join(missing_data_labels(context or {})[:3]) + "는 결론 강도를 낮추는 요소입니다.")
    lines = postprocess_opinion_lines(message_type, context or {}, lines)
    if not lines:
        return {}
    return {
        "engineVersion": AI_OPINION_ENGINE_VERSION,
        "messageType": message_type,
        "source": "알림 AI 의견",
        "lines": lines,
        "promptContext": prompt_context,
    }


def enrich_notification_ai_context(
    context: Dict[str, object],
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    enriched = dict(context or {})
    if enriched.get("notificationAiOpinion"):
        return enriched
    opinion = build_notification_ai_opinion(enriched, settings)
    if not opinion:
        return enriched
    enriched["notificationAiOpinion"] = opinion
    enriched["notificationAiPromptContext"] = opinion.get("promptContext") or {}
    enriched.setdefault("ontologyPromptContext", opinion.get("promptContext") or {})
    return enriched
