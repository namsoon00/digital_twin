from typing import Dict, List, Optional

from .message_types import MESSAGE_TYPE_LABELS
from .disclosure_analysis import local_disclosure_analysis
from .ontology_rules import (
    AI_PROMPT_REGISTRY_VERSION,
    default_ai_prompt_policy_text,
    prompt_template_for_message_type,
)


SKIP_AI_OPINION_TYPES = {"workHandoff", "modelReview"}
AI_OPINION_ENGINE_VERSION = "notification-ai-opinion-v1"
SENSITIVE_PROMPT_KEY_TERMS = ("secret", "token", "password", "clientid", "client_id", "appsecret", "app_key", "apikey", "api_key", "accountseq", "account_seq", "chatid", "chat_id")


def context_raw_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("rawLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    if raw:
        return [line.strip() for line in str(raw or "").splitlines() if line.strip()]
    lines = context.get("lines") if isinstance(context, dict) else ""
    if isinstance(lines, list):
        return [str(item or "").strip() for item in lines if str(item or "").strip()]
    return [
        line.strip().lstrip("-").strip()
        for line in str(lines or "").splitlines()
        if line.strip()
    ]


def criterion_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("criterionLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def line_value(lines: List[str], label: str) -> str:
    prefix = str(label or "").strip()
    if not prefix:
        return ""
    for raw in lines:
        line = str(raw or "").strip()
        if line.startswith(prefix + ":"):
            return line.split(":", 1)[1].strip()
        if line.startswith(prefix + " "):
            return line[len(prefix):].strip()
    return ""


def first_line_with(lines: List[str], *labels: str) -> str:
    for label in labels:
        value = line_value(lines, label)
        if value:
            return label + " " + value
    for line in lines:
        if str(line or "").strip():
            return str(line).strip()
    return ""


def relation_labels(context: Dict[str, object]) -> List[str]:
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if not relation_context:
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        relation_context = metadata.get("ontologyRelationContext") if isinstance(metadata.get("ontologyRelationContext"), dict) else {}
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    labels: List[str] = []
    for item in rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
        if label:
            labels.append(label)
    return labels


def missing_data_labels(context: Dict[str, object]) -> List[str]:
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if not relation_context:
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        relation_context = metadata.get("ontologyRelationContext") if isinstance(metadata.get("ontologyRelationContext"), dict) else {}
    missing = relation_context.get("missingData") if isinstance(relation_context, dict) else []
    labels: List[str] = []
    if isinstance(missing, list):
        for item in missing:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("key") or "").strip()
            else:
                label = str(item or "").strip()
            if label:
                labels.append(label)
    return labels


def relation_context_value(context: Dict[str, object]) -> Dict[str, object]:
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if relation_context:
        return relation_context
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    relation_context = metadata.get("ontologyRelationContext") if isinstance(metadata.get("ontologyRelationContext"), dict) else {}
    return relation_context if isinstance(relation_context, dict) else {}


def source_alert_event_items(context: Dict[str, object]) -> List[Dict[str, object]]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    raw_items = metadata.get("sourceAlertEvents") or context.get("sourceAlertEvents") or []
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def active_investment_opinion_value(context: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context, dict):
        return {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    containers = [
        context,
        metadata,
        context.get("ontologyReviewContext") if isinstance(context.get("ontologyReviewContext"), dict) else {},
        metadata.get("ontologyReviewContext") if isinstance(metadata.get("ontologyReviewContext"), dict) else {},
        context.get("aiContext") if isinstance(context.get("aiContext"), dict) else {},
        metadata.get("aiContext") if isinstance(metadata.get("aiContext"), dict) else {},
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("activeInvestmentOpinion", "active_investment_opinion"):
            value = container.get(key)
            if isinstance(value, dict) and value:
                return value
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item:
                        return item
    for event in source_alert_event_items(context):
        event_metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        for container in (event, event_metadata):
            value = container.get("activeInvestmentOpinion") if isinstance(container, dict) else {}
            if isinstance(value, dict) and value:
                return value
    return {}


def relation_facts(context: Dict[str, object]) -> Dict[str, object]:
    facts = relation_context_value(context).get("facts")
    return facts if isinstance(facts, dict) else {}


def relation_trend_dynamics(context: Dict[str, object]) -> Dict[str, object]:
    facts = relation_facts(context)
    dynamics = facts.get("trendDynamics") if isinstance(facts.get("trendDynamics"), dict) else {}
    return dynamics if isinstance(dynamics, dict) else {}


def active_rule_items(context: Dict[str, object]) -> List[Dict[str, object]]:
    relation_context = relation_context_value(context)
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    return [
        item for item in rules
        if isinstance(item, dict) and not item.get("referenceOnly") and not item.get("reference_only")
    ]


def active_rule_evidence(context: Dict[str, object], limit: int = 5) -> List[str]:
    evidence: List[str] = []
    for item in active_rule_items(context):
        raw = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        for value in raw:
            text = str(value or "").strip()
            if text and text not in evidence:
                evidence.append(text)
            if len(evidence) >= limit:
                return evidence
    return evidence


def disclosure_context(context: Dict[str, object]) -> Dict[str, object]:
    facts = relation_facts(context)
    disclosure = facts.get("dartDisclosure") if isinstance(facts.get("dartDisclosure"), dict) else {}
    if disclosure:
        return disclosure
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    for key in ["dartDisclosure", "disclosure"]:
        value = metadata.get(key) if isinstance(metadata.get(key), dict) else {}
        if value:
            return value
    return {}


def news_headline_items(context: Dict[str, object]) -> List[Dict[str, object]]:
    facts = relation_facts(context)
    news = facts.get("newsHeadlines") if isinstance(facts.get("newsHeadlines"), dict) else {}
    if not news:
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        news = metadata.get("newsHeadlines") if isinstance(metadata.get("newsHeadlines"), dict) else {}
    raw_items = news.get("items") if isinstance(news, dict) and isinstance(news.get("items"), list) else []
    return [item for item in raw_items if isinstance(item, dict) and str(item.get("title") or "").strip()]


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
        title = str(item.get("title") or item.get("summary") or item.get("source") or "").strip()
        if title and title not in titles:
            titles.append(compact_text(title, 72))
        if len(titles) >= limit:
            break
    return " / ".join(titles)


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
    if dynamics.get("dynamicRiskScore") not in (None, ""):
        parts.append("동역학 리스크 " + str(dynamics.get("dynamicRiskScore")) + "점")
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
    news_items = news_headline_items(context)
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
        parts.append(domain + ": " + compact_text(item.get("title"), 84) + suffix)
    return " / ".join(parts[:3])


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
                    "seenDate": str(item.get("seenDate") or item.get("seendate") or ""),
                }
                for item in news_headline_items(context)[:3]
            ],
            "disclosure": disclosure_context(context),
            "referenceDate": str(context.get("referenceDate") or ""),
            "activeRules": active_rule_items(context),
            "relationFacts": sanitized_prompt_data(relation_context.get("facts") if isinstance(relation_context, dict) else {}),
            "trendDynamics": sanitized_prompt_data(relation_trend_dynamics(context)),
            "activeInvestmentOpinion": sanitized_prompt_data(active_opinion),
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
    active_conviction = active_opinion.get("conviction")
    active_thesis = str(active_opinion.get("thesis") or "").strip()
    active_next_check = str(active_opinion.get("nextCheck") or "").strip()
    active_invalidation = str(active_opinion.get("invalidationCondition") or "").strip()
    active_evidence = active_opinion_evidence_text(active_opinion, "evidence") if active_opinion else ""
    active_counter = active_opinion_evidence_text(active_opinion, "counterEvidence") if active_opinion else ""

    if message_type == "investmentInsight":
        insight = context.get("ontologyInsight") if isinstance(context, dict) else {}
        if not isinstance(insight, dict):
            insight = {}
        insight_label = str(insight.get("insightLabel") or line_value(lines, "인사이트 유형") or "온톨로지 인사이트").strip()
        thesis = str(insight.get("thesis") or line_value(lines, "핵심 결론") or "").strip()
        next_check = str(insight.get("nextCheck") or line_value(lines, "다음 확인") or "").strip()
        current_price = line_value(lines, "현재가")
        average_price = line_value(lines, "평단가")
        return_rate = line_value(lines, "수익률") or line_value(lines, "손익")
        action_line = line_value(lines, "권장 액션")
        risk_line = line_value(lines, "주요 리스크")
        trend_line = line_value(lines, "추세")
        flow_line = line_value(lines, "수급")
        investor_line = line_value(lines, "투자자")
        trend_dynamics_text = trend_dynamics_summary(context)
        news_text = news_summary_text(context)
        disclosure_lines = disclosure_analysis_opinion_lines(context)
        source_types = insight.get("sourceSignalTypes") or context.get("sourceSignalTypes") if isinstance(context, dict) else []
        if isinstance(source_types, list):
            source_labels = [MESSAGE_TYPE_LABELS.get(str(item), str(item)) for item in source_types[:5]]
        else:
            source_labels = []
        source_text = ", ".join(source_labels) if source_labels else (line_value(lines, "근거 신호") or "관계 신호")
        stance = active_label or "실행보다 관찰 우선"
        if "기회" in insight_label or "매수" in (action_line + thesis):
            stance = "소액 분할매수 검토"
        if any(term in (insight_label + thesis + action_line + risk_line) for term in ["리스크", "손실", "축소", "손절", "보류"]):
            stance = "추가매수 보류, 손실·비중 관리 우선"
        if any(term in (insight_label + thesis + action_line) for term in ["분할매도", "익절", "리밸런싱"]):
            stance = "분할매도·비중 조정 우선"
        if active_label:
            stance = active_label + (" · 확신 " + str(active_conviction) + "%" if active_conviction not in (None, "") else "")
        summary_bits = [part for part in [
            ("현재가 " + current_price) if current_price else "",
            ("평단가 " + average_price) if average_price else "",
            ("수익률 " + return_rate) if return_rate else "",
        ] if part]
        result = [
            "판단: " + stance,
            "해석: " + target + "의 " + insight_label + "입니다. " + (thesis or "가격·수급·추세·외부 신호가 하나의 인사이트로 합성됐습니다."),
        ]
        if active_thesis:
            result.append("투자 의견 근거: " + active_thesis)
        if summary_bits:
            result.append("가격 위치: " + ", ".join(summary_bits))
        if active_evidence:
            result.append("근거: " + active_evidence)
        elif flow_line or investor_line or trend_line:
            result.append("근거: " + " / ".join(part for part in [flow_line, investor_line, trend_line] if part))
        if active_counter:
            result.append("반대 근거: " + active_counter)
        if trend_dynamics_text:
            result.append("추세 동역학: " + trend_dynamics_text)
        if risk_line:
            result.append("주의: " + risk_line)
        if news_text:
            result.append("뉴스·공시: " + news_text)
        result.extend(disclosure_lines)
        opinion_text = active_label or action_line or stance
        if active_invalidation:
            opinion_text += ". 무효화 조건: " + active_invalidation
        result.extend([
            "의견: " + opinion_text + ". " + source_text + "가 같은 방향으로 유지되는지 확인하고, 반대 신호가 있으면 실행 강도를 낮추세요.",
            "다음 확인: " + (active_next_check or next_check or "다음 조회에서도 같은 관계 규칙이 유지되는지, 뉴스·공시 원문에 반대 근거가 있는지 확인하세요."),
        ])
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
            result.append("판단: " + active_label + (" · 확신 " + str(active_conviction) + "%" if active_conviction not in (None, "") else ""))
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
    if message_type == "monitorDecisionChange":
        previous_value = line_value(lines, "이전")
        current_value = line_value(lines, "현재")
        result = []
        if active_label:
            result.append("판단: " + active_label + (" · 확신 " + str(active_conviction) + "%" if active_conviction not in (None, "") else ""))
        result.extend([
            "해석: 판단명이 바뀐 알림입니다. " + " -> ".join(part for part in [previous_value, current_value] if part),
        ])
        if active_thesis:
            result.append("투자 의견 근거: " + active_thesis)
        if active_counter:
            result.append("반대 근거: " + active_counter)
        result.extend([
            "의견: " + (active_label or "점수 변화만 보지 말고 선택 규칙과 성립 규칙 조합이 바뀌었는지 먼저 확인해야 합니다.") + (". 무효화 조건: " + active_invalidation if active_invalidation else ""),
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
            "의견: 추세 알림은 방향 신호입니다. 거래량과 투자자 수급이 같이 붙으면 신뢰도가 올라가고, 없으면 노이즈 가능성이 남습니다.",
            "다음 확인: 20일선 회복/이탈이 다음 봉에서도 유지되는지와 거래량 배율을 같이 보세요.",
        ]
    if message_type == "monitorPnlChange":
        return [
            "해석: 손익률 변화폭이 기준을 넘었습니다.",
            "의견: 손익률이 좋아졌다면 수익 보호 기준을, 나빠졌다면 손실 확대 방어 기준을 먼저 점검해야 합니다.",
            "다음 확인: 현재가와 평단가 차이, 변화가 가격 때문인지 환율/수량 변화 때문인지 확인하세요.",
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
            "의견: 의도한 매매가 계좌에 반영됐는지, 평단가와 비중이 계획과 맞는지 확인하는 알림입니다.",
            "다음 확인: 주문 체결 내역, 매도 가능 수량, 새 평단가를 함께 확인하세요.",
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
            "의견: 투자 판단보다 데이터 신뢰도 복구가 우선입니다. 1회성 실패는 관찰, 반복 실패는 키/권한 재점검 대상입니다.",
            "다음 확인: 실패 단계, 재시도 결과, 다음 주기에서 정상 복구되는지 확인하세요.",
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
            "다음 확인: Alpha Vantage 기준일과 실제 장 시간, 보유 종목이면 평단가 대비 위치를 확인하세요.",
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
        return [
            "해석: 외부 데이터 API 연결 문제가 감지됐습니다.",
            "의견: 해당 소스에 의존하는 알림은 일시적으로 신뢰도가 낮아질 수 있습니다.",
            "다음 확인: API 키, 호출 제한, 응답 형식, 마지막 성공 시각을 확인하세요.",
        ]

    result = generic_opinion(context, lines)
    if missing:
        result.append("부족 데이터: " + ", ".join(missing[:3]) + "는 판단에서 보수적으로 봐야 합니다.")
    return result


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
    lines.append("분석출처: 알림 AI 의견 / " + str(prompt_context.get("promptId") or message_type))
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
