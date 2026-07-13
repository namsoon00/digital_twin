import html
import re
from typing import Dict, List


DATA_LABEL_PREFIXES = [
    "환율",
    "금리",
    "미장 가격 변동",
    "비트코인 변동",
    "크립토 변동",
    "크립토 가격",
    "크립토 거래액",
    "매수 판단",
    "매도 판단",
    "모델 매수 점수",
    "모델 매도 점수",
    "적정가 대비",
    "24h 거래액",
    "현재가",
    "평균매입가",
    "평단가",
    "수익률",
    "보유 수량",
    "매도가능 수량",
    "종목 평가금액",
    "계좌 평가금액",
    "기준일",
    "발송시각",
    "연속 실패",
    "실패 단계",
    "재시도",
    "투자자",
    "기울기",
    "확인 행동",
    "권장 액션",
    "거래량",
    "거래액",
    "가격",
    "수급",
    "추세",
    "출처",
    "이전",
    "현재",
    "변화",
    "상태",
    "손익",
    "평가",
    "보유",
    "신호",
]

DATA_LABEL_ORDER = {
    "상태": 10,
    "연속 실패": 11,
    "실패 단계": 12,
    "재시도": 13,
    "환율": 18,
    "금리": 19,
    "손익": 20,
    "미장 가격 변동": 20,
    "현재가": 21,
    "평균매입가": 22,
    "평단가": 22,
    "수익률": 23,
    "보유 수량": 24,
    "매도가능 수량": 25,
    "종목 평가금액": 26,
    "계좌 평가금액": 27,
    "매수 판단": 25,
    "매도 판단": 26,
    "수급": 30,
    "거래량": 31,
    "거래액": 32,
    "추세": 40,
    "확인 행동": 41,
    "권장 액션": 41,
    "기울기": 45,
    "투자자": 50,
    "신호": 60,
    "비트코인 변동": 70,
    "크립토 변동": 71,
    "크립토 가격": 72,
    "크립토 거래액": 73,
    "출처": 88,
    "기준일": 89,
    "발송시각": 90,
}

SEPARATE_DATA_LABELS = {
    "상태",
    "연속 실패",
    "실패 단계",
    "재시도",
    "환율",
    "금리",
    "손익",
    "미장 가격 변동",
    "현재가",
    "평균매입가",
    "평단가",
    "수익률",
    "보유 수량",
    "매도가능 수량",
    "종목 평가금액",
    "계좌 평가금액",
    "매수 판단",
    "매도 판단",
    "수급",
    "추세",
    "확인 행동",
    "권장 액션",
    "기울기",
    "투자자",
    "신호",
    "거래량",
    "거래액",
    "비트코인 변동",
    "크립토 변동",
    "크립토 가격",
    "크립토 거래액",
    "출처",
    "기준일",
    "발송시각",
    "평가",
    "보유",
}
FOOTER_DATA_LABELS = {
    "기준일",
    "기준시각",
    "발송시각",
}
ONTOLOGY_INTERNAL_DATA_PREFIXES = (
    "관계 신호",
    "성립 규칙",
    "AI 질문",
    "온톨로지:",
    "온톨로지 판단",
    "thesis:",
    "판단 근거:",
    "관계 충돌:",
    "주요 위험:",
    "부족 데이터 ",
)

BEGINNER_FRIENDLY_REPLACEMENTS = [
    ("opinion assertion", "의견 기록"),
    ("supporting-evidence", "보조 근거"),
    ("legacyModelRole", "기존 모델 역할"),
    ("온톨로지 판단", "관계 판단"),
    ("온톨로지 컨텍스트", "관계 분석 정보"),
    ("온톨로지 그래프", "관계 분석 데이터"),
    ("온톨로지", "관계 분석"),
    ("세계관 집중도", "관련 종목 비중"),
    ("세계관", "투자 관점"),
    ("손실 thesis 재검증", "손실 구간 보유 이유 재확인"),
    ("thesis 충돌", "보유 이유와 충돌"),
    ("thesis 훼손", "보유 이유 약화"),
    ("보유 thesis", "보유 이유"),
    ("종목 thesis", "종목 보유 이유"),
    ("기존 thesis", "기존 보유 이유"),
    ("thesis", "보유 이유"),
    ("evidence", "근거"),
    ("belief", "판단 근거"),
    ("assertion", "기록"),
    ("legacy score", "기존 점수"),
    ("legacy", "기존"),
    ("증거", "근거"),
    ("컨텍스트", "정보"),
    ("가설", "설명"),
]

ABSOLUTE_BEGINNER_FRIENDLY_REPLACEMENTS = [
    ("중기 회복", "최근보다 조금 긴 기간의 가격 회복"),
    ("중기 방어선", "최근보다 조금 긴 기간의 버티는 가격대"),
    ("60일선 방어", "60일 평균 가격 근처에서 버팀"),
    ("20일선 회복", "20일 평균 가격 위로 다시 올라감"),
    ("60일선 회복", "60일 평균 가격 위로 다시 올라감"),
    ("5일선 회복", "5일 평균 가격 위로 다시 올라감"),
    ("20일선 이탈", "20일 평균 가격 아래로 내려감"),
    ("60일선 이탈", "60일 평균 가격 아래로 내려감"),
    ("5일선 이탈", "5일 평균 가격 아래로 내려감"),
    ("재이탈", "다시 아래로 내려감"),
    ("기준선 이탈", "주요 평균선 아래로 내려감"),
    ("추세 훼손", "가격 흐름 약화"),
    ("추세 약화", "가격 흐름 약화"),
    ("하락 가속", "하락 속도 증가"),
    ("중기선", "60일 평균 가격"),
    ("지지선", "가격이 버티는 선"),
    ("방어선", "버티는 가격대"),
    ("눌림목", "잠깐 내려온 구간"),
    ("추격 매수", "오른 뒤 급하게 따라 사기"),
    ("모멘텀", "힘"),
    ("괴리", "차이"),
    ("무효화 조건", "의견이 약해지는 조건"),
]


def beginner_friendly_text(value: object) -> str:
    text = str(value or "")
    for before, after in BEGINNER_FRIENDLY_REPLACEMENTS:
        text = text.replace(before, after)
    return text


def absolute_beginner_friendly_text(value: object) -> str:
    text = str(value or "")
    for before, after in ABSOLUTE_BEGINNER_FRIENDLY_REPLACEMENTS:
        text = text.replace(before, after)
    return beginner_friendly_text(text)


def plain_bullet(text: str) -> str:
    cleaned = beginner_friendly_text(text).strip()
    return "• " + cleaned if cleaned else ""


def html_bullet(text: str) -> str:
    cleaned = beginner_friendly_text(text).strip()
    return "• " + html.escape(cleaned, quote=False) if cleaned else ""


def split_label_value(text: str):
    cleaned = beginner_friendly_text(text).strip()
    if ": " not in cleaned:
        return "", cleaned
    label, value = cleaned.split(": ", 1)
    if 0 < len(label.strip()) <= 18 and value.strip():
        return label.strip(), value.strip()
    return "", cleaned


def criterion_row(text: str, rich: bool = False) -> str:
    label, value = split_label_value(text)
    if label and value:
        if rich:
            return "• <b>" + html.escape(label, quote=False) + "</b>: <code>" + html.escape(value, quote=False) + "</code>"
        return "• " + label + ": " + value
    return html_bullet(text) if rich else plain_bullet(text)


def criterion_rows(items: List[str], rich: bool = False) -> str:
    return "\n".join(criterion_row(item, rich) for item in items if str(item or "").strip())


def split_data_line(line: str):
    text = beginner_friendly_text(line).strip()
    for label in DATA_LABEL_PREFIXES:
        colon_prefix = label + ": "
        if text.startswith(colon_prefix):
            value = text[len(colon_prefix):].strip()
            if value:
                return label, value
        prefix = label + " "
        if text.startswith(prefix):
            value = text[len(prefix):].strip()
            if value:
                return label, value
    return "", text


def ordered_data_entries(raw_lines: List[str]) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for index, line in enumerate(raw_lines):
        label, value = split_data_line(line)
        if label and value:
            entries.append({
                "kind": "pair",
                "label": label,
                "value": value,
                "index": index,
                "order": DATA_LABEL_ORDER.get(label, 100 + index),
            })
        else:
            entries.append({
                "kind": "text",
                "text": str(line or "").strip(),
                "index": index,
                "order": 100 + index,
            })
    return sorted(entries, key=lambda item: (int(item["order"]), int(item["index"])))


def is_ontology_internal_data_line(line: str) -> bool:
    text = str(line or "").strip()
    return any(text.startswith(prefix) for prefix in ONTOLOGY_INTERNAL_DATA_PREFIXES)

def data_pair_text(label: str, value: str, rich: bool = False) -> str:
    label = beginner_friendly_text(label)
    value = beginner_friendly_text(value)
    if rich:
        return "<b>" + html.escape(label, quote=False) + "</b>: <code>" + html.escape(value, quote=False) + "</code>"
    return label + ": " + value


def formatted_data_rows(raw_lines: List[str], rich: bool = False) -> str:
    rows: List[str] = []
    pairs: List[str] = []

    def flush_pairs():
        nonlocal pairs
        if pairs:
            rows.extend(grouped_data_rows(pairs))
            pairs = []

    for entry in ordered_data_entries(raw_lines):
        if entry.get("kind") == "pair":
            label = str(entry.get("label") or "")
            value = str(entry.get("value") or "")
            pair_text = data_pair_text(label, value, rich)
            if label in SEPARATE_DATA_LABELS:
                flush_pairs()
                rows.append("• " + pair_text)
            else:
                pairs.append(pair_text)
            continue
        flush_pairs()
        text = str(entry.get("text") or "")
        rows.append(html_bullet(text) if rich else plain_bullet(text))
    flush_pairs()
    return "\n".join(row for row in rows if row)


def plain_data_rows(raw_lines: List[str]) -> str:
    return formatted_data_rows(raw_lines, False)


def telegram_data_rows(raw_lines: List[str]) -> str:
    return formatted_data_rows(raw_lines, True)


def grouped_data_rows(items: List[str], per_row: int = 2) -> List[str]:
    rows: List[str] = []
    for index in range(0, len(items), per_row):
        rows.append("• " + ", ".join(items[index:index + per_row]))
    return rows


def data_value(raw_lines: List[str], label: str) -> str:
    for line in raw_lines:
        parsed_label, value = split_data_line(line)
        if parsed_label == label and value:
            return value
    return ""


def signed_direction(value: str) -> int:
    match = re.search(r"([+-])\s*\d", str(value or ""))
    if not match:
        return 0
    return 1 if match.group(1) == "+" else -1


def dominant_signed_direction(value: str) -> int:
    signed_values: List[float] = []
    for match in re.finditer(r"([+-])\s*(\d+(?:\.\d+)?)", str(value or "")):
        sign = -1 if match.group(1) == "-" else 1
        signed_values.append(sign * float(match.group(2)))
    if not signed_values:
        return 0
    dominant = max(signed_values, key=lambda item: abs(item))
    if dominant > 0:
        return 1
    if dominant < 0:
        return -1
    return 0


def title_from_change(value: str, positive: str, negative: str, neutral: str) -> str:
    direction = dominant_signed_direction(value)
    if direction > 0:
        return positive
    if direction < 0:
        return negative
    return neutral


def first_data_text(raw_lines: List[str], pattern: str) -> str:
    compiled = re.compile(pattern)
    for line in raw_lines:
        text = str(line or "").strip()
        if compiled.search(text):
            return text
    return ""


def text_parts_from_value(value: object) -> List[str]:
    if isinstance(value, dict):
        parts: List[str] = []
        for nested in value.values():
            parts.extend(text_parts_from_value(nested))
        return parts
    if isinstance(value, list):
        parts = []
        for nested in value:
            parts.extend(text_parts_from_value(nested))
        return parts
    text = str(value or "").strip()
    return [text] if text else []

def format_score_value(value: object) -> str:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number.is_integer():
        return str(int(number))
    return ("%.1f" % number).rstrip("0").rstrip(".")
