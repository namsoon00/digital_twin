"""Publish an approved RuleBox as one immutable, atomically selected program."""

from uuid import uuid4
from digital_twin.modules.reasoning.domain.compiled_rulebox import COMPILED_RULEBOX_VERSION, compile_rulebox


class CompiledRuleboxReleaseService:
    def __init__(self, platform, validate_release, publish_event, synchronize_settings=None):
        self.platform = platform
        self.validate_release = validate_release
        self.publish_event = publish_event
        self.synchronize_settings = synchronize_settings or (lambda _control: None)

    def apply(self, artifact, *, expected_baseline, deployment_id="", initial_cutover=False):
        registry = self.platform.registry
        control = registry.control()
        if control.active_deployment_id != expected_baseline or control.delivery_deployment_id != expected_baseline:
            return {"status": "blocked", "reasonCode": "release-ownership-changed"}
        baseline = registry.release_artifact(expected_baseline)
        if not baseline.get("valid"):
            return {"status": "blocked", "reasonCode": "baseline-artifact-unavailable"}
        bundle = baseline["artifact"].get("releaseBundle") or {}
        versions = bundle.get("source_contract_versions") or bundle.get("sourceContractVersions") or []
        if COMPILED_RULEBOX_VERSION not in versions and not initial_cutover:
            return {"status": "blocked", "reasonCode": "explicit-initial-cutover-required"}
        program = compile_rulebox(artifact.get("rules") or [], tbox_fingerprint=artifact.get("tboxFingerprint", ""))
        if (not initial_cutover and artifact.get("ruleboxFingerprint") == baseline["artifact"].get("ruleboxFingerprint")
                and artifact.get("tboxFingerprint") == baseline["artifact"].get("tboxFingerprint")):
            return {"status": "unchanged", "backend": COMPILED_RULEBOX_VERSION,
                    "deploymentId": expected_baseline, "programFingerprint": program.fingerprint}
        target = deployment_id or "rulebox-compiled-" + program.fingerprint[:12] + "-" + uuid4().hex[:8]
        if control.candidate_deployment_id and control.candidate_deployment_id != target:
            return {"status": "blocked", "reasonCode": "another-release-is-being-validated"}
        result = self.platform.register_v2_release(target, "compiled-" + program.fingerprint[:24],
            graph_database="compiled_" + program.fingerprint[:24], release_seed_artifact=artifact,
            expected_baseline_deployment_id=expected_baseline, rule_execution_backend=COMPILED_RULEBOX_VERSION)
        if result.get("status") != "registered":
            return result
        try:
            validation = self.validate_release(target, expected_baseline, program, initial_cutover)
            if (validation.get("status") != "passed" or validation.get("deliveryCount") != 0
                    or validation.get("programFingerprint") != program.fingerprint
                    or validation.get("completeRuleCoverage") is not True):
                raise ValueError("Compiled release validation is incomplete: " + str(validation.get("reason") or validation.get("status")))
            current = registry.control()
            if current.active_deployment_id != expected_baseline or current.candidate_deployment_id != target:
                raise RuntimeError("Release ownership changed during validation")
            self.publish_event(target, "validated", {"baseline": expected_baseline, "validation": validation})
            receipt = {"version": COMPILED_RULEBOX_VERSION, "programFingerprint": program.fingerprint,
                       "sourceRuleboxFingerprint": artifact["ruleboxFingerprint"], "validation": validation,
                       "previousDeploymentId": expected_baseline, "source": "approved-rulebox-change"}
            selected = registry.switch_compiled_release(expected_baseline, target, receipt, expected_version=current.version)
        except Exception as error:
            # A lost commit response is not proof of rollback. Resolve the
            # authoritative owner before reporting or clearing our candidate.
            try:
                selected = registry.control()
            except Exception:
                return {"status":"verification-pending", "deploymentId":target,
                        "reason":"규칙 전환 결과를 확인하지 못했습니다. 운영 버전 상태를 다시 확인하세요."}
            if selected.active_deployment_id != target or selected.delivery_deployment_id != target:
                registry.abandon_compiled_candidate(expected_baseline, target, {
                    "status": "not-applied", "reason": str(error)[:500], "programFingerprint": program.fingerprint})
                return {"status": "not-applied", "deploymentId": target, "reason": str(error)[:500],
                        "activeDeploymentId": selected.active_deployment_id}
        warnings = []
        try:
            self.platform.synchronize_control_capabilities(selected)
        except Exception as error:
            warnings.append("capability-sync: " + str(error)[:200])
        settings_warning = ""
        try:
            self.synchronize_settings(selected)
        except Exception as error:
            settings_warning = str(error)[:300]
        try:
            cleanup = self.platform.supersede_inactive_pending_work(expected_baseline)
        except Exception as error:
            cleanup = {"status":"pending", "reason":str(error)[:200]}
            warnings.append("old-queue-cleanup-pending")
        return {"status": "applied", "backend": COMPILED_RULEBOX_VERSION, "deploymentId": target, "control": selected.to_dict(),
                "programFingerprint": program.fingerprint, "validation": validation, "queueCleanup": cleanup,
                "settingsSyncWarning": settings_warning, "postCommitWarnings":warnings}
