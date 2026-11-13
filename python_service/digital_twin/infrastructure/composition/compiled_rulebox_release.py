"""Approved authoring changes -> validated immutable compiled releases."""

from copy import deepcopy
import time

def compiled_rulebox_runtime_status(settings, authoring_snapshot):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.modules.reasoning.domain.compiled_rulebox import COMPILED_RULEBOX_VERSION
    configured = {**settings, '_skipOperationalSchemaBootstrap':'1', '_skipOperationalHistoryRetention':'1'}
    registry = stores.reasoning_engine_registry_store(configured)
    control = registry.control()
    active = registry.get(control.active_deployment_id) or {}
    versions = (active.get('releaseBundle') or {}).get('source_contract_versions') or []
    if COMPILED_RULEBOX_VERSION not in versions:
        return {'status':'native', 'deploymentId':control.active_deployment_id}
    from digital_twin.modules.model_registry.contracts import GraphInferenceRule, rulebox_rules_hash
    stored = registry.release_artifact(control.active_deployment_id)
    if not stored.get('valid'):
        return {'status':'unavailable', 'backend':COMPILED_RULEBOX_VERSION, 'reason':'운영 규칙 원본을 확인하지 못했습니다.'}
    def digest(rows):
        return rulebox_rules_hash([GraphInferenceRule.from_dict(row).to_dict() for row in rows])
    same = digest(authoring_snapshot.get('rules') or []) == digest(stored['artifact'].get('rules') or [])
    return {'status':'current' if same else 'not-applied', 'backend':COMPILED_RULEBOX_VERSION,
            'deploymentId':control.active_deployment_id, 'candidateDeploymentId':control.candidate_deployment_id,
            'reason':'' if same else '저장된 규칙과 운영 규칙이 다릅니다. 다시 저장해 검증·반영 결과를 확인하세요.'}


def validate_compiled_release(settings, deployment_id, baseline_id, program, initial_cutover, native_parity=None):
    from digital_twin.modules.reasoning.application.independent_reasoning_engine import projection_inference_identity
    from digital_twin.modules.reasoning.domain.independent_reasoning import IndependentReasoningRequest
    from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state
    from digital_twin.infrastructure.reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource
    from digital_twin.infrastructure import operational_store as stores
    if initial_cutover:
        parity = native_parity or {}
        if (parity.get('status') != 'passed' or parity.get('programFingerprint') != program.fingerprint
                or set(parity.get('testedRuleIds') or []) != set(program.rule_order)
                or parity.get('operationalGraphMutated') is not False or parity.get('deliveryCount') != 0):
            raise ValueError('Initial cutover requires exact-program native differential evidence')
    from .reasoning import build_v2_reasoning_engine
    engine = build_v2_reasoning_engine(settings, deployment_id=deployment_id, persistent_graph_cache_enabled=False)
    recorder = engine.inference_executor.projection_recorder
    monitor = stores.ontology_reasoning_monitor_store(settings)
    source = LatestMonitorSnapshotReasoningSource(monitor, settings)
    reports = []
    # Evaluate the frozen live inputs without invoking the execution orchestrator,
    # lifecycle writes, delivery cycle or notification transport.
    for account in stores.account_reader(settings).load_all():
        snapshot = account_snapshot_from_monitor_state(source.current_state(account.account_id))
        if snapshot is None:
            continue
        symbols = sorted({p.symbol for p in [*snapshot.positions, *snapshot.watchlist] if not p.is_cash()})
        if not symbols:
            continue
        started = time.perf_counter()
        result = recorder.record_snapshot(snapshot, target_symbols=symbols,
            reasoning_context={'validationOnly': True, 'validationPurpose': 'compiled-release-admission'})
        if not projection_inference_identity(result)['verified']:
            raise ValueError('Candidate projection did not preserve a complete durable proof')
        request = IndependentReasoningRequest('compiled-validation:'+deployment_id, deployment_id, (), (),
            (account.account_id,), tuple(symbols), (), 'compiled-release-validation', snapshot.generated_at,
            snapshot.generated_at, 'validation', result['materialFingerprint'], context={'validationOnly': True})
        candidates = engine.candidate_builder.build(request, [snapshot], {}, {account.account_id:result})
        if candidates.get('notificationContractFailures'):
            raise ValueError('Candidate notification evidence contract failed')
        reports.append({'symbolCount':len(symbols), 'ruleCount':len(program.rule_order),
            'traceCount':result['inferenceBox']['traceCount'], 'synthesisCount':len(candidates.get('syntheses') or []),
            'projectionMs':result['runtimeStages']['totalProjectionMs'],
            'ruleEvaluationMs':result['runtimeStages']['compiledRuleEvaluationMs'],
            'totalMs':round((time.perf_counter()-started)*1000),
            'auditRunId':result['projectionAudit']['runId']})
    if not reports:
        raise ValueError('No preserved account input is available for release validation')
    return {'status':'passed', 'programFingerprint':program.fingerprint, 'completeRuleCoverage':True,
            'deliveryCount':0, 'reports':reports, 'baselineDeploymentId':baseline_id,
            'validationMeaning':'execution-and-evidence-contracts; not-predictive-performance',
            **({'nativeParity':deepcopy(native_parity)} if initial_cutover else {})}


def build_compiled_release_service(settings, native_parity=None):
    from digital_twin.modules.reasoning.application.compiled_rulebox_release import CompiledRuleboxReleaseService
    from digital_twin.infrastructure.reasoning_engine_factory import build_reasoning_engine_platform
    configured = {**settings, '_skipOperationalSchemaBootstrap':'1', '_skipOperationalHistoryRetention':'1'}
    platform = build_reasoning_engine_platform(configured)
    def publish(target, phase, details):
        from digital_twin.infrastructure.event_bus import default_event_bus
        from digital_twin.shared_kernel.events import DomainEvent
        default_event_bus().publish(DomainEvent(name='ontology.rulebox_deployment_changed',
            aggregate_id='compiled-rulebox:'+target,
            payload={'operationId':target,'phase':phase,'status':'ready','details':details}))
    def sync(control):
        from digital_twin.infrastructure.settings import save_runtime_settings
        # Keep the authoring TypeDB binding. The control plane chooses the
        # realtime execution artifact independently of this authoring database.
        selected_artifact = platform.registry.release_artifact(control.active_deployment_id)
        authoring = (selected_artifact.get('artifact') or {}).get('ruleboxAuthoringDatabase')
        if not selected_artifact.get('valid') or not authoring:
            raise ValueError('The selected compiled release has no preserved authoring database')
        save_runtime_settings({'reasoningEngineActiveDeploymentId':control.active_deployment_id,
            'reasoningEngineDeliveryDeploymentId':control.delivery_deployment_id,
            'reasoningEngineV2DeploymentId':control.active_deployment_id,
            'reasoningEngineV2TypeDbDatabase':authoring,
            'reasoningEngineCandidateDeploymentId':'', 'reasoningEngineCandidateReleaseId':'',
            'reasoningEngineActiveVersion':'v2'})
    return CompiledRuleboxReleaseService(platform,
        lambda target,baseline,program,initial: validate_compiled_release(configured,target,baseline,program,initial,native_parity), publish, sync)


def compiled_rulebox_authoring_change(settings, repository, phase, rules, context=None):
    """Called only by an explicit RuleBox save/restore, never by realtime reads."""
    from digital_twin.modules.reasoning.domain.compiled_rulebox import COMPILED_RULEBOX_VERSION, compile_rulebox
    service = build_compiled_release_service(settings)
    registry = service.platform.registry
    control = registry.control()
    baseline = registry.release_artifact(control.active_deployment_id)
    artifact = baseline.get('artifact') or {}
    versions = (artifact.get('releaseBundle') or {}).get('source_contract_versions') or []
    if COMPILED_RULEBOX_VERSION not in versions:
        return {'status':'not-applicable'}
    authoring = artifact.get('ruleboxAuthoringDatabase') or settings.get('typedbDatabase')
    if repository.database != authoring:
        return {'status':'not-applicable'}
    if phase == 'preflight':
        program = compile_rulebox(rules, tbox_fingerprint=artifact.get('tboxFingerprint',''))
        if control.candidate_deployment_id:
            raise ValueError('다른 규칙 버전을 검증 중입니다. 해당 변경의 적용 상태를 먼저 확인해 주세요.')
        return {'status':'ready','baselineDeploymentId':control.active_deployment_id,
                'programFingerprint':program.fingerprint}
    if (context or {}).get('baselineDeploymentId') != control.active_deployment_id:
        return {'status':'not-applied','reason':'검증 중 실행 버전이 변경돼 기존 버전을 유지합니다.'}
    from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import ontology_release_seed_artifact
    from digital_twin.modules.model_registry.contracts import investment_language_registry
    successor = ontology_release_seed_artifact(rules, investment_language_registry(settings), repository.active_tbox_metadata())
    successor['ruleboxAuthoringDatabase'] = authoring
    return service.apply(successor, expected_baseline=control.active_deployment_id)
