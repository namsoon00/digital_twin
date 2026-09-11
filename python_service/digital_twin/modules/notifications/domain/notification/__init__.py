"""Notification bounded-context contracts.

Investment reasoning and AI decision semantics deliberately stay outside this
package.  The notification context owns admission, presentation, delivery,
and the auditable lifecycle of a message request.
"""

from digital_twin.modules.notifications.domain.notification.channel import DeliveryReceipt
from digital_twin.modules.notifications.domain.notification.document import NotificationDocument, NotificationSection
from digital_twin.modules.notifications.domain.notification.delivery_policy import DeliveryDecision, DeliveryPolicyContext, FINAL_AI_DELIVERY_POLICY_VERSION, evaluate_final_decision_delivery
from digital_twin.modules.notifications.domain.notification.eligibility import NotificationEligibility
from digital_twin.modules.notifications.domain.notification.lifecycle import NotificationLifecycleEvent, NotificationStage
from digital_twin.modules.notifications.domain.notification.ports import NotificationAuditPort, NotificationChannel, NotificationJobPort
from digital_twin.modules.notifications.domain.notification.request import NotificationRequest, NotificationSourceTrace

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
