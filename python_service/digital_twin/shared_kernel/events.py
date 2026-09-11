from dataclasses import asdict, dataclass, field
from digital_twin.shared_kernel.clock import utc_now_iso
from typing import Dict, Iterable, List, Mapping
import hashlib
import uuid


DOMAIN_EVENT_SCHEMA_VERSION = "domain-event-v1"


@dataclass(frozen=True)
class DomainEvent:
    name: str
    aggregate_id: str
    schema_version: str = DOMAIN_EVENT_SCHEMA_VERSION
    payload: Dict[str, object] = field(default_factory=dict)
    occurred_at: str = field(default_factory=utc_now_iso)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        return cls(
            name=str(payload.get("name") or ""),
            aggregate_id=str(payload.get("aggregate_id") or payload.get("aggregateId") or ""),
            schema_version=str(
                payload.get("schema_version")
                or payload.get("schemaVersion")
                or DOMAIN_EVENT_SCHEMA_VERSION
            ),
            payload=dict(payload.get("payload") or {}),
            occurred_at=str(payload.get("occurred_at") or payload.get("occurredAt") or utc_now_iso()),
            event_id=str(payload.get("event_id") or payload.get("eventId") or uuid.uuid4().hex),
            correlation_id=str(payload.get("correlation_id") or payload.get("correlationId") or ""),
        )


def _stable_reasoning_event_id(name: str, *parts: object) -> str:
    material = "|".join([str(name or ""), *[str(part or "") for part in parts]])
    return "event:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _event_text(value: object, limit: int = 600) -> str:
    """Keep the durable event transport small without changing source facts."""
    return str(value or "").strip()[:max(0, int(limit or 0))]


def _event_text_list(values: object, limit: int = 100, item_limit: int = 240) -> List[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    result = []
    for value in values:
        clean = _event_text(value, item_limit)
        if clean:
            result.append(clean)
        if len(result) >= max(0, int(limit or 0)):
            break
    return result


def _event_signature_digest(value: object) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""
