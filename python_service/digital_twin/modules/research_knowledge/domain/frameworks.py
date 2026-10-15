"""Authored research methods, never empirical evidence or trading rules."""
from copy import deepcopy

VERSION = "research-knowledge-v1"
PURPOSES = ("company-value", "opportunity", "portfolio-risk")
SERVICE_POLICY = {
    "id": "evidence-based-investment-partner-v1",
    "mission": "기업과 시장의 변화를 이해하고 가치·가격·개인 포트폴리오에 미치는 의미를 근거와 함께 연구합니다.",
    "purposes": list(PURPOSES),
    "principles": ["정확성과 출처 추적", "반론과 미확인 사항 보존", "목적·기간·계정 범위 구분", "사전 조건으로 결과 검증"],
    "researchPriority": "확인하면 기존 설명을 바꿀 수 있는 부족 근거와 반증을 먼저 조사합니다.",
    "authority": "research-reference-only",
}

# References justify the research method, not a security-specific prediction,
# threshold, causal conclusion, or permission to trade. These are paraphrases.
FRAMEWORKS = (
    {
        "id": "growth-cash-conversion-v1", "version": 2, "purpose": "company-value",
        "label": "성장의 질과 현금 전환",
        "mechanism": "손익과 현금흐름은 다른 측정입니다. 성장의 설명을 이익, 운전자본, 투자 지출과 연결해 확인합니다.",
        "applicability": "비금융 사업의 비교 가능한 보고 자료. 금융회사에는 산업별 보고·평가 방법을 먼저 확인합니다.",
        "questions": ["매출 변화가 이익과 영업현금흐름으로 이어지는가?", "매출채권·재고·투자 지출이 다른 설명을 지지하는가?"],
        "requirements": [
            {"key": "revenue", "category": ["company", "research"], "fields": ["reportedValues.revenue"], "label": "기간·통화·연결 범위가 일치하는 매출"},
            {"key": "operating-cash-flow", "category": ["company", "research"], "fields": ["reportedValues.operatingCashFlow"], "label": "동일 기간 영업현금흐름"},
            {"key": "working-capital", "category": ["company", "research"], "fields": ["reportedValues.accountsReceivable", "reportedValues.inventory"], "label": "매출채권·재고와 현금 회수 설명"},
        ],
        "alternatives": ["제품 구성이나 가격의 개선", "외상 판매·재고 증가에 따른 현금 회수 지연", "일회성 항목 또는 보고 범위 변경"],
        "limits": ["매출 증가만으로 현금창출 개선을 확정하지 않습니다.", "한 지표의 방향 관측은 전체 인과 가설의 검증이 아닙니다."],
        "references": [{"title": "SEC: Beginners' Guide to Financial Statements", "url": "https://www.sec.gov/about/reports-publications/investorpubsbegfinstmtguide", "role": "methodology"}],
    },
    {
        "id": "price-expectations-v1", "version": 2, "purpose": "opportunity",
        "label": "가격의 요구 조건과 확인 사건",
        "mechanism": "가치평가의 성장·수익성·재투자 가정을 명시하고, 현재 가격을 설명하는 조건과 실제 자료를 비교합니다.",
        "applicability": "기존 가치평가 모듈에 출처와 가정 버전이 있는 대상. 역산 결과는 다른 입력을 고정한 조건부 결과입니다.",
        "questions": ["현재 가격을 설명하려면 어떤 사업 조건이 필요한가?", "어떤 확인 사건이 가설을 강화·약화하며 언제 관측 가능한가?"],
        "requirements": [
            {"key": "valuation", "category": "valuation", "fields": [], "label": "출처·가정·방법이 고정된 가치평가 및 역산 결과"},
            {"key": "confirmation-event", "category": "research", "fields": [], "label": "발생 시점과 내용이 검증된 공시·사건"},
        ],
        "alternatives": ["가격에 반영된 조건의 변화", "현금흐름 또는 할인율 가정의 악화", "사업 변화와 무관한 가격 움직임"],
        "limits": ["역산 조건을 실제 투자자들의 확정된 믿음으로 표현하지 않습니다.", "조건부 평가 범위는 확률구간이나 단기 가격 예측이 아닙니다."],
        "references": [{"title": "Aswath Damodaran: Growth Companies — Value Drivers", "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/growthvaluedrivers.htm", "role": "methodology"}],
    },
    {
        "id": "portfolio-common-risk-v1", "version": 2, "purpose": "portfolio-risk",
        "label": "공통 위험 노출과 개인 투자 정책",
        "mechanism": "자산 개수와 위험 분산은 다릅니다. 공통 산업·통화·금리 노출을 개인 투자 기간과 정책 맥락에서 조사합니다.",
        "applicability": "현재 계정의 검증된 포지션·노출·정책 자료. 다른 계정의 비중이나 정책을 가져오지 않습니다.",
        "questions": ["이 기업의 변화가 기존 보유 자산과 같은 위험에 노출되는가?", "기업 전망과 현재 계좌의 적합성은 어떻게 다른가?"],
        "requirements": [
            {"key": "account-position", "category": "quote", "fields": ["positionWeight", "positionRole"], "label": "현재 계정의 대상 자산 비중과 보유·관심 역할"},
            {"key": "account-policy", "category": "quote", "fields": ["strategyMaxPositionWeightPct", "policyVersion"], "label": "동일 스냅샷의 비중 한도와 투자 정책 버전"},
            {"key": "common-exposure", "category": "portfolio", "fields": [], "label": "같은 계정의 전체 자산과 공통 위험 노출; 개별 종목만으로 확인할 수 없습니다."},
        ],
        "alternatives": ["같은 산업·통화에 대한 중복 노출", "기업 전망과 다른 개인 유동성·투자 기간 요구", "노출 자료 부족"],
        "limits": ["상관관계를 인과관계로 해석하지 않습니다.", "회사 전망을 개인 계좌의 추가 투자 적합성으로 자동 변환하지 않습니다."],
        "references": [{"title": "SEC: Asset Allocation, Diversification, and Rebalancing", "url": "https://www.sec.gov/about/reports-publications/investorpubsassetallocationhtm", "role": "methodology"}],
    },
)


def framework_catalog():
    return deepcopy(list(FRAMEWORKS))


def service_research_policy():
    return deepcopy(SERVICE_POLICY)
