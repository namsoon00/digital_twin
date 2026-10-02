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


CITATION_INSTRUCTIONS = """시세 시점 근거 인용 확장 계약:
counterEvidence의 시세 시점 설명은 period=assessment, factId=해당 stock의 id로 인용하세요.
field는 status, checkedAt, sourceAsOf, ageMinutes, maxAgeMinutes, referenceState 중 기록된 항목입니다.
이는 current.quoteAssessment.quotes의 evidenceId로 연결한 평가와 공통 checkedAt을 읽는 형식입니다.
facts에 quoteAssessment 필드를 만들거나 current/baseline의 field로 인용하지 마세요.
assessment는 원천 시세와 capturedAt으로 재계산해 검증한 참고 메타데이터이며 새로운 시장 사실이 아닙니다.
이 확장 형식은 counterEvidence만 허용합니다. 수치 비교·가설 근거·미래 확인 조건에는 사용할 수 없습니다.
독립 검토는 이 인용을 current.quoteAssessment와 대조하세요. 사실에 없는 quoteAssessment 필드를 요구하지 마세요.
기존 sourceAsOf/maxAgeMinutes 인용도 평가와 일치하는 시점 한계를 설명하면 메타데이터 진술로 검증하세요.
이 예외는 시점 설명에만 적용하며 다른 주장의 근거 검증·독립 검토·발송 기준을 완화하지 않습니다.
"""

ASSESSMENT_FIELDS = ("status", "checkedAt", "sourceAsOf", "ageMinutes", "maxAgeMinutes", "referenceState")


def verified_quote_assessments(packet):
    """Expose only recomputable captured metadata, never an invented ABox fact."""
    from digital_twin.modules.reasoning.contracts import content_hash, quote_clock_assessment
    assessment = packet.get("quoteAssessment")
    if not assessment:
        return {}
    expected = quote_clock_assessment(packet.get("facts", []), packet.get("capturedAt"))
    if content_hash(assessment) != content_hash(expected):
        raise ValueError("quote assessment does not match captured facts")
    return {row["evidenceId"]: {key: value for key, value in {**row, "checkedAt": expected["checkedAt"]}.items()
                               if key in ASSESSMENT_FIELDS and value is not None and value != ""}
            for row in expected["quotes"]}


def citable_management_prompt(packet, history, research):
    return CITATION_INSTRUCTIONS + clocked_management_prompt(packet, history, research)


def citable_review_prompt(packet, draft):
    return CITATION_INSTRUCTIONS + clocked_review_prompt(packet, draft)


def citable_management_schema(packet, research):
    from digital_twin.modules.ai_orchestration.domain.brain_management import management_schema
    from digital_twin.modules.ai_orchestration.domain.insight_schema import obj, choice
    schema = management_schema(packet, research)
    variants = [obj({"factId": choice([identity]), "field": choice(sorted(values)), "period": choice(["assessment"])})
                for identity, values in sorted(verified_quote_assessments(packet).items())]
    if variants:
        schema["$defs"]["limitation"] = {"anyOf": [schema["$defs"]["limitation"], *variants]}
    return schema
