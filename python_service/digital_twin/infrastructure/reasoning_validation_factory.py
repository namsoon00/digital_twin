"""Local-only composition for paired current-source validation ingress."""


def build_paired_reasoning_validation_service(settings=None):
    from digital_twin.infrastructure.settings import runtime_settings, utc_now
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
    from digital_twin.modules.reasoning.public import PairedReasoningValidationService
    from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state
    from digital_twin.modules.reasoning.domain.reasoning_source_snapshot import reasoning_source_snapshot_id

    configured = dict(settings or runtime_settings(fast_operational_read=True))
    configured.update(_skipOperationalSchemaBootstrap="1", _skipOperationalHistoryRetention="1")
    registry = stores.reasoning_engine_registry_store(configured)
    monitor = stores.ontology_reasoning_monitor_store(configured)
    jobs = stores.reasoning_engine_job_store(configured)

    def read_snapshots(account_id, symbols):
        for account in stores.account_reader(configured).load() or []:
            if account_id and account.account_id != account_id:
                continue
            metadata = monitor.snapshot_metadata(account.account_id) or {}
            source_id = reasoning_source_snapshot_id(account.account_id, metadata.get("generatedAt"))
            state = monitor.reasoning_source_snapshot_state(source_id, target_symbols=symbols)
            snapshot = account_snapshot_from_monitor_state(state or {})
            if snapshot is not None and snapshot.has_live_account_data():
                yield snapshot

    def record(event):
        with jobs.transaction() as connection:
            connection.execute("SELECT control_id FROM reasoning_engine_control WHERE control_id = 'global' FOR UPDATE").fetchone()
            pair = dict(event.payload.get("validationPair") or {})
            targets = jobs.target_deployments_with_connection(connection)
            if sorted(targets) != sorted([pair.get("baselineDeploymentId"), pair.get("candidateDeploymentId")]):
                return {"saved": False, "status": "control-pair-changed"}
            insert_domain_event_with_connection(connection, event)
            return jobs.ingress_event_with_connection(connection, event)

    return PairedReasoningValidationService(registry, read_snapshots, record, utc_now)
