"""Immutable request accepted by the notification bounded context."""

from dataclasses import asdict, dataclass, field
from typing import Dict, Mapping

from .presentation import DEFAULT_POLICY_TYPES


NOTIFICATION_REQUEST_CONTRACT_VERSION = "notification-request-v2"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class NotificationSourceTrace:
    source_event_id: str = ""
    source_event_name: str = ""
    engine_deployment_id: str = ""
    engine_version: str = ""
    source_abox_snapshot_id: str = ""
    inference_generation_id: str = ""
    decision_episode_id: str = ""
    decision_continuity_packet_id: str = ""
    generated_at: str = ""

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, object] = None,
        *,
        source_event_id: str = "",
        source_event_name: str = "",
    ):
        values = _mapping(context)
        relation = _mapping(values.get("ontologyRelationContext"))
        metadata = _mapping(values.get("metadata"))
        metadata_relation = _mapping(metadata.get("ontologyRelationContext"))
        relation = relation or metadata_relation
        continuity = _mapping(values.get("decisionContinuityPacket"))
        episode = _mapping(values.get("investmentDecisionEpisode"))
        return cls(
            source_event_id=str(source_event_id or values.get("sourceEventId") or ""),
            source_event_name=str(source_event_name or values.get("sourceEventName") or ""),
            engine_deployment_id=str(
                relation.get("reasoningEngineDeploymentId")
                or relation.get("engineDeploymentId")
                or values.get("reasoningEngineDeploymentId")
                or ""
            ),
            engine_version=str(
                relation.get("reasoningEngineVersion")
                or relation.get("engineVersion")
                or values.get("reasoningEngineVersion")
                or ""
            ),
            source_abox_snapshot_id=str(
                relation.get("sourceAboxSnapshotId")
                or values.get("sourceAboxSnapshotId")
                or ""
            ),
            inference_generation_id=str(
                relation.get("inferenceGenerationId")
                or values.get("inferenceGenerationId")
                or ""
            ),
            decision_episode_id=str(
                values.get("investmentDecisionEpisodeId")
                or values.get("decisionEpisodeId")
                or episode.get("episodeId")
                or ""
            ),
            decision_continuity_packet_id=str(continuity.get("packetId") or ""),
            generated_at=str(
                relation.get("inferenceGenerationAt")
                or values.get("eventGeneratedAt")
                or values.get("referenceDate")
                or ""
            ),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "sourceEventId": self.source_event_id,
            "sourceEventName": self.source_event_name,
            "engineDeploymentId": self.engine_deployment_id,
            "engineVersion": self.engine_version,
            "sourceAboxSnapshotId": self.source_abox_snapshot_id,
            "inferenceGenerationId": self.inference_generation_id,
            "decisionEpisodeId": self.decision_episode_id,
            "decisionContinuityPacketId": self.decision_continuity_packet_id,
            "generatedAt": self.generated_at,
        }


@dataclass(frozen=True)
class NotificationRequest:
    request_id: str
    account_id: str
    account_label: str
    message_type: str
    source_text: str
    context: Mapping[str, object] = field(default_factory=dict)
    dedupe_key: str = ""
    trace: NotificationSourceTrace = field(default_factory=NotificationSourceTrace)
    contract_version: str = NOTIFICATION_REQUEST_CONTRACT_VERSION
    kind: str = ""
    subject: Mapping[str, object] = field(default_factory=dict)
    content: Mapping[str, object] = field(default_factory=dict)
    extensions: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]):
        values = _mapping(payload)
        trace = _mapping(values.get("trace"))
        fields = {
            "source_event_id": "sourceEventId", "source_event_name": "sourceEventName",
            "engine_deployment_id": "engineDeploymentId", "engine_version": "engineVersion",
            "source_abox_snapshot_id": "sourceAboxSnapshotId", "inference_generation_id": "inferenceGenerationId",
            "decision_episode_id": "decisionEpisodeId", "decision_continuity_packet_id": "decisionContinuityPacketId",
            "generated_at": "generatedAt",
        }
        known = {"requestId", "accountId", "accountLabel", "messageType", "sourceText", "text", "body",
                 "context", "dedupeKey", "trace", "contractVersion", "kind", "subject", "content", "extensions"}
        return cls(
            request_id=str(values.get("requestId") or ""),
            account_id=str(values.get("accountId") or ""),
            account_label=str(values.get("accountLabel") or ""),
            message_type=str(values.get("messageType") or DEFAULT_POLICY_TYPES.get(str(values.get("kind") or ""), "notification")),
            source_text=str(values.get("sourceText") or values.get("text") or values.get("body") or ""),
            context=_mapping(values.get("context")), dedupe_key=str(values.get("dedupeKey") or ""),
            trace=NotificationSourceTrace(**{key: str(trace.get(alias) or "") for key, alias in fields.items()}),
            contract_version=str(values.get("contractVersion") or NOTIFICATION_REQUEST_CONTRACT_VERSION),
            kind=str(values.get("kind") or ""),
            subject=_mapping(values.get("subject")) or {"name": str(values.get("subject") or "")},
            content=_mapping(values.get("content")),
            extensions={**_mapping(values.get("extensions")), **{key: value for key, value in values.items() if key not in known}},
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload.update({
            "requestId": payload.pop("request_id"),
            "accountId": payload.pop("account_id"),
            "accountLabel": payload.pop("account_label"),
            "messageType": payload.pop("message_type"),
            "sourceText": payload.pop("source_text"),
            "dedupeKey": payload.pop("dedupe_key"),
            "contractVersion": payload.pop("contract_version"),
            "trace": self.trace.to_dict(),
        })
        payload["context"] = dict(self.context or {})
        return payload
