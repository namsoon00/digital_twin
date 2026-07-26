"""Resolve the icon shown for a notification type and its current context."""

from typing import Dict

from .message_types import MESSAGE_TYPE_EMOJIS
from .operational_notification_presentation import operational_notification_presentation


def notification_message_icon(message_type: object, context: Dict[str, object] = None) -> str:
    """Return the contextual icon without changing message meaning or routing."""
    values = dict(context or {}) if isinstance(context, dict) else {}
    if not values:
        return MESSAGE_TYPE_EMOJIS.get(str(message_type or "").strip(), "🔔")
    operational = operational_notification_presentation(message_type, values)
    if operational:
        return operational.icon
    for key in ["notificationIcon", "titleIcon"]:
        icon = str(values.get(key) or "").strip()
        if icon:
            return icon
    return MESSAGE_TYPE_EMOJIS.get(str(message_type or "").strip(), "🔔")
