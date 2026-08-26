"""Durable operational storage-capacity observation and notification use case."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Mapping, Tuple

from ..domain.events import (
    OPERATIONAL_STORAGE_CAPACITY_CHANGED,
    DomainEvent,
    operational_storage_capacity_changed_event,
)
from ..domain.data_freshness import parse_datetime
from ..domain.message_types import OPERATIONAL_STORAGE_CAPACITY
from ..domain.notifications import NotificationJob
from ..domain.operational_storage_capacity import (
    evaluate_operational_storage_capacity,
    operational_storage_capacity_enabled,
)


class OperationalStorageCapacityService:
    """Persist storage state so alerts survive worker restarts and recoveries."""

    state_key = "operationalStorageCapacity"

    def __init__(self, store=None, settings: Mapping[str, object] = None, now_provider=None):
        self.store = store
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def previous(self) -> Dict[str, object]:
        if not self.store or not hasattr(self.store, "load"):
            return {}
        try:
            payload = self.store.load()
        except Exception:  # noqa: BLE001 - capacity detection must still report through fallback delivery.
            return {}
        state = payload.get(self.state_key) if isinstance(payload, dict) else {}
        return dict(state or {}) if isinstance(state, dict) else {}

    def save(self, health: Mapping[str, object]) -> None:
        if not self.store or not hasattr(self.store, "load"):
            return
        try:
            payload = dict(self.store.load() or {})
            payload[self.state_key] = dict(health or {})
            if hasattr(self.store, "save"):
                self.store.save(payload)
            elif hasattr(self.store, "replace"):
                self.store.replace(payload)
        except Exception:
            return

    def record(
        self,
        snapshot: Mapping[str, object],
        force_alert: bool = False,
        force_alert_kind: str = "runtime-write-failure",
    ) -> Tuple[Dict[str, object], DomainEvent]:
        current = self.now_provider()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        previous = self.previous()
        health = evaluate_operational_storage_capacity(
            snapshot,
            previous=previous,
            settings=self.settings,
            now=current,
        )
        # An ENOSPC-style write failure is independent evidence that the
        # normal sampler was too late. It bypasses the long human reminder
        # window, while a short dedicated cooldown avoids one alert per
        # failing worker process.
        forced_kind = str(force_alert_kind or "runtime-write-failure").strip() or "runtime-write-failure"
        forced_alert_key = {
            "runtime-write-failure": "lastRuntimeFailureAlertAt",
            "typedb-auto-rotation": "lastCapacityRotationAlertAt",
            "typedb-auto-rotation-failed": "lastCapacityRotationFailureAlertAt",
        }.get(forced_kind, "lastForcedCapacityAlertAt")
        last_forced_alert = parse_datetime(previous.get(forced_alert_key))
        try:
            runtime_cooldown_minutes = int(
                float(str(self.settings.get("operationalStorageRuntimeFailureCooldownMinutes") or 5))
            )
        except (TypeError, ValueError):
            runtime_cooldown_minutes = 5
        runtime_cooldown_seconds = max(60, runtime_cooldown_minutes * 60)
        force_due = not last_forced_alert or (
            current.astimezone(timezone.utc) - last_forced_alert.astimezone(timezone.utc)
        ).total_seconds() >= runtime_cooldown_seconds
        if (
            force_alert
            and force_due
            and operational_storage_capacity_enabled(self.settings)
        ):
            health["capacityState"] = str(health.get("state") or "unknown")
            health.update({
                "alertRequired": True,
                "alertKind": forced_kind,
                "lastAlertAt": str(health.get("checkedAt") or ""),
                forced_alert_key: str(health.get("checkedAt") or ""),
            })
            if forced_kind == "runtime-write-failure":
                health["runtimeCapacityFailure"] = True
            else:
                health["forcedCapacityIncident"] = forced_kind
            if forced_kind == "typedb-auto-rotation":
                health["maintenanceState"] = "running"
                health["maintenanceReason"] = "TypeDB 안전 재구축 후보를 준비하고 있습니다."
            elif forced_kind == "typedb-auto-rotation-failed":
                failure_reason = str(snapshot.get("maintenanceFailureReason") or "").strip()
                health["maintenanceState"] = "failed"
                health["maintenanceReason"] = (
                    "후보 검증 실패: " + failure_reason
                    if failure_reason
                    else "후보 검증에 실패해 활성 TypeDB는 그대로 유지했습니다."
                )
                health["activeStorePreserved"] = True
                if str(health.get("state") or "healthy") == "healthy":
                    health["state"] = "warning"
                health["suggestedAction"] = (
                    "활성 TypeDB를 유지한 채 실패 원인을 확인하고 지수 백오프 후 다시 시도합니다."
                )
        for key in (
            "lastRuntimeFailureAlertAt",
            "lastForcedCapacityAlertAt",
            "lastCapacityRotationAlertAt",
            "lastCapacityRotationFailureAlertAt",
        ):
            if key in previous and key not in health:
                health[key] = previous[key]
        self.save(health)
        event = operational_storage_capacity_changed_event(health) if health.get("alertRequired") else None
        return health, event


class OperationalStorageCapacityNotificationEnqueuer:
    """Deliver capacity incidents through the operations queue with a direct fallback."""

    def __init__(self, queue, fallback_notifier_factory=None):
        self.queue = queue
        self.fallback_notifier_factory = fallback_notifier_factory

    def handle(self, event: DomainEvent) -> None:
        if event.name != OPERATIONAL_STORAGE_CAPACITY_CHANGED:
            return
        payload = dict(event.payload or {})
        if not payload.get("alertRequired"):
            return
        context = self.context(payload, event)
        job = NotificationJob.create(
            context["readableMessage"],
            account_id="",
            account_label="운영",
            message_type=OPERATIONAL_STORAGE_CAPACITY,
            source_event_id=event.event_id,
            source_event_name=event.name,
            dedupe_key="operational-storage-capacity:" + event.event_id,
            context=context,
        )
        try:
            self.queue.enqueue(job)
            return
        except Exception:
            pass
        if not callable(self.fallback_notifier_factory):
            return
        try:
            self.fallback_notifier_factory().send(context["readableMessage"])
        except Exception:
            return

    def context(self, payload: Mapping[str, object], event: DomainEvent) -> Dict[str, object]:
        values = dict(payload or {})
        state = str(values.get("state") or "unknown")
        previous = str(values.get("previousState") or "없음")
        recovered = str(values.get("alertKind") or "") == "recovered"
        kind = str(values.get("alertKind") or "")
        components = [
            item for item in list(values.get("limitingComponents") or [])
            if isinstance(item, dict)
        ]
        component_ids = {str(item.get("component") or "") for item in components}
        title = "운영 저장공간 알림 해소" if recovered else "운영 저장공간 제한 " + {
            "warning": "경고",
            "limited": "도달",
            "critical": "심각",
        }.get(state, "상태 변경")
        if not recovered and component_ids == {"mysql"}:
            title = "MySQL 저장공간 " + {
                "warning": "경고",
                "limited": "쓰기 제한",
                "critical": "심각",
            }.get(state, "상태 변경")
        if kind == "runtime-write-failure":
            title = "운영 저장공간 쓰기 실패"
        elif kind == "typedb-auto-rotation":
            title = "TypeDB 안전 재구축 시작"
        elif kind == "typedb-auto-rotation-failed":
            title = "TypeDB 안전 재구축 실패"
        elif kind in {"forecast", "forecast-eta-worsened"}:
            title = "운영 저장공간 소진 예상"
        elif kind == "material-worsening":
            title = "운영 저장공간 제한 악화"
        lines = [
            "[운영] " + title,
            "• 상태: " + state + " (이전 " + previous + ")",
            "• 공용 디스크 여유: " + str(values.get("freeMb") or 0) + "MB"
            + " · 내부 정리 " + str(values.get("warningFreeMb") or 0) + "MB"
            + " · 운영 알림 " + str(values.get("alertFreeMb") or 0) + "MB"
            + " · 제한 " + str(values.get("minimumFreeMb") or 0) + "MB"
            + " · 심각 " + str(values.get("criticalFreeMb") or 0) + "MB",
            "• TypeDB 실제 점유: " + str(values.get("typedbSizeMb") or 0) + "MB / 한도 " + str(values.get("typedbLimitMb") or 0) + "MB"
            + " · WAL " + str(values.get("typedbWalMb") or 0) + "MB"
            + " · checkpoint 참조 " + str(values.get("typedbCheckpointReferencedMb") or values.get("typedbCheckpointMb") or 0) + "MB",
            "• MySQL 실제 점유: " + str(values.get("mysqlSizeMb") or 0) + "MB / 운영 한도 "
            + str(values.get("mysqlLimitMb") or 0) + "MB ("
            + str(values.get("mysqlUsagePercent") or 0) + "%)",
            self._mysql_metadata_line(values),
            "• MySQL 단계: " + {
                "normal": "정상",
                "maintenance": "예방 정리",
                "warning": "경고",
                "restricted": "비필수 쓰기 제한",
                "critical": "긴급 정리",
                "core-only": "핵심 이력만 기록",
            }.get(str(values.get("mysqlCapacityStage") or "normal"), "정상")
            + " · 70% 정리 시작 / 80% 경고 / 90% 비필수 쓰기 제한 / 95% 긴급 정리 / 100% 핵심 이력만 기록",
            "• 로그: " + str(values.get("logSizeMb") or 0) + "MB / 한도 " + str(values.get("logLimitMb") or 0) + "MB",
            "• 처리 모드: " + str(values.get("cleanupMode") or "normal"),
            "• 핵심 이력 보호: 투자 결정, 판단 후 결과, 현재 가설 상태와 압축 전환 이력은 자동 삭제 대상에서 제외됩니다.",
            "• 조치: " + str(values.get("suggestedAction") or "저장공간 상태를 확인하세요."),
            "• 확인시각: " + str(values.get("checkedAt") or event.occurred_at),
        ]
        if recovered:
            lines.insert(2, "• 해소: " + str(values.get("recoveredFromState") or previous) + " 상태에서 운영 알림 기준을 벗어났습니다.")
        if values.get("forecastDetected"):
            eta = values.get("forecastEtaMinutes")
            eta_text = "계산 중" if eta is None else "약 " + str(eta) + "분 후"
            lines.insert(
                3,
                "• 소진 예측: 최근 " + str(values.get("forecastElapsedMinutes") or 0)
                + "분 흐름에서 " + eta_text + " "
                + str(values.get("forecastThresholdMb") or 0) + "MB 이하 예상",
            )
        if kind == "runtime-write-failure":
            lines.insert(3, "• 감지: 실제 저장소 쓰기 실패(ENOSPC 계열)를 감지해 일반 알림 쿨다운과 별도로 발송했습니다.")
        elif kind == "typedb-auto-rotation":
            lines.insert(3, "• 감지: TypeDB가 안전 재구축 기준에 도달해 MySQL 원천 데이터로 그래프를 다시 만드는 작업을 시작했습니다.")
        elif kind == "typedb-auto-rotation-failed":
            lines.insert(
                3,
                "• 감지: TypeDB 안전 재구축 후보 검증에 실패했습니다. 활성 저장소는 보존됐으며 5·15·30·60분 백오프로 재시도합니다.",
            )
        if values.get("maintenanceState"):
            lines.insert(
                3,
                "• 유지보수: " + str(values.get("maintenanceState"))
                + " · 용량 상태 " + str(values.get("capacityState") or state)
                + " · " + str(values.get("maintenanceReason") or ""),
            )
        try:
            shared_linked_mb = float(values.get("typedbSharedLinkedMb") or 0)
        except (TypeError, ValueError):
            shared_linked_mb = 0.0
        if shared_linked_mb > 0:
            lines.insert(
                4,
                "• 계산 기준: checkpoint 하드링크 참조 " + str(round(shared_linked_mb, 1))
                + "MB는 실제 디스크 사용량에 중복 합산하지 않았습니다.",
            )
        if components:
            labels = {
                "sharedDisk": "공용 디스크",
                "typedb": "TypeDB",
                "mysql": "MySQL",
                "logs": "로그",
            }
            names = ", ".join(
                labels.get(str(item.get("component") or ""), str(item.get("component") or ""))
                for item in components
            )
            if names:
                lines.insert(3, "• 제한 대상: " + names)
        text = "\n".join(lines)
        signal = "recovered" if recovered else (
            "capacityRotation" if kind == "typedb-auto-rotation" else
            "capacityRotationFailed" if kind == "typedb-auto-rotation-failed" else
            "capacityForecast" if kind in {"forecast", "forecast-eta-worsened"} else "capacityLimited"
        )
        return {
            "messageType": OPERATIONAL_STORAGE_CAPACITY,
            "accountId": "",
            "accountLabel": "운영",
            "deliveryAudience": "operations",
            "deliveryChannel": "operationsTelegram",
            "displayTarget": "운영 저장공간",
            "title": title,
            "rawTitle": title,
            "body": text,
            "readableMessage": text,
            "telegramMessage": text,
            "rawLines": text,
            "storageCapacityHealth": values,
            "eventGeneratedAt": event.occurred_at,
            "notificationSignals": ["operations", "storageCapacity", signal],
            "criteria": ["공용 디스크 여유", "TypeDB 저장·WAL·checkpoint 크기", "MySQL·로그 저장공간 한도"],
        }

    @staticmethod
    def _mysql_metadata_line(values: Mapping[str, object]) -> str:
        status = str(values.get("mysqlMetadataStatus") or "unavailable")
        if status != "available":
            reason = str(values.get("mysqlMetadataReason") or "일시적으로 조회하지 못함")[:180]
            return "• MySQL 내부 사용: 메타데이터 조회 실패 · 사유 " + reason
        return (
            "• MySQL 내부 사용: 데이터·인덱스 " + str(values.get("mysqlLiveDataMb") or 0)
            + "MB · 회수 가능 " + str(values.get("mysqlReclaimableMb") or 0)
            + "MB · 메타데이터 조회 정상"
        )
