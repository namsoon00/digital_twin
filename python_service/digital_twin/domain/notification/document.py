"""Structured presentation contract independent of Telegram or web HTML."""

from dataclasses import asdict, dataclass, field
from typing import Dict, Mapping, Tuple


@dataclass(frozen=True)
class NotificationSection:
    key: str
    title: str
    lines: Tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["lines"] = list(self.lines)
        payload["metadata"] = dict(self.metadata or {})
        return payload


@dataclass(frozen=True)
class NotificationDocument:
    title: str
    message_type: str
    sections: Tuple[NotificationSection, ...] = ()
    fallback_text: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "title": self.title,
            "messageType": self.message_type,
            "sections": [item.to_dict() for item in self.sections],
            "fallbackText": self.fallback_text,
            "metadata": dict(self.metadata or {}),
        }
