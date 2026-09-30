"""Web notification configuration boundary."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.adapters.notification_storage import notification_rule_store
from digital_twin.infrastructure.web.adapters.notification_storage import notification_store
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.common import parse_utc
from digital_twin.infrastructure.web.common import utc_iso
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.modules.market_data.domain.market_hours import DEFAULT_MARKET_HOUR_SESSIONS
from digital_twin.modules.market_data.domain.market_observations import market_observation_delivery_cadence_minutes
from digital_twin.modules.notifications.domain.event_types import NOTIFICATION_RULE_UPDATED
from digital_twin.modules.notifications.domain.event_types import NOTIFICATION_TEMPLATE_UPDATED
from digital_twin.modules.notifications.domain.message_types import DEFAULT_ALERT_RULES
from digital_twin.modules.notifications.domain.message_types import DEFAULT_CADENCE
from digital_twin.modules.notifications.domain.message_types import public_message_catalog
from digital_twin.modules.notifications.domain.message_types import user_managed_notification_types
from digital_twin.modules.notifications.domain.message_types import visible_notification_template_types
from digital_twin.modules.notifications.domain.notification_icon_policy import notification_message_icon
from digital_twin.modules.notifications.domain.notification_rules import CONDITION_TYPE_LABELS
from digital_twin.modules.notifications.domain.notification_rules import NotificationRuleConfig
from digital_twin.modules.notifications.domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES
from digital_twin.modules.notifications.domain.notification_templates import MESSAGE_TYPE_LABELS
from digital_twin.modules.notifications.domain.notification_templates import NotificationTemplate
from digital_twin.modules.notifications.domain.notification_templates import TRIGGER_SUMMARIES
from digital_twin.modules.notifications.domain.notification_templates import template_variables
from digital_twin.modules.portfolio.domain.portfolio import utc_now_iso
from digital_twin.shared_kernel.parsing import parse_assignments
from typing import Dict
from typing import List


NON_CADENCE_MESSAGE_GUIDES = {
    "modelReview": "판단 변화 알림이 발생하면 별도 워커가 충분히 분석한 뒤 보냅니다.",
    "workHandoff": "작업이 끝나고 커밋, 검증, 푸시, 재시작 결과를 공유할 때 보냅니다.",
    "notification": "사용자가 직접 만든 일반 알림이나 시스템 안내가 있을 때 보냅니다.",
    "default": "타입별 템플릿이 없을 때 fallback으로 사용됩니다.",
}

EVENT_DELIVERY_GUIDES = {
    "investmentInsight": "검증된 변화마다 즉시·중요 변화의 최소 간격을 적용합니다. 동일 판단의 정기 재확인은 웹 이력이며 정기 푸시가 아닙니다.",
    "newsDigest": "새 기사·사건이 선별되면 보냅니다. 기사 중복 억제 시간은 정기 발송 주기가 아닙니다.",
    "informationUpdate": "발표 결과나 1시간·24시간 후 새 관측이 확보되면 보냅니다.",
    "portfolioActivityObservation": "새 실계좌 변화가 확인될 때 에피소드별로 한 번 보냅니다.",
    "portfolioHoldingsSnapshot": "사용자가 명시적으로 요청한 보유 현황만 보냅니다.",
    "investmentCalendarReminder": "이벤트별 알림 시점에 보냅니다. 기본값은 하루 전·1시간 전·시작 시각입니다.",
}


def list_templates_payload() -> Dict[str, object]:
    visible_types = set(visible_notification_template_types())
    try:
        templates = [
            item
            for item in notification_store().list()
            if item.message_type in visible_types
        ]
    except Exception:  # noqa: BLE001 - default templates keep settings UI available without MySQL.
        templates = [NotificationTemplate.default(message_type) for message_type in visible_notification_template_types()]
    return {
        "templates": [item.to_dict() for item in templates],
        "variables": template_variables(),
        "visibleMessageTypes": visible_notification_template_types(),
    }


def list_notification_rules_payload(include_internal: bool = False) -> Dict[str, object]:
    managed_order = user_managed_notification_types()
    managed_types = set(managed_order)
    try:
        rules = notification_rule_store().list()
    except Exception:  # noqa: BLE001 - default rules keep settings UI available without MySQL.
        catalog = user_managed_notification_types() + ([] if not include_internal else [key for key in DEFAULT_ALERT_RULES if key not in managed_types])
        rules = [
            NotificationRuleConfig.from_dict({
                "messageType": message_type,
                "enabled": bool(DEFAULT_ALERT_RULES.get(message_type, 1)),
            })
            for message_type in catalog
        ]
    rules_by_type = {item.message_type: item for item in rules}
    visible_rules = [rules_by_type[item] for item in managed_order if item in rules_by_type]
    internal_rules = [item for item in rules if item.message_type not in managed_types]
    payload = {
        "rules": [item.to_dict() for item in visible_rules],
        "conditionTypes": CONDITION_TYPE_LABELS,
        "marketHoursSessions": list(DEFAULT_MARKET_HOUR_SESSIONS.values()),
        "messageCatalog": public_message_catalog(),
        "managedMessageTypes": user_managed_notification_types(),
        "internalRuleCount": len(internal_rules),
    }
    if include_internal:
        payload["internalRules"] = [item.to_dict() for item in internal_rules]
    return payload


def include_internal_notification_query(query: Dict[str, List[str]]) -> bool:
    value = first_query(query, "includeInternal").lower()
    return value in {"1", "true", "yes", "y"}


def cadence_records_for_type(sent: Dict[str, object], message_type: str) -> List[Dict[str, object]]:
    records = []
    prefix = "cadence:python:"
    for key, sent_at in sent.items():
        if not str(key).startswith(prefix):
            continue
        parts = str(key).split(":", 4)
        if len(parts) != 5 or parts[3] != message_type:
            continue
        parsed = parse_utc(str(sent_at or ""))
        if not parsed:
            continue
        records.append({
            "accountId": parts[2],
            "target": parts[4],
            "sentAt": utc_iso(parsed),
            "sentAtEpoch": parsed.timestamp(),
        })
    records.sort(key=lambda item: float(item.get("sentAtEpoch") or 0), reverse=True)
    return records


def notification_schedules_payload(include_internal: bool = False) -> Dict[str, object]:
    settings = runtime_settings()
    rules = parse_assignments(settings.get("alertRules", ""), DEFAULT_ALERT_RULES)
    cadence = parse_assignments(settings.get("alertCadenceMinutes", ""), DEFAULT_CADENCE)
    store = stores.monitor_store()
    accounts = {account.account_id: account for account in stores.account_reader().load()}
    now_at = datetime.now(timezone.utc)
    if include_internal:
        message_types = list(dict.fromkeys(list(DEFAULT_CADENCE.keys()) + list(DEFAULT_NOTIFICATION_TEMPLATES.keys())))
    else:
        message_types = user_managed_notification_types()
    schedules = []
    for message_type in message_types:
        has_cadence = message_type in DEFAULT_CADENCE
        minutes = int(cadence.get(message_type, DEFAULT_CADENCE.get(message_type, 0)) or 0)
        event_delivery = message_type in EVENT_DELIVERY_GUIDES
        if event_delivery:
            minutes = 0
        elif message_type == "marketObservation":
            minutes = market_observation_delivery_cadence_minutes(settings)
        records = cadence_records_for_type(store.sent, message_type)
        last_record = records[0] if records else {}
        last_sent_at = parse_utc(str(last_record.get("sentAt") or "")) if last_record else None
        next_eligible_at = last_sent_at + timedelta(minutes=max(10, minutes)) if last_sent_at and minutes else None
        enabled = bool(rules.get(message_type, 1)) if has_cadence else True
        recent_targets = []
        for record in records[:4]:
            account = accounts.get(str(record.get("accountId") or ""))
            target = str(record.get("target") or "all")
            recent_targets.append({
                "accountId": record.get("accountId") or "",
                "accountLabel": account.label if account else str(record.get("accountId") or ""),
                "target": "" if target == "all" else target,
                "sentAt": record.get("sentAt") or "",
            })
        if not enabled:
            status = "disabled"
        elif event_delivery or not has_cadence:
            status = "event"
        elif next_eligible_at and next_eligible_at > now_at:
            status = "waiting"
        else:
            status = "ready"
        if event_delivery:
            cadence_text = EVENT_DELIVERY_GUIDES[message_type]
        elif minutes:
            cadence_text = "조건이 다시 충족되면 최소 " + str(max(10, minutes)) + "분 간격으로 보냅니다."
        else:
            cadence_text = "정해진 주기 없이 해당 이벤트가 생길 때만 보냅니다."
        schedules.append({
            "messageType": message_type,
            "label": MESSAGE_TYPE_LABELS.get(message_type, message_type),
            "icon": notification_message_icon(message_type),
            "enabled": enabled,
            "status": status,
            "cadenceMinutes": max(10, minutes) if minutes else 0,
            "cadenceText": cadence_text,
            "cadenceMode": "event" if event_delivery else "minimum-interval",
            "historyBasis": "monitor-admission",
            "triggerSummary": TRIGGER_SUMMARIES.get(message_type) or NON_CADENCE_MESSAGE_GUIDES.get(message_type) or "설정한 조건이 실제 데이터에서 충족될 때 보냅니다.",
            "lastSentAt": utc_iso(last_sent_at) if last_sent_at else "",
            "nextEligibleAt": utc_iso(next_eligible_at) if next_eligible_at else "",
            "eligibleNow": bool(enabled and (not next_eligible_at or next_eligible_at <= now_at)),
            "recentTargets": recent_targets,
        })
    return {
        "generatedAt": utc_now_iso(),
        "schedules": schedules,
        "managedMessageTypes": user_managed_notification_types(),
    }


def save_template_payload(payload: Dict[str, object]) -> Dict[str, object]:
    message_type = configured(payload.get("messageType") or payload.get("message_type"))
    template = str(payload.get("template") or "")
    description = str(payload.get("description") or "")
    enabled = payload.get("enabled") is not False
    try:
        saved = notification_store().upsert(message_type, template, description, enabled)
    except Exception:  # noqa: BLE001 - respond with normalized payload when optional MySQL is offline.
        saved = NotificationTemplate(message_type, template, description, enabled, now())
    event = new_domain_event(
        NOTIFICATION_TEMPLATE_UPDATED,
        saved.message_type,
        {"messageType": saved.message_type, "enabled": saved.enabled, "updatedAt": saved.updated_at},
    )
    return {"template": saved.to_dict(), "eventId": event.event_id}


def reset_template_payload(message_type: str) -> Dict[str, object]:
    try:
        saved = notification_store().reset(message_type)
    except Exception:  # noqa: BLE001
        saved = NotificationTemplate.default(message_type)
    event = new_domain_event(
        NOTIFICATION_TEMPLATE_UPDATED,
        saved.message_type,
        {"messageType": saved.message_type, "enabled": saved.enabled, "updatedAt": saved.updated_at, "reset": True},
    )
    return {"template": saved.to_dict(), "eventId": event.event_id}


def save_notification_rule_payload(payload: Dict[str, object]) -> Dict[str, object]:
    requested = payload.get("rule") if isinstance(payload.get("rule"), dict) else payload
    rule = NotificationRuleConfig.from_dict(requested if isinstance(requested, dict) else {})
    try:
        saved = notification_rule_store().upsert(rule)
    except Exception:  # noqa: BLE001
        saved = rule
        saved.updated_at = now()
    event = new_domain_event(
        NOTIFICATION_RULE_UPDATED,
        saved.message_type,
        {
            "messageType": saved.message_type,
            "enabled": saved.enabled,
            "similarityEnabled": saved.similarity_enabled,
            "similarityWindowMinutes": saved.similarity_window_minutes,
            "similarityBypassConditionCount": len(saved.similarity_bypass_conditions),
            "stateCooldownEnabled": saved.state_cooldown_enabled,
            "immediateCooldownMinutes": saved.immediate_cooldown_minutes,
            "materialCooldownMinutes": saved.material_cooldown_minutes,
            "stateCooldownMinutes": saved.state_cooldown_minutes,
            "updatedAt": saved.updated_at,
        },
    )
    return {"rule": saved.to_dict(), "eventId": event.event_id}


def reset_notification_rule_payload(message_type: str) -> Dict[str, object]:
    try:
        saved = notification_rule_store().reset(message_type)
    except Exception:  # noqa: BLE001
        saved = NotificationRuleConfig.from_dict({"messageType": message_type, "enabled": bool(DEFAULT_ALERT_RULES.get(message_type, 1))})
        saved.updated_at = now()
    event = new_domain_event(
        NOTIFICATION_RULE_UPDATED,
        saved.message_type,
        {
            "messageType": saved.message_type,
            "enabled": saved.enabled,
            "similarityEnabled": saved.similarity_enabled,
            "similarityWindowMinutes": saved.similarity_window_minutes,
            "stateCooldownEnabled": saved.state_cooldown_enabled,
            "immediateCooldownMinutes": saved.immediate_cooldown_minutes,
            "materialCooldownMinutes": saved.material_cooldown_minutes,
            "stateCooldownMinutes": saved.state_cooldown_minutes,
            "updatedAt": saved.updated_at,
            "reset": True,
        },
    )
    return {"rule": saved.to_dict(), "eventId": event.event_id}
