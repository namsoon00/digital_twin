"""Web calendar boundary."""

from digital_twin.infrastructure.event_bus import EventBus
from digital_twin.infrastructure.service_factory import build_investment_calendar_candidate_service
from digital_twin.infrastructure.service_factory import build_investment_calendar_discovery_service
from digital_twin.infrastructure.service_factory import build_investment_calendar_research_service
from digital_twin.infrastructure.service_factory import build_investment_calendar_service
from digital_twin.infrastructure.service_factory import build_official_calendar_sync_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.events import RealtimeEventBridge
from typing import Dict
from typing import List


def investment_calendar_service():
    return build_investment_calendar_service(runtime_settings(), event_publisher=RealtimeEventBridge())


def investment_calendar_read_service():
    return build_investment_calendar_service(operational_read_settings(), event_publisher=EventBus())


def investment_calendar_candidate_service():
    return build_investment_calendar_candidate_service(runtime_settings(), event_publisher=RealtimeEventBridge())


def investment_calendar_candidate_read_service():
    return build_investment_calendar_candidate_service(operational_read_settings(), event_publisher=EventBus())


def investment_calendar_research_service():
    return build_investment_calendar_research_service(runtime_settings())


def investment_calendar_discovery_service():
    return build_investment_calendar_discovery_service(runtime_settings(), event_publisher=RealtimeEventBridge())


def investment_calendar_query_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return {
        "from": first_query(query, "from") or first_query(query, "fromAt"),
        "to": first_query(query, "to") or first_query(query, "toAt"),
        "status": first_query(query, "status"),
        "includeInactive": first_query(query, "includeInactive"),
        "symbol": first_query(query, "symbol"),
        "eventType": first_query(query, "eventType") or first_query(query, "event_type"),
        "limit": first_query(query, "limit") or "200",
    }


def investment_calendar_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return investment_calendar_read_service().list_events(investment_calendar_query_payload(query))


def investment_calendar_observation_payload(event):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.modules.market_data.public import InformationObservationService
    information = event.get("releaseInformation") or {}
    release = information.get("release") or {}
    symbols = list(event.get("symbols") or [])
    if not symbols:
        symbols = ["^KS11", "USDKRW=X"] if (event.get("payload") or {}).get("country") == "KR" else ["^GSPC", "^IXIC"]
    information["marketReaction"] = InformationObservationService(stores.market_time_series_store()).observe(release.get("releasedAt") or "", symbols)
    event["releaseInformation"] = information
    return event


def investment_calendar_event_payload(event_id):
    event = investment_calendar_read_service().get_event(event_id)
    return {"event": investment_calendar_observation_payload(event)} if event else {}


def save_investment_calendar_event_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_service().save_event(payload if isinstance(payload, dict) else {})


def delete_investment_calendar_event_payload(event_id: str) -> Dict[str, object]:
    return investment_calendar_service().delete_event(event_id)


def investment_calendar_reminders_once_payload() -> Dict[str, object]:
    # The manual UI control is a reminder check only. Official sync and external
    # date discovery run on the calendar worker or their explicit controls.
    return investment_calendar_service().enqueue_due_reminders()


def investment_calendar_sync_official_payload() -> Dict[str, object]:
    return build_official_calendar_sync_service(runtime_settings(), event_publisher=RealtimeEventBridge()).run_once(force=True)


def investment_calendar_candidates_query_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return {
        "status": first_query(query, "status") or "pending",
        "limit": first_query(query, "limit") or "100",
        "page": first_query(query, "page"),
        "pageSize": first_query(query, "pageSize") or first_query(query, "page_size"),
        "offset": first_query(query, "offset"),
    }


def investment_calendar_candidates_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    return investment_calendar_candidate_read_service().list_candidates(investment_calendar_candidates_query_payload(query))


def research_investment_calendar_candidates_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_research_service().recommend(payload if isinstance(payload, dict) else {})


def discover_investment_calendar_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_discovery_service().run_once(payload if isinstance(payload, dict) else {}, force=True)


def approve_investment_calendar_candidate_payload(candidate_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_candidate_service().approve_candidate(candidate_id, payload if isinstance(payload, dict) else {})


def reject_investment_calendar_candidate_payload(candidate_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return investment_calendar_candidate_service().reject_candidate(candidate_id, payload if isinstance(payload, dict) else {})
