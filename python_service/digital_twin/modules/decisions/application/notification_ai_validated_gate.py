from typing import Dict
from digital_twin.domain.context_observation_notifications import is_typedb_context_observation_notification
from digital_twin.domain.investment_brain import decision_episode_from_context
from digital_twin.domain.investment_flow import INVESTMENT_FLOW_VERSION, investment_flow_id
from digital_twin.domain.message_types import INVESTMENT_INSIGHT
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse, ai_gate_enabled_for_message_type
from digital_twin.domain.notification_ai_gate_validation import local_validated_ai_response
from digital_twin.domain.notification_ai_decision_brief import AI_DECISION_CONTRACT_VERSION, AI_DECISION_PROMPT_VERSION, notification_ai_execution_profile
from digital_twin.domain.notifications import NotificationJob
from digital_twin.modules.decisions.public import context_with_validated_ai_response
from digital_twin.modules.decisions.public import NotificationAIContractError, NotificationAIJudgementService
from digital_twin.modules.decisions.public import context_with_previous_investment_decision
from digital_twin.modules.notifications.public import apply_ontology_quality_gate_to_response, ontology_quality_gate_context


class NotificationAIValidatedGateEnricher:
    def __init__(self, reviewer=None, settings: Dict[str, object] = None, decision_episode_store=None):
        self.reviewer = reviewer
        self.settings = settings or {}
        self.decision_episode_store = decision_episode_store
        self.judgement_service = (
            NotificationAIJudgementService(
                reviewer,
                self.settings,
                max_prompt_bytes=int(self.settings.get("notificationAiQueueMaxPromptBytes") or 24 * 1024),
                repair_reasoning_effort=str(
                    self.settings.get("notificationAiComparisonRepairReasoningEffort") or "max"
                ),
                repair_timeout_seconds=int(
                    self.settings.get("notificationAiComparisonRepairTimeoutSeconds") or 0
                ),
                enforce_contract_for_typed_response=False,
            )
            if reviewer
            else None
        )

    def __call__(self, job: NotificationJob) -> None:
        if not ai_gate_enabled_for_message_type(job.message_type, self.settings):
            return
        context = dict(job.context or {})
        if is_typedb_context_observation_notification(context):
            return
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        context.setdefault("jobId", job.job_id)
        narrative = context.get("notificationNarrativeBrief")
        publication = context.get("notificationNarrativePublication")
        has_canonical_publication = bool(
            isinstance(narrative, dict)
            and narrative.get("version")
            and isinstance(narrative.get("claims"), list)
            and isinstance(publication, dict)
            and publication.get("version") == "investment-narrative-publication-v1"
        )
        if has_canonical_publication and isinstance(
            context.get("notificationAiValidatedResponse"), dict
        ):
            # AI completion is the publication boundary. Revalidating at send
            # time changes the evidence ledger and can silently replace an
            # accepted AI narrative with a deterministic fallback.
            job.context = context
            return
        if job.message_type == INVESTMENT_INSIGHT:
            context = context_with_previous_investment_decision(
                context,
                self.decision_episode_store,
                account_id=job.account_id,
            )
        quality_gate = ontology_quality_gate_context(context, self.settings)
        context["ontologyQualityGate"] = quality_gate
        if context.get("notificationAiValidatedResponse"):
            # Legacy jobs predate the immutable narrative publication contract.
            response = NotificationAIValidatedResponse.from_dict(context.get("notificationAiValidatedResponse"))
            job.context = context_with_validated_ai_response(context, response, self.settings)
            return
        try:
            if self.judgement_service:
                context["notificationAiDecisionContractVersion"] = AI_DECISION_CONTRACT_VERSION
                profile = notification_ai_execution_profile(context, self.settings)
                context["notificationAiExecutionProfile"] = profile
                outcome = self.judgement_service.judge(context, profile=profile)
                if not outcome.publishable:
                    raise NotificationAIContractError(
                        outcome.final_contract_error
                        or outcome.final_publication_error
                        or outcome.repair_error
                    )
                response = outcome.response
                context["_notificationAiInferencePacket"] = outcome.packet.to_audit_dict()
                context["notificationAiExecutionAudit"] = {
                    "version": "notification-ai-execution-audit-v2",
                    "status": "completed",
                    "promptVersion": AI_DECISION_PROMPT_VERSION,
                    "inferencePacket": outcome.packet.to_audit_dict(),
                    "promptHash": outcome.executed_prompt_hash,
                    "promptBytes": outcome.executed_prompt_bytes,
                    "executionProfile": profile,
                    "claimPublication": {
                        "status": str((response.claim_validation or {}).get("status") or "unavailable"),
                        "verifiedClaimCount": response.verified_claim_count,
                        "rejectedClaimCount": response.rejected_claim_count,
                        "sections": sorted(response.verified_claim_sections),
                    },
                    "contractRepair": outcome.audit_dict().get("repair") or {},
                }
            else:
                response = local_validated_ai_response(context, source="TypeDB inference fallback")
        except Exception as error:  # noqa: BLE001 - notification delivery should degrade to local validation.
            response = local_validated_ai_response(context, source="TypeDB inference fallback")
            response.validation_warnings.append("AI 검증 실패로 TypeDB 해석을 사용했습니다: " + str(error)[:140])
        apply_ontology_quality_gate_to_response(response, quality_gate)
        if (
            self.decision_episode_store
            and job.message_type == INVESTMENT_INSIGHT
            and not context.get("investmentSubjectDecisionCaseId")
        ):
            try:
                relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
                subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
                facts = dict(relation_context.get("facts") or {})
                facts["inferenceGenerationId"] = relation_context.get("inferenceGenerationId") or ""
                self.decision_episode_store.record_observation(
                    job.account_id,
                    str(subject.get("symbol") or ""),
                    facts,
                    str(relation_context.get("inferenceGenerationAt") or context.get("referenceDate") or ""),
                )
                episode = decision_episode_from_context(context, response.to_dict(), job_id=job.job_id)
                if episode:
                    self.decision_episode_store.save(episode)
                    previous = (
                        context.get("previousInvestmentDecisionEpisode")
                        if isinstance(context.get("previousInvestmentDecisionEpisode"), dict)
                        else {}
                    )
                    previous_action = str(previous.get("action") or "").strip()
                    previous_validation = str(previous.get("validationState") or "").strip()
                    current_action = str(episode.action or "").strip()
                    current_validation = str(episode.validation_state or "").strip()
                    context["investmentDecisionEpisodeId"] = episode.episode_id
                    context["investmentDecisionEpisode"] = episode.to_dict()
                    context["investmentFlow"] = {
                        "version": INVESTMENT_FLOW_VERSION,
                        "flowId": investment_flow_id(episode.account_id, episode.symbol, episode.episode_id),
                        "episodeId": episode.episode_id,
                        "previousAction": previous_action,
                        "currentAction": current_action,
                        "decisionChanged": bool(previous_action and previous_action != current_action),
                        "previousValidationState": previous_validation,
                        "currentValidationState": current_validation,
                        "validationChanged": bool(previous_validation and previous_validation != current_validation),
                    }
            except Exception as error:  # noqa: BLE001 - memory persistence must not block a time-sensitive alert.
                response.validation_warnings.append("투자 판단 기억 저장 실패: " + str(error)[:140])
        job.context = context_with_validated_ai_response(context, response, self.settings)
