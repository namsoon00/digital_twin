"""Web notification testing boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.service_factory import build_notification_queue_runner
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.toss_snapshots import build_snapshot
from digital_twin.infrastructure.web.adapters.notification_storage import notification_queue_store
from digital_twin.infrastructure.web.adapters.notification_storage import notification_store
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.modules.market_data.domain.monitoring import RealtimeMonitor
from digital_twin.modules.notifications.domain.event_types import NOTIFICATION_JOB_QUEUED
from digital_twin.modules.notifications.domain.event_types import NOTIFICATION_TEST_REQUESTED
from digital_twin.modules.notifications.domain.message_types import INVESTMENT_INSIGHT
from digital_twin.modules.notifications.domain.notification_delivery_explanation import build_customer_delivery_explanation
from digital_twin.modules.notifications.domain.notification_templates import alert_context
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.portfolio.domain.portfolio import utc_now_iso
from typing import Dict


def alert_event_public_payload(event) -> Dict[str, object]:
    context = alert_context(event)
    return {
        "accountId": event.account_id,
        "accountLabel": event.account_label,
        "messageType": event.rule,
        "rule": event.rule,
        "severity": event.severity,
        "symbol": event.symbol,
        "rawSymbol": context.get("rawSymbol") or event.symbol,
        "symbolName": context.get("symbolDisplayName") or "",
        "title": event.title,
        "lines": list(event.lines or []),
        "key": event.key,
    }


def selected_notification_test_account(payload: Dict[str, object]):
    requested = configured(payload.get("accountId") or payload.get("account_id"))
    accounts = stores.account_reader().load()
    if requested:
        for account in accounts:
            if account.account_id == requested:
                return account
        raise ValueError("요청한 계정을 찾지 못했습니다.")
    if not accounts:
        raise ValueError("테스트 발송에 사용할 계정이 없습니다.")
    return accounts[0]


def attach_notification_test_ontology_projection(snapshot, settings: Dict[str, str]) -> None:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
    existing_projection = ontology.get("projection") or ontology.get("typedb")
    if isinstance(existing_projection, dict) and isinstance(existing_projection.get("inferenceBox"), dict):
        return
    try:
        recorder = PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(settings),
            quality_store=stores.ontology_quality_sample_store(settings),
            projection_run_store=stores.ontology_projection_run_store(settings),
            decision_episode_store=stores.investment_decision_episode_store(settings),
            hypothesis_proposal_store=stores.investment_research_store(settings),
            settings=settings,
            source="notification-test",
        )
        recorder.record_snapshot(snapshot)
    except Exception as error:  # noqa: BLE001 - test dispatch should report TypeDB readiness instead of crashing.
        snapshot.metadata.setdefault("ontology", {})["projection"] = {
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reason": "notification test TypeDB projection failed: " + str(error)[:160],
        }


def notification_test_event(message_type: str, snapshot):
    settings = runtime_settings()
    attach_notification_test_ontology_projection(snapshot, settings)
    monitor = RealtimeMonitor(settings)
    events = monitor.type_check_events_for_snapshot(snapshot)
    for event in events:
        if event.rule == message_type:
            return event
    for event in monitor.events_for_snapshot(snapshot, {}):
        if event.rule == message_type:
            return event
    return None


def notification_template_test_payload(payload: Dict[str, object]):
    message_type = configured(payload.get("messageType") or payload.get("message_type"))
    if not message_type:
        raise ValueError("messageType은 필요합니다.")
    dry_run = request_bool(payload.get("dryRun", payload.get("dry_run")))
    bypass_policy = (
        request_bool(payload.get("bypassPolicy", payload.get("bypass_policy")))
        or request_bool(payload.get("directSend", payload.get("direct_send")))
    )
    account = selected_notification_test_account(payload)
    snapshot = build_snapshot(account)
    if snapshot.mode != "live" and not payload.get("allowDemo"):
        return 409, {
            "delivered": False,
            "messageType": message_type,
            "error": "실제 토스 데이터를 가져오지 못했습니다: " + (snapshot.status or snapshot.mode),
            "snapshot": {
                "accountId": snapshot.account_id,
                "accountLabel": snapshot.account_label,
                "mode": snapshot.mode,
                "status": snapshot.status,
                "generatedAt": snapshot.generated_at,
            },
        }
    if message_type == "investmentInsight":
        missing_event = notification_test_event("ontologyInferenceMissing", snapshot)
        if missing_event:
            return 409, {
                "delivered": False,
                "messageType": message_type,
                "blockedBy": "ontologyInferenceMissing",
                "error": "온톨로지 추론 결과가 없어 투자 판단 테스트 발송을 막았습니다.",
                "event": alert_event_public_payload(missing_event),
            }
    event = notification_test_event(message_type, snapshot)
    if not event:
        return 422, {
            "delivered": False,
            "messageType": message_type,
            "error": "현재 데이터로 만들 수 있는 알림 이벤트가 없습니다.",
        }
    context = alert_context(event)
    context.update({
        "testDispatch": True,
        "notificationTestBypassPolicy": bypass_policy,
        "messageType": event.rule or message_type,
    })
    public_event = alert_event_public_payload(event)
    source_event = new_domain_event(
        NOTIFICATION_TEST_REQUESTED,
        event.key or message_type,
        {"messageType": message_type, "accountId": account.account_id, "accountLabel": account.label, "event": public_event},
    )
    message = notification_store().render(event.rule, context)
    job = NotificationJob.create(
        message,
        account_id=account.account_id,
        account_label=account.label,
        message_type=event.rule or message_type,
        source_event_id=source_event.event_id,
        source_event_name=source_event.name,
        context=context,
    )
    synchronous_test = bool(dry_run or bypass_policy)
    runner = build_notification_queue_runner(dry_run=synchronous_test)
    runner.apply_account_delivery_context(job, account)
    if synchronous_test:
        if str(job.message_type or "") == INVESTMENT_INSIGHT:
            explanation = build_customer_delivery_explanation(
                message_type=job.message_type,
                source_event_name=job.source_event_name,
                source_event_id=job.source_event_id,
                context=job.context,
            )
            validation = explanation.get("validation") if isinstance(explanation.get("validation"), dict) else {}
            if validation.get("state") != "valid":
                return 422, {
                    "delivered": False,
                    "messageType": message_type,
                    "error": "검증 알림의 발송 사유 계약을 만들지 못했습니다.",
                    "validation": validation,
                    "event": public_event,
                }
            test_context = dict(job.context or {})
            test_context.update({
                "customerDeliveryExplanation": explanation,
                "customerDeliveryExplanationRequired": True,
                "customerDeliveryExplanationValidationState": "valid",
            })
            job.context = test_context
        rendered_message = runner.render(job)
        if rendered_message:
            job.text = rendered_message
    if dry_run:
        return 200, {
            "delivered": False,
            "dryRun": True,
            "messageType": message_type,
            "direct": bypass_policy,
            "message": job.text,
            "event": alert_event_public_payload(event),
        }
    if bypass_policy:
        store = notification_queue_store()
        job.status = "processing"
        job.attempts = 1
        job.updated_at = utc_now_iso()
        store.upsert_job(job)
        try:
            runner.deliver(job, {account.account_id: account}, job.text)
            operator_detail = runner.capture_operator_report_after_delivery(job, job.text)
            store.mark_done(job)
            return 200, {
                "delivered": True,
                "queued": False,
                "direct": True,
                "bypassPolicy": True,
                "jobId": job.job_id,
                "provider": "Notification Direct Test",
                "messageType": message_type,
                "operatorReportStatus": job.context.get("operatorReasoningReportStatus"),
                "operatorReportJobId": job.context.get("operatorReasoningReportJobId"),
                "operatorReportDetail": operator_detail,
                "event": public_event,
            }
        except Exception as error:  # noqa: BLE001 - expose direct test failures to the UI.
            store.mark_failed(job, str(error))
            return 502, {
                "delivered": False,
                "queued": False,
                "direct": True,
                "bypassPolicy": True,
                "jobId": job.job_id,
                "provider": "Notification Direct Test",
                "messageType": message_type,
                "event": public_event,
                "error": str(error),
            }
    if not notification_queue_store().enqueue(job):
        if job.status == "suppressed":
            return 202, {
                "delivered": False,
                "queued": False,
                "suppressed": True,
                "provider": "Notification Queue",
                "messageType": message_type,
                "event": public_event,
                "deliveryDecision": (job.context or {}).get("deliveryDecision"),
                "deliveryGateState": (job.context or {}).get("deliveryGateState"),
                "reasons": (job.context or {}).get("deliveryReasons") or [],
                "error": job.last_error,
            }
        return 409, {
            "delivered": False,
            "queued": False,
            "provider": "Notification Queue",
            "messageType": message_type,
            "event": public_event,
            "error": "알림 작업을 큐에 적재하지 못했습니다.",
        }
    new_domain_event(
        NOTIFICATION_JOB_QUEUED,
        job.job_id,
        {
            "jobId": job.job_id,
            "messageType": job.message_type,
            "accountId": job.account_id,
            "sourceEventId": source_event.event_id,
        },
    )
    return 202, {
        "delivered": False,
        "queued": True,
        "jobId": job.job_id,
        "provider": "Notification Queue",
        "messageType": message_type,
        "event": public_event,
    }
