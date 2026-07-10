import re
from datetime import datetime, timedelta, timezone
from typing import List

from .message_types import MESSAGE_TYPE_EMOJIS
from .notification_ontology_sections import ontology_relation_context
from .notification_text_formatting import (
    data_value,
    dominant_signed_direction,
    first_data_text,
    signed_direction,
    text_parts_from_value,
    title_from_change,
)
from .portfolio import AlertEvent


KST = timezone(timedelta(hours=9))


def investment_insight_signal_blob(raw_lines: List[str], event: AlertEvent) -> str:
    metadata = dict(getattr(event, "metadata", {}) or {})
    parts: List[str] = list(raw_lines) + [str(getattr(event, "title", "") or "")]
    for key in ["ontologyInsight", "insight", "notificationAiOpinion"]:
        value = metadata.get(key)
        if isinstance(value, dict):
            parts.extend(text_parts_from_value(value))
    for source_event in metadata.get("sourceAlertEvents") or []:
        if isinstance(source_event, dict):
            parts.extend(text_parts_from_value({
                "rule": source_event.get("rule"),
                "title": source_event.get("title"),
                "message": source_event.get("message"),
                "lines": source_event.get("lines"),
                "criteria": source_event.get("criteria"),
            }))
    relation_context = ontology_relation_context(metadata)
    if relation_context:
        parts.extend(text_parts_from_value({
            "decision": relation_context.get("decision"),
            "activeRules": relation_context.get("activeRules"),
        }))
    return " ".join(part for part in parts if part)


def investment_insight_decision_blob(raw_lines: List[str], event: AlertEvent) -> str:
    metadata = dict(getattr(event, "metadata", {}) or {})
    parts: List[str] = [
        data_value(raw_lines, "상태"),
        data_value(raw_lines, "손익") or data_value(raw_lines, "수익률"),
        data_value(raw_lines, "권장 액션"),
        data_value(raw_lines, "인사이트 유형"),
        data_value(raw_lines, "핵심 결론"),
        str(getattr(event, "title", "") or ""),
    ]
    relation_context = ontology_relation_context(metadata)
    if relation_context:
        parts.extend(text_parts_from_value({
            "decision": relation_context.get("decision"),
            "activeRules": relation_context.get("activeRules"),
        }))
    return " ".join(part for part in parts if part)


def investment_insight_is_holding(raw_lines: List[str], event: AlertEvent) -> bool:
    metadata = dict(getattr(event, "metadata", {}) or {})
    insight = metadata.get("ontologyInsight") if isinstance(metadata.get("ontologyInsight"), dict) else {}
    source_types = metadata.get("sourceSignalTypes") or insight.get("sourceSignalTypes") or []
    if isinstance(source_types, list) and any(str(item or "") in {"holdingTiming", "monitorPositionChange", "monitorPnlChange", "monitorValueChange", "monitorTrendChange", "monitorDecisionChange", "modelSell"} for item in source_types):
        return True
    if str(insight.get("holdingPolicyGroup") or metadata.get("holdingPolicyGroup") or "") == "holdingPositionCommon":
        return True
    blob = " ".join(list(raw_lines) + [str(getattr(event, "title", "") or "")])
    return any(term in blob for term in ["보유 수량", "매도가능 수량", "평균매입가", "평단가", "종목 평가금액", "손절", "분할축소", "분할매도"])


def percent_text(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?%", text)
    return match.group(0) if match else text


def compact_action_title(value: str) -> str:
    text = str(value or "").strip()
    for separator in [",", "·", "/", ";"]:
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    if len(text) > 28:
        text = text[:28].rstrip() + "..."
    return text


def has_investment_loss_signal(value: str) -> bool:
    return any(term in str(value or "") for term in ["손절", "손실", "분할축소", "추가매수 보류", "신규 진입 보류", "하락 가속", "riskWatch", "loss_guard", "LOSS", "entry.add_buy.blocked", "trend.breakdown_acceleration"])


def has_investment_profit_signal(value: str) -> bool:
    return any(term in str(value or "") for term in ["분할매도", "익절", "수익", "리밸런싱", "profit_take", "PROFIT"])


def has_entry_wait_signal(value: str) -> bool:
    return any(term in str(value or "") for term in [
        "entryWait",
        "ENTRY_WAIT",
        "신규 진입 대기",
        "신규 진입 관찰",
        "진입 관찰",
        "실행보다 관찰 우선",
        "지금 피할 일",
        "확인 전 신규 매수",
        "조건 재확인",
    ])


def notification_title_icon(rule: str, raw_lines: List[str], event: AlertEvent) -> str:
    key = str(rule or "")
    status = data_value(raw_lines, "상태")
    profit = data_value(raw_lines, "손익") or data_value(raw_lines, "수익률")
    change = data_value(raw_lines, "변화")
    signal = data_value(raw_lines, "신호")
    title_text = str(getattr(event, "title", "") or "")

    if key in {"modelBuy", "watchlistBuyCandidate"}:
        return "🟢"
    if key == "watchlistOntologySignal":
        return "🧭"
    if key == "investmentInsight":
        blob = investment_insight_signal_blob(raw_lines, event)
        decision_blob = investment_insight_decision_blob(raw_lines, event)
        is_holding = investment_insight_is_holding(raw_lines, event)
        if is_holding and signed_direction(profit) < 0:
            return "🛡️"
        if is_holding and (signed_direction(profit) > 0 or has_investment_profit_signal(decision_blob)):
            return "💰"
        if has_entry_wait_signal(blob) or has_entry_wait_signal(decision_blob):
            return "🧭"
        if any(term in blob for term in ["분할매수", "매수 후보", "기회 후보", "opportunityDetected", "watchlistBuyCandidate", "entry.pullback.supported"]):
            return "🟢"
        if signed_direction(profit) < 0 and has_investment_loss_signal(decision_blob):
            return "🛡️"
        if has_investment_profit_signal(decision_blob):
            return "💰"
        if has_investment_loss_signal(decision_blob):
            return "🛡️"
        if "외부" in blob:
            return "🌐"
        return "🧭"
    if key == "modelSell":
        return "🔴"
    if key == "holdingTiming":
        status_blob = " ".join([status, profit, title_text]).strip()
        if any(term in status_blob for term in ["손절", "손실"]) or signed_direction(profit) < 0:
            return "🛡️"
        if any(term in status_blob for term in ["분할", "익절", "수익"]):
            return "💰"
        return "⚖️"
    if key == "monitorPnlChange":
        return "📈" if dominant_signed_direction(change) > 0 else "📉" if dominant_signed_direction(change) < 0 else "📊"
    if key == "monitorValueChange":
        return "💵" if dominant_signed_direction(change) >= 0 else "💸"
    if key == "monitorTrendChange":
        if "하향" in signal or "이탈" in signal:
            return "📉"
        if "상향" in signal or "돌파" in signal:
            return "📈"
        return "📊"
    if key == "monitorDecisionChange":
        current = data_value(raw_lines, "현재")
        action = data_value(raw_lines, "권장 액션")
        decision_blob = " ".join([current, action])
        if any(term in decision_blob for term in ["손절", "손실", "축소"]):
            return "🛡️"
        if any(term in decision_blob for term in ["분할", "익절", "수익"]):
            return "💰"
        if "리밸런싱" in decision_blob:
            return "⚖️"
        return "🔁"
    if key == "externalCryptoMove":
        return "🪙"
    return MESSAGE_TYPE_EMOJIS.get(key, "🔔")


def notification_title_headline(rule: str, raw_lines: List[str], event: AlertEvent, fallback: str) -> str:
    key = str(rule or "")
    status = data_value(raw_lines, "상태")
    profit = data_value(raw_lines, "손익") or data_value(raw_lines, "수익률")
    change = data_value(raw_lines, "변화")
    signal = data_value(raw_lines, "신호")
    title_text = str(getattr(event, "title", "") or "")
    symbol = str(getattr(event, "symbol", "") or "").upper()

    if key in {"modelBuy", "watchlistBuyCandidate"}:
        return "매수 후보 감지"
    if key == "investmentInsight":
        insight_type = data_value(raw_lines, "인사이트 유형")
        blob = investment_insight_signal_blob(raw_lines, event)
        decision_blob = investment_insight_decision_blob(raw_lines, event)
        profit_text = percent_text(profit)
        is_holding = investment_insight_is_holding(raw_lines, event)
        if is_holding and (has_entry_wait_signal(blob) or has_entry_wait_signal(decision_blob)):
            if signed_direction(profit) < 0 or has_investment_loss_signal(decision_blob):
                return ("손실 " + profit_text + ": " if profit_text and signed_direction(profit) < 0 else "") + "추가매수 보류·손실 기준 점검"
            if signed_direction(profit) > 0 or has_investment_profit_signal(decision_blob):
                return ("수익 " + profit_text + ": " if profit_text and signed_direction(profit) > 0 else "") + "보유 유지·수익 보호 조건 점검"
            return "보유 판단: 추가매수 보류 조건 점검"
        if has_entry_wait_signal(blob) or has_entry_wait_signal(decision_blob):
            return "신규 진입 대기: 조건 재확인"
        if any(term in blob for term in ["분할매수", "매수 후보", "기회 후보", "opportunityDetected", "watchlistBuyCandidate", "entry.pullback.supported"]):
            return "분할매수 후보: 진입 조건 점검"
        if signed_direction(profit) < 0 and has_investment_loss_signal(decision_blob):
            return ("손실 " + profit_text + ": " if profit_text and signed_direction(profit) < 0 else "") + "손절·분할축소 점검"
        if has_investment_profit_signal(decision_blob):
            return ("수익 " + profit_text + ": " if profit_text and signed_direction(profit) > 0 else "") + "분할매도·리밸런싱 점검"
        if has_investment_loss_signal(decision_blob):
            return ("손실 " + profit_text + ": " if profit_text and signed_direction(profit) < 0 else "") + "손절·분할축소 점검"
        if "외부" in blob or "외부" in insight_type:
            return "외부 신호: 보유 영향 점검"
        if any(term in blob for term in ["매수", "기회"]):
            return "매수 후보: 진입 조건 점검"
        if insight_type:
            return insight_type + ": 대응 기준 점검"
        return "투자 인사이트: 대응 기준 점검"
    if key == "modelSell":
        return "매도 기준 점검"
    if key == "watchlistQuote":
        return "관심종목 시세 갱신"
    if key == "watchlistQuotePending":
        return "관심종목 시세 미수집"
    if key == "holdingTiming":
        status_blob = " ".join([status, profit, title_text]).strip()
        if any(term in status_blob for term in ["손절", "손실"]) or signed_direction(profit) < 0:
            profit_text = percent_text(profit)
            return ("손실 " + profit_text + ": " if profit_text else "") + "손절·분할축소 권장"
        if any(term in status_blob for term in ["분할", "익절", "수익"]):
            profit_text = percent_text(profit)
            return ("수익 " + profit_text + ": " if profit_text else "") + "분할매도 권장"
        if "조건부" in status_blob:
            return "조건부 보유: 추가매수 보류"
        return "보유 판단: 유지·대기"
    if key == "monitorHeartbeat":
        return "모니터링 상태 확인"
    if key == "monitorConnection":
        status_blob = " ".join([status, " ".join(raw_lines[:2])])
        if any(term in status_blob.lower() for term in ["실패", "오류", "unauthorized", "forbidden", "timeout", "error"]):
            return "토스 연결 오류"
        return "토스 연결 상태 변경"
    if key == "monitorPositionChange":
        body = " ".join(raw_lines)
        if "신규" in body:
            return "신규 보유 감지"
        if any(term in body for term in ["제외", "청산", "매도 완료"]):
            return "보유 제외 감지"
        return "보유 수량 변경"
    if key == "monitorPnlChange":
        return title_from_change(change, "손익률 개선", "손익률 악화", "손익률 변화")
    if key == "monitorValueChange":
        return title_from_change(change, "평가액 증가", "평가액 감소", "평가액 변화")
    if key == "monitorTrendChange":
        if "하향" in signal or "이탈" in signal:
            return "이동평균 하향 신호"
        if "상향" in signal or "돌파" in signal:
            return "이동평균 상향 신호"
        return "이동평균·추세 신호"
    if key == "monitorCashChange":
        return title_from_change(change, "현금 비중 증가", "현금 비중 감소", "현금 비중 변화")
    if key == "monitorDecisionChange":
        current = data_value(raw_lines, "현재")
        action = compact_action_title(data_value(raw_lines, "권장 액션"))
        if action:
            return "판단 변경: " + action
        if any(term in current for term in ["손절", "손실"]):
            return "판단 변경: 손절·분할축소 권장"
        if any(term in current for term in ["분할", "익절", "수익"]):
            return "판단 변경: 분할매도 권장"
        if "리밸런싱" in current:
            return "판단 변경: 리밸런싱 권장"
        if "보유" in current:
            return "판단 변경: 보유 유지"
        return "판단 변경: 대응 액션 변경"
    if key == "externalEquityMove":
        equity_change = data_value(raw_lines, "미장 가격 변동")
        return title_from_change(equity_change, "미장 가격 급등", "미장 가격 급락", "미장 가격·거래량 급변")
    if key == "externalCryptoMove":
        metadata = dict(getattr(event, "metadata", {}) or {})
        crypto_model = metadata.get("cryptoMoveModel") if isinstance(metadata.get("cryptoMoveModel"), dict) else {}
        model_title = str(crypto_model.get("titleLabel") or metadata.get("cryptoMoveTitle") or "").strip()
        if model_title:
            return model_title
        crypto_line = first_data_text(raw_lines, r"(비트코인|크립토).*?(24h|7d)")
        asset = "비트코인" if "비트코인" in crypto_line or symbol == "BTC" else "크립토"
        return title_from_change(crypto_line, asset + " 가격 급등", asset + " 가격 급락", asset + " 가격 급변")
    if key == "externalMacroShift":
        return "금리·거시 지표 변화"
    if key == "externalDartDisclosure":
        return "국내 공시 감지"
    if key == "externalDataConnection":
        provider = raw_lines[0] if raw_lines else ""
        return (provider + " 연결 점검").strip() if provider else "외부 API 연결 점검"
    if key == "ontologyInferenceMissing":
        return "온톨로지 추론 결과 없음"
    return fallback or title_text or key


def event_generated_at(event: AlertEvent) -> str:
    metadata = dict(getattr(event, "metadata", {}) or {})
    return str(
        getattr(event, "generated_at", "")
        or getattr(event, "generatedAt", "")
        or metadata.get("generatedAt")
        or metadata.get("updatedAt")
        or metadata.get("asOf")
        or ""
    ).strip()


def current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def reference_date_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except ValueError:
        return text


def raw_lines_with_reference_date(event: AlertEvent, raw_lines: List[str]) -> List[str]:
    if data_value(raw_lines, "기준일"):
        return list(raw_lines)
    reference_date = reference_date_text(event_generated_at(event) or current_utc_iso())
    if not reference_date:
        return list(raw_lines)
    return list(raw_lines) + ["기준일 " + reference_date]


def first_line_containing(raw_lines: List[str], terms: List[str]) -> str:
    for line in raw_lines:
        text = str(line or "").strip()
        if all(term in text for term in terms):
            return text
    return ""


def inferred_criterion_lines(event: AlertEvent, raw_lines: List[str], trigger_summary: str) -> List[str]:
    details: List[str] = []
    rule = str(event.rule or "")
    previous = data_value(raw_lines, "이전")
    current = data_value(raw_lines, "현재")
    change = data_value(raw_lines, "변화")
    signal = data_value(raw_lines, "신호")
    status = data_value(raw_lines, "상태")
    profit = data_value(raw_lines, "손익") or data_value(raw_lines, "수익률")

    if rule in {"modelBuy", "watchlistBuyCandidate"}:
        score = data_value(raw_lines, "매수 판단") or data_value(raw_lines, "모델 매수 점수")
        if score:
            details.append("감지: " + score)
    elif rule == "modelSell":
        score = data_value(raw_lines, "매도 판단") or data_value(raw_lines, "모델 매도 점수")
        if score:
            details.append("감지: " + score)
    elif rule == "holdingTiming":
        detected = ", ".join(part for part in ["상태 " + status if status else "", "수익률 " + profit if profit else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule in {"monitorPnlChange", "monitorValueChange", "monitorCashChange"}:
        detected = ", ".join(part for part in ["변화 " + change if change else "", "이전 " + previous if previous else "", "현재 " + current if current else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "monitorTrendChange":
        if signal:
            details.append("감지: " + signal)
        trend = data_value(raw_lines, "추세")
        if trend:
            details.append("확인 데이터: " + trend)
    elif rule == "monitorDecisionChange":
        detected = ", ".join(part for part in ["이전 " + previous if previous else "", "현재 " + current if current else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "externalEquityMove":
        change_value = data_value(raw_lines, "미장 가격 변동")
        price = data_value(raw_lines, "현재가") or data_value(raw_lines, "가격")
        detected = ", ".join(part for part in ["가격 변동 " + change_value if change_value else "", "현재가 " + price if price else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "externalCryptoMove":
        crypto_line = first_line_containing(raw_lines, ["24h"])
        if crypto_line:
            details.append("감지: " + crypto_line)
    elif rule == "externalMacroShift":
        macro_lines = [line for line in raw_lines if "bp" in str(line)]
        if macro_lines:
            details.append("감지: " + ", ".join(macro_lines[:3]))
    elif rule == "externalDartDisclosure":
        report = raw_lines[1] if len(raw_lines) > 1 else ""
        receipt_date = data_value(raw_lines, "접수일")
        detected = ", ".join(part for part in [report, "접수일 " + receipt_date if receipt_date else ""] if part)
        if detected:
            details.append("감지: " + detected)
    elif rule == "externalDataConnection":
        if raw_lines:
            details.append("감지: " + ", ".join(raw_lines[:2]))
    elif rule == "ontologyInferenceMissing":
        if raw_lines:
            details.append("감지: " + ", ".join(raw_lines[:2]))
    elif rule == "monitorPositionChange":
        if previous or current:
            details.append("감지: " + ", ".join(part for part in ["이전 " + previous if previous else "", "현재 " + current if current else ""] if part))
        elif raw_lines:
            details.append("감지: " + raw_lines[0])
    elif rule == "watchlistQuote":
        quote_change = first_line_containing(raw_lines, ["변화"])
        if quote_change:
            details.append("감지: " + quote_change)
        else:
            current_price = data_value(raw_lines, "현재가") or data_value(raw_lines, "현재")
            if current_price:
                details.append("감지: 현재가 " + current_price)
    elif rule == "watchlistQuotePending":
        if len(raw_lines) > 1:
            details.append("감지: " + raw_lines[1])
    elif rule == "monitorConnection":
        detected = ", ".join(part for part in ["이전 " + previous if previous else "", "현재 " + current if current else ""] if part)
        details.append("감지: " + (detected or ", ".join(raw_lines[:2])))
    elif rule == "monitorHeartbeat":
        status_value = status or (raw_lines[0] if raw_lines else "")
        if status_value:
            details.append("감지: " + status_value)

    if not details and raw_lines:
        details.append("감지: " + raw_lines[0])
    if details:
        return ["설정: " + trigger_summary] + details
    return [trigger_summary] if trigger_summary else []


def event_criterion_lines(event: AlertEvent, raw_lines: List[str], trigger_summary: str) -> List[str]:
    explicit = [str(item or "").strip() for item in getattr(event, "criteria", []) if str(item or "").strip()]
    if explicit:
        return explicit
    return inferred_criterion_lines(event, raw_lines, trigger_summary)
