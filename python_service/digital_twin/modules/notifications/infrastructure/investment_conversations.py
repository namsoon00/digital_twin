"""Transactional causal aliases and immutable job membership for Telegram replies."""
import json

from digital_twin.modules.notifications.domain.investment_conversation import VERSION, conversation_sources, key
from digital_twin.infrastructure.settings import utc_now


class InvestmentConversationStore:
    def register_investment_conversation(self, connection, job):
        source = conversation_sources(job)
        if source is None:
            return
        account, symbol = source["accountId"], source["symbol"]
        # A transaction-held scope row closes concurrent first-message races.
        scope = key("conversation-scope", account, symbol)
        connection.execute("INSERT IGNORE INTO notification_research_threads "
            "(thread_key,first_job_id,receipts_json,updated_at) VALUES (%s,'','{}',%s)", (scope, utc_now()))
        connection.execute("SELECT thread_key FROM notification_research_threads WHERE thread_key=%s FOR UPDATE", (scope,)).fetchone()
        existing = connection.execute("SELECT account_id,symbol,world_id,payload_json FROM notification_conversation_jobs "
            "WHERE job_id=%s FOR UPDATE", (job.job_id,)).fetchone()
        if existing:
            if (existing["account_id"], existing["symbol"], existing["world_id"]) != (account, symbol, source["worldId"]):
                raise ValueError("기존 알림 대화의 계좌·종목·world를 바꿀 수 없습니다.")
            job.context["notificationConversation"] = json.loads(existing["payload_json"])
            return  # Retained membership is immutable, including across outbox retention.
        aliases = [key(account, symbol, *item) for item in source["sources"]]
        matches = set()
        for alias in aliases:
            row = connection.execute("SELECT thread_key FROM notification_conversation_sources WHERE source_key=%s FOR UPDATE", (alias,)).fetchone()
            if row:
                matches.add(row["thread_key"])
        if source["parentJobId"] and source["worldId"]:
            row = connection.execute("SELECT thread_key,world_id FROM notification_conversation_jobs "
                "WHERE job_id=%s AND account_id=%s AND symbol=%s FOR UPDATE", (source["parentJobId"], account, symbol)).fetchone()
            if row and row["world_id"] == source["worldId"]:
                matches.add(row["thread_key"])
        if source["worldId"]:
            # Raw quote anchors have no world until the first analysis joins.
            # A reused source event cannot bridge two different graph worlds.
            for candidate in tuple(matches):
                worlds = connection.execute("SELECT world_id FROM notification_conversation_jobs "
                    "WHERE thread_key=%s AND world_id<>'' FOR UPDATE", (candidate,)).fetchall()
                if any(row["world_id"] != source["worldId"] for row in worlds):
                    matches.remove(candidate)
        thread = next(iter(matches)) if len(matches) == 1 else key("conversation-job", job.job_id)
        reason = "shared-source" if len(matches) == 1 else "multiple-conversations" if matches else "new-source"
        connection.execute("INSERT IGNORE INTO notification_research_threads "
            "(thread_key,first_job_id,receipts_json,updated_at) VALUES (%s,%s,'{}',%s)", (thread, job.job_id, utc_now()))
        root = connection.execute("SELECT first_job_id FROM notification_research_threads WHERE thread_key=%s FOR UPDATE", (thread,)).fetchone()
        metadata = {"version": VERSION, "threadKey": thread, "rootJobId": root["first_job_id"], "reason": reason,
                    "accountId": account, "symbol": symbol, "worldId": source["worldId"]}
        connection.execute("INSERT INTO notification_conversation_jobs "
            "(job_id,thread_key,account_id,symbol,world_id,payload_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (job.job_id, thread, account, symbol, source["worldId"], json.dumps(metadata)))
        for alias in aliases:
            connection.execute("INSERT IGNORE INTO notification_conversation_sources (source_key,thread_key) VALUES (%s,%s)", (alias, thread))
        job.context["notificationConversation"] = metadata

    def bind_market_conversation_event(self, connection, job, event_id):
        """Called in the monitor transaction which records this exact source event."""
        if job.message_type != "marketObservation" or not event_id:
            return
        member = connection.execute("SELECT thread_key,account_id,symbol FROM notification_conversation_jobs WHERE job_id=%s", (job.job_id,)).fetchone()
        if not member or member["account_id"] != job.account_id:
            return
        connection.execute("INSERT IGNORE INTO notification_conversation_sources (source_key,thread_key) VALUES (%s,%s)",
            (key(member["account_id"], member["symbol"], "event", "", event_id), member["thread_key"]))

    def investment_delivery_thread(self, job, destination):
        with self.connect() as connection:
            row = connection.execute("SELECT thread_key FROM notification_conversation_jobs WHERE job_id=%s AND account_id=%s AND symbol=%s",
                (job.job_id, job.account_id, str(job.context.get("symbol") or job.context.get("rawSymbol") or "").strip().upper())).fetchone()
        if not row:
            raise ValueError("알림 대화의 원본 연결 기록을 확인할 수 없습니다.")
        return self._delivery_thread(job, destination, row["thread_key"], lambda connection, job: None)

    def save_investment_delivery_progress(self, job, destination, checkpoint):
        with self.connect() as connection:
            row = connection.execute("SELECT thread_key FROM notification_conversation_jobs WHERE job_id=%s AND account_id=%s", (job.job_id, job.account_id)).fetchone()
        if not row:
            raise ValueError("알림 대화의 원본 연결 기록을 확인할 수 없습니다.")
        self._save_thread_progress(job, destination, checkpoint, row["thread_key"])
