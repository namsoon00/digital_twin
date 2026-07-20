from typing import Dict

from ..domain.data_pipeline_health import evaluate_market_data_collection_health, evaluate_news_collection_health
from ..domain.events import DATA_PIPELINE_HEALTH_CHANGED, DomainEvent, data_pipeline_health_changed_event
from ..domain.message_types import EXTERNAL_DATA_CONNECTION
from ..domain.notifications import NotificationJob


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


class DataPipelineHealthService:
    def __init__(self, store=None, settings: Dict[str, object] = None):
        self.store = store
        self.settings = dict(settings or {})

    def record_news_collection(self, result: Dict[str, object]):
        previous = self.pipeline_state("newsCollection")
        health = evaluate_news_collection_health(
            result,
            previous,
            blocked_warning_streak=int_setting(
                self.settings,
                "newsCollectionQualityBlockedWarningStreak",
                3,
                1,
                120,
            ),
            stale_after_minutes=int_setting(
                self.settings,
                "newsCollectionCoverageStaleMinutes",
                180,
                5,
                10080,
            ),
        )
        self.save(health.to_dict())
        event = data_pipeline_health_changed_event(health.to_dict()) if health.state_changed else None
        return health, event

    def record_market_data_collection(self, result: Dict[str, object]):
        previous = self.pipeline_state("marketSnapshot")
        health = evaluate_market_data_collection_health(result, previous)
        self.save(health.to_dict())
        event = data_pipeline_health_changed_event(health.to_dict()) if health.state_changed else None
        return health, event

    def pipeline_state(self, pipeline: str) -> Dict[str, object]:
        if not self.store or not hasattr(self.store, "load"):
            return {}
        try:
            payload = self.store.load()
        except Exception:  # noqa: BLE001 - health tracking must not stop collection.
            return {}
        pipelines = payload.get("pipelines") if isinstance(payload, dict) else {}
        return dict(pipelines.get(pipeline) or {}) if isinstance(pipelines, dict) else {}

    def save(self, health: Dict[str, object]) -> None:
        if not self.store or not hasattr(self.store, "replace"):
            return
        try:
            payload = self.store.load() if hasattr(self.store, "load") else {}
            payload = dict(payload or {})
            pipelines = dict(payload.get("pipelines") or {})
            pipelines[str(health.get("pipeline") or "unknown")] = dict(health)
            payload["pipelines"] = pipelines
            payload["updatedAt"] = health.get("checkedAt")
            self.store.replace(payload)
        except Exception:  # noqa: BLE001 - health persistence is observational.
            return


class DataPipelineHealthNotificationEnqueuer:
    def __init__(self, account_repository, queue, settings: Dict[str, object] = None):
        self.account_repository = account_repository
        self.queue = queue
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        value = str(self.settings.get("dataPipelineHealthNotificationsEnabled", "1")).strip().lower()
        return value not in DISABLED_VALUES

    def handle(self, event: DomainEvent) -> None:
        if event.name != DATA_PIPELINE_HEALTH_CHANGED or not self.enabled():
            return
        payload = dict(event.payload or {})
        if not payload.get("alertRequired"):
            return
        accounts = self.account_repository.load() if self.account_repository else []
        for account in accounts or []:
            if not getattr(account, "enabled", False):
                continue
            context = self.context(payload, event, account)
            job = NotificationJob.create(
                context["readableMessage"],
                account_id=str(getattr(account, "account_id", "") or ""),
                account_label=str(getattr(account, "label", "") or ""),
                message_type=EXTERNAL_DATA_CONNECTION,
                source_event_id=event.event_id,
                source_event_name=event.name,
                dedupe_key="pipeline-health:" + str(payload.get("pipeline") or "unknown") + ":" + event.event_id,
                context=context,
            )
            self.queue.enqueue(job)

    def context(self, payload: Dict[str, object], event: DomainEvent, account) -> Dict[str, object]:
        pipeline = str(payload.get("pipeline") or "데이터 파이프라인")
        state = str(payload.get("state") or "unknown")
        recovered = state in {"healthy", "idle"} and str(payload.get("previousState") or "") in {"degraded", "failed", "stale"}
        labels = {
            "newsCollection": "뉴스 수집",
            "marketSnapshot": "시장 데이터 수집",
        }
        display_name = labels.get(pipeline, pipeline)
        title = display_name + (" 정상화" if recovered else " 품질 점검 필요")
        provider_failures = int(payload.get("providerFailureCount") or 0)
        text = "\n".join([
            "[운영] " + title,
            "• 파이프라인: " + pipeline,
            "• 상태: " + state + " (이전 " + str(payload.get("previousState") or "없음") + ")",
            "• 이유: " + str(payload.get("reason") or ""),
            "• 연속 0건: " + str(payload.get("consecutiveZeroRuns") or 0) + "회",
            "• 공급자 실패: " + str(provider_failures) + "건",
            "• 확인시각: " + str(payload.get("checkedAt") or ""),
        ])
        return {
            "messageType": EXTERNAL_DATA_CONNECTION,
            "accountId": str(getattr(account, "account_id", "") or ""),
            "accountLabel": str(getattr(account, "label", "") or ""),
            "displayTarget": display_name,
            "title": title,
            "rawTitle": title,
            "readableMessage": text,
            "telegramMessage": text,
            "rawLines": text,
            "apiSource": pipeline,
            "apiStatus": state,
            "pipelineHealth": payload,
            "eventGeneratedAt": event.occurred_at,
            "notificationSignals": ["connectionIssue" if not recovered else "connectionRecovered", "actionable"],
            "criteria": ["공급자별 성공·실패", "수집 대상 대비 확보 건수", "원천 데이터 품질 통과 여부"],
        }
