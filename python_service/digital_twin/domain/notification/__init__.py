"""Notification bounded-context contracts.

Investment reasoning and AI decision semantics deliberately stay outside this
package.  The notification context owns admission, presentation, delivery,
and the auditable lifecycle of a message request.
"""

from .channel import DeliveryReceipt
from .document import NotificationDocument, NotificationSection
from .delivery_policy import (
    DeliveryDecision,
    DeliveryPolicyContext,
    FINAL_AI_DELIVERY_POLICY_VERSION,
    evaluate_final_decision_delivery,
)
from .eligibility import NotificationEligibility
from .lifecycle import NotificationLifecycleEvent, NotificationStage
from .ports import NotificationAuditPort, NotificationChannel, NotificationJobPort
from .request import NotificationRequest, NotificationSourceTrace

__all__ = [
    "DeliveryReceipt",
    "DeliveryDecision",
    "DeliveryPolicyContext",
    "FINAL_AI_DELIVERY_POLICY_VERSION",
    "NotificationAuditPort",
    "NotificationChannel",
    "NotificationDocument",
    "NotificationEligibility",
    "NotificationJobPort",
    "NotificationLifecycleEvent",
    "NotificationRequest",
    "NotificationSection",
    "NotificationSourceTrace",
    "NotificationStage",
    "evaluate_final_decision_delivery",
]
