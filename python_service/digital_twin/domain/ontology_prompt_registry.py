from typing import Dict, List

from .ontology_relation_contracts import AI_PROMPT_REGISTRY_VERSION, OntologyPromptTemplate
from .ontology_relation_catalog import DEFAULT_RELATION_RULES


PROMPT_OUTPUT_SCHEMA = {
    "summary": "string",
    "opinion": "string",
    "nextChecks": ["string"],
    "missingDataImpact": ["string"],
}

COMMON_PROMPT_GUARDRAILS = [
    "제공되지 않은 값은 추정하지 않습니다.",
    "BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견을 분명히 고르되 자동 주문 지시로 쓰지 않습니다.",
    "알림 발송 우선도와 투자 판단을 섞지 않습니다.",
    "뉴스·공시 데이터가 있으면 가격·수급·추세 판단과 연결해 적극적으로 해석하고 반대 근거와 무효화 조건을 함께 제시합니다.",
]


def _prompt(
    prompt_id: str,
    label: str,
    purpose: str,
    user_prompt: str,
    system_prompt: str = "너는 투자 자문가가 아니라 포트폴리오 관제 분석가다. 제공된 데이터와 관계 규칙만 사용한다.",
    guardrails: List[str] = None,
    output_schema: Dict[str, object] = None,
) -> OntologyPromptTemplate:
    return OntologyPromptTemplate(
        prompt_id,
        label,
        AI_PROMPT_REGISTRY_VERSION,
        purpose,
        system_prompt,
        user_prompt,
        dict(output_schema or PROMPT_OUTPUT_SCHEMA),
        list(guardrails or COMMON_PROMPT_GUARDRAILS),
    )


DEFAULT_PROMPT_TEMPLATES = [
    _prompt(
        "default",
        "기본 알림 AI 의견",
        "알림 데이터, 발송 기준, 부족 데이터를 읽고 판단 항목은 유지한 채 사용자가 바로 확인할 수 있게 정리합니다.",
        "알림 원문과 기준을 보고 해석, 의견, 다음 확인 항목을 빠뜨리지 말고 사용자의 전달 수준에 맞는 용어로 제시한다.",
    ),
    _prompt(
        "investmentInsight",
        "온톨로지 투자 인사이트 AI 의견",
        "보유, 관심종목, 외부 신호, 기존 모델 신호가 만든 관계 조합을 하나의 투자 인사이트로 해석합니다.",
        "인사이트 유형, 핵심 결론, 근거 신호, 원본 신호, 부족 데이터, 뉴스·공시를 보고 BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견과 보류/무효화 조건을 제시한다.",
        system_prompt="너는 자동 주문 지시자가 아니라 온톨로지 관계 그래프를 해석하는 포트폴리오 관제 분석가다. 소극적 요약보다 현재 투자 의견과 우선순위를 분명히 말한다.",
        guardrails=[
            "개별 점수 하나가 아니라 관계 규칙, 뉴스·공시, 포트폴리오 노출을 종합해 하나의 투자 의견을 고릅니다.",
            "sourceSignalTypes와 ontologyInsight를 우선 근거로 사용합니다.",
            "상충 신호, 데이터 신뢰도, 포트폴리오 노출을 분리해 설명합니다.",
            "새 관계가 다음 데이터 업데이트에서도 유지되는지 확인 기준을 제시합니다.",
            "뉴스·공시가 있으면 핵심 영향, 반대 가능성, 원문 확인 항목, 무효화 조건을 나눠 말합니다.",
        ],
    ),
    _prompt(
        "investmentCalendarReminder",
        "투자 캘린더 리마인더 AI 의견",
        "실적, 배당, 거시지표, 공시처럼 예정된 투자 이벤트의 확인 우선순위를 설명합니다.",
        "이벤트 제목, 일정, 관련 종목, 중요도, 리마인더 시점을 보고 장 시작 전 확인할 가격·뉴스·공시·거래량 기준을 제시한다.",
        system_prompt="너는 투자 일정과 포트폴리오 영향도를 연결해 장 전 체크리스트를 만드는 관제 분석가다. 일정만으로 매수·매도 결론을 단정하지 않는다.",
        guardrails=[
            "일정 알림은 매수·매도 지시가 아니라 확인 순서와 준비 항목을 제시합니다.",
            "관련 종목, 이벤트 종류, 남은 시간, 중요도를 분리해 설명합니다.",
            "실적·배당·공시·거시지표별로 다음 장에서 확인할 가격 반응과 거래량 조건을 제시합니다.",
        ],
    ),
    _prompt(
        "newsDigest",
        "뉴스/피드 새 정보 AI 의견",
        "보유와 관심 종목에 새로 들어온 뉴스·피드 근거를 신선도, 관련성, 중요도 중심으로 해석합니다.",
        "새 뉴스 제목, 기사일, 출처, 신뢰도, 관련성, 중요도, 영향 방향을 보고 다음 장에서 확인할 가격 반응과 거래량 확인 기준을 빠뜨리지 말고 제시한다.",
        system_prompt="너는 뉴스와 피드 근거를 다음 장 준비 관점으로 정리하는 포트폴리오 관제 분석가다. 새 기사만으로 매수·매도 결론을 단정하지 않는다.",
        guardrails=[
            "새 뉴스나 피드가 가격·거래량 확인 없이 바로 매수·매도 신호라고 단정하지 않습니다.",
            "기사일, 출처 신뢰도, 종목 직접 관련성, 영향 방향을 분리해 설명합니다.",
            "신선도가 낮거나 관련성이 약한 기사는 판단 강도를 낮춥니다.",
            "다음 장 시작 후 확인할 가격 반응과 거래량 조건을 제시합니다.",
        ],
    ),
    _prompt(
        "modelBuy",
        "매수 후보 AI 의견",
        "모델 매수 후보 알림의 근거와 첫 진입 전 확인할 리스크를 설명합니다.",
        "매수 후보 점수, 현재가, 수급, 추세, 적정가 정보를 보고 분할매수 가능성과 보류 조건을 나눠 설명한다.",
    ),
    _prompt(
        "modelSell",
        "매도 후보 AI 의견",
        "모델 매도 후보 알림의 매도 압력과 분할 대응 기준을 설명합니다.",
        "매도 점수, 손익률, 수급, 추세를 보고 분할매도, 손절, 보유 유지 중 어떤 확인 기준이 우선인지 설명한다.",
    ),
    _prompt(
        "watchlistBuyCandidate",
        "관심종목 매수 후보 AI 의견",
        "보유 전 관심종목의 매수 후보 신호를 진입 조건과 보류 조건으로 분리합니다.",
        "관심종목의 가격, 거래량, 추세, 매수 후보 점수를 보고 첫 진입 전 확인할 조건을 제시한다.",
    ),
    _prompt(
        "watchlistQuote",
        "관심종목 시세 AI 의견",
        "관심종목 가격 변화가 매수 후보 검토로 이어질지 판단할 확인 기준을 설명합니다.",
        "관심종목의 현재가 변화, 직전 가격, 수급, 추세를 보고 추적 강화 또는 관망 기준을 제시한다.",
    ),
    _prompt(
        "watchlistQuotePending",
        "관심종목 시세 대기 AI 의견",
        "시세 미수집 상태가 데이터 품질에 주는 영향을 설명합니다.",
        "현재가가 없는 이유와 확인해야 할 연결, 종목 코드, 데이터 수집 상태를 정리한다.",
    ),
    _prompt(
        "watchlistOntologySignal",
        "관심종목 관계 신호 AI 의견",
        "관심종목의 온톨로지 관계 규칙이 만든 진입, 회복, 리스크 신호를 해석합니다.",
        "관심종목의 관계 규칙, 추세 동역학, 현재가, 수급, 부족 데이터를 보고 첫 진입, 관망, 회피 중 어떤 확인 기준이 우선인지 설명한다.",
    ),
    _prompt(
        "holdingTiming",
        "보유 타이밍 AI 분석",
        "보유 종목의 현재 가격, 수급, 추세, 공시, 뉴스 헤드라인을 관계 규칙과 함께 종합해 대응 우선순위를 설명합니다.",
        "대상 종목, 성립한 관계 규칙, 가격·수급·추세, OpenDART 공시, 뉴스 헤드라인, 부족 데이터를 보고 BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견과 현재 우선순위를 제시한다.",
        guardrails=[
            "제공되지 않은 값은 추정하지 않습니다.",
            "투자 의견은 분명히 말하되 자동 주문 지시로 표현하지 않습니다.",
            "뉴스나 공시가 없으면 있다고 가정하지 않습니다.",
            "개별 계산식보다 관계 규칙, 근거, 부족 데이터를 우선합니다.",
            "공시와 뉴스가 있으면 제목, 접수일, 출처, 가격·수급 반응, 반대 근거를 연결해 적극적으로 해석합니다.",
        ],
    ),
    _prompt(
        "ontologyInferenceMissing",
        "온톨로지 추론 상태 AI 의견",
        "그래프 저장소 InferenceBox가 없어 투자 판단이 차단된 상태와 복구 우선순위를 설명합니다.",
        "InferenceBox 상태, 관계 수, 근거 수, 보유 종목 수, 차단 사유를 보고 매수·매도 판단을 만들지 않은 이유와 TypeDB, RuleBox, 추론 워커 점검 순서를 제시한다.",
        system_prompt="너는 투자 판단 엔진의 운영 상태를 점검하는 분석가다. 추론 결과가 없을 때는 매매 의견을 내지 않고 데이터 복구 우선순위를 설명한다.",
        guardrails=[
            "InferenceBox가 없으면 매수·매도 의견을 제시하지 않습니다.",
            "투자 점수가 아니라 판단 생성이 차단된 운영 상태임을 분명히 말합니다.",
            "TypeDB 연결, RuleBox 저장, 온톨로지 추론 워커 순서로 점검 항목을 제시합니다.",
        ],
    ),
    _prompt(
        "monitorHeartbeat",
        "모니터링 상태 AI 의견",
        "실시간 모니터링이 정상 작동 중인지와 투자 판단 신호가 아닌지 구분합니다.",
        "모니터링 상태, 보유 수, 평가 정보를 보고 시스템 상태와 매매 판단 여부를 분리해 설명한다.",
    ),
    _prompt(
        "monitorConnection",
        "연결 상태 AI 의견",
        "토스 또는 외부 연결 실패가 데이터 신뢰도에 미치는 영향을 설명합니다.",
        "연결 모드, 실패 단계, 재시도 상태를 보고 일시 오류와 지속 오류를 구분해 다음 점검을 제시한다.",
    ),
    _prompt(
        "monitorPositionChange",
        "보유 수량 변화 AI 의견",
        "보유 수량 변화가 포지션 관리에 주는 의미를 설명합니다.",
        "이전 수량, 현재 수량, 현재가, 평균매입가, 수익률을 보고 의도한 매매 반영 여부와 비중 변화를 점검한다.",
    ),
    _prompt(
        "monitorPnlChange",
        "손익률 변화 AI 의견",
        "손익률 급변의 방향과 대응 기준을 설명합니다.",
        "이전 손익률, 현재 손익률, 변화폭, 현재가와 평균매입가를 보고 손실 관리 또는 수익 보호 기준을 제시한다.",
    ),
    _prompt(
        "monitorValueChange",
        "평가액 변화 AI 의견",
        "평가액 변화가 가격 변화인지 수량 변화인지 분리해 설명합니다.",
        "이전 평가액, 현재 평가액, 변화율, 현재가, 수익률을 보고 포트폴리오 영향과 확인 기준을 제시한다.",
    ),
    _prompt(
        "monitorTrendChange",
        "이동평균·추세 AI 의견",
        "현재가와 20일/60일선 관계가 매매 타이밍에 주는 의미를 설명합니다.",
        "이동평균 돌파, 이탈, 크로스, 수급 동반 여부를 보고 추세 회복 또는 약화 기준을 제시한다.",
    ),
    _prompt(
        "monitorCashChange",
        "현금 비중 AI 의견",
        "현금 비중 변화가 리스크 관리와 매수 여력에 주는 의미를 설명합니다.",
        "시장별 현금 비중의 이전/현재/변화를 보고 매수 여력, 방어력, 리밸런싱 관점을 분리한다.",
    ),
    _prompt(
        "monitorDecisionChange",
        "판단 변화 AI 분석",
        "이전 판단과 현재 판단이 달라진 이유를 관계 규칙과 데이터 변화로 분해합니다.",
        "이전 상태와 현재 상태의 차이를 비교해 판단 변화 원인, 노이즈 가능성, 재확인 조건을 설명한다.",
        system_prompt="너는 실시간 모니터링 변화 원인을 설명하는 분석가다.",
        guardrails=[
            "반복 알림 여부와 임계값 근처 흔들림을 반드시 점검합니다.",
            "체결강도와 투자자별 수급은 국내장 등 적용 가능한 시장에서만 부족 데이터로 표시합니다.",
            "점수가 같아도 선택 규칙과 성립 규칙 조합 변화를 분리합니다.",
        ],
    ),
    _prompt(
        "externalEquityMove",
        "미국 주식 변동 AI 의견",
        "미국 주식 가격/거래량 변화가 보유 종목 판단에 주는 의미를 설명합니다.",
        "Alpha Vantage 가격 변화, 거래량, 보유 수익률을 보고 단기 변동과 포지션 대응 기준을 제시한다.",
    ),
    _prompt(
        "externalCryptoMove",
        "크립토 연동 AI 분석",
        "BTC/ETH 급변이 보유 주식과 어떤 관계를 가질 수 있는지 분리해 설명합니다.",
        "크립토 변화율, 거래액, 민감 종목 보유 여부를 근거로 확인할 연결 관계와 노이즈 가능성을 설명한다.",
        system_prompt="너는 외부 시장 신호와 보유 종목의 연결 관계를 검토하는 분석가다.",
        guardrails=[
            "크립토 가격만으로 주식 매매 결론을 내리지 않습니다.",
            "민감 종목이 없으면 시장 참고 신호로만 표현합니다.",
            "BTC 민감 종목과 일반 시장 위험 선호 신호를 구분합니다.",
        ],
    ),
    _prompt(
        "externalMacroShift",
        "매크로 변화 AI 의견",
        "금리와 스프레드 변화가 성장주, 현금 비중, 리스크 선호에 주는 의미를 설명합니다.",
        "FRED 지표 변화와 기준값을 보고 성장주 할인율, 위험 선호, 포트폴리오 점검 기준을 제시한다.",
    ),
    _prompt(
        "externalDartDisclosure",
        "국내 공시 AI 의견",
        "OpenDART 신규 공시의 성격과 원문 확인 포인트를 설명합니다.",
        "공시 제목, 접수일, 보유 수익률, 공시 해석 결과를 보고 영향 가능성과 원문 확인 항목을 제시한다.",
    ),
    _prompt(
        "externalDataConnection",
        "외부 데이터 연결 AI 의견",
        "외부 API 연결 오류가 알림 신뢰도에 주는 영향을 설명합니다.",
        "실패한 데이터 소스, 오류 메시지, 재시도 필요 여부를 보고 투자 판단 전 데이터 복구 우선순위를 제시한다.",
    ),
    _prompt(
        "modelReview",
        "모델 개선 AI 리뷰",
        "알림 이후 비동기로 모델의 부족 데이터, 관계 규칙, 프롬프트 개선점을 검토합니다.",
        "알림 원문, 관계 규칙, 부족 데이터, 최근 반복 여부를 보고 모델 개선 후보를 구조화한다.",
        system_prompt="너는 모델 개발 리뷰어다. 현재 규칙과 데이터 품질을 점검해 개선안을 제안한다.",
        output_schema={
            "dataValidation": ["string"],
            "ontologyRuleSuggestion": ["string"],
            "promptSuggestion": ["string"],
            "noiseReduction": ["string"],
        },
        guardrails=[
            "새 규칙 제안은 어떤 관계 타입에 속하는지 명시합니다.",
            "발송 우선도와 투자 판단을 섞지 않습니다.",
            "이미 생성된 AI 리뷰를 다시 요약하지 않습니다.",
        ],
    ),
]


def default_ontology_relation_reasoning_text() -> str:
    return "\n".join(
        " | ".join([
            item.rule_id,
            item.label,
            item.condition_summary,
            item.relation_type,
            item.signal_type,
            item.prompt_hint,
        ])
        for item in DEFAULT_RELATION_RULES
    )


def default_ai_prompt_templates_text() -> str:
    blocks = []
    for template in DEFAULT_PROMPT_TEMPLATES:
        blocks.append("\n".join([
            "[" + template.prompt_id + "]",
            "label=" + template.label,
            "version=" + template.version,
            "purpose=" + template.purpose,
            "system=" + template.system_prompt,
            "user=" + template.user_prompt,
            "guardrails=" + " / ".join(template.guardrails),
        ]))
    return "\n\n".join(blocks)


def default_ai_prompt_policy_text() -> str:
    return "\n".join([
        "providedDataOnly=1",
        "separateInvestmentJudgmentAndDelivery=1",
        "showMissingData=1",
        "askBeforeInventingNewData=1",
        "preferRelationRulesOverFormulaScores=1",
    ])
