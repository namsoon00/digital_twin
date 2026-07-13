from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.portfolio import utc_now_iso


@dataclass(frozen=True)
class NotificationReplayResult:
    requested_identifier: str
    status: str
    source_job_id: str = ""
    source_notification_number: str = ""
    replay_job_id: str = ""
    replay_notification_number: str = ""
    message_type: str = ""
    direct: bool = False
    dry_run: bool = False
    delivered: bool = False
    queued: bool = False
    reason: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "requestedIdentifier": self.requested_identifier,
            "status": self.status,
            "sourceJobId": self.source_job_id,
            "sourceNotificationNumber": self.source_notification_number,
            "replayJobId": self.replay_job_id,
            "replayNotificationNumber": self.replay_notification_number,
            "messageType": self.message_type,
            "direct": self.direct,
            "dryRun": self.dry_run,
            "delivered": self.delivered,
            "queued": self.queued,
            "reason": self.reason,
            "message": self.message,
        }


class NotificationReplayService:
    def __init__(
        self,
        queue,
        account_repository,
        runner_factory: Callable[[bool], object],
        lookup_limit: int = 200,
    ):
        self.queue = queue
        self.account_repository = account_repository
        self.runner_factory = runner_factory
        self.lookup_limit = max(1, min(1000, int(lookup_limit or 200)))

    def replay(self, identifier: str, direct: bool = False, dry_run: bool = False) -> NotificationReplayResult:
        requested = str(identifier or "").strip()
        if not requested:
            return NotificationReplayResult(requested, "not-found", reason="notification identifier is empty")
        source = self.find_job(requested)
        if not source:
            return NotificationReplayResult(requested, "not-found", reason="notification job was not found")

        replay_job = self.replay_job_from_source(source, direct=direct)
        runner = self.runner_factory(bool(dry_run))
        account = self.account_map().get(replay_job.account_id)
        if hasattr(runner, "apply_account_delivery_context"):
            runner.apply_account_delivery_context(replay_job, account)
        message = str(runner.render(replay_job) if hasattr(runner, "render") else replay_job.text).strip()
        message = self.replay_message(source, message)
        replay_job.text = message

        common = {
            "requested_identifier": requested,
            "source_job_id": source.job_id,
            "source_notification_number": notification_debug_number(source.job_id),
            "replay_job_id": replay_job.job_id,
            "replay_notification_number": notification_debug_number(replay_job.job_id),
            "message_type": replay_job.message_type,
            "direct": bool(direct),
            "dry_run": bool(dry_run),
            "message": message,
        }
        if dry_run:
            return NotificationReplayResult(status="dry-run", **common)
        if direct:
            return self.deliver_direct(runner, replay_job, common)
        queued = bool(self.queue.enqueue(replay_job))
        status = "queued" if queued else (replay_job.status or "suppressed")
        reason = "" if queued else (replay_job.last_error or "notification replay was not queued")
        return NotificationReplayResult(status=status, queued=queued, reason=reason, **common)

    def deliver_direct(self, runner, job: NotificationJob, common: Dict[str, object]) -> NotificationReplayResult:
        accounts = self.account_map()
        job.status = "processing"
        job.attempts = 1
        job.updated_at = utc_now_iso()
        self.upsert(job)
        try:
            if hasattr(runner, "deliver"):
                runner.deliver(job, accounts, job.text)
            if hasattr(self.queue, "mark_done"):
                self.queue.mark_done(job)
            else:
                job.status = "done"
                job.last_error = ""
                job.updated_at = utc_now_iso()
                self.upsert(job)
            return NotificationReplayResult(status="done", delivered=True, **common)
        except Exception as error:  # noqa: BLE001 - replay result must capture delivery failure.
            if hasattr(self.queue, "mark_failed"):
                self.queue.mark_failed(job, str(error))
            else:
                job.status = "failed"
                job.last_error = str(error)
                job.updated_at = utc_now_iso()
                self.upsert(job)
            return NotificationReplayResult(status="failed", delivered=False, reason=str(error), **common)

    def replay_job_from_source(self, source: NotificationJob, direct: bool = False) -> NotificationJob:
        context = dict(source.context or {})
        context.update({
            "notificationReplay": True,
            "notificationReplayMode": "direct" if direct else "queue",
            "notificationReplayRequestedAt": utc_now_iso(),
            "replaySourceJobId": source.job_id,
            "replaySourceNotificationNumber": notification_debug_number(source.job_id),
            "replaySourceStatus": source.status,
            "replaySourceMessageType": source.message_type,
            "notificationTestBypassPolicy": bool(direct),
        })
        return NotificationJob.create(
            source.text,
            account_id=source.account_id,
            account_label=source.account_label,
            message_type=source.message_type,
            source_event_id=source.source_event_id,
            source_event_name=source.source_event_name or "notification.replay_requested",
            dedupe_key="",
            context=context,
        )

    def replay_message(self, source: NotificationJob, message: str) -> str:
        header = "[재발송] 원본 알림 " + notification_debug_number(source.job_id)
        text = str(message or source.text or "").strip()
        if not text:
            return header
        if text.startswith(header):
            return text
        return header + "\n\n" + text

    def find_job(self, identifier: str) -> Optional[NotificationJob]:
        normalized = self.normalize_identifier(identifier)
        for job in self.candidate_jobs(identifier):
            if self.normalize_identifier(job.job_id) == normalized:
                return job
            if self.normalize_identifier(notification_debug_number(job.job_id)) == normalized:
                return job
        return None

    def candidate_jobs(self, identifier: str) -> List[NotificationJob]:
        if hasattr(self.queue, "recent"):
            try:
                return list(self.queue.recent(limit=self.lookup_limit))
            except TypeError:
                return list(self.queue.recent(self.lookup_limit))
        if hasattr(self.queue, "jobs"):
            return list(self.queue.jobs())
        return []

    def normalize_identifier(self, value: str) -> str:
        return str(value or "").strip().upper().replace("-", "")

    def account_map(self) -> Dict[str, object]:
        if hasattr(self.account_repository, "load_all"):
            return {account.account_id: account for account in self.account_repository.load_all()}
        if hasattr(self.account_repository, "load"):
            return {account.account_id: account for account in self.account_repository.load()}
        return {}

    def upsert(self, job: NotificationJob) -> None:
        if hasattr(self.queue, "upsert_job"):
            self.queue.upsert_job(job)
        elif hasattr(self.queue, "update"):
            self.queue.update(job)

