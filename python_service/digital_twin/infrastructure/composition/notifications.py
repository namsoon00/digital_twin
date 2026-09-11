"""Notifications runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.notifications.public import NotificationQueueRunner


def build_notification_queue_runner(dry_run: bool = False, lane: str = "all") -> NotificationQueueRunner:
    from digital_twin.domain.market_data import number
    from digital_twin.domain.monitoring import RealtimeMonitor
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.decisions import build_investment_brain_service
    from digital_twin.infrastructure.composition.market_data import monitor_account_job_store_from_settings
    from digital_twin.infrastructure.composition.reasoning_health import build_ontology_reasoning_queue_probe
    from digital_twin.infrastructure.disclosure_analyzer import disclosure_analyzer_from_settings
    from digital_twin.infrastructure.settings import runtime_settings, utc_now
    from digital_twin.infrastructure.share_notification_links import ActiveShareNotificationLinkResolver
    from digital_twin.modules.decisions.public import (
        DecisionContinuityService,
        InvestmentAlertCoverageNotificationEnqueuer,
        InvestmentAlertCoverageService,
        NotificationAIDecisionContextEnricher,
        NotificationAIRequestEnqueuer,
    )
    from digital_twin.modules.news_intelligence.public import NewsDigestEnqueuer, NewsDigestEventReconciler
    from digital_twin.modules.notifications.infrastructure.notification.transport import (
        notifier_for_account,
        notifier_for_operations,
    )
    from digital_twin.modules.notifications.public import (
        CompositeNotificationContextEnricher,
        DisclosureAnalysisNotificationEnricher,
        NotificationAIOpinionEnricher,
        NotificationHoldingSnapshotEnricher,
        NotificationHypothesisResearchEnricher,
        NotificationInstrumentIdentityEnricher,
        NotificationQueueRunner,
    )
    from digital_twin.modules.reasoning.public import (
        InvestmentReasoningOrchestrator,
        OntologyReasoningQueueHealthService,
    )

    settings = runtime_settings()
    del lane  # Compatibility argument; dedicated AI workers replaced notification lanes.
    include_message_types = []
    exclude_message_types = []
    monitor_store = stores.monitor_store(settings)
    decision_episode_store = stores.investment_decision_episode_store(settings)
    investment_domain_store = stores.investment_domain_store(settings)
    continuity_service = DecisionContinuityService(decision_episode_store, investment_domain_store)
    investment_brain_service = build_investment_brain_service(settings)
    reasoning_queue_probe = build_ontology_reasoning_queue_probe(settings)
    queue_health_service = OntologyReasoningQueueHealthService(
        store=stores.ontology_reasoning_cursor_store(settings),
        settings=settings,
    )
    refresh_job_store = monitor_account_job_store_from_settings(settings) if not dry_run else None

    def request_fresh_data_recheck(account_id: str, symbol: str, source_job_id: str):
        if not refresh_job_store:
            return {
                "requested": True,
                "scheduledAt": utc_now(),
                "scheduleMode": "next-monitor-cycle",
                "maxDelaySeconds": max(30, int(settings.get("monitorAccountIntervalSeconds") or 30)),
                "pipeline": "monitor-snapshot-typedb-ai",
                "reason": "다음 실시간 모니터 주기에서 새 스냅샷부터 다시 판단합니다.",
            }
        result = dict(refresh_job_store.request_refresh(account_id, priority=5) or {})
        result.update({
            "symbol": str(symbol or "").upper(),
            "sourceJobId": str(source_job_id or ""),
        })
        return result

    def queue_health_at_dispatch():
        # Do not trust the event payload alone: an operations job can wait
        # behind outbound delivery work while the reasoning queue already
        # recovered. This deliberately avoids the full runner.status() path:
        # delivery must not contend with account-priority or TypeDB diagnostics
        # while a live projection is writing.
        return queue_health_service.observe(reasoning_queue_probe())

    identity_enricher = NotificationInstrumentIdentityEnricher(
        stores.symbol_universe_store(settings),
    )
    holding_enricher = NotificationHoldingSnapshotEnricher(
        monitor_store.load_previous,
        RealtimeMonitor(settings),
    )
    disclosure_enricher = DisclosureAnalysisNotificationEnricher(
        disclosure_analyzer_from_settings(settings),
        settings,
    )
    research_enricher = NotificationHypothesisResearchEnricher(
        investment_brain_service,
        settings,
    )
    opinion_enricher = NotificationAIOpinionEnricher(settings)
    ai_decision_context_enricher = NotificationAIDecisionContextEnricher(
        stores.market_time_series_store(settings),
        settings,
        investment_domain_store=investment_domain_store,
    )
    ai_request_enqueuer = None
    reasoning_orchestrator = None
    news_digest_reconciler = None
    alert_coverage_reconciler = None
    if not dry_run:
        reasoning_orchestrator = InvestmentReasoningOrchestrator(
            stores.investment_reasoning_case_store(settings),
            decision_episode_store=decision_episode_store,
            subject_case_repository=stores.subject_decision_case_store(settings),
        )
        ai_request_enqueuer = NotificationAIRequestEnqueuer(
            stores.ai_inference_queue_store(settings),
            CompositeNotificationContextEnricher(
                identity_enricher,
                holding_enricher,
                disclosure_enricher,
                research_enricher,
                opinion_enricher,
                ai_decision_context_enricher,
            ),
            settings,
            decision_episode_store=decision_episode_store,
            continuity_service=continuity_service,
            reasoning_orchestrator=reasoning_orchestrator,
        )
        news_digest_reconciler = NewsDigestEventReconciler(
            event_reader=stores.event_log(settings),
            enqueuer=NewsDigestEnqueuer(
                account_repository=stores.account_reader(settings),
                monitor_store=monitor_store,
                queue=stores.notification_job_store(settings),
                settings=settings,
                max_items=int(number(settings.get("newsDigestMaxItems")) or 3),
                evidence_repository=stores.research_evidence_store(settings),
            ),
            cursor_store=stores.news_digest_reconciliation_state_store(settings),
        )
        coverage_queue = stores.notification_job_store(settings)
        coverage_registry = stores.reasoning_engine_registry_store(settings)

        def coverage_deployment_id():
            control = coverage_registry.control()
            return str(
                control.delivery_deployment_id
                or control.active_deployment_id
                or ""
            )

        coverage_service = InvestmentAlertCoverageService(
            stores.investment_alert_coverage_store(settings),
            coverage_deployment_id,
            settings,
        )
        coverage_enqueuer = InvestmentAlertCoverageNotificationEnqueuer(
            coverage_queue
        )

        def reconcile_alert_coverage():
            payload, event = coverage_service.run_once()
            if event is not None:
                coverage_enqueuer.handle(event)
            return payload

        alert_coverage_reconciler = reconcile_alert_coverage

    return NotificationQueueRunner(
        queue=stores.notification_job_store(settings),
        account_repository=stores.account_reader(settings),
        notifier_factory=notifier_for_account,
        operations_notifier_factory=notifier_for_operations,
        dry_run=dry_run,
        send_gap_seconds=float(settings.get("notificationSendGapSeconds") or 0),
        stale_after_minutes=int(settings.get("notificationProcessingStaleMinutes") or 2),
        template_renderer=stores.notification_template_store(settings).render_job,
        context_enricher=CompositeNotificationContextEnricher(
            identity_enricher,
        ),
        operator_reports_enabled=str(settings.get("operatorReasoningReportEnabled", "1")).strip().lower() not in {"0", "false", "no", "off"},
        settings=settings,
        operational_state_resolver=queue_health_at_dispatch,
        operational_delivery_recorder=queue_health_service.record_notification_delivery,
        include_message_types=include_message_types,
        exclude_message_types=exclude_message_types,
        ai_request_enqueuer=ai_request_enqueuer,
        reasoning_orchestrator=reasoning_orchestrator,
        news_digest_reconciler=news_digest_reconciler,
        alert_coverage_reconciler=alert_coverage_reconciler,
        fresh_data_recheck_requester=request_fresh_data_recheck,
        link_base_resolver=ActiveShareNotificationLinkResolver(),
    )
