"""One customer-language contract for investment insight documents.

The reasoning graph and AI response keep their exact technical vocabulary for
audit.  This module owns the smaller vocabulary that is allowed to cross the
customer boundary, independently of which engine produced the insight.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .alert_formatting import compact_number, price_money
from .customer_evidence_explanation import customer_safe_text, customer_text_quality_issues
from .investment_ubiquitous_language import user_facing_investment_language
from .notification_text_formatting import (
    absolute_beginner_friendly_text,
    beginner_friendly_text,
)


CUSTOMER_INVESTMENT_DOCUMENT_VERSION = "customer-investment-document-v1"
CUSTOMER_INVESTMENT_DOCUMENT_QUALITY_VERSION = "customer-investment-document-quality-v1"


@dataclass(frozen=True)
class CustomerInvestmentSection:
    key: str
    title: str
    rows: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "rows": list(self.rows),
        }


@dataclass(frozen=True)
class CustomerInvestmentLink:
    label: str
    url: str

    def to_dict(self) -> Dict[str, str]:
        return {"label": self.label, "url": self.url}


@dataclass(frozen=True)
class CustomerInvestmentDocument:
    role: str
    headline: str
    target: str = ""
    role_label: str = ""
    lead: str = ""
    sections: Tuple[CustomerInvestmentSection, ...] = ()
    links: Tuple[CustomerInvestmentLink, ...] = ()
    detail_url: str = ""
    reference_at: str = ""
    sent_at: str = ""
    notification_number: str = ""
    language_level: str = "beginner"
    version: str = CUSTOMER_INVESTMENT_DOCUMENT_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["sections"] = [item.to_dict() for item in self.sections]
        payload["links"] = [item.to_dict() for item in self.links]
        payload["referenceAt"] = payload.pop("reference_at")
        payload["sentAt"] = payload.pop("sent_at")
        payload["notificationNumber"] = payload.pop("notification_number")
        payload["roleLabel"] = payload.pop("role_label")
        payload["detailUrl"] = payload.pop("detail_url")
        payload["languageLevel"] = payload.pop("language_level")
        return payload


def customer_investment_document_from_dict(
    payload: object,
) -> Optional[CustomerInvestmentDocument]:
    """Restore only the versioned customer artifact used at delivery time."""

    if not isinstance(payload, Mapping):
        return None
    version = _clean_spaces(payload.get("version"))
    if version != CUSTOMER_INVESTMENT_DOCUMENT_VERSION:
        return None
    raw_sections = payload.get("sections") or []
    raw_links = payload.get("links") or []
    if not isinstance(raw_sections, (list, tuple)):
        return None
    if not isinstance(raw_links, (list, tuple)):
        return None
    sections = []
    for item in raw_sections:
        if not isinstance(item, Mapping):
            continue
        raw_rows = item.get("rows") or []
        if isinstance(raw_rows, str):
            raw_rows = [raw_rows]
        elif not isinstance(raw_rows, (list, tuple)):
            continue
        sections.append(CustomerInvestmentSection(
            key=_clean_spaces(item.get("key")),
            title=_clean_spaces(item.get("title")),
            rows=tuple(
                _clean_spaces(row) for row in raw_rows if _clean_spaces(row)
            ),
        ))
    links = []
    for item in raw_links:
        if not isinstance(item, Mapping):
            continue
        links.append(CustomerInvestmentLink(
            label=_clean_spaces(item.get("label")),
            url=_clean_spaces(item.get("url")),
        ))
    return CustomerInvestmentDocument(
        role=_clean_spaces(payload.get("role")),
        headline=_clean_spaces(payload.get("headline")),
        target=_clean_spaces(payload.get("target")),
        role_label=_clean_spaces(
            payload.get("roleLabel") or payload.get("role_label")
        ),
        lead=_clean_spaces(payload.get("lead")),
        sections=tuple(sections),
        links=tuple(links),
        detail_url=_clean_spaces(
            payload.get("detailUrl") or payload.get("detail_url")
        ),
        reference_at=_clean_spaces(
            payload.get("referenceAt") or payload.get("reference_at")
        ),
        sent_at=_clean_spaces(payload.get("sentAt") or payload.get("sent_at")),
        notification_number=_clean_spaces(
            payload.get("notificationNumber") or payload.get("notification_number")
        ),
        language_level=_clean_spaces(
            payload.get("languageLevel") or payload.get("language_level") or "beginner"
        ),
        version=version,
    )


_BEGINNER_PHRASE_REPLACEMENTS = (
    ("가격·거래량과 매수/매도 압력", "가격·거래 흐름"),
    ("거래량과 매수/매도 압력", "거래량과 매수·매도 흐름"),
    ("매수/매도 압력", "매수·매도 흐름"),
    ("가격·수급·실적·뉴스", "가격·거래 흐름·실적·뉴스"),
    ("가격·수급·뉴스", "가격·거래 흐름·뉴스"),
    ("가격·수급·사건", "가격·거래 흐름·사건"),
    ("가격·수급", "가격·거래 흐름"),
    ("가격 변화율", "주가 등락률"),
    ("매수 우위 수급", "매수 우위"),
    ("투자자 수급", "외국인·기관 매매 흐름"),
    ("TypeDB 추론", "규칙 기반 변화 감지"),
    ("TypeDB 관계 분석", "가격·수급·뉴스 연결 분석"),
    ("TypeDB", "분석 시스템"),
    ("TBox", "개념 기준"),
    ("ABox", "현재 데이터"),
    ("RuleBox", "판단 규칙"),
    ("InferenceBox", "분석 결과"),
    ("추론 세대", "데이터 갱신"),
    ("추론 결과", "분석 결과"),
    ("추론 계기", "확인한 변화"),
    ("관계 수명주기", "신호 변화 과정"),
    ("관계의 근거", "이 신호를 뒷받침하는 근거"),
    ("관계 근거", "연결해서 본 근거"),
    ("관계 상태", "신호 상태"),
    ("관계 변화", "달라진 신호"),
    ("관계 분석", "가격·수급·뉴스 연결 분석"),
    ("관계 신호", "연결해서 본 신호"),
    ("성립 기준", "확인 기준"),
    ("성립 조건", "확인된 조건"),
    ("성립값", "확인된 수치"),
    ("성립했습니다", "확인됐습니다"),
    ("성립됐습니다", "확인됐습니다"),
    ("성립하지 않습니다", "더는 확인되지 않습니다"),
    ("성립", "확인"),
    ("인과 경로", "원인과 결과의 연결"),
    ("인과관계", "원인과 결과의 연결"),
    ("가설 비교", "가능한 설명 비교"),
    ("가설", "가능한 설명"),
    ("관측값", "현재 수치"),
    ("관측", "확인"),
    ("임계값", "확인 기준"),
    ("재판단", "다시 판단"),
    ("무효화", "판단 취소"),
    ("펀더멘털", "실적과 재무 상태"),
    ("밸류에이션", "현재 가격 수준"),
    ("외국인 수급", "외국인 매매 흐름"),
    ("기관 수급", "기관 매매 흐름"),
    ("수급", "매수·매도 흐름"),
    ("추세", "가격 흐름"),
    ("상대가치", "비교 종목 대비 가격 수준"),
    ("가격 경로 위험", "주가가 다시 약해질 위험"),
    ("실행 적격 판단", "주문 가능 판단"),
    ("실행 적격", "주문 가능 조건"),
    ("허용 행동 범위", "현재 선택 가능한 행동"),
    ("검증 신호", "확인된 신호"),
    ("검증 수준", "확인된 근거 수준"),
    ("시나리오", "가능성"),
    ("피어", "비교할 만한 회사"),
    ("가치 함정 검증 신호", "싸 보이지만 실적 약화로 더 하락할 위험 신호"),
    ("가치 함정", "싸 보이지만 실적 약화로 더 하락할 위험"),
    ("실행 자격", "주문에 필요한 조건"),
    ("분할축소", "일부 매도"),
    ("신규 진입", "처음 매수"),
    ("희석 위험", "신주 발행으로 기존 주식 가치가 낮아질 위험"),
    ("호가 불균형", "매수·매도 대기 물량 차이"),
    ("거시 레짐", "금리·환율 환경"),
    ("벤치마크", "비교 지수"),
    ("리밸런싱", "투자 비중 조정"),
    ("포지션", "보유 종목"),
    ("노출", "투자 비중"),
    ("핵심 촉매", "주가에 영향을 줄 핵심 사건"),
    ("촉매", "주가에 영향을 줄 사건"),
    ("하방", "내릴 가능성"),
    ("상방", "오를 가능성"),
)

_INTERMEDIATE_PHRASE_REPLACEMENTS = (
    ("가격·수급·실적·뉴스", "가격·거래 흐름·실적·뉴스"),
    ("가격·수급·뉴스", "가격·거래 흐름·뉴스"),
    ("가격·수급·사건", "가격·거래 흐름·사건"),
    ("가격·수급", "가격·거래 흐름"),
    ("가격 변화율", "주가 등락률"),
    ("외국인 수급", "외국인 매매 흐름"),
    ("기관 수급", "기관 매매 흐름"),
    ("수급", "매수·매도 흐름"),
    ("추세", "가격 흐름"),
    ("밸류에이션", "현재 가격 수준"),
    ("펀더멘털", "실적과 재무 상태"),
    ("추론 세대", "데이터 갱신"),
    ("TypeDB 추론", "규칙 기반 변화 감지"),
    ("TypeDB", "분석 시스템"),
    ("TBox", "개념 기준"),
    ("ABox", "현재 데이터"),
    ("RuleBox", "판단 규칙"),
    ("InferenceBox", "분석 결과"),
    ("관계 수명주기", "관계 변화 과정"),
    ("관계 신호", "함께 본 신호"),
    ("성립값", "확인된 수치"),
    ("인과 경로", "원인과 결과의 연결"),
    ("상방 관점", "오를 가능성이 더 큰 해석"),
    ("하방 관점", "내릴 가능성이 더 큰 해석"),
    ("상방", "오를 가능성"),
    ("하방", "내릴 가능성"),
    ("관측", "확인"),
    ("무효화", "판단 취소"),
    ("재판단", "다시 판단"),
    ("동행 여부", "함께 움직이는지"),
)

_INTERNAL_TOKEN_PATTERN = re.compile(
    r"\b(?:ABox|TBox|RuleBox|InferenceBox|materialization|prompt-admission)\b",
    re.IGNORECASE,
)

_INTERNAL_PROSE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:qualification|actionReturnAvailable|reasoningLineage|"
    r"judgementEligible|entryAllocationRoom|entrySupportCount|"
    r"entryExternalRiskBlocked)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

_CUSTOMER_CODE_REPLACEMENTS = (
    (re.compile(r"(?<![A-Za-z0-9_])NO_ACTION(?![A-Za-z0-9_])", re.IGNORECASE), "주문하지 않음"),
    (re.compile(r"(?<![A-Za-z0-9_])HOLD(?![A-Za-z0-9_])", re.IGNORECASE), "현재 상태 유지"),
    (re.compile(r"(?<![A-Za-z0-9_])BUY(?![A-Za-z0-9_])", re.IGNORECASE), "매수 검토"),
    (re.compile(r"(?<![A-Za-z0-9_])ADD(?![A-Za-z0-9_])", re.IGNORECASE), "추가매수 검토"),
    (re.compile(r"(?<![A-Za-z0-9_])TRIM(?![A-Za-z0-9_])", re.IGNORECASE), "일부 매도 검토"),
    (re.compile(r"(?<![A-Za-z0-9_])SELL(?![A-Za-z0-9_])", re.IGNORECASE), "매도 검토"),
    (re.compile(r"(?<![A-Za-z0-9_])AVOID(?![A-Za-z0-9_])", re.IGNORECASE), "매수하지 않음"),
    (re.compile(r"(?<![A-Za-z0-9_])weakened(?![A-Za-z0-9_])", re.IGNORECASE), "근거가 약해진"),
    (re.compile(r"(?<![A-Za-z0-9_])supported(?![A-Za-z0-9_])", re.IGNORECASE), "근거가 확인된"),
    (re.compile(r"(?<![A-Za-z0-9_])unresolved(?![A-Za-z0-9_])", re.IGNORECASE), "아직 결론 나지 않은"),
    (re.compile(r"(?<![A-Za-z0-9_])strong(?![A-Za-z0-9_])", re.IGNORECASE), "강한"),
    (re.compile(r"(?<![A-Za-z0-9_])ready(?![A-Za-z0-9_])", re.IGNORECASE), "주문 조건 충족"),
)

_FOLLOW_UP_FIELD_LABELS = {
    "currentPrice": "현재가",
    "priceChangeRate": "주가 등락률",
    "priceChangePct": "주가 등락률",
    "ma5Distance": "5일 평균 가격과의 차이",
    "ma20Distance": "20일 평균 가격과의 차이",
    "ma60Distance": "60일 평균 가격과의 차이",
    "volumeRatio": "최근 평균 대비 거래량",
    "timeAdjustedVolumeRatio": "장 진행 시간을 반영한 거래량",
    "tradeStrength": "체결강도",
    "foreignNetVolume": "외국인 순매수",
    "institutionNetVolume": "기관 순매수",
    "usdKrw": "원·달러 환율",
    "usdKrwRate": "원·달러 환율",
    "us10yYield": "미국 10년 금리",
    "krBaseRate": "한국 기준금리",
}

_COMPARISON_SUFFIXES = {
    ">": "초과하면",
    ">=": "이상이면",
    "<": "미만이면",
    "<=": "이하이면",
    "==": "일 때",
    "!=": "이 아닐 때",
}

_CUSTOMER_GRAMMAR_REPAIRS = (
    (re.compile(r"실적과 재무 상태 반대 가능성가"), "실적과 재무 상태가 양호하다는 반대 근거가"),
    (re.compile(r"매출·현금흐름 개선 확인된 신호"), "매출·현금흐름 개선 신호"),
    (re.compile(r"강한 확인된 신호"), "강하게 확인된 신호"),
    (re.compile(r"가격 신호가 0% 아래로"), "주가 등락률이 0% 아래로"),
    (re.compile(r"0% 아래로 내려감되고"), "0% 아래로 내려가고"),
    (re.compile(r"0% 아래로 내려감되면"), "0% 아래로 내려가면"),
    (re.compile(r"0% 아래로 내려감된"), "0% 아래로 내려간"),
    (re.compile(r"일부 매도 여부으로 전환하되"), "일부 매도 여부를 다시 판단하되"),
    (re.compile(r"일부 매도 여부으로"), "일부 매도 여부를 기준으로"),
    (re.compile(r"두 조건이 모두 확인하면"), "두 조건이 모두 확인되면"),
    (re.compile(r"최근 기간 현재 수치 부족"), "최근 데이터 부족"),
    (re.compile(r"기간 히스토리 부족"), "과거 데이터 부족"),
    (re.compile(r"종목 고유 위험 신호"), "이 종목만의 위험 신호"),
    (re.compile(r"(\d+)일 이동평균"), r"\1일 평균 가격"),
)

_MALFORMED_CUSTOMER_KOREAN_PATTERN = re.compile(
    r"반대 가능성가|강한 확인된|개선 확인된 신호|"
    r"내려감(?:되고|되면|된)|여부으로|모두 확인하면|"
    r"현재 수치 부족|히스토리"
)


def _clean_spaces(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value: object):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: object, digits: int = 2) -> str:
    numeric = _number(value)
    if numeric is None:
        return _clean_spaces(value)
    return (("%." + str(digits) + "f") % numeric).rstrip("0").rstrip(".")


def _signed_decimal(value: object, digits: int = 2) -> str:
    numeric = _number(value)
    if numeric is None:
        return _clean_spaces(value)
    return ("+" if numeric > 0 else "") + _decimal(numeric, digits)


def _subject(value: object) -> str:
    text = _clean_spaces(value)
    for character in reversed(text):
        if "가" <= character <= "힣":
            return text + ("이" if (ord(character) - 0xAC00) % 28 else "가")
    return text


def _follow_up_value(field: str, value: object, currency: str) -> str:
    numeric = _number(value)
    if numeric is None:
        return _clean_spaces(value)
    if field == "currentPrice":
        return price_money(numeric, currency)
    if field in {
        "priceChangeRate", "priceChangePct", "ma5Distance", "ma20Distance",
        "ma60Distance", "us10yYield", "krBaseRate",
    }:
        return _signed_decimal(numeric) + "%"
    if field in {"volumeRatio", "timeAdjustedVolumeRatio"}:
        return _decimal(numeric) + "배"
    if field in {"foreignNetVolume", "institutionNetVolume"}:
        return compact_number(numeric) + "주"
    if field in {"usdKrw", "usdKrwRate"}:
        return format(round(numeric, 2), ",").rstrip("0").rstrip(".") + "원"
    return _decimal(numeric)


def _moving_average_condition(field: str, operator: str, threshold: object) -> str:
    days = {"ma5Distance": "5일", "ma20Distance": "20일", "ma60Distance": "60일"}[field]
    numeric = _number(threshold)
    if numeric == 0:
        if operator in {">", ">="}:
            return "현재가가 " + days + " 평균 가격 이상으로 올라가면"
        if operator in {"<", "<="}:
            return "현재가가 " + days + " 평균 가격 이하로 내려가면"
    suffix = _COMPARISON_SUFFIXES.get(operator, operator)
    return (
        "현재가와 " + days + " 평균 가격의 차이가 "
        + _follow_up_value(field, threshold, "KRW") + " " + suffix
    )


def _moving_average_current(field: str, value: object) -> str:
    days = {"ma5Distance": "5일", "ma20Distance": "20일", "ma60Distance": "60일"}[field]
    numeric = _number(value)
    if numeric is None:
        return ""
    if numeric > 0:
        return "현재 " + days + " 평균보다 " + _decimal(abs(numeric)) + "% 위"
    if numeric < 0:
        return "현재 " + days + " 평균보다 " + _decimal(abs(numeric)) + "% 아래"
    return "현재 " + days + " 평균과 같은 수준"


def customer_follow_up_condition_clause(
    field: object,
    operator: object,
    threshold: object,
    *,
    current: object = None,
    currency: str = "KRW",
    label: object = "",
) -> str:
    """Describe one machine-observable condition without schema vocabulary."""

    clean_field = str(field or "").strip()
    clean_operator = str(operator or "").strip()
    if not clean_field or clean_operator not in _COMPARISON_SUFFIXES:
        return ""
    if clean_field in {"ma5Distance", "ma20Distance", "ma60Distance"}:
        condition = _moving_average_condition(clean_field, clean_operator, threshold)
        current_text = _moving_average_current(clean_field, current)
    else:
        field_label = _FOLLOW_UP_FIELD_LABELS.get(clean_field) or _clean_spaces(label) or "확인 지표"
        threshold_text = _follow_up_value(clean_field, threshold, currency)
        suffix = _COMPARISON_SUFFIXES[clean_operator]
        condition = _subject(field_label) + " " + threshold_text + " " + suffix
        current_text = (
            "현재 " + _follow_up_value(clean_field, current, currency)
            if current not in (None, "") else ""
        )
    return condition + ((" (" + current_text + ")") if current_text else "")


def _remove_internal_prose_sentences(value: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", str(value or ""))
    clean = [
        sentence for sentence in sentences
        if sentence and not _INTERNAL_PROSE_PATTERN.search(sentence)
    ]
    return " ".join(clean).strip()


def customer_investment_text(value: object, level: str = "beginner") -> str:
    """Project technical prose into the vocabulary allowed for one user level."""

    normalized_level = str(level or "beginner").strip()
    text = user_facing_investment_language(value, level=normalized_level)
    replacements = (
        _BEGINNER_PHRASE_REPLACEMENTS
        if normalized_level in {"absoluteBeginner", "beginner"}
        else _INTERMEDIATE_PHRASE_REPLACEMENTS
    )
    for before, after in replacements:
        text = text.replace(before, after)
    for pattern, replacement in _CUSTOMER_CODE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = _remove_internal_prose_sentences(text)
    if normalized_level == "absoluteBeginner":
        text = absolute_beginner_friendly_text(text)
    elif normalized_level == "beginner":
        text = beginner_friendly_text(text)
    # Level formatters can introduce their own plain-language aliases. Apply
    # the boundary vocabulary once more so those aliases are normalized too.
    for before, after in replacements:
        text = text.replace(before, after)
    if normalized_level in {"absoluteBeginner", "beginner"}:
        text = re.sub(r"관계가", "연결해서 본 신호가", text)
        text = re.sub(r"관계를", "연결해서 본 신호를", text)
        text = re.sub(r"관계는", "연결해서 본 신호는", text)
        text = re.sub(r"관계와", "연결해서 본 신호와", text)
        text = re.sub(r"관계의", "연결해서 본 신호의", text)
        text = _INTERNAL_TOKEN_PATTERN.sub("분석 시스템", text)
    text = customer_safe_text(text)
    for before, after in replacements:
        text = text.replace(before, after)
    text = _INTERNAL_TOKEN_PATTERN.sub("분석 시스템", text)
    text = re.sub(r"^\[(?:AI|시스템 판단|시스템 요약|관계 검토)\]\s*", "", text)
    text = text.replace("새 현재 수치은", "새로 확인된 수치는")
    text = text.replace("변화 연결해서 본 신호가", "변화가")
    text = text.replace(
        "이 변화로 연결해서 본 신호를 다시 계산했습니다.",
        "이 변화가 투자 판단에 미치는 영향을 다시 확인했습니다.",
    )
    text = text.replace(
        "연결해서 본 신호를 다시 계산했습니다.",
        "투자 판단에 미치는 영향을 다시 확인했습니다.",
    )
    text = re.sub(
        r"(가격|거래|수급|추세) 투자 판단에 미치는 영향을",
        r"\1 변화가 투자 판단에 미치는 영향을",
        text,
    )
    text = text.replace("전제가 판단 취소되므로", "전제가 깨지므로")
    text = text.replace("재검토", "다시 판단")
    text = text.replace("시스템이 새 가격", "시스템은 새 가격")
    text = text.replace("의 함께 움직이는지", "가 함께 움직이는지")
    text = text.replace("움직이는지를 확인", "움직이는지 확인")
    text = text.replace("분석 시스템 분석", "시스템 분석")
    text = text.replace("가능한 설명 설명", "가능한 설명")
    text = text.replace("연결해서 본 신호 신호", "연결해서 본 신호")
    text = text.replace("진입 지지 연결해서 본 신호", "진입을 뒷받침하는 신호")
    text = text.replace("가격 회복 연결해서 본 신호", "가격 회복 신호")
    text = text.replace("근거가 근거가", "근거가")
    text = text.replace("관심종목은 보유로 관찰", "관심종목으로 계속 지켜봄")
    text = text.replace("관심종목은 보유하되", "관심종목으로 계속 지켜보되")
    text = text.replace("보유로 관심종목 관찰을 유지", "관심종목으로 계속 지켜보고")
    text = text.replace("관심종목으로 유지하는 보유를 실행하고", "관심종목으로 계속 지켜보고")
    text = text.replace("관심종목으로 계속 지켜봄하되", "관심종목으로 계속 지켜보되")
    text = text.replace("관심종목으로 계속 지켜보고하고", "관심종목으로 계속 지켜보고")
    text = text.replace("분할축소과", "일부 매도와")
    text = text.replace("일부 매도과", "일부 매도와")
    text = text.replace("부정적 가능한 설명", "부정적 해석")
    text = text.replace("반등 실패 가능한 설명", "반등 실패 해석")
    text = text.replace("반등 실패 위험 가능한 설명", "반등 실패와 추가 하락 위험 해석")
    text = text.replace("지배 가능한 설명", "핵심 해석")
    text = text.replace("긍정 가능한 설명", "긍정적 해석")
    text = text.replace("가능한 설명을 판단 취소", "해석을 취소")
    text = text.replace("지배 관점", "핵심 해석")
    text = text.replace("재평가 관점", "긍정적인 해석")
    text = text.replace("크립토", "가상자산")
    text = re.sub(r"(?<![A-Za-z0-9])7d(?![A-Za-z0-9])", "7일", text, flags=re.IGNORECASE)
    text = text.replace("재확인이 확인됐습니다", "다시 확인됐습니다")
    text = text.replace("재확인이 확인되었습니다", "다시 확인되었습니다")
    text = text.replace("다음 검증 확인", "다음 확인")
    text = text.replace("후속 검증", "후속 확인")
    text = text.replace("판단을 판단 취소", "판단을 취소")
    text = text.replace(
        "강한 실적과 재무 상태 반대 신호",
        "실적과 재무 상태가 양호하다는 강한 반대 근거",
    )
    text = text.replace(
        "실적과 재무 상태 반대 신호",
        "실적과 재무 상태가 양호하다는 반대 근거",
    )
    text = text.replace(
        "최신의 강한 비교 종목 대비 가격 수준 기회 신호",
        "최근 강하게 확인된 상대적으로 싼 가격 신호",
    )
    text = text.replace(
        "비교 종목 대비 가격 수준 기회 신호",
        "비교 종목보다 상대적으로 싼 가격 신호",
    )
    text = text.replace(
        "조건부 비교 종목 대비 가격 수준 신호이므로",
        "비교 종목보다 가격이 싸다는 신호만으로는 충분하지 않아",
    )
    text = text.replace(
        "현재 확인은 기존 보유를 유지할 근거이지만 확인된 근거 수준이 제한적이므로 행동을 추가 매수로 확대하지 않습니다.",
        "가격 회복은 보유 유지에 유리하지만 추가매수까지 뒷받침할 근거는 충분하지 않아 현재 수량을 유지합니다.",
    )
    text = text.replace("검증된 강한", "강하게 확인된")
    text = text.replace(
        "강하게 확인된 싸 보이지만 실적 약화로 더 하락할 위험 확인된 신호",
        "실적 약화로 더 하락할 위험 신호",
    )
    text = text.replace(
        "검증된 싸 보이지만 실적 약화로 더 하락할 위험 신호",
        "실적 약화로 더 하락할 위험 신호",
    )
    text = text.replace(
        "강하게 확인된 싸 보이지만 실적 약화로 더 하락할 위험 검증 신호",
        "실적 약화로 추가 하락할 위험이 강하게 확인됐고",
    )
    text = text.replace(
        "싸 보이지만 실적 약화로 더 하락할 위험 검증 신호",
        "실적 약화로 추가 하락할 위험 신호",
    )
    text = text.replace("매수 우위 매수·매도 흐름", "매수 우위")
    text = text.replace("매수 우위이", "매수 우위가")
    text = text.replace("가격·매수·매도 흐름", "가격·거래 흐름")
    text = text.replace("가격 흐름가", "가격 흐름이")
    text = text.replace("주가 등락률이 음수", "주가 등락률이 0% 아래")
    text = text.replace("재무 신호", "실적과 재무 상태")
    text = text.replace(
        "보유 지속 여부를 낮춰 평가합니다.",
        "보유 유지가 맞는지 더 보수적으로 다시 봅니다.",
    )
    text = text.replace("일부 매도 비교 판단", "일부 매도 여부")
    text = text.replace(
        "확인한 뒤 행동 변경을 검토하셔야 합니다.",
        "확인되면 시스템이 행동 변경 여부를 다시 판단합니다.",
    )
    text = text.replace(
        "주식수와 현금흐름 방향을 확인되면",
        "주식수와 현금흐름 방향이 확인되면",
    )
    text = text.replace(
        "현재 보유 투자 비중을 늘리거나 줄이지 않고 보유합니다.",
        "현재 보유 수량을 유지합니다.",
    )
    text = text.replace(
        "소액 분할매수를 검토합니다. 진입을 뒷받침하는 근거가 확인돼 소액 진입을 검토할 수 있습니다.",
        "소액 분할매수를 검토합니다.",
    )
    text = text.replace(
        "소액 분할매수를 검토합니다. 소액 진입 조건 확인",
        "소액 분할매수를 검토합니다.",
    )
    text = text.replace(
        "지금은 매수하지 않고 관심종목으로 유지합니다. 지금은 주문하지 않습니다.",
        "지금은 매수하지 않고 관심종목으로 유지합니다.",
    )
    text = text.replace(
        "지금은 매수하지 않고 관심종목으로 유지합니다. 소액 진입 조건 확인",
        "지금은 매수하지 않고 관심종목으로 유지합니다. 매수 조건이 더 확인되면 다시 판단합니다.",
    )
    text = text.replace(
        "실적 약화로 추가 하락할 위험이 강하게 확인됐고가 함께 확인돼",
        "실적 약화로 추가 하락할 위험도 확인돼",
    )
    text = text.replace("주문 없는 주문하지 않음", "주문하지 않는 상태")
    text = text.replace("주문하지 않음입니다", "주문하지 않습니다")
    text = text.replace("주문 조건 충족와", "주문 조건 충족과")
    text = text.replace(
        "주문 조건 충족과 주문에 필요한 조건 확인 전에는",
        "주문에 필요한 조건이 확인되기 전에는",
    )
    text = text.replace("일부 매도와 매도도", "일부 매도나 전체 매도도")
    text = text.replace("차단된 추가매수", "현재 허용되지 않은 추가매수")
    text = text.replace("처음 매수 회피", "매수 보류")
    text = text.replace("긍정적 긍정적인", "긍정적인")
    text = text.replace("대기 물량 차이을", "대기 물량 차이를")
    text = text.replace("현재가격", "현재가")
    text = text.replace("전제를 판단 취소하고", "근거가 깨져")
    text = text.replace("전제가 판단 취소된", "근거가 깨진")
    text = text.replace("가격 약세의 보강 효과를 제거하고", "가격 약세 근거가 약해지고")
    text = text.replace("확신도", "근거 강도")
    text = text.replace("강한 검증 신호", "강하게 확인된 신호")
    text = text.replace(
        "위험 방향은 부정적이지만 연결해서 본 근거가 약해진 상태이므로 현재 행동은 주문하지 않는 상태입니다.",
        "하락 위험 신호는 남아 있지만 근거가 이전보다 약해 지금은 보유 수량을 바꾸지 않습니다.",
    )
    text = text.replace("현재가이", "현재가가")
    text = re.sub(
        r"(\d+)일 평균 차이",
        r"현재가와 \1일 평균 가격의 차이",
        text,
    )
    text = re.sub(r"(\d+)일선 가격", r"\1일 평균 가격", text)
    text = re.sub(r"(\d+)일선 위", r"\1일 평균 가격 위", text)
    text = re.sub(r"(\d+)일선 아래", r"\1일 평균 가격 아래", text)
    text = re.sub(r"(\d+)일선 차이", r"\1일 평균 가격과의 차이", text)
    text = re.sub(r"(\d+)일선 기울기", r"\1일 평균 가격의 방향", text)
    text = re.sub(r"(\d+)일선", r"\1일 평균 가격", text)
    text = text.replace("양수를 유지", "0% 위를 유지")
    text = text.replace("음수로 전환", "0% 아래로 내려감")
    text = text.replace("계속 음수이면", "계속 0% 아래에 머물면")
    text = text.replace("음수이면", "0% 아래에 있으면")
    text = text.replace("회복 쪽: 가격 회복 쪽에서는", "가격 흐름을 보면")
    text = re.sub(
        r"(\d+일 이동평균)\s+([0-9]+(?:\.0+)?)원",
        lambda match: match.group(1) + " " + format(int(float(match.group(2))), ",") + "원",
        text,
    )
    text = re.sub(
        r"현재\s+([-+]?\d+(?:\.\d+)?)인\s+(\d+일선 차이)",
        lambda match: "현재 " + match.group(2) + " " + ("+" if float(match.group(1)) > 0 else "") + match.group(1) + "%",
        text,
    )
    text = text.replace(
        "현재 가능한 설명만으로 회피나 차단된 매매 행동으로 전환하는 것도 보류합니다.",
        "새 근거가 확인될 때까지 관심 상태를 유지합니다.",
    )
    text = text.replace(
        "가상자산 7일 상승 변동 재확인",
        "7일 가격 상승 기준이 다시 충족됐습니다.",
    )
    text = text.replace(
        "관심종목으로 유지합니다. 조건 충족 시 소액 분할 진입 검토",
        "관심종목으로 계속 지켜봅니다. 매수 조건이 충족되면 소액 진입을 다시 검토합니다.",
    )
    if normalized_level in {"absoluteBeginner", "beginner"}:
        text = text.replace("후행 PER", "지난 실적 기준 주가 수준(PER)")
        text = text.replace("선행 PER", "예상 실적 기준 주가 수준(PER)")
        text = re.sub(r"(?<![A-Za-z])PBR(?![A-Za-z])", "순자산 대비 주가(PBR)", text)
        text = re.sub(r"(?<![A-Za-z])ROE(?![A-Za-z])", "자기자본이익률(ROE)", text)
    for pattern, replacement in _CUSTOMER_GRAMMAR_REPAIRS:
        text = pattern.sub(replacement, text)
    return _clean_spaces(text)


def _unique_rows(values: Iterable[object], level: str, limit: int = 8) -> Tuple[str, ...]:
    rows: List[str] = []
    keys: List[str] = []
    for value in values or []:
        text = customer_investment_text(value, level)
        key = re.sub(r"[^0-9a-z가-힣]+", "", text.casefold())
        if not text or not key:
            continue
        if any(
            key == prior
            or (len(key) >= 24 and (key in prior or prior in key))
            for prior in keys
        ):
            continue
        rows.append(text)
        keys.append(key)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return tuple(rows)


def normalized_customer_investment_document(
    document: CustomerInvestmentDocument,
) -> CustomerInvestmentDocument:
    level = document.language_level or "beginner"
    sections = []
    for section in document.sections:
        rows = _unique_rows(section.rows, level)
        title = customer_investment_text(section.title, level)
        if title and rows:
            sections.append(replace(section, title=title, rows=rows))
    links = tuple(
        replace(link, label=customer_investment_text(link.label, level))
        for link in document.links
        if customer_investment_text(link.label, level)
        and str(link.url or "").strip().lower().startswith(("https://", "http://"))
    )
    return replace(
        document,
        headline=customer_investment_text(document.headline, level),
        target=_clean_spaces(document.target),
        role_label=customer_investment_text(document.role_label, level),
        lead=customer_investment_text(document.lead, level),
        sections=tuple(sections),
        links=links,
    )


def customer_investment_document_quality(
    document: CustomerInvestmentDocument,
) -> Dict[str, object]:
    """Validate the customer boundary without altering the audit vocabulary."""

    issues: List[str] = []
    if document.role not in {"typedb-observation", "ai-judgement", "system-judgement"}:
        issues.append("unsupported-role")
    if not _clean_spaces(document.headline):
        issues.append("missing-headline")
    if not _clean_spaces(document.lead):
        issues.append("missing-lead")
    section_keys = {section.key for section in document.sections if section.rows}
    if document.role in {"ai-judgement", "system-judgement"} and "action" not in section_keys:
        issues.append("missing-ai-action")
    if "next-update" not in section_keys:
        issues.append("missing-next-update")
    values = [document.headline, document.role_label, document.lead]
    values.extend(section.title for section in document.sections)
    values.extend(row for section in document.sections for row in section.rows)
    values.extend(link.label for link in document.links)
    combined = "\n".join(_clean_spaces(value) for value in values if _clean_spaces(value))
    for issue in customer_text_quality_issues(combined):
        issues.append("customer-text:" + issue)
    forbidden = {
        "internal-type-db": r"(?<![A-Za-z0-9])TypeDB(?![A-Za-z0-9])",
        "internal-box": r"(?<![A-Za-z0-9])(?:TBox|ABox|RuleBox|InferenceBox)(?![A-Za-z0-9])",
        "internal-reasoning": r"추론 세대|가설 관계|인과 경로|관계 수명주기|성립값",
        "internal-action-code": r"(?<![A-Za-z0-9_])(?:NO_ACTION|BUY|ADD|HOLD|TRIM|SELL|AVOID)(?![A-Za-z0-9_])",
        "internal-contract": r"qualification|reasoningLineage|judgementEligible|materialization|prompt-admission",
    }
    for issue, pattern in forbidden.items():
        flags = 0 if issue == "internal-action-code" else re.IGNORECASE
        if re.search(pattern, combined, flags=flags):
            issues.append(issue)
    if _MALFORMED_CUSTOMER_KOREAN_PATTERN.search(combined):
        issues.append("malformed-customer-korean")
    unique_issues = sorted(set(issues))
    return {
        "version": CUSTOMER_INVESTMENT_DOCUMENT_QUALITY_VERSION,
        "status": "passed" if not unique_issues else "failed",
        "issues": unique_issues,
        "role": document.role,
        "sectionCount": len(document.sections),
    }
