"""Current-use source-clock guidance, separate from historical ABox metadata."""


CLOCK_INSTRUCTIONS = """시세 시점 계약:
current.quoteAssessment는 원천 시세 시각을 캡처 시각에 다시 대조한 참고 정보입니다.
facts의 freshnessStatus·judgementEvidenceUsable·시장 세션은 원본 저장 당시 값이며 지금도 유효하다는 뜻이 아닙니다.
capturedAt·분석 시각·sourceFetchedAt이 새로워도 원천 sourceAsOf는 새로워지지 않습니다.
status=stale는 갱신 기준을 넘긴 과거 시점 자료입니다. fresh 이외의 시세를 실시간·현재 장중 상황으로 설명하지 마세요.
referenceState=last-close는 출처가 표시한 마감 참고값이며, 지금도 장이 닫혀 있다는 뜻이 아닙니다.
장 마감·연휴의 과거 자료도 기준 시점과 한계를 명시해 분석할 수 있습니다. 오래됐다는 이유만으로 모두 발송 보류하지 마세요.
본문은 당시 자료의 의미와 이후 확인할 내용을 구분하고, 현재 상황 확인이 필요하면 확인 한계에 적으세요.
프로그램이 원천 시각·경과 시간·갱신 기준을 따로 표시하므로 문장에 시간 숫자를 만들어 쓰지 마세요.
독립 검토는 이 시점 구분을 검사하며, 저장 당시 fresh 표시만으로 현재 시세라고 승인하지 마세요.
"""


def clocked_management_prompt(packet, history, research):
    from digital_twin.modules.ai_orchestration.domain.brain_management import management_prompt
    return CLOCK_INSTRUCTIONS + management_prompt(packet, history, research)


def clocked_review_prompt(packet, draft):
    from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
    return CLOCK_INSTRUCTIONS + review_prompt(packet, draft)
