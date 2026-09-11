"""Notification document rendering orchestration."""

import html
import hashlib
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from digital_twin.domain.context_observation_notifications import typedb_context_observation_contract, typedb_narrative_only_contract
from digital_twin.domain.customer_evidence_explanation import customer_text_quality_issues, enforce_customer_message_quality
from digital_twin.domain.customer_investment_document import customer_investment_document_from_dict, customer_investment_document_quality, normalized_customer_investment_document
from digital_twin.domain.message_types import INVESTMENT_INSIGHT
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_explanation import INVESTMENT_NOTIFICATION_PRESENTATION_VERSION
from digital_twin.modules.notifications.domain.notification.presentation import presentation_metadata
from digital_twin.domain.notifications import NotificationJob, notification_debug_number
from digital_twin.modules.notifications.application.customer_investment_message import render_customer_investment_document
from digital_twin.modules.notifications.public import execution_telegram_message
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
from digital_twin.modules.notifications.application.notification.presentation import content_body, present_notification, typed_customer_document


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
        warnings = []
        if self.context_enricher:
            self.context_enricher(job)
        try:
            self.apply_investment_presentation_contract(job)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            warnings.append("legacy-presentation:" + type(error).__name__)
        try:
            rendered = (
                str(self.template_renderer(job) or "").strip()
                if self.template_renderer else str(job.context.get("telegramMessage") or job.text).strip()
            )
        except Exception as error:  # A broken optional template must not discard the source body.
            warnings.append("template-fallback:" + type(error).__name__)
            rendered = ""
        rendered = rendered or content_body(job.context.get("notificationContent")) or job.text.strip()
        if rendered:
            context = dict(job.context or {})
            context["notificationPresentation"] = presentation_metadata(job.message_type, context)
            if warnings:
                context["notificationPresentationWarnings"] = list(dict.fromkeys([
                    *context.get("notificationPresentationWarnings", []), *warnings,
                ]))
            is_investment = str(job.message_type or "") == INVESTMENT_INSIGHT
            original_rendered = rendered
            original_quality_issues = (
                customer_text_quality_issues(rendered) if is_investment else []
            )
            document_quality = (
                context.get("customerInvestmentDocumentQuality")
                if isinstance(context.get("customerInvestmentDocumentQuality"), dict)
                else {}
            )
            if is_investment and document_quality.get("status") != "passed":
                rendered = enforce_customer_message_quality(rendered)
            rendered = present_notification(job.message_type, context, rendered)
            quality_issues = customer_text_quality_issues(rendered) if is_investment else []
            job.text = rendered
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
                "notificationKind": context["notificationPresentation"]["kind"],
                "warnings": context.get("notificationPresentationWarnings", []),
                "writerProvenance": dict(context.get("notificationWriterProvenance") or {}),
                "claimValidation": dict(context.get("notificationClaimValidation") or {}),
                "narrativeVersion": str(
                    (context.get("notificationNarrativeBrief") or {}).get("version") or ""
                ),
                "narrativeFingerprint": str(
                    (context.get("notificationNarrativeBrief") or {}).get("fingerprint") or ""
                ),
                "customerLanguageQuality": {
                    "version": "customer-message-quality-v2-repair-first",
                    "status": (
                        "failed" if quality_issues
                        else "repaired" if rendered != original_rendered
                        else "passed"
                    ),
                    "issues": quality_issues,
                    "originalIssues": original_quality_issues,
                    "repairApplied": rendered != original_rendered,
                },
            }
            job.context = context
        return rendered

    @staticmethod
    def render_persisted_customer_text(job: NotificationJob) -> str:
        """Re-render an archived investment alert without changing its ledger record."""

        original = str(job.text or "")
        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return original
        if bool((job.context or {}).get("notificationReplayPreserveOriginal")):
            return original
        snapshot = NotificationJob.from_dict(job.to_dict())
        try:
            NotificationRenderingService.apply_investment_presentation_contract(snapshot)
        except Exception:  # noqa: BLE001 - malformed legacy context must remain readable.
            return original
        rendered = str((snapshot.context or {}).get("telegramMessage") or snapshot.text or "").strip()
        quality = (
            (snapshot.context or {}).get("customerInvestmentDocumentQuality")
            if isinstance((snapshot.context or {}).get("customerInvestmentDocumentQuality"), dict)
            else {}
        )
        if not rendered:
            return original
        return rendered if quality.get("status") == "passed" else enforce_customer_message_quality(rendered)

    @staticmethod
    def apply_investment_presentation_contract(job: NotificationJob) -> None:
        """Render saved facts or a validated result; never manufacture an action."""

        if str(job.message_type or "") != INVESTMENT_INSIGHT:
            return
        context = dict(job.context or {})
        context.setdefault("messageType", INVESTMENT_INSIGHT)
        context.setdefault("notificationDetailLevel", "concise")
        observation = typedb_context_observation_contract(context)
        narrative_only = typedb_narrative_only_contract(context)
        if narrative_only:
            context.setdefault("notificationDecisionMode", narrative_only.get("decisionMode") or "typedb-review-observation")
        document = customer_investment_document_from_dict(context.get("customerInvestmentDocument"))
        if observation and presentation_metadata(job.message_type, context)["kind"] == "price-change":
            document = None
        if not document:
            validated = (
                context.get("notificationAiValidatedResponse")
                or context.get("notificationInferenceResponse")
                or context.get("validatedDecisionResponse")
            )
            if observation:
                rendered = typedb_observation_telegram_message(
                    context, detail_level=str(context.get("notificationDetailLevel") or "concise"),
                )
            elif isinstance(validated, dict) and validated.get("action"):
                response = NotificationAIValidatedResponse.from_dict(validated)
                if narrative_only:
                    # Historical narrative responses sometimes carried the dataclass HOLD default.
                    # Correct the presentation copy, never rewrite the persisted decision.
                    response = replace(response, action="NO_ACTION")
                rendered = execution_telegram_message(context, response)
            else:
                rendered = str(context.get("telegramMessage") or job.text or "").strip()
                context["notificationPresentationWarnings"] = ["validated-decision-not-provided"]
            document = customer_investment_document_from_dict(context.get("customerInvestmentDocument"))
        if document:
            document = typed_customer_document(document, job.message_type, context)
            document = normalized_customer_investment_document(replace(
                document,
                detail_url=str(context.get("notificationDetailUrl") or document.detail_url or ""),
                sent_at=str(context.get("sentTime") or document.sent_at or ""),
                notification_number=str(context.get("notificationNumber") or document.notification_number or ""),
            ))
            context["customerInvestmentDocument"] = document.to_dict()
            context["customerInvestmentDocumentQuality"] = customer_investment_document_quality(document)
            rendered = render_customer_investment_document(document)
        context.update({
            "telegramMessage": rendered,
            "readableMessage": html.unescape(re.sub(r"<[^>]+>", "", rendered)),
            "notificationPresentationContractVersion": INVESTMENT_NOTIFICATION_PRESENTATION_VERSION,
            "notificationPresentationMode": "saved-content-only",
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
