"""Price-anchored explanation scope and room for discretionary evidence."""
from . import external_influence

VERSION = "price-context-explanation-v1"
DISCOVERY_RESERVE_BYTES = 8192
TRACE_RESERVE_BYTES = 16384


def enabled(packet):
    return packet.get("marketExplanationPolicy") == VERSION and external_influence.enabled(packet)


def trace_reserve(contract, trace):
    from digital_twin.modules.reasoning.contracts import source_packet_bytes
    # Baseline admission already reserved the whole trace. As the trace grows,
    # reserve only its remaining space instead of charging that space twice.
    used = source_packet_bytes(contract.trace_summary(trace, "round-limit"))
    return max(2048, TRACE_RESERVE_BYTES - used)


def review_errors(result, review):
    if not enabled(result["input"]):
        return []
    checked = review.get("marketExplanation", {}) if isinstance(review, dict) else {}
    if (not isinstance(checked, dict) or checked.get("supported") is not True
            or not isinstance(checked.get("reason"), str) or not 8 <= len(checked["reason"].strip()) <= 600):
        return ["가격 비교 기간·원문 공개 시점·필수와 보충 자료의 구분을 독립 검토에서 확인하지 못했습니다."]
    return []


READ_INSTRUCTIONS = """이번 관찰의 설명 범위를 먼저 정하세요:
주가가 움직인 경우 현재 facts의 시세 원천 시각·등락률 비교 기준과 마지막 성공 알림을 먼저 확인합니다. 일간 등락률을 최근 몇 분의 변동으로 바꾸지 마세요.
eventIndex는 동결 ABox의 원문 찾기 목록입니다. 수집 시각이 아니라 공개 시각 순서이며 제목은 증거가 아닙니다. events/context의 관련 factId를 read_fact로 직접 읽고, 무관한 과거 기업행동 페이지를 반복 순회하지 마세요. 목록 밖에도 자료가 남을 수 있습니다.
같은 시각·거래 구간·비교 기준의 지수/업종 자료가 있을 때만 시장 공통 움직임과 종목 고유 움직임을 비교하세요. 없으면 비교 미확인으로 남깁니다. publishedAfterQuote인 자료는 당시 시장이 알고 있던 원인으로 사용하지 마세요.
기업의 보고 실적, 현재 가격 상황, 이번 변동의 가능한 배경은 각각 독립적으로 설명할 수 있습니다. 외부 사건을 반드시 찾거나 모든 기업 노출을 채울 필요는 없습니다.
missingEvidence는 선택한 설명 자체를 불가능하게 하는 필수 부족분만 담습니다. 원인 단정·정량 영향·시장 비교를 하지 않기로 한 경우 그 보충 자료 부족은 optionalEvidence에 남기고, 확인된 설명을 위한 근거가 충분하면 finish/blocker=none입니다. optionalEvidence는 다음 작성에도 보존되며 미확인 부분을 확정할 권한이 아닙니다.
현재 상태 설명에 필요한 최신 시세가 없으면 current-state로 완료할 수 없습니다. 과거 공시 설명만 가능하면 source-dated로 범위를 명확히 하세요. 필수 자료가 없으면 기존처럼 defer하세요. 코드가 defer를 finish로 바꾸지 않습니다.
이미 읽은 근거와 핵심 반대 근거를 선택하고 마지막 호출은 finish/defer로 마칩니다. 입력 여유가 작으면 무관한 보고서를 선택에서 제외하고 필요한 지표만 읽습니다. 같은 조사나 유료 호출을 추가로 반복하지 마세요.
"""

INSTRUCTIONS = """가격 상황과 배경 설명:
summary는 무엇이 관측됐고 무엇을 설명할 수 있는지 먼저 말합니다. 가격 등락은 실제 원천 시각과 비교 기준을 유지합니다.
hypothesis에는 확인된 기업·시장 배경과 가능한 전달 경로를 쓰고, counterEvidence에는 다른 설명과 미확인 부분을 씁니다. 재무제표 악화가 이번 가격 하락의 원인임을 자동으로 뜻하지 않으며, 평균 가격 돌파는 경제적 원인 증명이 아닙니다.
같은 기간·기준의 시장/업종 자료가 없으면 시장 대비 강약을 계산하거나 주장하지 마세요. 가격 이후 공개된 뉴스, 과거 배당·발행, 제목만 있는 자료로 현재 변동을 설명하지 마세요. 본문·공개 시점·기업 관련성·반대 근거를 확인한 경우에도 가능한 설명과 확인된 사실을 구분하세요.
retrieval.steps의 optionalEvidence는 보충 자료 부족입니다. 확보된 범위의 설명은 작성하되 관련 한계를 실제 문장에 반영하고, 원인을 확인하지 못했다면 명확하게 밝혀야 합니다. 필수 근거 부족을 optional로 이름만 바꿔 결론을 만들면 안 됩니다.
추가 조사는 답에 따라 기업 판단이나 이번 설명이 달라지는 중요한 부족분에만 기존 questions 한도 안에서 제안하세요. 원인 미확인 자체나 시세 알림의 재서술은 새 알림의 근거가 아닙니다. 기존 독립 검토·새 의미·반복 제한을 모두 유지하세요.
"""
