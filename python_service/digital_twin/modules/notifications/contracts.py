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

_EXPORTS['DEFAULT_QUIET_HOURS_ENABLED'] = ('digital_twin.modules.notifications.domain.account_preferences', 'DEFAULT_QUIET_HOURS_ENABLED')
_EXPORTS['DEFAULT_QUIET_HOURS_START'] = ('digital_twin.modules.notifications.domain.account_preferences', 'DEFAULT_QUIET_HOURS_START')
_EXPORTS['DEFAULT_QUIET_HOURS_END'] = ('digital_twin.modules.notifications.domain.account_preferences', 'DEFAULT_QUIET_HOURS_END')
_EXPORTS['DEFAULT_QUIET_HOURS_TIMEZONE'] = ('digital_twin.modules.notifications.domain.account_preferences', 'DEFAULT_QUIET_HOURS_TIMEZONE')
_EXPORTS['QUIET_HOURS_BYPASS_MESSAGE_TYPES'] = ('digital_twin.modules.notifications.domain.account_preferences', 'QUIET_HOURS_BYPASS_MESSAGE_TYPES')
_EXPORTS['DEFAULT_MESSAGE_DELIVERY_LEVEL'] = ('digital_twin.modules.notifications.domain.account_preferences', 'DEFAULT_MESSAGE_DELIVERY_LEVEL')
_EXPORTS['MESSAGE_DELIVERY_LEVELS'] = ('digital_twin.modules.notifications.domain.account_preferences', 'MESSAGE_DELIVERY_LEVELS')
_EXPORTS['normalize_message_delivery_level'] = ('digital_twin.modules.notifications.domain.account_preferences', 'normalize_message_delivery_level')
_EXPORTS['message_delivery_profile'] = ('digital_twin.modules.notifications.domain.account_preferences', 'message_delivery_profile')
_EXPORTS['bool_value'] = ('digital_twin.modules.notifications.domain.account_preferences', 'bool_value')
_EXPORTS['normalize_time_text'] = ('digital_twin.modules.notifications.domain.account_preferences', 'normalize_time_text')
_EXPORTS['quiet_minutes'] = ('digital_twin.modules.notifications.domain.account_preferences', 'quiet_minutes')
_EXPORTS['quiet_timezone'] = ('digital_twin.modules.notifications.domain.account_preferences', 'quiet_timezone')
_EXPORTS['is_quiet_time'] = ('digital_twin.modules.notifications.domain.account_preferences', 'is_quiet_time')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
