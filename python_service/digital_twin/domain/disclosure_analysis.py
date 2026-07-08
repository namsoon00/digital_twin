import html
import re
from dataclasses import asdict, dataclass
from typing import Dict, List


DISCLOSURE_ANALYSIS_PROMPT_VERSION = "dart-disclosure-analysis-v1"
SECTION_LABELS = ["의미", "영향", "확인", "대응"]


@dataclass
class DisclosureAnalysisResult:
    lines: List[str]
    source: str = "로컬 규칙"
    raw_output: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def context_raw_lines(context: Dict[str, object]) -> List[str]:
    raw = (context or {}).get("rawLines")
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def data_value(context: Dict[str, object], label: str) -> str:
    for line in context_raw_lines(context):
        text = str(line or "").strip()
        for prefix in [label + ": ", label + " "]:
            if text.startswith(prefix):
                return text[len(prefix):].strip()
    return ""


def metadata_value(context: Dict[str, object], key: str) -> str:
    metadata = (context or {}).get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get(key)
        if str(value or "").strip():
            return str(value or "").strip()
    value = (context or {}).get(key)
    return str(value or "").strip()


def disclosure_report_name(context: Dict[str, object]) -> str:
    report = metadata_value(context, "reportName")
    if report:
        return report
    for line in context_raw_lines(context):
        text = str(line or "").strip()
        if not text or text.startswith(("신규 공시", "접수일", "최근 공시", "출처", "기준일")):
            continue
        return text
    return ""


def disclosure_summary(context: Dict[str, object]) -> Dict[str, str]:
    return {
        "corpName": metadata_value(context, "corpName") or str((context or {}).get("title") or "").strip(),
        "symbol": str((context or {}).get("symbol") or "").strip(),
        "reportName": disclosure_report_name(context),
        "receiptNo": metadata_value(context, "receiptNo"),
        "receiptDate": metadata_value(context, "receiptDate") or data_value(context, "접수일"),
        "provider": metadata_value(context, "provider") or data_value(context, "출처") or "OpenDART",
    }


def build_disclosure_analysis_prompt(context: Dict[str, object]) -> str:
    disclosure = disclosure_summary(context)
    raw_lines = "\n".join("- " + line for line in context_raw_lines(context)) or "- 없음"
    return "\n".join([
        "너는 한국 주식 공시를 해석하는 금융 데이터 애널리스트다.",
        "매수 또는 매도 지시가 아니라 공시의 의미, 가능한 영향, 확인할 점, 대응 가이드를 설명한다.",
        "제공된 공시 정보만 사용하고, 원문 확인이 필요한 내용은 확인 필요라고 말한다.",
        "한국어로 텔레그램 알림에 바로 넣을 수 있게 짧게 작성한다.",
        "반드시 다음 4줄 형식만 사용한다: 의미: ..., 영향: ..., 확인: ..., 대응: ...",
        "각 줄은 90자 안팎으로 유지하고 과장된 확정 표현을 피한다.",
        "API 키, 계좌 정보, 개인 정보를 추정하거나 요청하지 마라.",
        "",
        "분석 버전: " + DISCLOSURE_ANALYSIS_PROMPT_VERSION,
        "회사: " + (disclosure["corpName"] or "-"),
        "종목코드: " + (disclosure["symbol"] or "-"),
        "보고서명: " + (disclosure["reportName"] or "-"),
        "접수번호: " + (disclosure["receiptNo"] or "-"),
        "접수일: " + (disclosure["receiptDate"] or "-"),
        "제공 API: " + (disclosure["provider"] or "-"),
        "알림 원문:",
        raw_lines,
    ])


def compact_line(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def clean_analysis_lines(output: str) -> List[str]:
    lines: List[str] = []
    for line in str(output or "").splitlines():
        cleaned = re.sub(r"^(```+|[-*•\d.)\s]+)", "", line.strip()).strip()
        cleaned = cleaned.strip("`").strip()
        if not cleaned or cleaned.lower() in {"json", "text"}:
            continue
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def normalize_labeled_line(label: str, line: str) -> str:
    text = re.sub(r"^\[?" + re.escape(label) + r"\]?\s*[:：-]?\s*", "", str(line or "").strip())
    text = compact_line(text)
    return label + ": " + (text or "확인 필요")


def normalize_disclosure_analysis_output(output: str, fallback: DisclosureAnalysisResult, source: str = "AI 분석") -> DisclosureAnalysisResult:
    candidates = clean_analysis_lines(output)
    selected: List[str] = []
    used_indexes = set()
    for label in SECTION_LABELS:
        for index, line in enumerate(candidates):
            if index in used_indexes:
                continue
            if re.match(r"^\[?" + re.escape(label) + r"\]?\s*[:：-]", line) or line.startswith(label + " "):
                selected.append(normalize_labeled_line(label, line))
                used_indexes.add(index)
                break
    if len(selected) < 3 and candidates:
        selected = [compact_line(line) for line in candidates[:4]]
    if len(selected) < 3:
        selected = list(fallback.lines)
        source = fallback.source
    return DisclosureAnalysisResult(selected[:4], source=source, raw_output=str(output or "").strip())


def local_disclosure_analysis(context: Dict[str, object], source: str = "로컬 규칙") -> DisclosureAnalysisResult:
    disclosure = disclosure_summary(context)
    report = disclosure["reportName"]
    normalized = report.replace("ㆍ", "·").replace(" ", "")
    meaning = "새 공시가 접수되어 회사 이벤트나 리스크 변화 여부를 확인해야 합니다."
    impact = "보고서 세부 내용에 따라 실적, 재무구조, 투자심리가 달라질 수 있습니다."
    check = "공시 원문에서 금액, 기간, 상대방, 발생 사유가 시가총액·매출 대비 큰지 확인하세요."
    action = "가격이 먼저 움직였으면 추격보다 원문 확인 후 비중 유지·축소·분할 대응을 결정하세요."

    if any(term in normalized for term in ["단일판매", "공급계약", "수주"]):
        meaning = "매출로 이어질 수 있는 계약 또는 수주성 공시입니다."
        impact = "계약 규모가 크면 실적 가시성과 투자심리에 긍정적일 수 있으나 마진은 별도 확인이 필요합니다."
        check = "계약금액의 최근 매출 대비 비중, 계약기간, 상대방, 해지 조건을 확인하세요."
        action = "급등 시에는 계약 규모와 수익성 확인 전 추격 비중을 제한하고 분할 대응하세요."
    elif any(term in normalized for term in ["유상증자", "전환사채", "신주인수권", "교환사채"]):
        meaning = "자금조달 또는 잠재 주식 수 증가와 관련된 공시입니다."
        impact = "운영자금 확보에는 도움이 될 수 있지만 주당가치 희석과 단기 수급 부담이 생길 수 있습니다."
        check = "발행 규모, 발행가, 사용 목적, 기존 주식 수 대비 희석률을 확인하세요."
        action = "희석률이 크거나 목적이 불명확하면 추가 매수보다 리스크 한도를 먼저 낮추세요."
    elif any(term in normalized for term in ["잠정실적", "영업실적", "매출액", "손익구조"]):
        meaning = "실적 추정 또는 확정치 변화와 관련된 공시입니다."
        impact = "컨센서스 대비 차이에 따라 주가 재평가나 실망 매물이 나올 수 있습니다."
        check = "매출, 영업이익, 순이익이 전년 대비와 시장 기대 대비 어떤지 확인하세요."
        action = "일회성 요인인지 반복 가능한 실적인지 나눠 보고 목표가와 손절 기준을 갱신하세요."
    elif any(term in normalized for term in ["소송", "횡령", "배임", "불성실", "거래정지"]):
        meaning = "법적·지배구조·거래 리스크와 관련된 공시입니다."
        impact = "불확실성이 커져 밸류에이션 할인, 신용 위험, 거래 제한 가능성이 생길 수 있습니다."
        check = "금액 규모, 자기자본 대비 비중, 거래소 조치, 재발 여부를 확인하세요."
        action = "손실 제한선을 우선 점검하고 원문 확인 전 신규 진입은 보수적으로 보세요."
    elif any(term in normalized for term in ["배당", "현금·현물배당"]):
        meaning = "주주환원 정책 또는 배당 규모와 관련된 공시입니다."
        impact = "배당수익률과 현금흐름 신뢰도가 높으면 투자심리에 긍정적일 수 있습니다."
        check = "배당금, 기준일, 배당성향, 일회성 배당 여부를 확인하세요."
        action = "배당락과 실적 지속성을 함께 보고 단기 배당 매수인지 장기 보유인지 구분하세요."
    elif any(term in normalized for term in ["자기주식", "자사주"]):
        if "처분" in normalized:
            meaning = "보유 자기주식을 처분하는 공시로 주주환원보다 수급 변화 확인이 우선입니다."
            impact = "시장 매각이면 단기 물량 부담이 될 수 있고 보상·전략 목적이면 영향이 제한될 수 있습니다."
            check = "처분 수량, 금액, 방법, 대상자, 목적, 기간, 총주식 대비 비중을 확인하세요."
            action = "손실 구간이면 공시 조건과 장중 수급·거래량 반응 확인 전 추가매수는 보류하세요."
        else:
            meaning = "자사주 취득·소각 등 주주환원이나 수급 변화 관련 공시입니다."
            impact = "취득·소각은 수급과 주주가치에 우호적일 수 있으나 실제 집행 여부가 중요합니다."
            check = "취득인지 소각인지, 규모, 기간, 목적, 실제 집행률을 확인하세요."
            action = "취득·소각이면 긍정 요인을 반영하되 가격 반응과 집행률을 추적하세요."
    elif any(term in normalized for term in ["합병", "분할", "영업양수", "영업양도"]):
        meaning = "회사 구조, 사업 포트폴리오, 지배구조가 바뀔 수 있는 공시입니다."
        impact = "사업 가치 재평가가 가능하지만 비율과 일정에 따라 주주가치 훼손 위험도 있습니다."
        check = "합병·분할 비율, 기준가, 주식매수청구권, 일정, 시너지 근거를 확인하세요."
        action = "이벤트 완료 전 변동성이 커질 수 있어 비중과 손절 기준을 먼저 정리하세요."
    elif "주요사항보고서" in normalized:
        meaning = "투자 판단에 중요한 회사 이벤트를 묶어 알리는 보고서입니다."
        impact = "세부 항목이 자금조달, 계약, 소송, 구조개편 중 무엇인지에 따라 영향이 크게 달라집니다."
        check = "보고서 원문에서 세부 제목, 금액, 자기자본·매출 대비 비중을 먼저 확인하세요."
        action = "원문 확인 전에는 방향을 단정하지 말고 기존 포지션의 리스크 한도부터 점검하세요."

    return DisclosureAnalysisResult([
        "의미: " + meaning,
        "영향: " + impact,
        "확인: " + check,
        "대응: " + action,
    ], source=source)


def split_labeled_text(line: str):
    text = str(line or "").strip()
    if ": " in text:
        label, value = text.split(": ", 1)
        if label.strip() and value.strip():
            return label.strip(), value.strip()
    return "", text


def disclosure_analysis_block(result: DisclosureAnalysisResult, rich: bool = False) -> str:
    if not result or not result.lines:
        return ""
    rows: List[str] = []
    for line in result.lines:
        label, value = split_labeled_text(line)
        if rich and label and value:
            rows.append("• <b>" + html.escape(label, quote=False) + "</b>: " + html.escape(value, quote=False))
        elif rich:
            rows.append("• " + html.escape(str(line or "").strip(), quote=False))
        else:
            rows.append("• " + str(line or "").strip())
    if result.source:
        if rich:
            rows.append("• <b>분석출처</b>: " + html.escape(result.source, quote=False))
        else:
            rows.append("• 분석출처: " + result.source)
    title = "<b>AI 공시 해석</b>" if rich else "AI 공시 해석"
    return title + "\n" + "\n".join(row for row in rows if row.strip())


def insert_analysis_block(message: str, block: str, rich: bool = False) -> str:
    text = str(message or "").strip()
    if not text or not block or "AI 공시 해석" in text:
        return text
    marker = "\n\n<b>발송 기준</b>" if rich else "\n\n발송 기준"
    if marker in text:
        return text.replace(marker, "\n\n" + block + marker, 1)
    return text + "\n\n" + block


def context_with_disclosure_analysis(context: Dict[str, object], result: DisclosureAnalysisResult) -> Dict[str, object]:
    values = dict(context or {})
    if values.get("disclosureAnalysis") or "AI 공시 해석" in str(values.get("telegramMessage") or ""):
        return values
    plain_block = disclosure_analysis_block(result, False)
    telegram_block = disclosure_analysis_block(result, True)
    values["disclosureAnalysis"] = plain_block
    values["telegramDisclosureAnalysis"] = telegram_block
    values["disclosureAnalysisSource"] = result.source
    values["disclosureAnalysisVersion"] = DISCLOSURE_ANALYSIS_PROMPT_VERSION
    original_telegram = str(values.get("telegramMessage") or "").strip()
    original_readable = str(values.get("readableMessage") or "").strip()
    values["telegramMessage"] = insert_analysis_block(original_telegram, telegram_block, True)
    values["readableMessage"] = insert_analysis_block(original_readable, plain_block, False)
    if not values.get("body") or str(values.get("body") or "").strip() == original_telegram:
        values["body"] = values["telegramMessage"]
    return values
