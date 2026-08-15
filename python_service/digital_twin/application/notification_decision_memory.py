"""Load bounded prior AI decisions for a notification review."""

from __future__ import annotations

from typing import Dict, Mapping

from ..domain.ai_inference_queue import notification_ai_subject
from ..domain.decision_continuity import compact_decision_continuity_packet
from ..domain.investment_decision_history import compact_decision_episode_memory


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
