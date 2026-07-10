import json
import re
from typing import Dict, List

from .notification_ai import active_investment_opinion_value, line_value
from .notification_ai_gate_contracts import ACTION_TEXT_REPLACEMENTS, VALID_ACTIONS


INTERNAL_VARIABLE_REPLACEMENTS = [
    (
        re.compile(r"entryAllocationRoom.*entrySupportCount.*entryExternalRiskBlocked.*?(?:다|\.|$)"),
        "추가매수 여력과 일부 지지 신호는 있지만, 공시·뉴스 같은 외부 위험 때문에 추가매수 근거로 보기는 어렵다.",
    ),
    (re.compile(r"\bentryAllocationRoom\b\s*(?:=|:|이|가)?\s*true", re.IGNORECASE), "추가매수 여력 있음"),
    (re.compile(r"\bentryAllocationRoom\b\s*(?:=|:|이|가)?\s*false", re.IGNORECASE), "추가매수 여력 부족"),
    (re.compile(r"\bentrySupportCount\b\s*(?:=|:|이|가)?\s*(\d+)", re.IGNORECASE), r"추가매수 지지 신호 \1개"),
    (re.compile(r"\bentryExternalRiskBlocked\b\s*(?:=|:|이|가)?\s*true", re.IGNORECASE), "공시·뉴스 같은 외부 위험으로 추가매수 보류"),
    (re.compile(r"\bentryExternalRiskBlocked\b\s*(?:=|:|이|가)?\s*false", re.IGNORECASE), "외부 위험 차단 조건 없음"),
    (re.compile(r"\bentryAllocationRoom\b", re.IGNORECASE), "추가매수 여력"),
    (re.compile(r"\bentrySupportCount\b", re.IGNORECASE), "추가매수 지지 신호 수"),
    (re.compile(r"\bentryExternalRiskBlocked\b", re.IGNORECASE), "외부 위험 차단 조건"),
    (re.compile(r"\bmissingData\b", re.IGNORECASE), "부족 데이터"),
    (re.compile(r"\brawLines\b", re.IGNORECASE), "알림 원문 데이터"),
    (re.compile(r"\bsourceFacts\b", re.IGNORECASE), "판단에 사용한 데이터"),
    (re.compile(r"\bontologyRelationContext\b", re.IGNORECASE), "관계 분석 데이터"),
    (re.compile(r"\bactiveInvestmentOpinion\b", re.IGNORECASE), "현재 투자 의견"),
    (re.compile(r"\bexecutionPlan\b", re.IGNORECASE), "실행 점검 계획"),
    (re.compile(r"\bcounterEvidence\b", re.IGNORECASE), "반대 근거"),
    (re.compile(r"\bnextChecks\b", re.IGNORECASE), "다음 확인"),
    (re.compile(r"\breferenceDate\b", re.IGNORECASE), "기준시각"),
    (re.compile(r"\bprimaryActionLabel\b", re.IGNORECASE), "우선 행동"),
    (re.compile(r"\bprimaryAction\b", re.IGNORECASE), "우선 행동"),
    (re.compile(r"\briskSignals\b", re.IGNORECASE), "위험 신호"),
    (re.compile(r"\bsupportSignals\b", re.IGNORECASE), "지지 신호"),
    (re.compile(r"\bweakenConditions\b", re.IGNORECASE), "의견이 약해지는 조건"),
]
INTERNAL_VARIABLE_TEXT_REPLACEMENTS = [
    ("entryAllocationRoom", "추가매수 여력"),
    ("entrySupportCount", "추가매수 지지 신호 수"),
    ("entryExternalRiskBlocked", "외부 위험 차단 조건"),
    ("missingData", "부족 데이터"),
    ("rawLines", "알림 원문 데이터"),
    ("sourceFacts", "판단에 사용한 데이터"),
    ("ontologyRelationContext", "관계 분석 데이터"),
    ("activeInvestmentOpinion", "현재 투자 의견"),
    ("executionPlan", "실행 점검 계획"),
    ("counterEvidence", "반대 근거"),
    ("nextChecks", "다음 확인"),
    ("referenceDate", "기준시각"),
    ("primaryActionLabel", "우선 행동"),
    ("primaryAction", "우선 행동"),
    ("riskSignals", "위험 신호"),
    ("supportSignals", "지지 신호"),
    ("weakenConditions", "의견이 약해지는 조건"),
]
USER_FRIENDLY_REPLACEMENTS = [
    ("손실 보유 + 기준선 이탈 -> 손실 관리", "손실이 커지고 주요 평균선 아래에 있어 손실 관리"),
    ("추세 훼손 + 하락 가속 -> 리스크 강화", "주요 평균선 아래에서 하락 속도가 빨라져 위험 증가"),
    ("보유 종목 + 추세 훼손 -> 추가매수 보류", "보유 종목의 가격 흐름이 약해져 추가매수 보류"),
    ("단기선 이탈 + 60일선 지지 -> 지지선 재확인", "20일선 아래지만 60일선 근처라 지지 여부 재확인"),
    ("수익 보유 + 추세 약화 -> 익절 점검", "수익 중이지만 가격 흐름이 약해져 분할매도 점검"),
    ("업종 집중 + 보유 비중 과대 -> 리밸런싱 점검", "한 업종이나 종목 비중이 커서 비중 조정 점검"),
    ("비트코인 급변 + 민감 종목 -> 연동 점검", "비트코인 변동에 민감한 종목이라 함께 점검"),
    ("기준선 이탈이 해소", "주요 평균선 아래 상태가 해소"),
    ("하락 가속이 멈추", "하락 속도가 더 빨라지는 흐름이 멈추"),
    ("기준선 이탈", "주요 평균선 아래로 내려감"),
    ("추세 훼손", "가격 흐름 약화"),
    ("하락 가속", "하락 속도 증가"),
    ("리스크 강화", "위험 증가"),
    ("리스크", "위험"),
    ("괴리", "차이"),
    ("feature 기여도", "판단에 영향을 준 항목"),
    ("feature", "판단 항목"),
    ("thesis", "보유 이유"),
    ("무효화 조건", "의견이 약해지는 조건"),
]

def _raw_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("rawLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def _text(value: object, limit: int = 220) -> str:
    cleaned = " ".join(str(value or "").split())
    if limit > 3 and len(cleaned) > limit:
        return cleaned[: limit - 3].rstrip() + "..."
    return cleaned


def _list(value: object, limit: int = 5) -> List[str]:
    if isinstance(value, list):
        result = [_text(item, 180) for item in value if _text(item, 180)]
    elif value:
        result = [_text(value, 180)]
    else:
        result = []
    seen = set()
    unique: List[str] = []
    for item in result:
        if item not in seen:
            seen.add(item)
            unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value or 0)))


def _strip_code_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def parse_ai_response_json(text: str) -> Dict[str, object]:
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return {}
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(cleaned[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _line_after_colon(lines: List[str], label: str) -> str:
    return line_value(lines, label)


def reference_date(context: Dict[str, object]) -> str:
    lines = _raw_lines(context)
    return (
        str(context.get("referenceDate") or "").strip()
        or _line_after_colon(lines, "기준일")
        or str(context.get("sentTime") or "").strip()
        or str(context.get("eventGeneratedAt") or "").strip()
    )


def fallback_action_from_label(value: object) -> str:
    text = str(value or "").upper()
    if "ADD" in text or "추가매수" in text:
        return "ADD"
    if "BUY" in text or ("매수" in text and "보류" not in text):
        return "BUY"
    if "TRIM" in text or "분할" in text or "축소" in text:
        return "TRIM"
    if "SELL" in text or "매도" in text or "손절" in text:
        return "SELL"
    if "AVOID" in text or "회피" in text or "보류" in text:
        return "AVOID"
    return "HOLD"


def user_friendly_ai_text(value: object, limit: int = 220) -> str:
    result = _text(value, limit)
    if not result:
        return ""
    for pattern, replacement in INTERNAL_VARIABLE_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    for before, after in INTERNAL_VARIABLE_TEXT_REPLACEMENTS:
        result = result.replace(before, after)
    for before, after in USER_FRIENDLY_REPLACEMENTS:
        result = result.replace(before, after)
    for action, label in ACTION_TEXT_REPLACEMENTS.items():
        result = re.sub(r"\b" + action + r"\s*의견", label + " 의견", result)
        result = re.sub(r"\b" + action + r"\s*을\s*선택", label + " 의견을 선택", result)
        result = re.sub(r"\b" + action + r"\s*를\s*선택", label + " 의견을 선택", result)
        result = re.sub(r"\b" + action + r"\b", label, result)
    result = result.replace("->", "→")
    result = re.sub(r"\btrue\b", "예", result, flags=re.IGNORECASE)
    result = re.sub(r"\bfalse\b", "아니오", result, flags=re.IGNORECASE)
    result = result.replace("주요 평균선 아래로 내려감이", "주요 평균선 아래 상태가")
    result = result.replace("하락 속도 증가이", "하락 속도 증가가")
    result = result.replace("조건를", "조건을")
    result = result.replace("..", ".")
    result = re.sub(r"\s+", " ", result).strip()
    return result


def user_friendly_ai_list(value: object, limit: int = 5) -> List[str]:
    return _list([user_friendly_ai_text(item, 180) for item in _list(value, limit * 2)], limit)


def append_unique_text(rows: List[str], value: object, limit: int = 180) -> None:
    text = user_friendly_ai_text(value, limit)
    if text and text not in rows:
        rows.append(text)


def precomputed_action_value(context: Dict[str, object]) -> str:
    opinion = active_investment_opinion_value(context)
    action = str(opinion.get("action") or opinion.get("primaryAction") or "").strip().upper() if isinstance(opinion, dict) else ""
    return action if action in VALID_ACTIONS else ""


def recursive_values(value: object, keys: set, limit: int = 16) -> List[str]:
    rows: List[str] = []

    def visit(item: object) -> None:
        if len(rows) >= limit:
            return
        if isinstance(item, dict):
            for key, raw in item.items():
                normalized = str(key or "").strip()
                if normalized in keys:
                    if isinstance(raw, list):
                        for part in raw:
                            append_unique_text(rows, part, 260)
                    else:
                        append_unique_text(rows, raw, 260)
                elif isinstance(raw, (dict, list)):
                    visit(raw)
        elif isinstance(item, list):
            for part in item:
                visit(part)

    visit(value)
    return rows[:limit]

def soften_order_language(text: str) -> str:
    replacements = {
        "무조건 매수": "매수 조건 검토",
        "무조건 추가매수": "추가매수 조건 검토",
        "무조건 매도": "매도 조건 검토",
        "반드시 매수": "매수 조건 검토",
        "반드시 매도": "매도 조건 검토",
        "즉시 매수": "매수 전 최종 확인",
        "즉시 매도": "매도 전 최종 확인",
    }
    result = str(text or "")
    for before, after in replacements.items():
        result = result.replace(before, after)
    return result
