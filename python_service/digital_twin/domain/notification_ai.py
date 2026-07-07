from typing import Dict, List, Optional

from .message_types import MESSAGE_TYPE_LABELS
from .ontology_rules import (
    AI_PROMPT_REGISTRY_VERSION,
    default_ai_prompt_policy_text,
    prompt_template_for_message_type,
)


SKIP_AI_OPINION_TYPES = {"workHandoff", "modelReview"}
AI_OPINION_ENGINE_VERSION = "notification-ai-opinion-v1"


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
            "referenceDate": str(context.get("referenceDate") or ""),
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

    if message_type == "holdingTiming":
        rule_text = ", ".join(rules[:2]) if rules else (state or "보유 타이밍 조건")
        next_check = action or "비중 확대 여부보다 손실 기준, 분할 대응 기준, 추세 회복 조건을 먼저 확인하세요."
        return [
            "해석: " + target + "에서 " + rule_text + " 신호가 성립했습니다.",
            "의견: " + (state or "보유 판단") + ("이고 " + pnl if pnl else "") + "라서 새 매수보다 기존 보유 thesis와 비중 관리 기준을 먼저 보는 쪽이 맞습니다.",
            "다음 확인: " + next_check,
        ]
    if message_type == "monitorDecisionChange":
        previous_value = line_value(lines, "이전")
        current_value = line_value(lines, "현재")
        return [
            "해석: 판단명이 바뀐 알림입니다. " + " -> ".join(part for part in [previous_value, current_value] if part),
            "의견: 점수 변화만 보지 말고 선택 규칙과 성립 규칙 조합이 바뀌었는지 먼저 확인해야 합니다.",
            "다음 확인: 같은 판단이 다음 조회에서도 유지되는지, 임계값 근처 흔들림인지 구분하세요.",
        ]
    if message_type in {"modelBuy", "watchlistBuyCandidate"}:
        return [
            "해석: 매수 후보 기준을 통과했습니다.",
            "의견: 바로 비중을 키우기보다 첫 진입은 작게 두고 손절 기준과 추가매수 조건을 먼저 정하는 쪽이 안전합니다.",
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
