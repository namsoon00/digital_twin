"""Load bounded prior AI decisions for a notification review."""

from __future__ import annotations

import re
from typing import Dict, Mapping

from digital_twin.modules.decisions.domain.ai_inference_queue import notification_ai_subject
from digital_twin.modules.decisions.domain.decision_continuity import (
    DECISION_CONTINUITY_PACKET_VERSION, compact_decision_continuity_packet,
)
from digital_twin.modules.decisions.domain.investment_decision_history import (
    compact_decision_episode_memory, decision_memory_matches_scope,
)
from digital_twin.modules.decisions.domain.investment_insight_assessment import compact_previous_investment_insight_episode
from digital_twin.modules.decisions.contracts import (
    canonical_investment_timestamp,
    parse_investment_timestamp,
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _canonical_machine_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if "T" not in text or not (text.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", text)):
        return ""
    return canonical_investment_timestamp(text)


def decision_continuity_cutoff(context: Mapping[str, object]) -> Dict[str, object]:
    """Resolve one source-bound clock, retaining KST only as legacy input."""

    values = _mapping(context)
    relation = _mapping(values.get("ontologyRelationContext"))
    verified = _mapping(relation.get("verifiedSourceSnapshot"))
    captured = _mapping(values.get("decisionContinuityPacket"))
    existing_clock = _mapping(values.get("decisionContinuityClock"))
    explicit_cutoff = _canonical_machine_timestamp(values.get("decisionContinuityCutoffAt"))
    if (
        explicit_cutoff
        and existing_clock.get("status") == "valid"
        and _canonical_machine_timestamp(existing_clock.get("cutoffAt")) == explicit_cutoff
    ):
        return {
            "status": "valid",
            "source": str(existing_clock.get("source") or "captured-machine-cutoff"),
            "raw": str(existing_clock.get("raw") or explicit_cutoff),
            "cutoffAt": explicit_cutoff,
            **({"compatibilityParser": True} if existing_clock.get("compatibilityParser") else {}),
        }
    candidates = (
        ("captured-machine-cutoff", values.get("decisionContinuityCutoffAt")),
        ("captured-continuity-packet", captured.get("capturedAt")),
        ("inference-generation", relation.get("inferenceGenerationAt")),
        ("verified-source-snapshot", verified.get("generatedAt")),
        ("source-observation", relation.get("sourceObservedAt")),
        ("event-generation", values.get("eventGeneratedAt")),
    )
    for source, raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        canonical = _canonical_machine_timestamp(text)
        return {
            "status": "valid" if canonical else "invalid",
            "source": source,
            "raw": text,
            "cutoffAt": canonical,
        }
    legacy = str(values.get("referenceDate") or relation.get("referenceDate") or "").strip()
    if legacy:
        canonical = canonical_investment_timestamp(legacy)
        return {
            "status": "valid" if canonical else "invalid",
            "source": "legacy-reference-date",
            "raw": legacy,
            "cutoffAt": canonical,
            "compatibilityParser": True,
        }
    return {"status": "missing", "source": "none", "raw": "", "cutoffAt": ""}


def frozen_current_position(context, symbol):
    relation = _mapping(context.get("ontologyRelationContext"))
    facts = _mapping(relation.get("facts"))
    subject = _mapping(relation.get("subject"))
    declared = str(subject.get("symbol") or context.get("rawSymbol") or "").upper()
    clock = decision_continuity_cutoff(context)
    as_of = str(clock.get("cutoffAt") or "")
    snapshot_id = str(relation.get("sourceAboxSnapshotId") or "")
    if declared != symbol or not facts or not as_of or not snapshot_id:
        return {}
    return {
        **{key: facts[key] for key in ("quantity", "sellableQuantity", "averagePrice", "currentPrice", "profitLossRate")
           if facts.get(key) is not None},
        "symbol": symbol, "observedAt": as_of, "source": "frozen-reasoning-facts",
        "sourceAboxSnapshotId": snapshot_id,
        "observationState": "observed",
    }


def context_with_previous_investment_decision(
    context: Mapping[str, object],
    decision_episode_store=None,
    continuity_service=None,
    *,
    account_id: str = "",
    symbol: str = "",
) -> Dict[str, object]:
    """Attach one immutable prior-decision continuity packet."""

    enriched = _mapping(context)
    subject = notification_ai_subject(enriched)
    resolved_account = str(account_id or enriched.get("accountId") or "").strip()
    resolved_symbol = str(symbol or subject.get("symbol") or "").strip().upper()
    current_episode_id = str(enriched.get("investmentDecisionEpisodeId") or "").strip()
    cutoff = decision_continuity_cutoff(enriched)
    if cutoff.get("status") == "valid":
        enriched["decisionContinuityCutoffAt"] = cutoff["cutoffAt"]
    enriched["decisionContinuityClock"] = cutoff
    request_cutoff = parse_investment_timestamp(cutoff.get("cutoffAt"))

    def in_scope(value, boundary=request_cutoff):
        decided_at = parse_investment_timestamp(_mapping(value).get("decidedAt"))
        return bool(boundary and decided_at and decided_at <= boundary) and decision_memory_matches_scope(
            value, resolved_account, resolved_symbol, exclude_episode_id=current_episode_id,
        )

    existing = compact_decision_episode_memory(enriched.pop("previousInvestmentDecisionEpisode", None))
    if not in_scope(existing):
        existing = {}
    enriched.pop("investmentDecisionHistory", None)
    captured_packet = compact_decision_continuity_packet(enriched.get("decisionContinuityPacket"))
    # This is a frozen historical summary, not an executable opinion to reauthorize.
    packet_previous = _mapping(captured_packet.get("previousDecision"))
    packet_cutoff = parse_investment_timestamp(captured_packet.get("capturedAt"))
    if (
        captured_packet
        and str(captured_packet.get("accountId") or "") == resolved_account
        and str(captured_packet.get("symbol") or "").upper() == resolved_symbol
        and (not captured_packet.get("previousDecision") or in_scope(captured_packet["previousDecision"], packet_cutoff))
        and (not request_cutoff or (packet_cutoff and packet_cutoff <= request_cutoff))
    ):
        enriched["decisionContinuityPacket"] = captured_packet
        if packet_previous:
            enriched["previousInvestmentDecisionEpisode"] = packet_previous
        enriched.setdefault("investmentDecisionHistory", {
            "version": "notification-decision-history-v2",
            "status": captured_packet.get("status") or "available",
            "source": "captured-continuity-packet",
            "accountId": resolved_account,
            "symbol": resolved_symbol,
            "previousEpisodeId": packet_previous.get("episodeId") or "",
            "continuityPacketId": captured_packet.get("packetId") or "",
        })
        return enriched
    enriched.pop("decisionContinuityPacket", None)

    if continuity_service and resolved_account and resolved_symbol:
        try:
            packet = continuity_service.build(
                account_id=resolved_account,
                symbol=resolved_symbol,
                exclude_episode_id=str(enriched.get("investmentDecisionEpisodeId") or "").strip(),
                captured_at=str(cutoff.get("cutoffAt") or ""),
                cutoff_source=str(cutoff.get("source") or ""),
                existing_previous=existing,
                current_position=frozen_current_position(enriched, resolved_symbol),
            )
        except Exception as error:  # noqa: BLE001 - a continuity read cannot block the alert.
            packet = {}
            continuity_error = type(error).__name__
        else:
            continuity_error = ""
        if packet:
            enriched["decisionContinuityPacket"] = packet
            packet_previous = _mapping(packet.get("previousDecision"))
            if packet_previous:
                existing = packet_previous
                enriched["previousInvestmentDecisionEpisode"] = packet_previous
            enriched["investmentDecisionHistory"] = {
                "version": "notification-decision-history-v2",
                "status": packet.get("status") or "available",
                "source": "decision-continuity-service",
                "accountId": resolved_account,
                "symbol": resolved_symbol,
                "previousEpisodeId": packet_previous.get("episodeId") or "",
                "previousDecidedAt": packet_previous.get("decidedAt") or "",
                "previousAction": packet_previous.get("action") or "",
                "continuityPacketId": packet.get("packetId") or "",
                "materialFingerprint": packet.get("materialFingerprint") or "",
                "cutoffAt": packet.get("capturedAt") or "",
                "cutoffSource": cutoff.get("source") or "",
            }
            return enriched
        if continuity_error:
            enriched["decisionContinuityPacket"] = {
                "contractVersion": DECISION_CONTINUITY_PACKET_VERSION,
                "status": "error",
                "accountId": resolved_account,
                "symbol": resolved_symbol,
                "sourceErrors": [continuity_error],
            }

    if existing:
        enriched["previousInvestmentDecisionEpisode"] = existing
        enriched.setdefault("investmentDecisionHistory", {
            "version": "notification-decision-history-v1",
            "status": "found",
            "source": "captured-context",
            "accountId": resolved_account,
            "symbol": resolved_symbol,
            "previousEpisodeId": existing.get("episodeId") or "",
            "previousDecidedAt": existing.get("decidedAt") or "",
        })
        return enriched

    audit = {
        "version": "notification-decision-history-v1",
        "status": "unavailable",
        "source": "investment-decision-episodes",
        "accountId": resolved_account,
        "symbol": resolved_symbol,
        "cutoffStatus": cutoff.get("status") or "missing",
        "cutoffAt": cutoff.get("cutoffAt") or "",
        "cutoffSource": cutoff.get("source") or "none",
    }
    if cutoff.get("status") != "valid":
        audit["status"] = "invalid-cutoff"
        enriched["investmentDecisionHistory"] = audit
        return enriched
    if not decision_episode_store or not resolved_account or not resolved_symbol:
        enriched["investmentDecisionHistory"] = audit
        return enriched

    try:
        if hasattr(decision_episode_store, "latest_decision_memory"):
            previous = decision_episode_store.latest_decision_memory(
                resolved_account,
                resolved_symbol,
                exclude_episode_id=current_episode_id,
                cutoff_at=cutoff.get("cutoffAt") or "",
            )
        elif hasattr(decision_episode_store, "list"):
            rows = decision_episode_store.list(
                account_id=resolved_account,
                symbol=resolved_symbol,
                limit=12,
            )
            previous = next((
                item for item in rows or []
                if in_scope(item) and compact_decision_episode_memory(item)
            ), None)
        else:
            previous = None
    except Exception as error:  # noqa: BLE001 - decision history must not block a live alert.
        audit.update({"status": "error", "errorType": type(error).__name__})
        enriched["investmentDecisionHistory"] = audit
        return enriched

    memory = compact_decision_episode_memory(previous)
    if not memory or not in_scope(memory):
        audit["status"] = "not-found-before-cutoff"
        enriched["investmentDecisionHistory"] = audit
        return enriched
    enriched["previousInvestmentDecisionEpisode"] = memory
    audit.update({
        "status": "found",
        "previousEpisodeId": memory.get("episodeId") or "",
        "previousDecidedAt": memory.get("decidedAt") or "",
        "previousAction": memory.get("action") or "",
    })
    enriched["investmentDecisionHistory"] = audit
    return enriched


def context_with_previous_investment_insight(
    context: Mapping[str, object],
    insight_episode_store=None,
    *,
    account_id: str = "",
    symbol: str = "",
) -> Dict[str, object]:
    """Attach the latest publishable AI insight for semantic change detection."""

    enriched = context_with_previous_delivered_investment_insight(
        context, insight_episode_store, account_id=account_id, symbol=symbol,
    )
    subject = notification_ai_subject(enriched)
    resolved_account = str(account_id or enriched.get("accountId") or "").strip()
    resolved_symbol = str(symbol or subject.get("symbol") or "").strip().upper()
    current_subject_case_id = str(
        enriched.get("investmentSubjectDecisionCaseId")
        or _mapping(enriched.get("investmentSubjectDecisionCase")).get("subjectCaseId")
        or ""
    ).strip()
    def in_scope(candidate):
        return decision_memory_matches_scope(candidate, resolved_account, resolved_symbol) and not (
            current_subject_case_id and candidate.get("subjectCaseId") == current_subject_case_id
        )

    existing = compact_previous_investment_insight_episode(
        enriched.pop("previousInvestmentAIInsightEpisode", None)
    )
    if existing and in_scope(existing):
        enriched["previousInvestmentAIInsightEpisode"] = existing
        return enriched
    enriched.pop("investmentInsightHistory", None)
    audit = {
        "version": "investment-insight-history-v1",
        "status": "unavailable",
        "accountId": resolved_account,
        "symbol": resolved_symbol,
    }
    reader = getattr(insight_episode_store, "latest_insight_episodes", None)
    if not callable(reader) or not resolved_account or not resolved_symbol:
        enriched["investmentInsightHistory"] = audit
        return enriched
    try:
        episodes = reader(
            account_id=resolved_account,
            symbol=resolved_symbol,
            limit=8,
        )
    except Exception as error:  # noqa: BLE001 - history must not block inference.
        audit.update({"status": "error", "errorType": type(error).__name__})
        enriched["investmentInsightHistory"] = audit
        return enriched

    previous = {}
    for episode in episodes or []:
        candidate = compact_previous_investment_insight_episode(episode)
        if not candidate or not in_scope(candidate):
            continue
        previous = candidate
        break
    if not previous:
        audit["status"] = "not-found"
        enriched["investmentInsightHistory"] = audit
        return enriched
    enriched["previousInvestmentAIInsightEpisode"] = previous
    audit.update({
        "status": "found",
        "previousEpisodeId": previous.get("episodeId") or "",
        "previousCreatedAt": previous.get("createdAt") or "",
        "previousMaterialFingerprint": _mapping(
            previous.get("insightAssessment")
        ).get("materialFingerprint") or "",
    })
    enriched["investmentInsightHistory"] = audit
    return enriched


def context_with_previous_delivered_investment_insight(
    context: Mapping[str, object],
    insight_episode_store=None,
    *,
    account_id: str = "",
    symbol: str = "",
    refresh: bool = False,
) -> Dict[str, object]:
    """Freeze receipt-backed delivery memory separately from AI analysis continuity."""

    enriched = _mapping(context)
    subject = notification_ai_subject(enriched)
    resolved_account = str(account_id or enriched.get("accountId") or "").strip()
    resolved_symbol = str(symbol or subject.get("symbol") or "").strip().upper()
    captured = _mapping(enriched.get("investmentInsightDeliveryHistory"))
    existing = compact_previous_investment_insight_episode(
        enriched.pop("previousDeliveredInvestmentAIInsightEpisode", None)
    )
    if (
        not refresh and captured.get("status") in {"found", "not-found"}
        and captured.get("accountId") == resolved_account
        and captured.get("symbol") == resolved_symbol
    ):
        if captured["status"] == "not-found":
            return enriched
        if (decision_memory_matches_scope(existing, resolved_account, resolved_symbol)
                and existing.get("episodeId") == captured.get("previousEpisodeId")):
            enriched["previousDeliveredInvestmentAIInsightEpisode"] = existing
            return enriched
    audit = {
        "version": "investment-insight-delivery-history-v1",
        "status": "unavailable",
        "accountId": resolved_account,
        "symbol": resolved_symbol,
    }
    reader = getattr(insight_episode_store, "latest_delivered_insight_episodes", None)
    if not callable(reader) or not resolved_account or not resolved_symbol:
        enriched["investmentInsightDeliveryHistory"] = audit
        return enriched
    try:
        episodes = reader(account_id=resolved_account, symbol=resolved_symbol, limit=8)
    except Exception as error:  # noqa: BLE001 - retain analysis, but do not claim delivery history is empty.
        audit.update({"status": "error", "errorType": type(error).__name__})
        enriched["investmentInsightDeliveryHistory"] = audit
        return enriched
    previous = {}
    for episode in episodes or []:
        episode = _mapping(episode)
        delivery = _mapping(episode.get("notificationDelivery"))
        if (
            str(episode.get("accountId") or "") != resolved_account
            or str(episode.get("symbol") or "").upper() != resolved_symbol
            or delivery.get("delivered") is not True
            or not delivery.get("deliveredAt")
        ):
            continue
        previous = compact_previous_investment_insight_episode(episode)
        if previous:
            audit["deliveredAt"] = delivery["deliveredAt"]
            audit["notificationJobId"] = delivery.get("notificationJobId") or ""
            break
    enriched["previousDeliveredInvestmentAIInsightEpisode"] = previous
    audit.update({
        "status": "found" if previous else "not-found",
        "previousEpisodeId": previous.get("episodeId") or "",
    })
    enriched["investmentInsightDeliveryHistory"] = audit
    return enriched


def refresh_insight_delivery_comparison(context, insight_episode_store, *, account_id=""):
    """Refresh delivery novelty without rewriting the frozen AI analysis input."""
    from digital_twin.modules.decisions.domain.investment_insight_assessment import investment_insight_delivery_transition

    before = _mapping(context.get("investmentInsightDeliveryHistory"))
    enriched = context_with_previous_delivered_investment_insight(
        context, insight_episode_store, account_id=account_id, refresh=True,
    )
    history = _mapping(enriched.get("investmentInsightDeliveryHistory"))
    if history.get("status") not in {"found", "not-found"}:
        raise RuntimeError("마지막 성공 발송 이력을 확인하지 못해 발송 비교를 재시도합니다.")
    enriched.setdefault("analysisDeliveryBaseline", before)
    trigger = _mapping(enriched.get("reasoningDeliveryTrigger"))
    delivered_at = parse_investment_timestamp(history.get("deliveredAt"))
    observed_at = parse_investment_timestamp(trigger.get("observedAt"))
    newer_source = bool(
        delivered_at and observed_at and observed_at > delivered_at
        and trigger.get("material") is True and trigger.get("userObservable") is True
        and trigger.get("facts")
    )
    enriched["deliveryBaselineRefresh"] = {
        "status": "verified", "previousEpisodeId": before.get("previousEpisodeId") or "",
        "currentEpisodeId": history.get("previousEpisodeId") or "",
        "changed": before.get("previousEpisodeId") != history.get("previousEpisodeId"),
        "newerObservedSource": newer_source,
    }
    assessment = _mapping(_mapping(enriched.get("notificationAiValidatedResponse")).get("insightAssessment"))
    if assessment:
        enriched["investmentInsightTransition"] = investment_insight_delivery_transition(enriched, assessment)
    return enriched
