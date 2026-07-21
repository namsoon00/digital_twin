import re
from typing import Dict, Iterable, List, Tuple

from .data_freshness import aggregate_freshness
from .message_types import (
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_DATA_CONNECTION,
    HOLDING_TIMING,
    INVESTMENT_INSIGHT,
    MESSAGE_TYPE_LABELS,
    MODEL_REVIEW,
    MONITOR_CONNECTION,
    MONITOR_HEARTBEAT,
    ONTOLOGY_INFERENCE_MISSING,
    WATCHLIST_ONTOLOGY_SIGNAL,
    WORK_HANDOFF,
)
from .notification_ai_context import is_graph_backed_relation_context
from .ontology_decision_state import (
    CHANGE_STATE_LABELS,
    CONFLICT_STATE_LABELS,
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    conflict_state_from_roles,
)
from .portfolio import AccountSnapshot, AlertEvent


INSIGHT_RULE = INVESTMENT_INSIGHT
VOLATILE_VALUE_SUFFIX = re.compile(r":-?\d+(?:\.\d+)?$")

SYSTEM_ALERT_TYPES = {
    MONITOR_CONNECTION,
    MONITOR_HEARTBEAT,
    ONTOLOGY_INFERENCE_MISSING,
    EXTERNAL_DATA_CONNECTION,
    EXTERNAL_CRYPTO_MOVE,
    MODEL_REVIEW,
    WORK_HANDOFF,
}

INVESTMENT_SIGNAL_TYPES = {
    WATCHLIST_ONTOLOGY_SIGNAL,
    HOLDING_TIMING,
}

HOLDING_POSITION_SIGNAL_TYPES = {
    HOLDING_TIMING,
}
HOLDING_POSITION_POLICY_GROUP = "holdingPositionCommon"
HOLDING_POSITION_DISPATCH_TYPE = "holdingPositionCommon"
HOLDING_POSITION_DISPATCH_SOURCE_KEY = "holdingPosition"

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

SOURCE_METADATA_KEYS = {
    "market",
    "provider",
    "watchlistOntologySignalType",
    "watchlistActiveRelationRules",
    "profitLossRate",
    "disclosureCount",
    "latestTradingDay",
    "lastUpdated",
    "dataFreshness",
    "dataFreshnessRequired",
}
ONTOLOGY_CONTEXT_KEYS = (
    "ontologyRelationContext",
    "ontologyPromptContext",
    "ontologyOpinion",
    "ontologyWorldview",
    "activeInvestmentOpinion",
    "ontologyReviewContext",
)
PROMOTED_REFERENCE_LABELS = (
    "상태",
    "현재가",
    "평균매입가",
    "평단가",
    "수익률",
    "보유 수량",
    "매도가능 수량",
    "종목 평가금액",
    "계좌 평가금액",
    "보유",
    "손익",
    "평가",
    "수급",
    "투자자",
    "추세",
    "기울기",
    "권장 액션",
    "주요 리스크",
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
    return [event for event in events or [] if event.rule in INVESTMENT_SIGNAL_TYPES and graph_backed_investment_event(event)]


def event_relation_context(event: AlertEvent) -> Dict[str, object]:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    relation_context = metadata.get("ontologyRelationContext") if isinstance(metadata.get("ontologyRelationContext"), dict) else {}
    return relation_context if isinstance(relation_context, dict) else {}


def graph_backed_investment_event(event: AlertEvent) -> bool:
    return is_graph_backed_relation_context(event_relation_context(event))


def signal_type_label(rule: str) -> str:
    return MESSAGE_TYPE_LABELS.get(rule, str(rule or "신호"))


REVIEW_LEVEL_ORDER = ("normal", "observe", "check", "act", "immediate")
KNOWN_REVIEW_LEVELS = REVIEW_LEVEL_ORDER + ("blocked",)
DATA_STATE_ORDER = ("sufficient", "partial", "insufficient", "unavailable")
CHANGE_STATE_ORDER = ("unchanged", "new-condition", "new-evidence", "improving", "worsening", "direction-changed")


def event_decision_state(event: AlertEvent) -> Dict[str, str]:
    relation_context = event_relation_context(event)
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    state = relation_context.get("decisionState") if isinstance(relation_context.get("decisionState"), dict) else {}
    severity = str(event.severity or "").upper()
    review_level = str(state.get("reviewLevel") or decision.get("reviewLevel") or "").strip().lower()
    if review_level not in KNOWN_REVIEW_LEVELS:
        review_level = "immediate" if severity == "ALERT" else "check" if severity == "WATCH" else "observe"
    data_state = str(state.get("dataState") or decision.get("dataState") or "partial").strip().lower()
    if data_state not in DATA_STATE_ORDER:
        data_state = "partial"
    change_state = str(state.get("changeState") or relation_context.get("changeState") or "unchanged").strip().lower()
    if change_state not in CHANGE_STATE_ORDER:
        change_state = "unchanged"
    conflict_state = str(state.get("conflictState") or "").strip().lower()
    if conflict_state not in CONFLICT_STATE_LABELS:
        roles = [
            str(item.get("evidenceRole") or item.get("evidence_role") or "context")
            for item in relation_context.get("activeRules") or relation_context.get("matchedRules") or []
            if isinstance(item, dict)
        ]
        conflict_state = conflict_state_from_roles(roles)
    return {
        "reviewLevel": review_level,
        "dataState": data_state,
        "changeState": change_state,
        "conflictState": conflict_state,
    }


def merged_decision_state(events: Iterable[AlertEvent]) -> Dict[str, str]:
    states = [event_decision_state(event) for event in events or []]
    if not states:
        return event_decision_state(AlertEvent("", "", "INFO", "", "", "", []))
    actionable_review_levels = [item["reviewLevel"] for item in states if item["reviewLevel"] != "blocked"]
    review_level = (
        max(actionable_review_levels, key=REVIEW_LEVEL_ORDER.index)
        if actionable_review_levels
        else "blocked"
    )
    data_state = max((item["dataState"] for item in states), key=DATA_STATE_ORDER.index)
    change_state = max((item["changeState"] for item in states), key=CHANGE_STATE_ORDER.index)
    conflict_values = {item["conflictState"] for item in states}
    if "mixed" in conflict_values or {"risk-only", "support-only"}.issubset(conflict_values):
        conflict_state = "mixed"
    elif "risk-only" in conflict_values:
        conflict_state = "risk-only"
    elif "support-only" in conflict_values:
        conflict_state = "support-only"
    else:
        conflict_state = "context-only"
    return {
        "reviewLevel": review_level,
        "dataState": data_state,
        "changeState": change_state,
        "conflictState": conflict_state,
        "judgementBlocked": bool(states) and all(item["reviewLevel"] == "blocked" for item in states),
    }


def event_subject(event: AlertEvent) -> str:
    symbol = str(event.symbol or "").strip().upper()
    if symbol:
        return symbol
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
    if WATCHLIST_ONTOLOGY_SIGNAL in source_types:
        signal_types = {
            str((event.metadata or {}).get("watchlistOntologySignalType") or "")
            for event in events
            if event.rule == WATCHLIST_ONTOLOGY_SIGNAL
        }
        if "dataQuality" in signal_types:
            return "dataQualityWarning"
        if "entryCandidate" in signal_types:
            return "opportunityDetected"
        if "riskWatch" in signal_types:
            return "riskIncrease" if "ALERT" in severities else "riskManagement"
        if "trendReview" in signal_types:
            return "opportunityDetected"
        return "relationshipChange"
    if HOLDING_TIMING in source_types:
        return "riskIncrease" if "ALERT" in severities else "riskManagement"
    return "relationshipChange"


def holding_position_policy_group(source_types: Iterable[str]) -> str:
    if set(source_types or []) & HOLDING_POSITION_SIGNAL_TYPES:
        return HOLDING_POSITION_POLICY_GROUP
    return ""


def dispatch_insight_type(insight_type: str, source_types: Iterable[str]) -> str:
    if holding_position_policy_group(source_types) and insight_type in {"riskIncrease", "riskManagement", "portfolioShift"}:
        return HOLDING_POSITION_DISPATCH_TYPE
    return str(insight_type or "")


def dispatch_source_key(source_key: str, source_types: Iterable[str]) -> str:
    if holding_position_policy_group(source_types):
        return HOLDING_POSITION_DISPATCH_SOURCE_KEY
    return str(source_key or "")


def highest_severity(events: List[AlertEvent]) -> str:
    if any(str(event.severity or "").upper() == "ALERT" for event in events):
        return "ALERT"
    if any(str(event.severity or "").upper() == "WATCH" for event in events):
        return "WATCH"
    return "INFO"


def stable_source_event_key(value: object) -> str:
    return VOLATILE_VALUE_SUFFIX.sub("", str(value or "").strip())


def relation_rule_ids(events: Iterable[AlertEvent]) -> List[str]:
    ids: List[str] = []
    for event in events or []:
        relation_context = event_relation_context(event)
        rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
        for item in rules:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("ruleId") or item.get("rule_id") or "").strip()
            if rule_id and rule_id not in ids:
                ids.append(rule_id)
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        for item in metadata.get("watchlistActiveRelationRules") or metadata.get("activeRelationRules") or []:
            if isinstance(item, dict):
                rule_id = str(item.get("ruleId") or item.get("rule_id") or "").strip()
            else:
                rule_id = str(item or "").strip()
            if rule_id and rule_id not in ids:
                ids.append(rule_id)
    return ids


def compact_relation_event_token(value: object) -> str:
    text = stable_source_event_key(value)
    text = re.sub(r"[^0-9A-Za-z가-힣:_./-]+", "-", text).strip("-")
    return text[:120]


def relation_news_event_tokens(relation_context: Dict[str, object], limit: int = 3) -> List[str]:
    if not isinstance(relation_context, dict):
        return []
    active_rule_ids = {
        str(item.get("ruleId") or item.get("rule_id") or "")
        for item in relation_context.get("activeRules") or relation_context.get("matchedRules") or []
        if isinstance(item, dict)
    }
    if not any(rule_id.startswith("news.") for rule_id in active_rule_ids):
        return []
    facts = relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else {}
    evidence = facts.get("researchEvidence") if isinstance(facts.get("researchEvidence"), list) else []
    tokens: List[str] = []
    for item in evidence:
        if not isinstance(item, dict) or str(item.get("kind") or "").lower() != "news":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        scope = str(item.get("relationScope") or payload.get("relationScope") or "").lower()
        if scope and scope != "direct":
            continue
        token = compact_relation_event_token(
            item.get("evidenceId")
            or item.get("evidence_id")
            or item.get("url")
            or item.get("title")
            or item.get("summary")
        )
        if token and token not in tokens:
            tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def relation_news_event_key_suffix(relation_context: Dict[str, object], limit: int = 3) -> str:
    tokens = relation_news_event_tokens(relation_context, limit=limit)
    return "news:" + "+".join(tokens) if tokens else ""


def material_relation_event_keys(events: Iterable[AlertEvent]) -> List[str]:
    keys: List[str] = []
    for event in events or []:
        key = stable_source_event_key(event.key)
        if any(marker in key.lower() for marker in [":news:", ":article:", ":rss:", ":disclosure:", ":dart:", ":filing:", ":sec:"]):
            if key not in keys:
                keys.append(key)
        relation_context = event_relation_context(event)
        suffix = relation_news_event_key_suffix(relation_context)
        if suffix and suffix not in keys:
            keys.append(suffix)
    return keys


def insight_semantic_components(
    subject: str,
    dispatch_type: str,
    source_types: List[str],
    events: List[AlertEvent],
) -> Dict[str, List[str]]:
    state = merged_decision_state(events)
    return {
        "subject": [str(subject or "").strip().upper() or "portfolio"],
        "dispatchType": [str(dispatch_type or "").strip()],
        "sourceSignalTypes": sorted(set(str(item or "").strip() for item in source_types or [] if str(item or "").strip())),
        "relationRuleIds": sorted(set(relation_rule_ids(events))),
        "materialSourceEventKeys": sorted(set(material_relation_event_keys(events))),
        "reviewLevel": [state["reviewLevel"]],
        "dataState": [state["dataState"]],
        "changeState": [state["changeState"]],
        "conflictState": [state["conflictState"]],
    }


def insight_semantic_signature(components: Dict[str, List[str]]) -> str:
    parts = []
    for key in [
        "subject", "dispatchType", "sourceSignalTypes", "relationRuleIds",
        "materialSourceEventKeys", "reviewLevel", "dataState", "changeState", "conflictState",
    ]:
        values = [str(item or "").strip() for item in components.get(key) or [] if str(item or "").strip()]
        parts.append(key + "=" + "+".join(values))
    return "|".join(parts)


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


def labeled_line_value(line: str, labels: Iterable[str]) -> Tuple[str, str]:
    text = str(line or "").strip()
    if not text:
        return "", ""
    for label in labels:
        prefix = str(label or "").strip()
        if not prefix:
            continue
        if text.startswith(prefix + ":"):
            return prefix, text.split(":", 1)[1].strip()
        if text.startswith(prefix + " "):
            return prefix, text[len(prefix):].strip()
    return "", ""


def promoted_reference_lines(events: List[AlertEvent]) -> List[str]:
    values: Dict[str, str] = {}
    for event in events or []:
        for line in event.lines or []:
            label, value = labeled_line_value(str(line or ""), PROMOTED_REFERENCE_LABELS)
            if label and value and label not in values:
                values[label] = value
    return [
        label + ": " + values[label]
        for label in PROMOTED_REFERENCE_LABELS
        if values.get(label)
    ]


def insight_thesis(insight_type: str, subject_name: str, source_labels: List[str], review_level: str) -> str:
    sources = ", ".join(source_labels[:4]) if source_labels else "관계 신호"
    review_text = REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["check"])
    if insight_type == "riskIncrease":
        return subject_name + "에서 확인할 위험 조건이 늘었습니다. 바로 매도하라는 뜻은 아니고, 보유 이유와 가격 반응을 먼저 다시 보라는 " + review_text + " 알림입니다."
    if insight_type == "riskManagement":
        return subject_name + "은 " + sources + " 때문에 보유 이유를 다시 확인해야 합니다. 현재 단계는 " + review_text + "이며 가격 하락 확률이나 매도 확정을 뜻하지 않습니다."
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


def promoted_data_freshness(events: List[AlertEvent]) -> Dict[str, object]:
    records = []
    for event in events:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        freshness = metadata.get("dataFreshness")
        if isinstance(freshness, dict):
            records.append(freshness)
    return aggregate_freshness(records, INSIGHT_RULE)


def promoted_active_opinion(promoted: Dict[str, object]) -> Dict[str, object]:
    value = promoted.get("activeInvestmentOpinion") if isinstance(promoted, dict) else {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                return item
    return {}


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
        decision_state = merged_decision_state(events)
        insight_type = infer_insight_type(events)
        policy_group = holding_position_policy_group(source_types)
        policy_dispatch_type = dispatch_insight_type(insight_type, source_types)
        insight_label = INSIGHT_TYPE_LABELS.get(insight_type, insight_type)
        subject_name = subject_display_name(snapshot, subject, events)
        thesis = insight_thesis(insight_type, subject_name, source_labels, decision_state["reviewLevel"])
        next_check = next_check_for_insight(insight_type, source_types)
        reference_lines = promoted_reference_lines(events)
        source_lines = unique_preserve(compact_source_line(event) for event in events)[:7]
        promoted_context = promoted_ontology_context(events)
        active_opinion = promoted_active_opinion(promoted_context)
        active_label = str(active_opinion.get("actionLabel") or active_opinion.get("action") or "").strip()
        active_review_label = str(active_opinion.get("reviewLevelLabel") or "").strip()
        active_thesis = str(active_opinion.get("thesis") or "").strip()
        active_lines = []
        if active_label:
            active_lines.append(
                "적극 의견: "
                + active_label
                + (" · " + active_review_label if active_review_label else "")
            )
        if active_thesis:
            active_lines.append("의견 근거: " + active_thesis)
        source_key = "|".join(sorted(source_types))
        policy_source_key = dispatch_source_key(source_key, source_types)
        insight_id = ":".join([snapshot.account_id, "ontology-insight", subject, insight_type, source_key])
        semantic_components = insight_semantic_components(subject, policy_dispatch_type, source_types, events)
        semantic_signature = insight_semantic_signature(semantic_components)
        criteria = [
            "설정: 온톨로지 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때",
            "감지: " + ", ".join(source_labels)
            + " · 확인 단계 " + REVIEW_LEVEL_LABELS.get(decision_state["reviewLevel"], decision_state["reviewLevel"])
            + " · 자료 상태 " + DATA_STATE_LABELS.get(decision_state["dataState"], decision_state["dataState"])
            + " · 변화 " + CHANGE_STATE_LABELS.get(decision_state["changeState"], decision_state["changeState"]),
        ]
        metadata = {
            "ontologyInsight": {
                "id": insight_id,
                "cadenceKey": ":".join(["cadence", "typedb", snapshot.account_id, INSIGHT_RULE, subject, policy_dispatch_type, policy_source_key]),
                "insightType": insight_type,
                "dispatchInsightType": policy_dispatch_type,
                "dispatchSourceKey": policy_source_key,
                "holdingPolicyGroup": policy_group,
                "insightLabel": insight_label,
                "subject": subject,
                "subjectName": subject_name,
                "reviewLevel": decision_state["reviewLevel"],
                "reviewLevelLabel": REVIEW_LEVEL_LABELS.get(decision_state["reviewLevel"], decision_state["reviewLevel"]),
                "dataState": decision_state["dataState"],
                "dataStateLabel": DATA_STATE_LABELS.get(decision_state["dataState"], decision_state["dataState"]),
                "changeState": decision_state["changeState"],
                "changeStateLabel": CHANGE_STATE_LABELS.get(decision_state["changeState"], decision_state["changeState"]),
                "conflictState": decision_state["conflictState"],
                "conflictStateLabel": CONFLICT_STATE_LABELS.get(decision_state["conflictState"], decision_state["conflictState"]),
                "severity": highest_severity(events),
                "sourceSignalTypes": source_types,
                "semanticSignature": semantic_signature,
                "semanticComponents": semantic_components,
                "sourceEventKeys": [stable_source_event_key(event.key) for event in events],
                "thesis": thesis,
                "nextCheck": next_check,
                "dispatchMode": "insight-driven-only",
                "sourceSignalRole": "graph-backed-evidence",
                "graphDerived": True,
                "graphSource": "typedbInferenceBox",
                "referenceDataLines": reference_lines,
            },
            "sourceSignalTypes": source_types,
            "dispatchInsightType": policy_dispatch_type,
            "dispatchSourceKey": policy_source_key,
            "holdingPolicyGroup": policy_group,
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
            "dataFreshness": promoted_data_freshness(events),
            "dataFreshnessRequired": True,
            "dispatchPolicy": {
                "mode": "insight-driven-only",
                "policyGroup": policy_group or "default",
                "cooldownPolicy": "insight-cadence-key",
                "noveltyPolicy": "source-relation-change",
                "suppressionPolicy": "legacy-signal-direct-dispatch-disabled",
                "graphSourceRequired": True,
            },
            "sourceSignalRole": "graph-backed-evidence",
        }
        metadata.update(promoted_context)
        insights.append(AlertEvent(
            snapshot.account_id,
            snapshot.account_label,
            highest_severity(events),
            INSIGHT_RULE,
            insight_id,
            subject_name,
            [
                "인사이트 유형: " + insight_label,
                *reference_lines,
                "핵심 결론: " + thesis,
                *active_lines,
                "근거 신호: " + ", ".join(source_labels),
                *["근거: " + line for line in source_lines],
                "다음 확인: " + next_check,
            ],
            "" if subject in {"portfolio", "macro"} else subject,
            criteria=criteria,
            metadata=metadata,
        ))
    return insights
