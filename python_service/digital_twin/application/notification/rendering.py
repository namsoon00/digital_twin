"""Notification document rendering orchestration."""

import html
import hashlib
import re
from datetime import datetime, timezone
from typing import Callable, Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ...domain.context_observation_notifications import (
    is_typedb_context_observation_notification,
    typedb_context_observation_contract,
)
from ...domain.message_types import INVESTMENT_INSIGHT
from ...domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from ...domain.notification_ai_gate_validation import local_validated_ai_response
from ...domain.notification_explanation import INVESTMENT_NOTIFICATION_PRESENTATION_VERSION
from ...domain.notification_narrative import (
    apply_narrative_brief_to_response,
    build_investment_narrative_brief,
    narrative_fingerprint,
)
from ...domain.notifications import NotificationJob, notification_debug_number
from ..notification_ai_gate_message import execution_telegram_message, prepend_execution_start_badge


class NotificationRenderingService:
    """Prepare send-time context and render the exact customer artifact."""

    def __init__(
        self,
        template_renderer: Callable = None,
        context_enricher: Callable = None,
        now_provider: Callable = None,
        link_base_resolver: Callable = None,
    ):
        self.template_renderer = template_renderer
        self.context_enricher = context_enricher
        self.now_provider = now_provider or (lambda: datetime.now(ZoneInfo("UTC")))
        self.link_base_resolver = link_base_resolver

    def render(self, job: NotificationJob) -> str:
        self.apply_send_time_context(job)
        if bool((job.context or {}).get("notificationReplayPreserveOriginal")):
            rendered = str(job.text or "").strip()
            context = dict(job.context or {})
            context["notificationPresentationAudit"] = {
                "version": "notification-replay-preserved-v1",
                "detailLevel": "archived-original",
                "renderedBytes": len(rendered.encode("utf-8")),
                "renderedSha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "replaySourceJobId": str(context.get("replaySourceJobId") or ""),
                "originalBodyPreserved": True,
            }
            job.context = context
            job.text = rendered
            return rendered
        if self.context_enricher:
            self.context_enricher(job)
        self.apply_investment_presentation_contract(job)
        rendered = (
            str(self.template_renderer(job) or "").strip()
            if self.template_renderer
            else job.text.strip()
        )
        if rendered:
            job.text = rendered
            context = dict(job.context or {})
            is_investment = str(job.message_type or "") == INVESTMENT_INSIGHT
            context["notificationPresentationAudit"] = {
                "version": (
                    INVESTMENT_NOTIFICATION_PRESENTATION_VERSION
                    if is_investment
                    else "notification-presentation-v2"
                ),
                "detailLevel": str(
                    context.get("notificationDetailLevel")
                    or ("concise" if is_investment else "full")
                ),
                "decisionContractVersion": str(
                    ((context.get("notificationAiPromptAudit") or {}).get("promptRelease") or {}).get("contractVersion")
                    or ""
                ),
                "promptVersion": str(
                    ((context.get("notificationAiExecutionAudit") or {}).get("promptRelease") or {}).get("version")
                    or (context.get("notificationAiExecutionAudit") or {}).get("promptVersion")
                    or ""
                ),
                "renderedBytes": len(rendered.encode("utf-8")),
                "renderedSha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "detailUrl": str(context.get("notificationDetailUrl") or ""),
                "writerProvenance": dict(context.get("notificationWriterProvenance") or {}),
                "claimValidation": dict(context.get("notificationClaimValidation") or {}),
                "narrativeVersion": str(
                    (context.get("notificationNarrativeBrief") or {}).get("version") or ""
                ),
                "narrativeFingerprint": str(
                    (context.get("notificationNarrativeBrief") or {}).get("fingerprint") or ""
                ),
            }
            job.context = context
        return rendered

    @staticmethod
    def apply_investment_presentation_contract(job: NotificationJob) -> None:
        """Render every investment insight through one versioned document contract."""

        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return
        context = dict(job.context or {})
        context.setdefault("messageType", INVESTMENT_INSIGHT)
        context.setdefault("notificationDetailLevel", "concise")
        narrative_payload = (
            dict(context.get("notificationNarrativeBrief") or {})
            if isinstance(context.get("notificationNarrativeBrief"), dict)
            else {}
        )
        publication = (
            dict(context.get("notificationNarrativePublication") or {})
            if isinstance(context.get("notificationNarrativePublication"), dict)
            else {}
        )
        stored_contract = bool(
            narrative_payload.get("version")
            and publication.get("version") == "investment-narrative-publication-v1"
            and isinstance(narrative_payload.get("claims"), list)
        )
        validated = context.get("notificationAiValidatedResponse")
        if stored_contract and not isinstance(validated, dict):
            validated = context.get("notificationInferenceResponse") or context.get("validatedDecisionResponse")
        if isinstance(validated, dict) and validated:
            response = NotificationAIValidatedResponse.from_dict(validated)
        else:
            observation = typedb_context_observation_contract(context)
            response = local_validated_ai_response(
                context,
                source=(
                    "TypeDB context observation"
                    if observation
                    else "TypeDB deterministic presentation"
                ),
            )
            if observation:
                label = str(observation.get("selectedRuleLabel") or "참고 관계").strip()
                response.investment_view = (
                    "TypeDB가 '" + label
                    + "' 관계를 참고 신호로 확인했습니다. 이 알림 자체는 매수·매도 판단이 아닙니다."
                )
                response.summary = "TypeDB 참고 관계가 새로 확인됐습니다. 투자 행동은 변경하지 않습니다."
                response.current_action_plan = "현재 주문 행동은 변경하지 않고 관계의 다음 변화를 관찰합니다."
                response.execution_decision = response.current_action_plan
                response.hypotheses = []
                response.selected_hypothesis_id = ""
                response.hypothesis_comparison_state = "not-required"
                response.hypothesis_selection_source = "not-required"
                response.decision_abstention = {}
        if stored_contract:
            writer = dict(
                context.get("notificationWriterProvenance")
                or narrative_payload.get("writerProvenance")
                or {}
            )
        else:
            narrative = build_investment_narrative_brief(context, response)
            apply_narrative_brief_to_response(narrative, response)
            narrative_payload = narrative.to_dict()
            narrative_payload["fingerprint"] = narrative_fingerprint(narrative_payload)
            publication = narrative.publication.to_dict()
            writer = dict(narrative.writer_provenance)
        writer_kind = str(writer.get("writerKind") or "deterministic")
        mode = (
            "typedb-context-observation"
            if typedb_context_observation_contract(context)
            else writer_kind + "-evidence-narrative"
        )
        context.update({
            "validatedDecisionResponse": response.to_dict(),
            "notificationNarrativeBrief": narrative_payload,
            "notificationNarrativePublication": publication,
            "notificationWriterProvenance": writer,
            "notificationClaimValidation": dict(response.claim_validation or {}),
        })
        if bool(writer.get("aiAuthored")):
            context["notificationAiValidatedResponse"] = response.to_dict()
        else:
            context.pop("notificationAiValidatedResponse", None)
            context["notificationInferenceResponse"] = response.to_dict()
        rendered = prepend_execution_start_badge(
            execution_telegram_message(context, response),
            context,
        )
        context.update({
            "telegramMessage": rendered,
            "readableMessage": html.unescape(re.sub(r"<[^>]+>", "", rendered)),
            "notificationPresentationContractVersion": INVESTMENT_NOTIFICATION_PRESENTATION_VERSION,
            "notificationPresentationMode": mode,
        })
        job.context = context

    def apply_send_time_context(self, job: NotificationJob) -> None:
        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(ZoneInfo("UTC"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("UTC"))
        sent_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        sent_time = now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
        context = dict(job.context or {})
        base_url = str(context.get("notifyLinkUrl") or "").strip()
        if self.link_base_resolver:
            try:
                base_url = str(self.link_base_resolver(base_url) or base_url).strip()
            except Exception:  # noqa: BLE001 - a link override must not block notification delivery.
                pass
        detail_url = ""
        if base_url:
            parts = urlsplit(base_url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query.update({
                "tab": "notifications",
                "notification": "decisions",
                "detail": "notification-job",
                "detailKey": str(job.job_id or ""),
            })
            detail_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        context.update({
            "jobId": job.job_id,
            "notificationNumber": notification_debug_number(job.job_id),
            "sentAt": sent_at,
            "sentTime": sent_time,
            "sentLine": "발송시각 " + sent_time,
            "notificationDetailUrl": detail_url,
        })
        job.context = context

    @staticmethod
    def append_holding_timing_sent_time(context: Dict[str, object], sent_time: str) -> None:
        plain_line = "발송시각 " + sent_time
        rich_line = "• <b>발송시각</b>: <code>" + sent_time + "</code>"
        raw_lines = str(context.get("rawLines") or "")
        if "발송시각" not in raw_lines:
            context["rawLines"] = "\n".join(part for part in [raw_lines, plain_line] if str(part or "").strip())
        telegram_data = str(context.get("telegramDataLines") or "")
        if "발송시각" not in telegram_data:
            context["telegramDataLines"] = "\n".join(part for part in [telegram_data, rich_line] if str(part or "").strip())
        telegram_message = str(context.get("telegramMessage") or "")
        if telegram_message and "발송시각" not in telegram_message:
            marker = "\n\n<b>발송 기준</b>"
            context["telegramMessage"] = (
                telegram_message.replace(marker, "\n" + rich_line + marker, 1)
                if marker in telegram_message
                else telegram_message + "\n" + rich_line
            )
        readable_message = str(context.get("readableMessage") or "")
        if readable_message and "발송시각" not in readable_message:
            plain_bullet = "• 발송시각: " + sent_time
            marker = "\n\n발송 기준"
            context["readableMessage"] = (
                readable_message.replace(marker, "\n" + plain_bullet + marker, 1)
                if marker in readable_message
                else readable_message + "\n" + plain_bullet
            )
