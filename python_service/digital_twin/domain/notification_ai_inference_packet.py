"""Immutable prompt and evidence boundary for one notification AI decision."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Dict, Iterable, Mapping, Tuple

from .notification_ai_decision_brief import build_notification_ai_prompt_bundle


AI_INFERENCE_PACKET_VERSION = "notification-ai-inference-packet-v1"


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _load(value: str) -> Dict[str, object]:
    parsed = json.loads(str(value or "{}"))
    return dict(parsed or {}) if isinstance(parsed, dict) else {}


def _evidence_material(rows: Iterable[Mapping[str, object]]) -> Tuple[Dict[str, object], ...]:
    normalized = [
        dict(item)
        for item in rows or []
        if isinstance(item, Mapping) and str(item.get("evidenceId") or "")
    ]
    normalized.sort(key=lambda item: str(item.get("evidenceId") or ""))
    return tuple(normalized)


@dataclass(frozen=True)
class NotificationAIInferencePacket:
    packet_id: str
    prompt: str
    prompt_hash: str
    prompt_bytes: int
    evidence_fingerprint: str
    evidence_ids: Tuple[str, ...]
    decision_core_json: str
    decision_brief_json: str
    context_routing_json: str
    prompt_release_json: str

    @property
    def decision_core(self) -> Dict[str, object]:
        return _load(self.decision_core_json)

    @property
    def decision_brief(self) -> Dict[str, object]:
        return _load(self.decision_brief_json)

    @property
    def context_routing(self) -> Dict[str, object]:
        return _load(self.context_routing_json)

    @property
    def prompt_release(self) -> Dict[str, object]:
        return _load(self.prompt_release_json)

    def bind_context(
        self,
        context: Mapping[str, object],
        *,
        timeout_seconds: object = None,
    ) -> Dict[str, object]:
        bound = dict(context or {})
        bound["_notificationAiPreparedPrompt"] = self.prompt
        bound["_notificationAiPreparedDecisionBrief"] = self.decision_brief
        bound["_notificationAiPreparedDecisionCore"] = self.decision_core
        bound["_notificationAiInferencePacket"] = self.to_audit_dict()
        if timeout_seconds not in (None, ""):
            bound["_notificationAiTimeoutSecondsOverride"] = timeout_seconds
        return bound

    def to_audit_dict(self, *, include_payload: bool = False) -> Dict[str, object]:
        release = self.prompt_release
        core = self.decision_core
        contract = core.get("narrativeClaimContract")
        contract = dict(contract or {}) if isinstance(contract, dict) else {}
        payload = {
            "version": AI_INFERENCE_PACKET_VERSION,
            "packetId": self.packet_id,
            "promptHash": self.prompt_hash,
            "promptBytes": self.prompt_bytes,
            "evidenceFingerprint": self.evidence_fingerprint,
            "evidenceIds": list(self.evidence_ids),
            "evidenceCount": len(self.evidence_ids),
            "promptVersion": str(release.get("version") or ""),
            "decisionContractVersion": str(release.get("contractVersion") or ""),
            "promptReleaseFingerprint": str(release.get("fingerprint") or ""),
            "claimContract": contract,
        }
        if include_payload:
            payload.update({
                "prompt": self.prompt,
                "decisionCore": core,
                "decisionBrief": self.decision_brief,
                "contextRouting": self.context_routing,
                "promptRelease": release,
            })
        return payload


def build_notification_ai_inference_packet(
    context: Dict[str, object],
    settings: Dict[str, object] = None,
    *,
    max_prompt_bytes: int = 0,
    profile: Dict[str, object] = None,
    decision_brief: Dict[str, object] = None,
) -> NotificationAIInferencePacket:
    bundle = build_notification_ai_prompt_bundle(
        context,
        settings,
        max_prompt_bytes=max_prompt_bytes,
        profile=profile,
        decision_brief=decision_brief,
    )
    prompt = str(bundle.get("prompt") or "")
    decision_core = dict(bundle.get("decisionCore") or {})
    evidence = _evidence_material(decision_core.get("evidenceLedger") or [])
    evidence_fingerprint = hashlib.sha256(_json(evidence).encode("utf-8")).hexdigest()
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    prompt_release = dict(bundle.get("promptRelease") or {})
    packet_material = "|".join((
        AI_INFERENCE_PACKET_VERSION,
        prompt_hash,
        evidence_fingerprint,
        str(prompt_release.get("fingerprint") or ""),
    ))
    packet_id = "ai-packet:" + hashlib.sha256(packet_material.encode("utf-8")).hexdigest()[:24]
    return NotificationAIInferencePacket(
        packet_id=packet_id,
        prompt=prompt,
        prompt_hash=prompt_hash,
        prompt_bytes=len(prompt.encode("utf-8")),
        evidence_fingerprint=evidence_fingerprint,
        evidence_ids=tuple(str(item.get("evidenceId") or "") for item in evidence),
        decision_core_json=_json(decision_core),
        decision_brief_json=_json(bundle.get("decisionBrief") or {}),
        context_routing_json=_json(bundle.get("contextRouting") or {}),
        prompt_release_json=_json(prompt_release),
    )
