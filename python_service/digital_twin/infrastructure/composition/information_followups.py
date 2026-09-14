"""Wire informational follow-ups to existing readers, calendar worker and outbox."""

import hashlib
from datetime import datetime, timedelta, timezone


def information_tracking_payload(kind, source_id, source_hash):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    settings = runtime_settings()
    row = stores.information_followup_store(settings).latest(kind, source_id)
    if not row or row["sourceHash"] != source_hash or row["status"] == "superseded":
        return None
    reaction = dict(row.get("marketReaction") or {})
    if not reaction:
        return None
    enabled = str(settings.get("informationFollowupEnabled", "1")).lower() not in {"0", "false", "off", "no", "disabled"}
    reaction["trackingStatus"] = row["status"] if enabled else "paused"
    if row["status"] == "active" and enabled:
        checked = datetime.fromisoformat(row["updatedAt"].replace('Z', '+00:00'))
        if checked < datetime.now(timezone.utc) - timedelta(minutes=30):
            reaction["trackingStatus"] = "stalled"
    if row.get("lastError") and row["status"] == "active":
        reaction.update(status="error", label="관측 기록 재확인 대기")
    reaction["delivery"] = []
    if row.get("delivery"):
        queue = stores.notification_job_store(settings)
        for entry in row["delivery"]:
            job = queue.get(entry.get("jobId")) if entry.get("jobId") else None
            reaction["delivery"].append({"phase": entry["phase"], "state": job.status if job else "not-found"})
    return reaction


def build_information_followup_service(settings):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.investment_calendar import build_investment_calendar_service
    from digital_twin.modules.market_data.application.information_followup_service import InformationFollowupService, stamp
    from digital_twin.modules.market_data.public import InformationObservationService
    from digital_twin.modules.news_intelligence.public import NewsDigestEnqueuer, build_information_brief, assess_news_eligibility
    from digital_twin.modules.notifications.contracts import NotificationJob
    from digital_twin.modules.notifications.domain.information_update import information_update_message

    evidence = stores.research_evidence_store(settings)
    accounts = stores.account_reader(settings)
    queue = stores.notification_job_store(settings)
    calendar = build_investment_calendar_service(settings)
    recipients = {}

    def sources(now):
        digest = NewsDigestEnqueuer(account_repository=accounts, monitor_store=stores.monitor_store(settings), queue=queue, settings=settings)
        active_accounts = [account for account in accounts.load_all() if account.enabled]
        recipients.clear()
        recipients.update({account.account_id: account for account in active_accounts})
        owned = {}
        for account in active_accounts:
            holding, watchlist = digest.account_symbols(account)
            owned[account.account_id] = set(holding) | set(watchlist)
        for item in evidence.latest(limit=150):
            if item.kind not in {"news", "disclosure", "filing"} or item.lifecycle_state != "active":
                continue
            account_ids = [identity for identity, symbols in owned.items() if item.symbol in symbols]
            if not account_ids:
                continue
            eligibility = assess_news_eligibility(item.raw_payload, title=item.title, summary=item.summary,
                symbol=item.symbol, source=item.source, url=item.url, lifecycle_state=item.lifecycle_state) if item.kind == "news" else None
            if eligibility and not eligibility.alert.eligible:
                continue
            brief = build_information_brief(item, eligibility=eligibility.to_dict() if eligibility else None)
            if brief.get("state") != "source-linked":
                continue
            yield {"sourceKind": "research", "sourceId": item.evidence_id, "sourceHash": brief.get("sourceHash"),
                "verified": True, "title": str((item.raw_payload or {}).get("translatedTitleKo") or item.title),
                "eventAt": item.published_at, "symbols": [item.symbol], "accountIds": account_ids, "sourceUrl": item.url,
                "notificationScope": item.symbol + ":" + str((item.raw_payload or {}).get("storyClusterId") or item.evidence_id)}
        result = calendar.list_events({"from": stamp(now - timedelta(days=3)), "to": stamp(now), "limit": 100})
        for item in result.get("events", []):
            if item.get("status", "active") != "active":
                continue
            release = (item.get("releaseInformation") or {}).get("release") or {}
            if not release:
                continue
            symbols = item.get("symbols") or (["^KS11", "USDKRW=X"] if release.get("country") == "KR" else ["^GSPC", "^IXIC"])
            yield {"sourceKind": "calendar", "sourceId": item["eventId"], "sourceHash": release["sourceHash"], "verified": True,
                "title": item["title"], "eventAt": release.get("releasedAt", ""), "publicationDate": release.get("releasedDate"),
                "symbols": symbols, "accountIds": item.get("accountIds") or list(recipients), "sourceUrl": release["sourceUrl"],
                "releaseMetrics": release["metrics"]}

    def validate_source(source):
        if source["sourceKind"] == "calendar":
            current = calendar.get_event(source["sourceId"])
            if not current or current.get("status", "active") != "active":
                return False
            release = ((current or {}).get("releaseInformation") or {}).get("release") or {}
            return release.get("sourceHash") == source["sourceHash"]
        item = evidence.get(source["sourceId"])
        if not item or item.lifecycle_state != "active":
            return False
        eligibility = assess_news_eligibility(item.raw_payload, title=item.title, summary=item.summary,
            symbol=item.symbol, source=item.source, url=item.url, lifecycle_state=item.lifecycle_state) if item.kind == "news" else None
        if eligibility and not eligibility.alert.eligible:
            return False
        brief = build_information_brief(item, eligibility=eligibility.to_dict() if eligibility else None)
        return brief.get("state") == "source-linked" and brief.get("sourceHash") == source["sourceHash"]

    def write_notifications(connection, row, event, phases):
        delivery = list(row.get("delivery") or [])
        seen = set()
        for account_id in row["source"].get("accountIds", []):
            account = recipients.get(account_id)
            if not account:
                continue
            route = (account.notify_provider, account.telegram_bot_token, account.telegram_chat_id)
            if route in seen:
                continue
            seen.add(route)
            for phase in phases:
                text = information_update_message(row, phase)
                scope = row["source"].get("notificationScope") or row["sourceId"]
                dedupe = "information:" + hashlib.sha256((scope + ":" + phase + ":" + account_id).encode()).hexdigest()
                context = {"messageType": "informationUpdate", "body": text, "title": row["source"]["title"],
                    "symbol": (row["source"].get("symbols") or [""])[0], "informationTrackingId": row["trackingId"],
                    "informationPhase": phase, "decisionAuthority": False, "notificationAiSkip": True,
                    "sourceKind": row["sourceKind"], "sourceId": row["sourceId"], "sourceHash": row["sourceHash"],
                    "notificationSignals": ["newInformation"], "informationObservation": row.get("marketReaction") or {}}
                job = NotificationJob.create(text, account_id=account_id, account_label=account.label,
                    message_type="informationUpdate", dedupe_key=dedupe, source_event_id=event.event_id, source_event_name=event.name, context=context)
                job.job_id = hashlib.sha256(dedupe.encode()).hexdigest()[:32]
                accepted = queue.enqueue_with_connection(connection, job)
                delivery.append({"phase": phase, "accountId": account_id, "jobId": job.job_id,
                                 "state": "queued" if accepted else "suppressed", "reason": job.last_error})
        return delivery

    return InformationFollowupService(stores.information_followup_store(settings, write_notifications), sources,
        InformationObservationService(stores.market_time_series_store(settings)), settings, validate_source=validate_source)
