import time
from datetime import datetime, timezone
from typing import Callable, Dict
from zoneinfo import ZoneInfo

from ..domain.disclosure_analysis import context_with_disclosure_analysis, local_disclosure_analysis
from ..domain.notification_ai import enrich_notification_ai_context
from ..domain.notification_ai_gate import ai_gate_enabled_for_message_type, context_with_validated_ai_response, local_validated_ai_response
from ..domain.notifications import NotificationJob


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


class NotificationAIValidatedGateEnricher:
    def __init__(self, reviewer=None, settings: Dict[str, object] = None):
        self.reviewer = reviewer
        self.settings = settings or {}

    def __call__(self, job: NotificationJob) -> None:
        if not ai_gate_enabled_for_message_type(job.message_type, self.settings):
            return
        context = dict(job.context or {})
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        if context.get("notificationAiValidatedResponse"):
            return
        try:
            response = self.reviewer.review(context) if self.reviewer else local_validated_ai_response(context)
        except Exception as error:  # noqa: BLE001 - notification delivery should degrade to local validation.
            response = local_validated_ai_response(context, source="local fallback")
            response.validation_warnings.append("AI 검증 실패로 로컬 의견을 사용했습니다: " + str(error)[:140])
        job.context = context_with_validated_ai_response(context, response)


class NotificationQueueRunner:
    def __init__(
        self,
        queue,
        account_repository,
        notifier_factory: Callable,
        dry_run: bool = False,
        send_gap_seconds: float = 0.0,
        template_renderer: Callable = None,
        context_enricher: Callable = None,
        now_provider: Callable = None,
    ):
        self.queue = queue
        self.account_repository = account_repository
        self.notifier_factory = notifier_factory
        self.dry_run = dry_run
        self.send_gap_seconds = max(0.0, float(send_gap_seconds or 0))
        self.template_renderer = template_renderer
        self.context_enricher = context_enricher
        self.now_provider = now_provider or (lambda: datetime.now(ZoneInfo("UTC")))

    def account_map(self) -> Dict[str, object]:
        return {account.account_id: account for account in self.account_repository.load_all()}

    def run_once(self, limit: int = 10) -> int:
        jobs = self.queue.pending(limit=limit)
        if not jobs:
            print("No pending notification jobs.")
            return 0
        accounts = self.account_map()
        processed = 0
        for job in jobs:
            if not job.text.strip():
                self.queue.mark_failed(job, "empty notification text")
                continue
            account = accounts.get(job.account_id)
            if not self.dry_run and account and account.quiet_hours_active(self.now_provider(), job.message_type):
                self.mark_quiet_hours_suppressed(job, account)
                processed += 1
                continue
            if not self.dry_run:
                self.queue.mark_processing(job)
            message = self.render(job)
            if not message:
                self.queue.mark_failed(job, "empty rendered notification text")
                continue
            if self.dry_run:
                print(message)
                processed += 1
                continue
            try:
                self.deliver(job, accounts, message)
                self.queue.mark_done(job)
                processed += 1
            except Exception as error:  # noqa: BLE001 - one failed delivery must not stop the queue.
                self.queue.mark_failed(job, str(error))
            if self.send_gap_seconds and processed < len(jobs):
                time.sleep(self.send_gap_seconds)
        return processed

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
            "sentAt": sent_at,
            "sentTime": sent_time,
            "sentLine": "발송시각 " + sent_time,
        })
        if job.message_type == "holdingTiming":
            self.append_holding_timing_sent_time(context, sent_time)
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
        notifier = self.notifier_factory(accounts.get(job.account_id))
        delivery = notifier.send(message)
        if not delivery.delivered:
            raise RuntimeError(delivery.reason or "notification delivery failed")

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


class NotificationQueueScheduler:
    def __init__(self, runner: NotificationQueueRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 30))
        self.running = True

    def stop(self) -> None:
        self.running = False

    def run_forever(self, limit: int = 10) -> None:
        print("Python notification worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                processed = self.runner.run_once(limit=limit)
                if processed:
                    print("Processed notification jobs: " + str(processed))
            except Exception as error:  # noqa: BLE001 - worker must continue after a cycle failure.
                print("Python notification worker error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
