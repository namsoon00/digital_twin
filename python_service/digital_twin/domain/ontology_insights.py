from typing import Dict, Iterable, List, Tuple

from .alert_formatting import compact_number
from .market_data import number
from .message_types import (
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_DART_DISCLOSURE,
    EXTERNAL_DATA_CONNECTION,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_MACRO_SHIFT,
    HOLDING_TIMING,
    INVESTMENT_INSIGHT,
    MESSAGE_TYPE_LABELS,
    MODEL_BUY,
    MODEL_REVIEW,
    MODEL_SELL,
    MONITOR_CASH_CHANGE,
    MONITOR_CONNECTION,
    MONITOR_DECISION_CHANGE,
    MONITOR_HEARTBEAT,
    MONITOR_PNL_CHANGE,
    MONITOR_POSITION_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_VALUE_CHANGE,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_QUOTE,
    WATCHLIST_QUOTE_PENDING,
    WORK_HANDOFF,
)
from .portfolio import AccountSnapshot, AlertEvent


INSIGHT_RULE = INVESTMENT_INSIGHT

SYSTEM_ALERT_TYPES = {
    MONITOR_CONNECTION,
    MONITOR_HEARTBEAT,
    EXTERNAL_DATA_CONNECTION,
    MODEL_REVIEW,
    WORK_HANDOFF,
}

INVESTMENT_SIGNAL_TYPES = {
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_QUOTE,
    WATCHLIST_QUOTE_PENDING,
    HOLDING_TIMING,
    MONITOR_POSITION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_CASH_CHANGE,
    MONITOR_DECISION_CHANGE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_MACRO_SHIFT,
    EXTERNAL_DART_DISCLOSURE,
}

INSIGHT_TYPE_LABELS = {
    "riskIncrease": "리스크 증가",
    "riskManagement": "리스크 관리",
    "opportunityDetected": "기회 후보",
    "portfolioShift": "포트폴리오 변화",
    "liquidityShift": "유동성 변화",
    "externalRegimeShift": "외부 환경 변화",
    "dataQualityWarning": "데이터 신뢰도 점검",
    "contradictionDetected": "상충 신호",
    "relationshipChange": "관계 변화",
}

SCORE_KEYS = (
    "ontologyPressure",
    "ontology_pressure",
    "holdingDecisionScore",
    "modelSellScore",
    "modelBuyScore",
    "watchlistBuyScore",
    "cryptoMoveScore",
    "changePercent",
    "change24h",
    "change7d",
    "profitLossRate",
)

SEVERITY_SCORE = {"ALERT": 82.0, "WATCH": 62.0, "INFO": 35.0}
SOURCE_METADATA_KEYS = {
    "market",
    "provider",
    "modelBuyScore",
    "modelSellScore",
    "watchlistBuyScore",
    "holdingDecisionScore",
    "profitLossRate",
    "changePercent",
    "change24h",
    "change7d",
    "cryptoMoveScore",
    "disclosureCount",
    "latestTradingDay",
}
ONTOLOGY_CONTEXT_KEYS = (
    "ontologyRelationContext",
    "ontologyPromptContext",
    "ontologyOpinion",
    "ontologyWorldview",
    "ontologyReviewContext",
)


def split_operational_and_investment_events(events: Iterable[AlertEvent]) -> Tuple[List[AlertEvent], List[AlertEvent]]:
    system_events: List[AlertEvent] = []
    signal_events: List[AlertEvent] = []
    for event in events or []:
        if event.rule in SYSTEM_ALERT_TYPES:
            system_events.append(event)
        elif event.rule in INVESTMENT_SIGNAL_TYPES:
            signal_events.append(event)
    return system_events, signal_events


def investment_signal_events(events: Iterable[AlertEvent]) -> List[AlertEvent]:
    return [event for event in events or [] if event.rule in INVESTMENT_SIGNAL_TYPES]


def signal_type_label(rule: str) -> str:
    return MESSAGE_TYPE_LABELS.get(rule, str(rule or "신호"))


def event_score(event: AlertEvent) -> float:
    metadata = event.metadata or {}
    nested_opinion = metadata.get("ontologyOpinion") if isinstance(metadata.get("ontologyOpinion"), dict) else {}
    candidates: List[float] = []
    for key in SCORE_KEYS:
        value = metadata.get(key)
        if value in (None, "") and isinstance(nested_opinion, dict):
            value = nested_opinion.get(key)
        if value in (None, ""):
            continue
        parsed = abs(number(value))
        if parsed:
            candidates.append(parsed)
    if candidates:
        return max(0.0, min(100.0, max(candidates)))
    return SEVERITY_SCORE.get(str(event.severity or "").upper(), 40.0)


def event_subject(event: AlertEvent) -> str:
    symbol = str(event.symbol or "").strip().upper()
    if symbol:
        return symbol
    if event.rule == MONITOR_CASH_CHANGE:
        return "portfolio"
    if event.rule == EXTERNAL_MACRO_SHIFT:
        return "macro"
    return "portfolio"


def snapshot_name_by_symbol(snapshot: AccountSnapshot) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for item in list(snapshot.positions or []) + list(snapshot.watchlist or []):
        symbol = str(getattr(item, "symbol", "") or "").strip().upper()
        name = str(getattr(item, "name", "") or "").strip()
        if symbol and name:
            names[symbol] = name
    for item in snapshot.decisions or []:
        symbol = str(getattr(item, "symbol", "") or "").strip().upper()
        name = str(getattr(item, "name", "") or "").strip()
        if symbol and name:
            names.setdefault(symbol, name)
    return names


def subject_display_name(snapshot: AccountSnapshot, subject: str, events: List[AlertEvent]) -> str:
    subject_key = str(subject or "").strip()
    if subject_key == "portfolio":
        return "포트폴리오"
    if subject_key == "macro":
        return "거시 환경"
    names = snapshot_name_by_symbol(snapshot)
    if subject_key.upper() in names:
        return names[subject_key.upper()]
    for event in events:
        title = str(event.title or "").strip()
        if title and title.upper() != subject_key.upper():
            return title
    return subject_key


def unique_preserve(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def infer_insight_type(events: List[AlertEvent]) -> str:
    source_types = {event.rule for event in events}
    severities = {str(event.severity or "").upper() for event in events}
    if {MODEL_BUY, MODEL_SELL}.issubset(source_types) or {WATCHLIST_BUY_CANDIDATE, MODEL_SELL}.issubset(source_types):
        return "contradictionDetected"
    if WATCHLIST_QUOTE_PENDING in source_types:
        return "dataQualityWarning"
    if MONITOR_CASH_CHANGE in source_types:
        return "liquidityShift"
    if source_types & {MODEL_SELL, HOLDING_TIMING, MONITOR_DECISION_CHANGE}:
        return "riskIncrease" if "ALERT" in severities else "riskManagement"
    if source_types & {MODEL_BUY, WATCHLIST_BUY_CANDIDATE, WATCHLIST_QUOTE}:
        return "opportunityDetected"
    if source_types & {EXTERNAL_EQUITY_MOVE, EXTERNAL_CRYPTO_MOVE, EXTERNAL_MACRO_SHIFT, EXTERNAL_DART_DISCLOSURE}:
        return "externalRegimeShift"
    if source_types & {MONITOR_POSITION_CHANGE, MONITOR_PNL_CHANGE, MONITOR_VALUE_CHANGE, MONITOR_TREND_CHANGE}:
        return "portfolioShift"
    return "relationshipChange"


def highest_severity(events: List[AlertEvent]) -> str:
    if any(str(event.severity or "").upper() == "ALERT" for event in events):
        return "ALERT"
    if any(str(event.severity or "").upper() == "WATCH" for event in events):
        return "WATCH"
    return "INFO"


def compact_source_line(event: AlertEvent) -> str:
    label = signal_type_label(event.rule)
    first_line = next((str(line or "").strip() for line in event.lines or [] if str(line or "").strip()), "")
    title = str(event.title or "").strip()
    if first_line:
        text = first_line
    elif title:
        text = title
    else:
        text = label
    if title and title not in text and title.upper() != str(event.symbol or "").upper():
        text = title + " · " + text
    return label + ": " + text


def insight_thesis(insight_type: str, subject_name: str, source_labels: List[str], score: float) -> str:
    sources = ", ".join(source_labels[:4]) if source_labels else "관계 신호"
    score_text = compact_number(round(score, 1))
    if insight_type == "riskIncrease":
        return subject_name + "에서 " + sources + "가 함께 강해져 리스크 우선 점검 대상으로 바뀌었습니다. 관계 강도는 " + score_text + "점입니다."
    if insight_type == "riskManagement":
        return subject_name + "의 보유 판단과 관계 신호가 리스크 관리 쪽으로 기울었습니다. 관계 강도는 " + score_text + "점입니다."
    if insight_type == "opportunityDetected":
        return subject_name + "에서 " + sources + "가 매수 후보 쪽으로 모였습니다. 아직 실행보다 확인 조건을 먼저 봐야 합니다."
    if insight_type == "portfolioShift":
        return subject_name + " 관련 포트폴리오 상태가 직전 스냅샷과 의미 있게 달라졌습니다. 변화 원인을 가격, 수량, 추세로 분리해 봐야 합니다."
    if insight_type == "liquidityShift":
        return "현금 비중과 시장별 유동성 관계가 달라졌습니다. 새 매수보다 가용 현금과 리스크 버퍼를 먼저 확인해야 합니다."
    if insight_type == "externalRegimeShift":
        return subject_name + "에 연결된 외부 시장 관계가 바뀌었습니다. 단일 가격보다 보유 종목 노출과 민감도 연결을 우선 확인해야 합니다."
    if insight_type == "dataQualityWarning":
        return subject_name + " 판단에 필요한 데이터가 부족합니다. 투자 판단보다 데이터 신뢰도 복구가 우선입니다."
    if insight_type == "contradictionDetected":
        return subject_name + "에서 매수·매도 계열 신호가 동시에 나타났습니다. 결론보다 상충 조건의 원인을 먼저 분리해야 합니다."
    return subject_name + "에서 " + sources + " 관계가 새로 감지되었습니다."


def next_check_for_insight(insight_type: str, source_types: List[str]) -> str:
    if insight_type in {"riskIncrease", "riskManagement"}:
        return "손절/분할축소 기준, 매도 가능 수량, 다음 조회에서도 같은 규칙이 유지되는지 확인하세요."
    if insight_type == "opportunityDetected":
        return "첫 진입 조건, 20일선 위치, 거래량 배율, 손절 기준을 같이 확인하세요."
    if insight_type == "portfolioShift":
        return "변화가 가격, 수량, 환율, 이동평균 중 어디에서 생겼는지 분리하세요."
    if insight_type == "liquidityShift":
        return "시장별 목표 현금 비중, 미체결 주문, 환전/입출금 여부를 확인하세요."
    if insight_type == "externalRegimeShift":
        return "외부 신호가 보유 종목의 가격·수급·섹터 노출과 같은 방향인지 확인하세요."
    if insight_type == "dataQualityWarning":
        return "종목 코드, 데이터 공급자 응답, 마지막 성공 시각을 먼저 복구하세요."
    if insight_type == "contradictionDetected":
        return "매수 후보 근거와 리스크 근거가 서로 다른 기간/데이터에서 온 것인지 분리하세요."
    if EXTERNAL_DART_DISCLOSURE in source_types:
        return "공시 원문, 접수번호, 장중 거래량 반응을 확인하세요."
    return "새 관계가 다음 데이터 업데이트에서도 유지되는지 확인하세요."


def source_metadata(event: AlertEvent) -> Dict[str, object]:
    metadata = event.metadata or {}
    return {key: metadata.get(key) for key in SOURCE_METADATA_KEYS if key in metadata}


def promoted_ontology_context(events: List[AlertEvent]) -> Dict[str, object]:
    promoted: Dict[str, object] = {}
    for key in ONTOLOGY_CONTEXT_KEYS:
        values = [event.metadata.get(key) for event in events if isinstance(event.metadata, dict) and event.metadata.get(key)]
        if not values:
            continue
        promoted[key] = values[0] if len(values) == 1 else values
    return promoted


def grouped_signal_events(events: Iterable[AlertEvent]) -> Dict[str, List[AlertEvent]]:
    groups: Dict[str, List[AlertEvent]] = {}
    for event in investment_signal_events(events):
        groups.setdefault(event_subject(event), []).append(event)
    return groups


def build_investment_insight_events(snapshot: AccountSnapshot, signal_events: Iterable[AlertEvent]) -> List[AlertEvent]:
    insights: List[AlertEvent] = []
    for subject, events in grouped_signal_events(signal_events).items():
        if not events:
            continue
        source_types = unique_preserve(event.rule for event in events)
        source_labels = unique_preserve(signal_type_label(rule) for rule in source_types)
        scores = [event_score(event) for event in events]
        score = max(scores) if scores else 0.0
        confidence = max(55.0, min(95.0, (sum(scores) / len(scores) if scores else score) * 0.72 + min(len(source_types), 5) * 5))
        novelty_score = max(25.0, min(100.0, len(source_types) * 18.0 + min(len(events), 6) * 6.0))
        insight_type = infer_insight_type(events)
        insight_label = INSIGHT_TYPE_LABELS.get(insight_type, insight_type)
        subject_name = subject_display_name(snapshot, subject, events)
        thesis = insight_thesis(insight_type, subject_name, source_labels, score)
        next_check = next_check_for_insight(insight_type, source_types)
        source_lines = unique_preserve(compact_source_line(event) for event in events)[:7]
        source_key = "|".join(sorted(source_types))
        score_bucket = str(int(round(score / 5.0) * 5))
        insight_id = ":".join([snapshot.account_id, "ontology-insight", subject, insight_type, source_key, score_bucket])
        criteria = [
            "설정: 온톨로지 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때",
            "감지: " + ", ".join(source_labels) + " · 관계 강도 " + compact_number(round(score, 1)) + "점 · 신뢰도 " + compact_number(round(confidence, 1)) + "%",
        ]
        metadata = {
            "ontologyInsight": {
                "id": insight_id,
                "cadenceKey": ":".join(["cadence", "python", snapshot.account_id, INSIGHT_RULE, subject, insight_type, source_key, score_bucket]),
                "insightType": insight_type,
                "insightLabel": insight_label,
                "subject": subject,
                "subjectName": subject_name,
                "score": round(score, 1),
                "confidence": round(confidence, 1),
                "noveltyScore": round(novelty_score, 1),
                "severity": highest_severity(events),
                "sourceSignalTypes": source_types,
                "sourceEventKeys": [event.key for event in events],
                "thesis": thesis,
                "nextCheck": next_check,
                "dispatchMode": "insight-driven-only",
                "legacyAlertTypesRole": "evidence-only",
            },
            "sourceSignalTypes": source_types,
            "sourceAlertEvents": [
                {
                    "rule": event.rule,
                    "label": signal_type_label(event.rule),
                    "key": event.key,
                    "severity": event.severity,
                    "title": event.title,
                    "symbol": event.symbol,
                    "lines": list(event.lines or []),
                    "criteria": list(event.criteria or []),
                    "metadata": source_metadata(event),
                }
                for event in events
            ],
            "dispatchPolicy": {
                "mode": "insight-driven-only",
                "cooldownPolicy": "insight-cadence-key",
                "noveltyPolicy": "source-relation-change",
                "suppressionPolicy": "legacy-signal-direct-dispatch-disabled",
            },
            "legacyAlertTypesRole": "evidence-only",
        }
        metadata.update(promoted_ontology_context(events))
        insights.append(AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            highest_severity(events),
            INSIGHT_RULE,
            insight_id,
            subject_name,
            [
                "인사이트 유형: " + insight_label,
                "핵심 결론: " + thesis,
                "근거 신호: " + ", ".join(source_labels),
                *["근거: " + line for line in source_lines],
                "다음 확인: " + next_check,
            ],
            "" if subject in {"portfolio", "macro"} else subject,
            criteria=criteria,
            metadata=metadata,
        ))
    return insights
