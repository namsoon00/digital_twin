"""Wire central AI to source facts and existing, owned research capabilities."""
import json


def call_observation_model(envelope, configured):
    import os
    import tempfile
    from pathlib import Path
    from digital_twin.modules.model_registry.infrastructure.model_reviewer import codex_process_arguments, background_ai_runtime_dir, run_background_ai_prompt
    from digital_twin.infrastructure.news_ai_analyzer import first_json_object
    from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
    validate_execution_input(envelope)
    with tempfile.TemporaryDirectory(prefix="orbit-observation-schema-") as directory:
        schema = Path(directory) / "response.json"
        schema.write_text(json.dumps(envelope["outputSchema"], ensure_ascii=False))
        os.chmod(schema, 0o600)
        command = codex_process_arguments("high", background_ai_runtime_dir(), output_schema_path=schema)
        completed = run_background_ai_prompt(command, envelope["prompt"], 240, configured)
        return first_json_object(completed.stdout)


def ai_control_subjects(configured):
    from digital_twin.infrastructure.operational_store import monitor_store
    from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state
    from digital_twin.modules.reasoning.contracts import world_from_snapshot
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
                    "name": position.name, "worldId": world.world_id}
    return list(result.values())


def build_ai_control_service(settings=None):
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.infrastructure.operational_store import investment_research_store
    from digital_twin.infrastructure.typedb_ontology import typedb_repository_from_settings
    from digital_twin.infrastructure.composition.news_intelligence import build_investment_research_orchestrator
    from digital_twin.modules.decisions.contracts import InvestmentQuestion
    from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget
    from digital_twin.modules.ai_orchestration.public import AIControlService
    from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
    from digital_twin.modules.ai_orchestration.infrastructure.execution import CURRENT_TASK, CURRENT_INPUT

    configured = dict(settings if settings is not None else runtime_settings())
    store = MySQLAIControlStore(configured)
    research_store = investment_research_store(configured)

    def subjects():
        return ai_control_subjects(configured)

    def evidence(job):
        from digital_twin.modules.ai_orchestration.infrastructure.observation_reader import GraphObservationReader
        return GraphObservationReader(typedb_repository_from_settings(configured))(job)

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
        run_id = "ai-control-" + job["taskId"][:40]
        existing = research_store.get_run(run_id)
        if existing and existing.get("status") not in {"queued", "processing", "failed"}:
            return {"runId": run_id, "status": existing["status"], "reused": True}
        question = InvestmentQuestion.create(job["question"], subject_symbol=job["symbol"],
            subject_name=job["name"], account_id=job["accountId"], source="ai-control")
        task = {"taskId": identity(job["taskId"], "sources"), "question": job["question"],
            "purpose": "독립 관찰에서 제기한 가설의 근거와 반대 근거 확인", "status": "blocked-by-data",
            "requiredEvidenceTypes": ["official-filing", "news"], "sourceTypes": ["official-filing", "news"],
            "maxAgeMinutes": 1440, "decisionRelevance": "supporting"}
        orchestrator = build_investment_research_orchestrator(configured, research_store)
        # The central planner already owns this plan. Preserve the existing source verification,
        # durable ResearchRun and evidence-change events, without a second planning model call.
        orchestrator.hypothesis_research_planner = None
        token = CURRENT_TASK.set(job["taskId"])
        try:
            run = orchestrator.run(question, NewsCollectionTarget(job["symbol"], job["name"]),
                {"researchPlan": {"planId": job["taskId"], "tasks": [task], "unresolvedQuestions": [job["question"]]}},
                account_id=job["accountId"], run_id=run_id,
                request_context={"aiControlTaskId": job["taskId"], "source": "ai-control", "question": question.to_dict()})
        finally:
            CURRENT_TASK.reset(token)
        return {"runId": run.run_id, "status": run.status, "changedEvidenceCount": run.changed_evidence_count,
                "stopReason": run.stop_reason, "authority": "research-only"}

    from digital_twin.infrastructure.transactions.ai_control_publication import AIControlPublication
    from digital_twin.infrastructure.operational_store import notification_job_store
    publication = AIControlPublication(configured, store, notification_job_store(configured), subjects)
    publication.retire_legacy_work()
    store.outbox_writer = publication.publish
    return AIControlService(store, subjects, evidence, planner, researcher, research_memory, configured,
                            delivery_memory=publication.memory, reviewer=planner)


def ai_control_status(settings=None, account_id=""):
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
    from digital_twin.modules.ai_orchestration.contracts import CAPABILITIES, enabled
    configured = settings if settings is not None else runtime_settings()
    status = MySQLAIControlStore(configured).status(account_id)
    from digital_twin.infrastructure.operational_store import notification_job_store
    publications = [task["result"].get("publication") for task in status["tasks"] if task["result"].get("publication", {}).get("jobId")]
    if publications:
        with notification_job_store(configured).connect() as connection:
            ids = [publication["jobId"] for publication in publications]
            rows = connection.execute("SELECT job_id,status,last_error FROM notification_jobs WHERE job_id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids)).fetchall()
            receipts = connection.execute("SELECT job_id,completed_at,JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.renderedMessage')) AS body "
                "FROM notification_delivery_attempts WHERE status='delivered' AND job_id IN (" + ",".join(["%s"] * len(ids)) + ") "
                "ORDER BY completed_at DESC", tuple(ids)).fetchall()
        deliveries = {row["job_id"]: row for row in rows}
        delivered = {}
        for receipt in receipts:
            delivered.setdefault(receipt["job_id"], {"deliveredAt": receipt["completed_at"], "body": receipt["body"] or "", "source": "transport-receipt"})
        for publication in publications:
            delivery = deliveries.get(publication["jobId"], {})
            publication["deliveryStatus"] = delivery.get("status", "unknown")
            if publication["jobId"] in delivered:
                publication["receipt"] = delivered[publication["jobId"]]
            if delivery.get("last_error"):
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
    if not isinstance(payload, dict) or set(payload) - {"aiControlEnabled", "aiControlBudgetEnabled", "aiControlDailyTaskBudget", "aiControlDailyCallBudget"}:
        raise ValueError("지원하지 않는 중앙 AI 설정입니다.")
    for key, maximum in (("aiControlDailyTaskBudget", 200), ("aiControlDailyCallBudget", 300)):
        if key in payload and not 0 <= int(payload[key]) <= maximum:
            raise ValueError("작업 또는 호출 한도를 확인하세요.")
    for key in ("aiControlEnabled", "aiControlBudgetEnabled"):
        if key in payload and payload[key] not in {"true", "false"}:
            raise ValueError("관찰 또는 한도 사용 여부를 확인하세요.")
    save_runtime_settings(payload)
    return {"saved": True}
