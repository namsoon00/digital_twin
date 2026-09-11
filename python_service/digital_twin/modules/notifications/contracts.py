"""Explicit, lazy contracts surface for the notifications module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'DeliveryPolicyContext': ('digital_twin.modules.notifications.domain.notification.delivery_policy',
                           'DeliveryPolicyContext'),
 'FINAL_AI_DELIVERY_POLICY_VERSION': ('digital_twin.modules.notifications.domain.notification.delivery_policy',
                                      'FINAL_AI_DELIVERY_POLICY_VERSION'),
 'NotificationLifecycleEvent': ('digital_twin.modules.notifications.domain.notification.lifecycle',
                                'NotificationLifecycleEvent'),
 'evaluate_final_decision_delivery': ('digital_twin.modules.notifications.domain.notification.delivery_policy',
                                      'evaluate_final_decision_delivery'),
 'notification_kind': ('digital_twin.modules.notifications.domain.notification.presentation',
                       'notification_kind'),
 'presentation_metadata': ('digital_twin.modules.notifications.domain.notification.presentation',
                           'presentation_metadata')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
