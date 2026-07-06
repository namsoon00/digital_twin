import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List

from .portfolio import utc_now_iso


MODEL_REVIEW_PROMPT_VERSION = "model-review-v2-ontology"


@dataclass
class ModelReviewJob:
    job_id: str
    account_id: str
    account_label: str
    symbol: str
    title: str
    alert_key: str
    alert_lines: List[str]
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""
    status: str = "pending"
    attempts: int = 0
    result: str = ""
    last_error: str = ""
    review_context: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def create(cls, payload: Dict[str, object]) -> "ModelReviewJob":
        seed = str(payload.get("key") or payload.get("alertKey") or uuid.uuid4().hex)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return cls(
            job_id=uuid.uuid5(uuid.NAMESPACE_URL, "digital-twin:model-review:" + seed).hex,
            account_id=str(payload.get("accountId") or ""),
            account_label=str(payload.get("accountLabel") or ""),
            symbol=str(payload.get("symbol") or ""),
            title=str(payload.get("title") or ""),
            alert_key=seed,
            alert_lines=[str(line) for line in payload.get("lines") or [] if str(line).strip()],
            review_context=dict(
                metadata.get("ontologyReviewContext")
                or metadata.get("ontologyPromptContext")
                or metadata.get("ontologyOpinion")
                or {}
            ),
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "ModelReviewJob":
        return cls(
            job_id=str(payload.get("jobId") or payload.get("job_id") or uuid.uuid4().hex),
            account_id=str(payload.get("accountId") or ""),
            account_label=str(payload.get("accountLabel") or ""),
            symbol=str(payload.get("symbol") or ""),
            title=str(payload.get("title") or ""),
            alert_key=str(payload.get("alertKey") or ""),
            alert_lines=[str(line) for line in payload.get("alertLines") or [] if str(line).strip()],
            created_at=str(payload.get("createdAt") or utc_now_iso()),
            updated_at=str(payload.get("updatedAt") or ""),
            status=str(payload.get("status") or "pending"),
            attempts=int(payload.get("attempts") or 0),
            result=str(payload.get("result") or ""),
            last_error=str(payload.get("lastError") or ""),
            review_context=dict(payload.get("reviewContext") or {}),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "jobId": payload["job_id"],
            "accountId": payload["account_id"],
            "accountLabel": payload["account_label"],
            "symbol": payload["symbol"],
            "title": payload["title"],
            "alertKey": payload["alert_key"],
            "alertLines": payload["alert_lines"],
            "createdAt": payload["created_at"],
            "updatedAt": payload["updated_at"],
            "status": payload["status"],
            "attempts": payload["attempts"],
            "result": payload["result"],
            "lastError": payload["last_error"],
            "reviewContext": payload["review_context"],
        }


def build_model_review_prompt(job: ModelReviewJob) -> str:
    lines = "\n".join(["- " + line for line in job.alert_lines])
    ontology_context = ""
    if job.review_context:
        ontology_context = "\n".join([
            "",
            "온톨로지/AI 투자 의견 컨텍스트:",
            str(job.review_context),
        ])
    return "\n".join([
        "너는 투자 판단 기준을 지속적으로 개선하는 금융 데이터 리뷰어다.",
        "이번 모델은 단순 점수 계산이 아니라 투자 세계관 온톨로지, 관계, evidence 충돌을 우선한다.",
        "매수/매도 지시가 아니라 판단 변화의 원인, 데이터 검증, 다음 실험을 분석한다.",
        "한국어로 텔레그램 메시지에 맞게 간결하지만 충분히 분석해라. 영어 또는 어려운 용어는 쉬운 한국어로 풀어 써라.",
        "메시지 제목에는 계정명이나 계정 ID를 넣지 마라. 계정 정보는 전송 라우팅에만 사용한다.",
        "섹션은 반드시 다음 순서로 작성한다: 세계관 변화, 관계/모순, 데이터 검증, 모델 보완, 다음 실험.",
        "기존 점수 모델은 보조 데이터로만 다뤄라.",
        "API 키, 토큰, 계좌 식별정보를 추정하거나 요청하지 마라.",
        "",
        "리뷰 버전: " + MODEL_REVIEW_PROMPT_VERSION,
        "계정: " + (job.account_label or job.account_id or "-"),
        "종목: " + (job.symbol or job.title or "-"),
        "알림 제목: " + (job.title or "-"),
        "알림 키: " + job.alert_key,
        "실시간 알림 내용:",
        lines or "- 없음",
        ontology_context,
    ])


def local_model_review(job: ModelReviewJob) -> str:
    joined = "\n".join(job.alert_lines)
    validation = "실시간 알림의 데이터 검증 라인을 우선 확인하고, 가격/수량/평가액/손익률 원천이 모두 같은 시점인지 대조하세요."
    improvement = "거래량, 이동평균, 평가액 변화를 판단 요소로 추가해 같은 판단 변화가 반복 재현되는지 검증하세요."
    if "손익률 급변" in joined:
        validation = "손익률 급변이 가격 원천 변경, 환율, 분할/배당, 장중 급등락 중 무엇에서 왔는지 먼저 분리하세요."
        improvement = "손익률 단독 변화와 거래량/이동평균 동반 변화를 분리해 급변 이벤트의 신뢰도를 점수화하세요."
    if "현재가/평단 없음" in joined or "평가액 없음" in joined:
        validation = "가격 또는 평가액 필드가 부족하므로 판단 변화의 근거가 약합니다. 원천 API 매핑부터 보완하세요."
        improvement = "필수 판단 요소가 빠졌을 때는 점수 산출을 보류하거나 신뢰도를 낮추는 게 좋습니다."
    ontology_line = "온톨로지 컨텍스트가 없어서 알림 라인과 기존 점수 evidence만 사용했습니다."
    if job.review_context:
        opinion = job.review_context.get("opinion") if isinstance(job.review_context, dict) else {}
        worldview = job.review_context.get("worldview") if isinstance(job.review_context, dict) else {}
        thesis = str((opinion or {}).get("thesis") or "").strip()
        dominant_sector = str((worldview or {}).get("dominantSector") or "").strip()
        ontology_line = (
            "온톨로지 thesis는 " + (thesis or "요약 없음")
            + ("이며, 지배 섹터는 " + dominant_sector + "입니다." if dominant_sector else "입니다.")
        )
    return "\n".join([
        (job.symbol or job.title or "판단 변화") + " 모델 리뷰",
        "- 세계관 변화: 실시간 판단 기준이 감지한 판단 이름 또는 매도/손절 압력 점수 변화가 기준선을 넘었습니다.",
        "- 관계/모순: " + ontology_line,
        "- 데이터 검증: " + validation,
        "- 모델 보완: " + improvement,
        "- 다음 실험: 동일 조건을 최근 20회 판단 변화에 다시 적용해 잘못 울린 알림과 이후 손익 흐름을 비교하세요.",
    ])


def value(payload: Dict[str, object], key: str) -> float:
    try:
        return float(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def text(payload: Dict[str, object], key: str) -> str:
    return str(payload.get(key) or "").strip()


def first_sentence(items: List[str], fallback: str) -> str:
    return "; ".join(items[:2]) if items else fallback


def signed_pct(number: float, suffix: str = "%") -> str:
    rounded = round(float(number or 0), 1)
    return ("+" if rounded > 0 else "") + str(rounded) + suffix


def pct_delta(current: float, previous: float) -> float:
    base = float(previous or 0)
    if not base:
        return 0.0
    return ((float(current or 0) / base) - 1) * 100


def decision_change_review_lines(
    current_position: Dict[str, object],
    previous_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pressure_threshold: float,
) -> List[str]:
    pressure_delta = value(current_decision, "exit_pressure") - value(previous_decision, "exit_pressure")
    pnl_delta = value(current_position, "profit_loss_rate") - value(previous_position, "profit_loss_rate")
    market_value_delta = pct_delta(value(current_position, "market_value"), value(previous_position, "market_value"))
    decision_changed = text(current_decision, "decision") != text(previous_decision, "decision")

    reasons: List[str] = []
    if decision_changed:
        reasons.append("판단명이 " + (text(previous_decision, "decision") or "-") + "에서 " + (text(current_decision, "decision") or "-") + "로 바뀜")
    if abs(pressure_delta) >= float(pressure_threshold or 0):
        reasons.append("매도/손절 압력 점수가 " + signed_pct(pressure_delta, "점") + " 변해 기준 " + str(round(float(pressure_threshold or 0), 1)) + "점 이상")

    drivers: List[str] = []
    if abs(pnl_delta) >= 1:
        drivers.append("손익률 " + signed_pct(pnl_delta, "%p"))
    if abs(market_value_delta) >= 3:
        drivers.append("평가액 " + signed_pct(market_value_delta))
    if value(current_position, "quantity") != value(previous_position, "quantity"):
        drivers.append("수량 " + str(previous_position.get("quantity", 0)) + " -> " + str(current_position.get("quantity", 0)))

    validation = model_data_validation(current_position, previous_position, current_decision, previous_decision, pnl_delta)
    improvement = model_improvement_hint(current_position, current_decision, previous_decision, pressure_delta, pnl_delta, pressure_threshold)

    return [
        "Codex 답변: " + first_sentence(reasons, "판단 기준에 의미 있는 변화가 감지됨") + ". 주요 변화는 " + first_sentence(drivers, "점수 구성값 변화") + "입니다.",
        "데이터 검증: " + validation,
        "모델 보완: " + improvement,
    ]


def model_data_validation(
    current_position: Dict[str, object],
    previous_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pnl_delta: float,
) -> str:
    issues: List[str] = []
    if not text(current_position, "symbol"):
        issues.append("종목코드 누락")
    if value(current_position, "market_value") <= 0:
        issues.append("평가액 없음")
    if value(current_position, "quantity") <= 0:
        issues.append("보유수량 없음")
    if value(current_position, "current_price") <= 0 and value(current_position, "average_price") <= 0:
        issues.append("현재가/평단 없음")
    if not text(current_position, "sector"):
        issues.append("섹터 미분류")
    if not text(current_decision, "decision") or not text(previous_decision, "decision"):
        issues.append("판단 라벨 누락")
    if abs(pnl_delta) >= 12:
        issues.append("손익률 급변 원천 확인")
    if value(previous_position, "market_value") <= 0:
        issues.append("이전 평가액 기준 약함")
    if issues:
        return "확인 필요 - " + ", ".join(issues[:4])
    return "평가액, 수량, 손익률, 판단 라벨이 모두 비교 가능"


def model_improvement_hint(
    current_position: Dict[str, object],
    current_decision: Dict[str, object],
    previous_decision: Dict[str, object],
    pressure_delta: float,
    pnl_delta: float,
    pressure_threshold: float,
) -> str:
    if abs(pressure_delta) < float(pressure_threshold or 0) and text(current_decision, "decision") != text(previous_decision, "decision"):
        return "판단 기준값 근처 흔들림을 줄이도록 완충 구간과 최소 유지 시간을 추가"
    if abs(pnl_delta) >= 5:
        return "거래량과 이동평균으로 손익률 급변이 추세인지 일시 변동인지 검증"
    if value(current_position, "current_price") <= 0:
        return "현재가 원천을 연결해 평가액 기반 점수의 신뢰도를 먼저 보강"
    if not text(current_position, "sector"):
        return "업종 매핑을 보강해 집중도 기반 매도/손절 압력 점수의 잘못된 알림을 줄이기"
    return "거래량, 이동평균, 평가액 변화를 판단 요소로 추가해 판단 변화의 재현성을 검증"
