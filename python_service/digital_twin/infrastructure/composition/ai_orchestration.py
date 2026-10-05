"""Wire central AI to source facts and existing, owned research capabilities."""
import json


def call_observation_model(envelope, configured):
    from digital_twin.modules.model_registry.infrastructure.model_reviewer import codex_process_arguments, background_ai_runtime_dir, run_background_ai_prompt
    from digital_twin.infrastructure.news_ai_analyzer import first_json_object
    from digital_twin.modules.ai_orchestration.infrastructure.observation_model import StructuredObservationModel
    adapter = StructuredObservationModel(
        lambda schema: codex_process_arguments("high", background_ai_runtime_dir(), output_schema_path=schema),
        run_background_ai_prompt, first_json_object)
    return adapter(envelope, configured)


def ai_control_subjects(configured):
    from digital_twin.infrastructure.operational_store import monitor_store
    from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state
    from digital_twin.modules.reasoning.contracts import world_from_snapshot, market_world
    from digital_twin.modules.accounts.infrastructure.mysql_watchlist_account_reader import MySQLWatchlistAccountReader
    accounts = {account.account_id: account for account in MySQLWatchlistAccountReader(configured).load_all() if account.enabled}
    result = {}
    for account_id, state in monitor_store(configured).load_previous().items():
        if account_id not in accounts:
            continue
        snapshot = account_snapshot_from_monitor_state(state)
        if not snapshot or not snapshot.has_live_account_data():
            continue
        world = world_from_snapshot(snapshot, configured)
        watchlist = [position for position in snapshot.watchlist if position.symbol in accounts[account_id].watchlist_symbols]
        for position in snapshot.positions + watchlist:
            symbol = str(position.symbol or "").upper().strip()
            if symbol:
                result[(account_id, symbol)] = {"accountId": account_id, "symbol": symbol,
                    "name": position.name, "worldId": world.world_id,
                    "marketWorldId": market_world(world.market_id, configured.get("ontologySharedMarketTenantId") or "shared").world_id}
    return list(result.values())


def build_ai_control_service(settings=None):
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.infrastructure.operational_store import investment_research_store
    from digital_twin.infrastructure.typedb_ontology import typedb_repository_from_settings
    from digital_twin.infrastructure.composition.news_intelligence import build_investment_research_orchestrator
    from digital_twin.modules.ai_orchestration.public import AIControlService
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
    from digital_twin.modules.ai_orchestration.infrastructure.execution import CURRENT_TASK, CURRENT_INPUT

    configured = dict(settings if settings is not None else runtime_settings())
    store = MySQLAIControlStore(configured)
    research_store = investment_research_store(configured)

    def subjects():
        return ai_control_subjects(configured)

    def evidence(job):
        from digital_twin.modules.ai_orchestration.infrastructure.observation_reader import GraphObservationReader
        return GraphObservationReader(typedb_repository_from_settings(configured),
            macro_world_id=job.get("marketWorldId", "")).capture_session(job)

    def planner(envelope, input_id):
        token = CURRENT_TASK.set(envelope["current"]["taskId"])
        input_token = CURRENT_INPUT.set(input_id)
        try:
            return call_observation_model(envelope, {**configured, "aiWorkload": "independent-observation"})
        finally:
            CURRENT_INPUT.reset(input_token)
            CURRENT_TASK.reset(token)

    def research_memory(account_id, symbol):
        result = []
        for row in research_store.list_runs(account_id=account_id, symbol=symbol, limit=3):
            claims = list(row.get("verifiedClaims") or [])
            result.append({**{key: row.get(key) for key in ("runId", "status", "completedAt", "stopReason")},
                           "verifiedClaimCount": len(claims), "verifiedClaims": claims[:8],
                           "taskAssessments": list(row.get("taskAssessments") or [])[:6]})
        return result

    def researcher(job):
        from digital_twin.modules.ai_orchestration.application.research import execute_research
        token = CURRENT_TASK.set(job["taskId"])
        try:
            return execute_research(job, research_store,
                lambda: build_investment_research_orchestrator(configured, research_store))
        finally:
            CURRENT_TASK.reset(token)

    from digital_twin.infrastructure.transactions.ai_control_publication import AIControlPublication
    from digital_twin.infrastructure.operational_store import notification_job_store
    publication = AIControlPublication(configured, store, notification_job_store(configured), subjects)
    publication.retire_legacy_work()
    store.outbox_writer = publication.publish
    from digital_twin.infrastructure.transactions.ai_control_development import AIControlDevelopment
    from digital_twin.infrastructure.operational_store import hypothesis_development_store
    from digital_twin.modules.news_intelligence.infrastructure.mysql_observation_development import MySQLObservationDevelopmentStore
    development = AIControlDevelopment(MySQLObservationDevelopmentStore(configured), lambda case_id: hypothesis_development_store(configured).get(case_id))
    store.development_writer = development.record
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
    agenda = MySQLBrainAgendaStore(configured)
    store.agenda_writer, store.agenda_failure = agenda.record, agenda.failed
    from digital_twin.infrastructure.transactions.ai_observation_wake import AIObservationEvidenceWake
    evidence_wake = AIObservationEvidenceWake(configured)
    return AIControlService(store, subjects, evidence, planner, researcher, research_memory, configured,
                            delivery_memory=publication.memory, reviewer=planner, development_memory=development.memory,
                            brain_memory=agenda.memory, brain_waker=agenda.wake_due, read_planner=planner,
                            evidence_waker=evidence_wake.run_once, read_round_budget=store.retrieval_round_budget)


def ai_control_status(settings=None, account_id=""):
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
    from digital_twin.modules.ai_orchestration.contracts import CAPABILITIES, enabled
    configured = settings if settings is not None else runtime_settings()
    status = MySQLAIControlStore(configured).status(account_id)
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
    status["brain"] = MySQLBrainAgendaStore(configured).status(account_id)
    from digital_twin.infrastructure.transactions.ai_control_development import AIControlDevelopment
    from digital_twin.infrastructure.operational_store import hypothesis_development_store
    from digital_twin.modules.news_intelligence.infrastructure.mysql_observation_development import MySQLObservationDevelopmentStore
    development = AIControlDevelopment(MySQLObservationDevelopmentStore(configured), lambda case_id: hypothesis_development_store(configured).get(case_id))
    development_records = {}
    for task in status["tasks"]:
        request_id = task["result"].get("development", {}).get("requestId")
        if request_id:
            subject = (task["accountId"], task["symbol"])
            if subject not in development_records:
                development_records[subject] = {row["requestId"]: row for row in development.memory(*subject)}
            task["developmentProgress"] = development_records[subject].get(request_id, {"status": "unavailable"})
    from digital_twin.infrastructure.operational_store import notification_job_store
    publications = []
    for task in status["tasks"]:
        publication = task["result"].get("publication") or {}
        publications.extend(item for item in (publication, publication.get("diagnostic") or {}) if item.get("jobId"))
    if publications:
        import json
        from digital_twin.modules.notifications.contracts import delivery_progress
        from digital_twin.infrastructure.operational_common import MAX_NOTIFICATION_DELIVERY_ATTEMPTS
        with notification_job_store(configured).connect() as connection:
            ids = [publication["jobId"] for publication in publications]
            rows = connection.execute("SELECT job_id,status,last_error,attempts,retry_at,created_at,message_type,"
                "JSON_EXTRACT(payload_json,'$.context.deliveryRecovery') AS recovery,"
                "JSON_EXTRACT(payload_json,'$.context.deliveryRetryAfterSeconds') AS retry_after "
                "FROM notification_jobs WHERE job_id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids)).fetchall()
            receipts = connection.execute("SELECT job_id,completed_at,JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.renderedMessage')) AS body "
                "FROM notification_delivery_attempts WHERE status='delivered' AND job_id IN (" + ",".join(["%s"] * len(ids)) + ") "
                "ORDER BY completed_at DESC", tuple(ids)).fetchall()
        deliveries = {row["job_id"]: row for row in rows}
        delivered = {}
        for receipt in receipts:
            delivered.setdefault(receipt["job_id"], {"deliveredAt": receipt["completed_at"], "body": receipt["body"] or "", "source": "transport-receipt"})
        for publication in publications:
            delivery = deliveries.get(publication["jobId"], {})
            delivery["context"] = {"deliveryRecovery": json.loads(delivery.get("recovery") or "null") or {},
                                   "deliveryRetryAfterSeconds": int(json.loads(delivery.get("retry_after") or "0") or 0)}
            publication.update(delivery_progress(delivery, delivered.get(publication["jobId"]),
                               max_attempts=MAX_NOTIFICATION_DELIVERY_ATTEMPTS))
            if delivery.get("last_error") and not publication.get("receipt"):
                publication["reason"] = delivery["last_error"]
    results = [task["result"] for task in status["tasks"] if task["result"].get("summary")]
    return {"notificationRoute": "ai-control", "legacyInvestmentNotifications": "retired",
            "qualitySummary": {"scope": "최근 관찰 기록", "reviewed": sum(bool(row.get("quality", {}).get("reviewInputId")) for row in results),
                "accepted": sum(row.get("quality", {}).get("status") == "accepted" for row in results),
                "rejected": sum(row.get("quality", {}).get("status") == "rejected" for row in results),
                "valueQualification": "사용자 유용성과 유료 가치는 별도 평가가 필요합니다."},
            "notificationPolicy": {"cooldownMinutes": 180, "invalidationCooldownMinutes": 60, "dailySubjectLimit": 2, "dailyAccountLimit": 8},
            "enabled": enabled(configured),
            "configuredEnabled": str(configured.get("aiControlEnabled", "true")).lower() not in {"false", "0", "off"},
            "capabilities": CAPABILITIES,
            **status}


def save_ai_control_settings(payload):
    from digital_twin.infrastructure.settings import save_runtime_settings
    if not isinstance(payload, dict) or set(payload) - {"aiControlEnabled", "aiControlBudgetEnabled", "aiControlDailyTaskBudget", "aiControlDailyCallBudget", "aiObservationPromptMaxBytes"}:
        raise ValueError("지원하지 않는 중앙 AI 설정입니다.")
    if "aiObservationPromptMaxBytes" in payload and not 65536 <= int(payload["aiObservationPromptMaxBytes"]) <= 524288:
        raise ValueError("관찰 입력 한도는 64–512 KiB 범위여야 합니다.")
    for key, maximum in (("aiControlDailyTaskBudget", 200), ("aiControlDailyCallBudget", 300)):
        if key in payload and not 0 <= int(payload[key]) <= maximum:
            raise ValueError("작업 또는 호출 한도를 확인하세요.")
    for key in ("aiControlEnabled", "aiControlBudgetEnabled"):
        if key in payload and payload[key] not in {"true", "false"}:
            raise ValueError("관찰 또는 한도 사용 여부를 확인하세요.")
    save_runtime_settings(payload)
    return {"saved": True}


def review_ai_service_feedback(payload):
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
    return MySQLBrainAgendaStore(runtime_settings()).review_feedback(payload)
