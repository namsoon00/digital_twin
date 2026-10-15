"""Research knowledge vocabulary; admission never grants investment authority."""
from .ontology_tbox_contracts import TBoxClassDef, TBoxRelationDef

RESEARCH_KNOWLEDGE_CLASSES = [
    TBoxClassDef("ServiceResearchPolicy", "research-evidence", "서비스 연구 목적", description="기업 이해·기회 탐색·포트폴리오 위험 연구의 목적과 근거 기준입니다. 투자 행동 정책이 아닙니다."),
    TBoxClassDef("ResearchFramework", "research-evidence", "전문 연구 틀", parent="ResearchPlan", description="근거 문헌, 적용 조건·예외, 질문과 자료 요구를 가진 버전별 연구 방법입니다."),
    TBoxClassDef("CompanyResearchModel", "research-evidence", "기업 연구 모형", description="원천 기업 자료와 연결된 계정 범위별 설명·연구 주장을 묶습니다."),
    TBoxClassDef("ResearchKnowledgeClaim", "research-evidence", "연구 지식 주장", parent="InvestmentThesis", description="지지·반박 근거, 가정, 반증 조건을 가진 검증 전 연구 주장입니다. 실행 가능한 모델 가설과 구분합니다."),
    TBoxClassDef("ResearchKnowledgeRevision", "research-evidence", "연구 지식 개정", parent="AIJudgmentAudit", description="동결 입력과 이전 버전을 참조하는 불변 연구 변경 기록입니다."),
    TBoxClassDef("ResearchKnowledgeAssessment", "outcome-learning", "연구 관측 평가", description="원래 계약에 대한 지표 관측을 연결합니다. 인과성·예측력 검증과 구분합니다."),
    TBoxClassDef("ResearchMethodProposal", "outcome-learning", "연구 방법 개선 제안", parent="LearningProposal", description="관측과 오류에서 제안한 적용 조건이 있는 개선안입니다. 채택·규칙 변경 권한을 부여하지 않습니다."),
    TBoxClassDef("KnowledgeMethodReference", "research-evidence", "연구 방법 문헌", description="일반 연구 방법의 출처이며 개별 기업이나 임계값의 실증 근거가 아닙니다."),
]
RESEARCH_KNOWLEDGE_RELATIONS = [
    TBoxRelationDef("USES_RESEARCH_FRAMEWORK", "research-evidence", "research-evidence", "research-evidence"),
    TBoxRelationDef("HAS_RESEARCH_KNOWLEDGE_CLAIM", "research-evidence", "research-evidence", "research-evidence"),
    TBoxRelationDef("HAS_RESEARCH_KNOWLEDGE_REVISION", "research-evidence", "research-evidence", "research-evidence"),
    TBoxRelationDef("HAS_RESEARCH_ASSESSMENT", "outcome-learning", "research-evidence", "outcome-learning"),
    TBoxRelationDef("PROPOSES_RESEARCH_METHOD", "outcome-learning", "outcome-learning", "outcome-learning"),
    TBoxRelationDef("HAS_METHODOLOGY_REFERENCE", "research-evidence", "research-evidence", "research-evidence"),
]
