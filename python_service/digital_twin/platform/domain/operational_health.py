"""Pure operational-health classification for investor and operator surfaces."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import re
from typing import Dict, Iterable, Mapping, Tuple


OPERATIONAL_HEALTH_CONTRACT_VERSION = "operational-health-v3"
STATE_ORDER = {
    "healthy": 0,
    "unknown": 1,
    "warning": 2,
    "critical": 3,
}
DIMENSION_LABELS = {
    "availability": "서비스 가용성",
    "freshness": "데이터 최신성",
    "workload": "처리 대기열",
    "delivery": "판단·알림 전달",
    "capacity": "저장공간",
    "historicalDebt": "과거 운영 부채",
}


def _state(value: object) -> str:
    normalized = str(value or "healthy").strip().lower()
    return normalized if normalized in STATE_ORDER else "unknown"


def _maximum_state(values: Iterable[object]) -> str:
    selected = "healthy"
    for value in values:
        candidate = _state(value)
        if STATE_ORDER[candidate] > STATE_ORDER[selected]:
            selected = candidate
    return selected


def _revision(value: object) -> int:
    matched = re.search(r"(?:^|[-_])r(?P<revision>\d+)$", str(value or "").strip(), re.IGNORECASE)
    return int(matched.group("revision")) if matched else -1


@dataclass(frozen=True)
class OperationalHealthSignal:
    signal_id: str
    label: str
    dimension: str
    state: str
    detail: str
    reason_code: str = ""
    updated_at: str = ""
    impact: str = "operational"
    historical: bool = False
    action: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "id": payload["signal_id"],
            "label": payload["label"],
            "dimension": payload["dimension"],
            "state": _state(payload["state"]),
            "detail": payload["detail"],
            "reasonCode": payload["reason_code"],
            "updatedAt": payload["updated_at"],
            "impact": payload["impact"],
            "historical": payload["historical"],
            "action": payload["action"],
            "actionable": bool(payload["action"]),
        }


@dataclass(frozen=True)
class OperationalHealthAssessment:
    signals: Tuple[OperationalHealthSignal, ...]

    def to_dict(self) -> Dict[str, object]:
        rows = [signal.to_dict() for signal in self.signals]
        current_rows = [row for row in rows if not row["historical"]]
        user_rows = [row for row in current_rows if row["impact"] == "user"]
        dimensions = {}
        for dimension, label in DIMENSION_LABELS.items():
            selected = [row for row in rows if row["dimension"] == dimension]
            dimensions[dimension] = {
                "label": label,
                "state": _maximum_state(row["state"] for row in selected),
                "count": len(selected),
                "attentionCount": sum(
                    1 for row in selected if row["state"] in {"warning", "critical", "unknown"}
                ),
            }
        actions = [
            {
                **dict(row["action"]),
                "signalId": row["id"],
                "state": row["state"],
                "historical": row["historical"],
            }
            for row in rows
            if row["action"] and row["state"] != "healthy"
        ]
        return {
            "contractVersion": OPERATIONAL_HEALTH_CONTRACT_VERSION,
            "serviceState": _maximum_state(row["state"] for row in user_rows),
            "attentionState": _maximum_state(row["state"] for row in rows),
            "attentionCount": sum(
                1 for row in rows if row["state"] in {"warning", "critical", "unknown"}
            ),
            "historicalDebtCount": sum(
                1 for row in rows if row["historical"] and row["state"] != "healthy"
            ),
            "dimensions": dimensions,
            "actions": actions,
            "summary": dict(Counter(row["state"] for row in rows)),
            "signals": rows,
        }


def reasoning_engine_health_signals(engine: Mapping[str, object]) -> Tuple[OperationalHealthSignal, ...]:
    """Separate current delivery health from durable historical failures."""

    values = dict(engine or {})
    control = dict(values.get("control") or {})
    queue = dict(values.get("queue") or {})
    active = dict(values.get("activeDeployment") or {})
    candidate = dict(values.get("candidateDeployment") or {})
    active_id = str(control.get("activeDeploymentId") or "").strip()
    delivery_id = str(control.get("deliveryDeploymentId") or "").strip()
    candidate_id = str(control.get("candidateDeploymentId") or "").strip()
    reasons = [str(item or "").strip() for item in values.get("reasons") or [] if str(item or "").strip()]
    unresolved = int(queue.get("unresolvedFailureCount") or 0)
    recent_failures = int(queue.get("recentFailureCount24h") or 0)
    pending = int(queue.get("pendingCount") or 0)
    historical_failure_only = bool(
        unresolved
        and not recent_failures
        and pending == 0
        and set(reasons).issubset({"reasoning-failures-present"})
    )
    current_reasons = [
        reason for reason in reasons
        if not (reason == "reasoning-failures-present" and historical_failure_only)
    ]
    platform_status = str(values.get("status") or "").strip().lower()
    if not active_id or not active:
        active_state = "critical"
        reason_code = "active-reasoning-deployment-unavailable"
    elif platform_status in {"unavailable", "blocked", "critical"}:
        active_state = "critical"
        reason_code = current_reasons[0] if current_reasons else "reasoning-platform-unavailable"
    elif current_reasons:
        active_state = "warning"
        reason_code = current_reasons[0]
    else:
        active_state = "healthy"
        reason_code = "reasoning-delivery-ready"
    signals = [OperationalHealthSignal(
        signal_id="reasoning-engine",
        label="추론 엔진 배포",
        dimension="availability",
        state=active_state,
        detail=(
            "활성·전달 배포가 일치하고 현재 추론 대기열을 처리할 수 있습니다."
            if active_state == "healthy"
            else " · ".join(current_reasons) or "활성 추론 배포를 사용할 수 없습니다."
        ),
        reason_code=reason_code,
        impact="user",
        action=(
            {}
            if active_state == "healthy"
            else {"id": "open-reasoning-status", "label": "추론 상태 확인", "view": "reasoning"}
        ),
    )]
    if unresolved:
        signals.append(OperationalHealthSignal(
            signal_id="reasoning-history",
            label="과거 추론 실패",
            dimension="historicalDebt",
            state="warning",
            detail=(
                f"미해결 {unresolved}건 · 최근 24시간 신규 실패 {recent_failures}건"
                + (" · 현재 처리에는 영향 없음" if historical_failure_only else "")
            ),
            reason_code="reasoning-historical-failures",
            updated_at=str(queue.get("latestUnresolvedFailureAt") or ""),
            impact="none" if historical_failure_only else "operational",
            historical=historical_failure_only,
            action={"id": "replay-reasoning-failures", "label": "복구 상태 확인", "view": "reasoning"},
        ))
    active_revision = _revision(active_id)
    candidate_revision = _revision(candidate_id)
    if candidate_id and active_revision >= 0 and candidate_revision >= 0 and candidate_revision <= active_revision:
        signals.append(OperationalHealthSignal(
            signal_id="reasoning-candidate",
            label="오래된 후보 배포",
            dimension="historicalDebt",
            state="warning",
            detail=f"활성 {active_id}보다 오래된 후보 {candidate_id}가 남아 있습니다.",
            reason_code="stale-reasoning-candidate",
            impact="none",
            historical=True,
            action={"id": "retire-stale-candidate", "label": "후보 상태 확인", "view": "reasoning"},
        ))
    if delivery_id and active_id and delivery_id != active_id and active_state == "healthy":
        signals[0] = OperationalHealthSignal(
            signal_id="reasoning-engine",
            label="추론 엔진 배포",
            dimension="availability",
            state="warning",
            detail=f"활성 {active_id}와 전달 {delivery_id}가 일치하지 않습니다.",
            reason_code="active-delivery-deployment-mismatch",
            impact="user",
            action={"id": "open-reasoning-status", "label": "추론 상태 확인", "view": "reasoning"},
        )
    return tuple(signals)


def assess_operational_health(signals: Iterable[OperationalHealthSignal]) -> Dict[str, object]:
    return OperationalHealthAssessment(tuple(signals or ())).to_dict()
