import time
from datetime import datetime, timezone
from typing import Callable, Dict, List
from zoneinfo import ZoneInfo

from ..domain.disclosure_analysis import local_disclosure_analysis
from ..domain.investment_brain import decision_episode_from_context
from ..domain.market_data import number
from ..domain.message_types import INVESTMENT_INSIGHT, OPERATOR_REASONING_REPORT, is_operations_delivery_message_type
from ..domain.monitoring import RealtimeMonitor
from ..domain.notification_ai import enrich_notification_ai_context
from ..domain.notification_ai_gate_contracts import ai_gate_enabled_for_message_type
from ..domain.notification_ai_gate_validation import local_validated_ai_response
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.notification_reasoning_report import build_notification_reasoning_report, render_operator_reasoning_report
from .notification_ai_gate_audit import context_with_validated_ai_response
from .notification_disclosure_rendering import context_with_disclosure_analysis


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
        if str(job.message_type or "") != "externalDartDisclosure":
            return
        if str(self.settings.get("dartDisclosureAiAnalysisEnabled", "1")).strip() == "0":
            return
        context = dict(job.context or {})
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
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        job.context = enrich_notification_ai_context(context, self.settings)


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


def notification_ontology_quality_min_score(settings: Dict[str, object] = None) -> float:
    settings = settings or {}
    raw = settings.get("notificationOntologyQualityMinScore") or settings.get("ontologyNotificationQualityMinScore") or 55
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 55.0


def _dict_value(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def ontology_quality_candidates(context: Dict[str, object]) -> List[Dict[str, object]]:
    context = _dict_value(context)
    metadata = _dict_value(context.get("metadata"))
    ontology = _dict_value(context.get("ontology"))
    metadata_ontology = _dict_value(metadata.get("ontology"))
    return [
        _dict_value(context.get("ontologyQuality")),
        _dict_value(context.get("ontologyQualitySample")),
        _dict_value(metadata.get("ontologyQuality")),
        _dict_value(metadata.get("ontologyQualitySample")),
        _dict_value(metadata_ontology.get("projection")),
        _dict_value(ontology.get("typedb")),
        _dict_value(metadata_ontology.get("typedb")),
    ]


def ontology_quality_score(candidate: Dict[str, object]):
    candidate = _dict_value(candidate)
    for key in ["score", "qualityScore", "overallScore", "overall_score"]:
        if key in candidate:
            if candidate.get(key) in (None, ""):
                return None
            return number(candidate.get(key))
    scores = _dict_value(candidate.get("scores"))
    if "overall" in scores:
        if scores.get("overall") in (None, ""):
            return None
        return number(scores.get("overall"))
    payload_scores = _dict_value(_dict_value(candidate.get("payload")).get("scores"))
    if "overall" in payload_scores:
        if payload_scores.get("overall") in (None, ""):
            return None
        return number(payload_scores.get("overall"))
    return None


def ontology_quality_gate_context(context: Dict[str, object], settings: Dict[str, object] = None) -> Dict[str, object]:
    min_score = notification_ontology_quality_min_score(settings)
    for candidate in ontology_quality_candidates(context):
        score = ontology_quality_score(candidate)
        if score is None:
            continue
        status = "passed" if score >= min_score else "limited"
        cap = 100.0 if status == "passed" else max(35.0, min(75.0, score + 20.0))
        return {
            "enabled": True,
            "status": status,
            "score": round(score, 2),
            "minScore": round(min_score, 2),
            "confidenceCap": round(cap, 1),
            "qualitySampleId": str(candidate.get("qualitySampleId") or candidate.get("sampleId") or candidate.get("sample_id") or ""),
            "source": str(candidate.get("source") or "ontologyQuality"),
            "reason": "온톨로지 품질 점수가 알림 기준 이상입니다." if status == "passed" else "온톨로지 품질 점수가 알림 기준보다 낮아 확신도와 판단 강도를 제한합니다.",
        }
    return {
        "enabled": True,
        "status": "unknown",
        "minScore": round(min_score, 2),
        "confidenceCap": 100.0,
        "reason": "알림 컨텍스트에 온톨로지 품질 점수가 없어 별도 제한을 적용하지 않았습니다.",
    }


def apply_ontology_quality_gate_to_response(response, gate: Dict[str, object]) -> None:
    if not response or not isinstance(gate, dict) or gate.get("status") != "limited":
        return
    cap = number(gate.get("confidenceCap")) or 75.0
    reason = str(gate.get("reason") or "온톨로지 품질 점수가 낮아 확신도를 제한했습니다.")
    if reason not in response.confidence_cap_reasons:
        response.confidence_cap_reasons.append(reason)
    if reason not in response.validation_warnings:
        response.validation_warnings.append(reason)
    if number(response.confidence_cap) > cap:
        response.confidence_cap = cap
    if number(response.confidence) > cap:
        response.validation_warnings.append("온톨로지 품질 게이트로 AI 확신도 " + str(round(number(response.confidence), 1)) + "%를 " + str(round(cap, 1)) + "%로 낮췄습니다.")
        response.confidence = cap


class NotificationAIValidatedGateEnricher:
    def __init__(self, reviewer=None, settings: Dict[str, object] = None, decision_episode_store=None):
        self.reviewer = reviewer
        self.settings = settings or {}
        self.decision_episode_store = decision_episode_store

    def __call__(self, job: NotificationJob) -> None:
        if not ai_gate_enabled_for_message_type(job.message_type, self.settings):
            return
        context = dict(job.context or {})
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        context.setdefault("jobId", job.job_id)
        quality_gate = ontology_quality_gate_context(context, self.settings)
        context["ontologyQualityGate"] = quality_gate
        if context.get("notificationAiValidatedResponse"):
            job.context = context
            return
        try:
            response = self.reviewer.review(context) if self.reviewer else local_validated_ai_response(context)
        except Exception as error:  # noqa: BLE001 - notification delivery should degrade to local validation.
            response = local_validated_ai_response(context, source="local fallback")
            response.validation_warnings.append("AI 검증 실패로 로컬 의견을 사용했습니다: " + str(error)[:140])
        apply_ontology_quality_gate_to_response(response, quality_gate)
        if self.decision_episode_store and job.message_type == INVESTMENT_INSIGHT:
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
                    context["investmentDecisionEpisodeId"] = episode.episode_id
                    context["investmentDecisionEpisode"] = episode.to_dict()
            except Exception as error:  # noqa: BLE001 - memory persistence must not block a time-sensitive alert.
                response.validation_warnings.append("투자 판단 기억 저장 실패: " + str(error)[:140])
        job.context = context_with_validated_ai_response(context, response)


class NotificationHypothesisResearchEnricher:
    def __init__(self, investment_brain_service=None, settings: Dict[str, object] = None):
        self.investment_brain_service = investment_brain_service
        self.settings = dict(settings or {})

    def __call__(self, job: NotificationJob) -> None:
        if job.message_type != INVESTMENT_INSIGHT or not self.enabled():
            return
        context = dict(job.context or {})
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
        stale_after_minutes: int = 30,
        template_renderer: Callable = None,
        context_enricher: Callable = None,
        now_provider: Callable = None,
        operator_reports_enabled: bool = False,
    ):
        self.queue = queue
        self.account_repository = account_repository
        self.notifier_factory = notifier_factory
        self.operations_notifier_factory = operations_notifier_factory
        self.dry_run = dry_run
        self.send_gap_seconds = max(0.0, float(send_gap_seconds or 0))
        self.stale_after_minutes = max(1, int(stale_after_minutes or 30))
        self.template_renderer = template_renderer
        self.context_enricher = context_enricher
        self.now_provider = now_provider or (lambda: datetime.now(ZoneInfo("UTC")))
        self.operator_reports_enabled = bool(operator_reports_enabled)
        self.last_run_details = []

    def account_map(self) -> Dict[str, object]:
        return {account.account_id: account for account in self.account_repository.load_all()}

    def run_once(self, limit: int = 10) -> int:
        self.last_run_details = []
        use_claim = (not self.dry_run) and hasattr(self.queue, "claim_pending")
        jobs = self.queue.claim_pending(limit=limit, stale_after_minutes=self.stale_after_minutes) if use_claim else self.queue.pending(limit=limit)
        if not jobs:
            return 0
        accounts = self.account_map()
        processed = 0
        for job in jobs:
            if str(job.message_type or "") == OPERATOR_REASONING_REPORT and not self.operator_reports_enabled:
                reason = "운영자 추론 보고서 알림이 비활성화되어 발송하지 않았습니다."
                if hasattr(self.queue, "mark_suppressed"):
                    self.queue.mark_suppressed(job, reason)
                else:
                    self.queue.mark_failed(job, reason)
                self.last_run_details.append(self.job_detail(job, "suppressed", "operator reports disabled"))
                processed += 1
                continue
            if not job.text.strip():
                self.queue.mark_failed(job, "empty notification text")
                self.last_run_details.append(self.job_detail(job, "failed", "empty text"))
                continue
            account = accounts.get(job.account_id)
            self.apply_account_delivery_context(job, account)
            if not self.dry_run and account and account.quiet_hours_active(self.now_provider(), job.message_type):
                self.mark_quiet_hours_suppressed(job, account)
                self.last_run_details.append(self.job_detail(job, "suppressed", "quiet hours"))
                processed += 1
                continue
            if not self.dry_run and not use_claim:
                self.queue.mark_processing(job)
            message = self.render(job)
            if not message:
                self.queue.mark_failed(job, "empty rendered notification text")
                self.last_run_details.append(self.job_detail(job, "failed", "empty rendered text"))
                continue
            if self.dry_run:
                print(message)
                self.last_run_details.append(self.job_detail(job, "dry-run"))
                processed += 1
                continue
            try:
                self.deliver(job, accounts, message)
                operator_detail = self.capture_operator_report_after_delivery(job, message)
                self.queue.mark_done(job)
                self.last_run_details.append(self.job_detail(job, "done", operator_detail))
                processed += 1
            except Exception as error:  # noqa: BLE001 - one failed delivery must not stop the queue.
                self.queue.mark_failed(job, str(error))
                self.last_run_details.append(self.job_detail(job, "failed", str(error)[:120]))
            if self.send_gap_seconds and processed < len(jobs):
                time.sleep(self.send_gap_seconds)
        return processed

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
        self.apply_send_time_context(job)
        if self.context_enricher:
            self.context_enricher(job)
        if self.template_renderer:
            return str(self.template_renderer(job) or "").strip()
        return job.text.strip()

    def apply_send_time_context(self, job: NotificationJob) -> None:
        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(ZoneInfo("UTC"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("UTC"))
        sent_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        sent_time = now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
        context = dict(job.context or {})
        context.update({
            "jobId": job.job_id,
            "notificationNumber": notification_debug_number(job.job_id),
            "sentAt": sent_at,
            "sentTime": sent_time,
            "sentLine": "발송시각 " + sent_time,
        })
        job.context = context

    def append_holding_timing_sent_time(self, context: Dict[str, object], sent_time: str) -> None:
        plain_line = "발송시각 " + sent_time
        rich_line = "• <b>발송시각</b>: <code>" + sent_time + "</code>"
        raw_lines = str(context.get("rawLines") or "")
        if "발송시각" not in raw_lines:
            context["rawLines"] = "\n".join(part for part in [raw_lines, plain_line] if str(part or "").strip())
        telegram_data = str(context.get("telegramDataLines") or "")
        if "발송시각" not in telegram_data:
            context["telegramDataLines"] = "\n".join(part for part in [telegram_data, rich_line] if str(part or "").strip())
        telegram_message = str(context.get("telegramMessage") or "")
        if telegram_message and "발송시각" not in telegram_message:
            marker = "\n\n<b>발송 기준</b>"
            if marker in telegram_message:
                telegram_message = telegram_message.replace(marker, "\n" + rich_line + marker, 1)
            else:
                telegram_message = telegram_message + "\n" + rich_line
            context["telegramMessage"] = telegram_message
        readable_message = str(context.get("readableMessage") or "")
        if readable_message and "발송시각" not in readable_message:
            plain_bullet = "• 발송시각: " + sent_time
            marker = "\n\n발송 기준"
            if marker in readable_message:
                readable_message = readable_message.replace(marker, "\n" + plain_bullet + marker, 1)
            else:
                readable_message = readable_message + "\n" + plain_bullet
            context["readableMessage"] = readable_message

    def deliver(self, job: NotificationJob, accounts: Dict[str, object], message: str) -> None:
        operations_delivery = is_operations_delivery_message_type(job.message_type)
        factory = self.operations_notifier_factory if operations_delivery and self.operations_notifier_factory else self.notifier_factory
        context = dict(job.context or {})
        context["deliveryAudience"] = "operations" if operations_delivery else "account"
        context["deliveryChannel"] = "operationsTelegram" if operations_delivery else "accountNotification"
        job.context = context
        notifier = factory(accounts.get(job.account_id))
        delivery = notifier.send(message)
        context["deliveryProvider"] = str(getattr(delivery, "label", "") or "")
        if getattr(delivery, "reason", ""):
            context["deliveryNote"] = str(delivery.reason)
        job.context = context
        if not delivery.delivered:
            raise RuntimeError(delivery.reason or "notification delivery failed")

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
