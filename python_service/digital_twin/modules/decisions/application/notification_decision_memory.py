"""Load bounded prior AI decisions for a notification review."""

from __future__ import annotations

from typing import Dict, Mapping

from digital_twin.modules.decisions.domain.ai_inference_queue import notification_ai_subject
from digital_twin.modules.decisions.domain.decision_continuity import compact_decision_continuity_packet
from digital_twin.modules.decisions.domain.investment_decision_history import compact_decision_episode_memory
from digital_twin.modules.decisions.domain.investment_insight_assessment import compact_previous_investment_insight_episode


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


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
    existing = compact_decision_episode_memory(enriched.get("previousInvestmentDecisionEpisode"))
    subject = notification_ai_subject(enriched)
    resolved_account = str(account_id or enriched.get("accountId") or "").strip()
    resolved_symbol = str(symbol or subject.get("symbol") or "").strip().upper()
    captured_packet = compact_decision_continuity_packet(enriched.get("decisionContinuityPacket"))
    if (
        captured_packet
        and str(captured_packet.get("accountId") or "") == resolved_account
        and str(captured_packet.get("symbol") or "").upper() == resolved_symbol
    ):
        enriched["decisionContinuityPacket"] = captured_packet
        packet_previous = compact_decision_episode_memory(captured_packet.get("previousDecision"))
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

    if continuity_service and resolved_account and resolved_symbol:
        try:
            packet = continuity_service.build(
                account_id=resolved_account,
                symbol=resolved_symbol,
                exclude_episode_id=str(enriched.get("investmentDecisionEpisodeId") or "").strip(),
                captured_at=str(enriched.get("referenceDate") or ""),
                existing_previous=existing,
            )
        except Exception as error:  # noqa: BLE001 - a continuity read cannot block the alert.
            packet = {}
            continuity_error = type(error).__name__
        else:
            continuity_error = ""
        if packet:
            enriched["decisionContinuityPacket"] = packet
            packet_previous = compact_decision_episode_memory(packet.get("previousDecision"))
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
            }
            return enriched
        if continuity_error:
            enriched["decisionContinuityPacket"] = {
                "contractVersion": "decision-continuity-packet-v2",
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
    }
    if not decision_episode_store or not resolved_account or not resolved_symbol:
        enriched["investmentDecisionHistory"] = audit
        return enriched

    current_episode_id = str(enriched.get("investmentDecisionEpisodeId") or "").strip()
    try:
        if hasattr(decision_episode_store, "latest_decision_memory"):
            previous = decision_episode_store.latest_decision_memory(
                resolved_account,
                resolved_symbol,
                exclude_episode_id=current_episode_id,
            )
        elif hasattr(decision_episode_store, "list"):
            rows = decision_episode_store.list(
                account_id=resolved_account,
                symbol=resolved_symbol,
                limit=4,
            )
            previous = next((
                item for item in rows or []
                if str(getattr(item, "episode_id", "") or _mapping(item).get("episodeId") or "").strip()
                != current_episode_id
            ), None)
        else:
            previous = None
    except Exception as error:  # noqa: BLE001 - decision history must not block a live alert.
        audit.update({"status": "error", "errorType": type(error).__name__})
        enriched["investmentDecisionHistory"] = audit
        return enriched

    memory = compact_decision_episode_memory(previous)
    if not memory:
        audit["status"] = "not-found"
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
    existing = compact_previous_investment_insight_episode(
        enriched.get("previousInvestmentAIInsightEpisode")
    )
    if existing:
        enriched["previousInvestmentAIInsightEpisode"] = existing
        return enriched

    subject = notification_ai_subject(enriched)
    resolved_account = str(account_id or enriched.get("accountId") or "").strip()
    resolved_symbol = str(symbol or subject.get("symbol") or "").strip().upper()
    current_subject_case_id = str(
        enriched.get("investmentSubjectDecisionCaseId")
        or _mapping(enriched.get("investmentSubjectDecisionCase")).get("subjectCaseId")
        or ""
    ).strip()
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
        if not candidate:
            continue
        if current_subject_case_id and str(candidate.get("subjectCaseId") or "") == current_subject_case_id:
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
) -> Dict[str, object]:
    """Freeze receipt-backed delivery memory separately from AI analysis continuity."""

    enriched = _mapping(context)
    subject = notification_ai_subject(enriched)
    resolved_account = str(account_id or enriched.get("accountId") or "").strip()
    resolved_symbol = str(symbol or subject.get("symbol") or "").strip().upper()
    captured = _mapping(enriched.get("investmentInsightDeliveryHistory"))
    if (
        captured.get("status") in {"found", "not-found"}
        and captured.get("accountId") == resolved_account
        and captured.get("symbol") == resolved_symbol
    ):
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
