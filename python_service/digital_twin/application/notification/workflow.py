import time
from datetime import datetime
from typing import Callable, Dict, List
from zoneinfo import ZoneInfo

from ...domain.context_observation_notifications import (
    is_typedb_context_observation_notification,
    typedb_context_observation_contract,
)
from ...domain.disclosure_analysis import local_disclosure_analysis
from ...domain.investment_brain import decision_episode_from_context
from ...domain.investment_flow import INVESTMENT_FLOW_VERSION, investment_flow_id
from ...domain.message_types import (
    INVESTMENT_INSIGHT,
    OPERATOR_REASONING_REPORT,
)
from ...domain.monitoring import RealtimeMonitor
from ...domain.notification_ai import enrich_notification_ai_context
from ...domain.notification_ai_delivery import final_ai_delivery_decision
from ...domain.notification_delivery_explanation import build_customer_delivery_explanation
from ...domain.notification_ai_gate_contracts import NotificationAIValidatedResponse, ai_gate_enabled_for_message_type
from ...domain.notification_ai_gate_validation import local_validated_ai_response
from ...domain.notification_ai_decision_brief import (
    AI_DECISION_CONTRACT_VERSION,
    AI_DECISION_PROMPT_VERSION,
    notification_ai_execution_profile,
)
from ...domain.notifications import NotificationJob, notification_debug_number
from ...domain.notification_identity import context_with_instrument_identity, notification_instrument_symbol
from ...domain.notification_reasoning_report import build_notification_reasoning_report, render_operator_reasoning_report
from ..notification_ai_gate_audit import context_with_validated_ai_response
from ..notification_ai_judgement_service import (
    NotificationAIContractError,
    NotificationAIJudgementService,
)
from ..notification_decision_memory import context_with_previous_investment_decision
from ..notification_disclosure_rendering import context_with_disclosure_analysis
from .dispatch import NotificationDispatchService
from .eligibility import NotificationDispatchEligibilityService
from .rendering import NotificationRenderingService
from .quality import (
    apply_ontology_quality_gate_to_response,
    ontology_quality_candidates,
    ontology_quality_gate_context,
)


class CompositeNotificationContextEnricher:
    def __init__(self, *enrichers):
        self.enrichers = [enricher for enricher in enrichers if enricher]

    def __call__(self, job: NotificationJob) -> None:
        for enricher in self.enrichers:
            enricher(job)


class DisclosureAnalysisNotificationEnricher:
    def __init__(self, analyzer=None, settings: Dict[str, object] = None):
        self.analyzer = analyzer
        self.settings = settings or {}

    def __call__(self, job: NotificationJob) -> None:
        context = dict(job.context or {})
        digest = context.get("newsDigest") if isinstance(context.get("newsDigest"), dict) else {}
        is_disclosure_digest = str(job.message_type or "") == "newsDigest" and digest.get("eventKind") == "disclosure"
        if str(job.message_type or "") != "externalDartDisclosure" and not is_disclosure_digest:
            return
        if str(self.settings.get("dartDisclosureAiAnalysisEnabled", "1")).strip() == "0":
            return
        if context.get("disclosureAnalysis") or "AI 공시 해석" in str(context.get("telegramMessage") or ""):
            return
        try:
            result = self.analyzer.analyze(context) if self.analyzer else local_disclosure_analysis(context)
        except Exception:  # noqa: BLE001 - disclosure delivery must not fail because AI enrichment failed.
            result = local_disclosure_analysis(context, "로컬 fallback")
        job.context = context_with_disclosure_analysis(context, result)


class NotificationAIOpinionEnricher:
    def __init__(self, settings: Dict[str, object] = None):
        self.settings = settings or {}

    def __call__(self, job: NotificationJob) -> None:
        context = dict(job.context or {})
        if is_typedb_context_observation_notification(context):
            return
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        job.context = enrich_notification_ai_context(context, self.settings)


class NotificationInstrumentIdentityEnricher:
    def __init__(self, symbol_repository=None):
        self.symbol_repository = symbol_repository
        self.cache: Dict[str, Dict[str, object]] = {}

    def __call__(self, job: NotificationJob) -> None:
        context = dict(job.context or {})
        symbol = notification_instrument_symbol(context)
        if not symbol or not self.symbol_repository:
            return
        identity = self.cache.get(symbol)
        if identity is None:
            try:
                item = self.symbol_repository.get(symbol)
            except Exception:  # noqa: BLE001 - identity lookup must not block notification delivery.
                return
            if not item:
                return
            identity = item.to_dict() if hasattr(item, "to_dict") else dict(item or {})
            self.cache[symbol] = identity
        job.context = context_with_instrument_identity(context, identity)


class NotificationHoldingSnapshotEnricher:
    def __init__(self, snapshot_provider: Callable = None, monitor: RealtimeMonitor = None):
        self.snapshot_provider = snapshot_provider
        self.monitor = monitor or RealtimeMonitor()

    def __call__(self, job: NotificationJob) -> None:
        context = dict(job.context or {})
        symbol = self.symbol_from_context(context)
        if not symbol:
            return
        state, position = self.state_and_position_for_symbol(job.account_id, symbol)
        if not position:
            return
        raw_lines = self.raw_lines(context)
        next_lines = list(raw_lines)
        self.monitor.use_external_fx_rates(state.get("externalSignals") if isinstance(state, dict) else {})
        state_positions = state.get("positions") if isinstance(state, dict) else None
        for line in self.monitor.holding_price_lines(position, state.get("portfolio") if isinstance(state, dict) else None, state_positions):
            label = str(line or "").split(":", 1)[0].strip()
            if line and not self.has_labeled_line(next_lines, label):
                next_lines.append(line)
        if next_lines != raw_lines:
            context["rawLines"] = "\n".join(next_lines)
            job.context = context

    def snapshot_states(self) -> Dict[str, object]:
        if not self.snapshot_provider:
            return {}
        try:
            value = self.snapshot_provider()
        except Exception:  # noqa: BLE001 - notification delivery must continue if snapshot lookup fails.
            return {}
        return value if isinstance(value, dict) else {}

    def state_and_position_for_symbol(self, account_id: str, symbol: str):
        states = self.snapshot_states()
        candidates = []
        if account_id and isinstance(states.get(account_id), dict):
            candidates.append(states.get(account_id))
        candidates.extend(state for key, state in states.items() if key != account_id and isinstance(state, dict))
        for state in candidates:
            positions = state.get("positions") if isinstance(state, dict) else {}
            if isinstance(positions, dict):
                item = positions.get(symbol.upper())
                if isinstance(item, dict):
                    return state, item
        return {}, {}

    def raw_lines(self, context: Dict[str, object]) -> List[str]:
        raw = context.get("rawLines")
        if isinstance(raw, list):
            return [str(line or "").strip() for line in raw if str(line or "").strip()]
        return [line.strip() for line in str(raw or "").splitlines() if line.strip()]

    def has_labeled_line(self, lines: List[str], label: str) -> bool:
        prefix = str(label or "").strip() + ":"
        return any(str(line or "").strip().startswith(prefix) for line in lines)

    def symbol_from_context(self, context: Dict[str, object]) -> str:
        for key in ["rawSymbol", "symbol", "target", "rawTarget"]:
            value = str(context.get(key) or "").strip().upper()
            if self.is_symbol_like(value):
                return value
        for key in ["displayTarget", "target", "title"]:
            value = str(context.get(key) or "").strip()
            for token in reversed(value.replace("/", " ").replace("|", " ").split()):
                token = token.strip().upper()
                if self.is_symbol_like(token):
                    return token
        return ""

    def is_symbol_like(self, value: str) -> bool:
        text = str(value or "").strip().upper()
        return bool(text and len(text) <= 12 and all(ch.isalnum() or ch in {".", "-"} for ch in text))


class NotificationAIValidatedGateEnricher:
    def __init__(self, reviewer=None, settings: Dict[str, object] = None, decision_episode_store=None):
        self.reviewer = reviewer
        self.settings = settings or {}
        self.decision_episode_store = decision_episode_store
        self.judgement_service = (
            NotificationAIJudgementService(
                reviewer,
                self.settings,
                max_prompt_bytes=int(self.settings.get("notificationAiQueueMaxPromptBytes") or 24 * 1024),
                repair_reasoning_effort=str(
                    self.settings.get("notificationAiComparisonRepairReasoningEffort") or "low"
                ),
                repair_timeout_seconds=int(
                    self.settings.get("notificationAiComparisonRepairTimeoutSeconds") or 60
                ),
                enforce_contract_for_typed_response=False,
            )
            if reviewer
            else None
        )

    def __call__(self, job: NotificationJob) -> None:
        if not ai_gate_enabled_for_message_type(job.message_type, self.settings):
            return
        context = dict(job.context or {})
        if is_typedb_context_observation_notification(context):
            return
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        context.setdefault("jobId", job.job_id)
        narrative = context.get("notificationNarrativeBrief")
        publication = context.get("notificationNarrativePublication")
        has_canonical_publication = bool(
            isinstance(narrative, dict)
            and narrative.get("version")
            and isinstance(narrative.get("claims"), list)
            and isinstance(publication, dict)
            and publication.get("version") == "investment-narrative-publication-v1"
        )
        if has_canonical_publication and isinstance(
            context.get("notificationAiValidatedResponse"), dict
        ):
            # AI completion is the publication boundary. Revalidating at send
            # time changes the evidence ledger and can silently replace an
            # accepted AI narrative with a deterministic fallback.
            job.context = context
            return
        if job.message_type == INVESTMENT_INSIGHT:
            context = context_with_previous_investment_decision(
                context,
                self.decision_episode_store,
                account_id=job.account_id,
            )
        quality_gate = ontology_quality_gate_context(context, self.settings)
        context["ontologyQualityGate"] = quality_gate
        if context.get("notificationAiValidatedResponse"):
            # Legacy jobs predate the immutable narrative publication contract.
            response = NotificationAIValidatedResponse.from_dict(context.get("notificationAiValidatedResponse"))
            job.context = context_with_validated_ai_response(context, response, self.settings)
            return
        try:
            if self.judgement_service:
                context["notificationAiDecisionContractVersion"] = AI_DECISION_CONTRACT_VERSION
                profile = notification_ai_execution_profile(context, self.settings)
                context["notificationAiExecutionProfile"] = profile
                outcome = self.judgement_service.judge(context, profile=profile)
                if not outcome.publishable:
                    raise NotificationAIContractError(
                        outcome.final_contract_error
                        or outcome.final_publication_error
                        or outcome.repair_error
                    )
                response = outcome.response
                context["_notificationAiInferencePacket"] = outcome.packet.to_audit_dict()
                context["notificationAiExecutionAudit"] = {
                    "version": "notification-ai-execution-audit-v2",
                    "status": "completed",
                    "promptVersion": AI_DECISION_PROMPT_VERSION,
                    "inferencePacket": outcome.packet.to_audit_dict(),
                    "promptHash": outcome.executed_prompt_hash,
                    "promptBytes": outcome.executed_prompt_bytes,
                    "executionProfile": profile,
                    "claimPublication": {
                        "status": str((response.claim_validation or {}).get("status") or "unavailable"),
                        "verifiedClaimCount": response.verified_claim_count,
                        "rejectedClaimCount": response.rejected_claim_count,
                        "sections": sorted(response.verified_claim_sections),
                    },
                    "contractRepair": outcome.audit_dict().get("repair") or {},
                }
            else:
                response = local_validated_ai_response(context, source="TypeDB inference fallback")
        except Exception as error:  # noqa: BLE001 - notification delivery should degrade to local validation.
            response = local_validated_ai_response(context, source="TypeDB inference fallback")
            response.validation_warnings.append("AI 검증 실패로 TypeDB 해석을 사용했습니다: " + str(error)[:140])
        apply_ontology_quality_gate_to_response(response, quality_gate)
        if (
            self.decision_episode_store
            and job.message_type == INVESTMENT_INSIGHT
            and not context.get("investmentSubjectDecisionCaseId")
        ):
            try:
                relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
                subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
                facts = dict(relation_context.get("facts") or {})
                facts["inferenceGenerationId"] = relation_context.get("inferenceGenerationId") or ""
                self.decision_episode_store.record_observation(
                    job.account_id,
                    str(subject.get("symbol") or ""),
                    facts,
                    str(relation_context.get("inferenceGenerationAt") or context.get("referenceDate") or ""),
                )
                episode = decision_episode_from_context(context, response.to_dict(), job_id=job.job_id)
                if episode:
                    self.decision_episode_store.save(episode)
                    previous = (
                        context.get("previousInvestmentDecisionEpisode")
                        if isinstance(context.get("previousInvestmentDecisionEpisode"), dict)
                        else {}
                    )
                    previous_action = str(previous.get("action") or "").strip()
                    previous_validation = str(previous.get("validationState") or "").strip()
                    current_action = str(episode.action or "").strip()
                    current_validation = str(episode.validation_state or "").strip()
                    context["investmentDecisionEpisodeId"] = episode.episode_id
                    context["investmentDecisionEpisode"] = episode.to_dict()
                    context["investmentFlow"] = {
                        "version": INVESTMENT_FLOW_VERSION,
                        "flowId": investment_flow_id(episode.account_id, episode.symbol, episode.episode_id),
                        "episodeId": episode.episode_id,
                        "previousAction": previous_action,
                        "currentAction": current_action,
                        "decisionChanged": bool(previous_action and previous_action != current_action),
                        "previousValidationState": previous_validation,
                        "currentValidationState": current_validation,
                        "validationChanged": bool(previous_validation and previous_validation != current_validation),
                    }
            except Exception as error:  # noqa: BLE001 - memory persistence must not block a time-sensitive alert.
                response.validation_warnings.append("투자 판단 기억 저장 실패: " + str(error)[:140])
        job.context = context_with_validated_ai_response(context, response, self.settings)


class NotificationHypothesisResearchEnricher:
    def __init__(self, investment_brain_service=None, settings: Dict[str, object] = None):
        self.investment_brain_service = investment_brain_service
        self.settings = dict(settings or {})

    def __call__(self, job: NotificationJob) -> None:
        if job.message_type != INVESTMENT_INSIGHT or not self.enabled():
            return
        context = dict(job.context or {})
        if is_typedb_context_observation_notification(context):
            return
        if context.get("researchCycle"):
            return
        if not self.investment_brain_service:
            return
        try:
            enricher = (
                self.investment_brain_service.enqueue_notification_research_context
                if hasattr(self.investment_brain_service, "enqueue_notification_research_context")
                else self.investment_brain_service.enrich_notification_context
            )
            job.context = enricher(
                context,
                account_id=job.account_id,
                event_id=job.job_id,
            )
        except Exception as error:  # noqa: BLE001 - research enrichment must not block a time-sensitive alert.
            context["researchCycle"] = {
                "status": "error",
                "reason": "가설 조사 실패로 기존 TypeDB 추론 세대를 사용합니다: " + str(error)[:140],
            }
            job.context = context

    def enabled(self) -> bool:
        value = str(self.settings.get("investmentBrainNotificationResearchEnabled", "1")).strip().lower()
        return value not in {"0", "false", "no", "off", "disabled"}


class NotificationQueueRunner:
    def __init__(
        self,
        queue,
        account_repository,
        notifier_factory: Callable,
        operations_notifier_factory: Callable = None,
        dry_run: bool = False,
        send_gap_seconds: float = 0.0,
        stale_after_minutes: int = 2,
        template_renderer: Callable = None,
        context_enricher: Callable = None,
        now_provider: Callable = None,
        operator_reports_enabled: bool = False,
        settings: Dict[str, object] = None,
        operational_state_resolver: Callable = None,
        operational_delivery_recorder: Callable = None,
        include_message_types: List[str] = None,
        exclude_message_types: List[str] = None,
        ai_request_enqueuer=None,
        reasoning_orchestrator=None,
        news_digest_reconciler=None,
        fresh_data_recheck_requester=None,
        link_base_resolver: Callable = None,
    ):
        self.queue = queue
        self.account_repository = account_repository
        self.notifier_factory = notifier_factory
        self.operations_notifier_factory = operations_notifier_factory
        self.dry_run = dry_run
        self.send_gap_seconds = max(0.0, float(send_gap_seconds or 0))
        self.stale_after_minutes = max(1, int(stale_after_minutes or 2))
        self.template_renderer = template_renderer
        self.context_enricher = context_enricher
        self.now_provider = now_provider or (lambda: datetime.now(ZoneInfo("UTC")))
        self.operator_reports_enabled = bool(operator_reports_enabled)
        self.dispatch_freshness_enabled = settings is not None
        self.settings = dict(settings or {})
        self.operational_state_resolver = operational_state_resolver
        self.operational_delivery_recorder = operational_delivery_recorder
        self.include_message_types = tuple(dict.fromkeys(str(item).strip() for item in include_message_types or [] if str(item).strip()))
        self.exclude_message_types = tuple(dict.fromkeys(str(item).strip() for item in exclude_message_types or [] if str(item).strip()))
        self.ai_request_enqueuer = ai_request_enqueuer
        self.reasoning_orchestrator = reasoning_orchestrator
        self.news_digest_reconciler = news_digest_reconciler
        self.fresh_data_recheck_requester = fresh_data_recheck_requester
        self.rendering_service = NotificationRenderingService(
            template_renderer=template_renderer,
            context_enricher=context_enricher,
            now_provider=self.now_provider,
            link_base_resolver=link_base_resolver,
        )
        self.dispatch_service = NotificationDispatchService(
            queue=queue,
            notifier_factory=notifier_factory,
            operations_notifier_factory=operations_notifier_factory,
        )
        self.eligibility_service = NotificationDispatchEligibilityService(
            queue=queue,
            settings=self.settings,
            now_provider=self.now_provider,
            operational_state_resolver=operational_state_resolver,
            operational_delivery_recorder=operational_delivery_recorder,
            fresh_data_recheck_requester=fresh_data_recheck_requester,
            ai_defer_predicate=self.should_defer_ai_inference,
            outcome_recorder=self.record_eligibility_outcome,
            freshness_enabled=self.dispatch_freshness_enabled,
        )
        self.last_news_digest_reconciliation: Dict[str, object] = {}
        self.last_run_details = []
        self.active_job = None
        self.active_job_stage = ""
        self.claimed_jobs = []
        self.active_job_index = -1

    def account_map(self) -> Dict[str, object]:
        return {account.account_id: account for account in self.account_repository.load_all()}

    def run_once(self, limit: int = 10) -> int:
        self.last_run_details = []
        self.active_job = None
        self.active_job_stage = ""
        self.claimed_jobs = []
        self.active_job_index = -1
        try:
            return self._run_once(limit)
        except Exception as error:
            self.recover_active_job(error)
            raise
        finally:
            self.active_job = None
            self.active_job_stage = ""
            self.claimed_jobs = []
            self.active_job_index = -1

    def _run_once(self, limit: int = 10) -> int:
        if self.news_digest_reconciler:
            self.last_news_digest_reconciliation = dict(self.news_digest_reconciler.run_once() or {})
        # Load account configuration before claiming durable jobs. A storage
        # timeout here must not leave an unowned processing lease behind.
        accounts = self.account_map()
        use_claim = (not self.dry_run) and hasattr(self.queue, "claim_pending")
        if use_claim:
            try:
                jobs = self.queue.claim_pending(
                    limit=limit,
                    stale_after_minutes=self.stale_after_minutes,
                    include_message_types=self.include_message_types,
                    exclude_message_types=self.exclude_message_types,
                )
            except TypeError:
                jobs = self.queue.claim_pending(limit=limit, stale_after_minutes=self.stale_after_minutes)
        else:
            scan_limit = max(int(limit or 10), 100) if (self.include_message_types or self.exclude_message_types) else limit
            jobs = self.queue.pending(limit=scan_limit)
            jobs = [job for job in jobs if self.message_type_allowed(job.message_type)][: int(limit or 10)]
        if not jobs:
            return 0
        self.claimed_jobs = list(jobs) if use_claim else []
        processed = 0
        for index, job in enumerate(jobs):
            self.active_job = job
            self.active_job_index = index
            self.active_job_stage = "claimed"
            if str(job.message_type or "") == OPERATOR_REASONING_REPORT and not self.operator_reports_enabled:
                reason = "운영자 추론 보고서 알림이 비활성화되어 발송하지 않았습니다."
                if hasattr(self.queue, "mark_suppressed"):
                    self.queue.mark_suppressed(job, reason)
                else:
                    self.queue.mark_failed(job, reason)
                self.record_operational_delivery(job, "suppressed", reason)
                self.last_run_details.append(self.job_detail(job, "suppressed", "operator reports disabled"))
                processed += 1
                continue
            if not job.text.strip():
                reason = "empty notification text"
                self.queue.mark_failed(job, reason)
                self.record_operational_delivery(job, "failed", reason)
                self.last_run_details.append(self.job_detail(job, "failed", "empty text"))
                continue
            account = accounts.get(job.account_id)
            self.apply_account_delivery_context(job, account)
            ai_decision_pending = self.should_defer_ai_inference(job)
            if (
                not self.dry_run
                and account
                and account.quiet_hours_active(self.now_provider(), job.message_type)
                and not ai_decision_pending
            ):
                self.mark_quiet_hours_suppressed(job, account)
                self.mark_reasoning_case_suppressed(job, "account quiet hours")
                self.record_operational_delivery(job, "suppressed", "quiet hours")
                self.last_run_details.append(self.job_detail(job, "suppressed", "quiet hours"))
                processed += 1
                continue
            if not self.dry_run and not use_claim:
                self.queue.mark_processing(job)
            if not self.apply_operational_state_gate(job, "AI 판단 전"):
                processed += 1
                continue
            if not self.apply_inference_change_gate(job):
                processed += 1
                continue
            if not self.apply_market_hours_gate(job, "AI 판단 전"):
                processed += 1
                continue
            if not self.apply_dispatch_freshness_gate(job, "AI 판단 전"):
                processed += 1
                continue
            if not self.apply_ai_freshness_headroom_gate(job):
                processed += 1
                continue
            if self.should_defer_ai_inference(job):
                self.active_job_stage = "ai-queue-handoff"
                try:
                    outcome = self.ai_request_enqueuer.enqueue(job)
                    status = str((outcome or {}).get("status") or "awaiting-ai")
                    self.record_lifecycle(job, "awaiting_decision", status)
                    self.last_run_details.append(self.job_detail(job, status))
                except Exception as error:  # noqa: BLE001 - the source notification remains retryable.
                    self.queue.mark_failed(job, "AI 추론 큐 등록 실패: " + str(error))
                    self.last_run_details.append(self.job_detail(job, "failed", str(error)[:160]))
                processed += 1
                continue
            self.active_job_stage = "final-ai-gate"
            if not self.apply_final_ai_delivery_gate(job):
                processed += 1
                continue
            if not self.apply_deferred_admission_delivery_gate(job):
                processed += 1
                continue
            if not self.apply_operational_state_gate(job, "발송 직전"):
                processed += 1
                continue
            if not self.apply_dispatch_freshness_gate(job, "발송 직전"):
                processed += 1
                continue
            if not self.apply_market_hours_gate(job, "발송 직전"):
                processed += 1
                continue
            if not self.apply_customer_delivery_explanation_gate(job):
                processed += 1
                continue
            self.record_lifecycle(job, "ready_to_render", "ready")
            self.active_job_stage = "rendering"
            message = self.render(job)
            if not message:
                reason = "empty rendered notification text"
                self.queue.mark_failed(job, reason)
                self.record_operational_delivery(job, "failed", reason)
                self.last_run_details.append(self.job_detail(job, "failed", "empty rendered text"))
                continue
            if self.dry_run:
                print(message)
                self.last_run_details.append(self.job_detail(job, "dry-run"))
                processed += 1
                continue
            self.active_job_stage = "delivering"
            try:
                self.deliver(job, accounts, message)
            except Exception as error:  # noqa: BLE001 - one failed delivery must not stop the queue.
                self.queue.mark_failed(job, str(error))
                self.record_operational_delivery(job, "failed", str(error))
                self.last_run_details.append(self.job_detail(job, "failed", str(error)[:120]))
            else:
                self.active_job_stage = "delivered"
                operator_detail = self.capture_operator_report_after_delivery(job, message)
                self.queue.mark_done(job)
                self.mark_reasoning_case_published(job)
                self.active_job_stage = "done"
                self.record_operational_delivery(job, "done")
                self.last_run_details.append(self.job_detail(job, "done", operator_detail))
                processed += 1
            if self.send_gap_seconds and processed < len(jobs):
                time.sleep(self.send_gap_seconds)
        return processed

    def recover_active_job(self, error: Exception) -> None:
        """Release a claimed job immediately after an unexpected cycle error."""

        job = self.active_job
        if job is None or self.dry_run:
            return
        reason = "알림 처리 중 예외(" + (self.active_job_stage or "unknown") + "): " + str(error)
        jobs = [job]
        if self.claimed_jobs and self.active_job_index >= 0:
            jobs = self.claimed_jobs[self.active_job_index :]
        for index, affected_job in enumerate(jobs):
            affected_reason = reason if index == 0 else "앞선 알림 처리 예외로 claim을 즉시 회수했습니다."
            try:
                if index == 0 and self.active_job_stage == "delivered":
                    # Telegram already accepted the message. Persist completion
                    # so a database retry cannot send the customer alert twice.
                    self.queue.mark_done(affected_job)
                    self.mark_reasoning_case_published(affected_job)
                    status = "done-after-storage-recovery"
                else:
                    self.queue.mark_failed(affected_job, affected_reason)
                    status = "failed-retryable"
                self.last_run_details.append(
                    self.job_detail(affected_job, status, affected_reason[:160])
                )
            except Exception as recovery_error:  # noqa: BLE001 - scheduler still reports the original failure.
                self.last_run_details.append(
                    self.job_detail(affected_job, "processing-recovery-failed", str(recovery_error)[:160])
                )

    def mark_reasoning_case_published(self, job: NotificationJob) -> None:
        if self.reasoning_orchestrator is None:
            return
        try:
            self.reasoning_orchestrator.notification_published(dict(job.context or {}))
        except Exception:  # noqa: BLE001 - delivery completion remains authoritative.
            return

    def mark_reasoning_case_suppressed(self, job: NotificationJob, reason: str) -> None:
        if self.reasoning_orchestrator is None:
            return
        try:
            self.reasoning_orchestrator.notification_suppressed(
                dict(job.context or {}),
                str(reason or "notification suppressed"),
            )
        except Exception:  # noqa: BLE001 - queue disposition remains authoritative.
            return

    def should_defer_ai_inference(self, job: NotificationJob) -> bool:
        if self.dry_run or self.ai_request_enqueuer is None:
            return False
        observation = typedb_context_observation_contract(job.context or {})
        if observation and not bool(observation.get("requiresAiNarrative")):
            return False
        if not ai_gate_enabled_for_message_type(job.message_type, self.settings):
            return False
        return not bool((job.context or {}).get("notificationAiValidatedResponse"))

    def apply_final_ai_delivery_gate(self, job: NotificationJob) -> bool:
        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return True
        context = dict(job.context or {})
        if is_typedb_context_observation_notification(context):
            return True
        decision = final_ai_delivery_decision(context)
        context["finalAiDeliveryGate"] = decision
        job.context = context
        if decision.get("decision") != "suppress":
            return True
        reason = str(decision.get("reason") or "최종 AI 판단이 유지되어 푸시하지 않습니다.")
        context["deliverySuppressionReason"] = str(
            decision.get("suppressionReason") or "final_ai_action_unchanged"
        )
        context["deliverySuppressionDetail"] = reason
        job.context = context
        if hasattr(self.queue, "mark_suppressed"):
            self.queue.mark_suppressed(job, reason)
        else:
            self.queue.mark_failed(job, reason)
        self.record_operational_delivery(job, "suppressed", reason)
        self.mark_reasoning_case_suppressed(job, reason)
        self.last_run_details.append(self.job_detail(job, "suppressed", "final AI action unchanged"))
        return False

    def apply_deferred_admission_delivery_gate(self, job: NotificationJob) -> bool:
        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return True
        context = dict(job.context or {})
        deferred = context.get("preDecisionDeliveryGate")
        deferred = deferred if isinstance(deferred, dict) else {}
        if str(deferred.get("status") or "") != "deferred":
            return True
        reason_code = str(deferred.get("reasonCode") or "")
        reason_codes = {
            str(item or "")
            for item in deferred.get("reasonCodes") or [reason_code]
            if str(item or "")
        }
        repeat_reason_code = next(
            (item for item in ("state_cooldown", "similar_repeat") if item in reason_codes),
            "",
        )
        if repeat_reason_code:
            reason_details = deferred.get("reasonDetails")
            reason_details = reason_details if isinstance(reason_details, dict) else {}
            reason = str(
                reason_details.get(repeat_reason_code)
                or deferred.get("reason")
                or "반복·쿨다운 발송 정책에 따라 이번 메시지를 보내지 않습니다."
            )
            context["postDecisionDeliveryGate"] = {
                "version": "post-decision-delivery-gate-v1",
                "status": "suppressed-after-decision",
                "reasonCode": repeat_reason_code,
                "reasonCodes": sorted(reason_codes),
                "reason": reason,
            }
            context["deliverySuppressionReason"] = repeat_reason_code
            context["deliverySuppressionDetail"] = reason
            job.context = context
            if hasattr(self.queue, "mark_suppressed"):
                self.queue.mark_suppressed(job, reason)
            else:
                self.queue.mark_failed(job, reason)
            self.record_operational_delivery(job, "suppressed", reason)
            self.mark_reasoning_case_suppressed(job, reason)
            self.last_run_details.append(self.job_detail(job, "suppressed", repeat_reason_code))
            return False
        if reason_codes.intersection({"market_closed", "market_hours"}):
            context["postDecisionDeliveryGate"] = {
                "version": "post-decision-delivery-gate-v1",
                "status": "market-hours-recheck",
                "reasonCode": reason_code,
                "reason": "AI 판단 저장 후 최신 장 상태와 장외 발송 설정을 다시 확인합니다.",
            }
            job.context = context
            return True
        return True

    def apply_customer_delivery_explanation_gate(self, job: NotificationJob) -> bool:
        """Freeze one customer-visible reason after every delivery gate passed."""

        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return True
        context = dict(job.context or {})
        explanation = build_customer_delivery_explanation(
            message_type=job.message_type,
            source_event_name=job.source_event_name,
            source_event_id=job.source_event_id,
            context=context,
        )
        validation = (
            dict(explanation.get("validation") or {})
            if isinstance(explanation.get("validation"), dict)
            else {}
        )
        context.update({
            "customerDeliveryExplanation": explanation,
            "customerDeliveryExplanationRequired": True,
            "customerDeliveryExplanationValidationState": str(validation.get("state") or "invalid"),
        })
        job.context = context
        if validation.get("state") == "valid":
            self.record_lifecycle(
                job,
                "delivery_reason_validated",
                "ready",
                metadata={
                    "version": str(explanation.get("version") or ""),
                    "purpose": str(explanation.get("purpose") or ""),
                    "primaryCause": str((explanation.get("primaryCause") or {}).get("category") or ""),
                },
            )
            return True
        errors = [str(item or "").strip() for item in validation.get("errors") or [] if str(item or "").strip()]
        reason = "사용자 알림 발송 사유 계약 오류"
        if errors:
            reason += ": " + ", ".join(errors[:6])
        context.update({
            "deliverySuppressionReason": "customer_delivery_explanation_invalid",
            "deliverySuppressionDetail": reason,
        })
        job.context = context
        if hasattr(self.queue, "mark_suppressed"):
            self.queue.mark_suppressed(job, reason)
        else:
            self.queue.mark_failed(job, reason)
        self.record_lifecycle(
            job,
            "delivery_reason_validated",
            "invalid",
            reason,
            {"errors": errors, "sourceEventName": job.source_event_name},
        )
        self.record_operational_delivery(job, "suppressed", reason)
        self.mark_reasoning_case_suppressed(job, reason)
        self.notify_delivery_explanation_contract_error(job, reason, errors)
        self.last_run_details.append(self.job_detail(job, "suppressed", reason))
        return False

    def notify_delivery_explanation_contract_error(
        self,
        job: NotificationJob,
        reason: str,
        errors: List[str],
    ) -> None:
        """Report deterministic contract failures without retrying the customer alert."""

        if self.dry_run or not callable(self.operations_notifier_factory):
            return
        message = "\n".join([
            "🚨 시스템 오류",
            "• 구성요소: Python notification delivery explanation",
            "• 오류 유형: NotificationDeliveryContractError",
            "• 오류 내용: " + reason,
            "• 단계: final delivery reason validation",
            "• 알림 작업: " + notification_debug_number(job.job_id),
            "• 원본 이벤트: " + (str(job.source_event_name or "-")[:191]),
        ])
        context = dict(job.context or {})
        try:
            delivery = self.operations_notifier_factory(None).send(message)
            context["customerDeliveryExplanationOperationalAlert"] = {
                "attempted": True,
                "delivered": bool(getattr(delivery, "delivered", False)),
                "provider": str(getattr(delivery, "label", "") or ""),
                "errors": list(errors or []),
            }
        except Exception as error:  # noqa: BLE001 - the original deterministic suppression remains authoritative.
            context["customerDeliveryExplanationOperationalAlert"] = {
                "attempted": True,
                "delivered": False,
                "error": str(error)[:180],
                "errors": list(errors or []),
            }
        job.context = context

    def message_type_allowed(self, message_type: object) -> bool:
        value = str(message_type or "").strip()
        if self.include_message_types and value not in self.include_message_types:
            return False
        if self.exclude_message_types and value in self.exclude_message_types:
            return False
        return True

    def record_eligibility_outcome(self, job: NotificationJob, status: str, reason: str = "") -> None:
        if str(status or "").strip().lower() == "suppressed":
            self.mark_reasoning_case_suppressed(job, reason)
        self.last_run_details.append(self.job_detail(job, status, reason))

    def record_lifecycle(
        self,
        job: NotificationJob,
        stage: str,
        outcome: str,
        reason: str = "",
        metadata: Dict[str, object] = None,
    ) -> None:
        if not hasattr(self.queue, "record_lifecycle"):
            return
        self.queue.record_lifecycle(job, stage, outcome, reason, metadata)

    def apply_operational_state_gate(self, job: NotificationJob, stage: str) -> bool:
        return self.eligibility_service.apply_operational_state_gate(job, stage)

    def apply_inference_change_gate(self, job: NotificationJob) -> bool:
        return self.eligibility_service.apply_inference_change_gate(job)

    def apply_market_hours_gate(self, job: NotificationJob, stage: str) -> bool:
        return self.eligibility_service.apply_market_hours_gate(job, stage)

    def apply_dispatch_freshness_gate(self, job: NotificationJob, stage: str) -> bool:
        return self.eligibility_service.apply_dispatch_freshness_gate(job, stage)

    def apply_ai_freshness_headroom_gate(self, job: NotificationJob) -> bool:
        return self.eligibility_service.apply_ai_freshness_headroom_gate(job)

    def request_fresh_data_recheck(self, job: NotificationJob, stage: str, reason: str) -> Dict[str, object]:
        return self.eligibility_service.request_fresh_data_recheck(job, stage, reason)

    @staticmethod
    def symbol_from_job(job: NotificationJob) -> str:
        return NotificationDispatchEligibilityService.symbol_from_job(job)

    def record_operational_delivery(self, job: NotificationJob, outcome: str, reason: str = "") -> None:
        self.eligibility_service.record_operational_delivery(job, outcome, reason)

    def job_detail(self, job: NotificationJob, status: str, reason: str = "") -> str:
        context = job.context if isinstance(job.context, dict) else {}
        target = (
            str(context.get("symbol") or "").strip()
            or str(context.get("displayTarget") or "").strip()
            or str(context.get("rawTarget") or "").strip()
            or "all"
        )
        reason_text = (" · " + reason) if reason else ""
        return notification_debug_number(job.job_id) + " " + str(job.message_type or "-") + "/" + target + " " + status + reason_text

    def apply_account_delivery_context(self, job: NotificationJob, account) -> None:
        if not account or not hasattr(account, "message_delivery_context"):
            return
        context = dict(job.context or {})
        context.update(account.message_delivery_context())
        job.context = context

    def render(self, job: NotificationJob) -> str:
        rendered = self.rendering_service.render(job)
        if rendered and hasattr(self.queue, "record_lifecycle"):
            self.queue.record_lifecycle(
                job,
                "rendered",
                "ready",
                metadata={
                    "renderedBytes": len(rendered.encode("utf-8")),
                    "renderedSha256": str(
                        ((job.context or {}).get("notificationPresentationAudit") or {}).get("renderedSha256")
                        or ""
                    ),
                },
            )
        return rendered

    def apply_send_time_context(self, job: NotificationJob) -> None:
        self.rendering_service.apply_send_time_context(job)

    def append_holding_timing_sent_time(self, context: Dict[str, object], sent_time: str) -> None:
        self.rendering_service.append_holding_timing_sent_time(context, sent_time)

    def deliver(self, job: NotificationJob, accounts: Dict[str, object], message: str) -> None:
        self.dispatch_service.deliver(job, accounts, message)

    def capture_operator_report_after_delivery(self, job: NotificationJob, customer_message: str) -> str:
        try:
            return self.enqueue_operator_reasoning_report(job, customer_message)
        except Exception as operator_error:  # noqa: BLE001 - operator audit must not retry the customer alert.
            context = dict(job.context or {})
            context.update({
                "operatorReasoningReportStatus": "error",
                "operatorReasoningReportError": str(operator_error)[:180],
            })
            job.context = context
            return "운영자 보고 생성 실패"

    def enqueue_operator_reasoning_report(self, job: NotificationJob, customer_message: str) -> str:
        if not self.operator_reports_enabled or str(job.message_type or "") != INVESTMENT_INSIGHT:
            return ""
        context = dict(job.context or {})
        relation_context = context.get("ontologyRelationContext")
        if not isinstance(relation_context, dict) or not relation_context:
            metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
            relation_context = metadata.get("ontologyRelationContext")
        if not isinstance(relation_context, dict) or not relation_context:
            context["operatorReasoningReportStatus"] = "skipped_missing_relation_context"
            job.context = context
            return "운영자 보고 생략: 관계 추론 없음"
        report = build_notification_reasoning_report(context, job.job_id, customer_message)
        report_text = render_operator_reasoning_report(report)
        if context.get("testDispatch"):
            report_text = "🧪 테스트 발송 · 운영자 검증용\n" + report_text
        if not report_text:
            context["operatorReasoningReportStatus"] = "skipped_empty_report"
            job.context = context
            return "운영자 보고 생략: 빈 보고서"
        report_context = {
            "messageType": OPERATOR_REASONING_REPORT,
            "accountId": job.account_id,
            "accountLabel": job.account_label,
            "customerNotificationNumber": report.customer_notification_number,
            "customerJobId": job.job_id,
            "displayTarget": report.target,
            "target": report.target,
            "symbol": report.symbol,
            "rawSymbol": report.symbol,
            "body": report_text,
            "telegramMessage": report_text,
            "readableMessage": report_text,
            "testDispatch": bool(context.get("testDispatch")),
            "notificationSignals": ["operatorAudit", "confirmingData", "actionable"],
            "reasoningReport": report.to_dict(),
        }
        operator_job = NotificationJob.create(
            report_text,
            account_id=job.account_id,
            account_label=job.account_label,
            message_type=OPERATOR_REASONING_REPORT,
            source_event_id=job.source_event_id,
            source_event_name=job.source_event_name,
            dedupe_key="operator-report:" + job.job_id,
            context=report_context,
        )
        if not self.queue.enqueue(operator_job):
            context["operatorReasoningReportStatus"] = "enqueue_failed"
            job.context = context
            return "운영자 보고 큐 적재 실패"
        context.update({
            "operatorReasoningReportStatus": "queued",
            "operatorReasoningReportJobId": operator_job.job_id,
        })
        job.context = context
        return "운영자 보고 큐 적재"

    def mark_quiet_hours_suppressed(self, job: NotificationJob, account) -> None:
        reason = account.quiet_hours_reason()
        context = dict(job.context or {})
        context.update({
            "quietHoursSuppressed": True,
            "quietHoursReason": reason,
            "quietHoursStart": account.quiet_hours_start,
            "quietHoursEnd": account.quiet_hours_end,
            "quietHoursTimezone": account.quiet_hours_timezone,
        })
        job.context = context
        if hasattr(self.queue, "mark_suppressed"):
            self.queue.mark_suppressed(job, reason)
        else:
            self.queue.mark_failed(job, reason)
