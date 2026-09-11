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

_EXPORTS['NotificationGateway'] = ('digital_twin.modules.notifications.domain.repositories', 'NotificationGateway')


_EXPORTS.update({
    'CRYPTO_ONTOLOGY_SIGNAL': ('digital_twin.modules.notifications.domain.message_types', 'CRYPTO_ONTOLOGY_SIGNAL'),
    'DEFAULT_ALERT_RULES': ('digital_twin.modules.notifications.domain.message_types', 'DEFAULT_ALERT_RULES'),
    'DEFAULT_ALERT_THRESHOLDS': ('digital_twin.modules.notifications.domain.message_types', 'DEFAULT_ALERT_THRESHOLDS'),
    'DEFAULT_CADENCE': ('digital_twin.modules.notifications.domain.message_types', 'DEFAULT_CADENCE'),
    'DEFAULT_NOTIFICATION_DETAIL_LEVEL': ('digital_twin.modules.notifications.domain.notification_explanation', 'DEFAULT_NOTIFICATION_DETAIL_LEVEL'),
    'DEFAULT_NOTIFICATION_RULES': ('digital_twin.modules.notifications.domain.notification_rule_models', 'DEFAULT_NOTIFICATION_RULES'),
    'DEFAULT_NOTIFICATION_TEMPLATES': ('digital_twin.modules.notifications.domain.notification_templates', 'DEFAULT_NOTIFICATION_TEMPLATES'),
    'DEFAULT_RELATION_RULE_THRESHOLDS': ('digital_twin.modules.notifications.domain.message_types', 'DEFAULT_RELATION_RULE_THRESHOLDS'),
    'EXTERNAL_CRYPTO_MOVE': ('digital_twin.modules.notifications.domain.message_types', 'EXTERNAL_CRYPTO_MOVE'),
    'EXTERNAL_DART_DISCLOSURE': ('digital_twin.modules.notifications.domain.message_types', 'EXTERNAL_DART_DISCLOSURE'),
    'EXTERNAL_DATA_CONNECTION': ('digital_twin.modules.notifications.domain.message_types', 'EXTERNAL_DATA_CONNECTION'),
    'EXTERNAL_EQUITY_MOVE': ('digital_twin.modules.notifications.domain.message_types', 'EXTERNAL_EQUITY_MOVE'),
    'EXTERNAL_MACRO_SHIFT': ('digital_twin.modules.notifications.domain.message_types', 'EXTERNAL_MACRO_SHIFT'),
    'ExternalSignalAlertMixin': ('digital_twin.modules.notifications.domain.external_signal_alerts', 'ExternalSignalAlertMixin'),
    'HOLDING_TIMING': ('digital_twin.modules.notifications.domain.message_types', 'HOLDING_TIMING'),
    'INSTRUMENT_MARKET_SCOPE': ('digital_twin.modules.notifications.domain.notification_decision_policy', 'INSTRUMENT_MARKET_SCOPE'),
    'INVESTMENT_ALERT_COVERAGE': ('digital_twin.modules.notifications.domain.message_types', 'INVESTMENT_ALERT_COVERAGE'),
    'INVESTMENT_CALENDAR_REMINDER': ('digital_twin.modules.notifications.domain.message_types', 'INVESTMENT_CALENDAR_REMINDER'),
    'INVESTMENT_INSIGHT': ('digital_twin.modules.notifications.domain.message_types', 'INVESTMENT_INSIGHT'),
    'MARKET_OBSERVATION': ('digital_twin.modules.notifications.domain.message_types', 'MARKET_OBSERVATION'),
    'MESSAGE_TYPE_LABELS': ('digital_twin.modules.notifications.domain.message_types', 'MESSAGE_TYPE_LABELS'),
    'MIN_CADENCE_MINUTES': ('digital_twin.modules.notifications.domain.message_types', 'MIN_CADENCE_MINUTES'),
    'MODEL_BUY': ('digital_twin.modules.notifications.domain.message_types', 'MODEL_BUY'),
    'MODEL_REVIEW': ('digital_twin.modules.notifications.domain.message_types', 'MODEL_REVIEW'),
    'MODEL_SELL': ('digital_twin.modules.notifications.domain.message_types', 'MODEL_SELL'),
    'MONITOR_CONNECTION': ('digital_twin.modules.notifications.domain.message_types', 'MONITOR_CONNECTION'),
    'MONITOR_DECISION_CHANGE': ('digital_twin.modules.notifications.domain.message_types', 'MONITOR_DECISION_CHANGE'),
    'MONITOR_HEARTBEAT': ('digital_twin.modules.notifications.domain.message_types', 'MONITOR_HEARTBEAT'),
    'MONITOR_PNL_CHANGE': ('digital_twin.modules.notifications.domain.message_types', 'MONITOR_PNL_CHANGE'),
    'MONITOR_POSITION_CHANGE': ('digital_twin.modules.notifications.domain.message_types', 'MONITOR_POSITION_CHANGE'),
    'MONITOR_TREND_CHANGE': ('digital_twin.modules.notifications.domain.message_types', 'MONITOR_TREND_CHANGE'),
    'MONITOR_VALUE_CHANGE': ('digital_twin.modules.notifications.domain.message_types', 'MONITOR_VALUE_CHANGE'),
    'NEWS_DIGEST': ('digital_twin.modules.notifications.domain.message_types', 'NEWS_DIGEST'),
    'NotificationJob': ('digital_twin.modules.notifications.domain.notifications', 'NotificationJob'),
    'NotificationRuleConfig': ('digital_twin.modules.notifications.domain.notification_rule_models', 'NotificationRuleConfig'),
    'NotificationTemplate': ('digital_twin.modules.notifications.domain.notification_templates', 'NotificationTemplate'),
    'ONTOLOGY_INFERENCE_MISSING': ('digital_twin.modules.notifications.domain.message_types', 'ONTOLOGY_INFERENCE_MISSING'),
    'ONTOLOGY_LAB_EXPERIMENT': ('digital_twin.modules.notifications.domain.message_types', 'ONTOLOGY_LAB_EXPERIMENT'),
    'ONTOLOGY_OBSERVATION_FOLLOWUP': ('digital_twin.modules.notifications.domain.message_types', 'ONTOLOGY_OBSERVATION_FOLLOWUP'),
    'ONTOLOGY_REASONING_QUEUE': ('digital_twin.modules.notifications.domain.message_types', 'ONTOLOGY_REASONING_QUEUE'),
    'OPERATOR_REASONING_REPORT': ('digital_twin.modules.notifications.domain.message_types', 'OPERATOR_REASONING_REPORT'),
    'PORTFOLIO_ACTIVITY_OBSERVATION': ('digital_twin.modules.notifications.domain.message_types', 'PORTFOLIO_ACTIVITY_OBSERVATION'),
    'PORTFOLIO_HOLDINGS_SNAPSHOT': ('digital_twin.modules.notifications.domain.message_types', 'PORTFOLIO_HOLDINGS_SNAPSHOT'),
    'PORTFOLIO_ONTOLOGY_SIGNAL': ('digital_twin.modules.notifications.domain.message_types', 'PORTFOLIO_ONTOLOGY_SIGNAL'),
    'PORTFOLIO_REBALANCE_REVIEW': ('digital_twin.modules.notifications.domain.message_types', 'PORTFOLIO_REBALANCE_REVIEW'),
    'StrategyAlertMixin': ('digital_twin.modules.notifications.domain.strategy_alerts', 'StrategyAlertMixin'),
    'WATCHLIST_BUY_CANDIDATE': ('digital_twin.modules.notifications.domain.message_types', 'WATCHLIST_BUY_CANDIDATE'),
    'WATCHLIST_ONTOLOGY_SIGNAL': ('digital_twin.modules.notifications.domain.message_types', 'WATCHLIST_ONTOLOGY_SIGNAL'),
    'WATCHLIST_QUOTE': ('digital_twin.modules.notifications.domain.message_types', 'WATCHLIST_QUOTE'),
    'WORK_HANDOFF': ('digital_twin.modules.notifications.domain.message_types', 'WORK_HANDOFF'),
    'absolute_beginner_friendly_text': ('digital_twin.modules.notifications.domain.notification_text_formatting', 'absolute_beginner_friendly_text'),
    'alert_context': ('digital_twin.modules.notifications.domain.notification_templates', 'alert_context'),
    'apply_market_hours_rule': ('digital_twin.modules.notifications.domain.notification_rule_evaluator', 'apply_market_hours_rule'),
    'apply_narrative_brief_to_response': ('digital_twin.modules.notifications.domain.notification_narrative', 'apply_narrative_brief_to_response'),
    'apply_similarity_rule': ('digital_twin.modules.notifications.domain.notification_rule_evaluator', 'apply_similarity_rule'),
    'apply_state_cooldown_rule': ('digital_twin.modules.notifications.domain.notification_rule_evaluator', 'apply_state_cooldown_rule'),
    'article_digest_context_item': ('digital_twin.modules.notifications.domain.sent_article_filter', 'article_digest_context_item'),
    'article_has_new_story_fact': ('digital_twin.modules.notifications.domain.sent_article_filter', 'article_has_new_story_fact'),
    'article_identity_keys': ('digital_twin.modules.notifications.domain.sent_article_filter', 'article_identity_keys'),
    'article_story_cluster_id': ('digital_twin.modules.notifications.domain.sent_article_filter', 'article_story_cluster_id'),
    'article_weak_identity_keys': ('digital_twin.modules.notifications.domain.sent_article_filter', 'article_weak_identity_keys'),
    'beginner_friendly_text': ('digital_twin.modules.notifications.domain.notification_text_formatting', 'beginner_friendly_text'),
    'build_decision_core_evidence_ledger': ('digital_twin.modules.notifications.domain.notification_narrative', 'build_decision_core_evidence_ledger'),
    'build_investment_narrative_brief': ('digital_twin.modules.notifications.domain.notification_narrative', 'build_investment_narrative_brief'),
    'collect_article_identity_keys_from_context': ('digital_twin.modules.notifications.domain.sent_article_filter', 'collect_article_identity_keys_from_context'),
    'compact_narrative_claim_evidence_contract': ('digital_twin.modules.notifications.domain.notification_narrative', 'compact_narrative_claim_evidence_contract'),
    'compact_number': ('digital_twin.modules.notifications.domain.alert_formatting', 'compact_number'),
    'context_observation_delivery_decision': ('digital_twin.modules.notifications.domain.context_observation_notifications', 'context_observation_delivery_decision'),
    'context_with_investment_notification_state': ('digital_twin.modules.notifications.domain.investment_notification_state', 'context_with_investment_notification_state'),
    'decision_policy_scope_contract': ('digital_twin.modules.notifications.domain.notification_decision_policy', 'decision_policy_scope_contract'),
    'default_notification_rule': ('digital_twin.modules.notifications.domain.notification_rule_models', 'default_notification_rule'),
    'evaluate_notification_rule': ('digital_twin.modules.notifications.domain.notification_rule_evaluator', 'evaluate_notification_rule'),
    'final_ai_delivery_decision': ('digital_twin.modules.notifications.domain.notification_ai_delivery', 'final_ai_delivery_decision'),
    'holding_review_baseline_is_deliverable': ('digital_twin.modules.notifications.domain.notification_ai_delivery', 'holding_review_baseline_is_deliverable'),
    'includes_portfolio_rebalance_policy': ('digital_twin.modules.notifications.domain.notification_decision_policy', 'includes_portfolio_rebalance_policy'),
    'investment_notification_icon': ('digital_twin.modules.notifications.domain.notification_icon_policy', 'investment_notification_icon'),
    'investment_notification_transition_line': ('digital_twin.modules.notifications.domain.investment_notification_state', 'investment_notification_transition_line'),
    'is_typedb_context_observation_notification': ('digital_twin.modules.notifications.domain.context_observation_notifications', 'is_typedb_context_observation_notification'),
    'market_decision_investment_strategy': ('digital_twin.modules.notifications.domain.notification_decision_policy', 'market_decision_investment_strategy'),
    'market_decision_raw_lines': ('digital_twin.modules.notifications.domain.notification_decision_policy', 'market_decision_raw_lines'),
    'market_decision_relation_context': ('digital_twin.modules.notifications.domain.notification_decision_policy', 'market_decision_relation_context'),
    'market_decision_strategy_guidance': ('digital_twin.modules.notifications.domain.notification_decision_policy', 'market_decision_strategy_guidance'),
    'money': ('digital_twin.modules.notifications.domain.alert_formatting', 'money'),
    'narrative_claim_evidence_contract': ('digital_twin.modules.notifications.domain.notification_narrative', 'narrative_claim_evidence_contract'),
    'narrative_fingerprint': ('digital_twin.modules.notifications.domain.notification_narrative', 'narrative_fingerprint'),
    'normalize_narrative_claims': ('digital_twin.modules.notifications.domain.notification_narrative', 'normalize_narrative_claims'),
    'normalize_notification_detail_level': ('digital_twin.modules.notifications.domain.notification_explanation', 'normalize_notification_detail_level'),
    'notification_debug_number': ('digital_twin.modules.notifications.domain.notifications', 'notification_debug_number'),
    'notification_detail_profile': ('digital_twin.modules.notifications.domain.notification_explanation', 'notification_detail_profile'),
    'notification_fingerprint': ('digital_twin.modules.notifications.domain.notification_rule_evaluator', 'notification_fingerprint'),
    'pct_delta': ('digital_twin.modules.notifications.domain.alert_formatting', 'pct_delta'),
    'pre_ai_deferred_delivery_decision': ('digital_twin.modules.notifications.domain.notification_ai_delivery', 'pre_ai_deferred_delivery_decision'),
    'price_money': ('digital_twin.modules.notifications.domain.alert_formatting', 'price_money'),
    'render_notification': ('digital_twin.modules.notifications.domain.notification_templates', 'render_notification'),
    'resolved_narrative_claim_evidence_contract': ('digital_twin.modules.notifications.domain.notification_narrative', 'resolved_narrative_claim_evidence_contract'),
    'response_writer_provenance': ('digital_twin.modules.notifications.domain.notification_narrative', 'response_writer_provenance'),
    'signed_number': ('digital_twin.modules.notifications.domain.alert_formatting', 'signed_number'),
    'signed_pct': ('digital_twin.modules.notifications.domain.alert_formatting', 'signed_pct'),
    'symbol_display_name': ('digital_twin.modules.notifications.domain.notification_templates', 'symbol_display_name'),
    'symbol_with_code': ('digital_twin.modules.notifications.domain.notification_templates', 'symbol_with_code'),
    'trade_strength_label': ('digital_twin.modules.notifications.domain.alert_formatting', 'trade_strength_label'),
    'typedb_context_observation_contract': ('digital_twin.modules.notifications.domain.context_observation_notifications', 'typedb_context_observation_contract'),
    'typedb_narrative_only_contract': ('digital_twin.modules.notifications.domain.context_observation_notifications', 'typedb_narrative_only_contract'),
    'typedb_review_observation_contract': ('digital_twin.modules.notifications.domain.context_observation_notifications', 'typedb_review_observation_contract'),
})

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
