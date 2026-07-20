import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List

from .notification_templates import symbol_display_name, symbol_with_code
from .ontology_decision_state import CHANGE_STATE_LABELS, DATA_STATE_LABELS, REVIEW_LEVEL_LABELS, review_level_for
from .portfolio import utc_now_iso


MODEL_REVIEW_PROMPT_VERSION = "model-review-v2-ontology"
MODEL_REVIEW_FOLLOWUP_NOTICE = "이 메시지는 실시간 알림 이후 생성된 후속 분석입니다. 실시간 판단과 겹치는 내용은 줄이고, 나중에 모델을 고칠 때 볼 기록으로 저장됩니다."
ACTIONABLE_REVIEW_ACTIONS = {"SELL", "TRIM", "AVOID"}
DECISION_LINE = re.compile(r"^(이전|현재)[:\s]+(.+)$")
LEGACY_ACTION_LABELS = {
    "손절 기준 확인": "손절·분할축소 권장",
    "손실 관리 기준 확인": "손실 축소 권장",
    "손실 기준 근접 관찰": "손실 방어 관망",
    "분할 매도 기준 확인": "분할매도 권장",
    "익절 조건 점검": "일부 익절 권장",
    "일부 익절 기준 확인": "일부 익절 권장",
    "리밸런싱 기준 확인": "리밸런싱 권장",
    "조건부 보유": "보유 유지",
}


@dataclass
class ModelReviewJob:
    job_id: str
    account_id: str
    account_label: str
    symbol: str
    title: str
    alert_key: str
    alert_lines: List[str]
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""
    status: str = "pending"
    attempts: int = 0
    result: str = ""
    last_error: str = ""
    review_context: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def create(cls, payload: Dict[str, object]) -> "ModelReviewJob":
        seed = str(payload.get("key") or payload.get("alertKey") or uuid.uuid4().hex)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        review_context = dict(
            metadata.get("ontologyRelationContext")
            or metadata.get("ontologyReviewContext")
            or metadata.get("ontologyPromptContext")
            or metadata.get("ontologyOpinion")
            or {}
        )
        decision_change = metadata.get("decisionChangeContext") or metadata.get("decisionChange")
        if isinstance(decision_change, dict) and decision_change:
            review_context["decisionChange"] = dict(decision_change)
        active_opinion = metadata.get("activeInvestmentOpinion")
        if isinstance(active_opinion, dict) and active_opinion:
            review_context["activeInvestmentOpinion"] = dict(active_opinion)
        return cls(
            job_id=uuid.uuid5(uuid.NAMESPACE_URL, "digital-twin:model-review:" + seed).hex,
            account_id=str(payload.get("accountId") or ""),
            account_label=str(payload.get("accountLabel") or ""),
            symbol=str(payload.get("symbol") or ""),
            title=str(payload.get("title") or ""),
            alert_key=seed,
            alert_lines=[str(line) for line in payload.get("lines") or [] if str(line).strip()],
            review_context=review_context,
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "ModelReviewJob":
        return cls(
            job_id=str(payload.get("jobId") or payload.get("job_id") or uuid.uuid4().hex),
            account_id=str(payload.get("accountId") or ""),
            account_label=str(payload.get("accountLabel") or ""),
            symbol=str(payload.get("symbol") or ""),
            title=str(payload.get("title") or ""),
            alert_key=str(payload.get("alertKey") or ""),
            alert_lines=[str(line) for line in payload.get("alertLines") or [] if str(line).strip()],
            created_at=str(payload.get("createdAt") or utc_now_iso()),
            updated_at=str(payload.get("updatedAt") or ""),
            status=str(payload.get("status") or "pending"),
            attempts=int(payload.get("attempts") or 0),
            result=str(payload.get("result") or ""),
            last_error=str(payload.get("lastError") or ""),
            review_context=dict(payload.get("reviewContext") or {}),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "jobId": payload["job_id"],
            "accountId": payload["account_id"],
            "accountLabel": payload["account_label"],
            "symbol": payload["symbol"],
            "title": payload["title"],
            "alertKey": payload["alert_key"],
            "alertLines": payload["alert_lines"],
            "createdAt": payload["created_at"],
            "updatedAt": payload["updated_at"],
            "status": payload["status"],
            "attempts": payload["attempts"],
            "result": payload["result"],
            "lastError": payload["last_error"],
            "reviewContext": payload["review_context"],
        }


def review_subject(job: ModelReviewJob) -> str:
    symbol = str(job.symbol or "").strip().upper()
    display = symbol_display_name(symbol, job.title)
    if symbol:
        return symbol_with_code(display, symbol)
    return str(job.title or display or "판단 변화").strip()


def normalize_model_review_result(job: ModelReviewJob, result: str) -> str:
    text_result = str(result or "").strip()
    if not text_result:
        return ""
    subject = review_subject(job)
    if not subject:
        return text_result
    lines = text_result.splitlines()
    if not lines:
        return text_result
    first = lines[0].strip()
    if first.startswith("🧠 판단 변화 후속 리뷰:"):
        return text_result
    candidates = [
        str(job.symbol or "").strip().upper(),
        str(job.title or "").strip(),
        symbol_display_name(job.symbol, job.title),
    ]
    for candidate in [item for item in candidates if item]:
        if first == candidate:
            lines[0] = subject
            break
        if first.startswith(candidate + " "):
            lines[0] = subject + first[len(candidate):]
            break
    if first in {"모델 리뷰", "판단 변화 리뷰"}:
        lines[0] = subject + " " + first
    return followup_model_review_message(subject, lines)


def followup_model_review_message(subject: str, lines: List[str]) -> str:
    body = [line for line in lines[1:] if str(line or "").strip()]
    if body and body[0].strip() == MODEL_REVIEW_FOLLOWUP_NOTICE:
        body = body[1:]
    title = "🧠 판단 변화 후속 리뷰: " + str(subject or "판단 변화").strip()
    return "\n".join([title, "", MODEL_REVIEW_FOLLOWUP_NOTICE, "", *body]).strip()


def model_review_telegram_mode(settings: Dict[str, object] = None) -> str:
    value = str((settings or {}).get("modelReviewTelegramMode") or "actionableOnly").strip()
    normalized = value.lower().replace("-", "").replace("_", "")
    if normalized in {"all", "always", "1", "true", "on"}:
        return "all"
    if normalized in {"off", "none", "never", "0", "false"}:
        return "off"
    return "actionableOnly"


def model_review_action(job: ModelReviewJob) -> str:
    context = job.review_context if isinstance(job.review_context, dict) else {}
    opinion = context.get("activeInvestmentOpinion") if isinstance(context.get("activeInvestmentOpinion"), dict) else {}
    return str(opinion.get("action") or opinion.get("primaryAction") or "").strip().upper()


def should_deliver_model_review(job: ModelReviewJob, result: str, settings: Dict[str, object] = None) -> bool:
    if not str(result or "").strip():
        return False
    mode = model_review_telegram_mode(settings)
    if mode == "all":
        return True
    if mode == "off":
        return False
    return model_review_action(job) in ACTIONABLE_REVIEW_ACTIONS


def build_model_review_prompt(job: ModelReviewJob) -> str:
    lines = "\n".join(["- " + line for line in job.alert_lines])
    ontology_context = ""
    if job.review_context:
        ontology_context = "\n".join([
            "",
            "관계 분석/AI 투자 의견 정보:",
            str(job.review_context),
        ])
    return "\n".join([
        "너는 투자 판단 기준을 지속적으로 개선하는 금융 데이터 리뷰어다.",
        "이번 모델은 관계 규칙, 근거, 부족 데이터, 근거끼리의 충돌을 우선한다.",
        "적극 투자 의견이 있으면 BUY/ADD/HOLD/TRIM/SELL/AVOID 선택 근거, 반대 근거, 무효화 조건을 검토한다.",
        "자동 주문 지시가 아니라 판단 변화의 원인, 데이터 검증, 다음 실험을 분석한다.",
        "한국어로 텔레그램 메시지에 맞게 간결하지만 충분히 분석해라. 영어 또는 어려운 용어는 쉬운 한국어로 풀어 써라.",
        "메시지 제목에는 계정명이나 계정 ID를 넣지 마라. 계정 정보는 전송 라우팅에만 사용한다.",
        "메시지 첫 줄에는 종목코드만 쓰지 말고 반드시 종목명 / 종목코드 형태의 대상을 먼저 써라.",
        "섹션은 반드시 다음 순서로 작성한다: 성립 규칙, 관계/반대 신호, 부족 데이터, 모델 보완, 다음 실험.",
        "판단명이 바뀐 경우에는 선택 규칙, 성립 규칙 조합, 자료 상태, 라벨 체계 변경 중 무엇 때문인지 분리해서 설명해라.",
        "확률, 확신도, 관계 점수, 종합 점수는 만들지 말고 확인 단계와 자료 상태로 설명해라.",
        "API 키, 토큰, 계좌 식별정보를 추정하거나 요청하지 마라.",
        "",
        "리뷰 버전: " + MODEL_REVIEW_PROMPT_VERSION,
        "계정: " + (job.account_label or job.account_id or "-"),
        "종목: " + (review_subject(job) or "-"),
        "알림 제목: " + (job.title or "-"),
        "알림 키: " + job.alert_key,
        "실시간 알림 내용:",
        lines or "- 없음",
        ontology_context,
    ])


def parsed_alert_decisions(lines: List[str]) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw_line in lines or []:
        line = str(raw_line or "").strip()
        match = DECISION_LINE.match(line)
        if not match:
            continue
        parsed[match.group(1)] = match.group(2).strip()
    return parsed


def relation_context_from_decision(decision: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(decision, dict):
        return {}
    context = decision.get("relation_rule_context") if "relation_rule_context" in decision else decision.get("relationRuleContext")
    return dict(context or {}) if isinstance(context, dict) else {}


def selected_rule_id(decision: Dict[str, object]) -> str:
    context = relation_context_from_decision(decision)
    nested = context.get("decision") if isinstance(context, dict) else {}
    if isinstance(nested, dict):
        return str(nested.get("selectedRuleId") or nested.get("selected_rule_id") or "").strip()
    return ""


def active_rule_labels(decision: Dict[str, object]) -> List[str]:
    context = relation_context_from_decision(decision)
    rules = context.get("activeRules") or context.get("matchedRules") or []
    labels: List[str] = []
    for item in rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
        if label:
            labels.append(label)
    return labels


def selected_rule_label(decision: Dict[str, object]) -> str:
    context = relation_context_from_decision(decision)
    rules = context.get("activeRules") or context.get("matchedRules") or []
    selected = selected_rule_id(decision)
    fallback = ""
    for item in rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
        rule_id = str(item.get("ruleId") or item.get("rule_id") or "").strip()
        if label and not fallback:
            fallback = label
        if selected and rule_id == selected and label:
            return label
    return fallback


def decision_change_context(
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pressure_threshold: float,
) -> Dict[str, object]:
    del pressure_threshold
    current_label = text(current_decision, "decision")
    previous_label = text(previous_decision, "decision")
    previous_rules = active_rule_labels(previous_decision)
    current_rules = active_rule_labels(current_decision)
    previous_relation = relation_context_from_decision(previous_decision)
    current_relation = relation_context_from_decision(current_decision)

    def state_snapshot(relation: Dict[str, object]) -> Dict[str, str]:
        decision = relation.get("decision") if isinstance(relation.get("decision"), dict) else {}
        data_state = str(decision.get("dataState") or relation.get("dataState") or "partial")
        review_level = str(decision.get("reviewLevel") or relation.get("reviewLevel") or "")
        if not review_level:
            review_level = review_level_for(decision.get("actionLevel"), data_state)
        return {
            "reviewLevel": review_level,
            "reviewLabel": str(decision.get("reviewLabel") or relation.get("reviewLevelLabel") or REVIEW_LEVEL_LABELS.get(review_level, "변화 관찰")),
            "dataState": data_state,
            "dataStateLabel": str(decision.get("dataStateLabel") or relation.get("dataStateLabel") or DATA_STATE_LABELS.get(data_state, "일부 자료만 있음")),
        }

    previous_state = state_snapshot(previous_relation)
    current_state = state_snapshot(current_relation)
    added_rules = [item for item in current_rules if item not in previous_rules]
    removed_rules = [item for item in previous_rules if item not in current_rules]
    if current_label and previous_label and current_label != previous_label:
        change_state = "direction-changed"
    elif added_rules or removed_rules:
        change_state = "new-evidence"
    else:
        change_state = "unchanged"
    return {
        "previous": {
            "label": previous_label,
            "selectedRuleId": selected_rule_id(previous_decision),
            "selectedRuleLabel": selected_rule_label(previous_decision),
            "activeRules": previous_rules,
            **previous_state,
        },
        "current": {
            "label": current_label,
            "selectedRuleId": selected_rule_id(current_decision),
            "selectedRuleLabel": selected_rule_label(current_decision),
            "activeRules": current_rules,
            **current_state,
        },
        "labelChanged": bool(current_label and previous_label and current_label != previous_label),
        "changeState": change_state,
        "changeStateLabel": CHANGE_STATE_LABELS[change_state],
        "addedRules": added_rules,
        "removedRules": removed_rules,
    }


def label_transition_reason(previous_label: str, current_label: str) -> str:
    previous = str(previous_label or "").strip()
    current = str(current_label or "").strip()
    if LEGACY_ACTION_LABELS.get(previous) == current:
        return "판단 라벨 체계가 확인형에서 행동형 권장 단계로 정리된 전환"
    return "선택 규칙, 성립 규칙 조합, 라벨 체계 중 하나가 바뀐 전환"


def particle_to(label: str) -> str:
    text_label = str(label or "").strip()
    if not text_label:
        return "로"
    code = ord(text_label[-1])
    if 0xAC00 <= code <= 0xD7A3:
        jong = (code - 0xAC00) % 28
        return "로" if jong in {0, 8} else "으로"
    return "로"


def label_change_phrase(previous_label: str, current_label: str) -> str:
    previous = str(previous_label or "-").strip() or "-"
    current = str(current_label or "-").strip() or "-"
    return previous + "에서 " + current + particle_to(current)


def label_change_reason(change: Dict[str, object]) -> str:
    previous = change.get("previous") if isinstance(change.get("previous"), dict) else {}
    current = change.get("current") if isinstance(change.get("current"), dict) else {}
    previous_label = str(previous.get("label") or "-")
    current_label = str(current.get("label") or "-")
    reason = label_transition_reason(previous_label, current_label)
    previous_rule = str(previous.get("selectedRuleLabel") or previous.get("selectedRuleId") or "").strip()
    current_rule = str(current.get("selectedRuleLabel") or current.get("selectedRuleId") or "").strip()
    rule_text = ""
    if previous_rule and current_rule and previous_rule != current_rule:
        rule_text = " 선택 규칙도 " + previous_rule + "에서 " + current_rule + "로 바뀌었습니다"
    elif current_rule:
        rule_text = " 선택 규칙은 " + current_rule + "로 유지됩니다"
    return (
        "판단명이 " + label_change_phrase(previous_label, current_label) + " 바뀌었습니다. "
        + reason + "이므로 가격 악화로 단정하지 않습니다." + rule_text
    )


def local_model_review(job: ModelReviewJob) -> str:
    joined = "\n".join(job.alert_lines)
    alert_decisions = parsed_alert_decisions(job.alert_lines)
    previous_alert = alert_decisions.get("이전")
    current_alert = alert_decisions.get("현재")
    label_changed = bool(previous_alert and current_alert and previous_alert != current_alert)
    validation = "실시간 알림의 데이터 검증 라인을 우선 확인하고, 가격/수량/평가액/손익률 원천이 모두 같은 시점인지 대조하세요."
    improvement = "거래량, 이동평균, 평가액 변화를 판단 요소로 추가해 같은 판단 변화가 반복 재현되는지 검증하세요."
    if label_changed:
        validation = "판단명이 달라졌으므로 가격 변화뿐 아니라 선택 규칙, 성립 규칙 조합, 자료 상태, 라벨 체계 변경 여부를 먼저 대조하세요."
        improvement = "판단명이 바뀐 알림은 선택 규칙 ID와 라벨 버전을 함께 저장하고, 라벨 체계 변경만 원인이면 반복 알림을 막으세요."
    if "손익률 급변" in joined:
        validation = "손익률 급변이 가격 원천 변경, 환율, 분할/배당, 장중 급등락 중 무엇에서 왔는지 먼저 분리하세요."
        improvement = "손익률 단독 변화와 거래량·이동평균이 함께 바뀐 경우를 분리하고, 자료 상태가 충분할 때만 판단에 사용하세요."
    if "현재가/평균매입가 없음" in joined or "현재가/평단 없음" in joined or "평가액 없음" in joined:
        validation = "가격 또는 평가액 필드가 부족하므로 판단 변화의 근거가 약합니다. 원천 API 매핑부터 보완하세요."
        improvement = "필수 판단 요소가 빠졌을 때는 판단을 보류하고 부족한 자료를 명시하세요."
    ontology_line = "관계 분석 정보가 없어서 알림 라인과 관측 데이터 근거만 사용했습니다."
    if job.review_context:
        opinion = job.review_context.get("opinion") if isinstance(job.review_context, dict) else {}
        worldview = job.review_context.get("worldview") if isinstance(job.review_context, dict) else {}
        thesis = str((opinion or {}).get("thesis") or "").strip()
        dominant_sector = str((worldview or {}).get("dominantSector") or "").strip()
        matched_rules = job.review_context.get("activeRules") or job.review_context.get("matchedRules") or []
        rule_names = [
            str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "")
            for item in matched_rules
            if isinstance(item, dict) and not item.get("referenceOnly") and not item.get("reference_only")
        ]
        if rule_names:
            ontology_line = "성립 규칙은 " + " · ".join(rule_names[:3]) + "입니다."
        else:
            ontology_line = (
                "보유 이유는 " + (thesis or "요약 없음")
                + ("이며, 지배 섹터는 " + dominant_sector + "입니다." if dominant_sector else "입니다.")
            )
    if label_changed and previous_alert and current_alert:
        ontology_line = (
            "판단명이 " + label_change_phrase(previous_alert, current_alert) + " 바뀌었습니다. "
            + label_transition_reason(previous_alert, current_alert) + "입니다."
        )
    return "\n".join([
        review_subject(job) + " 모델 리뷰",
        "- 성립 규칙: 실시간 판단 기준에서 새 관계가 성립했거나 기존 관계가 해제됐습니다.",
        "- 관계/모순: " + ontology_line,
        "- 데이터 검증: " + validation,
        "- 모델 보완: " + improvement,
        "- 다음 실험: 동일 조건을 최근 20회 판단 변화에 다시 적용해 잘못 울린 알림과 이후 손익 흐름을 비교하세요.",
    ])


def value(payload: Dict[str, object], key: str) -> float:
    try:
        return float(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def text(payload: Dict[str, object], key: str) -> str:
    return str(payload.get(key) or "").strip()


def first_sentence(items: List[str], fallback: str) -> str:
    return "; ".join(items[:2]) if items else fallback


def signed_pct(number: float, suffix: str = "%") -> str:
    rounded = round(float(number or 0), 1)
    return ("+" if rounded > 0 else "") + str(rounded) + suffix


def pct_delta(current: float, previous: float) -> float:
    base = float(previous or 0)
    if not base:
        return 0.0
    return ((float(current or 0) / base) - 1) * 100


def decision_change_review_lines(
    current_position: Dict[str, object],
    previous_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pressure_threshold: float,
) -> List[str]:
    change = decision_change_context(current_decision, previous_decision, pressure_threshold)
    pnl_delta = value(current_position, "profit_loss_rate") - value(previous_position, "profit_loss_rate")
    market_value_delta = pct_delta(value(current_position, "market_value"), value(previous_position, "market_value"))
    decision_changed = text(current_decision, "decision") != text(previous_decision, "decision")

    reasons: List[str] = []
    if decision_changed:
        reasons.append(label_change_reason(change))

    drivers: List[str] = []
    if abs(pnl_delta) >= 1:
        drivers.append("손익률 " + signed_pct(pnl_delta, "%p"))
    if abs(market_value_delta) >= 3:
        drivers.append("평가액 " + signed_pct(market_value_delta))
    if value(current_position, "quantity") != value(previous_position, "quantity"):
        drivers.append("수량 " + str(previous_position.get("quantity", 0)) + " -> " + str(current_position.get("quantity", 0)))
    previous = change.get("previous") if isinstance(change.get("previous"), dict) else {}
    current = change.get("current") if isinstance(change.get("current"), dict) else {}
    if previous.get("reviewLevel") != current.get("reviewLevel"):
        drivers.append("확인 단계 " + str(previous.get("reviewLabel") or "-") + " → " + str(current.get("reviewLabel") or "-"))
    if previous.get("dataState") != current.get("dataState"):
        drivers.append("자료 상태 " + str(previous.get("dataStateLabel") or "-") + " → " + str(current.get("dataStateLabel") or "-"))
    added_rules = change.get("addedRules") if isinstance(change.get("addedRules"), list) else []
    removed_rules = change.get("removedRules") if isinstance(change.get("removedRules"), list) else []
    if added_rules:
        drivers.append("새 성립 규칙 " + " · ".join(str(item) for item in added_rules[:2]))
    if removed_rules:
        drivers.append("해제 규칙 " + " · ".join(str(item) for item in removed_rules[:2]))

    validation = model_data_validation(current_position, previous_position, current_decision, previous_decision, pnl_delta)
    improvement = model_improvement_hint(current_position, current_decision, previous_decision, pnl_delta)

    return [
        "Codex 답변: " + first_sentence(reasons, "판단 기준에 의미 있는 변화가 감지됨") + ". 주요 변화는 " + first_sentence(drivers, "관계 조합 변화") + "입니다.",
        "변화 상태: " + str(change.get("changeStateLabel") or CHANGE_STATE_LABELS["unchanged"]),
        "데이터 검증: " + validation,
        "모델 보완: " + improvement,
    ]


def model_data_validation(
    current_position: Dict[str, object],
    previous_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pnl_delta: float,
) -> str:
    issues: List[str] = []
    if not text(current_position, "symbol"):
        issues.append("종목코드 누락")
    if value(current_position, "market_value") <= 0:
        issues.append("평가액 없음")
    if value(current_position, "quantity") <= 0:
        issues.append("보유수량 없음")
    if value(current_position, "current_price") <= 0 and value(current_position, "average_price") <= 0:
        issues.append("현재가/평단 없음")
    if not text(current_position, "sector"):
        issues.append("섹터 미분류")
    if not text(current_decision, "decision") or not text(previous_decision, "decision"):
        issues.append("판단 라벨 누락")
    if abs(pnl_delta) >= 12:
        issues.append("손익률 급변 원천 확인")
    if value(previous_position, "market_value") <= 0:
        issues.append("이전 평가액 기준 약함")
    if issues:
        return "확인 필요 - " + ", ".join(issues[:4])
    return "평가액, 수량, 손익률, 판단 라벨이 모두 비교 가능"


def model_improvement_hint(
    current_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pnl_delta: float,
) -> str:
    if text(current_decision, "decision") != text(previous_decision, "decision"):
        return "판단 라벨 버전, 선택 규칙 ID, 자료 상태를 함께 저장하고 실제 관계 변화가 없으면 반복 알림을 억제"
    if abs(pnl_delta) >= 5:
        return "거래량과 이동평균으로 손익률 급변이 추세인지 일시 변동인지 검증"
    if value(current_position, "current_price") <= 0:
        return "현재가 원천을 연결한 뒤에만 투자 판단을 다시 생성"
    if not text(current_position, "sector"):
        return "업종 매핑을 보강해 집중도 관계의 잘못된 알림을 줄이기"
    return "거래량, 이동평균, 평가액 변화를 판단 요소로 추가해 판단 변화의 재현성을 검증"
