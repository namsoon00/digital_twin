"""Transactional causal aliases and immutable job membership for Telegram replies."""
import json
from contextlib import contextmanager

from digital_twin.modules.notifications.domain.investment_conversation import VERSION, conversation_sources, key, relation_parent_job_id
from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
from digital_twin.modules.notifications.domain.research_thread import ResearchDeliveryDeferred
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
        if source["parentJobId"]:
            metadata["parentJobId"] = source["parentJobId"]
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

    @contextmanager
    def investment_delivery_thread(self, job, destination):
        parent = relation_parent_job_id(job)
        checkpoint = ((job.context.get("transportDelivery") or {}).get("checkpoint") or {})
        if checkpoint.get("messageIds") and checkpoint.get("destinationFingerprint") == destination:
            yield checkpoint.get("replyToMessageId") or None
            return  # The first sent chunk fixes the reply destination.
        if parent:
            # Reply to the hypothesis itself, even if its conversation began
            # with a quote alert. Receipts survive retention of the original job.
            with self.delivery_named_lock("relation-reply:" + key(job.account_id, parent)[:48]) as acquired:
                if not acquired:
                    raise ResearchDeliveryDeferred("같은 가설의 앞선 답글 발송을 기다립니다.")
                with self.connect() as connection:
                    receipt = connection.execute(
                        "SELECT JSON_EXTRACT(metadata_json, '$.deliveryCheckpoint.messageIds') AS message_ids "
                        "FROM notification_delivery_attempts WHERE job_id=%s AND status='delivered' "
                        "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.accountId'))=%s "
                        "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.deliveryCheckpoint.destinationFingerprint'))=%s "
                        "AND JSON_EXTRACT(metadata_json, '$.receiptVerified')=true "
                        "AND JSON_EXTRACT(metadata_json, '$.destinationVerified')=true "
                        "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.relationChangeSnapshot.symbol'))=%s "
                        "AND COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.relationChangeSnapshot.worldId')),''), "
                        "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.notificationConversation.worldId')))=%s "
                        "ORDER BY completed_at DESC,attempt_id DESC LIMIT 1", (parent, job.account_id, destination,
                            (job.context["relationChangeEvidence"]["previous"])["symbol"],
                            (job.context["relationChangeEvidence"]["previous"])["worldId"])).fetchone()
                ids = json.loads(receipt["message_ids"]) if receipt and receipt.get("message_ids") else []
                changes = (job.context.get("relationChangeEvidence") or {}).get("transitions") or []
                if not ids and changes and all(row.get("changeCategory") == "data-availability" for row in changes):
                    raise NotificationDeliverySuppressed("이 수신처의 원본 알림을 확인할 수 없어 자료 답글을 보내지 않습니다.")
                yield ids[0] if ids else None
            return
        with self.connect() as connection:
            relation = job.context.get("ontologyRelationContext") or {}
            symbol = str(job.context.get("symbol") or job.context.get("rawSymbol") or (relation.get("subject") or {}).get("symbol") or "").strip().upper()
            row = connection.execute("SELECT thread_key FROM notification_conversation_jobs WHERE job_id=%s AND account_id=%s AND symbol=%s",
                (job.job_id, job.account_id, symbol)).fetchone()
        if not row:
            raise ValueError("알림 대화의 원본 연결 기록을 확인할 수 없습니다.")
        with self._delivery_thread(job, destination, row["thread_key"], lambda connection, job: None) as reply_id:
            yield reply_id

    def save_investment_delivery_progress(self, job, destination, checkpoint):
        with self.connect() as connection:
            row = connection.execute("SELECT thread_key FROM notification_conversation_jobs WHERE job_id=%s AND account_id=%s", (job.job_id, job.account_id)).fetchone()
        if not row:
            raise ValueError("알림 대화의 원본 연결 기록을 확인할 수 없습니다.")
        self._save_thread_progress(job, destination, checkpoint, row["thread_key"])
