"""Versioned, independently reviewed meanings for customer observations."""

OBSERVATION_WORDING_VERSION = "observation-wording-v1"

WRITING_INSTRUCTIONS = """독자가 바로 이해할 수 있는 관찰 문장 계약:
summary, comparison, hypothesis, portfolioImpact, counterEvidence, notification.reason 모두
그 문장만 읽어도 대상·변화·의미를 알 수 있게 쓰세요. 필요한 판단 대상을 짧게 반복하세요.
‘이 설명을 재검토’, ‘설명 약화’, ‘기존 관점’, ‘충돌이 줄었다’만으로 의미를 대신하지 마세요.
무엇이 상승/하락 중인지, 어떤 판단을 유지하기 어려워지는지 구체적으로 밝히세요.
예를 들어 ‘가격 위치와 단기 방향의 충돌이 줄었다’ 대신, 해당 근거가 있을 때
‘가격은 평균 가격 위에 있고 평균 가격도 상승하기 시작해, 단기 회복을 뒷받침하는 근거가 늘었습니다’처럼 쓰세요.
followUpConditions.description은 알림에서 ‘조건 → description’ 형태로 그대로 표시됩니다.
description에는 조건식을 반복하지 말고, 조건이 성립하면 어떤 해석의 근거가 강해지거나
약해지는지를 독립된 문장으로 쓰세요. ‘확인합니다/재검토합니다’라는 작업명으로 끝내지 마세요.
예: ‘단기 반등이 이어진다고 볼 근거가 약해집니다.’
예: ‘최근 회복이 이어진다는 판단을 유지하기 어려워집니다.’
예시는 표현 형식이며 해당 종목의 결론이 아닙니다. 현재 근거와 hypothesis에 맞는 의미를 작성하세요.
supports/weakens/invalidates는 내부 효과 분류입니다. 분류 이름을 번역하는 대신 판단 대상을 명시하세요.
한 번의 조건 성립을 ‘상승 종료’, ‘하락 전환 확정’으로 확대하거나 매매 지시로 바꾸지 마세요.
수치·조건 연산자·확인 기간은 구조화된 근거 표시가 담당합니다. 쉬운 표현을 위해 새 사실을 만들지 마세요.
"""

REVIEW_INSTRUCTIONS = """독자가 바로 이해할 수 있는 관찰 문장 검토 계약:
각 본문 문장에서 무엇이 달라졌고 어떤 해석이 달라지는지 직접 읽을 수 있는지 확인하세요.
‘이 설명을 재검토’, ‘설명 약화’, ‘충돌 감소’만 제시해 다른 문단에서 대상을 찾아야 한다면
해당 sections 항목의 supported=false로 반환하고, 빠진 대상과 의미를 reason에 쓰세요.
followUpConditions.description은 고객에게 그대로 보입니다. hypothesis 항목에서 각각 검토하세요.
left/operator/right가 나타내는 조건과 description의 의미, effect, 본문의 해석이 모두 일치해야 합니다.
description이 지표만 반복하거나 ‘확인/재검토’ 작업만 말하면 hypothesis.supported=false입니다.
조건 성립으로 강해지거나 약해지는 구체적 해석이 있어야 하며, 다른 문단의 지시어를 따라갈 필요가 없어야 합니다.
조건이 충족됐다고 가정한 의미와 이미 관측된 사실을 구별하고, 확정적 전망·매매 지시·새 수치를 거부하세요.
표현이 쉬워도 의미가 과장되거나 근거가 없으면 승인하지 마세요.
"""


def readable_planning_prompt(packet, history, research):
    from .continuity import continuous_planning_prompt
    return WRITING_INSTRUCTIONS + continuous_planning_prompt(packet, history, research)


def readable_review_prompt(packet, draft):
    from .continuity import continuous_review_prompt
    return REVIEW_INSTRUCTIONS + continuous_review_prompt(packet, draft)
