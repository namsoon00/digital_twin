import html
import re
from dataclasses import asdict, dataclass
from typing import Dict, List

from .alert_formatting import signed_pct
from .message_types import MESSAGE_TYPE_EMOJIS, MESSAGE_TYPE_LABELS, TRIGGER_SUMMARIES
from .notification_ai import enrich_notification_ai_context
from .notification_ontology_sections import (
    CURVE_REGIME_LABELS,
    FX_REGIME_LABELS,
    RATE_REGIME_LABELS,
    ai_prompt_lines,
    append_unique_lines,
    block_from_lines,
    fact_number,
    fact_number_text,
    fx_fact_line,
    has_fact_value,
    macro_context_lines,
    missing_data_lines,
    notification_ai_opinion_lines,
    notification_ai_opinion_payload,
    notification_data_lines,
    ontology_missing_data,
    ontology_prompt_context,
    ontology_relation_context,
    ontology_relation_contexts,
    ontology_rule_lines,
    rate_fact_line,
    rule_value,
    source_fact_rows,
    telegram_block_from_lines,
)
from .notification_text_formatting import (
    BEGINNER_FRIENDLY_REPLACEMENTS,
    DATA_LABEL_ORDER,
    DATA_LABEL_PREFIXES,
    FOOTER_DATA_LABELS,
    ONTOLOGY_INTERNAL_DATA_PREFIXES,
    SEPARATE_DATA_LABELS,
    beginner_friendly_text,
    criterion_row,
    criterion_rows,
    data_pair_text,
    data_value,
    dominant_signed_direction,
    first_data_text,
    format_score_value,
    formatted_data_rows,
    grouped_data_rows,
    html_bullet,
    is_ontology_internal_data_line,
    ordered_data_entries,
    plain_bullet,
    plain_data_rows,
    signed_direction,
    split_data_line,
    split_label_value,
    telegram_data_rows,
    text_parts_from_value,
    title_from_change,
)
from .notification_title_rules import (
    compact_action_title,
    current_utc_iso,
    event_criterion_lines,
    event_generated_at,
    first_line_containing,
    has_entry_wait_signal,
    has_investment_loss_signal,
    has_investment_profit_signal,
    inferred_criterion_lines,
    investment_insight_decision_blob,
    investment_insight_is_holding,
    investment_insight_signal_blob,
    notification_title_headline,
    notification_title_icon,
    percent_text,
    raw_lines_with_reference_date,
    reference_date_text,
)
from .notifications import notification_debug_number
from .portfolio import AlertEvent
from .scoring import notification_signal_labels


LEGACY_DEFAULT_TEMPLATE = "{title}\n{lines}"
PREVIOUS_DEFAULT_TEMPLATE = "{readableMessage}"
DEFAULT_TEMPLATE = "{telegramMessage}"
BODY_TEMPLATE = "{body}"
MESSAGE_START_BADGE = "🔔 새 알림"
SYMBOL_DISPLAY_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "035720": "카카오",
    "005380": "현대차",
    "000020": "동화약품",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "TSLA": "Tesla",
    "MSTR": "Strategy",
    "STRC": "Strategy Preferred",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "BTC": "비트코인",
    "ETH": "이더리움",
}

SEVERITY_LABELS = {
    "INFO": "정보",
    "WATCH": "관찰",
    "ALERT": "주의",
}

SCORE_EXPLANATION_SKIP_TYPES = {
    "workHandoff",
}

SCORE_VALUE_PATTERN = re.compile(r"\d+(?:\.\d+)?점")

DEFAULT_NOTIFICATION_TEMPLATES = {
    "default": {
        "template": DEFAULT_TEMPLATE,
        "description": "기본 알림 템플릿. title, lines, body 변수를 사용할 수 있습니다.",
    },
    "investmentInsight": {
        "template": DEFAULT_TEMPLATE,
        "description": "온톨로지 관계 인사이트 기반 투자 알림",
    },
    "modelBuy": {
        "template": DEFAULT_TEMPLATE,
        "description": "모델 매수 조건 알림",
    },
    "modelSell": {
        "template": DEFAULT_TEMPLATE,
        "description": "모델 매도 조건 알림",
    },
    "watchlistBuyCandidate": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 매수 후보 알림",
    },
    "watchlistQuote": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 시세 수집 알림",
    },
    "watchlistQuotePending": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 시세 대기 알림",
    },
    "watchlistOntologySignal": {
        "template": DEFAULT_TEMPLATE,
        "description": "관심종목 온톨로지 관계 신호 알림",
    },
    "holdingTiming": {
        "template": DEFAULT_TEMPLATE,
        "description": "보유 종목 타이밍 점검 알림",
    },
    "ontologyInferenceMissing": {
        "template": DEFAULT_TEMPLATE,
        "description": "온톨로지 추론 결과 누락 상태 알림",
    },
    "monitorHeartbeat": {
        "template": DEFAULT_TEMPLATE,
        "description": "실시간 모니터링 상태 알림",
    },
    "monitorConnection": {
        "template": DEFAULT_TEMPLATE,
        "description": "토스 연결 상태 변화 알림",
    },
    "monitorPositionChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "보유 종목/수량 변화 알림",
    },
    "monitorPnlChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "손익률 변화 알림",
    },
    "monitorValueChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "평가액 변화 알림",
    },
    "monitorTrendChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "이동평균/추세 변화 알림",
    },
    "monitorCashChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "현금 비중 변화 알림",
    },
    "monitorDecisionChange": {
        "template": DEFAULT_TEMPLATE,
        "description": "매수/매도 판단 변화 알림",
    },
    "externalEquityMove": {
        "template": DEFAULT_TEMPLATE,
        "description": "Alpha Vantage 기반 미장 가격/거래량 변동 알림",
    },
    "externalCryptoMove": {
        "template": DEFAULT_TEMPLATE,
        "description": "CoinGecko 기반 크립토 변동 알림",
    },
    "externalMacroShift": {
        "template": DEFAULT_TEMPLATE,
        "description": "FRED 기반 금리/스프레드 변화 알림",
    },
    "externalDartDisclosure": {
        "template": DEFAULT_TEMPLATE,
        "description": "OpenDART 기반 국내 종목 공시 변화 알림",
    },
    "externalDataConnection": {
        "template": DEFAULT_TEMPLATE,
        "description": "외부 데이터 API 연결 상태 알림",
    },
    "modelReview": {
        "template": BODY_TEMPLATE,
        "description": "비동기 모델 리뷰 결과 알림",
    },
    "workHandoff": {
        "template": BODY_TEMPLATE,
        "description": "작업 완료 핸드오프 알림",
    },
    "notification": {
        "template": BODY_TEMPLATE,
        "description": "일반 텍스트 알림",
    },
}

PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")








@dataclass
class NotificationTemplate:
    message_type: str
    template: str
    description: str = ""
    enabled: bool = True
    updated_at: str = ""

    @classmethod
    def default(cls, message_type: str = "default") -> "NotificationTemplate":
        configured = DEFAULT_NOTIFICATION_TEMPLATES.get(message_type) or DEFAULT_NOTIFICATION_TEMPLATES["default"]
        return cls(message_type, configured["template"], configured.get("description", ""), True)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "messageType": payload["message_type"],
            "template": payload["template"],
            "description": payload["description"],
            "enabled": payload["enabled"],
            "updatedAt": payload["updated_at"],
        }


def symbol_display_name(symbol: object, title: object = "") -> str:
    raw_symbol = str(symbol or "").strip().upper()
    known = SYMBOL_DISPLAY_NAMES.get(raw_symbol, "")
    if known:
        return known
    title_text = str(title or "").strip()
    if title_text and title_text.upper() != raw_symbol:
        return title_text
    return raw_symbol


def symbol_with_code(display_symbol: object, raw_symbol: object) -> str:
    raw_text = str(raw_symbol or "").strip().upper()
    display_text = str(display_symbol or "").strip()
    if display_text and raw_text and display_text.upper() != raw_text:
        return display_text + " / " + raw_text
    return display_text or raw_text




def target_display_value(title: object, raw_symbol: object, display_symbol: object) -> str:
    title_text = str(title or "").strip()
    raw_text = str(raw_symbol or "").strip().upper()
    display_text = str(display_symbol or "").strip()
    symbol_text = symbol_with_code(display_text, raw_text)
    if title_text and raw_text and title_text.upper() == raw_text and display_text:
        return symbol_text
    if title_text and symbol_text:
        if title_text == display_text:
            return symbol_text
        if raw_text and raw_text in title_text:
            return title_text
        if display_text and display_text in title_text:
            return title_text + (" / " + raw_text if raw_text else "")
        return title_text + " / " + symbol_text
    return title_text or symbol_text


def headline_target_name(target_value: object) -> str:
    text = str(target_value or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"[/|]", text) if part.strip()]
    if parts:
        if parts[0] in {"크립토 변동"} and len(parts) > 1:
            text = parts[1]
        else:
            text = parts[0]
    return text[:24].rstrip()


def targeted_headline(status_headline: str, title_icon: str, target_value: str, title_headline: str) -> str:
    target = headline_target_name(target_value)
    action = str(title_headline or "").strip()
    if target and action.startswith(target + " "):
        action = action[len(target):].strip()
    if target and action:
        body = target + ": " + action
    else:
        body = action or target
    return " ".join(part for part in [status_headline, title_icon, body] if str(part or "").strip())


def alert_context(event: AlertEvent) -> Dict[str, object]:
    raw_lines = raw_lines_with_reference_date(event, [str(line).strip() for line in event.lines if str(line).strip()])
    metadata = dict(event.metadata or {})
    raw_lines = append_unique_lines(raw_lines, macro_context_lines(metadata))
    lines = "\n".join(["- " + line for line in raw_lines])
    bullet_lines = "\n".join([plain_bullet(line) for line in raw_lines])
    message_type_label = MESSAGE_TYPE_LABELS.get(event.rule, event.rule)
    severity_label = SEVERITY_LABELS.get(str(event.severity or "").upper(), event.severity or "")
    trigger_summary = TRIGGER_SUMMARIES.get(event.rule, "설정한 조건이 실제 데이터에서 충족될 때 보냅니다.")
    raw_symbol = str(event.symbol or "").strip()
    display_symbol = symbol_display_name(raw_symbol, event.title) if raw_symbol else ""
    symbol_text = symbol_with_code(display_symbol, raw_symbol)
    symbol_line = ("종목: " + symbol_text) if symbol_text else ""
    severity_line = ("상태: " + severity_label) if severity_label else ""
    type_line = ("유형: " + message_type_label) if message_type_label else ""
    trigger_line = ("발생 조건: " + trigger_summary) if trigger_summary else ""
    data_lines = lines
    status_headline = ("[" + severity_label + "]") if severity_label else ""
    title_icon = notification_title_icon(event.rule, raw_lines, event)
    title_headline = notification_title_headline(event.rule, raw_lines, event, message_type_label or event.title)
    target_value = target_display_value(event.title, raw_symbol, display_symbol)
    headline = targeted_headline(status_headline, title_icon, target_value, title_headline)
    target_line = "대상: " + target_value if target_value else ""
    criteria = event_criterion_lines(event, raw_lines, trigger_summary)
    signals = notification_signal_labels(event.rule, raw_lines)
    trigger_block_rows = criterion_rows(criteria, False)
    trigger_block = ("발송 기준\n" + trigger_block_rows) if trigger_block_rows else ""
    display_raw_lines = notification_data_lines(raw_lines, metadata)
    data_rows = plain_data_rows(display_raw_lines)
    data_block = ("데이터\n" + data_rows) if data_rows else ""
    ontology_lines = ontology_rule_lines(metadata)
    ai_lines = ai_prompt_lines(metadata)
    ai_opinion_lines = notification_ai_opinion_lines(metadata)
    missing_lines = missing_data_lines(metadata)
    ontology_block = block_from_lines("관계 규칙", ontology_lines)
    ai_opinion_block = block_from_lines("AI 의견", ai_opinion_lines)
    ai_prompt_block = ""
    missing_data_block = block_from_lines("부족 데이터", missing_lines)
    readable_parts = [
        headline,
        target_value,
    ]
    if data_rows:
        readable_parts.extend(["", data_block])
    if ai_opinion_block:
        readable_parts.extend(["", ai_opinion_block])
    if ontology_block:
        readable_parts.extend(["", ontology_block])
    if ai_prompt_block:
        readable_parts.extend(["", ai_prompt_block])
    if missing_data_block:
        readable_parts.extend(["", missing_data_block])
    if trigger_block:
        readable_parts.extend(["", trigger_block])
    readable_message = "\n".join(part for part in readable_parts if str(part).strip() or part == "").strip()
    escaped_target = html.escape(target_value, quote=False)
    telegram_trigger_rows = criterion_rows(criteria, True)
    telegram_data_lines = telegram_data_rows(display_raw_lines)
    telegram_ontology_block = telegram_block_from_lines("관계 규칙", ontology_lines)
    telegram_ai_opinion_block = telegram_block_from_lines("AI 의견", ai_opinion_lines)
    telegram_ai_prompt_block = ""
    telegram_missing_data_block = telegram_block_from_lines("부족 데이터", missing_lines)
    telegram_parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + escaped_target + "</code>") if escaped_target else "",
    ]
    if telegram_data_lines:
        telegram_parts.extend(["", "<b>데이터</b>", telegram_data_lines])
    if telegram_ai_opinion_block:
        telegram_parts.extend(["", telegram_ai_opinion_block])
    if telegram_ontology_block:
        telegram_parts.extend(["", telegram_ontology_block])
    if telegram_ai_prompt_block:
        telegram_parts.extend(["", telegram_ai_prompt_block])
    if telegram_missing_data_block:
        telegram_parts.extend(["", telegram_missing_data_block])
    if telegram_trigger_rows:
        telegram_parts.extend(["", "<b>발송 기준</b>", telegram_trigger_rows])
    telegram_message = "\n".join(part for part in telegram_parts if str(part).strip() or part == "").strip()
    body = telegram_message or readable_message or "\n".join([event.title] + ([lines] if lines else []))
    generated_at = event_generated_at(event)
    reference_date = data_value(raw_lines, "기준일") or reference_date_text(generated_at)
    context = {
        "messageType": event.rule,
        "accountId": event.account_id,
        "accountLabel": event.account_label,
        "severity": event.severity,
        "severityLabel": severity_label,
        "rule": event.rule,
        "key": event.key,
        "title": event.title,
        "symbol": event.symbol,
        "rawSymbol": raw_symbol,
        "symbolDisplayName": display_symbol,
        "displaySymbolName": display_symbol,
        "symbolWithCode": symbol_text,
        "target": event.target(),
        "rawTarget": event.target(),
        "displayTarget": target_value,
        "messageTypeLabel": message_type_label,
        "triggerSummary": trigger_summary,
        "headline": headline,
        "statusHeadline": status_headline,
        "titleIcon": title_icon,
        "titleHeadline": title_headline,
        "targetLine": target_line,
        "triggerBlock": trigger_block,
        "criterionBlock": trigger_block,
        "criterionLines": "\n".join(criteria),
        "dataBlock": data_block,
        "aiOpinionBlock": ai_opinion_block,
        "ontologyRelationBlock": ontology_block,
        "aiPromptBlock": ai_prompt_block,
        "missingDataBlock": missing_data_block,
        "divider": "",
        "telegramMessage": telegram_message,
        "telegramDataLines": telegram_data_lines,
        "telegramAiOpinionBlock": telegram_ai_opinion_block,
        "telegramOntologyRelationBlock": telegram_ontology_block,
        "telegramAiPromptBlock": telegram_ai_prompt_block,
        "telegramMissingDataBlock": telegram_missing_data_block,
        "symbolLine": symbol_line,
        "severityLine": severity_line,
        "typeLine": type_line,
        "triggerLine": trigger_line,
        "dataLines": data_lines,
        "bulletLines": bullet_lines,
        "lines": lines,
        "rawLines": "\n".join(raw_lines),
        "notificationSignals": list(signals),
        "notificationSignalText": " ".join(signals),
        "referenceDate": reference_date,
        "eventGeneratedAt": generated_at,
        "readableMessage": readable_message,
        "body": body,
    }
    context["metadata"] = metadata
    for key, value in metadata.items():
        context.setdefault(str(key), value)
    return context


def text_context(
    text: str,
    message_type: str = "notification",
    account_id: str = "",
    account_label: str = "",
) -> Dict[str, object]:
    body = str(text or "").strip()
    title = body.splitlines()[0] if body else ""
    return {
        "messageType": str(message_type or "notification"),
        "accountId": str(account_id or ""),
        "accountLabel": str(account_label or ""),
        "title": title,
        "body": body,
        "lines": "\n".join(body.splitlines()[1:]),
    }


def context_value(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return " ".join(str(value.get(key) or "") for key in sorted(value.keys()) if str(value.get(key) or "").strip())
    return str(value or "")


def render_template(template: str, context: Dict[str, object]) -> str:
    values = dict(context or {})

    def replace(match) -> str:
        return context_value(values.get(match.group(1), ""))

    rendered = PLACEHOLDER_PATTERN.sub(replace, str(template or BODY_TEMPLATE)).strip()
    compacted = []
    previous_blank = False
    for line in rendered.splitlines():
        cleaned = line.rstrip()
        if cleaned.strip():
            compacted.append(cleaned)
            previous_blank = False
        elif compacted and not previous_blank:
            compacted.append("")
            previous_blank = True
    while compacted and not compacted[-1].strip():
        compacted.pop()
    rendered = "\n".join(compacted)
    return beginner_friendly_text(rendered or context_value(values.get("body") or values.get("title") or ""))


def context_raw_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("rawLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def has_score_value(value: str) -> bool:
    return bool(SCORE_VALUE_PATTERN.search(str(value or "")))




def context_message_type(context: Dict[str, object]) -> str:
    return str((context or {}).get("messageType") or (context or {}).get("rule") or "").strip()


def score_reason_items(value: object) -> List[str]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").splitlines()
    return [friendly_score_reason(item) for item in items if str(item or "").strip()]


def friendly_score_reason(value: object) -> str:
    text = str(value or "").strip()
    replacements = [
        ("기본 ", "기본 우선도 "),
        ("주의 등급", "주의 알림"),
        ("관찰 등급", "관찰 알림"),
        ("종목 지정", "종목 코드/이름 포함"),
        ("핵심 투자 단어", "중요 투자 표현 포함"),
        ("확인 데이터 포함", "수급·추세 같은 확인 데이터 포함"),
        ("행동 필요 표현", "확인·점검 표현 포함"),
        ("본문 있음", "알림 내용 있음"),
        ("상태성 노이즈", "단순 상태 알림"),
        ("유사 메시지", "비슷한 알림 반복"),
        ("상태 정책", "반복 알림 조절"),
        ("장 시간", "장 운영 시간"),
        ("신규 임계값 상태", "새 기준값 상태"),
        ("같은 임계값 상태 지속", "같은 기준값 상태 지속"),
        ("임계값", "기준값"),
        ("리스크", "위험"),
        ("모델 매수", "내 매수 기준"),
        ("모델 매도", "내 매도 기준"),
        ("danger", "위험"),
        ("caution", "주의"),
        ("ALERT", "주의"),
        ("WATCH", "관찰"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    text = re.sub(r"([+-]\d+(?:\.\d+)?)$", r"\1점", text)
    return text


def formula_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number.is_integer():
        return str(int(number))
    return ("%.4f" % number).rstrip("0").rstrip(".")


def formula_audit_items(context: Dict[str, object], domain: str = "all") -> List[Dict[str, object]]:
    audits: List[Dict[str, object]] = []
    if domain in {"all", "model"}:
        raw_audits = (context or {}).get("formulaAudits")
    else:
        raw_audits = []
    if isinstance(raw_audits, list):
        audits.extend([item for item in raw_audits if isinstance(item, dict)])
    if domain in {"all", "delivery"}:
        notification_audit = (context or {}).get("notificationFormulaAudit")
        if isinstance(notification_audit, dict) and notification_audit:
            audits.append(notification_audit)
        elif (context or {}).get("honeyScore") not in (None, ""):
            expression = str((context or {}).get("notificationScoreFormula") or "rawScore").strip() or "rawScore"
            audits.append({
                "key": "notificationScoreFormula",
                "label": "알림 발송 공식",
                "expression": expression,
                "result": context.get("honeyScore"),
                "variables": {"rawScore": context.get("honeyScore")},
                "missing": [],
                "note": "상세 발송 변수표가 없는 이전 알림이어서 최종 발송 우선도를 rawScore로 표시합니다.",
            })
    seen = set()
    unique: List[Dict[str, object]] = []
    for audit in audits:
        key = str(audit.get("key") or audit.get("label") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(audit)
    return unique


def formula_variables_text(audit: Dict[str, object]) -> str:
    variables = audit.get("variables") if isinstance(audit, dict) else {}
    if not isinstance(variables, dict) or not variables:
        return "없음"
    return ", ".join(
        str(key) + "=" + formula_number(variables.get(key))
        for key in sorted(variables.keys())
    )


def formula_missing_text(audit: Dict[str, object]) -> str:
    missing = audit.get("missing") if isinstance(audit, dict) else []
    if isinstance(missing, str):
        missing = [missing]
    if not isinstance(missing, list) or not missing:
        return "없음"
    return ", ".join(str(item) for item in missing if str(item or "").strip()) or "없음"


def formula_detail_text(label: str, detail: str) -> str:
    return "없음" if detail == "없음" else label + " " + detail


def formula_audit_lines(context: Dict[str, object], domain: str = "model") -> List[str]:
    lines: List[str] = []
    is_delivery = domain == "delivery"
    for audit in formula_audit_items(context, domain):
        key = str(audit.get("key") or "").strip()
        label = str(audit.get("label") or key or "공식").strip()
        expression = str(audit.get("expression") or "").strip()
        result = formula_number(audit.get("result"))
        if is_delivery:
            result_text = "우선도 " + result
            formula_label = "발송 공식"
            variable_label = "발송 대입값"
            missing_label = "발송 부족 데이터"
            note_label = "발송 참고"
        else:
            result_text = ("선택됨, " if audit.get("selected") else "") + result + "점"
            formula_label = "모델 공식"
            variable_label = "모델 대입값"
            missing_label = "모델 부족 데이터"
            note_label = "모델 참고"
        if expression:
            lines.append(formula_label + ": " + label + "(" + key + ") = " + expression + " -> " + result_text)
        else:
            lines.append(formula_label + ": " + label + "(" + key + ") -> " + result_text)
        lines.append(variable_label + ": " + formula_detail_text(label, formula_variables_text(audit)))
        lines.append(missing_label + ": " + formula_detail_text(label, formula_missing_text(audit)))
        if is_delivery and key == "notificationScoreFormula" and (context or {}).get("honeyScore") not in (None, ""):
            final_score = formula_number((context or {}).get("honeyScore"))
            if result and final_score and result != final_score:
                lines.append("최종 발송 우선도: 공식 결과 " + result + "에서 반복·장 시간 정책 반영 후 " + final_score)
        note = str(audit.get("note") or "").strip()
        if note:
            lines.append(note_label + ": " + note)
    return lines


MODELING_LABELS = {
    "investmentInsight": "온톨로지 투자 인사이트 모델",
    "modelBuy": "매수 판단 모델",
    "modelSell": "매도 판단 모델",
    "watchlistBuyCandidate": "관심종목 매수 후보 모델",
    "watchlistQuote": "관심종목 시세 변화 감지 모델",
    "watchlistQuotePending": "관심종목 시세 수집 대기 모델",
    "watchlistOntologySignal": "관심종목 온톨로지 관계 신호 모델",
    "holdingTiming": "보유 타이밍 모델",
    "ontologyInferenceMissing": "온톨로지 추론 상태 점검 모델",
    "monitorHeartbeat": "실시간 모니터링 상태 모델",
    "monitorConnection": "토스 연결 상태 모델",
    "monitorPositionChange": "보유 수량 변화 감지 모델",
    "monitorPnlChange": "손익률 변화 감지 모델",
    "monitorValueChange": "평가액 변화 감지 모델",
    "monitorTrendChange": "이동평균/추세 변화 감지 모델",
    "monitorCashChange": "현금 비중 변화 감지 모델",
    "monitorDecisionChange": "보유 판단 변화 감지 모델",
    "externalEquityMove": "미장 가격/거래량 변동 모델",
    "externalCryptoMove": "크립토 변동 모델",
    "externalMacroShift": "거시 지표 변화 모델",
    "externalDartDisclosure": "공시 변화 감지 모델",
    "externalDataConnection": "외부 API 연결 상태 모델",
    "modelReview": "비동기 모델 리뷰 모델",
    "notification": "일반 알림 모델",
    "default": "기본 알림 모델",
}

MODEL_DATA_HINTS = {
    "investmentInsight": "보유·관심종목·외부 데이터·관계 규칙·기존 모델 신호를 연결한 온톨로지 인사이트",
    "modelBuy": "토스 시세·수급·추세·가치평가 데이터",
    "modelSell": "토스 시세·수급·추세·손절/가치평가 데이터",
    "watchlistBuyCandidate": "관심종목 시세·수급·추세·가치평가 데이터",
    "watchlistOntologySignal": "관심종목 ABox 관측값, 관계 규칙, 추세 동역학, 데이터 품질",
    "holdingTiming": "보유 스냅샷, 손익률, 수급, 추세, 매도 가능 수량",
    "ontologyInferenceMissing": "실계좌 스냅샷, Neo4j InferenceBox 상태, 관계·근거 추론 개수",
    "monitorDecisionChange": "이전/현재 보유 모델 점수와 보유 스냅샷",
    "monitorTrendChange": "현재가와 20일/60일 이동평균 비교",
    "monitorPnlChange": "이전/현재 손익률",
    "monitorValueChange": "이전/현재 평가액",
    "monitorCashChange": "이전/현재 현금 비중",
    "externalEquityMove": "Alpha Vantage 가격·거래량",
    "externalCryptoMove": "CoinGecko 24시간/7일 변동률·가격·거래액",
    "externalMacroShift": "FRED 금리·거시 시계열",
    "externalDartDisclosure": "OpenDART 공시",
    "externalDataConnection": "외부 API 호출 결과",
}


def context_number(context: Dict[str, object], key: str):
    value = (context or {}).get(key)
    if value in (None, ""):
        raw_value = data_value(context_raw_lines(context), key)
        value = raw_value
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def selected_holding_formula_key(context: Dict[str, object]) -> str:
    basis = str((context or {}).get("holdingDecisionBasis") or "").strip()
    if basis == "profitTake":
        return "profitTakeScoreFormula"
    if basis == "lossCut":
        return "lossCutScoreFormula"
    pnl = context_number(context, "profitLossRate")
    if pnl is None:
        pnl = context_number(context, "손익")
    if pnl is None:
        pnl = context_number(context, "수익률")
    if pnl is not None:
        return "lossCutScoreFormula" if pnl < 0 else "profitTakeScoreFormula"
    return ""


def strategy_formula_line(context: Dict[str, object]) -> str:
    message_type = context_message_type(context)
    if ontology_relation_context(context) and message_type in {"modelBuy", "modelSell", "watchlistBuyCandidate", "holdingTiming", "monitorDecisionChange"}:
        return "판단 기준: 관계 규칙(ontologyRelationRules)"
    if message_type == "investmentInsight":
        return "판단 기준: 온톨로지 관계 인사이트(sourceSignalTypes + ontologyInsight)"
    if message_type in {"modelBuy", "watchlistBuyCandidate"}:
        return "판단 기준: 관계 규칙 점수"
    if message_type == "modelSell":
        return "판단 기준: 관계 규칙 점수"
    if message_type in {"holdingTiming", "monitorDecisionChange"}:
        return "판단 기준: 관계 규칙"
    if message_type == "externalCryptoMove":
        return "판단 공식: 크립토 변동 공식(cryptoMoveScoreFormula)"
    return ""


def crypto_model_summary_lines(context: Dict[str, object]) -> List[str]:
    if context_message_type(context) != "externalCryptoMove":
        return []
    model = context.get("cryptoMoveModel") if isinstance(context, dict) else {}
    if not isinstance(model, dict):
        model = {}
    title = str(model.get("titleLabel") or context.get("cryptoMoveTitle") or "").strip()
    score = model.get("score", context.get("cryptoMoveScore"))
    period = str(model.get("dominantPeriodLabel") or context.get("cryptoMoveDominantPeriod") or "").strip()
    change = model.get("dominantChange", context.get("cryptoMoveDominantChange"))
    reason = str(model.get("reason") or context.get("cryptoMoveReason") or "").strip()
    lines: List[str] = []
    if title or score not in (None, ""):
        result = title or "크립토 가격 변동"
        if score not in (None, ""):
            result += " (모델 점수 " + format_score_value(score) + "점)"
        lines.append("판단 결과: " + result)
    if period or change not in (None, ""):
        change_text = signed_pct(change) if isinstance(change, (int, float)) else str(change or "").strip()
        lines.append("대표 변화: " + " ".join(part for part in [period, change_text] if part))
    if reason:
        lines.append("핵심 이유: " + reason)
    return lines


def modeling_lines(context: Dict[str, object]) -> List[str]:
    message_type = context_message_type(context)
    if message_type in SCORE_EXPLANATION_SKIP_TYPES:
        return []
    label = MODELING_LABELS.get(message_type, "기본 알림 모델")
    lines = ["모델: " + label]
    formula_line = strategy_formula_line(context)
    if formula_line:
        lines.append(formula_line)
    data_hint = MODEL_DATA_HINTS.get(message_type)
    if data_hint:
        lines.append("사용 데이터: " + data_hint)
    lines.extend(crypto_model_summary_lines(context))
    return lines


def delivery_score_lines(context: Dict[str, object]) -> List[str]:
    if not isinstance(context, dict) or context.get("honeyScore") in (None, ""):
        return []
    score = format_score_value(context.get("honeyScore"))
    threshold = format_score_value(context.get("honeyThreshold"))
    lines = [
        "발송 우선도: " + score + "/" + threshold,
        "발송 우선도는 투자 모델 점수가 아니라 이 알림을 실제로 보낼지 판단하는 별도 값입니다. 발송 공식으로 알림 중요도를 계산한 뒤 반복 여부와 장 시간 정책을 반영합니다.",
    ]
    formula = str(context.get("notificationScoreFormula") or "").strip()
    if formula and formula != "rawScore":
        lines.append("발송 공식: " + formula)
    reasons = score_reason_items(context.get("honeyReasons"))
    if reasons:
        lines.append("발송 판정 내역: " + ", ".join(reasons))
    return lines


def investment_score_lines(context: Dict[str, object]) -> List[str]:
    message_type = context_message_type(context)
    raw_lines = context_raw_lines(context)
    lines: List[str] = []
    buy_value = data_value(raw_lines, "매수 판단") or data_value(raw_lines, "모델 매수 점수")
    sell_value = data_value(raw_lines, "매도 판단") or data_value(raw_lines, "모델 매도 점수")
    status_value = data_value(raw_lines, "상태")
    previous_value = data_value(raw_lines, "이전")
    current_value = data_value(raw_lines, "현재")

    if message_type == "investmentInsight":
        insight = context.get("ontologyInsight") if isinstance(context, dict) else {}
        score = insight.get("score") if isinstance(insight, dict) else context.get("score")
        confidence = insight.get("confidence") if isinstance(insight, dict) else context.get("confidence")
        source_types = insight.get("sourceSignalTypes") if isinstance(insight, dict) else context.get("sourceSignalTypes")
        if isinstance(source_types, list):
            source_text = ", ".join(str(item) for item in source_types[:5])
        else:
            source_text = str(source_types or "").strip()
        parts = []
        if score not in (None, ""):
            parts.append("관계 강도 " + formula_number(score) + "점")
        if confidence not in (None, ""):
            parts.append("신뢰도 " + formula_number(confidence) + "%")
        if source_text:
            parts.append("근거 신호 " + source_text)
        lines.append(
            "온톨로지 인사이트 점수: 개별 알림 타입을 직접 발송하지 않고, 보유·관심종목·외부 신호가 만든 관계 조합을 하나의 투자 인사이트로 합성합니다."
            + (" " + ", ".join(parts) + "." if parts else "")
        )
    if message_type in {"modelBuy", "watchlistBuyCandidate"} and has_score_value(buy_value):
        lines.append(
            "매수 관계 점수: 직접 공식 하나가 아니라 성립한 관계 규칙의 강도를 0~100점으로 표시합니다. 눌림목, 지지선, 거래량, 체결강도, 투자자 수급, 뉴스·공시 리스크가 관계 규칙 안에서 함께 확인될 때 매수 후보에 가까워집니다."
        )
    if message_type == "modelSell" and has_score_value(sell_value):
        lines.append(
            "매도 관계 점수: 성립한 관계 규칙의 강도를 0~100점으로 표시합니다. 손실 관리, 수익 보호, 추세 훼손, 매도 수급, 포트폴리오 집중, 뉴스·공시 리스크가 강하게 연결될수록 분할매도나 손실 관리 점검에 가까워집니다."
        )
    if message_type in {"holdingTiming", "monitorDecisionChange"} and any(has_score_value(item) for item in [status_value, previous_value, current_value]):
        lines.append(
            "보유 관계 점수: 사용자가 설정한 개별 공식이 아니라 성립한 관계 규칙의 강도입니다. 손익률, 20일선·60일선, 거래량, 체결강도, 투자자 수급, 포트폴리오 비중, 뉴스·공시가 어떤 관계로 연결됐는지를 보고 점수와 판단 이름을 정합니다."
        )
    if message_type == "externalCryptoMove" and (context or {}).get("cryptoMoveScore") not in (None, ""):
        lines.append(
            "크립토 변동 모델 점수: 24시간 변화율과 7일 변화율이 각각의 기준값을 얼마나 넘었는지 비교해 0~100점으로 계산합니다. 모델은 더 강하게 기준을 넘은 기간을 대표 변화로 고르고, 그 방향으로 제목과 관찰/주의 등급을 정합니다."
        )
    return lines


def ontology_modeling_lines(context: Dict[str, object]) -> List[str]:
    relation_context = ontology_relation_context(context)
    if not relation_context:
        return []
    subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
    subject_text = " / ".join(
        str(value)
        for value in [subject.get("name"), subject.get("symbol"), subject.get("market")]
        if str(value or "").strip()
    )
    lines = [
        "모델: 관계 규칙 모델",
        "엔진: " + str(relation_context.get("engineVersion") or "-"),
    ]
    if subject_text:
        lines.append("대상: " + subject_text)
    strength = relation_context.get("signalStrength")
    if strength not in (None, ""):
        lines.append(
            "관계 신호: "
            + str(relation_context.get("signalStrengthLabel") or "")
            + " ("
            + format_score_value(strength)
            + "점)"
        )
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    if decision:
        selected = str(decision.get("selectedRuleId") or "").strip()
        lines.append("판단: " + str(decision.get("label") or "-") + ((" / 선택 규칙 " + selected) if selected else ""))
    if context_message_type(context) == "externalCryptoMove":
        model = context.get("cryptoMoveModel") if isinstance(context.get("cryptoMoveModel"), dict) else {}
        period = str(model.get("dominantPeriodLabel") or context.get("cryptoMoveDominantPeriod") or "").strip()
        change = model.get("dominantChange", context.get("cryptoMoveDominantChange"))
        if period or change not in (None, ""):
            change_text = signed_pct(change) if isinstance(change, (int, float)) else str(change or "").strip()
            lines.append("대표 변화: " + " ".join(part for part in [period, change_text] if part))
    for line in ontology_rule_lines(context)[:4]:
        lines.append(line)
    return lines


def ontology_missing_lines(context: Dict[str, object]) -> List[str]:
    lines = missing_data_lines(context)
    return ["부족 데이터 없음"] if ontology_relation_context(context) and not lines else lines


def ontology_prompt_section_lines(context: Dict[str, object]) -> List[str]:
    lines = ai_prompt_lines(context)
    prompt_context = ontology_prompt_context(context)
    policy = str(prompt_context.get("promptPolicy") or "").strip()
    if policy:
        compact_policy = ", ".join(line.strip() for line in policy.splitlines() if line.strip())
        if compact_policy:
            lines.append("정책: " + compact_policy)
    return lines


def score_explanation_sections(context: Dict[str, object]) -> List[tuple]:
    if context_message_type(context) in SCORE_EXPLANATION_SKIP_TYPES:
        return []
    message_type = context_message_type(context)
    has_relation_context = bool(ontology_relation_context(context))
    if has_relation_context:
        model_lines = ontology_modeling_lines(context)
        missing_lines = ontology_missing_lines(context)
        prompt_lines = []
    else:
        model_lines = modeling_lines(context)
        model_lines.extend(formula_audit_lines(context, "model"))
        model_lines.extend(investment_score_lines(context))
        if has_relation_context:
            model_lines.extend(ontology_rule_lines(context)[:3])
        missing_lines = []
        prompt_lines = []
    sections = []
    if model_lines:
        sections.append(("관계 판단" if has_relation_context else "모델 판단", model_lines))
    if missing_lines:
        sections.append(("부족 데이터", missing_lines))
    return sections


def score_explanation_lines(context: Dict[str, object]) -> List[str]:
    lines: List[str] = []
    for _title, section_lines in score_explanation_sections(context):
        lines.extend(section_lines)
    return lines


def score_explanation_block(context: Dict[str, object], rich: bool = False) -> str:
    sections = score_explanation_sections(context)
    if not sections:
        return ""
    blocks = []
    for title, lines in sections:
        if not lines:
            continue
        if rich:
            rows = []
            for line in lines:
                label, value = split_label_value(line)
                if label and value:
                    rows.append("• <b>" + html.escape(label, quote=False) + "</b>: " + html.escape(value, quote=False))
                else:
                    rows.append(html_bullet(line))
            blocks.append("<b>" + html.escape(title, quote=False) + "</b>\n" + "\n".join(rows))
        else:
            blocks.append(title + "\n" + "\n".join(plain_bullet(line) for line in lines))
    return "\n\n".join(blocks)


def ai_opinion_block(context: Dict[str, object], rich: bool = False) -> str:
    lines = notification_ai_opinion_lines(context)
    if not lines:
        return ""
    if rich:
        rows = []
        for line in lines:
            label, value = split_label_value(line)
            if label and value:
                rows.append("• <b>" + html.escape(label, quote=False) + "</b>: " + html.escape(value, quote=False))
            else:
                rows.append(html_bullet(line))
        return "<b>AI 의견</b>\n" + "\n".join(rows)
    return "AI 의견\n" + "\n".join(plain_bullet(line) for line in lines)


def context_with_score_explanation(context: Dict[str, object]) -> Dict[str, object]:
    values = enrich_notification_ai_context(dict(context or {}))
    raw_symbol = str(values.get("rawSymbol") or values.get("symbol") or "").strip().upper()
    display_symbol = str(
        values.get("symbolDisplayName")
        or values.get("displaySymbolName")
        or SYMBOL_DISPLAY_NAMES.get(raw_symbol, "")
        or ""
    ).strip()
    symbol_text = str(values.get("symbolWithCode") or "").strip() or symbol_with_code(display_symbol, raw_symbol)
    if symbol_text:
        values["symbol"] = symbol_text
    display_target = str(values.get("displayTarget") or "").strip()
    if not display_target and symbol_text and str(values.get("target") or "").strip().upper() == raw_symbol:
        display_target = symbol_text
    if display_target:
        values["target"] = display_target
    values["aiOpinionBlock"] = ai_opinion_block(values, False)
    values["telegramAiOpinionBlock"] = ai_opinion_block(values, True)
    values["scoreExplanation"] = score_explanation_block(values, False)
    values["telegramScoreExplanation"] = score_explanation_block(values, True)
    return values


def template_prefers_rich_score(template: str, rendered: str) -> bool:
    text = str(template or "")
    return "{telegramMessage}" in text or "<b>" in rendered or "<code>" in rendered


def footer_value_from_context(context: Dict[str, object], *keys: str) -> str:
    if not isinstance(context, dict):
        return ""
    for key in keys:
        value = str(context.get(key) or "").strip()
        if value:
            return value
    raw_lines = context_raw_lines(context)
    for key in keys:
        value = data_value(raw_lines, key)
        if value:
            return value
    return ""


def footer_analysis_source(context: Dict[str, object]) -> str:
    response = context.get("notificationAiValidatedResponse") if isinstance(context, dict) else {}
    if isinstance(response, dict):
        source = str(response.get("source") or "").strip()
        if source:
            return "AI 투자 판단 / " + source
    return str((context or {}).get("analysisSource") or "").strip()


def footer_row(label: str, value: str, rich: bool = False) -> str:
    if not str(value or "").strip():
        return ""
    if rich:
        return "• <b>" + html.escape(label, quote=False) + "</b>: <code>" + html.escape(str(value), quote=False) + "</code>"
    return "• " + label + ": " + str(value)


def message_footer(context: Dict[str, object], rich: bool = False) -> str:
    number = footer_value_from_context(context, "notificationNumber", "notificationNo", "debugNotificationNumber")
    if not number:
        number = notification_debug_number(footer_value_from_context(context, "jobId"))
    rows = [
        footer_row("번호", number, rich),
    ]
    rows = [row for row in rows if row]
    if not rows:
        return ""
    title = "<b>알림 추적</b>" if rich else "알림 추적"
    return "\n".join([title, *rows])


def append_message_footer(rendered: str, context: Dict[str, object], rich: bool = False) -> str:
    rendered_text = str(rendered or "").rstrip()
    if not rendered_text or "알림 추적" in rendered_text:
        return rendered_text
    footer = message_footer(context, rich)
    if not footer:
        return rendered_text
    return rendered_text + "\n\n" + footer


def append_score_explanation(rendered: str, context: Dict[str, object], rich: bool = False) -> str:
    rendered_text = str(rendered or "")
    if context_message_type(context) == "modelReview":
        return rendered
    if (
        context_message_type(context) in {"holdingTiming", "monitorDecisionChange", "investmentInsight"}
        and ontology_relation_context(context)
        and any(marker in rendered_text for marker in ["관계 규칙", "AI 분석 기준", "부족 데이터", "발송 기준"])
    ):
        return rendered
    if not rendered_text.strip() or "점수 계산" in rendered_text or "모델 판단" in rendered_text or "관계 판단" in rendered_text or "온톨로지 판단" in rendered_text or "알림 발송" in rendered_text:
        return rendered
    block = score_explanation_block(context, rich)
    if not block:
        return rendered
    return str(rendered).rstrip() + "\n\n" + block


def append_ai_opinion(rendered: str, context: Dict[str, object], rich: bool = False) -> str:
    rendered_text = str(rendered or "")
    if isinstance(context, dict) and context.get("notificationAiValidatedResponse"):
        return rendered
    if not rendered_text.strip() or "AI 의견" in rendered_text:
        return rendered
    block = ai_opinion_block(context, rich)
    if not block:
        return rendered
    insertion_markers = [
        "\n\n<b>관계 규칙</b>",
        "\n\n관계 규칙",
        "\n\n<b>발송 기준</b>",
        "\n\n발송 기준",
        "\n\n<b>모델 판단</b>",
        "\n\n모델 판단",
        "\n\n<b>관계 판단</b>",
        "\n\n관계 판단",
        "\n\n<b>온톨로지 판단</b>",
        "\n\n온톨로지 판단",
    ]
    for marker in insertion_markers:
        if marker in rendered_text:
            return rendered_text.replace(marker, "\n\n" + block + marker, 1)
    return rendered_text.rstrip() + "\n\n" + block


def prepend_message_start_badge(rendered: str, rich: bool = False) -> str:
    text = str(rendered or "").strip()
    if not text:
        return text
    if text.startswith(MESSAGE_START_BADGE) or text.startswith("<b>" + MESSAGE_START_BADGE + "</b>"):
        return text
    badge = "<b>" + MESSAGE_START_BADGE + "</b>" if rich else MESSAGE_START_BADGE
    return badge + "\n\n" + text


def render_notification(template: NotificationTemplate, context: Dict[str, object]) -> str:
    values = context_with_score_explanation(context)
    if template and template.enabled:
        rendered = render_template(template.template, values)
        rich = template_prefers_rich_score(template.template, rendered)
        rendered = append_ai_opinion(rendered, values, rich)
        rendered = beginner_friendly_text(append_score_explanation(rendered, values, rich))
        rendered = append_message_footer(rendered, values, rich)
        return prepend_message_start_badge(rendered, rich)
    rendered = render_template(BODY_TEMPLATE, values)
    rich = template_prefers_rich_score(BODY_TEMPLATE, rendered)
    rendered = append_ai_opinion(rendered, values, rich)
    rendered = beginner_friendly_text(append_score_explanation(rendered, values, rich))
    rendered = append_message_footer(rendered, values, rich)
    return prepend_message_start_badge(rendered, rich)



def template_variables() -> List[str]:
    return [
        "messageType",
        "accountId",
        "accountLabel",
        "severity",
        "severityLabel",
        "rule",
        "key",
        "title",
        "symbol",
        "rawSymbol",
        "symbolDisplayName",
        "displaySymbolName",
        "target",
        "rawTarget",
        "displayTarget",
        "messageTypeLabel",
        "triggerSummary",
        "headline",
        "statusHeadline",
        "titleIcon",
        "titleHeadline",
        "targetLine",
        "triggerBlock",
        "criterionBlock",
        "criterionLines",
        "dataBlock",
        "aiOpinionBlock",
        "ontologyRelationBlock",
        "aiPromptBlock",
        "missingDataBlock",
        "divider",
        "telegramMessage",
        "telegramDataLines",
        "telegramAiOpinionBlock",
        "telegramOntologyRelationBlock",
        "telegramAiPromptBlock",
        "telegramMissingDataBlock",
        "symbolLine",
        "severityLine",
        "typeLine",
        "triggerLine",
        "dataLines",
        "bulletLines",
        "lines",
        "rawLines",
        "notificationSignals",
        "notificationSignalText",
        "referenceDate",
        "eventGeneratedAt",
        "jobId",
        "notificationNumber",
        "sentAt",
        "sentTime",
        "sentLine",
        "readableMessage",
        "body",
        "disclosureAnalysis",
        "telegramDisclosureAnalysis",
        "disclosureAnalysisSource",
        "disclosureAnalysisVersion",
        "scoreExplanation",
        "telegramScoreExplanation",
        "honeyScore",
        "honeyThreshold",
        "honeyScoreText",
        "honeyDecision",
        "honeyReasons",
        "metadata",
        "formulaAudits",
        "legacyFormulaAudits",
        "notificationAiOpinion",
        "notificationAiPromptContext",
        "ontologyRelationContext",
        "ontologyPromptContext",
        "notificationFormulaAudit",
        "buyScoreFormula",
        "sellScoreFormula",
        "profitTakeScoreFormula",
        "lossCutScoreFormula",
        "notificationScoreFormula",
        "holdingDecision",
        "holdingDecisionBasis",
        "holdingDecisionScore",
        "profitLossRate",
        "market",
        "changePercent",
        "change24h",
        "change7d",
        "price",
        "volume",
        "volume24h",
        "provider",
        "cryptoMoveModel",
        "cryptoMoveScore",
        "cryptoMoveDirection",
        "cryptoMoveDominantPeriod",
        "cryptoMoveDominantChange",
        "cryptoMoveTitle",
        "cryptoMoveReason",
    ]
